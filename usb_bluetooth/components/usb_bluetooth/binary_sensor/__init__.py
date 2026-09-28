"""binary_sensor: - platform: usb_bluetooth -- one button of any input device.

ON while a key is pressed, for a moment, whichever device pressed it: a
television remote over AVRCP, a keyboard, or a gamepad read through its own
report descriptor. keys.cpp is where a device's button becomes one of these
names, once, for every kind of device.

Made for `lvgl: keypads:`, which takes one binary sensor per LVGL key. On a
keypad ONLY `next` and `prev` move LVGL's focus -- up, down, left and right
are handed to the focused widget, a slider or a roller (lv_indev.c,
indev_keypad_proc, LVGL 9.5) -- so to walk a page of buttons with a remote's
arrows, one sensor takes two of them:

    binary_sensor:
      - platform: usb_bluetooth
        id: bt_next
        key: [down, right]
      - platform: usb_bluetooth
        id: bt_prev
        key: [up, left]
      - platform: usb_bluetooth
        id: bt_enter
        key: enter
    lvgl:
      keypads:
        - next: bt_next
          prev: bt_prev
          enter: bt_enter

and just as usable on its own, with `on_press:`.

HELD FOR `hold:`, 100 ms by default. Most devices here report a PRESS and no
release that means anything to a key -- a gamepad's hat has no key-up, a
remote's AVRCP release is dropped on purpose -- and LVGL does not watch the
sensor, it READS it on its own input timer, every 30 ms by default. A sensor
that went ON and OFF inside one turn of the loop would be a press LVGL never
saw. 100 ms is three of its reads and still a short press: LVGL's long press
starts at 400.
"""

import esphome.codegen as cg
from esphome.components import binary_sensor
import esphome.config_validation as cv

from .. import UsbBluetooth, usb_bluetooth_ns

DEPENDENCIES = ["usb_bluetooth"]

CONF_USB_BLUETOOTH_ID = "usb_bluetooth_id"
CONF_KEY = "key"
CONF_HOLD = "hold"

UsbBluetoothKey = usb_bluetooth_ns.class_(
    "UsbBluetoothKey", binary_sensor.BinarySensor, cg.Component
)

# The keyboard page's own usages (HID Usage Tables, page 0x07), which is the
# vocabulary keys.cpp turns every device into. Named as `lvgl: keypads:` names
# its keys, so the two lists read alike.
PAGE_KEYBOARD = 0x07
KEYS = {
    "up": 0x52,
    "down": 0x51,
    "left": 0x50,
    "right": 0x4F,
    "enter": 0x28,
    "esc": 0x29,
    "backspace": 0x2A,
    "del": 0x4C,
    "next": 0x2B,  # Tab
    "end": 0x4D,
    # Home is ALSO a remote's Menu button and a gamepad's Home: see to_code.
    "home": 0x4A,
    "1": 0x1E,
    "2": 0x1F,
    "3": 0x20,
    "4": 0x21,
    "5": 0x22,
    "6": 0x23,
    "7": 0x24,
    "8": 0x25,
    "9": 0x26,
    "0": 0x27,
}

CONFIG_SCHEMA = (
    binary_sensor.binary_sensor_schema(UsbBluetoothKey)
    .extend(
        {
            cv.GenerateID(CONF_USB_BLUETOOTH_ID): cv.use_id(UsbBluetooth),
            cv.Required(CONF_KEY): cv.All(
                cv.ensure_list(cv.one_of(*KEYS, lower=True, string=True)),
                cv.Length(min=1),
            ),
            cv.Optional(
                CONF_HOLD, default="100ms"
            ): cv.positive_time_period_milliseconds,
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = await binary_sensor.new_binary_sensor(config)
    await cg.register_component(var, config)
    parent = await cg.get_variable(config[CONF_USB_BLUETOOTH_ID])
    cg.add(var.set_parent(parent))
    for key in config[CONF_KEY]:
        cg.add(var.add_usage(PAGE_KEYBOARD, KEYS[key]))
    cg.add(var.set_home("home" in config[CONF_KEY]))
    cg.add(var.set_hold(config[CONF_HOLD].total_milliseconds))
