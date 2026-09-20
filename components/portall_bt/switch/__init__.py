"""switch: - platform: portall_bt -- Bluetooth off and on.

WHAT OFF ACTUALLY DOES, because the word promises more than this delivers:
both profiles hang up and the panel stops paging for them. The dongle stays
enumerated and Bluedroid stays running. Taking those apart at runtime means
esp_bluedroid_disable, detaching the HCI driver and stopping two reader tasks
on a stack this repository's notes record nine separate faults in raising --
and none of it can be compiled here to find out what it breaks.

What off is worth is still real: a panel whose speaker has left the house
pages for it on a 2 s -> 60 s clock for ever, and that is radio time beside a
C6 whose antenna is centimetres from the dongle's.
"""

import esphome.codegen as cg
from esphome.components import switch
import esphome.config_validation as cv

from .. import PortallBT, portall_bt_ns

DEPENDENCIES = ["portall_bt"]

PortallBTSwitch = portall_bt_ns.class_(
    "PortallBTSwitch", switch.Switch, cg.Component
)

CONF_PORTALL_BT_ID = "portall_bt_id"

CONFIG_SCHEMA = (
    switch.switch_schema(PortallBTSwitch)
    .extend(
        {
            cv.GenerateID(CONF_PORTALL_BT_ID): cv.use_id(PortallBT),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = await switch.new_switch(config)
    await cg.register_component(var, config)
    parent = await cg.get_variable(config[CONF_PORTALL_BT_ID])
    cg.add(var.set_parent(parent))
