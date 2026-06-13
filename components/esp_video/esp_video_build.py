"""
Script de build PlatformIO pour ESP-Video
Ajoute tous les fichiers sources C/C++ des composants ESP-IDF
"""

import os
Import("env")

# Force enable ISP Pipeline Controller
# This define ensures ISP pipeline code is included in the build
print("[ESP-Video Build] ========================================")
print("[ESP-Video Build] Adding ISP Pipeline Controller define")
env.Append(CPPDEFINES=[
    ("CONFIG_ESP_VIDEO_ENABLE_ISP_PIPELINE_CONTROLLER", "1"),
    # SC2336 sensor configuration (must mirror CMakeLists.txt because the
    # PlatformIO build bypasses CMake for these sources)
    ("CONFIG_CAMERA_SC2336", "1"),
    ("CONFIG_CAMERA_SC2336_AUTO_DETECT", "1"),
    ("CONFIG_CAMERA_SC2336_AUTO_DETECT_MIPI_INTERFACE_SENSOR", "1"),
    ("CONFIG_CAMERA_SC2336_MIPI_IF_FORMAT_INDEX_DEFAULT", "0"),
    ("CONFIG_CAMERA_SC2336_DVP_IF_FORMAT_INDEX_DEFAULT", "0"),
    ("CONFIG_CAMERA_SC2336_CSI_LINESYNC_ENABLE", "0"),
    ("CONFIG_CAMERA_SC2336_ABSOLUTE_GAIN_LIMIT", "63008"),
    ("CONFIG_CAMERA_SC2336_ANA_GAIN_PRIORITY", "1"),
    ("CONFIG_CAMERA_SC2336_DIG_GAIN_PRIORITY", "0"),
    ("CONFIG_CAMERA_SC2336_MAX_SUPPORT", "1"),
    ("CONFIG_CAMERA_SC2336_MIPI_RAW8_800X800_30FPS", "1"),
    ("CONFIG_CAMERA_SC2336_MIPI_RAW10_800X800_30FPS", "1"),
    ("CONFIG_CAMERA_SC2336_MIPI_RAW8_1024X600_30FPS", "1"),
    ("CONFIG_CAMERA_SC2336_MIPI_RAW10_1280X720_30FPS", "1"),
    ("CONFIG_CAMERA_SC2336_MIPI_RAW10_1920X1080_30FPS", "1"),
    ("CONFIG_CAMERA_SC2336_MIPI_RAW10_640X480_50FPS", "1"),
])
print("[ESP-Video Build]   - CONFIG_ESP_VIDEO_ENABLE_ISP_PIPELINE_CONTROLLER=1")
print("[ESP-Video Build]   - CONFIG_CAMERA_SC2336_*=1 (driver enabled)")
print("[ESP-Video Build] ========================================")

# Obtenir le répertoire du composant (ce script est dans components/esp_video/)
# Dans SCons, __file__ n'existe pas, on utilise Dir('.').srcnode().abspath
script_dir = Dir('.').srcnode().abspath
component_dir = script_dir
parent_components_dir = os.path.dirname(component_dir)

print(f"[ESP-Video Build] Répertoire composant: {component_dir}")
print(f"[ESP-Video Build] Répertoire parent: {parent_components_dir}")

# Liste de tous les fichiers sources à compiler
sources_to_add = []

# ========================================================================
# Sources ESP-Video
# ========================================================================
esp_video_sources = [
    "src/esp_video_buffer.c",
    "src/esp_video_init.c",
    "src/esp_video_ioctl.c",
    "src/esp_video_mman.c",
    "src/esp_video_vfs.c",
    "src/esp_video.c",
    "src/esp_video_cam.c",
    "src/device/esp_video_csi_device.c",
    "src/device/esp_video_jpeg_device.c",
    "src/device/esp_video_isp_device.c",
    # USB-UVC host device driver. The file is wholly #if-guarded on
    # CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE, so listing it here is safe
    # for MIPI-only builds (it compiles to an empty unit) and active only when
    # enable_uvc: true is set in the ESPHome config.
    "src/device/esp_video_usb_uvc_device.c",
    "src/esp_video_isp_pipeline.c",
    "src/esp_video_isp_stubs.c",
]

