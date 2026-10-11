# SPDX-FileCopyrightText: 2025 Bob Grant
# SPDX-License-Identifier: MIT
"""
Display Drivers for Sensor System
===================================
Reusable display classes for different screen types.
All drivers implement the same interface: __init__(config) and update(readings).

Lazy imports: each class only imports its display libraries when instantiated,
so boards without displays pay no memory cost.

Supported displays:
  - TFTDisplay:    Built-in TFT (Feather ESP32-S3 Reverse TFT) - CO2/Temp/Humidity
  - TFTDevDisplay: Feather-S2 Regular TFT -- for now, however trying to just use main TFT display
  - EPD29Display:  2.9" E-Paper (UC8151D) - CO2/Temp/Humidity/Time/Battery
  - EPDThinkInk29: 2.9" E-Paper (SSD1680) - CO2/Temp/Humidity/Time/Battery
  - EPD213Display: 2.13" E-Paper (SSD1680) - CO2/Temp/Humidity/Time/Battery
  - OLEDDisplay:   128x32 I2C OLED (SSD1306) - Compact CO2/Temp/Humidity
  - NullDisplay:   No-op for boards without displays

Version: 3.0.4
"""


class NullDisplay:
    """No-op display for boards without screens"""

    def __init__(self, config, debug=False):
        pass

    def update(self, readings=None):
        pass


class TFTDevDisplay:
    """
    Built-in color TFT display (Feather ESP32-S2 TFT)
    Shows CO2, temperature and humidity.

    CO2 color coding:
      ≤ 1000 ppm = Green
      ≤ 1400 ppm = Yellow
      > 1400 ppm = Red
    """

    GREEN = 0x00FF00
    YELLOW = 0xFFFF00
    RED = 0xFF0000
    WHITE = 0xFFFFFF

    def __init__(self, config, debug=False):
        import displayio
        from adafruit_display_text import label
        import terminalio
        import board

        self._displayio = displayio
        self._label = label
        self._font = terminalio.FONT
        self._config = config

        self.hw_display = board.DISPLAY
        self.hw_display.rotation = 180
        if debug:
            print(f"TFT Dev Display: {self.hw_display.width}x{self.hw_display.height}")

    def update(self, readings=None):
        """Update TFT with current CO2, temperature, humidity from readings"""
        if readings is None:
            return

        # Extract values from readings list of tuples
        values = dict(readings) if isinstance(readings, list) else readings
        co2_raw = values.get('CO2')
        temp_raw = values.get('Temp')
        humidity_raw = values.get('Humidity')

        try:
            co2 = int(float(co2_raw)) if co2_raw else 0
        except (ValueError, TypeError):
            co2 = 0

        # Color code CO2
        if co2 <= 1000:
            color = self.GREEN
        elif co2 <= 1400:
            color = self.YELLOW
        else:
            color = self.RED

        # Build display
        main_group = self._displayio.Group()

        # CO2 - large, centered
        co2_label = self._label.Label(
            self._font,
            text=str(co2),
            color=color,
            x=60,
            y=30,
            scale=7
        )
        main_group.append(co2_label)

        # Temperature - bottom left, no decimals
        # Shift right when < 100 (2 digits) to avoid left-edge crowding
        try:
            temp_val = float(temp_raw) if temp_raw else 0
            temp_str = f"{temp_val:0.0f}"
        except (ValueError, TypeError):
            temp_val = 0
            temp_str = "--"
        x_off = 20 if temp_val < 100 else 0

        temp_label = self._label.Label(
            self._font,
            text=temp_str,
            color=self.WHITE,
            x=x_off,
            y=110,
            scale=6
        )
        main_group.append(temp_label)

        # Humidity - bottom right, no decimals
        try:
            hum_val = float(humidity_raw) if humidity_raw else 0
            hum_str = f"{hum_val:0.0f}%"
        except (ValueError, TypeError):
            hum_str = "--%"

        hum_label = self._label.Label(
            self._font,
            text=hum_str,
            color=0x0000FF,
            x=130,
            y=110,
            scale=6
        )
        main_group.append(hum_label)

        self.hw_display.root_group = main_group


