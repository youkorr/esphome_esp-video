

if(CONFIG_CAMERA_SC2336)
    if(CONFIG_CAMERA_SC2336_DEFAULT_IPA_JSON_CONFIGURATION_FILE)
        idf_build_set_property(ESP_IPA_JSON_CONFIG_FILE_PATH "${COMPONENT_PATH}/sensors/sc2336/cfg/sc2336_default.json" APPEND)
    elseif(CONFIG_CAMERA_SC2336_CUSTOMIZED_IPA_JSON_CONFIGURATION_FILE)
        idf_build_set_property(ESP_IPA_JSON_CONFIG_FILE_PATH ${CONFIG_CAMERA_SC2336_CUSTOMIZED_IPA_JSON_CONFIGURATION_FILE_PATH} APPEND)
    endif()
endif()

if(CONFIG_CAMERA_OV2710)
    if(CONFIG_CAMERA_OV2710_DEFAULT_IPA_JSON_CONFIGURATION_FILE)
        idf_build_set_property(ESP_IPA_JSON_CONFIG_FILE_PATH "${COMPONENT_PATH}/sensors/ov2710/cfg/ov2710_default.json" APPEND)
    elseif(CONFIG_CAMERA_OV2710_CUSTOMIZED_IPA_JSON_CONFIGURATION_FILE)
        idf_build_set_property(ESP_IPA_JSON_CONFIG_FILE_PATH ${CONFIG_CAMERA_OV2710_CUSTOMIZED_IPA_JSON_CONFIGURATION_FILE_PATH} APPEND)
    endif()
endif()

if(CONFIG_CAMERA_OV02C10)
    if(CONFIG_CAMERA_OV02C10_DEFAULT_IPA_JSON_CONFIGURATION_FILE)
        idf_build_set_property(ESP_IPA_JSON_CONFIG_FILE_PATH "${COMPONENT_PATH}/sensor/ov02c10/cfg/ov02c10_default.json" APPEND)
    elseif(CONFIG_CAMERA_OV02C10_CUSTOMIZED_IPA_JSON_CONFIGURATION_FILE)
        idf_build_set_property(ESP_IPA_JSON_CONFIG_FILE_PATH ${CONFIG_CAMERA_OV02C10_CUSTOMIZED_IPA_JSON_CONFIGURATION_FILE_PATH} APPEND)
    endif()
endif()

# SC2356 (M5Stack Tab5 / reTerminal). Registered unconditionally, unlike the
# blocks above: esp_video enables its sensors with -D compiler flags only, which
# CMake never sees, so a CONFIG_CAMERA_SC2356 guard here would never fire and
# the JSON would never be embedded. Without it the ISP runs untuned and the
# image comes out with a heavy green cast.
#
# The other sensors are left as they are -- they do not need this today, and
# guarding on a symbol that is always empty is at least harmless there.
if(EXISTS "${COMPONENT_PATH}/sensor/sc2356/cfg/sc2356_default.json")
    if(CONFIG_CAMERA_SC2356_CUSTOMIZED_IPA_JSON_CONFIGURATION_FILE)
        idf_build_set_property(ESP_IPA_JSON_CONFIG_FILE_PATH ${CONFIG_CAMERA_SC2356_CUSTOMIZED_IPA_JSON_CONFIGURATION_FILE_PATH} APPEND)
    else()
        idf_build_set_property(ESP_IPA_JSON_CONFIG_FILE_PATH "${COMPONENT_PATH}/sensor/sc2356/cfg/sc2356_default.json" APPEND)
    endif()
    message(STATUS "esp_cam_sensor: SC2356 IPA tuning registered")
endif()
