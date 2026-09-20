"""Host a USB Bluetooth dongle on the ESP32-P4, and report what answers.

The ESP32-C6 that gives these panels their radio is BLE only -- no Bluetooth
Classic, so no A2DP and no Classic HID. A dongle brings its own controller,
Classic included, and the P4 has a spare USB controller to host it on. That is
the whole idea; everything here is the first step of proving it.

This step is deliberately SMALL. It starts a USB host on one of the P4's two
OTG controllers and prints what enumerates: address, speed, vendor and product,
and the class of every interface. It does not speak HCI and it does not carry a
Bluetooth stack. The reason for stopping there is that three separate things
are unproven and they fail in the same place -- that the build works at all,
that CherryUSB drives this chip's host port under ESPHome, and that a
FULL-SPEED dongle enumerates on a HIGH-SPEED PHY. One line of log answers all
three, and nothing above them is worth writing until it does.

What to look for, with a Bluetooth dongle plugged in:

    portall_bt: device 1 on bus 0: 0a5c:21e8, full speed
    portall_bt:   interface 0: class e0 subclass 01 protocol 01
    portall_bt:   interface 1: class e0 subclass 01 protocol 01

And then it puts the radio to work: an `inquiry_seconds:` window of Bluetooth
CLASSIC inquiry, which is exactly what the ESP32-C6 on these panels cannot do,
so every device it names is one no panel here could have heard before.

    portall_bt: listening for Bluetooth devices for about 10 seconds
    portall_bt:   found 4C:87:5D:11:22:33  audio/video  -54 dBm
    portall_bt: inquiry finished, status 00, 3 devices heard

Class E0 subclass 1 protocol 1 is the Bluetooth primary controller descriptor,
and it is exactly what CherryUSB's own class driver matches on -- so a dongle
that prints those two lines is one its driver would bind to unchanged. That
driver is not built here: CherryUSB deliberately switches it off for ESP-IDF
(`# set(CONFIG_CHERRYUSB_HOST_BLUETOOTH 1)` in its CMakeLists, and
`depends on !IDF_CMAKE` in its Kconfig), so carrying the file is the second
step, once this one has printed something.

WHICH CONTROLLER. The P4 has two OTG peripherals, high-speed and full-speed,
and `controller:` picks one. The default is high-speed because that is where
the sockets are: on the M5Stack Tab5 the USB-A host port is the high-speed
one, and its 5V is switched by the IO expander rather than being always on --
see the YAML note below. The full-speed controller sits on GPIO26/27 by
default, which the Tab5 uses for I2S, so it is only an option on a board that
routes those two pins somewhere.

AND IT CANNOT BE SHARED WITH portall. That component puts the high-speed
controller in DEVICE mode, so on the Tab5 the two cannot both have it. This is
meant to be built on its own for now -- a firmware with `portall_bt:` and no
`portall:` block. A panel fed over Wi-Fi loses nothing by it, since the USB-C
is carrying power and not pictures, but it is a separate firmware all the same.

VBUS. A host supplies the 5V, and on the Tab5 that is a pin of the second
PI4IOE5V6408 (0x44, bit 3, `usb_5v_power`). Nothing here touches it: it is a
GPIO on an expander the board's own YAML already configures, and a component
that reached in to switch somebody's power rail would be guessing at which
board it is on. Turn it on from the YAML before anything can enumerate:

    switch:
      - platform: gpio
        pin:
          pi4ioe5v6408: pi4ioe2
          number: 3
        id: usb_5v_power
        restore_mode: ALWAYS_ON

CHERRYUSB IS PATCHED AT BUILD TIME, and one number is the whole reason. Its
ESP port fixes how many alternate settings an interface may have at two:

    osal/idf/usb_config.h:  #define CONFIG_USBHOST_MAX_INTF_ALTSETTINGS 2

A Bluetooth dongle has two interfaces. The first carries HCI; the second
carries SCO -- voice -- and that one has SIX alternate settings, one per audio
channel bandwidth. The USB Bluetooth class says so, so every dongle is built
that way. CherryUSB's parser gives up at the third and returns -USB_ERR_NOMEM
for the WHOLE configuration descriptor, so the device never enumerates.
Measured on a Tab5 with a Broadcom BCM20702A1:

    [I/usbh_core] New device found,idVendor:0a5c,idProduct:21e8
    [E/usbh_core] Interface altsetting num 2 overflow
    [E/usbh_core] Parse config descriptor fail

Note what came BEFORE those two lines: the device descriptor was read. So the
host works, the full-speed dongle attaches to the high-speed PHY, and the only
thing in the way is an array four entries too short.

It cannot be reached from a configuration. It is not a Kconfig option, and it
is a bare #define with no #ifndef around it -- on the pinned version and on
master alike -- so neither sdkconfig nor a -D can override it. The first
answer here was a fork of CherryUSB, and it was the wrong one: a second
repository to keep in step, and a build that fails for anybody who has not
made it. `components/cherryusb_patch/` is a tiny ESP-IDF component that
compiles nothing and edits that one line in the copy the component manager
downloaded INTO THE BUILD DIRECTORY, loudly, before any compiler reads it.
Everything stays in this repository and there is nothing for anybody to
create.

It also explains something that had looked like a policy: CherryUSB switches
its Bluetooth class driver off for ESP-IDF. At two alternate settings that
driver could never have bound to any dongle, because none of them enumerate.

The fix worth having is upstream and is four lines -- putting these constants
behind `#ifndef`, the way the rest of that same file already does for
everything else -- and then a project sets them from its own build. When that
lands, the replacement stops matching and the patch retires itself.

`host_stack: bluedroid` IS A PROBE, and it is deliberately only half a step.

It turns on ESP-IDF's own Bluetooth host in the one configuration a chip with
no controller of its own can have -- Bluedroid, Classic and A2DP, with
BT_CONTROLLER_DISABLED -- and calls `esp_bluedroid_init()`, which sets the
stack up without trying to talk to anything. It does NOT call
`esp_bluedroid_enable()`, because that is where the host reaches for a
controller and there is not yet anything for it to reach.

The point is the BUILD. Nothing about whether that configuration compiles and
links for an esp32p4 target can be established from a Kconfig file: the
Kconfig says it is selectable, which is a different claim. If it links, the
route is open and the glue is the work. If it does not, the undefined symbols
ARE the specification for that glue -- the host stack naming, in the linker's
own words, exactly what a controller is expected to provide.

Leave it at `none` unless you are running that experiment.

THE VERSION IS PINNED, and not out of caution. `usbh_initialize` grew a third
parameter -- the event handler this component uses -- in 1.6.0; 1.5.x takes
two. A floating version would stop compiling on an upgrade, with an error
that says nothing about why.
"""

