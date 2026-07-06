/*
 * SPDX-FileCopyrightText: 2024-2025 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Modified: 2025-11-08 - Force rebuild to ensure SC202CS is first in detection order
 */

#include <stddef.h>
#include "esp_cam_sensor_types.h"
#include "esp_cam_sensor_detect.h"
#include "ov5647.h"
#include "sc202cs.h"
#include "ov02c10.h"
#include "sc2336.h"
#include "sc2356.h"

/**
 * @brief Camera sensor detection array - ESPHome/PlatformIO implementation
 *
 * CRITICAL FIX v4: Declare variables in REVERSE order to compensate for linker inversion.
 *
 * The header declares:
 *   extern esp_cam_sensor_detect_fn_t __esp_cam_sensor_detect_fn_array_start[];
 *   extern esp_cam_sensor_detect_fn_t __esp_cam_sensor_detect_fn_array_end;
 *
 * The code iterates: for (p = start; p < &end; ++p)
 *
 * PROBLEM 1 (original): Linker placed _end 15108 bytes away, causing 1259 iterations
 * ATTEMPTED FIX v1: Used .sensor_detect.00 and .sensor_detect.01 subsections
 * PROBLEM 2: Linker placed sections in REVERSE order (.01 before .00)!
 * ATTEMPTED FIX v2: Used .rodata.sensor_detect
 * PROBLEM 3: Assembler warning - .rodata sections have incorrect attributes
 * ATTEMPTED FIX v3: Used .data.sensor_detect
 * PROBLEM 4: ESP-IDF linker STILL inverted the order!
 *   Result: _end=0x4ff1390c, _start=0x4ff13918 → difference = -12 bytes
 *
 * SOLUTION v4: DECLARE IN REVERSE ORDER to compensate for linker inversion!
 * If linker systematically inverts, declaring _end BEFORE _start will result
 * in the linker placing _start before _end in memory (the correct order).
 */

// INTENTIONALLY DECLARE _end FIRST (linker will place it LAST)
// Define _end as a sentinel in .data.sensor_detect
__attribute__((section(".data.sensor_detect"), used))
esp_cam_sensor_detect_fn_t __esp_cam_sensor_detect_fn_array_end = {
    .detect = NULL,
    .port = 0,
    .sccb_addr = 0
};

// INTENTIONALLY DECLARE _start SECOND (linker will place it FIRST)
// Define _start as an array containing all sensors
// ORDER MATTERS: Put most likely sensor first for faster detection
__attribute__((section(".data.sensor_detect"), used))
esp_cam_sensor_detect_fn_t __esp_cam_sensor_detect_fn_array_start[] = {
    // Sensor 0: SC202CS (M5Stack Tab5 default sensor - try first!)
    {
        .detect = (esp_cam_sensor_device_t *(*)(void *))sc202cs_detect,
        .port = ESP_CAM_SENSOR_MIPI_CSI,
        .sccb_addr = SC202CS_SCCB_ADDR
    },
    // Sensor 1: OV5647
    {
        .detect = (esp_cam_sensor_device_t *(*)(void *))ov5647_detect,
        .port = ESP_CAM_SENSOR_MIPI_CSI,
        .sccb_addr = OV5647_SCCB_ADDR
    },
    // Sensor 2: OV02C10
    {
        .detect = (esp_cam_sensor_device_t *(*)(void *))ov02c10_detect,
        .port = ESP_CAM_SENSOR_MIPI_CSI,
        .sccb_addr = OV02C10_SCCB_ADDR
    },
    // Sensor 3: SC2336 (I2C addr 0x30)
    {
        .detect = (esp_cam_sensor_device_t *(*)(void *))sc2336_detect,
        .port = ESP_CAM_SENSOR_MIPI_CSI,
        .sccb_addr = SC2336_SCCB_ADDR
    },
    // Sensor 4: SC2356 (Seeed reTerminal-D1001, I2C addr 0x36)
    {
        .detect = (esp_cam_sensor_device_t *(*)(void *))sc2356_detect,
        .port = ESP_CAM_SENSOR_MIPI_CSI,
        .sccb_addr = SC2356_SCCB_ADDR
    },
};

/**
 * How this works (REVERSE DECLARATION HACK):
 *
 * 1. Both variables in section ".data.sensor_detect" (.data for initialized globals)
 * 2. ESP-IDF linker INVERTS declaration order within custom sections
 * 3. We declare _end FIRST, _start SECOND (reverse of logical order)
 * 4. Linker inverts this: places _start in memory first, _end second (correct!)
 * 5. __attribute__((used)) prevents optimization
 * 6. Loop: for (p = start; p < &end; ++p) now works correctly
 *
 * Declaration order (in source code):
 *   Line 44: __esp_cam_sensor_detect_fn_array_end (declared FIRST)
 *   Line 54: __esp_cam_sensor_detect_fn_array_start[] (declared SECOND)
 *
 * Expected memory layout (after linker inversion):
 *   0x4ff13xxx: __esp_cam_sensor_detect_fn_array_start[0] (SC202CS) ← placed FIRST
 *   0x4ff13xxx+12: __esp_cam_sensor_detect_fn_array_start[1] (OV5647)
 *   0x4ff13xxx+24: __esp_cam_sensor_detect_fn_array_start[2] (OV02C10)
 *   0x4ff13xxx+36: __esp_cam_sensor_detect_fn_array_end (sentinel) ← placed LAST
 *
 * Pointer difference should be +36 bytes (3 sensors × 12 bytes), NOT -12!
 */
