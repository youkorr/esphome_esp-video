#pragma once
// Stand-in, copied field for field from ESP-IDF v5.5.5
// components/bt/host/bluedroid/api/include/api/esp_avrc_api.h -- only what
// portall_bt names.
//
// The volume is 0..127 and the key state is 0 for PRESSED, which is the way
// round nobody guesses: every other API in this component uses 1 for a key
// being down. Copied rather than remembered for exactly that reason.
#include "esp_bt_defs.h"
#include "esp_err.h"
#include <cstdint>

typedef enum {
  /* The AV/C panel subunit's navigation commands, which is where this
     stand-in was HALF a header until keys.cpp needed them: it carried only
     the media transport codes, because those were all the component happened
     to use, so a missing navigation constant could never have been caught.
     Copied field for field from v5.5.5, like the rest of these files. */
  ESP_AVRC_PT_CMD_SELECT = 0x00,
  ESP_AVRC_PT_CMD_UP = 0x01,
  ESP_AVRC_PT_CMD_DOWN = 0x02,
  ESP_AVRC_PT_CMD_LEFT = 0x03,
  ESP_AVRC_PT_CMD_RIGHT = 0x04,
  ESP_AVRC_PT_CMD_ROOT_MENU = 0x09,
  ESP_AVRC_PT_CMD_SETUP_MENU = 0x0A,
  ESP_AVRC_PT_CMD_CONT_MENU = 0x0B,
  ESP_AVRC_PT_CMD_FAV_MENU = 0x0C,
  ESP_AVRC_PT_CMD_EXIT = 0x0D,
  ESP_AVRC_PT_CMD_ENTER = 0x2B,
  ESP_AVRC_PT_CMD_CLEAR = 0x2C,
  ESP_AVRC_PT_CMD_CHAN_UP = 0x30,
  ESP_AVRC_PT_CMD_CHAN_DOWN = 0x31,
  ESP_AVRC_PT_CMD_PAGE_UP = 0x37,
  ESP_AVRC_PT_CMD_PAGE_DOWN = 0x38,
  ESP_AVRC_PT_CMD_POWER = 0x40,
  ESP_AVRC_PT_CMD_VOL_UP = 0x41,
  ESP_AVRC_PT_CMD_VOL_DOWN = 0x42,
  ESP_AVRC_PT_CMD_MUTE = 0x43,
  ESP_AVRC_PT_CMD_PLAY = 0x44,
  ESP_AVRC_PT_CMD_STOP = 0x45,
  ESP_AVRC_PT_CMD_PAUSE = 0x46,
  ESP_AVRC_PT_CMD_RECORD = 0x47,
  ESP_AVRC_PT_CMD_REWIND = 0x48,
  ESP_AVRC_PT_CMD_FAST_FORWARD = 0x49,
  ESP_AVRC_PT_CMD_EJECT = 0x4A,
  ESP_AVRC_PT_CMD_FORWARD = 0x4B,
  ESP_AVRC_PT_CMD_BACKWARD = 0x4C,
} esp_avrc_pt_cmd_t;

typedef enum {
  ESP_AVRC_PT_CMD_STATE_PRESSED = 0,
  ESP_AVRC_PT_CMD_STATE_RELEASED = 1,
} esp_avrc_pt_cmd_state_t;

typedef enum {
  ESP_AVRC_PSTH_FILTER_ALLOWED_CMD = 0,
  ESP_AVRC_PSTH_FILTER_SUPPORTED_CMD = 1,
  ESP_AVRC_PSTH_FILTER_SUPPORT_MAX,
} esp_avrc_psth_filter_t;

typedef struct {
  uint16_t bits[8];
} esp_avrc_psth_bit_mask_t;

typedef enum {
  ESP_AVRC_TG_CONNECTION_STATE_EVT = 0,
  ESP_AVRC_TG_REMOTE_FEATURES_EVT = 1,
  ESP_AVRC_TG_PASSTHROUGH_CMD_EVT = 2,
  ESP_AVRC_TG_SET_ABSOLUTE_VOLUME_CMD_EVT = 3,
  ESP_AVRC_TG_REGISTER_NOTIFICATION_EVT = 4,
  ESP_AVRC_TG_SET_PLAYER_APP_VALUE_EVT = 5,
  ESP_AVRC_TG_PROF_STATE_EVT = 6,
} esp_avrc_tg_cb_event_t;

typedef union {
  struct avrc_tg_conn_stat_param {
    bool connected;
    esp_bd_addr_t remote_bda;
  } conn_stat;

  struct avrc_tg_psth_cmd_param {
    uint8_t key_code;
    uint8_t key_state;
  } psth_cmd;

  struct avrc_tg_set_abs_vol_param {
    uint8_t volume;
  } set_abs_vol;
} esp_avrc_tg_cb_param_t;

typedef void (*esp_avrc_tg_cb_t)(esp_avrc_tg_cb_event_t event, esp_avrc_tg_cb_param_t *param);

esp_err_t esp_avrc_tg_register_callback(esp_avrc_tg_cb_t callback);
esp_err_t esp_avrc_tg_init(void);
esp_err_t esp_avrc_tg_deinit(void);
esp_err_t esp_avrc_tg_get_psth_cmd_filter(esp_avrc_psth_filter_t filter, esp_avrc_psth_bit_mask_t *cmd_set);
esp_err_t esp_avrc_tg_set_psth_cmd_filter(esp_avrc_psth_filter_t filter,
                                          const esp_avrc_psth_bit_mask_t *cmd_set);
