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


# Both do their work and return: set_awake() flips a flag and marks a message
# to send, all before play() ends. Nothing is deferred to a callback or a
# timer, which is what synchronous means here.
@automation.register_action(
    "usb_display.sleep", SleepAction, _AWAKE_ACTION_SCHEMA, synchronous=True
)
@automation.register_action(
    "usb_display.wake", WakeAction, _AWAKE_ACTION_SCHEMA, synchronous=True
)
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
CONF_RENDER_WIDTH = "render_width"
CONF_RENDER_HEIGHT = "render_height"
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
            # The size the host draws on, when that is to be smaller than the
            # panel. What it buys is at the other end: the machine rendering
            # the page pays for every pixel four times -- painting, encoding,
            # comparing with the last one, encoding again -- and this board's
            # pixel-processing accelerator scales the result up for nothing,
            # being a DMA engine that is otherwise idle. 640x1024 for an
            # 800x1280 panel is 64% of the pixels, so about a third off every
            # stage, for a picture that is softer but not by much.
            cv.Optional(CONF_RENDER_WIDTH): cv.int_range(min=16, max=4096),
            cv.Optional(CONF_RENDER_HEIGHT): cv.int_range(min=16, max=4096),
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
    _validate_render_size,
)


# The tile the sender compares and cuts on. Every rectangle it produces has
# its origin and its size on this grid, so these are the coordinates that have
# to land on whole panel pixels once scaled.
_SENDER_TILE = 64


