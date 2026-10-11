# SPDX-FileCopyrightText: Copyright (c) 2026 Bob Grant for grgrant
#
# SPDX-License-Identifier: MIT

"""
WiFi Connection Library for CircuitPython
==========================================

A robust WiFi connection manager with priority-based SSID selection,
connectivity verification, and NTP time synchronization.
Now Inherits properties from wifi for single wifi use.

Author: Bob Grant
Version: 0.1.4b (Make tx_power default to None and leave alone or change if desired and set a power)
         0.2.0 Make subclass of wifi
         0.2.1 Fix not-implemented RSSI (and local Signal Strength for Pico2W return 0)
               Also make the debug output more quiet
         0.2.2 Changes in ntp synchronization code.  Global symbols to reduce unnecessary resync and
               new resync timer with a resync frequency parameter default every 2 hours
         1.0.0 Release via GitHub -- running in production for months now
         1.0.1 Fix to take new NTP library defaults
         1.0.2 Fix to not reinstantiate NTP library every update
         1.0.3 Fix to remove wifi.wifi and add externally accessible ntp instance via wifi.ntp

License: MIT
"""
__repo__ = "https://github.com/grgrant/GRGrant_CircuitPython_wifi_connection.git"
VERSION = "1.0.3a"
DEBUG2 = False # Extra debugging prints - quiet if False
DEFAULT_NTP_UPDATE_FREQUENCY=3600

_DEFAULT_NTP_SERVERS = (
    "0.adafruit.pool.ntp.org",
    "1.adafruit.pool.ntp.org",
    # Let's have a few anycast servers in the middle
    "time.google.com",
    "time.cloudflare.com",
    "2.adafruit.pool.ntp.org",
    "3.adafruit.pool.ntp.org",
)

import time
import wifi
import socketpool
import ipaddress
import os
#import adafruit_ntp # Imported in time sync

# Globals to keep state across instantiations of class
_initialized = False
_time_synced = False
_last_sync_time = None
_first_connection = True
# persistent adafruit_ntp.NTP globals
_ntp = None
_ntp_pool = None
_ntp_config = None

class WiFiConnectionError(Exception):
    """Base exception for WiFi connection errors"""
    pass


