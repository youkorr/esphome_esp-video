"""text_sensor: - platform: portall_bt -- what this panel has paired.

ONE BLOCK, BOTH DEVICES, which is the "all in one place" this exists for:

    text_sensor:
      - platform: portall_bt
        speaker:
          name: "Bluetooth speaker"
        input:
          name: "Bluetooth controller"

There are exactly two and that is structural rather than a shortcut.
`Remembered` in portall_bt.h holds one address for a speaker and one for an
input device, because reconnecting BY ADDRESS is what lets this component come
back without an inquiry -- and an inquiry is what takes the panel's own Wi-Fi
down for as long as it runs. So there is no list to page through; there is a
speaker slot and an input slot.

Each reports what the device calls itself, its address and whether it is
connected -- or `none`:

    Orange TV remote (A4:C1:38:9E:22:07) connected

A REMOTE IS AN INPUT DEVICE, so `input:` is its slot. A Bluetooth remote pairs
over HID exactly as a gamepad does, which is why there is no third entry here
for one; if a panel has both, the slot holds whichever was paired last.

The name is held in RAM, not in NVS. `Remembered` stores six bytes per role
and is deliberately left alone -- it is already in the NVS of every panel that
has ever paired, and an ESPHome preference is found by a hash AND a size, so
growing it would make each of them forget what it is paired to. The cost of
keeping the name in RAM is that a panel which has just restarted shows the
address alone until the device connects and answers a Remote Name Request.
"""

import esphome.codegen as cg
from esphome.components import text_sensor
import esphome.config_validation as cv

from .. import PortallBT, portall_bt_ns

DEPENDENCIES = ["portall_bt"]

PortallBTTextSensor = portall_bt_ns.class_(
    "PortallBTTextSensor", text_sensor.TextSensor, cg.PollingComponent
)

CONF_PORTALL_BT_ID = "portall_bt_id"
CONF_SPEAKER = "speaker"
CONF_INPUT = "input"

_SENSOR_SCHEMA = text_sensor.text_sensor_schema(
    PortallBTTextSensor
).extend(cv.polling_component_schema("10s"))

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_PORTALL_BT_ID): cv.use_id(PortallBT),
        cv.Optional(CONF_SPEAKER): _SENSOR_SCHEMA,
        cv.Optional(CONF_INPUT): _SENSOR_SCHEMA,
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_PORTALL_BT_ID])
    for key, is_speaker in ((CONF_SPEAKER, True), (CONF_INPUT, False)):
        if key not in config:
            continue
        var = await text_sensor.new_text_sensor(config[key])
        await cg.register_component(var, config[key])
        cg.add(var.set_parent(parent))
        cg.add(var.set_speaker(is_speaker))
