set(srcs "src/esp_video_buffer.c"
         "src/esp_video_init.c"
         "src/esp_video_ioctl.c"
         "src/esp_video_mman.c"
         "src/esp_video_vfs.c"
         "src/esp_video.c"
         "src/esp_video_cam.c")

set(include_dirs "include")
set(priv_include_dirs "private_include")
set(priv_requires "vfs" "spi_flash" "esp_hw_support")
set(requires "esp_driver_cam" "esp_driver_isp" "esp_cam_sensor" "esp_driver_jpeg" "esp_sccb_intf")

# MIGRATION ESPHome 2026.7 (build ESP-IDF natif) : les CONFIG_ESP_VIDEO_ENABLE_*
# sont posés en -D par __init__.py (donc actifs dans le CODE) mais pas dans le
# sdkconfig lu par CMake, donc les if(CONFIG_...) seraient faux. On compile donc ces
# sources inconditionnellement — comportement identique à l'ancien esp_video_build.py
# (script PlatformIO, qui ne tourne plus en IDF natif et compilait tout).
list(APPEND srcs
    "src/device/esp_video_csi_device.c"
    "src/device/esp_video_jpeg_device.c"
    "src/device/esp_video_isp_device.c"
    "src/device/esp_video_usb_uvc_device.c"   # corps gardé par #if CONFIG_..._USB_UVC...
    "src/esp_video_isp_pipeline.c"
    "src/esp_video_isp_stubs.c"
    "src/embedded_ov5647_ipa_config_json.c"
    "src/embedded_ov02c10_ipa_config_json.c"
    "src/embedded_sc202cs_ipa_config_json.c"
    "src/embedded_sc2336_ipa_config_json.c")

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS ${include_dirs}
                       PRIV_INCLUDE_DIRS ${priv_include_dirs}
                       PRIV_REQUIRES ${priv_requires}
                       REQUIRES ${requires})

# CRITICAL FIX: Always require esp_ipa for JSON IPA loading
# This is safe for all sensors - the code detects sensor type at runtime
# (ESPHome 2026.7 natif: cmake_utilities indisponible -> liaison directe au lieu
#  de idf_component_optional_requires)
target_link_libraries(${COMPONENT_LIB} PRIVATE idf::esp_ipa)

# Disable the MIPI-CSI driver "backup buffer" allocation.
# The CSI driver normally allocates a full-frame DMA-capable buffer as a
# fallback when no user buffer is queued in time. For larger resolutions
# (e.g. 1920x1080 RGB565 = ~4MB) this alloc fails because the suitable
# heap region is too small or fragmented, and VIDIOC_STREAMON returns
# ESP_ERR_NO_MEM ("no mem for backup buffer"). V4L2 USERPTR with two
# pre-allocated SPIRAM buffers already covers double-buffering, so the
# backup is redundant. Disabling it unblocks 1920x1080 and saves one
# frame worth of internal RAM on every resolution.
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    CONFIG_ESP_VIDEO_DISABLE_MIPI_CSI_DRIVER_BACKUP_BUFFER=1
)