def _validate_render_size(config):
    """Refuse a render size that would not land on whole panel pixels.

    A rectangle arrives in the host's coordinates and is multiplied by
    panel / render to find where it goes. If that division is not exact the
    rectangles no longer meet: measured on the arithmetic, 533x853 into
    800x1280 leaves 2079 panel pixels that no rectangle ever covers, which is
    a scatter of stale pixels that only the thirty-second redraw clears.

    Two ways to be safe. An exact integer ratio works whatever the tile is,
    because every coordinate scales exactly. Otherwise the render size has to
    sit on the tile grid and divide a tile's worth of panel, which is what
    makes every multiple of the tile exact.
    """
    width, height = config[CONF_WIDTH], config[CONF_HEIGHT]
    render_w = config.get(CONF_RENDER_WIDTH)
    render_h = config.get(CONF_RENDER_HEIGHT)
    if render_w is None and render_h is None:
        return config
    if render_w is None or render_h is None:
        raise cv.Invalid(
            f"{CONF_RENDER_WIDTH} and {CONF_RENDER_HEIGHT} go together: give "
            f"both or neither"
        )
    if render_w > width or render_h > height:
        raise cv.Invalid(
            f"the render size {render_w}x{render_h} is larger than the panel's "
            f"{width}x{height}. The accelerator here scales up, not down"
        )
    if width * render_h != height * render_w:
        raise cv.Invalid(
            f"{render_w}x{render_h} is not the same shape as the panel's "
            f"{width}x{height}, so the picture would be stretched. Keep the "
            f"two ratios equal"
        )
    if config[CONF_ROTATION] != 0:
        raise cv.Invalid(
            f"rendering smaller is not supported together with rotation "
            f"({config[CONF_ROTATION]} degrees). The accelerator can do both "
            f"in one pass, but that combination has never been run on a board "
            f"and is refused rather than guessed at"
        )

    def lands_whole(render, panel):
        if panel % render == 0:
            return True
        return render % _SENDER_TILE == 0 and (_SENDER_TILE * panel) % render == 0

    if not lands_whole(render_w, width) or not lands_whole(render_h, height):
        suggestions = [
            f"{width // n}x{height // n}"
            for n in (2, 4)
            if width % n == 0 and height % n == 0
        ]
        tiled = [
            f"{w}x{h}"
            for w in range(_SENDER_TILE, width + 1, _SENDER_TILE)
            for h in [w * height // width]
            if width * h == height * w
            and (_SENDER_TILE * width) % w == 0
            and (_SENDER_TILE * height) % h == 0
            and h % _SENDER_TILE == 0
        ]
        raise cv.Invalid(
            f"{render_w}x{render_h} does not divide {width}x{height} into whole "
            f"pixels, so the rectangles would not meet and parts of the panel "
            f"would never be redrawn. Try one of: "
            f"{', '.join(dict.fromkeys(suggestions + tiled)) or 'none available'}"
        )
    return config


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    disp = await cg.get_variable(config[CONF_DISPLAY_ID])
    cg.add(var.set_display(disp))
    cg.add(var.set_resolution(config[CONF_WIDTH], config[CONF_HEIGHT]))
    if (render_w := config.get(CONF_RENDER_WIDTH)) is not None:
        cg.add(var.set_render_resolution(render_w, config[CONF_RENDER_HEIGHT]))
    cg.add(var.set_frame_buffers(config[CONF_FRAME_BUFFERS]))
    cg.add(var.set_max_frame_bytes(config[CONF_MAX_FRAME_BYTES]))
    cg.add(var.set_rotation(config[CONF_ROTATION]))
    cg.add(var.set_max_fps(config[CONF_MAX_FPS]))
    if (port := config.get(CONF_PORT)) is not None:
        cg.add(var.set_port(port))
        # Receiving at video rates is bounded by the TCP receive window, and
        # nothing else about the link. A window is how much a sender may have
        # in flight before it must stop and wait for an acknowledgement, so
        # the ceiling is the window divided by the round trip -- and the
        # default is four segments, about 5.7 kB, which over Wi-Fi is a
        # fraction of what the radio can carry.
        #
        # Sending does not suffer the same way, which is why a camera serving
        # JPEG out of this board reaches rates this could not take in. Set
        # only when frames actually arrive over the network; these are
        # device-wide and not worth spending on a board that is USB-only.
        # 64800 is 45 segments of the default 1440-byte MSS, and the largest
        # multiple of it that still fits the 16-bit window field a header
        # carries without window scaling. What it buys is a ceiling: a window
        # is how much may be in flight before the sender must stop and wait, so
        # the most that can arrive is the window divided by the round trip.
        # 28800 gave 23 Mbit/s at a 10 ms round trip and 11.5 at 20, and a busy
        # five-second window on a panel has been measured at 6.2 -- close
        # enough on a loaded radio to be worth the room. This doubles it.
        esp32.add_idf_sdkconfig_option("CONFIG_LWIP_TCP_WND_DEFAULT", 64800)
        # Deliberately not raised with it. This one is the send side, and this
        # board sends touches: a few bytes at a time. It is left where it is
        # rather than lowered because these options are device-wide, and a
        # camera serving JPEG out of the same board is the one thing here that
        # does need a send buffer.
        esp32.add_idf_sdkconfig_option("CONFIG_LWIP_TCP_SND_BUF_DEFAULT", 28800)
        # How many segments may queue for the socket before lwip drops them and
        # asks for them again, which is what a burst of a large frame is. This
        # is not free to choose: it has to hold a whole window's worth, and
        # Espressif's rule is TCP_WND / TCP_MSS + 2, so 64800 over 1440 plus
        # two is 47. Raising the window without raising this would have made
        # things worse rather than better -- a window the stack invites the
        # sender to fill and then drops the tail of.
        esp32.add_idf_sdkconfig_option("CONFIG_LWIP_TCP_RECVMBOX_SIZE", 64)
        # Without this, lwip does not implement SO_RCVBUF at all and the
        # setsockopt in network.cpp fails with ENOPROTOOPT -- silently until
        # that call learned to complain. It is a ceiling on what one socket
        # will hold, sitting above the window rather than replacing it.
        esp32.add_idf_sdkconfig_option("CONFIG_LWIP_SO_RCVBUF", True)

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
