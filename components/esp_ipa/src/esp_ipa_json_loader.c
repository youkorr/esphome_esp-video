/*
 * SPDX-FileCopyrightText: 2025 Custom IPA JSON Loader
 * SPDX-License-Identifier: MIT
 */

#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <errno.h>
#include <sys/ioctl.h>
#include "esp_log.h"
#include "esp_err.h"
#include "cJSON.h"
#include "esp_ipa.h"
#include "esp_ipa_json_loader.h"
#include "esp_video_isp_ioctl.h"
#include "linux/videodev2.h"

static const char *TAG = "ipa_json";

// Déclarations externes des JSON embarqués (créés par esp_video_build.py)
extern const char ov02c10_ipa_config_json_start[];
extern const char *ov02c10_ipa_config_json_end;
extern const size_t ov02c10_ipa_config_json_size;
extern const char ov5647_ipa_config_json_start[];
extern const char *ov5647_ipa_config_json_end;
extern const size_t ov5647_ipa_config_json_size;
extern const char sc2336_ipa_config_json_start[];
extern const char *sc2336_ipa_config_json_end;
extern const size_t sc2336_ipa_config_json_size;
extern const char sc202cs_ipa_config_json_start[];
extern const char *sc202cs_ipa_config_json_end;
extern const size_t sc202cs_ipa_config_json_size;

/* ----------------------------------------------------------------------
 * Build-time IPA stage toggles
 *
 * Skip individual IPA stages at runtime. Both bare and CONFIG_-prefixed
 * names are accepted: ESPHome's `sdkconfig_options:` mechanism tends to
 * emit `CONFIG_xxx=y` into sdkconfig (so the C macro becomes
 * `CONFIG_xxx`), while a plain `-D` build flag would emit the unprefixed
 * one. We honour either form so the user doesn't have to think about it.
 *
 *   ESP_IPA_DISABLE_CCM       skip CCM application
 *   ESP_IPA_DISABLE_AWB       skip AWB range application
 *   ESP_IPA_DISABLE_SHARPEN   skip sharpen application
 *   ESP_IPA_DISABLE_CONTRAST  skip contrast application
 *   ESP_IPA_DISABLE_ALL       skip every stage (image goes through ISP
 *                             with whatever defaults the sensor driver
 *                             set up internally — this is what M5Stack's
 *                             official Tab5 user-demo does)
 * ---------------------------------------------------------------------- */
#if defined(CONFIG_ESP_IPA_DISABLE_ALL) || defined(ESP_IPA_DISABLE_ALL)
#define IPA_DISABLE_ALL 1
#else
#define IPA_DISABLE_ALL 0
#endif
#if defined(CONFIG_ESP_IPA_DISABLE_CCM) || defined(ESP_IPA_DISABLE_CCM) || IPA_DISABLE_ALL
#define IPA_DISABLE_CCM 1
#else
#define IPA_DISABLE_CCM 0
#endif
#if defined(CONFIG_ESP_IPA_DISABLE_AWB) || defined(ESP_IPA_DISABLE_AWB) || IPA_DISABLE_ALL
#define IPA_DISABLE_AWB 1
#else
#define IPA_DISABLE_AWB 0
#endif
#if defined(CONFIG_ESP_IPA_DISABLE_SHARPEN) || defined(ESP_IPA_DISABLE_SHARPEN) || IPA_DISABLE_ALL
#define IPA_DISABLE_SHARPEN 1
#else
#define IPA_DISABLE_SHARPEN 0
#endif
#if defined(CONFIG_ESP_IPA_DISABLE_CONTRAST) || defined(ESP_IPA_DISABLE_CONTRAST) || IPA_DISABLE_ALL
#define IPA_DISABLE_CONTRAST 1
#else
#define IPA_DISABLE_CONTRAST 0
#endif

/*
 * The default SC202CS JSON ships in esp_cam_sensor with a CCM/AWB
 * tuned for a different optical stack than what M5Stack uses on the
 * Tab5. Applying it produces a strong green tint and washed-out reds.
 *
 * The official M5Stack user-demo for the Tab5 doesn't apply any IPA
 * tuning at all — it just opens /dev/video0 and reads frames. We mirror
 * that behaviour by default for SC202CS, while keeping the JSON tuning
 * for the other sensors which have well-matched calibrations.
 *
 * To opt back in (e.g. once you have your own Tab5 calibration in the
 * JSON), set sdkconfig_options ESP_IPA_FORCE_SC202CS_TUNING=y.
 */
