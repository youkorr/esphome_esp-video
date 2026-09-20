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

This puts the board's HIGH-SPEED USB OTG controller in device mode, so that one
cannot also be a host -- nor a UVC webcam, which needs the same controller.

It used to say "single" here, and on the ESP32-P4 that is wrong: the chip has
TWO OTG peripherals, one high-speed and one full-speed, and Espressif's own
datasheet says the pair is what allows several USB peripherals in host mode at
once. This component takes the high-speed one and does not touch the other, so
a host-mode use of the full-speed port -- a Bluetooth dongle, say -- is not
ruled out by anything here. Whether ESP-IDF drives that port as a host on this
chip is a separate question and is not answered by this file.
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

portall_ns = cg.esphome_ns.namespace("portall")
Portall = portall_ns.class_("Portall", cg.Component)

# Turning the backlight off saves the most power on its own, but the sender
# goes on rendering, encoding and transmitting for a screen nobody can see.
# These say so, so it can stop -- and stop the traffic with it.
SleepAction = portall_ns.class_("SleepAction", automation.Action)
WakeAction = portall_ns.class_("WakeAction", automation.Action)

_AWAKE_ACTION_SCHEMA = automation.maybe_simple_id(
    {cv.Required(CONF_ID): cv.use_id(Portall)}
)


# Both do their work and return: set_awake() flips a flag and marks a message
# to send, all before play() ends. Nothing is deferred to a callback or a
# timer, which is what synchronous means here.
@automation.register_action(
    "portall.sleep", SleepAction, _AWAKE_ACTION_SCHEMA, synchronous=True
)
@automation.register_action(
    "portall.wake", WakeAction, _AWAKE_ACTION_SCHEMA, synchronous=True
)
async def portall_awake_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


# portall.key and portall.home -- what a remote or a gamepad presses through.
#
# THE NAMES ARE RESOLVED HERE, so a YAML never carries a number and the board
# never carries a table of names. What crosses the wire is the HID usage, and
# the sender turns that into whatever its browser calls the key: one table, in
# Python, correctable without reflashing a panel.
#
# The list is deliberately short and navigational. A remote's media buttons are
# not here because a panel that plays to a Bluetooth speaker already has them
# through AVRCP -- `on_media_key` on portall_bt -- and two ways to send play
# and pause would be two things to keep in step.
KEYS = {
    # HID Keyboard/Keypad page, and these four are why this exists: YouTube's
    # television interface and any grid of tiles are driven by arrows.
    "up": (0x07, 0x52),
    "down": (0x07, 0x51),
    "left": (0x07, 0x50),
    "right": (0x07, 0x4F),
    "ok": (0x07, 0x28),  # Return
    "back": (0x07, 0x29),  # Escape, which is what a television interface takes
    "tab": (0x07, 0x2B),
    "space": (0x07, 0x2C),
    "page_up": (0x07, 0x4B),
    "page_down": (0x07, 0x4E),
}

CONF_KEY = "key"

KeyAction = portall_ns.class_("KeyAction", automation.Action)
HomeAction = portall_ns.class_("HomeAction", automation.Action)


@automation.register_action(
    "portall.key",
    KeyAction,
    cv.maybe_simple_value(
        {
            cv.GenerateID(): cv.use_id(Portall),
            cv.Required(CONF_KEY): cv.one_of(*KEYS, lower=True),
        },
        key=CONF_KEY,
    ),
    # send_key() writes into a queue and returns; the network task is what
    # touches the socket. Nothing is deferred to a callback or a timer.
    synchronous=True,
)
async def portall_key_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    page, usage = KEYS[config[CONF_KEY]]
    cg.add(var.set_usage(page, usage))
    return var


@automation.register_action(
    "portall.home",
    HomeAction,
    _AWAKE_ACTION_SCHEMA,
    # ask_home() sets a latch. Same shape as sleep and wake, which is why it
    # shares their schema.
    synchronous=True,
)
async def portall_home_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


# portall.set_volume, for a volume somebody wants to own themselves: a template
# number with restore_value and an initial_value, which is how ESPHome does a
# setting and is what the component's own number entity deliberately is not --
# that one follows the sound rather than deciding it.
CONF_VOLUME = "volume"

