# SPDX-FileCopyrightText: 2025 Bob Grant
# SPDX-License-Identifier: MIT
"""
Sensor System Core
====================
Core classes for the CircuitPython IoT sensor system:
  - SystemManager:   Main coordinator (boot, init, run loop, sleep)
  - NetworkManager:  WiFi + Adafruit IO + NTP
  - SensorManager:   Read sensors, send data, buffer, alerts
  - AlertManager:    Threshold alerts and production logging
  - DataBuffer:      Offline reading storage
  - VersionManager:  Version reporting to AIO

Version (paired with code.py, sensor_configs.py)

"""
VERSION = "3.0.6db"

import gc
import time
import rtc
import ssl
import board
import microcontroller
import neopixel
import digitalio
import wifi
import socketpool
import adafruit_requests
from adafruit_io.adafruit_io import IO_HTTP, AdafruitIO_RequestError
import watchdog
from wifi_connection import WiFiConnection
from sensor_configs import CONFIGS_VERSION

try:
    import crash_log
    from crash_log import (
        PH_IDLE, PH_BOOT, PH_I2C, PH_SENSOR, PH_WIFI, PH_AIO, PH_LOOP,
    )
except ImportError:
    crash_log = None
    # Fallbacks so beat() call sites still resolve when the modules aren't
    # installed. Only ever used in the "both absent" case, where nothing reads
    # them -- so the values don't matter, they just need to exist.
    PH_IDLE = PH_BOOT = PH_I2C = PH_SENSOR = PH_WIFI = PH_AIO = PH_LOOP = 0

try:
    import breadcrumb
except ImportError:
    breadcrumb = None

def _beat(phase):
    if breadcrumb is not None:
        breadcrumb.beat(phase)
        
# ═══════════════════════════════════════════════════════════════════════════
# Data Buffer
# ═══════════════════════════════════════════════════════════════════════════

class DataBuffer:
    """Stores sensor readings when offline for later batch upload"""

    def __init__(self, max_size=50):
        self.buffer = []
        self.max_size = max_size

    def add_reading(self, timestamp, readings):
        if len(self.buffer) >= self.max_size:
            self.buffer.pop(0)
        self.buffer.append({'timestamp': timestamp, 'readings': readings})

    def get_all(self):
        return self.buffer.copy()

    def clear(self):
        self.buffer.clear()

    def is_empty(self):
        return len(self.buffer) == 0

    def size(self):
        return len(self.buffer)


# ═══════════════════════════════════════════════════════════════════════════
# Alert Manager
# ═══════════════════════════════════════════════════════════════════════════