static int g_skip_all_for_sensor = 0;

/**
 * @brief Parse CCM (Color Correction Matrix) from JSON
 */
static esp_err_t parse_ccm_from_json(cJSON *sensor_root, esp_ipa_ccm_config_t *ccm_config)
{
    cJSON *acc = cJSON_GetObjectItem(sensor_root, "acc");
    if (!acc) return ESP_ERR_NOT_FOUND;
    cJSON *ccm = cJSON_GetObjectItem(acc, "ccm");
    if (!ccm) return ESP_ERR_NOT_FOUND;
    cJSON *table = cJSON_GetObjectItem(ccm, "table");
    if (!table || !cJSON_IsArray(table) || cJSON_GetArraySize(table) == 0) return ESP_ERR_INVALID_ARG;

    // This simplified loader pushes ONE static CCM to the ISP (no runtime
    // color-temperature interpolation like Espressif's full IPA). Picking
    // table[0] is wrong: Espressif anchors entry 0 at a very low CT with a
    // near-identity matrix (-> washed-out), and some forks put an aggressive
    // low-CT matrix there (-> green tint). Select the entry whose color_temp is
    // closest to a typical indoor/daylight white (~5000K) instead, which gives
    // correct, saturated colors without a tint. This is a parse-time choice; the
    // matrix itself is applied by the ISP hardware (zero CPU cost).
    const int target_ct = 5000;
    int n = cJSON_GetArraySize(table);
    cJSON *ccm_entry = NULL;
    int best_diff = 1000000;
    for (int e = 0; e < n; e++) {
        cJSON *cand = cJSON_GetArrayItem(table, e);
        if (!cand) continue;
        cJSON *ct = cJSON_GetObjectItem(cand, "color_temp");
        int ctv = (ct && cJSON_IsNumber(ct)) ? ct->valueint : 0;
        int diff = ctv > target_ct ? ctv - target_ct : target_ct - ctv;
        if (diff < best_diff) { best_diff = diff; ccm_entry = cand; }
    }
    if (!ccm_entry) ccm_entry = cJSON_GetArrayItem(table, 0);
    if (!ccm_entry) return ESP_ERR_INVALID_ARG;

    cJSON *color_temp = cJSON_GetObjectItem(ccm_entry, "color_temp");
    cJSON *matrix = cJSON_GetObjectItem(ccm_entry, "matrix");
    if (!matrix || !cJSON_IsArray(matrix)) return ESP_ERR_INVALID_ARG;
    if (cJSON_GetArraySize(matrix) != 9) return ESP_ERR_INVALID_SIZE;

    ccm_config->color_temp = color_temp ? color_temp->valueint : 0;
    for (int i = 0; i < 9; i++) {
        cJSON *value = cJSON_GetArrayItem(matrix, i);
        if (!value || !cJSON_IsNumber(value)) return ESP_ERR_INVALID_ARG;
        ccm_config->matrix[i / 3][i % 3] = (float)value->valuedouble;
    }
    ESP_LOGI(TAG, "CCM Matrix loaded:");
    ESP_LOGI(TAG, "  [%.3f, %.3f, %.3f]", ccm_config->matrix[0][0], ccm_config->matrix[0][1], ccm_config->matrix[0][2]);
    ESP_LOGI(TAG, "  [%.3f, %.3f, %.3f]", ccm_config->matrix[1][0], ccm_config->matrix[1][1], ccm_config->matrix[1][2]);
    ESP_LOGI(TAG, "  [%.3f, %.3f, %.3f]", ccm_config->matrix[2][0], ccm_config->matrix[2][1], ccm_config->matrix[2][2]);
    return ESP_OK;
}

/**
 * @brief Parse AWB from JSON
 */
