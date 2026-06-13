"""
Composant ESPHome pour ESP-Video d'Espressif (v1.4.0)
Support JPEG avec dépendances ESP-IDF

Ce composant initialise ESP-Video en utilisant le bus I2C d'ESPHome.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import i2c, esp32
from esphome.const import CONF_ID, CONF_I2C_ID
from esphome.core import CORE
import os
import logging

CODEOWNERS = ["@youkorr"]
DEPENDENCIES = ["esp32", "i2c"]  # i2c ajouté car nous utilisons le bus I2C ESPHome
AUTO_LOAD = []

esp_video_ns = cg.esphome_ns.namespace("esp_video")
ESPVideoComponent = esp_video_ns.class_("ESPVideoComponent", cg.Component)

# Configuration
CONF_ENABLE_JPEG = "enable_jpeg"
CONF_ENABLE_ISP = "enable_isp"
CONF_ENABLE_UVC = "enable_uvc"
CONF_USE_HEAP_ALLOCATOR = "use_heap_allocator"
CONF_XCLK_PIN = "xclk_pin"
CONF_XCLK_FREQ = "xclk_freq"
CONF_ENABLE_XCLK_INIT = "enable_xclk_init"

# Constante pour indiquer qu'il n'y a pas d'horloge externe contrôlée par GPIO
# Utilisez xclk_pin: -1 pour les cartes avec oscillateur externe sur le PCB
NO_CLOCK = -1

def parse_gpio_pin(value):
    """Parse une pin GPIO au format ESPHome (GPIO36 ou -1)"""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value == "-1" or value.upper() == "NO_CLOCK":
            return NO_CLOCK
        # Format "GPIO36" -> 36
        if value.upper().startswith("GPIO"):
            try:
                return int(value[4:])
            except ValueError:
                raise cv.Invalid(f"Format GPIO invalide: {value}. Utilisez 'GPIO36' ou -1")
        # Si c'est juste un nombre en string
        try:
            return int(value)
        except ValueError:
            raise cv.Invalid(f"Format GPIO invalide: {value}. Utilisez 'GPIO36' ou -1")
    raise cv.Invalid(f"Type de pin invalide: {type(value)}")

def validate_esp_video_config(config):
    """Valide la configuration ESP-Video"""
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema({
        cv.GenerateID(): cv.declare_id(ESPVideoComponent),
        cv.Required(CONF_I2C_ID): cv.use_id(i2c.I2CBus),
        cv.Optional(CONF_ENABLE_JPEG, default=True): cv.boolean,
        cv.Optional(CONF_ENABLE_ISP, default=True): cv.boolean,
        # USB-UVC host: enables plugging an external USB (UVC) camera into the
        # ESP32-P4 USB OTG port. Off by default (MIPI-CSI only). When true, the
        # USB host stack + UVC host driver are compiled in and started, and a
        # connected UVC camera is enumerated as a /dev/videoN V4L2 device.
        cv.Optional(CONF_ENABLE_UVC, default=False): cv.boolean,
        cv.Optional(CONF_USE_HEAP_ALLOCATOR, default=True): cv.boolean,
        # XCLK pin accepte: "GPIO36", 36, -1, ou "NO_CLOCK"
        cv.Optional(CONF_XCLK_PIN, default="GPIO36"): cv.Any(cv.string, cv.int_range(min=-1, max=48)),
        cv.Optional(CONF_XCLK_FREQ, default=24000000): cv.int_range(min=1000000, max=40000000),  # 1-40 MHz
        # Enable XCLK initialization via LEDC (for non-M5Stack boards)
        cv.Optional(CONF_ENABLE_XCLK_INIT, default=False): cv.boolean,
    }).extend(cv.COMPONENT_SCHEMA),
    validate_esp_video_config
)


async def to_code(config):
    # -----------------------------------------------------------------------
    # Vérification du framework (CRITICAL: faire ça en premier)
    # -----------------------------------------------------------------------
    if not CORE.using_esp_idf:
        raise cv.Invalid(
            "ESP-Video nécessite le framework esp-idf. "
            "Ajoutez 'framework: type: esp-idf' dans votre configuration."
        )

    # ============================================================================
    # AUTO-DOWNLOAD DES DÉPENDANCES (comme LVGL 9.4 avec cg.add_library)
    # ============================================================================
    # LVGL fait: cg.add_library("lvgl/lvgl", "9.4.0")
    # ESP-Video fait: Auto-download depuis esp-adf-libs

    component_dir = os.path.dirname(__file__)
    parent_components_dir = os.path.dirname(component_dir)

    # Importer le module de téléchargement
    from .esp_video_download import ensure_esp_video_dependencies

    # Télécharger automatiquement les dépendances manquantes
    # (Si les composants existent déjà, cette fonction ne fait rien)
    try:
        ensure_esp_video_dependencies(parent_components_dir)
    except Exception as e:
        logging.warning(
            f"[ESP-Video] Auto-download failed: {e}\n"
            f"If components are already present locally, this is OK."
        )

    # ============================================================================
    # Configuration du composant
    # ============================================================================

    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    # Récupérer le bus I2C d'ESPHome
    i2c_bus = await cg.get_variable(config[CONF_I2C_ID])
    cg.add(var.set_i2c_bus(i2c_bus))

    # Configure XCLK pour la détection des capteurs MIPI-CSI
    # CRITICAL: Les capteurs ont besoin de XCLK actif pour répondre sur I2C!
    xclk_pin_raw = config[CONF_XCLK_PIN]
    xclk_pin = parse_gpio_pin(xclk_pin_raw)  # Convertit "GPIO36" -> 36, ou "-1" -> -1
    xclk_freq = config[CONF_XCLK_FREQ]
    has_ext_clock = xclk_pin != NO_CLOCK

    # Cast explicite en gpio_num_t pour éviter l'erreur de compilation
    cg.add(var.set_xclk_pin(cg.RawExpression(f"static_cast<gpio_num_t>({xclk_pin})")))
    cg.add(var.set_xclk_freq(xclk_freq))
    cg.add(var.set_enable_xclk_init(config[CONF_ENABLE_XCLK_INIT]))
    cg.add(var.set_enable_uvc(config[CONF_ENABLE_UVC]))

    # USB-UVC host: pull Espressif's USB Host UVC driver (native 2.x API, P4
    # support) which also provides the esp_private/uvc_esp_video.h glue the
    # esp_video UVC device driver builds against. Only when enabled, so MIPI-only
    # builds don't grow the USB host stack.
    if config[CONF_ENABLE_UVC]:
        esp32.add_idf_component(name="espressif/usb_host_uvc", ref="2.4.1")

    # Logs silencieux sauf erreurs
    logging.debug(f"[ESP-Video] I2C bus: '{config[CONF_I2C_ID]}'")
    if has_ext_clock:
        logging.debug(f"[ESP-Video] XCLK: GPIO{xclk_pin} @ {xclk_freq/1000000:.1f} MHz")
    else:
        logging.debug(f"[ESP-Video] XCLK: PCB oscillator @ {xclk_freq/1000000:.1f} MHz")

    # -----------------------------------------------------------------------
    # Chemins des composants ESP-IDF
    # -----------------------------------------------------------------------
    # Chemin du composant esp_video
    component_dir = os.path.dirname(__file__)
    parent_components_dir = os.path.dirname(component_dir)

    # -----------------------------------------------------------------------
    # Ajout des répertoires include
    # -----------------------------------------------------------------------
    includes_found = False

    # esp_video
    esp_video_includes = ["include", "private_include", "src"]
    for inc in esp_video_includes:
        inc_path = os.path.join(component_dir, inc)
        if os.path.exists(inc_path):
            cg.add_build_flag(f"-I{inc_path}")
            includes_found = True

    # esp_cam_sensor
    esp_cam_sensor_dir = os.path.join(parent_components_dir, "esp_cam_sensor")
    if os.path.exists(esp_cam_sensor_dir):
        for inc in ["include", "sensor/ov5647/include", "sensor/sc202cs/include", "sensor/ov02c10/include", "src", "src/driver_spi", "src/driver_cam"]:
            inc_path = os.path.join(esp_cam_sensor_dir, inc)
            if os.path.exists(inc_path):
                cg.add_build_flag(f"-I{inc_path}")
                includes_found = True

    # esp_ipa
    esp_ipa_dir = os.path.join(parent_components_dir, "esp_ipa")
    if os.path.exists(esp_ipa_dir):
        for inc in ["include", "src"]:
            inc_path = os.path.join(esp_ipa_dir, inc)
            if os.path.exists(inc_path):
                cg.add_build_flag(f"-I{inc_path}")
                includes_found = True

    # esp_sccb_intf
    esp_sccb_intf_dir = os.path.join(parent_components_dir, "esp_sccb_intf")
    if os.path.exists(esp_sccb_intf_dir):
        for inc in ["include", "interface", "sccb_i2c/include"]:
            inc_path = os.path.join(esp_sccb_intf_dir, inc)
            if os.path.exists(inc_path):
                cg.add_build_flag(f"-I{inc_path}")
                includes_found = True

    if not includes_found:
        logging.warning(
            "[ESP-Video] No include directories found! "
            "Check ESP-Video component structure."
        )

    # -----------------------------------------------------------------------
    # FLAGS ESP-Video selon la configuration
    # -----------------------------------------------------------------------
    flags = []

    # Flags de base (toujours activés)
    flags.extend([
        "-DCONFIG_ESP_VIDEO_ENABLE_MIPI_CSI_VIDEO_DEVICE=1",
        "-DCONFIG_IDF_TARGET_ESP32P4=1",
        "-DCONFIG_SOC_I2C_SUPPORTED=1",
    ])

    # Capteurs de caméra - configurations pour TOUS les capteurs supportés
    # L'auto-détection essaiera tous les capteurs et utilisera celui détecté

    # SC202CS - Configuration identique à M5Stack Tab5
    # Digital gain priority recommandé pour éviter le bruit à faible lumière
    flags.extend([
        "-DCONFIG_CAMERA_SC202CS=1",
        "-DCONFIG_CAMERA_SC202CS_AUTO_DETECT=1",
        "-DCONFIG_CAMERA_SC202CS_AUTO_DETECT_MIPI_INTERFACE_SENSOR=1",
        "-DCONFIG_CAMERA_SC202CS_ABSOLUTE_GAIN_LIMIT=63008",  # M5Stack value
        "-DCONFIG_CAMERA_SC202CS_ANA_GAIN_PRIORITY=0",        # Disabled (M5Stack)
        "-DCONFIG_CAMERA_SC202CS_DIG_GAIN_PRIORITY=1",        # Enabled (M5Stack)
        "-DCONFIG_CAMERA_SC202CS_MAX_SUPPORT=1",
    ])

    # OV5647
    flags.extend([
        "-DCONFIG_CAMERA_OV5647=1",
        "-DCONFIG_CAMERA_OV5647_AUTO_DETECT=1",
        "-DCONFIG_CAMERA_OV5647_AUTO_DETECT_MIPI_INTERFACE_SENSOR=1",
        "-DCONFIG_CAMERA_OV5647_CSI_LINESYNC_ENABLE=0",
        "-DCONFIG_CAMERA_OV5647_MIPI_IF_FORMAT_INDEX_DEFAULT=0",
        "-DCONFIG_CAMERA_OV5647_DEFAULT_IPA_JSON_CONFIGURATION_FILE=0",  # Disabled: CCM in JSON causes red tint (matrix amplifies red 2.0x)
    ])

    # OV02C10
    flags.extend([
        "-DCONFIG_CAMERA_OV02C10=1",
        "-DCONFIG_CAMERA_OV02C10_AUTO_DETECT=1",
        "-DCONFIG_CAMERA_OV02C10_AUTO_DETECT_MIPI_INTERFACE_SENSOR=1",
        "-DCONFIG_CAMERA_OV02C10_ABSOLUTE_GAIN_LIMIT=16000",  # 16x max
        "-DCONFIG_CAMERA_OV02C10_ANA_GAIN_PRIORITY=1",         # Analog gain priority
        "-DCONFIG_CAMERA_OV02C10_DIG_GAIN_PRIORITY=0",
        "-DCONFIG_CAMERA_OV02C10_CSI_LINESYNC_ENABLE=0",
        "-DCONFIG_CAMERA_OV02C10_MIPI_IF_FORMAT_INDEX_DEFAULT=0",
        "-DCONFIG_CAMERA_OV02C10_MAX_SUPPORT=1",
        "-DCONFIG_CAMERA_OV02C10_DEFAULT_IPA_JSON_CONFIGURATION_FILE=1",  # Utiliser cfg/ov02c10_default.json
    ])

    # ISP (Image Signal Processor)
    if config[CONF_ENABLE_ISP]:
        flags.extend([
            "-DCONFIG_ESP_VIDEO_ENABLE_ISP=1",
            "-DCONFIG_ESP_VIDEO_ENABLE_ISP_VIDEO_DEVICE=1",
            "-DCONFIG_ESP_VIDEO_ENABLE_ISP_PIPELINE_CONTROLLER=1",
            "-DESP_VIDEO_ISP_ENABLED=1",  # Pour esp_video_component.cpp
        ])

    # USB-UVC host video device (external USB camera on the P4 USB OTG port)
    if config[CONF_ENABLE_UVC]:
        flags.append("-DCONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE=1")

    # Allocateur mémoire
    if config[CONF_USE_HEAP_ALLOCATOR]:
        flags.append("-DCONFIG_ESP_VIDEO_USE_HEAP_ALLOCATOR=1")

    # Encodeur JPEG
    if config[CONF_ENABLE_JPEG]:
        flags.extend([
            "-DCONFIG_ESP_VIDEO_ENABLE_JPEG_VIDEO_DEVICE=1",
            "-DCONFIG_ESP_VIDEO_ENABLE_HW_JPEG_VIDEO_DEVICE=1",
            "-DESP_VIDEO_JPEG_ENABLED=1",  # Pour esp_video_component.cpp
        ])

    # Appliquer tous les flags
    for flag in flags:
        cg.add_build_flag(flag)

    extra_flags = [
        "-Wno-unused-function",
        "-Wno-unused-variable",
        "-Wno-missing-field-initializers",
    ]

    for flag in extra_flags:
        cg.add_build_flag(flag)

    # -----------------------------------------------------------------------
    # Script de build PlatformIO (obligatoire)
    # -----------------------------------------------------------------------
    build_script_path = os.path.join(component_dir, "esp_video_build.py")
    if os.path.exists(build_script_path):
        cg.add_platformio_option("extra_scripts", [f"post:{build_script_path}"])
    else:
        raise cv.Invalid(f"Script de build introuvable: {build_script_path}")

