class TFTDisplay:
    """
    Built-in color TFT display (Feather ESP32-S3 Reverse TFT)
    Shows CO2 prominently with color coding, plus temperature and humidity.
    No battery display per Bob's request.

    CO2 color coding:
      ≤ 1000 ppm = Green
      ≤ 1400 ppm = Yellow
      > 1400 ppm = Red
    """

    GREEN = 0x00FF00
    YELLOW = 0xFFFF00
    RED = 0xFF0000
    WHITE = 0xFFFFFF

    def __init__(self, config, debug=False):
        import displayio
        from adafruit_display_text import label
        import terminalio
        import board

        self._displayio = displayio
        self._label = label
        self._font = terminalio.FONT
        self._config = config

        self.hw_display = board.DISPLAY
        #self.hw_display.rotation = 0 # Maybe do this when necessary in boot.py/Settings.toml
        if debug:
            print(f"TFT Display: {self.hw_display.width}x{self.hw_display.height}")

    def update(self, readings=None):
        """Update TFT with current CO2, temperature, humidity from readings"""
        if readings is None:
            return

        # Extract values from readings list of tuples
        values = dict(readings) if isinstance(readings, list) else readings
        co2_raw = values.get('CO2')
        temp_raw = values.get('Temp')
        humidity_raw = values.get('Humidity')

        try:
            co2 = int(float(co2_raw)) if co2_raw else 0
        except (ValueError, TypeError):
            co2 = 0

        # Color code CO2
        if co2 <= 1000:
            color = self.GREEN
        elif co2 <= 1400:
            color = self.YELLOW
        else:
            color = self.RED

        # Build display
        main_group = self._displayio.Group()

        # CO2 - large, centered
        co2_label = self._label.Label(
            self._font,
            text=str(co2),
            color=color,
            x=60,
            y=30,
            scale=7
        )
        main_group.append(co2_label)

        # Temperature - bottom left, no decimals
        # Shift right when < 100 (2 digits) to avoid left-edge crowding
        try:
            temp_val = float(temp_raw) if temp_raw else 0
            temp_str = f"{temp_val:0.0f}"
        except (ValueError, TypeError):
            temp_val = 0
            temp_str = "--"
        x_off = 20 if temp_val < 100 else 0

        temp_label = self._label.Label(
            self._font,
            text=temp_str,
            color=self.WHITE,
            x=x_off,
            y=110,
            scale=6
        )
        main_group.append(temp_label)

        # Humidity - bottom right, no decimals
        try:
            hum_val = float(humidity_raw) if humidity_raw else 0
            hum_str = f"{hum_val:0.0f}%"
        except (ValueError, TypeError):
            hum_str = "--%"

        hum_label = self._label.Label(
            self._font,
            text=hum_str,
            color=0x0000FF,
            x=130,
            y=110,
            scale=6
        )
        main_group.append(hum_label)

        self.hw_display.root_group = main_group