static esp_err_t parse_awb_from_json(cJSON *sensor_root, esp_ipa_awb_config_t *awb_config)
{
    cJSON *awb = cJSON_GetObjectItem(sensor_root, "awb");
    if (!awb) return ESP_ERR_NOT_FOUND;
    cJSON *range = cJSON_GetObjectItem(awb, "range");
    if (!range) return ESP_ERR_NOT_FOUND;
    cJSON *rg = cJSON_GetObjectItem(range, "rg");
    cJSON *bg = cJSON_GetObjectItem(range, "bg");
    if (!rg || !bg) return ESP_ERR_INVALID_ARG;
    cJSON *rg_min = cJSON_GetObjectItem(rg, "min");
    cJSON *rg_max = cJSON_GetObjectItem(rg, "max");
    cJSON *bg_min = cJSON_GetObjectItem(bg, "min");
    cJSON *bg_max = cJSON_GetObjectItem(bg, "max");
    if (!rg_min || !rg_max || !bg_min || !bg_max) return ESP_ERR_INVALID_ARG;
    awb_config->rg_min = (float)rg_min->valuedouble;
    awb_config->rg_max = (float)rg_max->valuedouble;
    awb_config->bg_min = (float)bg_min->valuedouble;
    awb_config->bg_max = (float)bg_max->valuedouble;
    cJSON *min_counted = cJSON_GetObjectItem(awb, "min_counted");
    awb_config->min_counted = min_counted ? min_counted->valueint : 2000;
    ESP_LOGI(TAG, "AWB Ranges loaded: RG %.3f-%.3f BG %.3f-%.3f", awb_config->rg_min, awb_config->rg_max, awb_config->bg_min, awb_config->bg_max);
    return ESP_OK;
}

/**
 * @brief Parse Sharpen from JSON
 */
static esp_err_t parse_sharpen_from_json(cJSON *sensor_root, esp_ipa_sharpen_config_t *sharpen_config)
{
    cJSON *aen = cJSON_GetObjectItem(sensor_root, "aen");
    if (!aen) return ESP_ERR_NOT_FOUND;
    cJSON *sharpen = cJSON_GetObjectItem(aen, "sharpen");
    if (!sharpen || !cJSON_IsArray(sharpen) || cJSON_GetArraySize(sharpen) == 0) return ESP_ERR_NOT_FOUND;
    cJSON *sharpen_entry = cJSON_GetArrayItem(sharpen, 0);
    if (!sharpen_entry) return ESP_ERR_INVALID_ARG;
    cJSON *param = cJSON_GetObjectItem(sharpen_entry, "param");
    if (!param) return ESP_ERR_INVALID_ARG;
    cJSON *h_thresh = cJSON_GetObjectItem(param, "h_thresh");
    cJSON *l_thresh = cJSON_GetObjectItem(param, "l_thresh");
    cJSON *h_coeff = cJSON_GetObjectItem(param, "h_coeff");
    cJSON *m_coeff = cJSON_GetObjectItem(param, "m_coeff");
    cJSON *matrix = cJSON_GetObjectItem(param, "matrix");
    if (h_thresh) sharpen_config->h_thresh = h_thresh->valueint;
    if (l_thresh) sharpen_config->l_thresh = l_thresh->valueint;
    if (h_coeff) sharpen_config->h_coeff = (float)h_coeff->valuedouble;
    if (m_coeff) sharpen_config->m_coeff = (float)m_coeff->valuedouble;
    if (matrix && cJSON_IsArray(matrix) && cJSON_GetArraySize(matrix) == 9) {
        for (int i = 0; i < 9; i++) {
            cJSON *value = cJSON_GetArrayItem(matrix, i);
            if (value && cJSON_IsNumber(value)) sharpen_config->matrix[i / 3][i % 3] = (uint8_t)value->valueint;
        }
        sharpen_config->has_matrix = true;
    } else {
        sharpen_config->has_matrix = false;
    }
    ESP_LOGI(TAG, "Sharpen loaded: H=%d L=%d Hc=%.2f Mc=%.2f", sharpen_config->h_thresh, sharpen_config->l_thresh, sharpen_config->h_coeff, sharpen_config->m_coeff);
    return ESP_OK;
}

static esp_err_t parse_gamma_from_json(cJSON *sensor_root, esp_ipa_gamma_config_t *gamma_config)
{
    cJSON *aen = cJSON_GetObjectItem(sensor_root, "aen");
    if (!aen) return ESP_ERR_NOT_FOUND;
    cJSON *gamma = cJSON_GetObjectItem(aen, "gamma");
    if (!gamma) return ESP_ERR_NOT_FOUND;
    cJSON *use_gamma_param = cJSON_GetObjectItem(gamma, "use_gamma_param");
    cJSON *table = cJSON_GetObjectItem(gamma, "table");
    if (use_gamma_param) gamma_config->use_gamma_param = cJSON_IsTrue(use_gamma_param);
    if (table && cJSON_IsArray(table) && cJSON_GetArraySize(table) > 0) {
        cJSON *gamma_entry = cJSON_GetArrayItem(table, 0);
        cJSON *gamma_param = cJSON_GetObjectItem(gamma_entry, "gamma_param");
        if (gamma_param) {
            gamma_config->gamma_param = (float)gamma_param->valuedouble;
            return ESP_OK;
        }
    }
    return ESP_ERR_NOT_FOUND;
}

