# ============================================================================
# Global include directories for the ESP-Video stack
# ============================================================================
#
# The ESPHome "glue" sources are compiled inside the main ESPHome component
# (__idf_src), NOT inside the esp_video / esp_cam_sensor IDF components:
#
#   * esphome/components/esp_video/esp_video_component.cpp
#   * esphome/components/esp_cam_sensor/esp_cam_sensor_camera.cpp
#
# __idf_src does not "REQUIRES" esp_video / esp_cam_sensor / esp_ipa /
# esp_sccb_intf, so in a pure ESP-IDF (CMake/ninja) build it never receives
# their public INCLUDE_DIRS. The Python codegen tries to compensate with
# cg.add_build_flag("-I...") in __init__.py, but those flags are not always
# propagated to the __idf_src compile commands, which produces:
#
#   fatal error: esp_video_init.h: No such file or directory
#   fatal error: sys/mman.h: No such file or directory   (bundled POSIX shim)
#
# project_include.cmake is evaluated in PROJECT scope, so appending the include
# directories to the global COMPILE_OPTIONS makes the headers visible to every
# translation unit (including the ESPHome glue files) regardless of component
# REQUIRES. This mirrors the include list in esp_video/__init__.py.

get_filename_component(_esp_video_dir "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)
get_filename_component(_components_dir "${_esp_video_dir}/.." ABSOLUTE)

set(_esp_video_global_includes
    # esp_video
    "${_esp_video_dir}/include"
    "${_esp_video_dir}/private_include"
    "${_esp_video_dir}/src"
    # esp_cam_sensor
    "${_components_dir}/esp_cam_sensor/include"
    "${_components_dir}/esp_cam_sensor/sensor/ov5647/include"
    "${_components_dir}/esp_cam_sensor/sensor/sc202cs/include"
    "${_components_dir}/esp_cam_sensor/sensor/sc2336/include"
    "${_components_dir}/esp_cam_sensor/sensor/ov02c10/include"
    "${_components_dir}/esp_cam_sensor/src"
    "${_components_dir}/esp_cam_sensor/src/driver_spi"
    "${_components_dir}/esp_cam_sensor/src/driver_cam"
    # esp_ipa
    "${_components_dir}/esp_ipa/include"
    "${_components_dir}/esp_ipa/src"
    # esp_sccb_intf
    "${_components_dir}/esp_sccb_intf/include"
    "${_components_dir}/esp_sccb_intf/interface"
    "${_components_dir}/esp_sccb_intf/sccb_i2c/include"
)

foreach(_inc ${_esp_video_global_includes})
    if(EXISTS "${_inc}")
        idf_build_set_property(COMPILE_OPTIONS "-I${_inc}" APPEND)
    endif()
endforeach()