class EPD29Display:
    """
    2.9" E-Paper display (UC8151D via FourWire SPI)
    Layout: CO2 (large) | ppm | Temp | Humidity | Time | Battery%

    Pin configuration comes from config.epd_pins dict.
    Uses custom PCF fonts from /fonts/ directory.
    """

    def __init__(self, config, debug=False):
        import displayio
        import board
        import busio
        import fourwire
        import adafruit_uc8151d
        from adafruit_display_text import label
        from adafruit_bitmap_font import bitmap_font

        self._displayio = displayio
        self._label = label
        self._config = config

        displayio.release_displays()

        spi = busio.SPI(board.SCK, board.MOSI)
        pins = config.epd_pins

        epd_cs = getattr(board, pins['cs'])
        epd_dc = getattr(board, pins['dc'])
        epd_reset = getattr(board, pins.get('reset', ''), None) if pins.get('reset') else None
        epd_busy = getattr(board, pins.get('busy', ''), None) if pins.get('busy') else None

        display_bus = fourwire.FourWire(
            spi, command=epd_dc, chip_select=epd_cs,
            reset=epd_reset, baudrate=1000000
        )

        self.hw_display = adafruit_uc8151d.UC8151D(
            display_bus,
            width=296, height=128,
            busy_pin=epd_busy,
            seconds_per_frame=3,
            rotation=270,
            black_bits_inverted=True,
            color_bits_inverted=True,
            refresh_time=5,
            highlight_color=0xFF0000,
        )

        # Load custom fonts
        self._NSM44 = bitmap_font.load_font("/fonts/NSM44.pcf")
        self._NSM32 = bitmap_font.load_font("/fonts/NSM32.pcf")
        self._NSM28 = bitmap_font.load_font("/fonts/NSM28.pcf")

        # Create persistent display group with labels
        self._group = displayio.Group()

        self.txtCO2 = label.Label(self._NSM44, text="----",
                                  anchored_position=(0, 0), anchor_point=(0, 0))
        self._group.append(self.txtCO2)

        self.txtPPM = label.Label(self._NSM32, text="ppm",
                                  anchored_position=(150, 10), anchor_point=(0, 0))
        self._group.append(self.txtPPM)

        self.txtUpd = label.Label(self._NSM28, text=" ",
                                  anchored_position=(self.hw_display.width - 7,
                                                     self.hw_display.height - 20),
                                  anchor_point=(0, 0))
        self._group.append(self.txtUpd)

        self.txtTemp = label.Label(self._NSM32, text="--F",
                                   anchored_position=(5, 55), anchor_point=(0, 0))
        self._group.append(self.txtTemp)

        self.txtRH = label.Label(self._NSM32, text="--%",
                                  anchored_position=(self.hw_display.width // 2, 55),
                                  anchor_point=(0, 0))
        self._group.append(self.txtRH)

        self.txtTime = label.Label(self._NSM28, text="--:--",
                                    anchored_position=(5, 100), anchor_point=(0, 0))
        self._group.append(self.txtTime)

        self.txtBat = label.Label(self._NSM28, text="B: --%",
                                   anchored_position=(self.hw_display.width // 2, 100),
                                   anchor_point=(0, 0))
        self._group.append(self.txtBat)

        self.hw_display.root_group = self._group

        if debug:
            print(f"EPD 2.9\" Display initialized: {self.hw_display.width}x{self.hw_display.height}")

    def update(self, readings=None):
        """Update EPD labels and refresh display"""
        if readings is None:
            return

        values = dict(readings) if isinstance(readings, list) else readings

        # Update labels
        co2_raw = values.get('CO2')
        try:
            co2_val = int(float(co2_raw)) if co2_raw else 0
            self.txtCO2.text = f"{co2_val:0.0f}"
        except (ValueError, TypeError):
            self.txtCO2.text = "---"

        temp_raw = values.get('Temp')
        try:
            temp_val = float(temp_raw) if temp_raw else 0
            self.txtTemp.text = f"{temp_val:0.1f}F"
        except (ValueError, TypeError):
            self.txtTemp.text = "--F"

        rh_raw = values.get('Humidity')
        try:
            rh_val = float(rh_raw) if rh_raw else 0
            self.txtRH.text = f"{rh_val:0.0f}% rH"
        except (ValueError, TypeError):
            self.txtRH.text = "--% rH"

        bat_raw = values.get('BatteryPct')
        try:
            bat_val = float(bat_raw) if bat_raw else 0
            self.txtBat.text = f"B: {bat_val:0.0f}%"
        except (ValueError, TypeError):
            self.txtBat.text = "B: --%"

        # Time from RTC
        try:
            import rtc
            ct = rtc.RTC().datetime
            self.txtTime.text = f"{ct.tm_hour}:{ct.tm_min:02d}"
        except:
            pass

        # Mark update indicator
        self.txtUpd.text = "."

        # Refresh display
        try:
            self.hw_display.auto_refresh = False
            self.hw_display.refresh()
            self._wait_for_refresh()
        except RuntimeError as e:
            print(f"EPD refresh error: {e}")

    def _wait_for_refresh(self, min_wait=3):
        """Wait for EPD refresh to complete"""
        import time
        # Wait for busy pin if available
        try:
            while self.hw_display.busy:
                time.sleep(0.1)
        except:
            pass
        # Time-based wait
        extra_wait = 1
        wait_time = max(self.hw_display.time_to_refresh + extra_wait, min_wait)
        time.sleep(wait_time)


class EPDThinkInk29:
    """
    2.9" E-Paper display TriColor (SSD1680 via FourWire SPI)
    Layout: CO2 (large) | ppm | Temp | Humidity | Time | Battery%

    Pin configuration comes from config.epd_pins dict.
    Uses custom PCF fonts from /fonts/ directory.
    """

    def __init__(self, config, debug=False):
        import displayio
        import board
        import busio
        import fourwire
        #import adafruit_EPD
        import adafruit_ssd1680
        from adafruit_display_text import label
        from adafruit_bitmap_font import bitmap_font

        self._displayio = displayio
        self._label = label
        self._config = config

        displayio.release_displays()

        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        pins = config.epd_pins

        epd_cs = getattr(board, pins['cs'])
        epd_dc = getattr(board, pins['dc'])
        epd_reset = getattr(board, pins.get('reset', ''), None) if pins.get('reset') else None
        epd_busy = getattr(board, pins.get('busy', ''), None) if pins.get('busy') else None

        display_bus = fourwire.FourWire(
            spi, command=epd_dc, chip_select=epd_cs,
            reset=epd_reset, baudrate=1000000
        )

        self.hw_display = adafruit_ssd1680.SSD1680(
            display_bus, width=296, height=128, busy_pin=epd_busy, rotation=270,
            seconds_per_frame=3,
            black_bits_inverted=True,
            color_bits_inverted=True,
            refresh_time=5,
            highlight_color=0xFF0000,
            colstart=0
        )

        """ # Keep until we get ssd1680 working
        self.hw_display = adafruit_uc8151d.UC8151D(
            display_bus,
            width=296, height=128,
            busy_pin=epd_busy,
            seconds_per_frame=3,
            rotation=270,
            black_bits_inverted=True,
            color_bits_inverted=True,
            refresh_time=5,
            highlight_color=0xFF0000,
        )
        """

        # Load custom fonts
        self._NSM44 = bitmap_font.load_font("/fonts/NSM44.pcf")
        self._NSM32 = bitmap_font.load_font("/fonts/NSM32.pcf")
        self._NSM28 = bitmap_font.load_font("/fonts/NSM28.pcf")

        # Create persistent display group with labels
        self._group = displayio.Group()

        self.txtCO2 = label.Label(self._NSM44, text="----",
                                  anchored_position=(0, 0), anchor_point=(0, 0))
        self._group.append(self.txtCO2)

        self.txtPPM = label.Label(self._NSM32, text="ppm",
                                  anchored_position=(150, 10), anchor_point=(0, 0))
        self._group.append(self.txtPPM)

        self.txtUpd = label.Label(self._NSM28, text=" ",
                                  anchored_position=(self.hw_display.width - 7,
                                                     self.hw_display.height - 20),
                                  anchor_point=(0, 0))
        self._group.append(self.txtUpd)

        self.txtTemp = label.Label(self._NSM32, text="--F",
                                   anchored_position=(5, 55), anchor_point=(0, 0))
        self._group.append(self.txtTemp)

        self.txtRH = label.Label(self._NSM32, text="--%",
                                  anchored_position=(self.hw_display.width // 2, 55),
                                  anchor_point=(0, 0))
        self._group.append(self.txtRH)

        self.txtTime = label.Label(self._NSM28, text="--:--",
                                    anchored_position=(5, 100), anchor_point=(0, 0))
        self._group.append(self.txtTime)

        self.txtBat = label.Label(self._NSM28, text="B: --%",
                                   anchored_position=(self.hw_display.width // 2, 100),
                                   anchor_point=(0, 0))
        self._group.append(self.txtBat)

        self.hw_display.root_group = self._group

        if debug:
            print(f"EPD 2.9\" Display initialized: {self.hw_display.width}x{self.hw_display.height}")

    def update(self, readings=None):
        """Update EPD labels and refresh display"""
        if readings is None:
            return

        values = dict(readings) if isinstance(readings, list) else readings

        # Update labels
        co2_raw = values.get('CO2')
        try:
            co2_val = int(float(co2_raw)) if co2_raw else 0
            self.txtCO2.text = f"{co2_val:0.0f}"
        except (ValueError, TypeError):
            self.txtCO2.text = "---"

        temp_raw = values.get('Temp')
        try:
            temp_val = float(temp_raw) if temp_raw else 0
            self.txtTemp.text = f"{temp_val:0.1f}F"
        except (ValueError, TypeError):
            self.txtTemp.text = "--F"

        rh_raw = values.get('Humidity')
        try:
            rh_val = float(rh_raw) if rh_raw else 0
            self.txtRH.text = f"{rh_val:0.0f}% rH"
        except (ValueError, TypeError):
            self.txtRH.text = "--% rH"

        bat_raw = values.get('BatteryPct')
        try:
            bat_val = float(bat_raw) if bat_raw else 0
            self.txtBat.text = f"B: {bat_val:0.0f}%"
        except (ValueError, TypeError):
            self.txtBat.text = "B: --%"

        # Time from RTC
        try:
            import rtc
            ct = rtc.RTC().datetime
            self.txtTime.text = f"{ct.tm_hour}:{ct.tm_min:02d}"
        except:
            pass

        # Mark update indicator
        self.txtUpd.text = "."

        # Refresh display
        try:
            self.hw_display.auto_refresh = False
            self.hw_display.refresh()
            self._wait_for_refresh()
        except RuntimeError as e:
            print(f"EPD refresh error: {e}")

    def _wait_for_refresh(self, min_wait=3):
        """Wait for EPD refresh to complete"""
        import time
        # Wait for busy pin if available
        try:
            while self.hw_display.busy:
                time.sleep(0.1)
        except:
            pass
        # Time-based wait
        extra_wait = 1
        wait_time = max(self.hw_display.time_to_refresh + extra_wait, min_wait)
        time.sleep(wait_time)



class EPD213Display:
    """
    2.13" E-Paper display (SSD1680 via FourWire SPI)
    Same layout as 2.9" but adjusted for 250x122 dimensions.
    """

    def __init__(self, config, debug=False):
        import displayio
        import board
        import fourwire
        import adafruit_ssd1680
        from adafruit_display_text import label
        from adafruit_bitmap_font import bitmap_font

        self._displayio = displayio
        self._label = label
        self._config = config

        displayio.release_displays()

        spi = board.SPI()
        pins = config.epd_pins

        epd_cs = getattr(board, pins['cs'])
        epd_dc = getattr(board, pins['dc'])
        epd_reset = getattr(board, pins.get('reset', ''), None) if pins.get('reset') else None
        epd_busy = getattr(board, pins.get('busy', ''), None) if pins.get('busy') else None

        display_bus = fourwire.FourWire(
            spi, command=epd_dc, chip_select=epd_cs,
            reset=epd_reset, baudrate=1000000
        )
        import time
        time.sleep(2)

        self.hw_display = adafruit_ssd1680.SSD1680(
            display_bus,
            width=250, height=122,
            busy_pin=epd_busy,
            seconds_per_frame=3,
            black_bits_inverted=True,
            color_bits_inverted=True,
            rotation=270,
        )

        # Load custom fonts
        self._NSM44 = bitmap_font.load_font("/fonts/NSM44.pcf")
        self._NSM32 = bitmap_font.load_font("/fonts/NSM32.pcf")
        self._NSM28 = bitmap_font.load_font("/fonts/NSM28.pcf")

        # Create persistent display group
        self._group = displayio.Group()

        self.txtCO2 = label.Label(self._NSM44, text="----",
                                  anchored_position=(0, 0), anchor_point=(0, 0))
        self._group.append(self.txtCO2)

        self.txtPPM = label.Label(self._NSM32, text="ppm",
                                  anchored_position=(150, 10), anchor_point=(0, 0))
        self._group.append(self.txtPPM)

        self.txtTemp = label.Label(self._NSM32, text="--F",
                                   anchored_position=(0, 50), anchor_point=(0, 0))
        self._group.append(self.txtTemp)

        self.txtRH = label.Label(self._NSM32, text="--%",
                                  anchored_position=(self.hw_display.width // 2, 50),
                                  anchor_point=(0, 0))
        self._group.append(self.txtRH)

        self.txtTime = label.Label(self._NSM28, text="--:--",
                                    anchored_position=(0, 90), anchor_point=(0, 0))
        self._group.append(self.txtTime)

        self.txtBat = label.Label(self._NSM28, text="--%",
                                   anchored_position=(self.hw_display.width // 2, 90),
                                   anchor_point=(0, 0))
        self._group.append(self.txtBat)

        self.hw_display.root_group = self._group

        if debug:
            print(f"EPD 2.13\" Display initialized: {self.hw_display.width}x{self.hw_display.height}")

    def update(self, readings=None):
        """Update EPD labels and refresh display"""
        if readings is None:
            return

        values = dict(readings) if isinstance(readings, list) else readings

        co2_raw = values.get('CO2')
        try:
            co2_val = int(float(co2_raw)) if co2_raw else 0
            self.txtCO2.text = f"{co2_val:0.0f}"
        except (ValueError, TypeError):
            self.txtCO2.text = "---"

        temp_raw = values.get('Temp')
        try:
            temp_val = float(temp_raw) if temp_raw else 0
            self.txtTemp.text = f"{temp_val:0.1f}F"
        except (ValueError, TypeError):
            self.txtTemp.text = "--F"

        rh_raw = values.get('Humidity')
        try:
            rh_val = float(rh_raw) if rh_raw else 0
            #self.txtRH.text = f"{rh_val:0.0f}% rH"
            self.txtRH.text = f"{rh_val:0.0f}%"
        except (ValueError, TypeError):
            #self.txtRH.text = "--% rH"
            self.txtRH.text = "--%"

        bat_raw = values.get('BatteryPct')
        try:
            bat_val = float(bat_raw) if bat_raw else 0
            self.txtBat.text = f"{bat_val:0.0f}%"
        except (ValueError, TypeError):
            self.txtBat.text = "--%"

        try:
            import rtc
            ct = rtc.RTC().datetime
            self.txtTime.text = f"{ct.tm_hour}:{ct.tm_min:02d}"
        except:
            pass

        try:
            self.hw_display.auto_refresh = False
            self.hw_display.refresh()
            self._wait_for_refresh()
        except RuntimeError as e:
            print(f"EPD refresh error: {e}")

    def _wait_for_refresh(self, min_wait=3):
        """Wait for EPD refresh to complete"""
        import time
        try:
            while self.hw_display.busy:
                time.sleep(0.1)
        except:
            pass
        extra_wait = 1
        wait_time = max(self.hw_display.time_to_refresh + extra_wait, min_wait)
        time.sleep(wait_time)


class OLEDDisplay:
    """
    128x32 I2C Monochrome OLED (SSD1306)
    Minimalist layout: CO2/Temp/Humidity on two lines, no labels or units.

    Line 1: CO2 value (larger if possible)
    Line 2: Temp  Humidity

    Adafruit product: http://adafru.it/4440
    """

    def __init__(self, config, debug=False, i2c=None):
        import board
        import displayio
        import adafruit_displayio_ssd1306
        from adafruit_display_text import label
        import terminalio

        self._label = label
        self._font = terminalio.FONT
        self._config = config

        displayio.release_displays()

        if i2c is None:
            try:
                i2c = board.STEMMA_I2C()
            except:
                i2c = board.I2C()

        # I2CDisplay moved from displayio to i2cdisplaybus in CircuitPython 9.x
        try:
            from i2cdisplaybus import I2CDisplayBus
            display_bus = I2CDisplayBus(i2c, device_address=0x3C)
        except ImportError:
            from displayio import I2CDisplay
            display_bus = I2CDisplay(i2c, device_address=0x3C)

        self.hw_display = adafruit_displayio_ssd1306.SSD1306(
            display_bus, width=128, height=32
        )

        self._group = displayio.Group()

        # Line 1: CO2 (scale 2 = 12x16 effective)
        self.txtCO2 = label.Label(
            self._font, text="----",
            x=0, y=8, scale=2
        )
        self._group.append(self.txtCO2)

        # Line 2: Temp and Humidity side by side (scale 1 = 6x8)
        self.txtTemp = label.Label(
            self._font, text="--",
            x=0, y=28, scale=1
        )
        self._group.append(self.txtTemp)

        self.txtRH = label.Label(
            self._font, text="--",
            x=64, y=28, scale=1
        )
        self._group.append(self.txtRH)

        self.hw_display.root_group = self._group

        if debug:
            print("OLED 128x32 Display initialized")

    def update(self, readings=None):
        """Update OLED with CO2/Temp/Humidity - no labels, no units"""
        if readings is None:
            return

        values = dict(readings) if isinstance(readings, list) else readings

        co2_raw = values.get('CO2') or values.get('MBSenCO2')
        try:
            self.txtCO2.text = str(int(float(co2_raw))) if co2_raw else "----"
        except (ValueError, TypeError):
            self.txtCO2.text = "----"

        # Find first key containing 'Temp' (case-sensitive) for temperature
        temp_raw = None
        for key, val in values.items():
            if 'Temp' in key and val is not None:
                temp_raw = val
                break
        try:
            temp_val = float(temp_raw) if temp_raw else 0
            self.txtTemp.text = f"{temp_val:.0f}"
        except (ValueError, TypeError):
            self.txtTemp.text = "--"

        # Find first key containing 'Humidity' or 'humid' for humidity
        rh_raw = None
        for key, val in values.items():
            if 'umid' in key and val is not None:
                rh_raw = val
                break
        try:
            rh_val = float(rh_raw) if rh_raw else 0
            self.txtRH.text = f"{rh_val:.0f}"
        except (ValueError, TypeError):
            self.txtRH.text = "--"

        try:
            self.hw_display.refresh()
        except:
            pass


# ── Display factory ──────────────────────────────────────────────────────────

_DISPLAY_REGISTRY = {
    "TFTDisplay":    TFTDisplay,
    "EPD29Display":  EPD29Display,
    "EPDThinkInk29": EPDThinkInk29,
    "EPD213Display": EPD213Display,
    "OLEDDisplay":   OLEDDisplay,
    None:            NullDisplay,
}


def create_display(config, debug=False):
    """
    Factory function: create the appropriate display driver for a config.

    Args:
        config: Config instance with display_class attribute
        debug: verbose output

    Returns:
        Display driver instance
    """
    display_name = config.display_class
    display_cls = _DISPLAY_REGISTRY.get(display_name)

    if display_cls is None:
        print(f"Unknown display class '{display_name}', using NullDisplay")
        display_cls = NullDisplay

    if debug and display_name:
        print(f"Initializing display: {display_name}")

    try:
        return display_cls(config, debug=debug)
    except Exception as e:
        print(f"Display init failed ({display_name}): {e}")
        return NullDisplay(config, debug=debug)
