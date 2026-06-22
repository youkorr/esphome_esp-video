set(srcs "src/esp_video_buffer.c"
         "src/esp_video_init.c"
         "src/esp_video_ioctl.c"
         "src/esp_video_mman.c"
         "src/esp_video_vfs.c"
         "src/esp_video.c"
         "src/esp_video_cam.c"
         "src/device/esp_video_device_common.c"
         # [YOUKORR/ESPHome] shim de compatibilité migration 2.2.0 (voir MIGRATION_2.2.0.md)
         "src/esp_video_youkorr_compat.c")

set(include_dirs "include")
set(priv_include_dirs "private_include")
set(priv_requires "vfs")
set(requires "esp_driver_cam" "esp_cam_sensor" "lwip")

if(CONFIG_IDF_TARGET_ESP32P4)
    if(CONFIG_ESP_VIDEO_ENABLE_SWAP_SHORT)
        list(APPEND srcs "src/data_reprocessing/esp32p4/esp_video_swap_short.c")
        list(APPEND srcs "src/data_reprocessing/esp32p4/esp_video_swap_short.S")
    endif()

    if(CONFIG_ESP_VIDEO_ENABLE_SWAP_BYTE)
        list(APPEND srcs "src/data_reprocessing/esp32p4/esp_video_swap_byte.c")
        list(APPEND srcs "src/data_reprocessing/esp32p4/esp_video_swap_byte.S")
    endif()
endif()

if(CONFIG_ESP_VIDEO_ENABLE_MIPI_CSI_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_csi_device.c" "src/device/esp_video_csi_format.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_DVP_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_dvp_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_SPI_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_spi_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_usb_uvc_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_H264_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_h264_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_JPEG_ENC_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_jpeg_enc_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_JPEG_DEC_VIDEO_DEVICE)
    list(APPEND srcs "src/device/esp_video_jpeg_dec_device.c")
endif()

if(CONFIG_ESP_VIDEO_ENABLE_ISP)
    list(APPEND srcs "src/device/esp_video_isp_device.c")

    if(CONFIG_ESP_VIDEO_ENABLE_ISP_PIPELINE_CONTROLLER)
        list(APPEND srcs "src/esp_video_isp_pipeline.c")
    endif()
endif()

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS ${include_dirs}
                       PRIV_INCLUDE_DIRS ${priv_include_dirs}
                       PRIV_REQUIRES ${priv_requires}
                       REQUIRES ${requires})

# [YOUKORR/ESPHome] cu_pkg_define_version() (cmake_utilities) générait normalement
# ESP_VIDEO_VER_{MAJOR,MINOR,PATCH} (utilisés par esp_video_ioctl.c VIDIOC_QUERYCAP).
# cmake_utilities étant indisponible hors registre, on définit la version 2.2.0 ici.
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    ESP_VIDEO_VER_MAJOR=2
    ESP_VIDEO_VER_MINOR=2
    ESP_VIDEO_VER_PATCH=0)

# [YOUKORR/ESPHome] La 2.2.0 upstream utilise cmake_utilities
# (include(package_manager) / cu_pkg_define_version / idf_component_optional_requires),
# composant managé indisponible hors registre Espressif. On lie directement les
# composants nécessaires (esp_ipa est local ; esp_driver_isp/jpeg sont intégrés à l'IDF).
if(CONFIG_ESP_VIDEO_ENABLE_ISP_PIPELINE_CONTROLLER)
    target_link_libraries(${COMPONENT_LIB} PRIVATE idf::esp_ipa)
endif()

if(CONFIG_IDF_TARGET_ESP32P4)
    if(CONFIG_ESP_VIDEO_ENABLE_ISP)
        target_link_libraries(${COMPONENT_LIB} PUBLIC idf::esp_driver_isp)
    endif()

    if(CONFIG_ESP_VIDEO_ENABLE_H264_VIDEO_DEVICE)
        target_link_libraries(${COMPONENT_LIB} PRIVATE idf::esp_h264)
    endif()
endif()

if(CONFIG_ESP_VIDEO_ENABLE_HW_JPEG_ENC_VIDEO_DEVICE OR CONFIG_ESP_VIDEO_ENABLE_HW_JPEG_DEC_VIDEO_DEVICE)
    target_link_libraries(${COMPONENT_LIB} PUBLIC idf::esp_driver_jpeg)
endif()
