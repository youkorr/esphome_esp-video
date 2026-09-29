"""Host a USB Bluetooth dongle on an ESP32-P4, as a Bluetooth CLASSIC host.

The ESP32-P4 has no radio, and the ESP32-C6 beside it on most P4 boards is
Bluetooth LE only. A dongle brings its own controller, Classic included, and
the P4 has two USB OTG peripherals to host it on. This component drives the
dongle through CherryUSB, attaches it to ESP-IDF's Bluedroid as its HCI
transport (esp_bluedroid_attach_hci_driver), and puts two profiles on top:

- `audio: true` -- an A2DP SOURCE. `speaker: - platform: usb_bluetooth` is an
  ordinary ESPHome speaker, so a media_player, a voice assistant or a mixer
  can play through a Bluetooth speaker, headphones or a car receiver.
- `hid: true` -- a HID HOST, for a gamepad, a keyboard or a remote. Buttons are
  decoded from the device's own report descriptor, and AVRCP buttons from the
  speaker too. `binary_sensor: - platform: usb_bluetooth` with `key: up` and so
  on turns them into binary sensors, which is what `lvgl: keypads:` takes.

Proved on real hardware (M5Stack Tab5, Guition, Waveshare 7B) with a Broadcom
BCM20702A1 and a TP-Link UB500 (Realtek RTL8761BU), an UGREEN car receiver
and an NVIDIA Shield controller. See README.md beside the components folder.

This is a stand-alone copy of `portall_bt` from
github.com/youkorr/esphome_esp-video, with everything that reached into the
`portall` screen component taken out. The long comments in the C++ still tell
the story of how each part was found on that project's panels; they are kept
because they are the reason each line is the way it is.

WHICH CONTROLLER. `controller:` picks the high-speed or full-speed OTG
peripheral. It must be the one the board wires its USB-A (host) socket to, and
nothing else in the firmware may use it: ESPHome's own `usb_host` or
`tinyusb` on the same peripheral is two drivers on one register block.

VBUS. A host supplies the 5 V. On the Tab5 it is switched by an IO expander,
which the board's own YAML turns on; nothing here touches it.

CHERRYUSB IS PATCHED AT BUILD TIME. Its ESP port fixes
CONFIG_USBHOST_MAX_INTF_ALTSETTINGS at 2 with no #ifndef, and a dongle's SCO
interface has six alternate settings, so the whole configuration descriptor is
refused and nothing enumerates. `cherryusb_patch/` beside this file is a tiny
ESP-IDF component that raises it to 8 in the downloaded copy, during CMake's
configure step. The real fix is four lines upstream in CherryUSB.

THE VERSION IS PINNED: `usbh_initialize` grew a third parameter in 1.6.0.
"""

import os

import esphome.automation as automation
from pathlib import Path

import esphome.codegen as cg
from esphome.components import esp32
from esphome.components.usb_host import DOMAIN as USB_HOST_DOMAIN
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_TRIGGER_ID
from esphome.core import CORE
import esphome.final_validate as fv

CODEOWNERS = ["@youkorr"]
DEPENDENCIES = ["esp32"]

CONF_CONTROLLER = "controller"
CONF_INQUIRY_SECONDS = "inquiry_seconds"
CONF_HOST_STACK = "host_stack"
CONF_HID = "hid"
CONF_AUDIO = "audio"
CONF_TEST_TONE = "test_tone"
CONF_DEVICE_NAME = "device_name"
CONF_PAIR_SECONDS = "pair_seconds"
CONF_SHOW_REPORTS = "show_reports"
CONF_FIRMWARE = "firmware"
CONF_FIRMWARE_CONFIG = "firmware_config"
CONF_ON_HID_REPORT = "on_hid_report"
CONF_ON_MEDIA_KEY = "on_media_key"
CONF_ON_MEDIA_VOLUME = "on_media_volume"

# "bluedroid" turns ESP-IDF's own Bluetooth host on, in the one configuration a
# chip with no controller of its own can have. See the module docstring.
HOST_STACKS = {"none": False, "bluedroid": True}


