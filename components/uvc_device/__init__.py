"""Present the ESP32-P4's MIPI-CSI camera to a USB host as a webcam.

This is Espressif's esp_video `uvc` example rebuilt as an ESPHome component:
the sensor feeds the hardware JPEG encoder, and usb_device_uvc streams the
result over USB. Plug the board into a PC and it shows up as an ordinary
webcam; plug it into another ESP32-P4 and it is a UVC camera for the
esp_video_camera `device: uvc` host path.

The board is the USB *device* here, so it must be cabled to the host's host
port. It cannot be a UVC host at the same time -- the ESP32-P4 has one USB OTG
controller and this component puts it in device mode.
"""

import esphome.codegen as cg
from esphome.components import esp32
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_RESOLUTION

CODEOWNERS = ["@youkorr"]
# esp_video owns esp_video_init(); this component only opens the devices it
# created, so it cannot work without it.
DEPENDENCIES = ["esp_video"]

uvc_device_ns = cg.esphome_ns.namespace("uvc_device")
UVCDevice = uvc_device_ns.class_("UVCDevice", cg.Component)

CONF_FRAMERATE = "framerate"
CONF_JPEG_QUALITY = "jpeg_quality"
CONF_TRANSFER_MODE = "transfer_mode"
CONF_USB_SPEED = "usb_speed"
CONF_MANUFACTURER = "manufacturer"
CONF_PRODUCT = "product"
CONF_VENDOR_ID = "vendor_id"
CONF_PRODUCT_ID = "product_id"
CONF_SERIAL = "serial"

# Isochronous is what a webcam normally uses and what every host supports.
# Bulk trades guaranteed bandwidth for higher throughput, and some hosts
# (including esp_video's own UVC host driver) get on better with it.
_TRANSFER_MODES = {
    "isochronous": "CONFIG_UVC_MODE_ISOC_CAM1",
    "bulk": "CONFIG_UVC_MODE_BULK_CAM1",
}

# The ESP32-P4 defaults to its High-Speed PHY, which is the only way to move a
# 720p MJPEG stream. Full-Speed is there for a board that wires the USB
# connector to the Full-Speed pins instead.
_USB_SPEEDS = {
    "high": "CONFIG_TINYUSB_RHPORT_HS",
    "full": "CONFIG_TINYUSB_RHPORT_FS",
}


def _validate_resolution(value):
    value = cv.string(value)
    parts = value.lower().split("x")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise cv.Invalid(
            f"resolution '{value}' is invalid. Use 'WIDTHxHEIGHT', e.g. '1280x720'."
        )
    width, height = (int(part) for part in parts)
    if width == 0 or height == 0:
        raise cv.Invalid("resolution: neither side can be zero.")
    return (width, height)


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(UVCDevice),
            # No default: this has to match the format the sensor was built for,
            # because a MIPI sensor cannot be resized at runtime. Announcing a
            # size the sensor does not produce gives the host a torn image.
            cv.Required(CONF_RESOLUTION): _validate_resolution,
            cv.Optional(CONF_FRAMERATE, default=15): cv.int_range(min=1, max=60),
            cv.Optional(CONF_JPEG_QUALITY, default=80): cv.int_range(min=1, max=100),
            cv.Optional(CONF_TRANSFER_MODE, default="isochronous"): cv.enum(
                _TRANSFER_MODES, lower=True
            ),
            cv.Optional(CONF_USB_SPEED, default="high"): cv.enum(
                _USB_SPEEDS, lower=True
            ),
            cv.Optional(CONF_MANUFACTURER, default="ESPHome"): cv.string_strict,
            cv.Optional(CONF_PRODUCT, default="ESP32-P4 Camera"): cv.string_strict,
            cv.Optional(CONF_VENDOR_ID, default=0x303A): cv.hex_int_range(
                min=0, max=0xFFFF
            ),
            cv.Optional(CONF_PRODUCT_ID, default=0x8000): cv.hex_int_range(
                min=0, max=0xFFFF
            ),
            cv.Optional(CONF_SERIAL, default="0001"): cv.string_strict,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    # MIPI-CSI, the hardware JPEG encoder and the High-Speed USB PHY are all
    # ESP32-P4 silicon.
    esp32.only_on_variant(supported=[esp32.VARIANT_ESP32P4]),
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    width, height = config[CONF_RESOLUTION]
    cg.add(var.set_resolution(width, height))
    cg.add(var.set_framerate(config[CONF_FRAMERATE]))
    cg.add(var.set_jpeg_quality(config[CONF_JPEG_QUALITY]))

    esp32.add_idf_component(name="espressif/usb_device_uvc", ref="1.3.*")

    # The USB descriptor is generated from Kconfig at build time, so the
    # resolution and frame rate the host sees are set here, not at runtime.
    esp32.add_idf_sdkconfig_option("CONFIG_FORMAT_MJPEG_CAM1", True)
    esp32.add_idf_sdkconfig_option("CONFIG_UVC_CAM1_FRAMESIZE_WIDTH", width)
    # "HEIGT" is upstream's spelling of the symbol, not a typo here.
    esp32.add_idf_sdkconfig_option("CONFIG_UVC_CAM1_FRAMESIZE_HEIGT", height)
    esp32.add_idf_sdkconfig_option("CONFIG_UVC_CAM1_FRAMERATE", config[CONF_FRAMERATE])
    # cv.enum hands back the key the user wrote, so map it to the symbol here.
    esp32.add_idf_sdkconfig_option(_TRANSFER_MODES[config[CONF_TRANSFER_MODE]], True)
    esp32.add_idf_sdkconfig_option(_USB_SPEEDS[config[CONF_USB_SPEED]], True)

    esp32.add_idf_sdkconfig_option("CONFIG_TUSB_VID", config[CONF_VENDOR_ID])
    esp32.add_idf_sdkconfig_option("CONFIG_TUSB_PID", config[CONF_PRODUCT_ID])
    esp32.add_idf_sdkconfig_option(
        "CONFIG_TUSB_MANUFACTURER", config[CONF_MANUFACTURER]
    )
    esp32.add_idf_sdkconfig_option("CONFIG_TUSB_PRODUCT", config[CONF_PRODUCT])
    esp32.add_idf_sdkconfig_option("CONFIG_TUSB_SERIAL_NUM", config[CONF_SERIAL])

    # The encoder this component feeds. esp_video only builds it in when asked.
    esp32.add_idf_sdkconfig_option(
        "CONFIG_ESP_VIDEO_ENABLE_JPEG_ENC_VIDEO_DEVICE", True
    )
    esp32.add_idf_sdkconfig_option(
        "CONFIG_ESP_VIDEO_ENABLE_HW_JPEG_ENC_VIDEO_DEVICE", True
    )
