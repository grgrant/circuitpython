CIRCUITPY_CREATOR_ID = 0x000C2886
CIRCUITPY_CREATION_ID = 0x00C30001

IDF_TARGET = esp32c3

CIRCUITPY_ESP_FLASH_MODE = qio
CIRCUITPY_ESP_FLASH_FREQ = 80m
CIRCUITPY_ESP_FLASH_SIZE = 4MB

CIRCUITPY_ESP_USB_SERIAL_JTAG = 1

#CIRCUITPY_FULL_BUILD = 0

# Turn off displayio to make room for frozen libs.
#CIRCUITPY_DISPLAYIO = 0
#CIRCUITPY_USB_MIDI = 0
#CIRCUITPY_PULSEIO = 0

FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_MiniMQTT
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_AdafruitIO
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_ConnectionManager
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_Requests
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_NTP
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_HDC302x
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_Ticks
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_SSD1306
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_framebuf
FROZEN_MPY_DIRS += $(TOP)/frozen/myprivate_lib
