# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
"""A network test bench: the ESP32-P4 and its C6, three socket servers, and
in every answer what the build was compiled with.

It asks for ESPHome's high-performance networking exactly as a streaming
component does (portall, sendspin), because that request is what the Wi-Fi
settings under test hang on: without it neither build applies any of them.
Nothing else is set here unless asked for, so what differs between two
builds is ESPHome's.
"""
import esphome.codegen as cg
from esphome.components import esp32
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_PORT
from esphome.core import CORE, coroutine_with_priority

DEPENDENCIES = ["network"]
AUTO_LOAD = ["socket"]

CONF_LABEL = "label"
CONF_MALLOC_TO_PSRAM = "malloc_to_psram"

# What Portall sets on a panel for the same reason (components/portall,
# _let_malloc_spill_to_psram), and found the same way.
_SPIRAM_MALLOC = {
    "CONFIG_SPIRAM_USE_CAPS_ALLOC": False,
    "CONFIG_SPIRAM_USE_MALLOC": True,
    "CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL": 131072,
}

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
            # esp-hosted copies every received packet with a plain malloc()
            # ("bus_rx: payload copy alloc failed" in 3.1.0's
            # eh_host_bus_sdio.c), ESPHome keeps malloc() in internal RAM,
            # and its high-performance window of 512000 bytes lets in more
            # than a P4's internal RAM holds. Measured on a panel: free
            # internal heap from 476 KB to 20 KB, packets dropped, TCP
            # receive stopped. True lets malloc() spill into PSRAM, in both
            # builds alike; false is ESPHome as it is, which reproduces that.
            cv.Optional(CONF_MALLOC_TO_PSRAM, default=False): cv.boolean,
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
    if config[CONF_MALLOC_TO_PSRAM]:
        CORE.add_job(_let_malloc_spill_to_psram)


@coroutine_with_priority(-1000.0)
async def _let_malloc_spill_to_psram():
    """After psram's to_code, which sets CAPS_ALLOC. Only with PSRAM and
    esp-hosted, and a value written in sdkconfig_options wins."""
    psram = CORE.config.get("psram")
    if not psram or psram.get("disabled") or "esp32_hosted" not in CORE.config:
        return
    framework = (CORE.config.get("esp32") or {}).get("framework") or {}
    user = framework.get("sdkconfig_options") or {}
    if any(name in user for name in _SPIRAM_MALLOC):
        return
    for name, value in _SPIRAM_MALLOC.items():
        esp32.add_idf_sdkconfig_option(name, value)
