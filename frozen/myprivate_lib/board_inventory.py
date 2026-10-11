# SPDX-FileCopyrightText: Copyright (c) 2026 Bob Grant for grgrant
#
# SPDX-License-Identifier: MIT
"""
board_inventory.py - CircuitPython Board Inventory client
=========================================================

Drop this file into the ``lib/`` folder on your CIRCUITPY drive. Boards check
in with a central server, reporting their identity, software versions, and a
few health metrics.

Minimal usage in ``code.py``::

    from board_inventory import BoardInventory

    CODEBASE = "common_sensor"
    VERSION = "2.1.0"

    inventory = BoardInventory(
        codebase_name=CODEBASE,
        codebase_version=VERSION,
        description="HDC302x temp/humidity, garage west wall",
        aio_group_key="garage-env",
    )
    inventory.checkin()

    while True:
        # ... sensor work ...
        inventory.battery = f"{read_battery_pct()}%"   # optional, see below
        inventory.checkin_if_due()
        time.sleep(60)

Server URL resolution order
    1. ``server_url=`` constructor parameter
    2. ``BOARD_INVENTORY_URL`` in settings.toml
    3. ``DEFAULT_SERVER_URL`` constant below

Hostname resolution order
    1. ``hostname=`` constructor parameter
    2. ``BOARD_INVENTORY_HOSTNAME`` in settings.toml
    3. ``CIRCUITPY_WIFI_HOSTNAME`` in settings.toml
    4. ``CIRCUITPY_WEB_INSTANCE_NAME`` in settings.toml
    5. chip_id (final fallback)

Battery / status reporting
    ``battery`` is free-form text, so report whatever suits the board:
    ``"87%"``, ``"4.02V"``, or ``"87% / 4.02V"``. It is read fresh at each
    checkin, so set ``inventory.battery`` in your loop before ``checkin()``.
    Boards without a battery simply never set it. To report something else
    entirely, pass any extra keyword(s) to ``checkin(**fields)``; unknown
    fields are stored server-side and shown on the board's detail view.

Commands and the unsafe-command guard
    The server can queue commands that a board picks up on checkin. Each is a
    dict like ``{"action": "set_debug", "value": True}`` handed to your
    ``command_handler``. Actions that can take a board offline -- ``reboot``
    and ``enter_uf2`` -- are dropped before reaching your handler unless you
    pass ``allow_unsafe_commands=True``. This is an explicit opt-in: it keeps a
    stray or repeated reboot command from cycling a board you didn't mean to
    touch.

Sharing a socket pool with other network code
    If your ``code.py`` already talks to the network (MQTT, requests, ...),
    pass the SAME socketpool in so both sides share one
    adafruit_connection_manager instance instead of each caching sockets behind
    the other's back::

        import adafruit_connection_manager
        pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
        ssl_ctx = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
        inventory = BoardInventory(..., socket_pool=pool, ssl_context=ssl_ctx)

    Left unset, the pool is resolved via adafruit_connection_manager if it is
    installed (it is already a dependency of adafruit_requests) and falls back
    to socketpool.SocketPool(wifi.radio) otherwise.
"""

__repo__ = "https://github.com/grgrant/GRGrant_CircuitPython_board_inventory.git"

VERSION = "1.2.2a"

import gc
import os
import time

import microcontroller
import ssl
import socketpool
import wifi
import adafruit_requests

# ---------------------------------------------------------------------------
# Change this to your inventory server's address. It lives here rather than in
# settings.toml because it rarely changes and is the same across the fleet.
# Override per board via settings.toml BOARD_INVENTORY_URL or the server_url=
# constructor parameter.
# ---------------------------------------------------------------------------
DEFAULT_SERVER_URL = "http://192.168.1.100:5000"

# Command actions that can take a board offline. Ignored unless the board is
# constructed with allow_unsafe_commands=True.
UNSAFE_COMMANDS = ("reboot", "enter_uf2")


