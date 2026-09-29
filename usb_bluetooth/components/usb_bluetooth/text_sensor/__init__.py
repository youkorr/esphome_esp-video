"""text_sensor: - platform: usb_bluetooth -- what this board has paired.

ONE BLOCK, BOTH KINDS, which is the "all in one place" this exists for:

    text_sensor:
      - platform: usb_bluetooth
        speaker:
          name: "Bluetooth speaker"
        input:
          name: "Bluetooth controllers"

TWO ENTRIES AND NOT A LIST OF DEVICES, which was asked about directly -- "si
je dispose de plus peripherique bluetooth ... comment les text_sensor alors
qu'il que que deux text_sensor". The panel holds up to four input devices now
and `input:` names all of them on one line, separated by commas:

    Orange TV remote (A4:C1:38:9E:22:07) connected, NVIDIA Controller
    v01.04 (00:04:4B:93:A9:B2) paired, away

rather than one entity per slot. A slot is not a thing a household chose --
it is wherever a device happened to land -- so an entity per slot would put
three empty cards on a device page for every panel with one gamepad, which is
the dark entity this component already had to take out once.

ONE SPEAKER PLAYS and that is structural: A2DP source is a single stream with
one encoder. Up to four are remembered; `speaker:` without `slot:` names the
one playing, and with `slot:` names one remembered speaker, reading "not in
use" when it is not the one playing.

Each entry reports what the device calls itself, its address and whether it is
connected -- or `none` when nothing of that kind is paired.

A REMOTE IS AN INPUT DEVICE, so `input:` is its slot. A Bluetooth remote pairs
over HID exactly as a gamepad does, which is why there is no third entry for
one -- and with several slots a panel can now hold both at once rather than
the last one paired replacing the other.

The name is held in RAM, not in NVS. The stored record is six bytes per
device and is deliberately kept that small -- an ESPHome preference is found
by a hash AND a size, so growing the older one would have made every panel
that has ever paired forget what it is paired to, which is why the input list
is a record of its own under its own key. The cost of keeping the name in RAM
is that a panel which has just restarted shows the address alone until the
device connects and answers a Remote Name Request.
"""

import esphome.codegen as cg
from esphome.components import text_sensor
import esphome.config_validation as cv

from .. import MAX_INPUT_SLOTS, MAX_SPEAKER_SLOTS, UsbBluetooth, usb_bluetooth_ns

DEPENDENCIES = ["usb_bluetooth"]

UsbBluetoothTextSensor = usb_bluetooth_ns.class_(
    "UsbBluetoothTextSensor", text_sensor.TextSensor, cg.PollingComponent
)

CONF_USB_BLUETOOTH_ID = "usb_bluetooth_id"
CONF_SPEAKER = "speaker"
CONF_INPUT = "input"
CONF_SLOT = "slot"

_SENSOR_SCHEMA = text_sensor.text_sensor_schema(
    UsbBluetoothTextSensor
).extend(cv.polling_component_schema("10s"))

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_USB_BLUETOOTH_ID): cv.use_id(UsbBluetooth),
        # `slot:` reports ONE remembered speaker -- "not in use" unless it is
        # the one playing -- for a row with its own Use and Forget buttons.
        # Without it, the speaker playing.
        cv.Optional(CONF_SPEAKER): _SENSOR_SCHEMA.extend(
            {cv.Optional(CONF_SLOT): cv.int_range(min=1, max=MAX_SPEAKER_SLOTS)}
        ),
        # `slot:` reports ONE input slot rather than the list: what a screen
        # needs to show one device per row, each with its own Forget
        # (usb_bluetooth.forget_input with the same slot). "none" when empty.
        cv.Optional(CONF_INPUT): _SENSOR_SCHEMA.extend(
            {cv.Optional(CONF_SLOT): cv.int_range(min=1, max=MAX_INPUT_SLOTS)}
        ),
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_USB_BLUETOOTH_ID])
    for key, is_speaker in ((CONF_SPEAKER, True), (CONF_INPUT, False)):
        if key not in config:
            continue
        var = await text_sensor.new_text_sensor(config[key])
        await cg.register_component(var, config[key])
        cg.add(var.set_parent(parent))
        cg.add(var.set_speaker(is_speaker))
        if CONF_SLOT in config[key]:
            cg.add(var.set_slot(config[key][CONF_SLOT]))
