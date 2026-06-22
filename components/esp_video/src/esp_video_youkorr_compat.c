/*
 * SPDX-License-Identifier: ESPRESSIF MIT
 *
 * [YOUKORR/ESPHome] Compatibility shims for the esp_video 2.2.0 migration.
 *
 * The youkorr fork added an ISP/IPA pipeline controller (esp_video_isp_pipeline.c,
 * esp_video_isp_stubs.c, embedded_*_ipa_config_json.c) plus the helper
 * esp_video_reconfigure_isp_pipeline(), all consumed by the ESPHome camera
 * component (esp_cam_sensor_camera.cpp). esp_video 2.2.0 ships a different ISP
 * pipeline implementation, so that subsystem is not yet reintegrated.
 *
 * To keep the ESPHome consumer compiling and LINKING against esp_video 2.2.0,
 * this file provides a no-op implementation of the missing symbol. Per-sensor ISP
 * re-init (used by OV5647 custom formats) is therefore inactive until the IPA
 * pipeline controller is ported. See MIGRATION_2.2.0.md.
 */

#include "esp_err.h"
#include "esp_log.h"
#include "esp_video_init.h"

static const char *TAG = "esp_video_compat";

esp_err_t esp_video_reconfigure_isp_pipeline(const char *sensor_name)
{
    ESP_LOGW(TAG,
             "esp_video_reconfigure_isp_pipeline('%s'): no-op (IPA pipeline controller "
             "not yet ported to esp_video 2.2.0 - see MIGRATION_2.2.0.md)",
             sensor_name ? sensor_name : "(null)");
    return ESP_OK;
}
