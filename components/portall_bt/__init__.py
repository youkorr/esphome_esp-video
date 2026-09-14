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

CHERRYUSB'S VERSION IS PINNED, and not out of caution. `usbh_initialize` grew
a third parameter -- the event handler this component uses -- in 1.6.0; 1.5.x
takes two. A floating version would therefore stop compiling on an upgrade,
with an error that says nothing about why.
"""

import esphome.codegen as cg
from esphome.components import esp32
import esphome.config_validation as cv
from esphome.const import CONF_ID

CODEOWNERS = ["@youkorr"]
DEPENDENCIES = ["esp32"]

CONF_CONTROLLER = "controller"

# True selects the high-speed peripheral. The C++ turns it into CherryUSB's
# ESP_USB_HS0_BASE / ESP_USB_FS0_BASE rather than repeating those addresses
# here, so there is one definition of which register block is which.
CONTROLLERS = {"high_speed": True, "full_speed": False}

# See the module docstring: 1.6.0 changed usbh_initialize's signature.
CHERRYUSB_VERSION = "1.6.1"

portall_bt_ns = cg.esphome_ns.namespace("portall_bt")
PortallBT = portall_bt_ns.class_("PortallBT", cg.Component)

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(PortallBT),
            cv.Optional(CONF_CONTROLLER, default="high_speed"): cv.enum(
                CONTROLLERS, lower=True, space="_"
            ),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    esp32.only_on_variant(supported=[esp32.VARIANT_ESP32P4]),
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_high_speed(config[CONF_CONTROLLER]))

    # Fetched from Espressif's component registry at build time rather than
    # carried here: ESPHome writes this into src/idf_component.yml and the IDF
    # component manager downloads it. Nothing for the user to install.
    esp32.add_idf_component(name="cherry-embedded/cherryusb", ref=CHERRYUSB_VERSION)

    # CherryUSB's own CMakeLists force-enables every host class driver it
    # supports on IDF, so there is nothing to select per class here. These
    # three are what turn the stack on at all, and DWC2_ESP is the controller
    # driver for this chip's USB IP.
    esp32.add_idf_sdkconfig_option("CONFIG_CHERRYUSB", True)
    esp32.add_idf_sdkconfig_option("CONFIG_CHERRYUSB_HOST", True)
    esp32.add_idf_sdkconfig_option("CONFIG_CHERRYUSB_HOST_DWC2_ESP", True)
