"""Opt-in, bounded LAN TCP speed test for Portall panels."""
import re

import esphome.codegen as cg
from esphome.components import esp32, socket
import esphome.config_validation as cv
import esphome.final_validate as fv
from esphome.const import CONF_ID, CONF_PORT

DEPENDENCIES = ["portall", "network"]
AUTO_LOAD = ["socket"]
ns = cg.esphome_ns.namespace("portall_speedtest")
SpeedTest = ns.class_("SpeedTest", cg.Component)


def key(value):
    value = cv.string_strict(value)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise cv.Invalid("Use a 64-digit hex key (openssl rand -hex 32)")
    return value


def validate(config):
    panel = fv.full_config.get().get("portall") or {}
    if config[CONF_PORT] in (panel.get("port"), panel.get("audio_port")):
        raise cv.Invalid("Speedtest port must differ from the Portall video and audio ports")
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema({
        cv.GenerateID(): cv.declare_id(SpeedTest),
        cv.Optional(CONF_PORT, default=5201): cv.port,
        cv.Required("key"): key,
    }).extend(cv.COMPONENT_SCHEMA),
    esp32.only_on_variant(supported="ESP32P4"),
    socket.consume_sockets(1, "portall_speedtest"),
    socket.consume_sockets(1, "portall_speedtest", socket.SocketType.TCP_LISTEN),
)
FINAL_VALIDATE_SCHEMA = validate


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_key(config["key"]))
