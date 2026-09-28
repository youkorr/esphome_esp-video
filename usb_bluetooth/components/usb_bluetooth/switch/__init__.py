"""switch: - platform: usb_bluetooth -- Bluetooth off and on.

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

from .. import UsbBluetooth, usb_bluetooth_ns

DEPENDENCIES = ["usb_bluetooth"]

UsbBluetoothSwitch = usb_bluetooth_ns.class_(
    "UsbBluetoothSwitch", switch.Switch, cg.Component
)

CONF_USB_BLUETOOTH_ID = "usb_bluetooth_id"

CONFIG_SCHEMA = (
    switch.switch_schema(UsbBluetoothSwitch)
    .extend(
        {
            cv.GenerateID(CONF_USB_BLUETOOTH_ID): cv.use_id(UsbBluetooth),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = await switch.new_switch(config)
    await cg.register_component(var, config)
    parent = await cg.get_variable(config[CONF_USB_BLUETOOTH_ID])
    cg.add(var.set_parent(parent))
