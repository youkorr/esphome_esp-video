"""Make the ESP32-P4 a second monitor for a PC, over USB.

USB has no standard display class, so this speaks Espressif's udisp protocol
over a vendor interface: a PC application captures a screen region, encodes it
as JPEG and pushes it; the P4 decodes with its hardware JPEG decoder and draws
to an ESPHome display.

The PC half is required, because USB has no display class: no operating system
knows how to put its screen on a USB data connection by itself, which is why
every USB monitor ships software for it. udisp_send.py, next to this file, is
that half -- it captures the screen, encodes it and pushes it, and runs on
Linux, macOS and Windows. Espressif's own answer is the windows_driver
directory of their usb_extend_screen example, which is Windows-only and needs
a signed driver; the board does not care which of the two is talking to it.

The component logs the exact command line for the sender at startup, built
from the configuration here, so the two cannot disagree about the geometry.

This puts the board's single USB OTG controller in device mode, so it cannot be
a USB host at the same time -- nor a UVC webcam, which needs the same
controller.
"""

import os

import esphome.codegen as cg
from esphome.components import display, esp32
import esphome.config_validation as cv
from esphome.const import (
    CONF_HEIGHT,
    CONF_ID,
    CONF_RAW_DATA_ID,
    CONF_ROTATION,
    CONF_WIDTH,
)
from esphome.core import HexInt

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
CONF_SENDER_DRIVE = "sender_drive"

# The PC half, carried by the board itself. Alongside this file so there is one
# place to look for everything this display needs.
SENDER_SCRIPT = os.path.join(os.path.dirname(__file__), "udisp_send.py")

_USB_SPEEDS = {"high": True, "full": False}

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(USBDisplay),
            cv.GenerateID(CONF_RAW_DATA_ID): cv.declare_id(cg.uint8),
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
            # For a panel that is not mounted the way the host sends its frames.
            # The P4's pixel-processing accelerator does this, so it is free;
            # note that a quarter turn swaps the axes, so a 1024x600 stream on a
            # panel turned 90 degrees needs a 600x1024 panel to land on.
            cv.Optional(CONF_ROTATION, default=0): cv.one_of(0, 90, 180, 270, int=True),
            cv.Optional(CONF_USB_SPEED, default="high"): cv.enum(
                _USB_SPEEDS, lower=True
            ),
            # Mass storage is a class every operating system already has a
            # driver for, so the board can hand over the sender with nothing to
            # install and nothing to download. Turn it off to go back to a
            # single-interface device.
            cv.Optional(CONF_SENDER_DRIVE, default=True): cv.boolean,
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
    cg.add(var.set_rotation(config[CONF_ROTATION]))

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
    esp32.add_idf_sdkconfig_option(
        "CONFIG_USB_DISPLAY_SENDER_DRIVE", config[CONF_SENDER_DRIVE]
    )

    if config[CONF_SENDER_DRIVE]:
        with open(SENDER_SCRIPT, "rb") as handle:
            script = handle.read()
        # Line endings the way the drive's other file has them: this is opened
        # on the machine that mounted the drive, and Notepad is still the thing
        # that opens a .py there.
        script = script.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        arr = cg.progmem_array(
            config[CONF_RAW_DATA_ID], [HexInt(byte) for byte in script]
        )
        cg.add(var.set_sender_script(arr, len(script)))