# Ajouter le chemin d'include private_include de esp_video
esp_video_private_include = os.path.join(component_dir, "private_include")
if os.path.exists(esp_video_private_include):
    env.Append(CPPPATH=[esp_video_private_include])
    print(f"[ESP-Video Build] Include privé ajouté: {esp_video_private_include}")

for src in esp_video_sources:
    src_path = os.path.join(component_dir, src)
    if os.path.exists(src_path):
        sources_to_add.append(src_path)
        # print(f"[ESP-Video Build] + {src}")

# ========================================================================
# Sources esp_cam_sensor
# ========================================================================
esp_cam_sensor_dir = os.path.join(parent_components_dir, "esp_cam_sensor")
esp_cam_sensor_sources = [
    "src/esp_cam_sensor.c",
    "src/esp_cam_motor.c",
    "src/esp_cam_sensor_xclk.c",
    "src/esp_cam_sensor_detect_stubs.c",  # Linker symbols for sensor auto-detection
    "src/driver_spi/spi_slave.c",
    "src/driver_cam/esp_cam_ctlr_spi_cam.c",
    "sensor/ov5647/ov5647.c",
    "sensor/sc202cs/sc202cs.c",
    "sensor/sc2336/sc2336.c",
    "sensor/ov02c10/ov02c10.c",
]

# Ajouter les chemins d'include pour les sensors (private_include + public include)
esp_cam_sensor_includes = [
    "sensor/ov5647/include",
    "sensor/ov5647/private_include",
    "sensor/sc202cs/include",
    "sensor/sc202cs/include/private_include",
    "sensor/sc2336/include",
    "sensor/sc2336/include/private_include",
    "sensor/ov02c10/include",
    "sensor/ov02c10/private_include",
]

if os.path.exists(esp_cam_sensor_dir):
    # Ajouter les chemins d'include
    for inc in esp_cam_sensor_includes:
        inc_path = os.path.join(esp_cam_sensor_dir, inc)
        if os.path.exists(inc_path):
            env.Append(CPPPATH=[inc_path])
            print(f"[ESP-Video Build] Include sensor ajouté: {inc_path}")

    # Compiler les sources
    for src in esp_cam_sensor_sources:
        src_path = os.path.join(esp_cam_sensor_dir, src)
        if os.path.exists(src_path):
            sources_to_add.append(src_path)
            print(f"[ESP-Video Build] + esp_cam_sensor/{src}")

# ========================================================================
# Sources esp_ipa (IMPORTANT: compiler AVANT de linker avec libesp_ipa.a)
# ========================================================================
esp_ipa_dir = os.path.join(parent_components_dir, "esp_ipa")
esp_ipa_sources = [
    "src/version.c",              # Config IPA custom (5 IPAs: AWB, denoise, sharpen, gamma, CC - PAS AGC)
    "src/esp_ipa_detect_stubs.c", # Detection array
    "src/esp_ipa_json_loader.c",  # JSON IPA parser pour charger configs OV02C10/OV5647/SC202CS
]

if os.path.exists(esp_ipa_dir):
    for src in esp_ipa_sources:
        src_path = os.path.join(esp_ipa_dir, src)
        if os.path.exists(src_path):
            sources_to_add.append(src_path)
            print(f"[ESP-Video Build] esp_ipa/{src} -> libesp_video_full.a")
else:
    pass

# ========================================================================
# Sources esp_sccb_intf
# ========================================================================
esp_sccb_intf_dir = os.path.join(parent_components_dir, "esp_sccb_intf")
esp_sccb_intf_sources = [
    "src/sccb.c",
    "sccb_i2c/src/sccb_i2c.c",
]

if os.path.exists(esp_sccb_intf_dir):
    for src in esp_sccb_intf_sources:
        src_path = os.path.join(esp_sccb_intf_dir, src)
        if os.path.exists(src_path):
            sources_to_add.append(src_path)
            print(f"[ESP-Video Build] + esp_sccb_intf/{src}")

