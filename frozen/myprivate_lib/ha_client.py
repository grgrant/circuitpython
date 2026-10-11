# SPDX-FileCopyrightText: 2025 Bob Grant
# SPDX-License-Identifier: MIT
"""
Home Assistant Client for Sensor System
=========================================
Sends sensor readings to a local Home Assistant instance via the REST API.
Requires a long-lived access token configured in settings.toml.

settings.toml entries needed:
    HA_URL = "http://homeassistant.local:8123"
    HA_TOKEN = "your_long_lived_access_token_here"

Entity IDs are auto-generated from group_name and sensor name:
    sensor.{group_name}_{sensor_name_lowercase}
    e.g. sensor.rack_swintemp, sensor.s3lp_co2

Version: 3.0.0

Usage:
    ha = HAClient(group_name="rack", debug=True)
    if ha.available:
        ha.send_readings([('SwInTemp', '78.5'), ('Humidity', '45.2')])
"""

from os import getenv


class HAClient:
    """
    Home Assistant REST API client.

    Sends sensor states via POST to /api/states/sensor.{entity_id}.
    Falls back gracefully if HA is unreachable or not configured.
    """

    def __init__(self, group_name, debug=False):
        self.group_name = group_name
        self.debug = debug
        self.available = False
        self._url = None
        self._token = None
        self._session = None

        # Load config from settings.toml
        try:
            self._url = getenv("HA_URL")
            self._token = getenv("HA_TOKEN")
        except Exception:
            pass

        if self._url and self._token:
            self.available = True
            if debug:
                print(f"HA client configured: {self._url}")
        else:
            if debug:
                print("HA client: not configured (HA_URL/HA_TOKEN missing from settings.toml)")

    def _ensure_session(self):
        """Lazy-create the requests session (only when first needed)"""
        if self._session is not None:
            return True
        try:
            import wifi
            import socketpool
            import ssl
            import adafruit_requests

            pool = socketpool.SocketPool(wifi.radio)
            self._session = adafruit_requests.Session(pool, ssl.create_default_context())
            return True
        except Exception as e:
            print(f"HA session setup error: {e}")
            return False

    def send_readings(self, readings):
        """
        Send sensor readings to Home Assistant.

        Args:
            readings: list of (name, value) tuples

        Returns:
            int: number of readings successfully sent
        """
        if not self.available:
            return 0

        if not self._ensure_session():
            return 0

        sent = 0
        for name, value in readings:
            if value is None:
                continue
            try:
                self._send_state(name, value)
                sent += 1
            except Exception as e:
                print(f"HA send error ({name}): {e}")

        if self.debug and sent > 0:
            print(f"HA: sent {sent}/{len(readings)} readings")

        return sent

    def _send_state(self, sensor_name, value):
        """
        POST a sensor state to Home Assistant REST API.

        Endpoint: POST /api/states/sensor.{group}_{name}
        """
        # Build entity_id: lowercase, underscores
        entity_id = f"sensor.{self.group_name}_{sensor_name.lower()}"
        url = f"{self._url}/api/states/{entity_id}"

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        # Determine unit of measurement from sensor name
        unit = self._guess_unit(sensor_name)

        payload = {
            "state": str(value),
            "attributes": {
                "unit_of_measurement": unit,
                "friendly_name": f"{self.group_name} {sensor_name}",
                "source": "circuitpython_sensor",
            }
        }

        import json
        body = json.dumps(payload)

        response = self._session.post(url, headers=headers, data=body)

        if response.status_code not in (200, 201):
            raise RuntimeError(f"HA API returned {response.status_code}")

        response.close()

    @staticmethod
    def _guess_unit(sensor_name):
        """Guess the unit of measurement from the sensor name"""
        name_lower = sensor_name.lower()
        if "temp" in name_lower:
            return "°F"
        elif "humidity" in name_lower or "rh" in name_lower:
            return "%"
        elif "co2" in name_lower:
            return "ppm"
        elif "battery" in name_lower and "pct" in name_lower:
            return "%"
        elif "battery" in name_lower and "volt" in name_lower:
            return "V"
        elif "rssi" in name_lower:
            return "dBm"
        elif "txpwr" in name_lower:
            return "dBm"
        else:
            return ""
