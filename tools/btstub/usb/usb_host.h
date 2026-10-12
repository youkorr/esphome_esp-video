// Stand-in for ESP-IDF v5.5.5 components/usb/include/usb/usb_host.h
// (Apache-2.0, Espressif), copied as it is there for what portall_bt's own USB
// layer calls. See usb_types_ch9.h.
#pragma once
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "esp_err.h"
#include "esp_intr_alloc.h"
#include "usb/usb_helpers.h"
#include "usb/usb_types_ch9.h"
#include "usb/usb_types_stack.h"
#ifdef __cplusplus
extern "C" {
#endif

typedef struct usb_host_client_handle_s *usb_host_client_handle_t;

#define USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS 0x01
#define USB_HOST_LIB_EVENT_FLAGS_ALL_FREE 0x02

typedef enum {
  USB_HOST_CLIENT_EVENT_NEW_DEV,
  USB_HOST_CLIENT_EVENT_DEV_GONE,
} usb_host_client_event_t;

typedef struct {
  usb_host_client_event_t event;
  union {
    struct {
      uint8_t address;
    } new_dev;
    struct {
      usb_device_handle_t dev_hdl;
    } dev_gone;
  };
} usb_host_client_event_msg_t;

typedef void (*usb_host_client_event_cb_t)(const usb_host_client_event_msg_t *event_msg, void *arg);

typedef struct {
  bool skip_phy_setup;
  bool root_port_unpowered;
  int intr_flags;
  usb_host_enum_filter_cb_t enum_filter_cb;
  struct {
    uint32_t nptx_fifo_lines;
    uint32_t ptx_fifo_lines;
    uint32_t rx_fifo_lines;
  } fifo_settings_custom;
  unsigned peripheral_map;
} usb_host_config_t;

typedef struct {
  bool is_synchronous;
  int max_num_event_msg;
  union {
    struct {
      usb_host_client_event_cb_t client_event_callback;
      void *callback_arg;
    } async;
  };
} usb_host_client_config_t;

esp_err_t usb_host_install(const usb_host_config_t *config);
esp_err_t usb_host_lib_handle_events(TickType_t timeout_ticks, uint32_t *event_flags_ret);
esp_err_t usb_host_client_register(const usb_host_client_config_t *client_config,
                                   usb_host_client_handle_t *client_hdl_ret);
esp_err_t usb_host_client_handle_events(usb_host_client_handle_t client_hdl, TickType_t timeout_ticks);
esp_err_t usb_host_device_open(usb_host_client_handle_t client_hdl, uint8_t dev_addr,
                               usb_device_handle_t *dev_hdl_ret);
esp_err_t usb_host_device_close(usb_host_client_handle_t client_hdl, usb_device_handle_t dev_hdl);
esp_err_t usb_host_device_info(usb_device_handle_t dev_hdl, usb_device_info_t *dev_info);
esp_err_t usb_host_get_device_descriptor(usb_device_handle_t dev_hdl, const usb_device_desc_t **device_desc);
esp_err_t usb_host_get_active_config_descriptor(usb_device_handle_t dev_hdl, const usb_config_desc_t **config_desc);
esp_err_t usb_host_interface_claim(usb_host_client_handle_t client_hdl, usb_device_handle_t dev_hdl,
                                   uint8_t bInterfaceNumber, uint8_t bAlternateSetting);
esp_err_t usb_host_interface_release(usb_host_client_handle_t client_hdl, usb_device_handle_t dev_hdl,
                                     uint8_t bInterfaceNumber);
esp_err_t usb_host_endpoint_halt(usb_device_handle_t dev_hdl, uint8_t bEndpointAddress);
esp_err_t usb_host_endpoint_flush(usb_device_handle_t dev_hdl, uint8_t bEndpointAddress);
esp_err_t usb_host_endpoint_clear(usb_device_handle_t dev_hdl, uint8_t bEndpointAddress);
esp_err_t usb_host_transfer_alloc(size_t data_buffer_size, int num_isoc_packets, usb_transfer_t **transfer);
esp_err_t usb_host_transfer_free(usb_transfer_t *transfer);
esp_err_t usb_host_transfer_submit(usb_transfer_t *transfer);
esp_err_t usb_host_transfer_submit_control(usb_host_client_handle_t client_hdl, usb_transfer_t *transfer);

#ifdef __cplusplus
}
#endif
