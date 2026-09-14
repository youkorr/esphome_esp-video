#pragma once
// Stand-in, copied field for field from ESP-IDF v5.5.4
// components/bt/host/bluedroid/api/include/api/esp_gap_bt_api.h.
//
// The shapes that matter and were checked rather than remembered:
//   - disc_res carries `int num_prop` and `esp_bt_gap_dev_prop_t *prop`, so a
//     property is found by WALKING them, never by index.
//   - auth_cmpl.device_name is a fixed array in the struct, not a pointer.
//   - esp_bt_gap_get_bond_device_list takes `int *dev_num` IN and OUT.
#include "esp_bt_defs.h"
#include "esp_err.h"
#include <cstdint>

typedef enum {
  ESP_BT_NON_CONNECTABLE,
  ESP_BT_CONNECTABLE,
} esp_bt_connection_mode_t;

typedef enum {
  ESP_BT_NON_DISCOVERABLE,
  ESP_BT_LIMITED_DISCOVERABLE,
  ESP_BT_GENERAL_DISCOVERABLE,
} esp_bt_discovery_mode_t;

typedef enum {
  ESP_BT_GAP_DEV_PROP_BDNAME = 1,
  ESP_BT_GAP_DEV_PROP_COD,
  ESP_BT_GAP_DEV_PROP_RSSI,
  ESP_BT_GAP_DEV_PROP_EIR,
} esp_bt_gap_dev_prop_type_t;

#define ESP_BT_GAP_MAX_BDNAME_LEN 248

typedef struct {
  esp_bt_gap_dev_prop_type_t type;
  int len;
  void *val;
} esp_bt_gap_dev_prop_t;

typedef enum {
  ESP_BT_COD_MAJOR_DEV_MISC = 0,
  ESP_BT_COD_MAJOR_DEV_COMPUTER = 1,
  ESP_BT_COD_MAJOR_DEV_PHONE = 2,
  ESP_BT_COD_MAJOR_DEV_LAN_NAP = 3,
  ESP_BT_COD_MAJOR_DEV_AV = 4,
  ESP_BT_COD_MAJOR_DEV_PERIPHERAL = 5,
  ESP_BT_COD_MAJOR_DEV_UNCATEGORIZED = 31,
} esp_bt_cod_major_dev_t;

#define ESP_BT_COD_MAJOR_DEV_BIT_MASK 0x1f00
#define ESP_BT_COD_MAJOR_DEV_BIT_OFFSET 8

static inline uint32_t esp_bt_gap_get_cod_major_dev(uint32_t cod) {
  return (cod & ESP_BT_COD_MAJOR_DEV_BIT_MASK) >> ESP_BT_COD_MAJOR_DEV_BIT_OFFSET;
}

#define ESP_BT_PIN_CODE_LEN 16
typedef uint8_t esp_bt_pin_code_t[ESP_BT_PIN_CODE_LEN];

#define ESP_BT_IO_CAP_OUT 0
#define ESP_BT_IO_CAP_IO 1
#define ESP_BT_IO_CAP_IN 2
#define ESP_BT_IO_CAP_NONE 3
typedef uint8_t esp_bt_io_cap_t;

typedef enum {
  ESP_BT_SP_IOCAP_MODE = 0,
} esp_bt_sp_param_t;

typedef enum {
  ESP_BT_GAP_DISCOVERY_STOPPED,
  ESP_BT_GAP_DISCOVERY_STARTED,
} esp_bt_gap_discovery_state_t;

typedef enum {
  ESP_BT_INQ_MODE_GENERAL_INQUIRY,
  ESP_BT_INQ_MODE_LIMITED_INQUIRY,
} esp_bt_inq_mode_t;

typedef enum { ESP_BT_LINK_KEY_COMB = 0 } esp_bt_link_key_type_t;

typedef enum {
  ESP_BT_GAP_DISC_RES_EVT = 0,
  ESP_BT_GAP_DISC_STATE_CHANGED_EVT,
  ESP_BT_GAP_RMT_SRVCS_EVT,
  ESP_BT_GAP_RMT_SRVC_REC_EVT,
  ESP_BT_GAP_AUTH_CMPL_EVT,
  ESP_BT_GAP_PIN_REQ_EVT,
  ESP_BT_GAP_CFM_REQ_EVT,
  ESP_BT_GAP_KEY_NOTIF_EVT,
  ESP_BT_GAP_KEY_REQ_EVT,
  ESP_BT_GAP_EVT_MAX,
} esp_bt_gap_cb_event_t;

typedef union {
  struct disc_res_param {
    esp_bd_addr_t bda;
    int num_prop;
    esp_bt_gap_dev_prop_t *prop;
  } disc_res;

  struct disc_state_changed_param {
    esp_bt_gap_discovery_state_t state;
  } disc_st_chg;

  struct auth_cmpl_param {
    esp_bd_addr_t bda;
    esp_bt_status_t stat;
    esp_bt_link_key_type_t lk_type;
    uint8_t device_name[ESP_BT_GAP_MAX_BDNAME_LEN + 1];
  } auth_cmpl;

  struct pin_req_param {
    esp_bd_addr_t bda;
    bool min_16_digit;
  } pin_req;

  struct cfm_req_param {
    esp_bd_addr_t bda;
    uint32_t num_val;
  } cfm_req;
} esp_bt_gap_cb_param_t;

typedef void (*esp_bt_gap_cb_t)(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t *param);

esp_err_t esp_bt_gap_register_callback(esp_bt_gap_cb_t callback);
esp_err_t esp_bt_gap_set_scan_mode(esp_bt_connection_mode_t c_mode, esp_bt_discovery_mode_t d_mode);
esp_err_t esp_bt_gap_start_discovery(esp_bt_inq_mode_t mode, uint8_t inq_len, uint8_t num_rsps);
esp_err_t esp_bt_gap_cancel_discovery(void);
esp_err_t esp_bt_gap_set_device_name(const char *name);
esp_err_t esp_bt_gap_remove_bond_device(esp_bd_addr_t bd_addr);
int esp_bt_gap_get_bond_device_num(void);
esp_err_t esp_bt_gap_get_bond_device_list(int *dev_num, esp_bd_addr_t *dev_list);
esp_err_t esp_bt_gap_set_security_param(esp_bt_sp_param_t param_type, void *value, uint8_t len);
esp_err_t esp_bt_gap_ssp_confirm_reply(esp_bd_addr_t bd_addr, bool accept);
esp_err_t esp_bt_gap_pin_reply(esp_bd_addr_t bd_addr, bool accept, uint8_t pin_code_len,
                               esp_bt_pin_code_t pin_code);
