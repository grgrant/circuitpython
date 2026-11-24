# SPDX-License-Identifier: MIT
"""
sntp_clock - shared wall-clock time for CircuitPython via SNTP.

Keeps time as an (offset, rate) correction applied to time.monotonic_ns().
Never touches the RTC. Accuracy over local Wi-Fi to a good NTP server is
typically ~1 ms or better.

How it works:
  * A sync picks the SNTP exchange with the smallest round-trip delay
    (least disturbed by Wi-Fi queuing).
  * Offset = ((t2 - t1) + (t3 - t4)) / 2, standard NTP math.
  * A least-squares fit over recent syncs estimates crystal drift (ppm),
    so now_us() stays accurate between syncs.

Two ways to keep it synced:
  * Burst (default): sync_if_due() runs a full burst of `samples` exchanges
    every resync interval. Most accurate per sync, but blocks the caller.
  * Incremental (incremental=True): the first sync is a full burst; after
    that, sync_if_due() does ONE exchange every `poll_s` seconds and keeps
    the samples. Every resync interval it commits the lowest-delay sample
    from that window. Same quality over time, with only a few ms of
    blocking per call.

Usage:
    clock = SNTPClock(pool, "192.168.1.10", incremental=True)
    clock.sync()              # full burst at startup
    t = clock.now_us()        # microseconds since Unix epoch (UTC)
    clock.sync_if_due()       # call often from your main loop
"""

import gc
import struct
import time

_NTP_TO_UNIX = 2_208_988_800  # seconds from 1900-01-01 to 1970-01-01
_NTP_PORT = 123
_MAX_WINDOW = 32


def _local_us():
    return time.monotonic_ns() // 1000


def _ntp_to_us(buf, pos):
    secs, frac = struct.unpack_from(">II", buf, pos)
    return (secs - _NTP_TO_UNIX) * 1_000_000 + ((frac * 1_000_000) >> 32)