def _wants(config, key):
    """Whether an enum option is on, which `if config[key]` does NOT answer.

    `cv.enum({"none": False, ...})` returns the KEY as a string carrying the
    mapped value on `.enum_value`. So `config["host_stack"]` is the string
    "none" -- which is perfectly truthy -- and every `if` written against it is
    always taken. Codegen is unaffected, because cpp_generator's safe_exp
    unwraps an EnumValue before emitting it, and that is exactly what hid the
    fault: `set_host_stack(...)` emitted `false` correctly while the sdkconfig
    block beside it ran anyway.

    Which means every firmware built with `host_stack: none`, and every one
    that never mentioned it at all, has been compiling the whole of Bluedroid
    -- CONFIG_BT_ENABLED, Classic, A2DP, 1430 objects of it -- into a binary
    whose own C++ then refused to use it. Nothing failed. It cost flash and
    build time and said nothing, which is why it survived several releases.

    Found by writing a validator against the same wrong assumption and TESTING
    IT against a configuration it was supposed to refuse. It did not refuse it.
    That is the only reason any of this came to light, and it is the rule this
    repository keeps having to relearn: run the check against the state it was
    written to catch, before believing it.
    """
    return bool(config[key].enum_value)

# True selects the high-speed peripheral. The C++ turns it into CherryUSB's
# ESP_USB_HS0_BASE / ESP_USB_FS0_BASE rather than repeating those addresses
# here, so there is one definition of which register block is which.
CONTROLLERS = {"high_speed": True, "full_speed": False}

# See the module docstring: 1.6.0 changed usbh_initialize's signature.
CHERRYUSB_VERSION = "1.6.1"

usb_bluetooth_ns = cg.esphome_ns.namespace("usb_bluetooth")
UsbBluetooth = usb_bluetooth_ns.class_("UsbBluetooth", cg.Component)
PairAction = usb_bluetooth_ns.class_("PairAction", automation.Action)
ForgetAction = usb_bluetooth_ns.class_("ForgetAction", automation.Action)
# One action per role rather than one taking a role: a household reads
# `usb_bluetooth.forget_speaker` on a button and knows what it does. That is the
# same named-per-thing choice `quality:`, `user_agent:` and `fps:` per link
# were argued into, by the same person, and they were right each time.
ForgetSpeakerAction = usb_bluetooth_ns.class_("ForgetSpeakerAction", automation.Action)
ForgetInputAction = usb_bluetooth_ns.class_("ForgetInputAction", automation.Action)
# Several speakers are remembered and one plays; this chooses which.
UseSpeakerAction = usb_bluetooth_ns.class_("UseSpeakerAction", automation.Action)
# What `on_hid_report` hands the YAML: the bytes the device sent, nothing
# invented. ESP_HIDH_DATA_IND_EVT carries no report id -- see usb_bluetooth.h.
HID_REPORT_TRIGGER = automation.Trigger.template(cg.std_vector.template(cg.uint8))

# AVRCP, from the buttons on the speaker itself: a car receiver's steering
# wheel, a headphone's play/pause, a volume knob. `code` is an
# esp_avrc_pt_cmd_t -- 0x44 play, 0x46 pause, 0x4B next, 0x4C previous -- and
# unlike a HID report these ARE a fixed enumeration in Espressif's header, so
# naming them is reading rather than guessing.
MEDIA_KEY_TRIGGER = automation.Trigger.template(cg.uint8, cg.bool_)
MEDIA_VOLUME_TRIGGER = automation.Trigger.template(cg.float_)

def _validate_hid(config):
    """A profile needs a host, and the host is not on by default.

    `hid: true` with `host_stack: none` validates, compiles, flashes and then
    does nothing whatever -- there is no Bluetooth host in the build for a
    profile to attach to. That is the silent no-op this project keeps paying
    for, most recently as a probe firmware that spent a round trip to a board
    proving nothing because the question was never asked. So it is a refusal.
    """
    if config[CONF_HID] and not _wants(config, CONF_HOST_STACK):
        raise cv.Invalid(
            "hid: true needs a Bluetooth host to attach to. Add "
            "host_stack: bluedroid, which is what brings ESP-IDF's Classic "
            "stack into the build.",
            path=[CONF_HID],
        )
    if config[CONF_AUDIO] and not _wants(config, CONF_HOST_STACK):
        raise cv.Invalid(
            "audio: true needs a Bluetooth host to attach to. Add "
            "host_stack: bluedroid, which is what brings ESP-IDF's Classic "
            "stack into the build.",
            path=[CONF_AUDIO],
        )
    for key in (CONF_ON_MEDIA_KEY, CONF_ON_MEDIA_VOLUME):
        if key in config and not config[CONF_AUDIO]:
            raise cv.Invalid(
                f"{key} comes from AVRCP, which rides alongside the audio "
                "connection, so it needs audio: true. Without it nothing ever "
                "fires and the automation is silently dead.",
                path=[key],
            )
    if config[CONF_TEST_TONE] and not config[CONF_AUDIO]:
        raise cv.Invalid(
            "test_tone: has nothing to play through. It is the A2DP source's "
            "stand-in for a real sound, so it needs audio: true.",
            path=[CONF_TEST_TONE],
        )
    return config