import os

import esphome.automation as automation
import esphome.codegen as cg
from esphome.components import esp32
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_TRIGGER_ID
import esphome.final_validate as fv

CODEOWNERS = ["@youkorr"]
DEPENDENCIES = ["esp32"]

CONF_CONTROLLER = "controller"
# portall's own option names, spelt out rather than imported: portall_bt is a
# whole firmware without portall (yaml/tab5-bt-probe.yaml), so a hard import
# would fail for every board that has only this component.
CONF_USB = "usb"
CONF_USB_SPEED = "usb_speed"
CONF_INQUIRY_SECONDS = "inquiry_seconds"
CONF_HOST_STACK = "host_stack"
CONF_HID = "hid"
CONF_AUDIO = "audio"
CONF_TEST_TONE = "test_tone"
CONF_DEVICE_NAME = "device_name"
CONF_PAIR_SECONDS = "pair_seconds"
CONF_SHOW_REPORTS = "show_reports"
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

portall_bt_ns = cg.esphome_ns.namespace("portall_bt")
PortallBT = portall_bt_ns.class_("PortallBT", cg.Component)
PairAction = portall_bt_ns.class_("PairAction", automation.Action)
ForgetAction = portall_bt_ns.class_("ForgetAction", automation.Action)
# One action per role rather than one taking a role: a household reads
# `portall_bt.forget_speaker` on a button and knows what it does. That is the
# same named-per-thing choice `quality:`, `user_agent:` and `fps:` per link
# were argued into, by the same person, and they were right each time.
ForgetSpeakerAction = portall_bt_ns.class_("ForgetSpeakerAction", automation.Action)
ForgetInputAction = portall_bt_ns.class_("ForgetInputAction", automation.Action)
# What `on_hid_report` hands the YAML: the bytes the device sent, nothing
# invented. ESP_HIDH_DATA_IND_EVT carries no report id -- see portall_bt.h.
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


PORTALL_BT_ACTION_SCHEMA = automation.maybe_simple_id(
    {cv.GenerateID(): cv.use_id(PortallBT)}
)