class BoardInventory:
    def __init__(
        self,
        server_url=None,
        codebase_name=None,
        codebase_version=None,
        hostname=None,
        description=None,
        aio_group_key=None,
        battery=None,               # free-text, settable per checkin
        checkin_interval=300,       # seconds between periodic checkins
        retry_interval=60,          # first retry gap after a FAILED checkin
        max_retry_interval=None,    # backoff ceiling (defaults to checkin_interval)
        command_handler=None,       # callback: def handler(command_dict) -> None
        allow_unsafe_commands=False, # opt in to reboot / enter_uf2
        socket_pool=None,           # share a pool with the caller's network code
        ssl_context=None,           # share an ssl context with the caller
        session=None,               # or hand us a fully built requests Session
        request_timeout=10,         # seconds; adafruit_requests defaults to 60
    ):
        # Server URL: explicit param > settings.toml > module constant
        self._server_url = (
            server_url
            or os.getenv("BOARD_INVENTORY_URL")
            or DEFAULT_SERVER_URL
        )

        # Hostname: explicit > settings.toml (three keys) > chip_id (in property)
        self._hostname = (
            hostname
            or os.getenv("BOARD_INVENTORY_HOSTNAME")
            or os.getenv("CIRCUITPY_WIFI_HOSTNAME")
            or os.getenv("CIRCUITPY_WEB_INSTANCE_NAME")
        )

        self._codebase_name = codebase_name
        self._codebase_version = codebase_version
        self._description = (
            description
            or os.getenv("BOARD_INVENTORY_DESCRIPTION")
            or None
        )

        # These are meant to be updated from user code between checkins.
        self.aio_group_key = aio_group_key
        self.battery = battery

        self._checkin_interval = checkin_interval
        self._retry_interval = retry_interval
        self._max_retry_interval = max_retry_interval or checkin_interval
        self._command_handler = command_handler
        self._allow_unsafe_commands = allow_unsafe_commands
        self._request_timeout = request_timeout

        self._last_checkin = 0    # last SUCCESSFUL checkin (monotonic)
        self._last_attempt = 0    # last ATTEMPT, success or fail (monotonic)
        self._backoff = 0         # 0 = healthy; >0 = current failure backoff (s)
        self._reachable = False   # last checkin outcome; starts False (never yet)
        self._incident = None  # one-shot crash/safe-mode string for next checkin

        # chip_id is stable for the life of the board; build it once.
        # self._chip_id = ":".join(f"{b:02x}" for b in microcontroller.cpu.uid) # Former now no-colons
        self._chip_id = microcontroller.cpu.uid[:8].hex()

        # Running RSSI average (dBm, negative). Reported as a string.
        self._rssi_total = 0
        self._rssi_count = 0

        self._session = self._build_session(session, socket_pool, ssl_context)

    # -- session setup ------------------------------------------------------

    def _build_session(self, session, socket_pool, ssl_context):
        """Prefer whatever the caller already has: a Session, or a pool/ssl
        context. Otherwise resolve through adafruit_connection_manager so we
        land on the same cached pool as any other library on this board
        (minimqtt, adafruit_requests, ...) rather than minting a second pool,
        which would give the board two connection managers each caching sockets
        the other doesn't know about.
        """
        if session is not None:
            return session

        if socket_pool is None:
            try:
                import adafruit_connection_manager
                socket_pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
                if ssl_context is None:
                    ssl_context = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
            except ImportError:
                socket_pool = socketpool.SocketPool(wifi.radio)

        if ssl_context is None:
            ssl_context = ssl.create_default_context()

        return adafruit_requests.Session(socket_pool, ssl_context)

    # -- properties ---------------------------------------------------------

    @property
    def chip_id(self):
        return self._chip_id

    @property
    def hostname(self):
        return self._hostname or self._chip_id

    @property
    def checkin_due(self):
        """True once it's time to (try to) check in. Lets an event-driven caller
        test cheaply, then call checkin() at a point where blocking on the
        network for a second or two is safe.

        When the last attempt FAILED (server unreachable, e.g. the board is on a
        phone hotspot that can't see the LAN, or the server is down), this holds
        off for a growing backoff measured from the last ATTEMPT rather than
        going true every loop. Previously it keyed only off the last *success*,
        so a failure left it perpetually due and the caller hammered an
        unreachable server every loop. On the next success the backoff clears and
        the normal interval resumes."""
        now = time.monotonic()
        if self._backoff:
            return (now - self._last_attempt) >= self._backoff
        return (now - self._last_checkin) >= self._checkin_interval

    @property
    def reachable(self):
        """The last checkin outcome, as a plain bool with no network I/O.

        Starts False (no successful checkin yet), goes True on a confirmed
        success, and back to False on any subsequent failure -- so it drives a
        status light exactly like a connection indicator: red until the first
        good checkin, green while checkins succeed, red again the moment one
        fails. Because a checkin only happens on the interval/backoff schedule,
        this reflects the most recent *attempt*, not live reachability."""
        return self._reachable

    # -- report building ----------------------------------------------------

    def _rssi_avg(self):
        """Running average RSSI in dBm (negative), as a string. Empty if the
        radio has no AP info yet."""
        try:
            rssi = wifi.radio.ap_info.rssi
        except Exception:
            return ""
        self._rssi_total += rssi
        self._rssi_count += 1
        return str(self._rssi_total // self._rssi_count)

    def _board_type(self):
        """os.uname().machine, shortened for the dashboard. The substitutions
        are purely cosmetic -- edit or delete them to taste."""
        machine = os.uname().machine
        try:
            import re
            machine = re.sub("Adafruit", "AF", machine)
            machine = re.sub("Seeed Studio XIAO", "XIAO", machine)
            machine = re.sub("^FeatherS3 with ESP32S3", "UM Feather ESP32-S3", machine)
            machine = re.sub("TinyS2 with ESP32S2", "UM TinyS2 ESP32-S2", machine)
        except Exception:
            pass
        if len(machine) > 25:
            machine = machine.split(" with ")[0]
        return machine

    def _build_report(self):
        gc.collect()  # for an accurate free-memory reading

        version = os.uname().version
        if len(version) > 8:
            version = version.split()[0]  # keep just the version number

        report = {
            "chip_id": self._chip_id,
            "board_type": self._board_type(),
            "hostname": self.hostname,
            "cp_version": version,
            "ip_address": str(wifi.radio.ipv4_address),
            "uptime_seconds": int(time.monotonic()),  # board-powered time
            "free_memory": gc.mem_free(),
            "rssi": self._rssi_avg(),
            "transport": "wifi",
        }

        if self._codebase_name:
            report["codebase_name"] = self._codebase_name
        if self._codebase_version:
            report["codebase_version"] = self._codebase_version
        if self._description:
            report["description"] = self._description
        if self.aio_group_key:
            report["aio_group_key"] = self.aio_group_key
        if self.battery:
            report["battery"] = str(self.battery)
        if self._incident:
            report["incident"] = self._incident

        return report

    # -- command handling ---------------------------------------------------

    def _dispatch(self, commands):
        """Hand commands to the user's handler, filtering unsafe ones unless
        the board opted in."""
        if not (commands and self._command_handler):
            return
        for cmd in commands:
            action = cmd.get("action") if isinstance(cmd, dict) else None
            if action in UNSAFE_COMMANDS and not self._allow_unsafe_commands:
                print(f"BoardInventory: ignoring unsafe command '{action}' "
                      f"(allow_unsafe_commands is False)")
                continue
            try:
                self._command_handler(cmd)
            except Exception as e:
                print(f"BoardInventory: command handler error: {e}")

    # -- session recovery ---------------------------------------------------

    def _recover_session(self, exc):
        """Un-wedge adafruit_requests after something else closed our socket.

        adafruit_requests holds the previous Response in Session._last_response
        and closes it at the START of the next request. If another library on
        this board closed our socket behind our back -- notably
        adafruit_connection_manager.connection_manager_close_all(), which some
        MQTT reconnect paths call -- that close raises "Socket not managed"
        BEFORE _last_response is cleared, so every later request raises the same
        thing forever. Dropping the stale reference lets the next one start
        clean. Closing our own responses (in checkin) means this should never
        fire, but it is cheap insurance for callers sharing a pool.
        """
        if "not managed" not in str(exc):
            return
        try:
            if getattr(self._session, "_last_response", None) is not None:
                self._session._last_response = None
        except Exception:
            pass

    # -- public API ---------------------------------------------------------

    def set_incident(self, text):
        """Attach a one-shot incident string (crash / safe-mode report) to the
        next checkin. Unlike ``battery``, it is not re-read every checkin: it
        fires once and clears as soon as a checkin succeeds, so the board sends
        it exactly once and the server is responsible for latching it and
        deciding how long its dashboard card stays up."""
        self._incident = text

    def _mark_success(self):
        self._last_checkin = time.monotonic()
        self._backoff = 0   # back to the normal interval
        self._reachable = True

    def _mark_failure(self):
        # Exponential backoff from retry_interval, capped at max_retry_interval,
        # so an unreachable server is retried gently instead of every loop.
        if self._backoff:
            self._backoff = min(self._backoff * 2, self._max_retry_interval)
        else:
            self._backoff = min(self._retry_interval, self._max_retry_interval)
        self._reachable = False

    def checkin(self):
        """Send a checkin to the server and act on any returned commands.

        Returns the list of commands the server handed back (before unsafe
        filtering). Fails silently with a print() on error so it can never
        crash your main loop.
        """
        if not self._server_url:
            return []

        response = None
        self._last_attempt = time.monotonic()   # measured whether or not it works
        try:
            report = self._build_report()
            url = self._server_url.rstrip("/") + "/api/checkin"
            response = self._session.post(url, json=report, timeout=self._request_timeout)

            if response.status_code == 200:
                data = response.json()
                self._mark_success()
                self._incident = None  # delivered; don't resend on next checkin
                commands = data.get("commands", [])
                self._dispatch(commands)
                return commands

            print(f"BoardInventory: checkin HTTP {response.status_code}")
            self._mark_failure()
            return []

        except Exception as e:
            print(f"BoardInventory: checkin failed: {e}")
            self._recover_session(e)
            self._mark_failure()
            return []

        finally:
            # Always hand the socket back. Without this, adafruit_requests keeps
            # it checked out (one leaked socket per checkin) and leaves a
            # dangling Response for the next request to trip over.
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def checkin_if_due(self):
        """Call this in your main loop; only actually sends when the interval
        has elapsed. Returns the commands list, or empty."""
        if self.checkin_due:
            return self.checkin()
        return []
