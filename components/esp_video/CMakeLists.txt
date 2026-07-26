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
    "src/embedded_sc2336_ipa_config_json.c"
    "src/embedded_sc2356_ipa_config_json.c")

# Hardware H.264 encoder video device (/dev/video11, ESP32-P4 only).
# `esp_video_h264_device.c` includes the esp_h264 headers unconditionally, so it
# can only be compiled when that managed component is present. The ESPHome
# component signals the choice by writing this marker file (enable_h264: true),
# which is the one channel CMake can read: the -D flags it also poses are not
# part of sdkconfig, so if(CONFIG_...) would always be false here.
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/.enable_h264")
    list(APPEND srcs "src/device/esp_video_h264_device.c")
    list(APPEND requires "espressif__esp_h264")
    set(ESP_VIDEO_H264_ENABLED TRUE)
endif()

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

# Same values as the -D flags posed by __init__.py, so the definitions agree
# whichever path the build takes (native ESP-IDF or the legacy SCons script).
if(ESP_VIDEO_H264_ENABLED)
    target_compile_definitions(${COMPONENT_LIB} PUBLIC
        CONFIG_ESP_VIDEO_ENABLE_H264_VIDEO_DEVICE=1
        CONFIG_ESP_VIDEO_ENABLE_HW_H264_VIDEO_DEVICE=1
    )
endif()