# ========================================================================
# Sources esp_audio_codec - REMOVED (not working)
# ========================================================================
# Audio codec has been removed because it does not work properly

# ========================================================================
# Embarquer les fichiers JSON IPA des capteurs comme binary data
# ========================================================================

# Liste des fichiers JSON à embarquer
json_files_to_embed = [
    {
        "path": os.path.join(esp_cam_sensor_dir, "sensor/ov5647/cfg/ov5647_default.json"),
        "symbol": "ov5647_ipa_config_json",
    },
    {
        "path": os.path.join(esp_cam_sensor_dir, "sensor/ov02c10/cfg/ov02c10_default.json"),
        "symbol": "ov02c10_ipa_config_json",
    },
    {
        # Required for the M5Stack Tab5 (SC202CS sensor). Without this entry
        # the IPA loader falls into "Unknown sensor: SC202CS", the ISP
        # starts without any tuning (no CCM / AWB / gamma), the colors
        # come out washed-out and YOLO11 / face-detect see nothing.
        "path": os.path.join(esp_cam_sensor_dir, "sensor/sc202cs/cfg/sc202cs_default.json"),
        "symbol": "sc202cs_ipa_config_json",
    },
    {
        # ESP32-P4 eco4 (rev1.3) — matches current hardware of users reporting SC2336 issues
        "path": os.path.join(esp_cam_sensor_dir, "sensor/sc2336/cfg/sc2336_default_p4_eco4.json"),
        "symbol": "sc2336_ipa_config_json",
    },
]

# Embarquer chaque fichier JSON comme binary data
embedded_json_objects = []
for json_info in json_files_to_embed:
    json_path = json_info["path"]
    symbol_name = json_info["symbol"]

    if os.path.exists(json_path):
        # Créer un nom de fichier objet pour ce JSON
        json_basename = os.path.basename(json_path).replace(".", "_")
        obj_filename = f"embedded_{json_basename}.o"
        obj_path = os.path.join("$BUILD_DIR", obj_filename)

        # Utiliser objcopy pour créer un fichier objet depuis le JSON
        # Les symbols générés seront: _binary_<name>_start, _binary_<name>_end, _binary_<name>_size
        objcopy_cmd = f"xtensa-esp32s3-elf-objcopy --input-target binary --output-target elf32-xtensa-le --binary-architecture xtensa {json_path} {obj_path}"

        # Note: PlatformIO/SCons n'a pas objcopy par défaut, donc on va utiliser une approche différente
        # On va créer un fichier C qui contient le JSON comme string
        c_wrapper_content = f'''/* Auto-generated wrapper for {os.path.basename(json_path)} */
#include <stddef.h>

const char {symbol_name}_start[] __attribute__((aligned(4))) =
'''

        # Lire le contenu du JSON et le convertir en string C
        try:
            with open(json_path, 'r') as f:
                json_content = f.read()
                # Échapper les caractères spéciaux pour le string C
                json_content_escaped = json_content.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                c_wrapper_content += f'    "{json_content_escaped}";\n\n'
                c_wrapper_content += f'const char *{symbol_name}_end = {symbol_name}_start + sizeof({symbol_name}_start);\n'
                c_wrapper_content += f'const size_t {symbol_name}_size = sizeof({symbol_name}_start);\n'

            # Créer le fichier C wrapper
            wrapper_filename = f"embedded_{symbol_name}.c"
            wrapper_path = os.path.join(component_dir, "src", wrapper_filename)

            # Écrire le fichier wrapper
            with open(wrapper_path, 'w') as f:
                f.write(c_wrapper_content)

            # Ajouter ce wrapper aux sources à compiler
            sources_to_add.append(wrapper_path)
            print(f"[ESP-Video Build] JSON embarqué: {os.path.basename(json_path)} -> {symbol_name}")

        except Exception as e:
            print(f"[ESP-Video Build]  Erreur lors de l'embedding de {json_path}: {e}")
    else:
        print(f"[ESP-Video Build]  Fichier JSON introuvable: {json_path}")

