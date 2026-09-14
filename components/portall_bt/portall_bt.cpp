#include "portall_bt.h"

#ifdef USE_ESP32

#include "esphome/core/log.h"

// CherryUSB comes from Espressif's component registry -- __init__.py writes it
// into idf_component.yml -- so these resolve only in an ESP-IDF build with
// that dependency present. usbh_core.h pulls CherryUSB's own usb_config.h,
// which is where ESP_USB_HS0_BASE and ESP_USB_FS0_BASE are defined, so the
// register addresses are never repeated here.
#include "usbh_core.h"

namespace esphome {
namespace portall_bt {

static const char *const TAG = "portall_bt";

// CherryUSB takes a plain C function pointer and gives no user pointer with
// it, so the instance has to be reachable from a file-scope variable. There is
// one host bus and one component; the schema does not allow a second.
static PortallBT *g_instance = nullptr;

static void usb_event_handler(uint8_t busid, uint8_t hub_index, uint8_t hub_port, uint8_t intf,
                              uint8_t event) {
  (void) busid;
  (void) intf;
  if (g_instance != nullptr) {
    g_instance->on_usb_event(hub_index, hub_port, event);
  }
}

static const char *speed_name(uint8_t speed) {
  switch (speed) {
    case USB_SPEED_LOW:
      return "low";
    case USB_SPEED_FULL:
      return "full";
    case USB_SPEED_HIGH:
      return "high";
    default:
      return "unknown";
  }
}

void PortallBT::setup() {
  g_instance = this;

  // Which register block is which is CherryUSB's to say. The high-speed one is
  // the default because that is where the sockets are: the Tab5's USB-A host
  // port is on it. Note what the port glue does with this choice -- the PHY is
  // configured for high speed on HS0 and full speed otherwise, so a full-speed
  // dongle on the high-speed port has to negotiate down, and that is exactly
  // the case this first firmware exists to test.
  const uintptr_t base = this->high_speed_ ? ESP_USB_HS0_BASE : ESP_USB_FS0_BASE;

  const int err = usbh_initialize(0, base, usb_event_handler);
  if (err != 0) {
    // Not mark_failed(): a board whose host will not start should still boot,
    // say so once, and let everything else on it work. This component is an
    // accessory and must never cost the rest of the firmware.
    ESP_LOGE(TAG, "The USB host would not start on the %s controller (error %d)",
             this->high_speed_ ? "high-speed" : "full-speed", err);
    return;
  }

  this->started_ = true;
  ESP_LOGI(TAG, "USB host running on the %s controller; waiting for a device",
           this->high_speed_ ? "high-speed" : "full-speed");
}

void PortallBT::on_usb_event(uint8_t hub_index, uint8_t hub_port, uint8_t event) {
  // CONFIGURED rather than CONNECTED: the descriptors are only filled in once
  // the device has been enumerated and a configuration chosen, and the
  // descriptors are the whole point of this firmware.
  if (event != USBH_EVENT_DEVICE_CONFIGURED) {
    return;
  }

  const uint8_t next = (uint8_t) ((this->head_ + 1) % QUEUE);
  if (next == this->tail_) {
    return;  // full: keep what is already waiting to be read
  }
  this->queue_[this->head_].hub_index = hub_index;
  this->queue_[this->head_].hub_port = hub_port;
  this->head_ = next;
}

void PortallBT::loop() {
  while (this->tail_ != this->head_) {
    const Arrival arrival = this->queue_[this->tail_];
    this->tail_ = (uint8_t) ((this->tail_ + 1) % QUEUE);
    this->report_(arrival.hub_index, arrival.hub_port);
  }
}

void PortallBT::report_(uint8_t hub_index, uint8_t hub_port) {
  struct usbh_hubport *hport = usbh_find_hubport(0, hub_index, hub_port);
  if (hport == nullptr || !hport->connected) {
    // It was unplugged between the event and this line, which is a race a
    // person can lose by being quick, not a fault.
    ESP_LOGW(TAG, "a device enumerated on hub %u port %u and was gone before it could be read",
             hub_index, hub_port);
    return;
  }

  ESP_LOGI(TAG, "device %u on bus 0: %04x:%04x, %s speed", hport->dev_addr,
           hport->device_desc.idVendor, hport->device_desc.idProduct, speed_name(hport->speed));
  if (hport->iManufacturer != nullptr || hport->iProduct != nullptr) {
    ESP_LOGI(TAG, "  %s %s", hport->iManufacturer != nullptr ? hport->iManufacturer : "",
             hport->iProduct != nullptr ? hport->iProduct : "");
  }

  uint8_t interfaces = hport->config.config_desc.bNumInterfaces;
  if (interfaces > CONFIG_USBHOST_MAX_INTERFACES) {
    // The descriptor is the device's word for how many it has; the array is
    // ours. Believing the device over our own array is how a stack walks off
    // the end of one.
    ESP_LOGW(TAG, "  it claims %u interfaces and only %d were stored", interfaces,
             CONFIG_USBHOST_MAX_INTERFACES);
    interfaces = CONFIG_USBHOST_MAX_INTERFACES;
  }

  bool bluetooth = false;
  for (uint8_t i = 0; i < interfaces; i++) {
    const struct usb_interface_descriptor *desc = &hport->config.intf[i].altsetting[0].intf_desc;
    ESP_LOGI(TAG, "  interface %u: class %02x subclass %02x protocol %02x", i,
             desc->bInterfaceClass, desc->bInterfaceSubClass, desc->bInterfaceProtocol);
    // E0/01/01 is the Bluetooth primary controller, and it is the exact triple
    // CherryUSB's own bluetooth_class_info matches on -- so saying it out loud
    // here turns the next step from a guess into a yes or no.
    if (desc->bInterfaceClass == 0xE0 && desc->bInterfaceSubClass == 0x01 &&
        desc->bInterfaceProtocol == 0x01) {
      bluetooth = true;
    }
  }

  if (bluetooth) {
    ESP_LOGI(TAG, "  this is a Bluetooth controller, and CherryUSB's class driver would bind to it");
  }
}

void PortallBT::dump_config() {
  ESP_LOGCONFIG(TAG, "Portall Bluetooth:");
  ESP_LOGCONFIG(TAG, "  Controller: %s", this->high_speed_ ? "high-speed" : "full-speed");
  ESP_LOGCONFIG(TAG, "  USB host: %s", this->started_ ? "running" : "NOT running");
  if (this->high_speed_) {
    // Worth saying on every boot rather than in documentation nobody reads:
    // this is the controller portall puts in device mode, so the two cannot
    // share it and this is meant to be a firmware of its own for now.
    ESP_LOGCONFIG(TAG, "  This controller is the one portall uses for USB device mode.");
  }
  ESP_LOGCONFIG(TAG, "  The host supplies 5V: on the Tab5 that is the second PI4IOE5V6408,");
  ESP_LOGCONFIG(TAG, "  bit 3 (usb_5v_power), which this component does not touch.");
}

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_ESP32