USB_BLUETOOTH_ACTION_SCHEMA = automation.maybe_simple_id(
    {cv.GenerateID(): cv.use_id(UsbBluetooth)}
)


# Both do their work and return. pair() hands a discovery to the stack and
# comes straight back -- the results arrive on a callback -- and forget()
# walks the bond list and writes one preference before it ends. Neither waits
# on anything, which is what synchronous means here; esphome warns by name for
# an action registered without saying.
@automation.register_action(
    "usb_bluetooth.pair", PairAction, USB_BLUETOOTH_ACTION_SCHEMA, synchronous=True
)
@automation.register_action(
    "usb_bluetooth.forget", ForgetAction, USB_BLUETOOTH_ACTION_SCHEMA, synchronous=True
)
async def usb_bluetooth_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


# `slot:` forgets the ONE input device in that slot, 1 to 4; without it,
# every input device. A slot is only something a person can name on a screen
# that lists them one row each -- text_sensor's `input: slot:` is that row --
# which is why it is optional and the action still reads as a role without it.
CONF_SLOT = "slot"
MAX_INPUT_SLOTS = 4  # MAX_INPUTS in usb_bluetooth.h
MAX_SPEAKER_SLOTS = 4  # MAX_SINKS in usb_bluetooth.h


# `slot:` forgets the speaker remembered in that slot; without it, the one
# playing -- which is what the action always did.
@automation.register_action(
    "usb_bluetooth.forget_speaker",
    ForgetSpeakerAction,
    automation.maybe_simple_id(
        {
            cv.GenerateID(): cv.use_id(UsbBluetooth),
            cv.Optional(CONF_SLOT): cv.int_range(min=1, max=MAX_SPEAKER_SLOTS),
        }
    ),
    synchronous=True,
)
async def usb_bluetooth_forget_speaker_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    if CONF_SLOT in config:
        cg.add(var.set_slot(config[CONF_SLOT]))
    return var


# Play through the speaker remembered in that slot: the one playing is hung up
# and this one connected once the Bluetooth stack has let the other go. One
# plays at a time -- A2DP from this board is a single stream.
@automation.register_action(
    "usb_bluetooth.use_speaker",
    UseSpeakerAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(UsbBluetooth),
            cv.Required(CONF_SLOT): cv.int_range(min=1, max=MAX_SPEAKER_SLOTS),
        }
    ),
    synchronous=True,
)
async def usb_bluetooth_use_speaker_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    cg.add(var.set_slot(config[CONF_SLOT]))
    return var


@automation.register_action(
    "usb_bluetooth.forget_input",
    ForgetInputAction,
    # maybe_simple_id, so `usb_bluetooth.forget_input: dongle` still reads.
    automation.maybe_simple_id(
        {
            cv.GenerateID(): cv.use_id(UsbBluetooth),
            cv.Optional(CONF_SLOT): cv.int_range(min=1, max=MAX_INPUT_SLOTS),
        }
    ),
    synchronous=True,
)
async def usb_bluetooth_forget_input_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    if CONF_SLOT in config:
        cg.add(var.set_slot(config[CONF_SLOT]))
    return var


