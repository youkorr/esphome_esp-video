#pragma once
// Stand-in, copied field for field from ESP-IDF v5.5.4
// components/bt/host/bluedroid/api/include/api/esp_hidh_api.h.
//
// THE FIELD THAT IS NOT THERE is the point of copying this rather than
// writing it from memory: ESP_HIDH_DATA_IND_EVT carries status, handle,
// proto_mode, len and data -- and NO report id, although every HID example in
// circulation prints one. A stub with an invented field would have let code
// that cannot build pass this check, which is the one thing it exists to stop.
#include "esp_bt_defs.h"
#include "esp_err.h"
#include <cstdint>

typedef enum {
  ESP_HIDH_OK,
  ESP_HIDH_HS_HID_NOT_READY,
  ESP_HIDH_ERR,
} esp_hidh_status_t;

typedef enum {
  ESP_HIDH_CONN_STATE_CONNECTED = 0,
  ESP_HIDH_CONN_STATE_CONNECTING,
  ESP_HIDH_CONN_STATE_DISCONNECTED,
  ESP_HIDH_CONN_STATE_DISCONNECTING,
  ESP_HIDH_CONN_STATE_UNKNOWN,
} esp_hidh_connection_state_t;

typedef enum {
  ESP_HIDH_BOOT_MODE = 0x00,
  ESP_HIDH_REPORT_MODE = 0x01,
} esp_hidh_protocol_mode_t;

typedef enum {
  ESP_HIDH_INIT_EVT = 0,
  ESP_HIDH_DEINIT_EVT,
  ESP_HIDH_OPEN_EVT,
  ESP_HIDH_CLOSE_EVT,
  ESP_HIDH_GET_RPT_EVT,
  ESP_HIDH_SET_RPT_EVT,
  ESP_HIDH_GET_PROTO_EVT,
  ESP_HIDH_SET_PROTO_EVT,
  ESP_HIDH_GET_IDLE_EVT,
  ESP_HIDH_SET_IDLE_EVT,
  ESP_HIDH_GET_DSCP_EVT,
  ESP_HIDH_ADD_DEV_EVT,
  ESP_HIDH_RMV_DEV_EVT,
  ESP_HIDH_VC_UNPLUG_EVT,
  ESP_HIDH_DATA_EVT,
  ESP_HIDH_DATA_IND_EVT,
  ESP_HIDH_SET_INFO_EVT,
} esp_hidh_cb_event_t;

typedef union {
  struct hidh_init_evt_param {
    esp_hidh_status_t status;
  } init;

  struct hidh_open_evt_param {
    esp_hidh_status_t status;
    esp_hidh_connection_state_t conn_status;
    bool is_orig;
    uint8_t handle;
    esp_bd_addr_t bd_addr;
  } open;

  struct hidh_close_evt_param {
    esp_hidh_status_t status;
    uint8_t reason;
    esp_hidh_connection_state_t conn_status;
    uint8_t handle;
  } close;

  struct hidh_data_ind_evt_param {
    esp_hidh_status_t status;
    uint8_t handle;
    esp_hidh_protocol_mode_t proto_mode;
    uint16_t len;
    uint8_t *data;
  } data_ind;

  /* ESP_HIDH_GET_DSCP_EVT -- the device's own report descriptor, which is
     what lets this component drive a gamepad nobody here owns. Copied field
     for field from ESP-IDF v5.5.5, like the rest of this file: an invented
     field here would let code that cannot build pass the only C++ check this
     repository has, which is a fault it has already recorded once. */
  struct hidh_get_dscp_evt_param {
    esp_hidh_status_t status;
    uint8_t handle;
    bool added;
    uint16_t vendor_id;
    uint16_t product_id;
    uint16_t version;
    uint16_t ssr_max_latency;
    uint16_t ssr_min_tout;
    uint8_t ctry_code;
    uint16_t dl_len;
    uint8_t *dsc_list;
  } dscp;
} esp_hidh_cb_param_t;

typedef void (*esp_hh_cb_t)(esp_hidh_cb_event_t event, esp_hidh_cb_param_t *param);

esp_err_t esp_bt_hid_host_register_callback(esp_hh_cb_t callback);
esp_err_t esp_bt_hid_host_init(void);
esp_err_t esp_bt_hid_host_deinit(void);
esp_err_t esp_bt_hid_host_connect(esp_bd_addr_t bd_addr);
esp_err_t esp_bt_hid_host_disconnect(esp_bd_addr_t bd_addr);
esp_err_t esp_bt_hid_host_virtual_cable_unplug(esp_bd_addr_t bd_addr);