static esp_err_t parse_contrast_from_json(cJSON *sensor_root, esp_ipa_contrast_config_t *contrast_config)
{
    cJSON *aen = cJSON_GetObjectItem(sensor_root, "aen");
    if (!aen) return ESP_ERR_NOT_FOUND;
    cJSON *contrast = cJSON_GetObjectItem(aen, "contrast");
    if (!contrast || !cJSON_IsArray(contrast) || cJSON_GetArraySize(contrast) == 0) return ESP_ERR_NOT_FOUND;
    cJSON *contrast_entry = cJSON_GetArrayItem(contrast, 0);
    if (!contrast_entry) return ESP_ERR_INVALID_ARG;
    cJSON *value = cJSON_GetObjectItem(contrast_entry, "value");
    if (value) {
        contrast_config->value = value->valueint;
        return ESP_OK;
    }
    return ESP_ERR_NOT_FOUND;
}

esp_err_t esp_ipa_load_json_config(const char *sensor_name, esp_ipa_json_config_t *ipa_json_config)
{
    if (!sensor_name || !ipa_json_config) return ESP_ERR_INVALID_ARG;
    ESP_LOGI(TAG, "Loading IPA JSON config for sensor: %s", sensor_name);

    g_skip_all_for_sensor = 0;
    const char *json_data = NULL;
    size_t json_size = 0;

    if (strcasecmp(sensor_name, "OV02C10") == 0) {
        json_data = ov02c10_ipa_config_json_start;
        json_size = ov02c10_ipa_config_json_size;
        ESP_LOGI(TAG, "Using OV02C10 JSON (%zu bytes)", json_size);
    } else if (strcasecmp(sensor_name, "OV5647") == 0) {
        json_data = ov5647_ipa_config_json_start;
        json_size = ov5647_ipa_config_json_size;
        ESP_LOGI(TAG, "Using OV5647 JSON (%zu bytes)", json_size);
    } else if (strcasecmp(sensor_name, "SC2336") == 0) {
        json_data = sc2336_ipa_config_json_start;
        json_size = sc2336_ipa_config_json_size;
        ESP_LOGI(TAG, "Using SC2336 JSON (%zu bytes)", json_size);
    } else if (strcasecmp(sensor_name, "SC202CS") == 0) {
        json_data = sc202cs_ipa_config_json_start;
        json_size = sc202cs_ipa_config_json_size;
        ESP_LOGI(TAG, "Using SC202CS JSON (%zu bytes) [M5Stack Tab5]", json_size);
        // Color tuning (CCM + AWB) is applied to the ISP by default. It runs
        // entirely on the ESP32-P4 ISP HARDWARE (one-shot V4L2 controls at init),
        // so it costs no CPU/PSRAM on the video path and does not affect fps.
        //
        // The old green tint was NOT inherent to the tuning: it came from a
        // mis-edited JSON (an aggressive low-CT CCM forced into table[0] and AWB
        // ranges copied from the OV02C10). With the official AWB ranges restored
        // and the CCM now chosen near ~5000K (see parse_ccm_from_json), applying
        // the tuning fixes the washed-out look without the tint.
        //
        // Set ESP_IPA_DISABLE_SC202CS_TUNING=y to fall back to no tuning (the
        // old washed-out behaviour) if a particular board needs it.
#if defined(CONFIG_ESP_IPA_DISABLE_SC202CS_TUNING) || defined(ESP_IPA_DISABLE_SC202CS_TUNING)
        ESP_LOGW(TAG, "SC202CS: ISP IPA tuning disabled by config -> colors will look washed out.");
        g_skip_all_for_sensor = 1;
#endif
    } else {
        ESP_LOGE(TAG, "Unknown sensor: %s", sensor_name);
        ESP_LOGE(TAG, "Supported sensors: OV02C10, OV5647, SC2336, SC202CS");
        return ESP_ERR_NOT_SUPPORTED;
    }

    if (!json_data || json_size == 0) return ESP_ERR_NOT_FOUND;

    cJSON *root = cJSON_Parse(json_data);
    if (!root) return ESP_ERR_INVALID_ARG;

    cJSON *sensor_root = cJSON_GetObjectItem(root, sensor_name);
    if (!sensor_root) {
        char sensor_lower[32];
        strncpy(sensor_lower, sensor_name, sizeof(sensor_lower) - 1);
        sensor_lower[sizeof(sensor_lower) - 1] = '\0';
        for (int i = 0; sensor_lower[i]; i++) sensor_lower[i] = tolower((unsigned char)sensor_lower[i]);
        sensor_root = cJSON_GetObjectItem(root, sensor_lower);
    }
    if (!sensor_root) {
        char sensor_upper[32];
        strncpy(sensor_upper, sensor_name, sizeof(sensor_upper) - 1);
        sensor_upper[sizeof(sensor_upper) - 1] = '\0';
        for (int i = 0; sensor_upper[i]; i++) sensor_upper[i] = toupper((unsigned char)sensor_upper[i]);
        sensor_root = cJSON_GetObjectItem(root, sensor_upper);
    }
    if (!sensor_root) {
        ESP_LOGE(TAG, "Sensor section '%s' not found in JSON", sensor_name);
        cJSON_Delete(root);
        return ESP_ERR_NOT_FOUND;
    }

    memset(ipa_json_config, 0, sizeof(esp_ipa_json_config_t));
    if (parse_ccm_from_json(sensor_root, &ipa_json_config->ccm) == ESP_OK) ipa_json_config->has_ccm = true;
    if (parse_awb_from_json(sensor_root, &ipa_json_config->awb) == ESP_OK) ipa_json_config->has_awb = true;
    if (parse_sharpen_from_json(sensor_root, &ipa_json_config->sharpen) == ESP_OK) ipa_json_config->has_sharpen = true;
    if (parse_gamma_from_json(sensor_root, &ipa_json_config->gamma) == ESP_OK) ipa_json_config->has_gamma = true;
    if (parse_contrast_from_json(sensor_root, &ipa_json_config->contrast) == ESP_OK) ipa_json_config->has_contrast = true;
    cJSON_Delete(root);

    ESP_LOGI(TAG, "IPA JSON loaded - CCM:%s AWB:%s Sharpen:%s Gamma:%s Contrast:%s",
             ipa_json_config->has_ccm ? "Y" : "N",
             ipa_json_config->has_awb ? "Y" : "N",
             ipa_json_config->has_sharpen ? "Y" : "N",
             ipa_json_config->has_gamma ? "Y" : "N",
             ipa_json_config->has_contrast ? "Y" : "N");
    return ESP_OK;
}