def _one_bluedroid_transport(config):
    """One Bluedroid, one HCI transport -- and the C6 is already a controller.

    An ESP32-P4 panel reaches Bluetooth twice over, and the two routes are the
    same architecture with a different wire underneath:

    - THIS component, over a USB dongle. It fills Espressif's
      `esp_bluedroid_hci_driver_operations_t` -- send / check_send_available /
      register_host_callback -- and calls `esp_bluedroid_attach_hci_driver()`.
    - ESPHome's `esp32_ble`, over the C6 that is already there for the Wi-Fi.
      When it sees an `esp32_hosted:` block it writes
      CONFIG_ESP_HOSTED_ENABLE_BT_BLUEDROID and CONFIG_ESP_HOSTED_BLUEDROID_HCI_VHCI,
      and esp-hosted's own Bluedroid glue then attaches THE SAME ops struct over
      the SDIO link. Espressif's design note says so in as many words.

    So both hand one Bluedroid host a transport, and it has room for one. There
    is nothing to arbitrate at runtime and nothing that could report it: whoever
    attaches second either replaces the first or is refused, and a panel would
    show a Bluetooth stack that is up and talks to nothing.

    THE SDKCONFIG SAYS IT WITHOUT ANYBODY ASKING. This component sets
    CONFIG_BT_CLASSIC_ENABLED True, because Classic is the whole point of a
    dongle; esp32_ble sets the same key False, because the C6 has no Classic
    radio to enable. `add_idf_sdkconfig_option` is a plain dict assignment, so
    the build takes whichever `to_code` ran last and says nothing at all --
    which is why both blocks in one file validated `ok` before this check
    existed: two settings that must agree, with nothing comparing them.

    The panel is not left with nothing either way, and the message says which
    half it can keep. The C6 is BLUETOOTH LE ONLY -- Espressif's esp-hosted
    documentation: "Classic Bluetooth requires an ESP32 as the co-processor.
    All other ESP chips provide BLE only." So a Bluetooth SPEAKER (A2DP) and a
    Classic gamepad are the dongle's and cannot move; a Bluetooth proxy, a
    thermometer, a BLE remote are the C6's and need no dongle.
    """
    if not _wants(config, CONF_HOST_STACK):
        return config
    fconf = fv.full_config.get()
    # esp32_ble is AUTO_LOADed by esp32_ble_tracker, bluetooth_proxy,
    # ble_client and esp32_ble_server alike, so it is the one key that catches
    # every way a YAML asks for the C6's Bluetooth.
    if "esp32_ble" not in fconf:
        return config

    raise cv.Invalid(
        "usb_bluetooth and ESPHome's own Bluetooth are both trying to drive one "
        "Bluedroid: this component attaches the USB dongle as Bluedroid's HCI "
        "transport, and esp32_ble (pulled in by esp32_ble_tracker, "
        "bluetooth_proxy, ble_client or esp32_ble_server) attaches the C6 over "
        "esp-hosted as the same thing. There is room for one, and the build "
        "would not say which it took -- the two also set "
        "CONFIG_BT_CLASSIC_ENABLED to opposite values into one dictionary.\n"
        "Pick by what the panel needs. The C6 is Bluetooth LOW ENERGY only, so "
        "a Bluetooth speaker (A2DP) and a Classic gamepad such as a Shield or "
        "a DualSense need the dongle: keep usb_bluetooth and take the "
        "esp32_ble_tracker / bluetooth_proxy blocks out. A Bluetooth proxy for "
        "Home Assistant, or a BLE sensor, needs no dongle at all: drop "
        "`usb_bluetooth:` and keep them.",
        path=[CONF_HOST_STACK],
    )


def _refuse_beside_usb_host(config):
    """This component and ESPHome's usb_host cannot both be in one firmware.

    The same rule, for the same reason, as `_reject_uvc_beside_usb_host` in
    esphome/esphome#16944 (esp_video_camera): two owners of a USB host is a
    boot that goes wrong, not a clear failure, so it is refused here.

    Here the two are not even the same stack. This component drives the dongle
    through CherryUSB, which takes the OTG peripheral's registers and interrupt
    for itself; usb_host installs ESP-IDF's USB Host Library with an empty
    `usb_host_config_t`, and a zero `peripheral_map` is the HIGH-SPEED
    peripheral on a P4 (ESP-IDF v5.5.5 usb_host.h), with no option to move it.
    So with `controller: high_speed`, the default, both drivers sit on one
    register block and one interrupt line -- the interrupt watchdog timeout
    portall already met when TinyUSB and CherryUSB shared that peripheral.
    With `controller: full_speed` the two would be on different peripherals,
    and that pairing has never been run; it is refused all the same until it
    has been.

    usb_uart AUTO_LOADs usb_host, so checking for the one domain catches both.
    """
    if USB_HOST_DOMAIN not in fv.full_config.get():
        return config
    raise cv.Invalid(
        "usb_bluetooth cannot be used in the same configuration as the usb_host "
        "component (which usb_uart also pulls in): usb_bluetooth runs its own "
        "USB host stack for the dongle, and usb_host installs ESP-IDF's on the "
        "high-speed controller, so the two would own the same USB hardware and "
        "the result is a crash at boot rather than a clear failure. Use one or "
        "the other for now.",
        path=[CONF_CONTROLLER],
    )


