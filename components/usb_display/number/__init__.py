"""Volume of the sound the host sends to this board.

The host sets its own volume over USB and this reflects the same value from the
other side, so moving either one moves the sound.
"""

import esphome.codegen as cg
from esphome.components import number
import esphome.config_validation as cv
from esphome.const import CONF_ID

from .. import USBDisplay, usb_display_ns

DEPENDENCIES = ["usb_display"]

USBVolumeNumber = usb_display_ns.class_("USBVolumeNumber", number.Number, cg.Component)

CONF_USB_DISPLAY_ID = "usb_display_id"

CONFIG_SCHEMA = number.number_schema(USBVolumeNumber).extend(
    {
        cv.GenerateID(CONF_USB_DISPLAY_ID): cv.use_id(USBDisplay),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = await number.new_number(config, min_value=0, max_value=100, step=1)
    await cg.register_component(var, config)
    parent = await cg.get_variable(config[CONF_USB_DISPLAY_ID])
    cg.add(var.set_parent(parent))
