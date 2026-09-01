"""How loudly this board plays the sound it is sent.

It governs both ways in: the USB audio class and the network, which share one
speaker and one volume. Over USB the host has its own control and this shows
the same value from the other side, so moving either one moves the sound.
"""

import esphome.codegen as cg
from esphome.components import number
import esphome.config_validation as cv
from esphome.const import CONF_ID

from .. import Portall, portall_ns

DEPENDENCIES = ["portall"]

USBVolumeNumber = portall_ns.class_("USBVolumeNumber", number.Number, cg.Component)

CONF_PORTALL_ID = "portall_id"
# The old spelling, kept working rather than refused. Nobody normally writes
# either -- the id is generated when there is one component to point at -- so
# this exists only for a configuration that names it by hand, where breaking it
# would buy nothing but a puzzled evening.
CONF_USB_DISPLAY_ID = "usb_display_id"

CONFIG_SCHEMA = cv.All(
    number.number_schema(USBVolumeNumber)
    .extend(
        {
            cv.GenerateID(CONF_PORTALL_ID): cv.use_id(Portall),
            cv.Optional(CONF_USB_DISPLAY_ID): cv.use_id(Portall),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = await number.new_number(config, min_value=0, max_value=100, step=1)
    await cg.register_component(var, config)
    parent = await cg.get_variable(
        config.get(CONF_USB_DISPLAY_ID) or config[CONF_PORTALL_ID]
    )
    cg.add(var.set_parent(parent))
