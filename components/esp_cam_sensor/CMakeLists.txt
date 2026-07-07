set(srcs "src/esp_cam_sensor.c"
         "src/esp_cam_motor.c"
         "src/esp_cam_sensor_xclk.c"
         "src/esp_cam_sensor_detect_stubs.c"
         "src/driver_spi/spi_slave.c"
         "src/driver_cam/esp_cam_ctlr_spi_cam.c"
         "sensor/ov5647/ov5647.c"
         "sensor/sc202cs/sc202cs.c"
         "sensor/sc2336/sc2336.c"
         "sensor/sc2356/sc2356.c"
         "sensor/ov02c10/ov02c10.c")

set(include_dirs "include"
                 "sensor/ov5647/include"
                 "sensor/sc202cs/include"
                 "sensor/sc2336/include"
                 "sensor/sc2356/include"
                 "sensor/ov02c10/include")

set(priv_include_dirs "src"
                      "src/driver_spi"
                      "src/driver_cam"
                      "sensor/ov5647/private_include"
                      "sensor/sc202cs/include/private_include"
                      "sensor/sc2336/include/private_include"
                      "sensor/sc2356/private_include"
                      "sensor/ov02c10/private_include")

set(requires "esp_driver_i2c" "esp_driver_gpio" "esp_driver_ledc" "esp_driver_cam" "esp_sccb_intf" "esp_driver_spi")
set(priv_requires "spi_flash" "driver")

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS ${include_dirs}
                       PRIV_INCLUDE_DIRS ${priv_include_dirs}
                       REQUIRES ${requires}
                       PRIV_REQUIRES ${priv_requires})

# SC202CS configuration - Match M5Stack Tab5 settings
# These flags MUST be set here to be compiled into the driver
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    CONFIG_CAMERA_SC202CS=1
    CONFIG_CAMERA_SC202CS_AUTO_DETECT=1
    CONFIG_CAMERA_SC202CS_AUTO_DETECT_MIPI_INTERFACE_SENSOR=1
    # Probe/default format = index 1 = 1280x720 (the sensor's native default,
    # see sc202cs.c). Index 0 is the 800x600 centered-crop mode; forcing 0 here
    # made the sensor probe at 800x600 so a 1280x720 request was rejected by
    # csi_video ("format width or height is invalid"). 800x600 still works: the
    # component applies sc202cs_custom_format_800x600 via VIDIOC_S_SENSOR_FMT
    # when that resolution is requested, so this only sets the fallback.
    CONFIG_CAMERA_SC202CS_MIPI_IF_FORMAT_INDEX_DEFAULT=1
    CONFIG_CAMERA_SC202CS_ABSOLUTE_GAIN_LIMIT=63008
    CONFIG_CAMERA_SC202CS_ANA_GAIN_PRIORITY=0
    CONFIG_CAMERA_SC202CS_DIG_GAIN_PRIORITY=1
    CONFIG_CAMERA_SC202CS_MAX_SUPPORT=1
)

# OV5647 configuration
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    CONFIG_CAMERA_OV5647=1
    CONFIG_CAMERA_OV5647_AUTO_DETECT=1
    CONFIG_CAMERA_OV5647_AUTO_DETECT_MIPI_INTERFACE_SENSOR=1
    CONFIG_CAMERA_OV5647_MIPI_IF_FORMAT_INDEX_DEFAULT=0
    CONFIG_CAMERA_OV5647_CSI_LINESYNC_ENABLE=0
    CONFIG_CAMERA_OV5647_MAX_SUPPORT=1
)

# OV02C10 configuration
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    CONFIG_CAMERA_OV02C10=1
    CONFIG_CAMERA_OV02C10_AUTO_DETECT=1
    CONFIG_CAMERA_OV02C10_AUTO_DETECT_MIPI_INTERFACE_SENSOR=1
    CONFIG_CAMERA_OV02C10_MIPI_IF_FORMAT_INDEX_DEFAULT=0
    CONFIG_CAMERA_OV02C10_CSI_LINESYNC_ENABLE=0
    CONFIG_CAMERA_OV02C10_ABSOLUTE_GAIN_LIMIT=16000
    CONFIG_CAMERA_OV02C10_ANA_GAIN_PRIORITY=1
    CONFIG_CAMERA_OV02C10_DIG_GAIN_PRIORITY=0
    CONFIG_CAMERA_OV02C10_MAX_SUPPORT=1
)

# SC2336 configuration
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    CONFIG_CAMERA_SC2336=1
    CONFIG_CAMERA_SC2336_AUTO_DETECT=1
    CONFIG_CAMERA_SC2336_AUTO_DETECT_MIPI_INTERFACE_SENSOR=1
    CONFIG_CAMERA_SC2336_MIPI_IF_FORMAT_INDEX_DEFAULT=0
    CONFIG_CAMERA_SC2336_DVP_IF_FORMAT_INDEX_DEFAULT=0
    CONFIG_CAMERA_SC2336_CSI_LINESYNC_ENABLE=0
    CONFIG_CAMERA_SC2336_ABSOLUTE_GAIN_LIMIT=63008
    CONFIG_CAMERA_SC2336_ANA_GAIN_PRIORITY=0
    CONFIG_CAMERA_SC2336_DIG_GAIN_PRIORITY=1
    CONFIG_CAMERA_SC2336_MAX_SUPPORT=1
    CONFIG_CAMERA_SC2336_MIPI_RAW8_800X800_30FPS=1
    CONFIG_CAMERA_SC2336_MIPI_RAW10_800X800_30FPS=1
    CONFIG_CAMERA_SC2336_MIPI_RAW8_1024X600_30FPS=1
    CONFIG_CAMERA_SC2336_MIPI_RAW10_1280X720_30FPS=1
    CONFIG_CAMERA_SC2336_MIPI_RAW10_1920X1080_30FPS=1
    CONFIG_CAMERA_SC2336_MIPI_RAW10_640X480_50FPS=1
)

# SC2356 configuration (Seeed reTerminal-D1001). Shares SCCB address 0x36 with
# SC202CS/SC2336; each detect() reads its own sensor ID so co-registration is
# fine. Formats in sc2356_format_info[] are unconditional, so only the default
# index needs to be set: index 0 = 1280x720 RAW8 (the driver's default).
target_compile_definitions(${COMPONENT_LIB} PUBLIC
    CONFIG_CAMERA_SC2356=1
    CONFIG_CAMERA_SC2356_AUTO_DETECT=1
    CONFIG_CAMERA_SC2356_AUTO_DETECT_MIPI_INTERFACE_SENSOR=1
    CONFIG_CAMERA_SC2356_MIPI_IF_FORMAT_INDEX_DEFAULT=0
    CONFIG_CAMERA_SC2356_ABSOLUTE_GAIN_LIMIT=63008
    CONFIG_CAMERA_SC2356_ANA_GAIN_PRIORITY=0
    CONFIG_CAMERA_SC2356_DIG_GAIN_PRIORITY=1
    CONFIG_CAMERA_SC2356_MAX_SUPPORT=1
)

# Force linker to include sensor detection functions (prevent optimization)
# These symbols are defined via ESP_CAM_SENSOR_DETECT_FN macro in sensor source files
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u esp_cam_sensor_detect_fn_sc202cs_detect_1")
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u esp_cam_sensor_detect_fn_ov5647_detect_1")
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u esp_cam_sensor_detect_fn_ov02c10_detect_1")
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u esp_cam_sensor_detect_fn_sc2336_detect_1")
target_link_libraries(${COMPONENT_LIB} INTERFACE "-u esp_cam_sensor_detect_fn_sc2356_detect_1")
