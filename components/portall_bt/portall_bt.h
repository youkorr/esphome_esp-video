#pragma once

#include "esphome/core/component.h"

struct usbh_hubport;
struct usb_endpoint_descriptor;

namespace esphome {
namespace portall_bt {

// What the board reports about one device that enumerated. Filled in by
// CherryUSB's event handler, which runs in CherryUSB's own task, and read by
// loop(). Only the two numbers needed to find the port again are carried:
// walking the descriptors is done on the ESPHome task, where logging is cheap
// and a slow log line costs nobody an interrupt.
struct Arrival {
  uint8_t hub_index;
  uint8_t hub_port;
};

class PortallBT : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  void set_high_speed(bool high_speed) { this->high_speed_ = high_speed; }
  void set_inquiry_seconds(uint16_t seconds) { this->inquiry_seconds_ = seconds; }
  void set_host_stack(bool wanted) { this->host_stack_ = wanted; }

  // Called from CherryUSB's task. Records the port and returns; everything
  // that can wait, waits.
  void on_usb_event(uint8_t hub_index, uint8_t hub_port, uint8_t event);

  // Runs in a task of its own, because every transfer it makes blocks until
  // the dongle answers and the ESPHome loop may not be stopped for that.
  void probe_hci(uint8_t hub_index, uint8_t hub_port, uint8_t intf_index);

 private:
  void report_(uint8_t hub_index, uint8_t hub_port);
  void inquire_(struct usbh_hubport *hport, uint8_t intf, struct usb_endpoint_descriptor *events);

  void try_host_stack_();

  // Attaches the transport, initialises Bluedroid and enables it, in that
  // order -- which is Espressif's, not a preference: the HCI driver has to be
  // attached before esp_bluedroid_init(). Called from the probe's task once a
  // controller has answered, because before that there is nothing to attach.
  void start_host_stack_(struct usbh_hubport *hport, uint8_t intf,
                         struct usb_endpoint_descriptor *events,
                         struct usb_endpoint_descriptor *acl_in,
                         struct usb_endpoint_descriptor *acl_out);

  uint16_t inquiry_seconds_{10};
  bool host_stack_{false};
  bool stack_up_{false};
  bool probing_{false};
  bool high_speed_{true};
  bool started_{false};

  // Single producer (CherryUSB's task), single consumer (loop). A ring this
  // small cannot overflow in practice -- a person plugs one dongle in -- but
  // it drops the NEWEST when it does, because the older entries are the ones
  // somebody is waiting to read about. That is the opposite of the choice
  // portall's touch queue makes, and for the opposite reason: a touch's last
  // event is the one that matters, an enumeration's first one is.
  static constexpr uint8_t QUEUE = 8;
  Arrival queue_[QUEUE];
  volatile uint8_t head_{0};
  volatile uint8_t tail_{0};
};

}  // namespace portall_bt
}  // namespace esphome