class SNTPClock:
    def __init__(
        self,
        pool,
        server,
        samples=8,
        timeout=0.5,
        resync_s=300,
        fast_resync_s=30,
        history=8,
        min_fit_span_s=60,
        incremental=False,
        poll_s=30,
        poll_timeout=0.1,
        gc_each_sample=True,
    ):
        self._pool = pool
        self._server = server
        self._samples = samples
        self._timeout = timeout
        self._resync_us = resync_s * 1_000_000
        self._fast_resync_us = fast_resync_s * 1_000_000
        self._retry_us = 30 * 1_000_000
        self._history = history
        self._min_span_us = min_fit_span_s * 1_000_000

        self._incremental = incremental
        self._poll_us = poll_s * 1_000_000
        self._poll_timeout = poll_timeout
        self._gc_each_sample = gc_each_sample

        self._req = bytearray(48)
        self._resp = bytearray(48)
        self._addr = None

        self._fits = []  # [(local_us, offset_us), ...]
        self._window = []  # incremental samples: [(local_mid, offset, delay)]
        self._anchor_local = 0
        self._anchor_offset = 0
        self._rate = 0.0  # ppm == microseconds of offset change per second
        self._next_sync = None  # next burst (burst mode) or poll (incremental)
        self._next_commit = None  # incremental: when to commit best sample

        self.synced = False
        self.last_delay_us = None  # round-trip delay of last committed sample

    # ---------------- public ----------------

    def now_us(self):
        """Microseconds since the Unix epoch (UTC), drift-corrected."""
        local = _local_us()
        dt = local - self._anchor_local
        return local + self._anchor_offset + int(self._rate * dt / 1_000_000)

    @property
    def offset_us(self):
        return self._anchor_offset

    @property
    def rate_ppm(self):
        return self._rate

    def sync(self):
        """Full burst of SNTP exchanges (blocking). Returns True on success."""
        best = None
        sock = self._open(self._timeout)
        try:
            for _ in range(self._samples):
                if self._gc_each_sample:
                    gc.collect()  # keep GC out of the timed window
                try:
                    r = self._exchange(sock)
                except OSError:  # timeout or network hiccup
                    r = None
                if r and (best is None or r[2] < best[2]):
                    best = r
                time.sleep(0.02)  # be polite to the server
        finally:
            sock.close()

        now = _local_us()
        if best is None:
            self._next_sync = now + self._retry_us
            return False

        self._commit(best, now)
        self._window = []
        if self._incremental:
            self._next_sync = now + self._poll_us
        else:
            self._next_sync = self._next_commit
        return True

    def sync_if_due(self):
        """Call often. Does a burst or a single poll only when one is due."""
        now = _local_us()
        if self._next_sync is not None and now < self._next_sync:
            return False
        if not self._incremental or not self.synced:
            return self.sync()
        return self._poll(now)

    # ---------------- internals ----------------

    def _open(self, timeout):
        if self._addr is None:
            self._addr = self._pool.getaddrinfo(self._server, _NTP_PORT)[0][4]
        sock = self._pool.socket(self._pool.AF_INET, self._pool.SOCK_DGRAM)
        sock.settimeout(timeout)
        return sock

    def _poll(self, now):
        """Incremental mode: one exchange, commit best of window when due."""
        self._next_sync = now + self._poll_us
        sock = self._open(self._poll_timeout)
        try:
            r = self._exchange(sock)
        except OSError:
            r = None
        finally:
            sock.close()

        if r:
            self._window.append(r)
            if len(self._window) > _MAX_WINDOW:
                self._window.pop(0)

        if now >= self._next_commit and self._window:
            best = self._window[0]
            for s in self._window:
                if s[2] < best[2]:
                    best = s
            self._commit(best, _local_us())
            self._window = []
            return True
        return False

    def _commit(self, sample, now):
        local_mid, offset, delay = sample
        self.last_delay_us = delay
        self._record(local_mid, offset)
        self.synced = True
        fast = len(self._fits) < 3
        self._next_commit = now + (self._fast_resync_us if fast else self._resync_us)

    def _exchange(self, sock):
        req = self._req
        for i in range(48):
            req[i] = 0
        req[0] = 0x1B  # LI=0, VN=3, Mode=3 (client)

        t1 = _local_us()
        # Stamp our transmit field so the reply's originate field can be
        # matched, discarding late replies from earlier timed-out requests.
        struct.pack_into(">Q", req, 40, t1)
        sock.sendto(req, self._addr)
        n, _ = sock.recvfrom_into(self._resp)
        t4 = _local_us()

        resp = self._resp
        if n < 48 or (resp[0] & 0x07) != 4 or resp[1] == 0:
            return None  # not a server reply, or kiss-o'-death
        if resp[24:32] != req[40:48]:
            return None  # stale reply

        t2 = _ntp_to_us(resp, 32)  # server receive
        t3 = _ntp_to_us(resp, 40)  # server transmit
        offset = ((t2 - t1) + (t3 - t4)) // 2
        delay = (t4 - t1) - (t3 - t2)
        return (t1 + t4) // 2, offset, delay

    def _record(self, local, offset):
        fits = self._fits
        fits.append((local, offset))
        if len(fits) > self._history:
            fits.pop(0)

        x0, y0 = fits[0]
        if len(fits) >= 2 and local - x0 >= self._min_span_us:
            # Least-squares fit of offset vs time. Values are made relative
            # to the first sample so single-precision floats stay accurate.
            n = len(fits)
            xs = [(x - x0) / 1_000_000 for x, _ in fits]  # seconds
            ys = [y - y0 for _, y in fits]  # microseconds
            mx = sum(xs) / n
            my = sum(ys) / n
            sxx = sum((x - mx) ** 2 for x in xs)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            if sxx > 0:
                self._rate = sxy / sxx
                # Anchor on the fitted line, not the raw (noisy) sample.
                fitted = my + self._rate * (xs[-1] - mx)
                self._anchor_local = local
                self._anchor_offset = y0 + int(fitted)
                return

        self._anchor_local = local
        self._anchor_offset = offset
