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

if(CONFIG_ESP_VIDEO_ENABLE_MIPI_CSI_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_csi_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_DVP_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_dvp_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_JPEG_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_jpeg_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_ISP)
    list(APPEND srcs "src/device/esp_video_isp_device.c")

    # CRITICAL FIX: Always compile ISP pipeline controller for JSON IPA loading
    # This is safe for all sensors - the code detects sensor type at runtime
    list(APPEND srcs "src/esp_video_isp_pipeline.c")
endif()

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS ${include_dirs}
                       PRIV_INCLUDE_DIRS ${priv_include_dirs}
                       PRIV_REQUIRES ${priv_requires}
                       REQUIRES ${requires})

# CRITICAL FIX: Always require esp_ipa for JSON IPA loading
# This is safe for all sensors - the code detects sensor type at runtime
idf_component_optional_requires(PRIVATE "esp_ipa")

# USB-UVC host support (enable_uvc: true).
# When UVC is enabled, esp_video_init.c includes "usb/usb_host.h" (from the core
# IDF "usb" component) and the UVC device driver includes "usb/uvc_host.h" /
# "esp_private/uvc_esp_video.h" (from the "espressif/usb_host_uvc" managed
# component pulled by __init__.py). Without these in REQUIRES the pure ESP-IDF
# build fails with "usb/usb_host.h: No such file or directory".
# optional_requires only adds them when the components are actually present, so
# MIPI-only builds (UVC off, usb_host_uvc not pulled) are unaffected.
idf_component_optional_requires(PRIVATE "usb" "usb_host_uvc")

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


