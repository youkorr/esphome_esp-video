import esphome.codegen as cg
from esphome.components import display
import esphome.config_validation as cv
from esphome.const import CONF_DISPLAY_ID, CONF_ID, CONF_ROTATION
from esphome.core import CORE
import esphome.final_validate as fv

DEPENDENCIES = ["lvgl", "esp_cam_sensor"]
AUTO_LOAD = ["esp_cam_sensor"]

CONF_CAMERA_ID = "camera_id"
CONF_CANVAS_ID = "canvas_id"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_FACE_DETECTION_ID = "face_detection_id"
CONF_YOLO11_DETECTION_ID = "yolo11_detection_id"
CONF_PEDESTRIAN_DETECTION_ID = "pedestrian_detection_id"
CONF_DIRECT = "direct"

lvgl_camera_display_ns = cg.esphome_ns.namespace("lvgl_camera_display")
LVGLCameraDisplay = lvgl_camera_display_ns.class_("LVGLCameraDisplay", cg.Component)

esp_cam_sensor_ns = cg.esphome_ns.namespace("esp_cam_sensor")
EspCamSensor = esp_cam_sensor_ns.class_("MipiDSICamComponent", cg.Component)

face_detection_ns = cg.esphome_ns.namespace("face_detection")
FaceDetectionComponent = face_detection_ns.class_("FaceDetectionComponent", cg.Component)

yolo11_detection_ns = cg.esphome_ns.namespace("yolo11_detection")
YOLO11DetectionComponent = yolo11_detection_ns.class_("YOLO11DetectionComponent", cg.Component)

pedestrian_detection_ns = cg.esphome_ns.namespace("pedestrian_detection")
PedestrianDetectionComponent = pedestrian_detection_ns.class_("PedestrianDetectionComponent", cg.Component)

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(LVGLCameraDisplay),
    cv.Required(CONF_CAMERA_ID): cv.use_id(EspCamSensor),
    cv.Required(CONF_CANVAS_ID): cv.string,
    cv.Optional(CONF_UPDATE_INTERVAL, default="33ms"): cv.positive_time_period_milliseconds,
    cv.Optional(CONF_FACE_DETECTION_ID): cv.use_id(FaceDetectionComponent),
    cv.Optional(CONF_YOLO11_DETECTION_ID): cv.use_id(YOLO11DetectionComponent),
    cv.Optional(CONF_PEDESTRIAN_DETECTION_ID): cv.use_id(PedestrianDetectionComponent),
    # Direct mode: the PPA turns each frame once, straight onto the display,
    # instead of LVGL copying it into the canvas, redrawing the canvas and
    # turning the whole area again in its flush.
    cv.Optional(CONF_DIRECT, default=False): cv.boolean,
    # The display to draw on. Left out, it is the lvgl: block's first one.
    cv.Optional(CONF_DISPLAY_ID): cv.use_id(display.Display),
}).extend(cv.COMPONENT_SCHEMA)


def _lvgl_blocks(full_config):
    blocks = full_config.get("lvgl") or []
    return blocks if isinstance(blocks, list) else [blocks]


def _direct_display(full_config, config):
    """The display direct mode writes to: the one named, or lvgl's own."""
    if CONF_DISPLAY_ID in config:
        return config[CONF_DISPLAY_ID]
    for block in _lvgl_blocks(full_config):
        displays = block.get("displays") or []
        if displays:
            return displays[0]
    return None


def display_depth(full_config, display_id):
    """16 or 24, off the display's own color_depth: (mipi_dsi says "16" by
    default), so the PPA writes the frame in the depth the display copies
    without converting -- anything else is converted one pixel at a time."""
    wanted = getattr(display_id, "id", display_id)
    for block in full_config.get("display") or []:
        if getattr(block.get(CONF_ID), "id", None) != wanted:
            continue
        depth = str(block.get("color_depth", "16")).lower().removesuffix("bit")
        return 24 if depth == "24" else 16
    return 16


def _final_validate(config):
    if not config[CONF_DIRECT]:
        return config
    full = fv.full_config.get()
    blocks = _lvgl_blocks(full)
    if len(blocks) != 1:
        raise cv.Invalid(f"{CONF_DIRECT}: needs exactly one lvgl: block")
    if blocks[0].get("full_refresh"):
        raise cv.Invalid(
            f"{CONF_DIRECT}: does not work with lvgl: full_refresh: true -- every "
            "LVGL refresh then rewrites the whole screen, the camera's area "
            "included. Remove full_refresh, or leave direct: out"
        )
    if _direct_display(full, config) is None:
        raise cv.Invalid(f"{CONF_DIRECT}: no display found; give {CONF_DISPLAY_ID}:")
    return config


FINAL_VALIDATE_SCHEMA = _final_validate


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    camera = await cg.get_variable(config[CONF_CAMERA_ID])
    cg.add(var.set_camera(camera))

    cg.add(var.set_canvas_id(config[CONF_CANVAS_ID]))

    if config[CONF_DIRECT]:
        disp_id = _direct_display(CORE.config, config)
        disp = await cg.get_variable(disp_id)
        # Read off the lvgl: block rather than asked for again, so the two
        # cannot disagree. ESPHome's lvgl and youkorr/lvgl_9.5 both store
        # rotation: as its degrees, clockwise.
        rotation = int(_lvgl_blocks(CORE.config)[0].get(CONF_ROTATION, 0))
        depth = display_depth(CORE.config, disp_id)
        cg.add(var.set_direct(disp, rotation, depth))

    update_interval_ms = config[CONF_UPDATE_INTERVAL].total_milliseconds
    cg.add(var.set_update_interval(int(update_interval_ms)))

    if CONF_FACE_DETECTION_ID in config:
        cg.add_define("USE_FACE_DETECTION")
        face_detect = await cg.get_variable(config[CONF_FACE_DETECTION_ID])
        cg.add(var.set_face_detection(face_detect))

    if CONF_YOLO11_DETECTION_ID in config:
        cg.add_define("USE_YOLO11_DETECTION")
        yolo11_detect = await cg.get_variable(config[CONF_YOLO11_DETECTION_ID])
        cg.add(var.set_yolo11_detection(yolo11_detect))

    if CONF_PEDESTRIAN_DETECTION_ID in config:
        cg.add_define("USE_PEDESTRIAN_DETECTION")
        ped_detect = await cg.get_variable(config[CONF_PEDESTRIAN_DETECTION_ID])
        cg.add(var.set_pedestrian_detection(ped_detect))