def _final_validate(config):
    _one_bluedroid_transport(config)
    _refuse_beside_usb_host(config)
    return config


# The firmware this component carries, beside its own source. A Realtek
# controller runs a ROM that answers every HCI command and does almost nothing
# on the air -- which is why a panel reported its speaker and its gamepad both
# sitting beside it and neither connecting, on a dongle whose stack had
# started perfectly.
#
# It lives HERE rather than in a household's configuration directory, and that
# is the whole point. `external_components` clones this repository and installs
# a meta finder over it -- it copies nothing and filters nothing -- so every
# file beside this one is on disk at build time for every user, with nothing
# to download and no path to get right. The first version took a path, and a
# panel reported exactly what that is worth:
#
#     Could not find file '/config/esphome/rtl8761bu_fw.bin'
#
# because `cv.file_` resolves against the CONFIG's directory and the files were
# in the repository. A fix the reader cannot reach from where they are standing
# has not been delivered, which this project has now recorded six times.
CARRIED_FIRMWARE = Path(__file__).parent / "firmware"


def _firmware(value):
    """Where to take the Realtek patch from: carried, none, or a file.

    The parse is rtlfw.py beside this file, which can be run against the real file on a
    workstation; the board is handed bytes and a length and never sees the
    format.
    """
    if isinstance(value, str) and value.strip().lower() in ("none", "off", "no"):
        return None
    path = cv.file_(value)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as err:
        raise cv.Invalid(f"{value} could not be read ({err})")


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(UsbBluetooth),
            cv.Optional(CONF_CONTROLLER, default="high_speed"): cv.enum(
                CONTROLLERS, lower=True, space="_"
            ),
            # How long to listen for Bluetooth Classic devices once the dongle
            # has answered. 0 turns it off. The specification's own ceiling is
            # 61 seconds and the C++ clamps to it.
            cv.Optional(
                CONF_INQUIRY_SECONDS, default="10s"
            ): cv.All(cv.positive_time_period_seconds, cv.Range(max=cv.TimePeriod(seconds=61))),
            cv.Optional(CONF_HOST_STACK, default="none"): cv.enum(HOST_STACKS, lower=True),
            # A gamepad, a keyboard, a mouse or a remote -- one feature, not
            # four. See hid.cpp; they differ only in which buttons get pressed.
            cv.Optional(CONF_HID, default=False): cv.boolean,
            # A2DP source: the panel sends its sound to a Bluetooth speaker,
            # a car receiver or a pair of headphones. Classic, so the C6 can
            # never do it and the dongle is the whole point.
            cv.Optional(CONF_AUDIO, default=False): cv.boolean,
            # A sine, in hertz, played whenever nothing else has been fed in.
            # 0 is off. It exists so the link can be proved before there is
            # anything real to play through it -- tools/playsound.py made the
            # same choice for the panel's own speaker.
            cv.Optional(CONF_TEST_TONE, default=0): cv.int_range(min=0, max=20000),
            # What this board calls itself while pairing. Seen once, on the
            # screen of whatever is being paired with. The node's own name when
            # left out.
            cv.Optional(CONF_DEVICE_NAME): cv.string_strict,
            # How long usb_bluetooth.pair scans for. The specification's ceiling
            # is 61 seconds and the C++ clamps to it.
            cv.Optional(CONF_PAIR_SECONDS, default="10s"): cv.All(
                cv.positive_time_period_seconds,
                cv.Range(max=cv.TimePeriod(seconds=61)),
            ),
            # Print every input report. A report descriptor differs per device,
            # so these bytes are the only specification there is for a mapping
            # -- and a moving thumbstick sends a hundred a second, which is why
            # it is a flag rather than the default.
            cv.Optional(CONF_SHOW_REPORTS, default=False): cv.boolean,
            cv.Optional(CONF_FIRMWARE): _firmware,
            cv.Optional(CONF_FIRMWARE_CONFIG): _firmware,
            cv.Optional(CONF_ON_HID_REPORT): automation.validate_automation(
                {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(HID_REPORT_TRIGGER)}
            ),
            cv.Optional(CONF_ON_MEDIA_KEY): automation.validate_automation(
                {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MEDIA_KEY_TRIGGER)}
            ),
            cv.Optional(CONF_ON_MEDIA_VOLUME): automation.validate_automation(
                {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MEDIA_VOLUME_TRIGGER)}
            ),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    esp32.only_on_variant(supported=[esp32.VARIANT_ESP32P4]),
    _validate_hid,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_high_speed(config[CONF_CONTROLLER]))
    cg.add(var.set_inquiry_seconds(config[CONF_INQUIRY_SECONDS].total_seconds))
    cg.add(var.set_host_stack(config[CONF_HOST_STACK]))
    cg.add(var.set_hid_host(config[CONF_HID]))
    cg.add(var.set_a2dp(config[CONF_AUDIO]))
    cg.add(var.set_test_tone(config[CONF_TEST_TONE]))
    cg.add(var.set_device_name(config.get(CONF_DEVICE_NAME, CORE.name)))
    cg.add(var.set_pair_seconds(config[CONF_PAIR_SECONDS].total_seconds))
    cg.add(var.set_show_reports(config[CONF_SHOW_REPORTS]))

    # Which patch to build in, and the DEFAULT is the one carried beside this
    # file: a household that plugs in a Realtek dongle should not have to know
    # that it needs a patch, let alone find one. `firmware: none` turns it off
    # for a board that will only ever see a Broadcom, which needs none; a path
    # names a different chip's pair.
    blob = config.get(CONF_FIRMWARE, "carried")
    conf = config.get(CONF_FIRMWARE_CONFIG, "carried")
    if blob == "carried":
        carried = CARRIED_FIRMWARE / "rtl8761bu_fw.bin"
        blob = carried.read_bytes() if carried.is_file() else None
    if conf == "carried":
        carried = CARRIED_FIRMWARE / "rtl8761bu_config.bin"
        conf = carried.read_bytes() if carried.is_file() else b""
    if conf is None:
        conf = b""

    if blob is not None:
        # Parsed at BUILD time, so the board never carries the format. Errors
        # here name the file rather than reaching a panel as silence on the
        # air, which is the failure this whole option exists to end.
        from . import rtlfw

        try:
            images, version, lmp, which = rtlfw.images(blob, conf)
        except rtlfw.NotFirmware as err:
            raise cv.Invalid(f"{CONF_FIRMWARE}: {err}")
        if len(images) > 4:
            raise cv.Invalid(
                f"{CONF_FIRMWARE}: this file covers {len(images)} ROM versions "
                f"and the board holds four"
            )
        for rom in sorted(images):
            blob = images[rom]
            name = f"rtl_firmware_{rom}"
            # One line per array rather than per byte: a thirty-kilobyte
            # literal is already a lot of generated source.
            body = ",".join(str(b) for b in blob)
            cg.add_global(
                cg.RawExpression(
                    f"static const uint8_t {name}[{len(blob)}] = {{{body}}}"
                )
            )
            cg.add(var.add_realtek_firmware(rom, cg.RawExpression(name), len(blob)))
    for conf in config.get(CONF_ON_HID_REPORT, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        cg.add(var.add_hid_report_trigger(trigger))
        await automation.build_automation(
            trigger, [(cg.std_vector.template(cg.uint8), "data")], conf
        )
    for conf in config.get(CONF_ON_MEDIA_KEY, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        cg.add(var.add_media_key_trigger(trigger))
        await automation.build_automation(
            trigger, [(cg.uint8, "code"), (cg.bool_, "pressed")], conf
        )
    for conf in config.get(CONF_ON_MEDIA_VOLUME, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        cg.add(var.add_media_volume_trigger(trigger))
        await automation.build_automation(trigger, [(cg.float_, "volume")], conf)

    # Fetched from Espressif's component registry at build time rather than
    # carried here: ESPHome writes this into src/idf_component.yml and the IDF
    # component manager downloads it. Nothing for the user to install.
    esp32.add_idf_component(name="cherry-embedded/cherryusb", ref=CHERRYUSB_VERSION)

    # And a component that compiles nothing, whose whole job is to raise one
    # constant in what was just downloaded. See the docstring, and
    # cherryusb_patch/patch.cmake beside this file, which says it at length in
    # the one place somebody debugging this would look.
    esp32.add_idf_component(
        name="cherryusb_patch",
        path=os.path.join(os.path.dirname(__file__), "cherryusb_patch"),
    )

    # CherryUSB's own CMakeLists force-enables every host class driver it
    # supports on IDF, so there is nothing to select per class here. These
    # three are what turn the stack on at all, and DWC2_ESP is the controller
    # driver for this chip's USB IP.
    esp32.add_idf_sdkconfig_option("CONFIG_CHERRYUSB", True)
    esp32.add_idf_sdkconfig_option("CONFIG_CHERRYUSB_HOST", True)
    esp32.add_idf_sdkconfig_option("CONFIG_CHERRYUSB_HOST_DWC2_ESP", True)

    if _wants(config, CONF_HOST_STACK):
        # Read out of ESP-IDF's own Kconfig rather than assumed, because every
        # one of these has a dependency and three of them would be refused on
        # this chip if the controller were not disabled:
        #
        #   BT_ENABLED             depends on !APP_NO_BLOBS
        #   BT_BLUEDROID_ENABLED   no dependency at all
        #   BT_CONTROLLER_ENABLED  depends on SOC_BT_SUPPORTED  <- not on a P4
        #   BT_CONTROLLER_DISABLED no dependency
        #       "recommended for Bluetooth Host only usecases"
        #   BT_CLASSIC_ENABLED     depends on BT_BLUEDROID_ENABLED &&
        #       ((BT_CONTROLLER_ENABLED && SOC_BT_CLASSIC_SUPPORTED)
        #        || BT_CONTROLLER_DISABLED)
        #   BT_A2DP_ENABLE         depends on BT_CLASSIC_ENABLED
        #
        # That `|| BT_CONTROLLER_DISABLED` in BT_CLASSIC_ENABLED is the whole
        # permission slip: with an external controller, Classic is selectable
        # whatever the chip itself can do. Espressif wrote that clause for this
        # case, and it is why A2DP on a P4 is a configuration rather than a
        # hope.
        # ESPHome does not merely leave ESP-IDF's components alone: it EXCLUDES
        # most of them, `bt` among them, and generates src/CMakeLists.txt with
        # REQUIRES set to whatever survives. So turning the sdkconfig options on
        # builds Bluedroid and still leaves our own file unable to see its
        # headers -- which is exactly what happened, in IDF's own words:
        #
        #   usb_bluetooth.cpp (in "src" component) includes esp_bt_main.h,
        #   provided by bt component(s). However, bt component(s) is not in
        #   the requirements list of "src".
        #
        # `include_builtin_idf_component` is the supported way back in. It takes
        # a name off ESPHome's exclusion set, and the generated REQUIRES is that
        # set's complement, so one call fixes both halves. Guarded because a
        # helper that moves between versions reaches a user as a build failure
        # on their own board, which this project has already paid for once.
        include_builtin = getattr(esp32, "include_builtin_idf_component", None)
        if include_builtin is None:
            raise cv.Invalid(
                "host_stack: bluedroid needs an ESPHome that can put ESP-IDF's "
                "bt component back into the build "
                "(esp32.include_builtin_idf_component). Upgrade ESPHome, or set "
                "host_stack: none."
            )
        include_builtin("bt")

        esp32.add_idf_sdkconfig_option("CONFIG_BT_ENABLED", True)
        esp32.add_idf_sdkconfig_option("CONFIG_BT_BLUEDROID_ENABLED", True)
        esp32.add_idf_sdkconfig_option("CONFIG_BT_CONTROLLER_DISABLED", True)
        esp32.add_idf_sdkconfig_option("CONFIG_BT_CLASSIC_ENABLED", True)

        # Espressif's coexistence messages, which only Espressif's own radio
        # understands. bta_dm_main.c sends two of them -- opcode 0xFC82, "A2DP
        # streaming" set and "A2DP paused" cleared, or the reverse -- every
        # time a speaker's stream starts or stops, and Bluedroid's Kconfig
        # turns them on by DEFAULT whenever BT_CONTROLLER_DISABLED, on the
        # assumption that the controller is then an ESP32 behind esp-hosted.
        # Here it is a USB dongle, which answers "Illegal Command" and logs a
        # warning pair each time -- every ten seconds of quiet on a panel,
        # since that is what suspends the stream (a2dp_idle_tick_). There is
        # no esp-hosted controller on this transport to tell anything to:
        # _one_bluedroid_transport refuses esp32_ble beside this component.
        esp32.add_idf_sdkconfig_option("CONFIG_BT_BLUEDROID_ESP_COEX_VSC", False)

        # A2DP, and with it AVRCP, which Bluedroid couples to the same option.
        # Asked for rather than always on: it is a profile like any other and
        # a panel that only wants a gamepad should not carry an audio stack.
        #
        # The internal SBC codec is what the default gives, and that is the
        # one this component uses -- see components/usb_bluetooth/a2dp.cpp. The
        # alternative, CONFIG_BT_A2DP_USE_EXTERNAL_CODEC, moves the encoding
        # into the application, and Espressif's own help text says the
        # internal one "will be removed in the future". When that happens this
        # needs an SBC encoder; it is not needed today.
        esp32.add_idf_sdkconfig_option("CONFIG_BT_A2DP_ENABLE", config[CONF_AUDIO])

        # And these two are the whole difference between a stack that starts
        # and one that aborts, on BOTH dongles this has been run against.
        #
        # Bluedroid's controller startup carries this, in device/controller.c:
        #
        #     #if (BLE_50_FEATURE_SUPPORT == TRUE && BLE_42_FEATURE_SUPPORT == FALSE)
        #     #if (BLE_50_EXTEND_SYNC_EN == TRUE)
        #             response = AWAIT_COMMAND(...read_periodic_adv_list_size());
        #
        # There is no `if` at run time. `LE Read Periodic Advertiser List Size`
        # -- opcode 0x204A, a Bluetooth 5.0 command -- is compiled in and sent
        # whatever the controller says it can do. Espressif can write it that
        # way because their own controller is built alongside it and always
        # supports it; with somebody else's controller it is an unconditional
        # 5.0 demand, and a controller that refuses answers `Unknown HCI
        # Command` -- a status and nothing else -- while
        # parse_ble_read_periodic_adv_list_size_response asserts on anything
        # shorter than five parameters.
        #
        # Measured, on two dongles that share nothing but a USB socket:
        #
        #   BCM20702A1 (4.0)  frame 16: opcode 204a, 4 parameters -> assert
        #   RTL8761BU  (5.x)  frame 19: opcode 204a, 4 parameters -> assert
        #
        # Turning the 4.2 features ON is what compiles that block out, because
        # the guard wants 50 AND NOT 42. It also drops 0x203A beside it, which
        # the 5.x dongle answered and the 4.0 one would not have.
        #
        # These are ESPHome's own two lines, written by request_bluetooth() --
        # which this component deliberately does not call, and the comment
        # saying why claimed it "writes BLE sdkconfig defaults nobody here
        # wants". They were exactly what this needed. The reason not to call it
        # stands (it does not exist in 2026.6.5), so the two lines are written
        # here instead, on purpose rather than by inheritance.
        esp32.add_idf_sdkconfig_option("CONFIG_BT_BLE_42_FEATURES_SUPPORTED", True)
        esp32.add_idf_sdkconfig_option("CONFIG_BT_BLE_50_FEATURES_SUPPORTED", True)

        if config[CONF_HID]:
            # Read out of ESP-IDF's own Kconfig.in rather than remembered:
            #
            #   BT_HID_ENABLED       depends on BT_CLASSIC_ENABLED, default n
            #   BT_HID_HOST_ENABLED  depends on BT_HID_ENABLED,     default n
            #
            # A menuconfig and the option under it, so both have to be set.
            esp32.add_idf_sdkconfig_option("CONFIG_BT_HID_ENABLED", True)
            esp32.add_idf_sdkconfig_option("CONFIG_BT_HID_HOST_ENABLED", True)

            # AND THIS ONE IS A TRAP, and it is Espressif's default rather than
            # a choice of theirs to argue with:
            #
            #   BT_HID_REMOVE_DEVICE_BONDING_ENABLED  default y
            #
            # It throws the pairing away when a device asks for a "virtual
            # cable unplug". The HID specification asks for that -- optional in
            # 1.0, mandatory in 1.1 -- and it is also, from the sofa, a gamepad
            # that has silently forgotten the panel and has to be paired again
            # by someone who did nothing wrong. A panel is not a PC being
            # handed between desks; the device it is paired with is the one in
            # the same room, for years. So the bonding stays, which is the
            # whole of what was asked for here.
            esp32.add_idf_sdkconfig_option(
                "CONFIG_BT_HID_REMOVE_DEVICE_BONDING_ENABLED", False
            )


FINAL_VALIDATE_SCHEMA = _final_validate