class WiFiConnection():
    """
    Manages WiFi connections with priority-based SSID selection and verification.
    
    Features:
    - Priority-based SSID selection from settings.toml
    - Three verification levels (connection only, gateway, internet)
    - Optional NTP time synchronization
    - Comprehensive error handling
    - Optional callback system
    - Verbose logging support
    
    Example:
        >>> wifi = WiFiConnection(verbose=True)
        >>> if wifi.connect():
        ...     print(f"Connected to {wifi.connected_ssid}")
        ...     print(f"IP: {wifi.ip_address}")
    """
    
    def __init__(
        self,
        wifi_timeout=5,
        ping_timeout=5,
        overall_timeout=30,
        retry_attempts=0,
        enable_scan=True,
        force_reconnect=False,
        prefer_rssi=False,
        verification_level=2,
        ping_target="8.8.8.8",
        http_backup=True,
        http_test_url="http://wifitest.adafruit.com/testwifi/index.html",
        sync_time=True,
        ntp_server=_DEFAULT_NTP_SERVERS,
        ntp_socket_timeout=None,
        ntp_cache_seconds=300,
        ntp_update_frequency=DEFAULT_NTP_UPDATE_FREQUENCY,
        tz_offset=0,
        verbose=False,
        callbacks=None,
        tx_power=None # Default None to leave at board default
    ):
        """
        Initialize WiFi connection manager.
        
        Args:
            wifi_timeout (int): Connection timeout per SSID in seconds
            ping_timeout (int): Ping test timeout in seconds
            overall_timeout (int): Total process timeout in seconds
            retry_attempts (int): Number of retry attempts per SSID
            enable_scan (bool): Enable network scanning
            force_reconnect (bool): Force reconnection even if connected
            prefer_rssi (bool): Prioritize by signal strength over numeric order
            verification_level (int): 0=connection, 1=gateway, 2=internet
            ping_target (str): Internet ping target for Level 2 verification
            http_backup (bool): Enable HTTP fallback for Level 2
            http_test_url (str): URL for HTTP connectivity test
            sync_time (bool): Automatically sync time via NTP after connection
            ntp_server (str): NTP server address
            tz_offset (int): Timezone offset in hours (e.g., -8 for Los Angeles/PST)
            verbose (bool): Enable verbose logging
            callbacks (dict): Optional callback functions
            tx_power (int): Setting for radio tx_power reset to board default if None
            otherwise ensure our setting
        """
        global _initialized,_time_synced,_last_sync_time,_first_connection

        if not _initialized: # do one time items
            # Turns out that the ntp stuff is better as global params but keep this for
            # possible future use
            # print("First initialization set one time params")
            #first_connection = True  # Track if this is first connect() call
            # Connection state
            #_time_synced = False
            #_last_sync_time = None
            _initialized = True

        # Timeout settings
        self._radio = wifi.radio
        self.wifi_timeout = wifi_timeout
        self.ping_timeout = ping_timeout
        self.overall_timeout = overall_timeout
        self.retry_attempts = retry_attempts
        
        # Connection settings
        self.enable_scan = enable_scan
        self.force_reconnect = force_reconnect
        self.prefer_rssi = prefer_rssi
        self._tx_power = tx_power
        
        # Verification settings
        self.verification_level = verification_level
        self.ping_target = ping_target
        self.http_backup = http_backup
        self.http_test_url = http_test_url
        
        # NTP settings
        self.sync_time = sync_time
        self.ntp_server = ntp_server
        self.ntp_socket_timeout = ntp_socket_timeout
        self.ntp_cache_seconds = ntp_cache_seconds
        if tz_offset is None: tz_offset = 0 # None means UTC no offset
        self.tz_offset = tz_offset
        self.ntp_update_frequency = ntp_update_frequency
        
        # Logging and callbacks
        self.verbose = verbose
        self.callbacks = callbacks or {}


        # Connection state
        self._connected_ssid = None
        self._signal_strength = None
        self._ip_address = None
        self._gateway = None

        """
        _time_synced = False
        _last_sync_time = None
        try:
            print("In init - _first_connection:",_first_connection)
        except:
            pass
        """
        self._version=VERSION
        
        # Network configuration cache
        self._networks_config = []
        self._available_networks = []

        # Log initialization if verbose
        if self.verbose:
            print(f"[INIT] WiFiConnection (V{self._version}) initialized with verbose=True")
            print(f"[INIT] Verification level: {self.verification_level}")
            if _last_sync_time is None:
                sync_age = "None"
            else:
                sync_age = int(time.monotonic() - _last_sync_time)
            print(f"[INIT] Sync time: {self.sync_time}  Synced: {_time_synced} ",
                  f"Sync age: {sync_age} < {ntp_update_frequency}")

    # =========================================================================
    # Public Methods
    # =========================================================================
    
    def connect(self):
        """
        Attempt to connect to WiFi using priority-based SSID selection.
        
        Returns:
            bool: True if successfully connected and verified, False otherwise
        """
        global _initialized, _time_synced, _last_sync_time, _first_connection
        start_time = time.monotonic()
        
        try:
            self._log("Starting WiFi connection process", "INFO")
            self._trigger_callback('on_scanning', {})
            
            # Check overall timeout
            if self._check_timeout(start_time, self.overall_timeout):
                self._log("Overall timeout exceeded before starting", "ERROR")
                return False
            
            # Step 1: Check if already connected
            if self._is_already_connected():
                if not self.force_reconnect:
                    self._log(f"Already connected to {self._connected_ssid}, skipping reconnection WiFi TxPwr: {wifi.radio.tx_power}","INFO")
                    
                    # Sync time on first connect() call (e.g., after boot/deep sleep)
                    # but not on subsequent calls in the same session
                    if self.sync_time and not _time_synced and _first_connection:
                        self._log("First connection after boot/instantiation - syncing time", "INFO")
                        self.sync_ntp_time()
                    elif self.sync_time and self.ntp_resync_due():
                        self._log("Resynchronizing time", "INFO")
                        self.sync_ntp_time()

                    # Mark that we've done the first connection
                    _first_connection = False
                    
                    return True
                # I don't think we want to disconnect when force_reconnect is set to true
                # Let our program decide when an where to disconnect
                #else:
                #    self._log("Force reconnect enabled, disconnecting...", "INFO")
                #    self.disconnect()
            
            # Step 2: Enable radio if needed
            if not self._ensure_radio_enabled():
                self._log("Failed to enable WiFi radio", "ERROR")
                return False
            
            # Step 3: Load settings.toml
            if not self._load_settings():
                self._log("Failed to load settings.toml", "ERROR")
                return False
            
            # Step 4: Scan networks if enabled
            if self.enable_scan:
                if not self._scan_networks():
                    self._log("Network scan failed", "WARNING")
                    # Continue anyway with configured networks
            
            # Step 5: Prioritize SSIDs
            priority_list = self._prioritize_ssids()
            if not priority_list:
                self._log("No valid SSIDs to try", "ERROR")
                return False
            
            self._log(f"Will try {len(priority_list)} SSID(s) in priority order", "INFO")
            
            # Step 6: Try each SSID in priority order
            for network in priority_list:
                # Check overall timeout
                if self._check_timeout(start_time, self.overall_timeout):
                    self._log("Overall timeout exceeded", "ERROR")
                    self._trigger_callback('on_complete', {'success': False, 'reason': 'timeout'})
                    return False
                
                ssid = network['ssid']
                password = network['password']
                
                self._log(f"Attempting to connect to '{ssid}'...", "INFO")
                self._trigger_callback('on_connecting', {'ssid': ssid})
                
                # Try connection with retries
                for attempt in range(self.retry_attempts + 1):
                    if attempt > 0:
                        self._log(f"Retry attempt {attempt} for '{ssid}'", "INFO")
                    
                    if self._attempt_connection(ssid, password):
                        # Connection successful, now verify
                        self._trigger_callback('on_verification', {
                            'ssid': ssid,
                            'level': self.verification_level
                        })
                        
                        if self._verify_connectivity():
                            # Success!
                            self._trigger_callback('on_connected', {
                                'ssid': ssid,
                                'rssi': self._signal_strength,
                                'ip': self._ip_address
                            })
                            
                            # Sync time if enabled AND (first connection OR not synced yet)
                            if self.sync_time and (_first_connection or not _time_synced or self.ntp_resync_due()):
                                sync_type = "Initial connection"
                                if self.ntp_resync_due():
                                    sync_type = "Resync due"
                                self._log(f"Syncing time - {sync_type}", "INFO")
                                self.sync_ntp_time()
                            
                            # Mark that we've completed first connection
                            _first_connection = False
                            
                            self._log(f"Successfully connected to '{ssid}'", "SUCCESS")
                            self._trigger_callback('on_complete', {'success': True})
                            return True
                        else:
                            self._log(f"Verification failed for '{ssid}'", "WARNING")
                            # Disconnect and try next
                            self._disconnect_radio()
                            break  # Don't retry if verification fails
                    
                    # Connection failed, wait before retry
                    if attempt < self.retry_attempts:
                        time.sleep(1)
                
                # This SSID failed, trigger callback
                self._trigger_callback('on_failed', {
                    'ssid': ssid,
                    'reason': 'Connection or verification failed'
                })
            
            # All SSIDs failed
            self._log("Failed to connect to any configured network", "ERROR")
            self._trigger_callback('on_complete', {'success': False, 'reason': 'all_failed'})
            return False
            
        except Exception as e:
            self._log(f"Unexpected error during connection: {e}", "ERROR")
            self._trigger_callback('on_complete', {'success': False, 'reason': str(e)})
            return False
    
    def disconnect(self):
        """
        Disconnect from WiFi and turn off radio.
        
        Returns:
            bool: True if successfully disconnected
        """
        try:
            self._log("Disconnecting from WiFi...", "INFO")
            self._disconnect_radio()
            
            # Clear connection state but preserve first_connection flag
            # (first_connection tracks per-instantiation, not per-connection)
            self._connected_ssid = None
            self._signal_strength = None
            self._ip_address = None
            self._gateway = None
            # Note: We intentionally keep _time_synced and _first_connection
            # so we don't re-sync time if reconnecting in the same session
            
            return True
        except Exception as e:
            self._log(f"Error during disconnect: {e}", "ERROR")
            return False
    
    def is_connected(self):
        """
        Check if currently connected to WiFi.
        Used as external method and does a ntp resync if necessary
        For internal use use _is_already_connected()
        
        Returns:
            bool: True if connected
        """
        try:
            connected = wifi.radio.connected
        except:
            connected = False

        if connected and self.ntp_resync_due():
            try:
                self.sync_ntp_time()
            except:
                pass
        return connected

    def ntp_resync_due(self, verbose=False):
        global _initialized, _time_synced, _last_sync_time, _first_connection
        if _last_sync_time is None:
            return False
        else:
            secs_since_resync = int(time.monotonic() - _last_sync_time)
            resync = (secs_since_resync) > self.ntp_update_frequency
            if verbose:
                if resync:
                    print(f"NTP resync -- secs since last sync {secs_since_resync}")
                else:
                    print(f"Next NTP resync in {self.ntp_update_frequency-secs_since_resync} seconds")
            return resync

    def sync_ntp_time(self):
        """
        Manually synchronize time via NTP.

        Reuses a single, module-level adafruit_ntp.NTP instance so the
        library's own DNS re-resolve throttling (reresolve_interval,
        default 3600s) and server failover rotation persist across calls.

        Returns:
            bool: True if time sync successful
        """
        global _initialized, _time_synced, _last_sync_time, _first_connection
        global _ntp, _ntp_pool, _ntp_config
        if not self._is_already_connected():
            self._log("Cannot sync time: not connected to WiFi", "WARNING")
            return False

        self._log(f"Syncing time with NTP server: {self.ntp_server}", "INFO")
        self._trigger_callback('on_time_sync', {'server': self.ntp_server})

        try:
            import adafruit_ntp
        except ImportError as e:
            self._log(f"adafruit_ntp library not available: {e}", "WARNING")
            self._log("Install adafruit_ntp from CircuitPython library bundle", "WARNING")
            self._trigger_callback('on_time_sync', {
                'server': self.ntp_server,
                'success': False,
                'error': 'adafruit_ntp not installed'
            })
            return False

        try:
            # Build the NTP client (and socket pool) once, then reuse.
            # Rebuild only if the relevant NTP settings actually change,
            # which also covers a later instance created with a different
            # server / tz_offset / socket_timeout.
            ntp_config = (self.ntp_server, self.tz_offset, self.ntp_socket_timeout, self.ntp_cache_seconds)
            if _ntp is None or _ntp_config != ntp_config:
                if _ntp_pool is None:
                    if DEBUG2: self._log("Creating socket pool...", "DEBUG")
                    _ntp_pool = socketpool.SocketPool(wifi.radio)
                    if DEBUG2: self._log("Socket pool created", "DEBUG")

                kwargs = {"tz_offset": self.tz_offset, "cache_seconds": self.ntp_cache_seconds}
                if self.ntp_server is not None:
                    kwargs["server"] = self.ntp_server
                if self.ntp_socket_timeout is not None:
                    kwargs["socket_timeout"] = self.ntp_socket_timeout

                self._log(f"Creating NTP client for server: {self.ntp_server}", "DEBUG")
                self._log(f"Using timezone offset: {self.tz_offset} hours", "DEBUG")
                _ntp = adafruit_ntp.NTP(_ntp_pool, **kwargs)
                _ntp_config = ntp_config
                if DEBUG2: self._log("NTP client created (persistent)", "DEBUG")
            else:
                if DEBUG2: self._log("Reusing persistent NTP client", "DEBUG")

            # .datetime handles query caching, failover, and hourly
            # re-resolve internally now that the object survives.
            self._log("Requesting time from NTP server...", "DEBUG")
            ntp_time = _ntp.datetime
            self._log(f"Received NTP time: {ntp_time}", "DEBUG")

            if DEBUG2: self._log("Setting system RTC...", "DEBUG")
            import rtc
            rtc.RTC().datetime = ntp_time
            if DEBUG2: self._log("RTC updated successfully", "DEBUG")

            _time_synced = True
            _last_sync_time = time.monotonic()

            self._log("Time synchronized successfully", "SUCCESS")
            self._trigger_callback('on_time_sync', {
                'server': self.ntp_server,
                'success': True
            })
            return True

        except OSError as e:
            self._log(f"Network error during NTP sync: {e}", "ERROR")
            self._log("Check that NTP port 123 is not blocked on your network", "WARNING")
            self._trigger_callback('on_time_sync', {
                'server': self.ntp_server,
                'success': False,
                'error': f'Network error: {e}'
            })
            return False

        except Exception as e:
            self._log(f"Failed to sync time: {type(e).__name__}: {e}", "ERROR")
            self._trigger_callback('on_time_sync', {
                'server': self.ntp_server,
                'success': False,
                'error': str(e)
            })
            return False


    # =========================================================================
    # Properties (Read-only)
    # =========================================================================

    @property
    def ntp(self):
        """The persistent adafruit_ntp.NTP instance (None until first sync)."""
        return _ntp
    
    @property
    def radio(self):
        return self._radio

    @property
    def connected_ssid(self):
        """Get the SSID of the currently connected network"""
        return self._connected_ssid
    
    @property
    def rssi(self):
        """
        Make a same name as parent class for consistency
        Get the RSSI value of the current connection
        RP2xxx W models don't do RSSI so return 0 to not break existing code
        """
        try:
            rssi_val = wifi.radio.ap_info.rssi  # Get the current signal strength
        except:
            rssi_val = 0
        return rssi_val

    @property
    def signal_strength(self):
        """Get the RSSI value of the current connection -- left in for backwards compatibility """
        return self.rssi # Use previous property with its error handling

    @property
    def ip_address(self):
        """Get the assigned IP address"""
        return self._ip_address
    
    @property
    def gateway(self):
        """Get the gateway IP address"""
        return self._gateway
    
    @property
    def time_synced(self):
        """Check if time has been synchronized via NTP"""
        return _time_synced
    
    @property
    def last_sync_time(self):
        """Get timestamp of last NTP sync"""
        return _last_sync_time
    
    @property
    def connected(self):
        return self.is_connected()

    @property
    def version(self):
        """Return version of this library"""
        return self._version

    #@property
    #def wifi(self):
        """
        Disabled remove in subsequent version 
        
        Return the parent wifi object to access various extra properties from parent
        Accessed as wifi.wifi if this library is instantiated as wifi
        """
    #  print("Using deprecated wifi.wifi  -- not needed anymore")
    # return wifi

    @property
    def tx_power(self):
        """
        Return the currently requested tx_power from initialization
        May have been changed
        """
        return self._tx_power

    @tx_power.setter
    def tx_power(self, value):
        """
        Request a new tx_power.  Change in both local requested setting and with the radio if enabled
        """
        self._tx_power = value
        try:
            self.radio.tx_power = value
        except:
            # I'm not sure we ever get here because when the radio is disconnected we can still set
            print("Unable to reset tx_power on radio because it is not enabled")

    # =========================================================================
    # Private Methods - Radio Management
    # =========================================================================
    
    def _ensure_radio_enabled(self):
        """Enable WiFi radio if not already enabled"""
        try:
            if not wifi.radio.enabled:
                self._log("Enabling WiFi radio...", "INFO")
                wifi.radio.enabled = True
                time.sleep(0.5)  # Give radio time to initialize
            self._log(f"Tx Power settings: Radio: {wifi.radio.tx_power} ClassProperty: {self._tx_power}", "INFO")
            if self._tx_power is not None and wifi.radio.tx_power != self._tx_power:
                # WiFi tx_power can be changed using the class property so reset if it is chaged
                wifi.radio.tx_power = self._tx_power
                self._log(f"New Tx Power settings: Radio: {wifi.radio.tx_power} ClassProperty: {self._tx_power}","INFO")
            return True
        except Exception as e:
            self._log(f"Failed to enable radio: {e}", "ERROR")
            return False
    
    def _disconnect_radio(self):
        """Disconnect from WiFi and disable radio"""
        try:
            if wifi.radio.connected:
                wifi.radio.stop_station()
            wifi.radio.enabled = False
        except Exception as e:
            self._log(f"Error disabling radio: {e}", "WARNING")
    
    def _is_already_connected(self):
        """Check if already connected and update state"""
        try:
            if wifi.radio.connected and wifi.radio.ipv4_address:
                self._connected_ssid = str(wifi.radio.ap_info.ssid)
                self._signal_strength = self.rssi # Use property with error checking
                self._ip_address = str(wifi.radio.ipv4_address)
                
                # Try to get gateway
                try:
                    self._gateway = str(wifi.radio.ipv4_gateway)
                except Exception:
                    self._gateway = None
                
                return True
        except Exception:
            pass
        return False
    
    # =========================================================================
    # Private Methods - Configuration
    # =========================================================================
    
    def _load_settings(self):
        """Load WiFi credentials from settings.toml"""
        try:
            self._log("Loading settings.toml...", "INFO")
            self._networks_config = []
            
            # Try to read environment variables
            # Priority 0: CIRCUITPY_WIFI_SSID
            try:
                ssid = os.getenv("CIRCUITPY_WIFI_SSID")
                password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
                if ssid and password:
                    self._networks_config.append({
                        'ssid': ssid,
                        'password': password,
                        'priority': 0,
                        'available': False,
                        'rssi': None
                    })
                    self._log(f"Loaded CIRCUITPY_WIFI_SSID: {ssid}", "DEBUG")
            except Exception:
                pass
            
            # Priority 1+: WIFI_SSID0-9
            for i in range(10):
                try:
                    ssid = os.getenv(f"WIFI_SSID{i}")
                    password = os.getenv(f"WIFI_PASSWORD{i}")
                    if ssid and password:
                        self._networks_config.append({
                            'ssid': ssid,
                            'password': password,
                            'priority': i + 1,  # Priority after CIRCUITPY
                            'available': False,
                            'rssi': None
                        })
                        self._log(f"Loaded WIFI_SSID{i}: {ssid}", "DEBUG")
                except Exception:
                    continue
            
            if not self._networks_config:
                self._log("No WiFi credentials found in settings.toml", "ERROR")
                return False
            
            self._log(f"Loaded {len(self._networks_config)} network(s) from settings", "INFO")
            return True
            
        except Exception as e:
            self._log(f"Error loading settings: {e}", "ERROR")
            return False
    
    # =========================================================================
    # Private Methods - Network Scanning
    # =========================================================================
    
    def _scan_networks(self):
        """Scan for available WiFi networks"""
        try:
            self._log("Scanning for WiFi networks...", "INFO")
            self._available_networks = []
            
            # Perform scan
            networks = wifi.radio.start_scanning_networks()
            time.sleep(1)  # Give scan time to complete
            networks_list = list(networks)
            wifi.radio.stop_scanning_networks()
            
            # Store available networks
            for network in networks_list:
                self._available_networks.append({
                    'ssid': str(network.ssid),
                    'rssi': network.rssi,
                    'channel': network.channel
                })
            
            self._log(f"Found {len(self._available_networks)} network(s)", "INFO")
            
            # Mark configured networks as available
            for config_net in self._networks_config:
                for avail_net in self._available_networks:
                    if config_net['ssid'] == avail_net['ssid']:
                        config_net['available'] = True
                        config_net['rssi'] = avail_net['rssi']
                        self._log(f"Network '{config_net['ssid']}' is available (RSSI: {avail_net['rssi']})", "DEBUG")
                        break
            
            return True
            
        except Exception as e:
            self._log(f"Error during network scan: {e}", "WARNING")
            return False
    
    def _prioritize_ssids(self):
        """Create prioritized list of SSIDs to try"""
        try:
            # Filter to only available networks (or all if scan disabled)
            if self.enable_scan:
                candidates = [n for n in self._networks_config if n['available']]
            else:
                candidates = self._networks_config
            
            if not candidates:
                self._log("No candidate networks available", "WARNING")
                return []
            
            # Sort based on preference
            if self.prefer_rssi:
                # Sort by RSSI (highest first), then by priority
                candidates.sort(key=lambda x: (x['rssi'] or -100), reverse=True)
                self._log("Prioritizing by signal strength (RSSI)", "DEBUG")
            else:
                # Sort by numeric priority (lowest number first)
                candidates.sort(key=lambda x: x['priority'])
                self._log("Prioritizing by numeric order", "DEBUG")
            
            return candidates
            
        except Exception as e:
            self._log(f"Error prioritizing SSIDs: {e}", "ERROR")
            return []
    
    # =========================================================================
    # Private Methods - Connection
    # =========================================================================
    
    def _attempt_connection(self, ssid, password):
        """
        Attempt to connect to a specific SSID.
        
        Returns:
            bool: True if connection successful
        """
        try:
            # Connect to network
            wifi.radio.connect(ssid=ssid, password=password, timeout=self.wifi_timeout)
            # Wait a moment for connection to stabilize
            time.sleep(1)
            
            # Verify connection and get info
            if wifi.radio.connected and wifi.radio.ipv4_address:
                self._connected_ssid = ssid
                self._signal_strength = self.rssi
                self._ip_address = str(wifi.radio.ipv4_address)
                
                # Try to get gateway
                try:
                    self._gateway = str(wifi.radio.ipv4_gateway)
                except Exception:
                    self._gateway = None
                
                self._log(f"Connected to '{ssid}' - IP: {self._ip_address}, RSSI: {self._signal_strength}", "DEBUG")
                return True
            
            return False
            
        except Exception as e:
            self._log(f"Connection attempt failed for '{ssid}': {e}", "DEBUG")
            return False
    
    # =========================================================================
    # Private Methods - Verification
    # =========================================================================
    
    def _verify_connectivity(self):
        """
        Verify connectivity based on verification level.
        
        Returns:
            bool: True if verification passed
        """
        try:
            if self.verification_level == 0:
                return self._verify_level_0()
            elif self.verification_level == 1:
                return self._verify_level_1()
            elif self.verification_level == 2:
                return self._verify_level_2()
            else:
                self._log(f"Invalid verification level: {self.verification_level}", "WARNING")
                return True  # Default to accepting connection
                
        except Exception as e:
            self._log(f"Verification error: {e}", "ERROR")
            return False
    
    def _verify_level_0(self):
        """Level 0: Accept connection without verification"""
        self._log("Verification Level 0: Connection only (no tests)", "DEBUG")
        return True
    
    def _verify_level_1(self):
        """Level 1: Verify gateway connectivity"""
        self._log("Verification Level 1: Testing gateway connectivity", "DEBUG")
        
        if not self._gateway:
            self._log("Gateway address not available", "WARNING")
            return False
        
        return self._ping_test(self._gateway)
    
    def _verify_level_2(self):
        """Level 2: Verify internet connectivity"""
        self._log("Verification Level 2: Testing internet connectivity", "DEBUG")
        
        # Try ping first
        if self._ping_test(self.ping_target):
            return True
        
        # Try HTTP backup if enabled
        if self.http_backup:
            self._log("Ping failed, trying HTTP backup test", "DEBUG")
            return self._http_test()
        
        return False
    
    def _ping_test(self, target):
        """
        Ping a target address.
        
        Args:
            target (str): IP address to ping
            
        Returns:
            bool: True if ping successful
        """
        try:
            self._log(f"Pinging {target}...", "DEBUG")
            
            # Convert to IP address if needed
            if isinstance(target, str):
                target_ip = ipaddress.ip_address(target)
            else:
                target_ip = target
            
            # Perform ping
            result = wifi.radio.ping(target_ip, timeout=self.ping_timeout)
            
            if result is not None and result > 0:
                self._log(f"Ping successful: {result*1000:.1f}ms", "DEBUG")
                return True
            else:
                self._log(f"Ping failed to {target}", "DEBUG")
                return False
                
        except Exception as e:
            self._log(f"Ping error: {e}", "DEBUG")
            return False
    
    def _http_test(self):
        """
        Test internet connectivity via HTTP request.
        
        Returns:
            bool: True if HTTP test successful
        """
        try:
            self._log(f"Testing HTTP connectivity to {self.http_test_url}", "DEBUG")
            
            import ssl
            pool = socketpool.SocketPool(wifi.radio)
            
            # Parse URL
            url = self.http_test_url
            if url.startswith("http://"):
                use_ssl = False
                host = url[7:].split('/')[0]
                path = '/' + '/'.join(url[7:].split('/')[1:]) if '/' in url[7:] else '/'
                port = 80
            elif url.startswith("https://"):
                use_ssl = True
                host = url[8:].split('/')[0]
                path = '/' + '/'.join(url[8:].split('/')[1:]) if '/' in url[8:] else '/'
                port = 443
            else:
                return False
            
            # Create socket
            if use_ssl:
                context = ssl.create_default_context()
                sock = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
                sock.settimeout(self.ping_timeout)
                sock.connect((host, port))
                sock = context.wrap_socket(sock, server_hostname=host)
            else:
                sock = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
                sock.settimeout(self.ping_timeout)
                sock.connect((host, port))
            
            # Send HTTP request
            request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            sock.send(request.encode())

            # Read response
            buffer = bytearray(512) # Saving space -- should fit in 512 rather than prev 1024
            num_bytes = sock.recv_into(buffer)
            response = buffer[:num_bytes].decode('utf-8')
            sock.close()
            del buffer

            # Check for successful response
            if "200 OK" in response or "HTTP/1.1 200" in response or "HTTP/1.0 200" in response:
                self._log("HTTP test successful", "DEBUG")
                return True
            else:
                self._log("HTTP test failed: invalid response", "DEBUG")
                return False
                
        except Exception as e:
            self._log(f"HTTP test error: {e}", "DEBUG")
            return False
    
    # =========================================================================
    # Private Methods - Utilities
    # =========================================================================
    
    def _check_timeout(self, start_time, timeout):
        """Check if timeout has been exceeded"""
        elapsed = time.monotonic() - start_time
        return elapsed >= timeout
    
    def _log(self, message, level="INFO"):
        """Log a message if verbose mode is enabled"""
        if self.verbose:
            try:
                timestamp = time.monotonic()
                print(f"[{timestamp:.3f}] [{level}] {message}")
            except Exception:
                # Fallback if formatting fails
                print(f"[{level}] {message}")
    
    def _trigger_callback(self, event, data):
        """Trigger a callback function if registered"""
        if event in self.callbacks and callable(self.callbacks[event]):
            try:
                self.callbacks[event](data)
            except Exception as e:
                self._log(f"Callback error for '{event}': {e}", "WARNING")


    # --- The magic: everything else proxies to the wifi module ---
    def __getattr__(self, name):
       return getattr(wifi, name)