# ========================================================================
# Forcer la recompilation en modifiant le timestamp ET supprimant les .o
# ========================================================================
import time as time_module
import glob

# Fichiers critiques qui doivent être recompilés (problème de cache SCons)
force_rebuild_files = [
    os.path.join(component_dir, "src/esp_video_init.c"),
    os.path.join(esp_cam_sensor_dir, "src/esp_cam_sensor_detect_stubs.c"),
]

print("[ESP-Video Build] ========================================")
print("[ESP-Video Build] === FORCED REBUILD OF CRITICAL FILES ===")

# Étape 1: Supprimer tous les .o correspondants PARTOUT
build_root = env.subst("$PROJECT_BUILD_DIR")
for src_file in force_rebuild_files:
    basename = os.path.basename(src_file).replace('.c', '.o')
    # Chercher récursivement dans tout le projet
    obj_pattern = os.path.join(build_root, "**", basename)
    found_objs = glob.glob(obj_pattern, recursive=True)
    for obj_file in found_objs:
        try:
            os.remove(obj_file)
            print(f"[ESP-Video Build] DELETED: {obj_file}")
        except Exception as e:
            print(f"[ESP-Video Build] Could not delete {obj_file}: {e}")

# Étape 2: Modifier les timestamps des sources
for src_file in force_rebuild_files:
    if os.path.exists(src_file):
        # Modifier le timestamp du fichier pour forcer SCons à le recompiler
        current_time = time_module.time()
        os.utime(src_file, (current_time, current_time))
        print(f"[ESP-Video Build] FORCED REBUILD: {os.path.basename(src_file)}")
        print(f"[ESP-Video Build]    Updated timestamp to force recompilation")
    else:
        print(f"[ESP-Video Build] File not found: {src_file}")

print("[ESP-Video Build] ========================================")

# ========================================================================
# Ajouter toutes les sources à la compilation
# ========================================================================
if sources_to_add:
    # Compiler chaque fichier source en objet
    objects = []

    for src_file in sources_to_add:
        # Vérifier si c'est un fichier critique qui doit être forcé à recompiler
        is_critical = any(
            src_file.endswith(os.path.basename(critical_file))
            for critical_file in force_rebuild_files
        )

        # Compiler le fichier source en .o
        obj = env.Object(src_file)

        # Pour les fichiers critiques, forcer SCons à toujours les recompiler
        if is_critical:
            # AlwaysBuild: Force SCons to rebuild this file every time
            env.AlwaysBuild(obj)
            # NoCache: Don't use cached version of this object file
            env.NoCache(obj)
            print(f"[ESP-Video Build] ========================================")
            print(f"[ESP-Video Build] ALWAYS BUILD (NO CACHE): {os.path.basename(src_file)}")
            print(f"[ESP-Video Build] This file will be recompiled with ISP defines")
            print(f"[ESP-Video Build] ========================================")

        objects.extend(obj)

    # Créer une bibliothèque statique avec les objets compilés
    lib = env.StaticLibrary(
        os.path.join("$BUILD_DIR", "libesp_video_full"),
        objects
    )

    # Ajouter la bibliothèque au linkage (PREPEND = avant les autres libs)
    env.Prepend(LIBS=[lib])

    # Maintenant linker avec libesp_ipa.a pour les fonctions IPA internes
    # Le linker utilisera notre version.o de libesp_video_full.a (déjà Prepend ci-dessus)
    # avant de chercher dans libesp_ipa.a
    esp_ipa_lib_dir = os.path.join(parent_components_dir, "esp_ipa", "lib/esp32p4")
    if os.path.exists(esp_ipa_lib_dir):
        env.Append(LIBPATH=[esp_ipa_lib_dir])
        env.Append(LIBS=["esp_ipa"])
    else:
        pass
else:
    pass

# Message simple final
if sources_to_add:
    print("[ESP-Video Build] ========================================")
    print(f"[ESP-Video Build] {len(sources_to_add)} source files compiled")
    print("[ESP-Video Build] ISP Pipeline Controller: ENABLED")
    print("[ESP-Video Build] esp-video: ok")
    print("[ESP-Video Build] ========================================")