# Both do their work and return. pair() hands a discovery to the stack and
# comes straight back -- the results arrive on a callback -- and forget()
# walks the bond list and writes one preference before it ends. Neither waits
# on anything, which is what synchronous means here; esphome warns by name for
# an action registered without saying.
@automation.register_action(
    "portall_bt.pair", PairAction, PORTALL_BT_ACTION_SCHEMA, synchronous=True
)
@automation.register_action(
    "portall_bt.forget", ForgetAction, PORTALL_BT_ACTION_SCHEMA, synchronous=True
)
@automation.register_action(
    "portall_bt.forget_speaker",
    ForgetSpeakerAction,
    PORTALL_BT_ACTION_SCHEMA,
    synchronous=True,
)
@automation.register_action(
    "portall_bt.forget_input",
    ForgetInputAction,
    PORTALL_BT_ACTION_SCHEMA,
    synchronous=True,
)
async def portall_bt_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


def _one_controller_each(config):
    """Two drivers must not be handed the same USB peripheral.

    Reported from a Waveshare 7B as a board that has to be REBOOTED once the
    Bluetooth dongle is plugged in, with an `Interrupt wdt timeout on CPU1`
    whose register dump was sitting inside `[C][display.mipi_dsi:389]` --
    dump_config, so at boot rather than in use.

    THE ESP32-P4 HAS TWO USB OTG PERIPHERALS AND BOTH COMPONENTS DEFAULT TO
    THE SAME ONE. `portall:` defaults to `usb: true` with `usb_speed: high`,
    which puts TinyUSB on the high-speed controller as a DEVICE; `portall_bt:`
    defaults to `controller: high_speed`, which puts CherryUSB on that same
    register block as a HOST. Two drivers, one peripheral, one interrupt line.

    And nothing compared them, so it built, validated, flashed and booted.
    What makes it look like a Bluetooth fault rather than a configuration one
    is WHEN it bites: with the socket empty the host side enumerates nothing
    and stays quiet, so the board comes up perfectly. Plug the dongle in and
    the host starts servicing a peripheral TinyUSB also owns -- which is
    exactly "it needs rebooting once the dongle is in".

    Judged on what the two settings MEAN rather than on their spellings, which
    differ between the components ("high" against "high_speed"): both say high
    speed if they start with "high", so a rename on either side still compares
    correctly instead of silently passing.
    """
    fconf = fv.full_config.get()
    portall = fconf.get("portall")
    # portall_bt on its own is a whole firmware -- yaml/tab5-bt-probe.yaml is
    # one -- so a configuration without portall has nothing to collide with.
    if portall is None or not portall.get(CONF_USB, False):
        return config

    device_high = str(portall.get(CONF_USB_SPEED, "high")).startswith("high")
    host_high = str(config[CONF_CONTROLLER]).startswith("high")
    if device_high != host_high:
        return config

    which = "high-speed" if host_high else "full-speed"
    raise cv.Invalid(
        f"portall and portall_bt are both using the {which} USB controller: "
        f"portall is a USB DEVICE on it (usb: true, usb_speed: "
        f"{portall.get(CONF_USB_SPEED, 'high')}) and portall_bt drives it as a "
        f"HOST for the dongle (controller: {config[CONF_CONTROLLER]}). The "
        "ESP32-P4 has two of these peripherals and one driver may have each. "
        "A board like this boots normally with the socket empty and crashes "
        "with an interrupt watchdog timeout once a dongle is plugged in.\n"
        "Either add `usb: false` under portall: -- the picture, the touches "
        "and the sound then all arrive over `port:`, which is what "
        "yaml/tab5-portall-bluetooth.yaml does -- or move one of them to the "
        "other controller, if this board's sockets are wired for that.",
        path=[CONF_CONTROLLER],
    )


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(PortallBT),
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
            # What this panel calls itself while pairing. Seen once, on the
            # screen of whatever is being paired with.
            cv.Optional(CONF_DEVICE_NAME, default="portall"): cv.string_strict,
            # How long portall_bt.pair scans for. The specification's ceiling
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
    cg.add(var.set_device_name(config[CONF_DEVICE_NAME]))
    cg.add(var.set_pair_seconds(config[CONF_PAIR_SECONDS].total_seconds))
    cg.add(var.set_show_reports(config[CONF_SHOW_REPORTS]))
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
    # components/cherryusb_patch/patch.cmake, which says it at length in the
    # one place somebody debugging this would look.
    esp32.add_idf_component(
        name="cherryusb_patch",
        path=os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "cherryusb_patch"
        ),
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
        #   portall_bt.cpp (in "src" component) includes esp_bt_main.h,
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

        # A2DP, and with it AVRCP, which Bluedroid couples to the same option.
        # Asked for rather than always on: it is a profile like any other and
        # a panel that only wants a gamepad should not carry an audio stack.
        #
        # The internal SBC codec is what the default gives, and that is the
        # one this component uses -- see components/portall_bt/a2dp.cpp. The
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


FINAL_VALIDATE_SCHEMA = _one_controller_each