class AlertManager:
    """Manages alert conditions, SMS alerts, and production logging"""

    def __init__(self, network_manager, group_name, thresholds, debug=False):
        self.network = network_manager
        self.group_name = group_name
        self.thresholds = thresholds or {}
        self.debug = debug
        self.alert_cooldown = {}
        self.regular_cooldown_period = 3600    # 1 hour
        self.sms_cooldown_period = 21600       # 6 hours

    def _display_name(self):
        """Capitalize first letter of group name (CircuitPython compatible)"""
        if self.group_name and len(self.group_name) > 0:
            return self.group_name[0].upper() + self.group_name[1:]
        return "Unknown"

    def check_and_send_alerts(self, readings):
        """Check readings against thresholds and send alerts"""
        alerts_sent = []
        current_time = time.monotonic()

        # readings is a dict
        for sensor_name, value in readings.items():
            if value is None or sensor_name not in self.thresholds:
                continue

            thresholds = self.thresholds[sensor_name]
            alert_key = None
            alert_type = None
            is_sms_alert = thresholds.get('sms', False)

            try:
                numeric_value = float(value)

                if thresholds.get('high') and numeric_value > thresholds['high']:
                    alert_type = "High"
                    alert_key = f"{sensor_name}_high"
                elif thresholds.get('low') and numeric_value < thresholds['low']:
                    alert_type = "Low"
                    alert_key = f"{sensor_name}_low"
            except (ValueError, TypeError):
                continue

            if alert_key and alert_type:
                cooldown = self.sms_cooldown_period if is_sms_alert else self.regular_cooldown_period
                last_alert = self.alert_cooldown.get(alert_key, 0)

                if current_time - last_alert > cooldown:
                    display_name = self._display_name()
                    message = f"{display_name}: {sensor_name} - {alert_type}: {value}"

                    if is_sms_alert:
                        if self._send_sms_alert(message):
                            self.alert_cooldown[alert_key] = current_time
                            alerts_sent.append(f"SMS: {message}")
                            if self.debug:
                                print(f"SMS alert sent: {message}")
                    else:
                        if self._send_alert(message):
                            self.alert_cooldown[alert_key] = current_time
                            alerts_sent.append(f"Alert: {message}")
                            if self.debug:
                                print(f"Alert sent: {message}")

        return alerts_sent

    def _send_alert(self, message):
        try:
            feed = self.network.get_or_create_feed(self.group_name, "alerts")
            self.network.send_data(feed, message)
            return True
        except Exception as e:
            print(f"Failed to send alert: {e}")
            return False

    def _send_sms_alert(self, message):
        try:
            feed = self.network.get_or_create_feed("logging", "sms-alerts")
            self.network.send_data(feed, message)
            return True
        except Exception as e:
            print(f"Failed to send SMS alert: {e}")
            return False

    def send_production_log(self, message):
        """Send event to logging.production feed"""
        try:
            display_name = self._display_name()
            if not message.startswith(display_name + ":"):
                message = f"{display_name}: {message}"
            feed = self.network.get_or_create_feed("logging", "production")
            self.network.send_data(feed, message)
            if self.debug:
                print(f"Production log sent: {message}")
            return True
        except Exception as e:
            print(f"Failed to send production log: {e}")
            return False

    def send_incident_log(self, message):
        """Crash/safe-mode events -> logging.incident (separate from
        logging.production so it's findable later and easy to hang an AIO
        Action on for a text)."""
        try:
            display_name = self._display_name()
            if not message.startswith(display_name + ":"):
                message = f"{display_name}: {message}"
            feed = self.network.get_or_create_feed("logging", "incident")
            self.network.send_data(feed, message)
            if self.debug:
                print(f"Incident log sent: {message}")
            return True
        except Exception as e:
            print(f"Failed to send incident log: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════
# Version Manager
# ═══════════════════════════════════════════════════════════════════════════

class VersionManager:
    """Reports version to Adafruit IO (no update checking)"""

    def __init__(self, network_manager, group_name, current_version, debug=False):
        self.network = network_manager
        self.group_name = group_name
        self.current_version = current_version
        self.debug = debug

    def report_version(self):
        try:
            version_string = f"V{self.current_version} C{CONFIGS_VERSION} S{VERSION}"
            feed = self.network.get_or_create_feed(self.group_name, "version")
            self.network.send_data(feed, version_string)
            #if self.debug:
            #    print(f"Version reported: {version_string}")
        except Exception as e:
            print(f"Failed to report version: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Network Manager
# ═══════════════════════════════════════════════════════════════════════════

class NetworkManager:
    """Manages WiFi (via WiFiConnection) and Adafruit IO connections"""

    def __init__(self, aio_username, aio_key, utc_offset=-7, tx_power=None,
                 enable_buffering=False, rtc_time_fudge=45, debug=False):
        self.aio_username = aio_username
        self.aio_key = aio_key
        self.utc_offset = utc_offset
        self.enable_buffering = enable_buffering
        self.rtc_time_fudge = rtc_time_fudge
        self.debug = debug

        self.io = None
        self.pool = None
        self.requests = None
        self.rtclk = rtc.RTC()
        self.feeds_cache = {}
        self._time_valid = False

        self.wifi_conn = WiFiConnection(
            sync_time=True,
            tz_offset=utc_offset,
            verification_level=2,
            verbose=debug,
            tx_power=tx_power,
            ntp_socket_timeout=15,
            overall_timeout=45,
            retry_attempts=1,
        )

    def connect_wifi(self):
        _beat(PH_WIFI)
        try:
            success = self.wifi_conn.connect()
            if success:
                print(f"Connected to {self.wifi_conn.connected_ssid}!")
                print(f"IP: {self.wifi_conn.ip_address}")
                print(f"RSSI: {self.wifi_conn.signal_strength}  TX_PWR: {wifi.radio.tx_power}")
                if self.wifi_conn.time_synced:
                    self._time_valid = True
                    print(f"Time synced: {self._format_datetime()}")
                return True
            else:
                print("WiFiConnection: All connection attempts failed")
                return False
        except Exception as e:
            print(f"WiFi connection error: {e}")
            return False

    def ensure_wifi(self):
        """Ensure WiFi is connected, reconnect if needed"""
        _beat(PH_WIFI)

        try:
            if self.wifi_conn.connected:
                return True
            print("WiFi connection lost, reconnecting...")
            success = self.wifi_conn.connect()
            if success:
                print(f"Reconnected to {self.wifi_conn.connected_ssid}")
                if self.wifi_conn.time_synced:
                    self._time_valid = True
                self.setup_io()
            return success
        except Exception as e:
            print(f"WiFi ensure error: {e}")
            return False

    def disconnect_wifi(self):
        """Disconnect WiFi to save power"""
        try:
            self.wifi_conn.disconnect()
            self.io = None  # Force re-init on next connect
        except Exception as e:
            print(f"WiFi disconnect error: {e}")

    def setup_io(self):
        _beat(PH_AIO)
        try:
            self.pool = socketpool.SocketPool(wifi.radio)
            self.requests = adafruit_requests.Session(self.pool, ssl.create_default_context())
            self.io = IO_HTTP(self.aio_username, self.aio_key, self.requests)
            return True
        except Exception as e:
            print(f"Adafruit IO setup error: {e}")
            return False

    def sync_time(self):
        try:
            if self.wifi_conn.sync_ntp_time():
                self._time_valid = True
                print(f"Time synced: {self._format_datetime()}")
                return True
            else:
                print("NTP sync failed")
                return False
        except Exception as e:
            print(f"NTP sync error: {e}")
            return False

    def is_time_valid(self):
        if not self._time_valid:
            return False
        try:
            ct = self.rtclk.datetime
            if ct.tm_year < 2025:
                print(f"WARNING: RTC year {ct.tm_year} invalid, marking time invalid")
                self._time_valid = False
                return False
            return True
        except Exception:
            return False

    def get_or_create_feed(self, group_name, feed_name):
        """Get or create a feed. Feed keys are always lowercase."""
        _beat(PH_AIO)
        feed_key = f"{group_name}.{feed_name.lower()}"

        if feed_key in self.feeds_cache:
            return self.feeds_cache[feed_key]

        try:
            feed = self.io.get_feed(feed_key)
            self.feeds_cache[feed_key] = feed
            return feed
        except AdafruitIO_RequestError:
            print(f"Creating feed: {feed_key}")
            feed = self.io.create_feed_in_group(group_name, feed_name)
            self.feeds_cache[feed_key] = feed
            return feed

    def send_data(self, feed, value):
        _beat(PH_AIO)
        return self.io.send_data(feed["key"], value)

    def send_data_with_timestamp(self, feed, value, timestamp):
        metadata = {'created_at': timestamp}
        return self.io.send_data(feed["key"], value, metadata=metadata)

    def get_remote_config(self, group_name, config_feeds=None):
        """
        Fetch remote configuration values from sensorcfg feeds.

        Args:
            group_name: sensor group name
            config_feeds: dict of {attribute_name: feed_key} to check

        Returns:
            dict of config updates (may be empty)
        """
        _beat(PH_AIO)
        config_updates = {}

        # Only check feeds passed in from config — no standard feeds
        # (sleep and programming mode are handled per-board via config_feeds)
        all_feeds = config_feeds or {}

        for attr_name, feed_key in all_feeds.items():
            try:
                feed = self.io.get_feed(feed_key)
                data = self.io.receive_data(feed["key"])
                raw_value = data['value']

                # Parse based on attribute name
                if 'program' in attr_name:
                    config_updates[attr_name] = int(raw_value) > 0
                else:
                    config_updates[attr_name] = int(raw_value)

                if self.debug:
                    print(f"Remote config {attr_name}: {config_updates[attr_name]}")
            except Exception:
                # Feed doesn't exist or read failed — silently continue
                pass

        return config_updates

    def _format_datetime(self):
        ct = self.rtclk.datetime
        return f"{ct.tm_year}/{ct.tm_mon}/{ct.tm_mday} {ct.tm_hour}:{ct.tm_min:02d}:{ct.tm_sec:02d}"

    def get_timestamp(self):
        """Get ISO 8601 timestamp for AIO, or None if time invalid or buffering disabled"""
        if not self.is_time_valid():
            if self.debug:
                print("WARNING: Time not valid, skipping timestamp (server time)")
            return None

        if not self.enable_buffering:
            return None

        ct = self.rtclk.datetime
        offset_sign = '-' if self.utc_offset < 0 else '+'
        offset_str = "{}{:02d}:00".format(offset_sign, abs(self.utc_offset))

        # Apply time fudge to avoid future timestamps
        hr = ct.tm_hour
        mn = ct.tm_min
        sec = ct.tm_sec
        secs = (hr * 3600) + (mn * 60) + sec
        secs -= self.rtc_time_fudge
        if secs < 0:
            secs = 0
        hr = secs // 3600
        secs -= hr * 3600
        mn = secs // 60
        sec = secs % 60

        return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}{}".format(
            ct.tm_year, ct.tm_mon, ct.tm_mday, hr, mn, sec, offset_str
        )