esp_err_t esp_ipa_apply_json_to_isp(int isp_fd, const esp_ipa_json_config_t *ipa_json_config)
{
    if (isp_fd < 0 || !ipa_json_config) return ESP_ERR_INVALID_ARG;

    /* Sensor-driven full skip (currently used for SC202CS — see comment in
     * esp_ipa_load_json_config). */
    if (g_skip_all_for_sensor) {
        ESP_LOGW(TAG, "All IPA stages skipped for this sensor (matches M5Tab5 official user-demo).");
        return ESP_OK;
    }

    /* Build-time global skip */
#if IPA_DISABLE_ALL
    ESP_LOGW(TAG, "IPA_DISABLE_ALL=1 — not pushing any tuning to the ISP.");
    return ESP_OK;
#endif

    struct v4l2_ext_controls controls;
    struct v4l2_ext_control control[1];
    esp_err_t ret = ESP_OK;
    int applied_count = 0;

    ESP_LOGI(TAG, "Applying JSON IPA parameters to ISP...");

    // 1. CCM
    if (ipa_json_config->has_ccm) {
#if IPA_DISABLE_CCM
        ESP_LOGW(TAG, "  CCM SKIPPED (build-time disable)");
#else
        esp_video_isp_ccm_t ccm = { .enable = true };
        memcpy(ccm.matrix, ipa_json_config->ccm.matrix, sizeof(ccm.matrix));
        controls.ctrl_class = V4L2_CID_USER_CLASS;
        controls.count = 1;
        controls.controls = control;
        control[0].id = V4L2_CID_USER_ESP_ISP_CCM;
        control[0].p_u8 = (uint8_t *)&ccm;
        if (ioctl(isp_fd, VIDIOC_S_EXT_CTRLS, &controls) == 0) {
            ESP_LOGI(TAG, "  CCM matrix applied successfully");
            applied_count++;
        } else {
            ESP_LOGE(TAG, "  Failed to apply CCM matrix: errno=%d", errno);
            ret = ESP_FAIL;
        }
#endif
    }

    // 2. AWB
    if (ipa_json_config->has_awb) {
#if IPA_DISABLE_AWB
        ESP_LOGW(TAG, "  AWB SKIPPED (build-time disable)");
#else
        esp_video_isp_awb_t awb = {
            .enable = true,
            .rg_min = ipa_json_config->awb.rg_min, .rg_max = ipa_json_config->awb.rg_max,
            .bg_min = ipa_json_config->awb.bg_min, .bg_max = ipa_json_config->awb.bg_max,
            .green_min = 0, .green_max = 255,
        };
        controls.ctrl_class = V4L2_CID_USER_CLASS;
        controls.count = 1;
        controls.controls = control;
        control[0].id = V4L2_CID_USER_ESP_ISP_AWB;
        control[0].p_u8 = (uint8_t *)&awb;
        if (ioctl(isp_fd, VIDIOC_S_EXT_CTRLS, &controls) == 0) {
            ESP_LOGI(TAG, "  AWB ranges applied (RG: %.3f-%.3f, BG: %.3f-%.3f)",
                     awb.rg_min, awb.rg_max, awb.bg_min, awb.bg_max);
            applied_count++;
        } else {
            ESP_LOGE(TAG, "  Failed to apply AWB ranges: errno=%d", errno);
            ret = ESP_FAIL;
        }
#endif
    }

    // 3. Sharpen
    if (ipa_json_config->has_sharpen) {
#if IPA_DISABLE_SHARPEN
        ESP_LOGW(TAG, "  Sharpen SKIPPED (build-time disable)");
#else
        esp_video_isp_sharpen_t sharpen = {
            .enable = true,
            .h_thresh = ipa_json_config->sharpen.h_thresh,
            .l_thresh = ipa_json_config->sharpen.l_thresh,
            .h_coeff = ipa_json_config->sharpen.h_coeff,
            .m_coeff = ipa_json_config->sharpen.m_coeff,
        };
        if (ipa_json_config->sharpen.has_matrix)
            memcpy(sharpen.matrix, ipa_json_config->sharpen.matrix, sizeof(sharpen.matrix));
        else
            memset(sharpen.matrix, 1, sizeof(sharpen.matrix));
        controls.ctrl_class = V4L2_CID_USER_CLASS;
        controls.count = 1;
        controls.controls = control;
        control[0].id = V4L2_CID_USER_ESP_ISP_SHARPEN;
        control[0].p_u8 = (uint8_t *)&sharpen;
        if (ioctl(isp_fd, VIDIOC_S_EXT_CTRLS, &controls) == 0) {
            ESP_LOGI(TAG, "  Sharpen applied");
            applied_count++;
        } else {
            ESP_LOGE(TAG, "  Failed to apply sharpen: errno=%d", errno);
        }
#endif
    }

    // 4. Gamma
    if (ipa_json_config->has_gamma && ipa_json_config->gamma.use_gamma_param) {
        ESP_LOGW(TAG, "  Gamma parameter found but V4L2 gamma control requires point coordinates");
    }

    // 5. Contrast
    if (ipa_json_config->has_contrast) {
#if IPA_DISABLE_CONTRAST
        ESP_LOGW(TAG, "  Contrast SKIPPED (build-time disable)");
#else
        controls.ctrl_class = V4L2_CID_USER_CLASS;
        controls.count = 1;
        controls.controls = control;
        control[0].id = V4L2_CID_CONTRAST;
        control[0].value = ipa_json_config->contrast.value;
        if (ioctl(isp_fd, VIDIOC_S_EXT_CTRLS, &controls) == 0) {
            ESP_LOGI(TAG, "  Contrast applied: %d", ipa_json_config->contrast.value);
            applied_count++;
        }
#endif
    }

    ESP_LOGI(TAG, "JSON IPA application complete: %d parameters applied", applied_count);
    return applied_count > 0 ? ESP_OK : ret;
}
