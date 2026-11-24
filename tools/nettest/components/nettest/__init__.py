# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""A network test bench: the ESP32-P4 and its C6, three socket servers, and
in every answer what the build was compiled with.

It asks for ESPHome's high-performance networking exactly as a streaming
component does (portall, sendspin), because that request is what the Wi-Fi
settings under test hang on: without it neither build applies any of them.
Nothing else is set here, so what differs between two builds is ESPHome's.
"""
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_PORT

DEPENDENCIES = ["network"]
AUTO_LOAD = ["socket"]

CONF_LABEL = "label"

nettest_ns = cg.esphome_ns.namespace("nettest")
NetTest = nettest_ns.class_("NetTest", cg.Component)


def _ask_for_fast_network(config):
    from esphome.components import network, socket

    network.require_high_performance_networking()
    socket.consume_sockets(2, "nettest")(config)
    socket.consume_sockets(2, "nettest", socket.SocketType.TCP_LISTEN)(config)
    socket.consume_sockets(1, "nettest", socket.SocketType.UDP)(config)
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(NetTest),
            cv.Optional(CONF_PORT, default=5201): cv.int_range(min=1, max=65533),
            # Said in every answer, so a result cannot be filed under the
            # wrong build. Up to 15 characters.
            cv.Required(CONF_LABEL): cv.All(cv.string_strict, cv.Length(min=1, max=15)),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.only_on_esp32,
    _ask_for_fast_network,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_label(config[CONF_LABEL]))
