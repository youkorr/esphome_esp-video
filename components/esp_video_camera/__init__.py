"""
Plateforme caméra ESPHome pour esp_video → API Home Assistant.

Expose le flux du pipeline esp_video (encodeur JPEG matériel pour les capteurs
MIPI-CSI auto-détectés, ou caméra USB-UVC) comme une entité `camera` de l'API
native Home Assistant. La caméra apparaît directement dans Home Assistant, sans
serveur web ni configuration supplémentaire.

Fonctionne avec TOUS les capteurs supportés par esp_video (SC202CS, OV5647,
OV02C10, SC2336...) ainsi qu'avec une caméra USB-UVC (enable_uvc: true).
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID
from esphome.core.entity_helpers import setup_entity

CODEOWNERS = ["@youkorr"]
DEPENDENCIES = ["esp_video"]
AUTO_LOAD = ["camera"]

esp_video_camera_ns = cg.esphome_ns.namespace("esp_video_camera")
ESPVideoCamera = esp_video_camera_ns.class_("ESPVideoCamera", cg.Component, cg.EntityBase)

CONF_DEVICE = "device"
CONF_RESOLUTION = "resolution"
CONF_JPEG_QUALITY = "jpeg_quality"
CONF_MAX_FRAMERATE = "max_framerate"

_RESOLUTION_ALIASES = ("QVGA", "VGA", "480P", "720P", "1080P")


def validate_resolution(value):
    """Accepte "auto", un alias (QVGA/VGA/480P/720P/1080P) ou un format "WxH"."""
    value = cv.string(value)
    if value.lower() == "auto":
        return "auto"
    if value.upper() in _RESOLUTION_ALIASES:
        return value.upper()
    parts = value.lower().split("x")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0])}x{int(parts[1])}"
    raise cv.Invalid(
        f"resolution '{value}' invalide. Utilisez 'auto', un alias "
        "(QVGA/VGA/480P/720P/1080P) ou 'LARGEURxHAUTEUR' (ex: '1280x720')."
    )


def validate_device(value):
    """Accepte un alias ("jpeg", "uvc", "uvc0".."uvc9", "csi") ou un chemin /dev/videoN."""
    value = cv.string(value)
    low = value.lower()
    if low in ("jpeg", "uvc", "csi"):
        return low
    if low.startswith("uvc") and len(low) == 4 and low[3].isdigit():
        return low
    if value.startswith("/dev/video"):
        return value
    raise cv.Invalid(
        f"device '{value}' invalide. Utilisez 'jpeg' (encodeur matériel, capteurs MIPI), "
        "'uvc' / 'uvc0'..'uvc9' (caméra USB-UVC), 'csi', ou un chemin '/dev/videoN'."
    )


CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(ESPVideoCamera),
            cv.Optional(CONF_DEVICE, default="jpeg"): validate_device,
            cv.Optional(CONF_RESOLUTION, default="auto"): validate_resolution,
            cv.Optional(CONF_JPEG_QUALITY, default=10): cv.int_range(min=1, max=63),
            cv.Optional(CONF_MAX_FRAMERATE, default=10): cv.float_range(min=0.1, max=60.0),
        }
    )
    .extend(cv.ENTITY_BASE_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    cg.add_define("USE_CAMERA")

    var = cg.new_Pvariable(config[CONF_ID])
    await setup_entity(var, config, "camera")
    await cg.register_component(var, config)

    cg.add(var.set_device(config[CONF_DEVICE]))
    cg.add(var.set_resolution(config[CONF_RESOLUTION]))
    cg.add(var.set_jpeg_quality(config[CONF_JPEG_QUALITY]))
    cg.add(var.set_max_framerate(config[CONF_MAX_FRAMERATE]))
