#pragma once
// Stand-in, copied field for field from ESP-IDF v5.5.5
// components/bt/host/bluedroid/api/include/api/esp_a2dp_api.h and the
// esp_a2dp_legacy_api.h it includes on its line 12.
//
// THAT INCLUDE IS THE WHOLE REASON THIS FILE EXISTS AS IT DOES. Reading
// esp_a2dp_api.h on its own says the raw-PCM source callback was removed in
// 5.5 -- it is not in that file -- and this project wrote that down as a
// finding, planned an SBC encoder around it, and was wrong. The declaration
// is one include away, in a header whose name could not be guessed. A header
// is not one file: follow what it includes before concluding a symbol is gone.
#include "esp_bt_defs.h"
#include "esp_err.h"
#include <cstdint>

typedef uint16_t esp_a2d_conn_hdl_t;

typedef enum {
  ESP_A2D_CONNECTION_STATE_DISCONNECTED = 0,
  ESP_A2D_CONNECTION_STATE_CONNECTING,
  ESP_A2D_CONNECTION_STATE_CONNECTED,
  ESP_A2D_CONNECTION_STATE_DISCONNECTING,
} esp_a2d_connection_state_t;

typedef enum {
  ESP_A2D_DISC_RSN_NORMAL = 0,
  ESP_A2D_DISC_RSN_ABNORMAL,
} esp_a2d_disc_rsn_t;

typedef enum {
  ESP_A2D_AUDIO_STATE_SUSPEND = 0,
  ESP_A2D_AUDIO_STATE_STARTED,
} esp_a2d_audio_state_t;

typedef enum {
  ESP_A2D_MEDIA_CTRL_ACK_SUCCESS = 0,
  ESP_A2D_MEDIA_CTRL_ACK_FAILURE,
  ESP_A2D_MEDIA_CTRL_ACK_BUSY,
} esp_a2d_media_ctrl_ack_t;

typedef enum {
  ESP_A2D_MEDIA_CTRL_NONE = 0,
  ESP_A2D_MEDIA_CTRL_CHECK_SRC_RDY,
  ESP_A2D_MEDIA_CTRL_START,
  ESP_A2D_MEDIA_CTRL_SUSPEND,
  ESP_A2D_MEDIA_CTRL_STOP,
} esp_a2d_media_ctrl_t;

typedef enum {
  ESP_A2D_DEINIT_SUCCESS = 0,
  ESP_A2D_INIT_SUCCESS,
} esp_a2d_init_state_t;

typedef enum {
  ESP_A2D_CONNECTION_STATE_EVT = 0,
  ESP_A2D_AUDIO_STATE_EVT,
  ESP_A2D_AUDIO_CFG_EVT,
  ESP_A2D_MEDIA_CTRL_ACK_EVT,
  ESP_A2D_PROF_STATE_EVT,
  ESP_A2D_SEP_REG_STATE_EVT,
  ESP_A2D_SNK_PSC_CFG_EVT,
  ESP_A2D_SNK_SET_DELAY_VALUE_EVT,
  ESP_A2D_SNK_GET_DELAY_VALUE_EVT,
  ESP_A2D_REPORT_SNK_DELAY_VALUE_EVT,
} esp_a2d_cb_event_t;

typedef union {
  struct a2d_conn_stat_param {
    esp_a2d_connection_state_t state;
    esp_bd_addr_t remote_bda;
    esp_a2d_conn_hdl_t conn_hdl;
    uint16_t audio_mtu;
    esp_a2d_disc_rsn_t disc_rsn;
  } conn_stat;

  struct a2d_audio_stat_param {
    esp_a2d_audio_state_t state;
    esp_bd_addr_t remote_bda;
    esp_a2d_conn_hdl_t conn_hdl;
  } audio_stat;

  struct media_ctrl_stat_param {
    esp_a2d_media_ctrl_t cmd;
    esp_a2d_media_ctrl_ack_t status;
  } media_ctrl_stat;

  struct a2d_prof_stat_param {
    esp_a2d_init_state_t init_state;
  } a2d_prof_stat;
} esp_a2d_cb_param_t;

typedef void (*esp_a2d_cb_t)(esp_a2d_cb_event_t event, esp_a2d_cb_param_t *param);

// From esp_a2dp_legacy_api.h. Marked [Deprecated] there and declared with no
// #if of any kind, so it is what a default build (BT_A2DP_USE_EXTERNAL_CODEC
// off) uses. `len` of -1 is a FLUSH and the return value is then ignored.
typedef int32_t (*esp_a2d_source_data_cb_t)(uint8_t *buf, int32_t len);
esp_err_t esp_a2d_source_register_data_callback(esp_a2d_source_data_cb_t callback);

esp_err_t esp_a2d_register_callback(esp_a2d_cb_t callback);
esp_err_t esp_a2d_media_ctrl(esp_a2d_media_ctrl_t ctrl);
esp_err_t esp_a2d_source_init(void);
esp_err_t esp_a2d_source_deinit(void);
esp_err_t esp_a2d_source_connect(esp_bd_addr_t remote_bda);
esp_err_t esp_a2d_source_disconnect(esp_bd_addr_t remote_bda);