# ═══════════════════════════════════════════════════════════════════════════
# Sensor Manager
# ═══════════════════════════════════════════════════════════════════════════

class SensorManager:
    """Manages sensor reading, AIO sending, buffering, and alerts"""

    def __init__(self, config, network_manager, pixel, alert_thresholds,
                 enable_buffering=False, debug=False, ha_client=None):
        self.config = config
        self.network = network_manager
        self.pixel = pixel
        self.debug = debug
        self.enable_buffering = enable_buffering
        self.ha_client = ha_client
        self.last_successful_update = time.monotonic()
        self.buffer = DataBuffer(max_size=50)
        self.alert_manager = AlertManager(
            network_manager, config.group_name, alert_thresholds, debug=debug
        )

    def update_feeds(self, force_send=False, feed_watchdog=None, readings=None):
        """Send sensor data to Adafruit IO and HA.
        
        Args:
            force_send: force sending buffered data
            feed_watchdog: callback to feed watchdog timer
            readings: pre-read sensor readings as list of (name, value) tuples.
                      If None, reads sensors fresh.
        """
        _beat(PH_SENSOR)
        try:
            if readings is None:
                if self.debug:
                    print("Reading sensors...")
                self.pixel.fill((255, 165, 0))  # Orange = reading
                readings_list = self.config.get_sensor_readings(debug=self.debug)
            else:
                readings_list = readings

            readings_dict = dict(readings_list)

            timestamp = self.network.get_timestamp()

            # Check alerts
            self.alert_manager.check_and_send_alerts(readings_dict)

            # Ensure WiFi
            if not self.network.ensure_wifi():
                if self.enable_buffering and timestamp is not None:
                    print(f"WiFi down, buffering ({self.buffer.size() + 1} buffered)")
                    self.buffer.add_reading(timestamp, readings_dict)
                elif not self.enable_buffering:
                    print("WiFi down, buffering disabled - reading discarded")
                else:
                    print("WiFi down, time invalid - reading discarded")
                self.pixel.fill((255, 0, 0))
                time.sleep(2)
                self.pixel.fill((0, 0, 0))
                return False

            # Feed watchdog before network operations
            if feed_watchdog:
                feed_watchdog()

            # Send current readings
            success = self._send_readings(readings_list, timestamp, feed_watchdog)

            if not success:
                if self.enable_buffering and timestamp is not None:
                    print(f"Send failed, buffering ({self.buffer.size() + 1} buffered)")
                    self.buffer.add_reading(timestamp, readings_dict)
                elif not self.enable_buffering:
                    print("Send failed, buffering disabled - reading discarded")
                else:
                    print("Send failed, time invalid - reading discarded")
                self.pixel.fill((255, 0, 0))
                time.sleep(2)
                self.pixel.fill((0, 0, 0))
                return False

            # Send to Home Assistant if configured
            if self.ha_client and self.ha_client.available:
                try:
                    self.ha_client.send_readings(readings_list)
                except Exception as e:
                    print(f"HA send error: {e}")

            # Try buffered data
            if self.enable_buffering and not self.buffer.is_empty():
                if force_send or self.buffer.size() >= 5:
                    self._send_buffered_data(feed_watchdog)

            self.last_successful_update = time.monotonic()
            self.pixel.fill((0, 255, 0))  # Green = success
            time.sleep(2)
            self.pixel.fill((0, 0, 0))
            return True

        except Exception as e:
            print(f"Update feeds error: {e}")
            self.pixel.fill((255, 0, 0))
            time.sleep(2)
            self.pixel.fill((0, 0, 0))
            return False

    def _send_readings(self, readings_list, timestamp=None, feed_watchdog=None):
        """Send current readings to Adafruit IO"""
        _beat(PH_AIO)
        try:
            if self.debug:
                if timestamp:
                    print(f"Sending to AIO with timestamp {timestamp}...")
                else:
                    print(f"Sending to AIO (server time) group={self.config.group_name}...")

            self.pixel.fill((255, 0, 0))  # Red = sending
            success_count = 0

            for name, value in readings_list:
                if value is not None:
                    try:
                        feed = self.network.get_or_create_feed(
                            self.config.group_name, name
                        )
                        if self.debug:
                            # print(f"  {feed['key']} = {value}")
                            pass
                        if timestamp:
                            self.network.send_data_with_timestamp(feed, value, timestamp)
                        else:
                            self.network.send_data(feed, value)
                        success_count += 1
                    except Exception as e:
                        print(f"Error sending {name}: {e}")

                    # Feed watchdog between sends
                    if feed_watchdog:
                        feed_watchdog()

            if success_count > 0:
                print(f"Sent {success_count}/{len(readings_list)} readings")
                return True
            return False

        except Exception as e:
            print(f"Send readings error: {e}")
            return False

    def _send_buffered_data(self, feed_watchdog=None):
        """Send buffered data with original timestamps"""
        if not self.enable_buffering or self.buffer.is_empty():
            return

        buffer_size = self.buffer.size()
        print(f"Sending {buffer_size} buffered readings...")

        try:
            buffered_data = self.buffer.get_all()
            sent_count = 0

            for entry in buffered_data:
                timestamp = entry['timestamp']
                readings = entry['readings']

                if self.debug:
                    print(f"  Buffered reading from {timestamp}")

                entry_success = False
                for name, value in readings.items():
                    if value is not None:
                        try:
                            feed = self.network.get_or_create_feed(
                                self.config.group_name, name
                            )
                            self.network.send_data_with_timestamp(feed, value, timestamp)
                            entry_success = True
                        except Exception as e:
                            print(f"Error sending buffered {name}: {e}")
                            error_str = str(e).lower()
                            if "throttle" in error_str or "429" in error_str or "too many" in error_str:
                                print("Rate limited, stopping buffer send")
                                if sent_count > 0:
                                    self.buffer.buffer = self.buffer.buffer[sent_count:]
                                    print(f"Kept {self.buffer.size()} for later")
                                return

                if entry_success:
                    sent_count += 1

                if feed_watchdog:
                    feed_watchdog()
                time.sleep(2)  # Rate limiting

            self.buffer.clear()
            print(f"Sent {sent_count} buffered readings")

        except Exception as e:
            print(f"Batch send error: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# System Manager
# ═══════════════════════════════════════════════════════════════════════════

class SystemManager:
    """Main system coordinator: boot, init, run loop, sleep/deep-sleep"""

    def __init__(self, board_table, debug=True, version="0.0.0",
                 enable_buffering=False, neopixel_brightness=0.1,
                 utc_offset=-7, sea_level_pressure=1016.26,
                 default_sleep=300, watchdog_timeout=120, tx_power=None,
                 rtc_time_fudge=45, alert_thresholds=None, ha_enabled=False,
                 inventory=None, on_healthy=None):

        self.debug = debug
        self.version = version
        self.enable_buffering = enable_buffering
        self.utc_offset = utc_offset
        self.sea_level_pressure = sea_level_pressure
        self.default_sleep = default_sleep
        self.watchdog_timeout = watchdog_timeout
        self.alert_thresholds = alert_thresholds or {}
        self.ha_enabled = ha_enabled
        self.inventory = inventory
        # Optional callback invoked once, the first time initialize()
        # succeeds (WiFi + AIO up, startup log sent). This is "deep enough
        # into the process" to mean the board is genuinely healthy --
        # used by code.py to clear its consecutive-crash counter.
        self._on_healthy = on_healthy

        self.reset_reason = microcontroller.cpu.reset_reason
        if debug:
            print(f"Reset reason: {self.reset_reason}")

        # Watchdog — set up first so it covers the entire init sequence
        # (I2C, sensors, network, display, etc.), not just the run loop.
        self.watchdog = None
        self._setup_watchdog()

        # Setup for crash logging if libraries are present
        self._pending_incident = crash_log.report_crash() if crash_log else None
        if breadcrumb is not None:
            breadcrumb.hb_init()
        
        # Board configuration (from code.py's BOARD_TABLE)
        from sensor_configs import BoardManager
        self.config = BoardManager.get_config(board_table, debug=debug)

        # Set the aio group feed in the inventory
        #print("Setting group name in inventory:",self.config.group_name)
        self.inventory.aio_group_key=self.config.group_name

        # NeoPixel status LED — per-board override takes precedence
        self.np_power = None
        if hasattr(board, 'NEOPIXEL_POWER'):
            self.np_power = digitalio.DigitalInOut(board.NEOPIXEL_POWER)
            self.np_power.direction = digitalio.Direction.OUTPUT
            self.np_power.value = True  # Start enabled in case board starts disabled
        brightness = self.config.neopixel_brightness or neopixel_brightness
        self.pixel = None
        if hasattr(board, 'NEOPIXEL'):
            self.pixel = neopixel.NeoPixel(board.NEOPIXEL, 1)
            self.pixel.brightness = brightness
            if debug:
                print(f"NeoPixel brightness: {brightness}")

        # Load AIO credentials
        from os import getenv
        self.aio_username = getenv("ADAFRUIT_AIO_USERNAME")
        self.aio_key = getenv("ADAFRUIT_AIO_KEY")
        if None in [self.aio_username, self.aio_key]:
            raise RuntimeError("Adafruit IO settings must be in settings.toml")

        # I2C initialization with optional frequency override
        self.i2c = self._init_i2c()

        # Sensor setup
        #if self.i2c is not None:
        #    self.config.setup_sensors(self.i2c, debug=debug)
        #else:
        #    print("WARNING: No I2C bus available, sensors not initialized")
        # Now let sensors get inited even in absence of i2c in case we want to send other feeds
        if self.i2c is None:
            print("WARNING: No I2C bus available, calling setup_sensors for other feeds")
        self.config.setup_sensors(self.i2c, debug=debug)

        # Network
        self.network = NetworkManager(
            self.aio_username, self.aio_key,
            utc_offset=utc_offset, tx_power=tx_power,
            enable_buffering=enable_buffering,
            rtc_time_fudge=rtc_time_fudge, debug=debug,
        )

        # Home Assistant client (lazy init)
        self.ha_client = None
        if ha_enabled:
            try:
                from ha_client import HAClient
                self.ha_client = HAClient(self.config.group_name, debug=debug)
            except Exception as e:
                print(f"HA client init error: {e}")

        # Display (lazy import via factory)
        try:
            from display_drivers import create_display
            self.config.display = create_display(self.config, debug=debug)
        except Exception as e:
            print(f"Display init error: {e}")

        # Sensor manager
        self.sensor_manager = SensorManager(
            self.config, self.network, self.pixel,
            self.alert_thresholds,
            enable_buffering=enable_buffering,
            debug=debug,
            ha_client=self.ha_client,
        )

        # Version manager — reports code.py's deployment VERSION to AIO
        self.version_manager = VersionManager(
            self.network, self.config.group_name, self.version, debug=debug
        )

        # Deep sleep state
        self._programming_mode = False
        self._remote_sleep_minutes = None

    def _init_i2c(self):
        """Initialize I2C bus with optional frequency override"""
        _beat(PH_I2C)
        freq = self.config.i2c_frequency
        i2c = None

        if freq:
            # Custom frequency needed (e.g., SCD30 at 10kHz)
            try:
                import busio
                i2c = busio.I2C(board.SCL, board.SDA, frequency=freq)
                if self.debug:
                    print(f"I2C initialized at {freq}Hz")
                return i2c
            except Exception as e:
                print(f"Custom I2C init error: {e}")

        # Standard initialization: try STEMMA_I2C first
        if self.config.use_stemma_i2c:
            try:
                i2c = board.STEMMA_I2C()
                if self.debug:
                    print("Using STEMMA_I2C")
                return i2c
            except:
                pass

        # Fallback to standard I2C
        try:
            i2c = board.I2C()
            if self.debug:
                print("Using board.I2C")
            return i2c
        except:
            #print("Fatal error: Unable to initialize I2C")
            return None

    def _setup_watchdog(self):
        try:
            self.watchdog = microcontroller.watchdog
            self.watchdog.timeout = self.watchdog_timeout
            self.watchdog.mode = watchdog.WatchDogMode.RESET
            if self.debug:
                print(f"Watchdog enabled: {self.watchdog_timeout}s")
        except Exception as e:
            print(f"Watchdog setup failed: {e}")
            self.watchdog = None

    def _feed_watchdog(self):
        if self.watchdog:
            self.watchdog.feed()

    def _disable_watchdog(self):
        """Disable watchdog before deep sleep (it keeps running!)"""
        if self.watchdog:
            try:
                self.watchdog.mode = None
                if self.debug:
                    print("Watchdog disabled")
            except:
                pass

    def _enable_watchdog(self):
        """Re-enable watchdog after light sleep"""
        if self.watchdog:
            try:
                self.watchdog.mode = watchdog.WatchDogMode.RESET
                if self.debug:
                    print("Watchdog re-enabled")
            except:
                pass

    def initialize(self):
        """Initialize network connections and report startup"""
        max_retries = 3
        for attempt in range(max_retries):
            self._feed_watchdog()

            if not self.network.connect_wifi():
                if attempt < max_retries - 1:
                    print("WiFi failed, retrying...")
                    time.sleep(10)
                    continue
                else:
                    print("WiFi failed after all retries")
                    return False

            if not self.network.setup_io():
                if attempt < max_retries - 1:
                    print("AIO setup failed, retrying...")
                    time.sleep(10)
                    continue
                else:
                    print("AIO setup failed after all retries")
                    return False

            # NTP sync fallback
            if not self.network.is_time_valid():
                print("Time not synced, attempting NTP...")
                self.network.sync_time()
            if not self.network.is_time_valid():
                print("WARNING: Time sync failed - using server time")

            # Report version
            self.version_manager.report_version()

            # Fetch remote config (sleep duration, programming mode)
            self._fetch_remote_config()

            # Log startup with all versions
            buf_status = "enabled" if self.enable_buffering else "DISABLED"
            startup_msg = (
                f"Started V{self.version} C{CONFIGS_VERSION} S{VERSION}\n"
                f"Reset reason - {self.reset_reason}"
                #f"\n   (buffering: {buf_status})"
            )
            self.sensor_manager.alert_manager.send_production_log(startup_msg)

            # Report incidents if enabled:
            if self._pending_incident:
                self.sensor_manager.alert_manager.send_incident_log(self._pending_incident)
                if self.inventory:
                    self.inventory.set_incident(self._pending_incident)
                    self.inventory.checkin()
                if crash_log:                 # technically redundant -- _pending_incident is
                    crash_log.clear_crash_record()   # only truthy when crash_log loaded -- but safe
            self._pending_incident = None
            
            # CircuitPython issue #10054: web workflow won't restart after
            # watchdog reset without a hard reset
            if self.reset_reason == microcontroller.ResetReason.WATCHDOG:
                microcontroller.reset()

            # We're genuinely up: WiFi connected, AIO reachable, startup
            # logged. Let code.py know it can clear its crash-loop counter.
            if self._on_healthy:
                try:
                    self._on_healthy()
                except Exception as e:
                    print(f"on_healthy callback error: {e}")

            return True

        return False

    def _fetch_remote_config(self):
        """Fetch programming mode and sleep from sensorcfg feeds"""
        config_feeds = {}

        if self.config.programming_mode_feed:
            config_feeds['programming_mode'] = self.config.programming_mode_feed
        if self.config.sleep_feed:
            config_feeds['remote_sleep'] = self.config.sleep_feed

        if config_feeds:
            updates = self.network.get_remote_config(
                self.config.group_name, config_feeds
            )
            self._programming_mode = updates.get('programming_mode', False)
            remote_sleep = updates.get('remote_sleep')
            if remote_sleep is not None:
                self._remote_sleep_minutes = max(remote_sleep, 1)

            if self.debug:
                print(f"Programming mode: {self._programming_mode}")
                if self._remote_sleep_minutes:
                    print(f"Remote sleep: {self._remote_sleep_minutes} min")

    def run(self, one_shot=False):
        """Main run loop with deep sleep support for battery boards"""
        if not self.initialize():
            print("Init failed, sleeping 60s")
            self._sleep(60)
            return

        if one_shot:
            self._feed_watchdog()
            readings = self.config.get_sensor_readings(debug=self.debug)
            self.config.update_display(readings)
            self.sensor_manager.update_feeds(feed_watchdog=self._feed_watchdog)
            return

        if self.debug:
            print(f"Entering {'deep sleep' if self.config.use_deep_sleep else 'continuous'} loop")

        if self.config.use_deep_sleep:
            self._run_deep_sleep_loop()
        else:
            self._run_continuous_loop()

    def _neopixel_sleep(self):
        if self.np_power is not None:
            self.np_power.value = False
            return

    def _run_continuous_loop(self):
        """Light-sleep loop for always-on boards (Rack, Garage, etc.)"""
        update_counter = 0
        while True:
            self._feed_watchdog()

            # Read sensors and update display
            readings = self.config.get_sensor_readings(debug=self.debug)
            self.config.update_display(readings)

            # Update the inventory system
            # print("Updating Inventory")
            self.inventory.checkin_if_due()

            force_buffer = self.enable_buffering and (update_counter % 10 == 0)
            if self.sensor_manager.update_feeds(
                force_send=force_buffer, feed_watchdog=self._feed_watchdog,
                readings=readings
            ):
                # Update the sleep time from a remote config if provided
                # Refresh remote config
                self._fetch_remote_config()
                # Determine sleep duration
                sleep_minutes = self._remote_sleep_minutes or (self.config.sleep_duration // 60)
                sleep_time = sleep_minutes * 60 # self.config.sleep_duration
            else:
                sleep_time = 60

            update_counter += 1
            self._sleep(sleep_time)

    def _run_deep_sleep_loop(self):
        """Deep-sleep loop for battery-powered boards (S3LP, S4LP)"""
        first_iteration = True
        while True:
            self._feed_watchdog()
            if self.debug:
                print("Deep sleep loop: reading sensors...")

            # On first iteration after boot, give CO2 sensor time to
            # have fresh data ready (boot init was long ago)
            """
            # Try without this for a while
            if first_iteration:
                first_iteration = False
                try:
                    if hasattr(self.config, 'scd') and self.config.scd is not None:
                        timeout = 10
                        while not self.config.scd.data_ready and timeout > 0:
                            if self.debug:
                                print("  Waiting for CO2 sensor...")
                            time.sleep(2)
                            timeout -= 2
                            self._feed_watchdog()
                except:
                    pass
            """

            # Read sensors
            readings = self.config.get_sensor_readings(debug=self.debug)

            # Update display BEFORE WiFi send (EPD refresh is slow)
            if self.debug:
                #print("Updating display...")
                pass
            self.config.update_display(readings)
            if self.debug:
                #print("Display update complete")
                pass

            self._feed_watchdog()

            # Connect WiFi, send data
            if self.network.ensure_wifi():
                # Re-setup IO if needed
                if self.network.io is None:
                    self.network.setup_io()

                if self.network.io is not None:
                    self.sensor_manager.update_feeds(
                        feed_watchdog=self._feed_watchdog,
                        readings=readings
                    )

                    # Refresh remote config
                    self._fetch_remote_config()

                self._feed_watchdog()

                # Update the inventory system. On a normal deep-sleep
                # cycle this is usually a no-op (checkin already happened
                # once in code.py this boot, and the interval likely
                # hasn't elapsed yet) -- but in _programming_mode this
                # loop runs repeatedly without a reset, and this is what
                # keeps the inventory server's view of the board current.
                
                # First set the battery level if it exists
                try:
                    bat = dict(readings).get('BatteryPct')
                    if bat is not None:
                        try:
                            # Might fail due to some being strings already
                            self.inventory.battery = f"{round(bat,1)}%"
                            if self.debug:
                                print("Reported inventory battery",round(bat,1))
                        except:
                            pass
                except:
                    pass

                self.inventory.checkin() # Just do the full checkin so we get battery info
                #self.inventory.checkin_if_due()

            self._feed_watchdog()

            # Disconnect WiFi if configured (battery savings)
            # Keep WiFi alive in programming mode for remote access
            if self.config.wifi_disconnect_after_send and not self._programming_mode:
                self.network.disconnect_wifi()

            # Determine sleep duration
            sleep_minutes = self._remote_sleep_minutes or (self.config.sleep_duration // 60)

            # Night sleep override (skip in programming mode)
            if self.config.night_sleep_enabled and not self._programming_mode:
                try:
                    ct = rtc.RTC().datetime
                    hour = ct.tm_hour
                    if hour >= self.config.night_sleep_hour:
                        night_mins = (self.config.night_wake_hour + 24 - hour) * 60
                        sleep_minutes = night_mins
                        if self.debug:
                            print(f"Night sleep: {sleep_minutes} min")
                except:
                    pass

            # Battery check — deep sleep for a month if critically low
            if self.config.min_battery_pct is not None:
                try:
                    bat = dict(readings).get('BatteryPct')
                    if bat is not None and float(bat) < self.config.min_battery_pct:
                        print(f"Battery critical ({bat}%), long sleep")
                        self._deep_sleep(30 * 24 * 60)  # 30 days
                        return
                except:
                    pass

            # Programming mode: light sleep instead of deep sleep
            # Keeps WiFi alive for remote web workflow access
            # Board reads sensors and sends data normally each cycle
            if self._programming_mode:
                if self.debug:
                    print(f"Programming mode: light sleep {sleep_minutes} min")
                self._disable_watchdog()
                time.sleep(sleep_minutes * 60)
                self._enable_watchdog()
                continue

            # Normal deep sleep
            self._deep_sleep(sleep_minutes)
            return  # Won't reach here — deep sleep resets

    def _deep_sleep(self, minutes):
        """Enter deep sleep for specified minutes"""
        _beat(PH_IDLE)
        # Use blue light to indicate deep-sleep
        self.pixel.fill((0, 0, 255))  # Blue
        time.sleep(3)
        if self.debug:
            print(f"Deep sleeping {minutes} minutes")
        self._disable_watchdog()
        self._neopixel_sleep() # disable neopixel power to save battery
        time.sleep(1)  # Settle time

        try:
            import alarm
            time_alarm = alarm.time.TimeAlarm(
                monotonic_time=time.monotonic() + minutes * 60
            )
            alarm.exit_and_deep_sleep_until_alarms(time_alarm)
        except ImportError:
            # alarm module not available on all boards
            print("alarm module not available, using light sleep")
            time.sleep(minutes * 60)

    def _sleep(self, duration):
        """Light sleep with watchdog feeding"""
        _beat(PH_IDLE)

        if self.debug:
            print(f"Sleeping {duration}s")
        self._feed_watchdog()
        sleep_start = time.monotonic()
        while time.monotonic() - sleep_start < duration:
            remaining = duration - (time.monotonic() - sleep_start)
            time.sleep(min(10, remaining))
            self._feed_watchdog()