SetVolumeAction = portall_ns.class_("SetVolumeAction", automation.Action)


@automation.register_action(
    "portall.set_volume",
    SetVolumeAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(Portall),
            # A fraction, as every volume in ESPHome is. A slider that runs to
            # a hundred therefore wants: !lambda 'return x / 100.0;'
            cv.Required(CONF_VOLUME): cv.templatable(cv.percentage),
        }
    ),
    # set_audio_volume writes a float and calls the speaker; nothing is
    # deferred to a callback, a timer or the loop, so play() is finished when
    # it returns. esphome says so out loud when this is left off.
    synchronous=True,
)
async def portall_set_volume_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    cg.add(var.set_volume(await cg.templatable(config[CONF_VOLUME], args, float)))
    return var


CONF_DISPLAY_ID = "display_id"
CONF_FRAME_BUFFERS = "frame_buffers"
CONF_MAX_FRAME_BYTES = "max_frame_bytes"
CONF_MANUFACTURER = "manufacturer"
CONF_PRODUCT = "product"
CONF_VENDOR_ID = "vendor_id"
CONF_PRODUCT_ID = "product_id"
CONF_SERIAL = "serial"
CONF_USB = "usb"
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


# The tile the sender compares and cuts on. Every rectangle it produces has
# its origin and its size on this grid, so these are the coordinates that have
# to land on whole panel pixels once scaled.
CONF_PPA_BURST = "ppa_burst"

_SENDER_TILE = 64


def _request_fast_network(config):
    """Ask ESPHome for its high-performance networking, when frames arrive here.

    This is the one thing that decides how fast pictures can reach the board,
    and it had been set by hand and set too low. A TCP receive window is how
    much a sender may have in flight before it must stop and wait, so the most
    that can arrive is the window divided by the round trip -- nothing else
    about the link enters into it. This component used to set that window to
    64800 itself, chosen as the largest value the 16-bit window field of a TCP
    header can carry *without window scaling*, and it also lowered the send
    buffer and the receive mailbox to match.

    Every part of that was self-imposed. ESPHome turns window scaling on and
    uses 512000 with 512-deep mailboxes when PSRAM is guaranteed, which every
    board this runs on has -- so the hand-set values were not a floor being
    raised, they were a ceiling being lowered, by a factor of eight. It capped
    what could arrive at about 26 Mbit/s at a 20 ms round trip, which is what a
    panel was measured receiving, and the user's own VLC capture of the same
    board serving its camera at 25 932 kb/s is what said the radio was not the
    thing in the way.

    Requesting it is also more than lwip: the wifi component reads the same
    flag and raises its RX/TX buffers, turns on AMPDU aggregation and moves
    those buffers into PSRAM.

    Only asked for when `port:` is present. A USB-only board sends nothing over
    the network and should not pay the memory for it.
    """
    if CONF_PORT not in config:
        return config
    try:
        from esphome.components import network

        network.require_high_performance_networking()
    except (ImportError, AttributeError):
        # Older ESPHome has no such request. Nothing is set instead: the
        # values this used to set were the problem, not the fix.
        pass
    return config


