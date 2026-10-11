CIRCUITPY_CREATOR_ID =  0x0000239A
CIRCUITPY_CREATION_ID = 0x00C60001

IDF_TARGET = esp32c6

CIRCUITPY_ESP_FLASH_MODE = qio
CIRCUITPY_ESP_FLASH_FREQ = 80m
CIRCUITPY_ESP_FLASH_SIZE = 4MB

# Board was originally defined with a 2MB firmware, almost 2MB user filesystem. Leave it that way.
CIRCUITPY_4MB_FLASH_LARGE_USER_FS_LAYOUT = 1

FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_MiniMQTT
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_AdafruitIO
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_ConnectionManager
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_Requests
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_NTP
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_HDC302x
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_Ticks
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_PixelBuf
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_NeoPixel
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_SSD1306
FROZEN_MPY_DIRS += $(TOP)/frozen/Adafruit_CircuitPython_framebuf
FROZEN_MPY_DIRS += $(TOP)/frozen/myprivate_lib
