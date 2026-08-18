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

ha_send.py, also next to this file, is a third: instead of mirroring a screen
it renders a Home Assistant dashboard in a browser with no window and sends
only the rectangles that changed, which is what a panel on a battery can
afford. Touches travel back to it over the same socket, so the panel drives the
page it is showing.

The component logs the exact command line for the sender at startup, built
from the configuration here, so the two cannot disagree about the geometry.

This puts the board's single USB OTG controller in device mode, so it cannot be
a USB host at the same time -- nor a UVC webcam, which needs the same
controller.
"""

import logging
import os

import esphome.automation as automation
import esphome.codegen as cg
from esphome.components import display, esp32, speaker, touchscreen
import esphome.config_validation as cv
from esphome.const import (
    CONF_HEIGHT,
    CONF_PORT,
    CONF_TRIGGER_ID,
    CONF_ID,
    CONF_RAW_DATA_ID,
    CONF_ROTATION,
    CONF_WIDTH,
)
from esphome.core import HexInt

CODEOWNERS = ["@youkorr"]
DEPENDENCIES = ["display"]
# The speaker path hands the host's stream over with an audio::AudioStreamInfo.
AUTO_LOAD = ["audio"]

usb_display_ns = cg.esphome_ns.namespace("usb_display")
USBDisplay = usb_display_ns.class_("USBDisplay", cg.Component)

# Turning the backlight off saves the most power on its own, but the sender
# goes on rendering, encoding and transmitting for a screen nobody can see.
# These say so, so it can stop -- and stop the traffic with it.
SleepAction = usb_display_ns.class_("SleepAction", automation.Action)
WakeAction = usb_display_ns.class_("WakeAction", automation.Action)

_AWAKE_ACTION_SCHEMA = automation.maybe_simple_id(
    {cv.Required(CONF_ID): cv.use_id(USBDisplay)}
)


@automation.register_action("usb_display.sleep", SleepAction, _AWAKE_ACTION_SCHEMA)
@automation.register_action("usb_display.wake", WakeAction, _AWAKE_ACTION_SCHEMA)
async def usb_display_awake_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


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
CONF_JPEG_QUALITY = "jpeg_quality"
CONF_MAX_FPS = "max_fps"
CONF_TOUCHSCREEN_ID = "touchscreen_id"
CONF_SPEAKER_ID = "speaker_id"
CONF_ON_AUDIO_START = "on_audio_start"
CONF_ON_AUDIO_STOP = "on_audio_stop"

_LOGGER = logging.getLogger(__name__)

# Espressif's signed Indirect Display Driver binds by product ID, and it tells
# the two shapes of their device apart that way: one identifier for a board that
# is only a display, another for their composite one with touch and audio. Ours
# grows a second interface when the sender drive is on, which matches neither.
_ESPRESSIF_DISPLAY_ONLY_PID = 0x2987
_ESPRESSIF_COMPOSITE_PID = 0x2986

# The PC half, carried by the board itself. Alongside this file so there is one
# place to look for everything this display needs.
SENDER_SCRIPT = os.path.join(os.path.dirname(__file__), "udisp_send.py")

_USB_SPEEDS = {"high": True, "full": False}


def _warn_about_espressif_driver(config):
    """Say something when the product ID asks for a driver this shape will not get.

    Nothing here is wrong enough to refuse -- the identifiers belong to somebody
    else and they are free to change them -- but a board that enumerates as the
    wrong shape fails by simply never being bound, which looks like the firmware
    being broken rather than the two disagreeing about what the device is.
    """
    pid = config[CONF_PRODUCT_ID]
    # Anything beyond the picture is a second function, and which identifier is
    # used decides whether the host ever sees it: Espressif's driver claims the
    # whole device under 0x2987, so Windows creates no child devices and the
    # other interfaces are invisible however correct they are. Their 0x2986
    # sets up the composite parent, and every function appears.
    extra_functions = [
        name
        for key, name in (
            (CONF_TOUCHSCREEN_ID, "touchscreen_id"),
            (CONF_SPEAKER_ID, "speaker_id"),
        )
        if key in config
    ]
    if pid == _ESPRESSIF_DISPLAY_ONLY_PID and extra_functions:
        _LOGGER.warning(
            "product_id 0x%04X is what Espressif's driver binds to for a board "
            "that is only a display: it claims the whole device, so the "
            "interface added by %s is never exposed to the host. Their composite "
            "identifier is 0x%04X.",
            pid,
            " and ".join(extra_functions),
            _ESPRESSIF_COMPOSITE_PID,
        )
    elif pid == _ESPRESSIF_DISPLAY_ONLY_PID and config[CONF_SENDER_DRIVE]:
        _LOGGER.warning(
            "product_id 0x%04X is what Espressif's display driver binds to for a "
            "board that is only a display, but sender_drive adds a second "
            "interface. Set sender_drive: false -- with that driver the sender is "
            "not needed anyway.",
            pid,
        )
    elif pid == _ESPRESSIF_COMPOSITE_PID and config[CONF_SENDER_DRIVE]:
        _LOGGER.warning(
            "product_id 0x%04X is what Espressif's driver binds to for their "
            "composite device, which has a display and a touch interface and no "
            "drive. Set sender_drive: false -- with that driver the sender is not "
            "needed anyway.",
            pid,
        )
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(USBDisplay),
            cv.GenerateID(CONF_RAW_DATA_ID): cv.declare_id(cg.uint8),
            cv.Required(CONF_DISPLAY_ID): cv.use_id(display.Display),
            # Optional, and the whole of what touch needs: ESPHome applies the
            # touchscreen's own transform: before a listener sees a point, so a
            # panel mounted upside down is corrected in one place for both this
            # and LVGL.
            #
            # Contacts go wherever there is somebody to send them: to the host
            # as an HID digitizer over the cable, and back up the socket to a
            # connected network sender. The second is what makes a page
            # rendered on another machine pressable here.
            cv.Optional(CONF_TOUCHSCREEN_ID): cv.use_id(touchscreen.Touchscreen),
            # Optional. Any ESPHome speaker, so this can be one input of a
            # mixer alongside a media player and a voice assistant rather than
            # fighting them for the same I2S bus.
            cv.Optional(CONF_SPEAKER_ID): cv.use_id(speaker.Speaker),
            # What to do when the host starts and stops sending sound. Every
            # board answers this differently -- switch an amplifier on, stand a
            # wake word down off a shared I2S bus -- so it is left to the
            # configuration rather than guessed at here.
            cv.Optional(CONF_ON_AUDIO_START): automation.validate_automation(
                {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(automation.Trigger.template())}
            ),
            cv.Optional(CONF_ON_AUDIO_STOP): automation.validate_automation(
                {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(automation.Trigger.template())}
            ),
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
            # Accept frames over the network as well as over USB. The USB
            # interfaces stay: sound has no equivalent here, so a board can be
            # plugged in for that and take its picture over Wi-Fi. Touches
            # travel back over this socket, so a board with no cable at all is
            # still an input device.
            cv.Optional(CONF_PORT): cv.port,
            # Advertised to the host in the vendor interface string, which is
            # how a driver on the other end learns what to send. Espressif's
            # scale, not the usual one to ninety-five.
            cv.Optional(CONF_JPEG_QUALITY, default=6): cv.int_range(min=1, max=10),
            cv.Optional(CONF_MAX_FPS, default=60): cv.int_range(min=1, max=60),
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
    _warn_about_espressif_driver,
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
    cg.add(var.set_max_fps(config[CONF_MAX_FPS]))
    if (port := config.get(CONF_PORT)) is not None:
        cg.add(var.set_port(port))

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
    # The HID report descriptor states the coordinate range, so it needs the
    # geometry at compile time as well.
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_WIDTH", config[CONF_WIDTH])
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_HEIGHT", config[CONF_HEIGHT])
    esp32.add_idf_sdkconfig_option(
        "CONFIG_USB_DISPLAY_TOUCH", CONF_TOUCHSCREEN_ID in config
    )
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_AUDIO", CONF_SPEAKER_ID in config)
    if speaker_id := config.get(CONF_SPEAKER_ID):
        esp32.add_idf_component(name="espressif/usb_device_uac", ref="~1.3.0")
        # Plugged into the TinyUSB device this component already brings up,
        # rather than bringing up one of its own.
        esp32.add_idf_sdkconfig_option("CONFIG_USB_DEVICE_UAC_AS_PART", True)
        esp32.add_idf_sdkconfig_option("CONFIG_UAC_SPEAKER_CHANNEL_NUM", 1)
        esp32.add_idf_sdkconfig_option("CONFIG_UAC_MIC_CHANNEL_NUM", 0)
        esp32.add_idf_sdkconfig_option("CONFIG_UAC_SAMPLE_RATE", 48000)
        spk = await cg.get_variable(speaker_id)
        cg.add(var.set_speaker(spk))

    for key, setter in (
        (CONF_ON_AUDIO_START, var.set_audio_start_trigger),
        (CONF_ON_AUDIO_STOP, var.set_audio_stop_trigger),
    ):
        for conf in config.get(key, []):
            trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
            cg.add(setter(trigger))
            await automation.build_automation(trigger, [], conf)

    if touchscreen_id := config.get(CONF_TOUCHSCREEN_ID):
        touch = await cg.get_variable(touchscreen_id)
        cg.add(var.set_touchscreen(touch))

    # The vendor interface string is not a label. Espressif's Windows display
    # driver reads it off the interface and parses the screen's geometry and
    # limits out of it, so a driver that finds anything else there has no idea
    # what it is talking to. The layout is theirs, byte for byte, the same way
    # the frame header is.
    esp32.add_idf_sdkconfig_option(
        "CONFIG_USB_DISPLAY_VENDOR_STRING",
        f"esp32p4udisp0_R{config[CONF_WIDTH]}x{config[CONF_HEIGHT]}"
        f"_Ejpg{config[CONF_JPEG_QUALITY]}"
        f"_Fps{config[CONF_MAX_FPS]}"
        f"_Bl{config[CONF_MAX_FRAME_BYTES]}",
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