def _validate_usb(config):
    """Refuse a board that has given up both ways of being fed.

    `usb: false` exists for one reason, and it is a fact about the silicon
    rather than a preference. The ESP32-P4 has two USB OTG peripherals and a
    board wires each of its sockets to one of them; on the M5Stack Tab5 the
    USB-A HOST socket is on the high-speed peripheral, which is exactly the one
    this component puts in DEVICE mode. So a panel cannot host anything on USB
    -- a Bluetooth dongle for Classic audio and HID, which is what this was
    written for -- while it is still pretending to be a screen on a cable.

    What it costs is everything on that cable: the vendor pipe the picture used
    to arrive on, the HID digitizer, the USB sound card and the drive carrying
    the sender. What it does not cost is the panel: `port:` is the other way in
    and it carries pictures, touches and sound already.

    Which is why the one refusal here is having neither. A board with no `port:`
    and no USB device has nothing that can ever send it a picture, and it would
    boot, allocate every buffer, and sit black for ever with a perfectly clean
    log. That is the silent no-op this project keeps paying for, so it is a
    validation failure instead.
    """
    if not config[CONF_USB] and CONF_PORT not in config:
        raise cv.Invalid(
            "usb: false releases the USB peripheral, so the picture has to "
            "arrive over the network -- but no port: is set, and nothing else "
            "can feed this panel. Add port: 5000, or leave usb: true.",
            path=[CONF_USB],
        )
    return config


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


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(Portall),
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
            # How much the accelerator moves per burst of external memory.
            # 64 leaves more bandwidth for the display controller's own fetch
            # of the framebuffer, which is what this competes with; 128 gets
            # the accelerator through its own work sooner. Measured in the
            # author's LVGL work on the same silicon, where a fill at 64 cost
            # a third of the refresh rate under load -- but that was a fill,
            # and this is a scale-rotate once per rectangle, so the answer
            # here is not the answer there. The board's stats line reports
            # the microseconds spent in the accelerator, which is how to
            # choose between them rather than argue about it.
            cv.Optional(CONF_PPA_BURST, default=64): cv.one_of(64, 128, int=True),
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
            # Whether this board is a USB device at all. See _validate_usb.
            cv.Optional(CONF_USB, default=True): cv.boolean,
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
    _validate_usb,
    _validate_render_size,
    _request_fast_network,
)


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
    cg.add(var.set_ppa_burst(config[CONF_PPA_BURST]))
    cg.add(var.set_max_fps(config[CONF_MAX_FPS]))
    if (port := config.get(CONF_PORT)) is not None:
        cg.add(var.set_port(port))
        # How fast pictures can arrive is decided by the TCP receive window,
        # and this component no longer sets it: _request_fast_network asks
        # ESPHome for its high-performance networking instead, which turns
        # window scaling on and uses 512000 where this used to write 64800.
        # The hand-set values were not a floor being raised -- they were a
        # ceiling being lowered by a factor of eight.

    # This one is added either way, and not only for the descriptors: it also
    # carries usb_descriptors.h, which is where the udisp WIRE FORMAT is
    # defined -- the frame header the network path parses. One definition of
    # that header is worth more than a tidier pair of files, so the component
    # registers its include directories whatever `usb:` says and its
    # CMakeLists returns before reaching for TinyUSB.
    esp32.add_idf_component(
        name="usb_display_tusb",
        path=os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "usb_display_tusb"
        ),
    )

    # And this is the whole of what `usb: false` buys: no TinyUSB in the build,
    # so nothing claims the OTG peripheral and a USB HOST may have it. See
    # _validate_usb.
    usb = config[CONF_USB]
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_DEVICE", usb)
    if usb:
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
    # A drive is a USB interface, so there is not one to present without USB --
    # and the script it carries is a hundred kilobytes of flash that would be
    # compiled in for nobody. Both halves key on the same name.
    sender_drive = usb and config[CONF_SENDER_DRIVE]
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_SENDER_DRIVE", sender_drive)
    # The HID report descriptor states the coordinate range, so it needs the
    # geometry at compile time as well.
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_WIDTH", config[CONF_WIDTH])
    esp32.add_idf_sdkconfig_option("CONFIG_USB_DISPLAY_HEIGHT", config[CONF_HEIGHT])
    # These two are the USB FACE of the touch screen and the speaker -- a HID
    # digitizer and a sound card -- and neither exists without a USB device.
    # Both things themselves survive: contacts still go back up the socket to
    # whoever is sending the picture, and the speaker still plays the PCM that
    # arrives on it.
    esp32.add_idf_sdkconfig_option(
        "CONFIG_USB_DISPLAY_TOUCH", usb and CONF_TOUCHSCREEN_ID in config
    )
    esp32.add_idf_sdkconfig_option(
        "CONFIG_USB_DISPLAY_AUDIO", usb and CONF_SPEAKER_ID in config
    )
    if speaker_id := config.get(CONF_SPEAKER_ID):
        if usb:
            esp32.add_idf_component(name="espressif/usb_device_uac", ref="~1.3.0")
            # Plugged into the TinyUSB device this component already brings up,
            # rather than bringing up one of its own. All of it is the USB
            # sound card; the speaker below is wired either way, because the
            # PCM that arrives over the network goes to the same place.
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

    if sender_drive:
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
