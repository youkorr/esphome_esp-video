#pragma once
/* Every symbol the component calls that a test on a workstation cannot have.
 *
 * One copy, included by each test in tools/bttest/, because two copies of a
 * list like this drift the moment somebody adds a profile -- and the whole
 * point of linking against the real sources is that the LINKER is asked
 * whether a declaration still matches its definition. A second, stale copy
 * would answer that question about the wrong list.
 *
 * Include this from exactly one translation unit per test binary: these are
 * definitions, not declarations.
 */

// Stubs for everything the component calls and this test does not: it is
// linked, not run, apart from the two length functions.
int usbh_submit_urb(struct usbh_urb *) { return -1; }
int usbh_set_interface(struct usbh_hubport *, uint8_t, uint8_t) { return 0; }
int usbh_control_transfer(struct usbh_hubport *, struct usb_setup_packet *, uint8_t *) { return 0; }
int usbh_initialize(uint8_t, uintptr_t, usbh_event_handler_t) { return 0; }
struct usbh_hubport *usbh_find_hubport(uint8_t, uint8_t, uint8_t) { return nullptr; }
int xTaskCreate(void (*)(void *), const char *, unsigned, void *, unsigned, TaskHandle_t *) { return 1; }
void vTaskDelete(TaskHandle_t) {}
unsigned xTaskGetTickCount(void) { return 0; }
void vTaskDelay(unsigned) {}
esp_bluedroid_status_t esp_bluedroid_get_status(void) { return ESP_BLUEDROID_STATUS_ENABLED; }
esp_err_t esp_bluedroid_enable(void) { return ESP_OK; }
esp_err_t esp_bluedroid_disable(void) { return ESP_OK; }
esp_err_t esp_bluedroid_init(void) { return ESP_OK; }
esp_err_t esp_bluedroid_deinit(void) { return ESP_OK; }
esp_err_t esp_bluedroid_attach_hci_driver(const esp_bluedroid_hci_driver_operations_t *) { return ESP_OK; }
esp_err_t esp_bluedroid_detach_hci_driver(void) { return ESP_OK; }
const uint8_t *esp_bt_dev_get_address(void) { return nullptr; }

// And hid.cpp's half. It is linked here rather than mocked because a
// declaration that no longer matches its definition is exactly the fault this
// whole tool was built to catch -- so the linker is asked the question too.
namespace esphome {
uint32_t millis() { return 0; }
uint32_t fnv1_hash(const char *) { return 0; }
static ESPPreferences preferences_stub;
ESPPreferences *global_preferences = &preferences_stub;
}  // namespace esphome
esp_err_t esp_bt_gap_register_callback(esp_bt_gap_cb_t) { return ESP_OK; }
esp_err_t esp_bt_gap_set_scan_mode(esp_bt_connection_mode_t, esp_bt_discovery_mode_t) { return ESP_OK; }
esp_err_t esp_bt_gap_start_discovery(esp_bt_inq_mode_t, uint8_t, uint8_t) { return ESP_OK; }
esp_err_t esp_bt_gap_cancel_discovery(void) { return ESP_OK; }
esp_err_t esp_bt_gap_set_device_name(const char *) { return ESP_OK; }
esp_err_t esp_bt_gap_remove_bond_device(esp_bd_addr_t) { return ESP_OK; }
int esp_bt_gap_get_bond_device_num(void) { return 0; }
esp_err_t esp_bt_gap_get_bond_device_list(int *dev_num, esp_bd_addr_t *) {
  *dev_num = 0;
  return ESP_OK;
}
esp_err_t esp_bt_gap_set_security_param(esp_bt_sp_param_t, void *, uint8_t) { return ESP_OK; }
esp_err_t esp_bt_gap_ssp_confirm_reply(esp_bd_addr_t, bool) { return ESP_OK; }
esp_err_t esp_bt_gap_pin_reply(esp_bd_addr_t, bool, uint8_t, esp_bt_pin_code_t) { return ESP_OK; }
esp_err_t esp_bt_hid_host_register_callback(esp_hh_cb_t) { return ESP_OK; }
esp_err_t esp_bt_hid_host_init(void) { return ESP_OK; }
esp_err_t esp_bt_hid_host_deinit(void) { return ESP_OK; }
esp_err_t esp_bt_hid_host_connect(esp_bd_addr_t) { return ESP_OK; }
esp_err_t esp_bt_hid_host_disconnect(esp_bd_addr_t) { return ESP_OK; }
esp_err_t esp_bt_hid_host_virtual_cable_unplug(esp_bd_addr_t) { return ESP_OK; }

#if defined(CONFIG_BT_A2DP_ENABLE)
#include "esp_a2dp_api.h"
#include "esp_avrc_api.h"

/* The A2DP source and the AVRCP target, for the tests that build a2dp.cpp with
 * its profile switched on. `esp_a2d_source_register_data_callback` is the
 * deprecated pull API -- declared in esp_a2dp_legacy_api.h, which
 * esp_a2dp_api.h includes unconditionally, a fact this project got wrong once
 * by grepping a single file. */
esp_err_t esp_a2d_register_callback(esp_a2d_cb_t) { return ESP_OK; }
esp_err_t esp_a2d_source_register_data_callback(esp_a2d_source_data_cb_t) { return ESP_OK; }
esp_err_t esp_a2d_source_init(void) { return ESP_OK; }
esp_err_t esp_a2d_source_deinit(void) { return ESP_OK; }
esp_err_t esp_a2d_source_connect(esp_bd_addr_t) { return ESP_OK; }
esp_err_t esp_a2d_source_disconnect(esp_bd_addr_t) { return ESP_OK; }
esp_err_t esp_a2d_media_ctrl(esp_a2d_media_ctrl_t) { return ESP_OK; }
esp_err_t esp_avrc_tg_init(void) { return ESP_OK; }
esp_err_t esp_avrc_tg_deinit(void) { return ESP_OK; }
esp_err_t esp_avrc_tg_register_callback(esp_avrc_tg_cb_t) { return ESP_OK; }
esp_err_t esp_avrc_tg_get_psth_cmd_filter(esp_avrc_psth_filter_t, esp_avrc_psth_bit_mask_t *) { return ESP_OK; }
esp_err_t esp_avrc_tg_set_psth_cmd_filter(esp_avrc_psth_filter_t, const esp_avrc_psth_bit_mask_t *) {
  return ESP_OK;
}
#endif
