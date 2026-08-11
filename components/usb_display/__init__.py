"""Make the ESP32-P4 a second monitor for a PC, over USB.

USB has no standard display class, so this speaks Espressif's udisp protocol
over a vendor interface: a PC application captures a screen region, encodes it
as JPEG and pushes it; the P4 decodes with its hardware JPEG decoder and draws
to an ESPHome display.

The PC half is required and is not part of this component. It is the
windows_driver directory of Espressif's usb_extend_screen example, which is
Windows-only.

This puts the board's single USB OTG controller in device mode, so it cannot be
a USB host at the same time -- nor a UVC webcam, which needs the same
controller.
"""

import os

import esphome.codegen as cg
from esphome.components import display, esp32
import esphome.config_validation as cv
from esphome.const import CONF_HEIGHT, CONF_ID, CONF_WIDTH

CODEOWNERS = ["@youkorr"]
DEPENDENCIES = ["display"]

usb_display_ns = cg.esphome_ns.namespace("usb_display")
USBDisplay = usb_display_ns.class_("USBDisplay", cg.Component)

CONF_DISPLAY_ID = "display_id"
CONF_FRAME_BUFFERS = "frame_buffers"
CONF_MAX_FRAME_BYTES = "max_frame_bytes"
CONF_MANUFACTURER = "manufacturer"
CONF_PRODUCT = "product"
CONF_VENDOR_ID = "vendor_id"
CONF_PRODUCT_ID = "product_id"
CONF_SERIAL = "serial"
CONF_USB_SPEED = "usb_speed"

_USB_SPEEDS = {"high": True, "full": False}

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(USBDisplay),
            cv.Required(CONF_DISPLAY_ID): cv.use_id(display.Display),
            # Must match what the PC application is told to send: the header of
            # every frame carries the size, and a frame whose size does not
            # match is dropped rather than drawn at the wrong shape.
            cv.Required(CONF_WIDTH): cv.int_range(min=16, max=4096),
            cv.Required(CONF_HEIGHT): cv.int_range(min=16, max=4096),
            # Frames in flight between USB and the decoder. Espressif's example
            # uses six; four is enough to keep the decoder fed and costs less
            # PSRAM.
            cv.Optional(CONF_FRAME_BUFFERS, default=4): cv.int_range(min=2, max=8),
            # Upper bound on one compressed frame. A 1024x600 JPEG of a desktop
            # is tens of kilobytes; 128 KB leaves room for a busy screen.
            cv.Optional(CONF_MAX_FRAME_BYTES, default=131072): cv.int_range(
                min=16384, max=1048576
            ),
            cv.Optional(CONF_USB_SPEED, default="high"): cv.enum(
                _USB_SPEEDS, lower=True
            ),
            cv.Optional(CONF_MANUFACTURER, default="ESPHome"): cv.string_strict,
            # "udisp" is what Espressif's Windows driver looks for.
            cv.Optional(CONF_PRODUCT, default="udisp"): cv.string_strict,
            cv.Optional(CONF_VENDOR_ID, default=0x303A): cv.hex_int_range(
                min=0, max=0xFFFF
            ),
            cv.Optional(CONF_PRODUCT_ID, default=0x4001): cv.hex_int_range(
                min=0, max=0xFFFF
            ),
            cv.Optional(CONF_SERIAL, default="0001"): cv.string_strict,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    # The hardware JPEG decoder and the High-Speed USB PHY are both ESP32-P4.
    esp32.only_on_variant(supported=[esp32.VARIANT_ESP32P4]),
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    disp = await cg.get_variable(config[CONF_DISPLAY_ID])
    cg.add(var.set_display(disp))
    cg.add(var.set_resolution(config[CONF_WIDTH], config[CONF_HEIGHT]))
    cg.add(var.set_frame_buffers(config[CONF_FRAME_BUFFERS]))
    cg.add(var.set_max_frame_bytes(config[CONF_MAX_FRAME_BYTES]))

    # The descriptors have to be compiled into TinyUSB itself, which only a
    # real IDF component can do (see that component's CMakeLists).
    esp32.add_idf_component(
        name="usb_display_tusb",
        path=os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "usb_display_tusb"
        ),
    )
    esp32.add_idf_component(name="espressif/tinyusb", ref="*")

    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_VID", config[CONF_VENDOR_ID])
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_PID", config[CONF_PRODUCT_ID])
    esp32.add_idf_sdkconfig_option(
        "CONFIG_USB_DISPLAY_MANUFACTURER", config[CONF_MANUFACTURER]
    )
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_PRODUCT", config[CONF_PRODUCT])
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_SERIAL", config[CONF_SERIAL])
    esp32.add_idf_sdkconfig_option(
        "CONFIG_USB_DISPLAY_HIGH_SPEED", _USB_SPEEDS[config[CONF_USB_SPEED]]
    )
