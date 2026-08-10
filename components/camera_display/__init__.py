import esphome.codegen as cg
from esphome.components import display
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_X, CONF_Y

DEPENDENCIES = ["esp_cam_sensor", "display"]

CONF_CAMERA_ID = "camera_id"
CONF_DISPLAY_ID = "display_id"

camera_display_ns = cg.esphome_ns.namespace("camera_display")
CameraDisplay = camera_display_ns.class_("CameraDisplay", cg.Component)

esp_cam_sensor_ns = cg.esphome_ns.namespace("esp_cam_sensor")
EspCamSensor = esp_cam_sensor_ns.class_("MipiDSICamComponent", cg.Component)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(CameraDisplay),
        cv.Required(CONF_CAMERA_ID): cv.use_id(EspCamSensor),
        cv.Required(CONF_DISPLAY_ID): cv.use_id(display.Display),
        cv.Optional(CONF_X, default=0): cv.int_range(min=0),
        cv.Optional(CONF_Y, default=0): cv.int_range(min=0),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    camera = await cg.get_variable(config[CONF_CAMERA_ID])
    cg.add(var.set_camera(camera))

    disp = await cg.get_variable(config[CONF_DISPLAY_ID])
    cg.add(var.set_display(disp))

    cg.add(var.set_position(config[CONF_X], config[CONF_Y]))
