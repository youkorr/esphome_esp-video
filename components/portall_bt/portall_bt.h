#pragma once

#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/core/preferences.h"

#include <vector>

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

/// One input report from a paired HID device, on its way to the ESPHome loop.
///
/// Bluedroid hands these over on its own BTC task and an ESPHome automation may
/// not run there, so they are queued and drained by loop() -- the same shape as
/// the arrival queue below, and for the same reason.
struct HidReport {
  uint8_t len;
  uint8_t data[63];
};

/// Which remote device plays which part, remembered across restarts.
///
/// This is NOT the pairing. Bluedroid keeps the link keys itself, in NVS, and
/// they survive a restart with nothing written here -- what it does not keep is
/// which of the bonded addresses is the gamepad and which is the speaker, and
/// nothing in its bond list says. So one small record of our own answers that,
/// and the two stores are separate on purpose: losing this one costs a
/// reconnection, losing Bluedroid's costs the pairing.
///
/// Both slots exist from the first version deliberately. An ESPHome preference
/// is found by a hash AND a size, so growing this struct later would silently
/// forget what every panel had stored -- and a panel that forgets its speaker
/// because the firmware gained a gamepad is exactly the kind of quiet loss this
/// project keeps paying for.
struct Remembered {
  uint8_t hid[6];
  uint8_t sink[6];
  bool has_hid;
  bool has_sink;
} __attribute__((packed));

class PortallBT : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  void set_high_speed(bool high_speed) { this->high_speed_ = high_speed; }
  void set_inquiry_seconds(uint16_t seconds) { this->inquiry_seconds_ = seconds; }
  void set_host_stack(bool wanted) { this->host_stack_ = wanted; }
  void set_hid_host(bool wanted) { this->hid_host_ = wanted; }
  void set_a2dp(bool wanted) { this->a2dp_ = wanted; }
  void set_test_tone(uint16_t hz) { this->test_tone_hz_ = hz; }

  /* A2DP source, defined in a2dp.cpp.
   *
   * fill_pcm runs on BLUEDROID's own A2DP task and is the clock for the whole
   * path: it is called when the encoder is about to want bytes, for exactly
   * the number it wants. 44100 Hz, signed 16-bit little-endian, TWO channels
   * interleaved -- not a preference, a hardcoded constant in
   * btc_a2dp_source.c with a comment saying as much. */
  uint32_t fill_pcm(uint8_t *buf, uint32_t len);
  /// Hand PCM to the speaker, in that same format. Safe from any task.
  void feed_audio(const uint8_t *data, uint32_t len);
  void on_a2dp_ready();
  void on_a2dp_open(const uint8_t *addr);
  void on_a2dp_closed(bool abnormal);
  void on_a2dp_audio(bool started);
  void set_device_name(const char *name) { this->device_name_ = name; }
  void set_pair_seconds(uint16_t seconds) { this->pair_seconds_ = seconds; }
  void set_show_reports(bool wanted) { this->show_reports_ = wanted; }
  void add_hid_report_trigger(Trigger<std::vector<uint8_t>> *trigger) {
    this->hid_report_triggers_.push_back(trigger);
  }

  /* Called from Bluedroid's own task, so each one does as little as it can.
   *
   * ESP_HIDH_DATA_IND_EVT carries a status, a handle, the protocol mode, a
   * length and a pointer -- and NO report id, which was worth checking rather
   * than assuming, because every HID example prints one. Where a device uses
   * report ids at all, the first byte of the data IS the id; where it does not,
   * inventing a field would have put a number in the log that means nothing.
   * So what comes out of here is exactly what the device sent. */
  void on_hid_ready();
  void on_hid_open(const uint8_t *addr);
  void on_hid_closed();
  void on_hid_report(const uint8_t *data, uint16_t len);

  /// Look for something to pair with, once, because somebody asked.
  ///
  /// This is the one operation here that runs an INQUIRY, and an inquiry sweeps
  /// the whole 2.4 GHz band at full power -- measured twice on a Tab5, it takes
  /// the Wi-Fi down for exactly as long as it runs and the link comes back the
  /// second it ends. On a panel fed over Wi-Fi that is the picture stopping. So
  /// it is an action somebody invokes and never a loop, never on boot: once
  /// paired, everything below reconnects by ADDRESS, which needs no inquiry at
  /// all. Remembering the device and not killing the Wi-Fi are the same thing.
  /// Whether this panel is looking for a speaker / an input device at all.
  /// Read by the pairing callback, which has one scan and two kinds of
  /// device to sort it into.
  bool wants_speaker() const { return this->a2dp_; }
  bool wants_input() const { return this->hid_host_; }

  void pair();
  /// Forget every bonded device, both stores. The way back from a gamepad
  /// somebody has given away.
  void forget();

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

  // Defined in hid.cpp, and compiled away with it.
  void start_profiles_();
  void drain_reports_();
  void reconnect_tick_();
  void remember_hid_(const uint8_t *addr);
  void load_remembered_();

  void hid_reconnect_();

  // Defined in a2dp.cpp.
  void start_a2dp_();
  void remember_sink_(const uint8_t *addr);
  void a2dp_reconnect_();

  // Attaches the transport, initialises Bluedroid and enables it, in that
  // order -- which is Espressif's, not a preference: the HCI driver has to be
  // attached before esp_bluedroid_init(). Called from the probe's task once a
  // controller has answered, because before that there is nothing to attach.
  void start_host_stack_(struct usbh_hubport *hport, uint8_t intf,
                         struct usb_endpoint_descriptor *events,
                         struct usb_endpoint_descriptor *acl_in,
                         struct usb_endpoint_descriptor *acl_out);

  uint16_t inquiry_seconds_{10};
  uint16_t pair_seconds_{10};
  const char *device_name_{"portall"};
  bool host_stack_{false};
  bool hid_host_{false};
  bool a2dp_{false};
  bool a2dp_up_{false};
  bool a2dp_open_{false};
  bool a2dp_playing_{false};
  // 0 is off. A tone exists so the path can be proved before there is
  // anything real to play through it -- tools/playsound.py made the same
  // choice for the panel's own speaker, for the same reason.
  uint16_t test_tone_hz_{0};
  uint32_t pcm_starved_{0};
  uint32_t pcm_dropped_{0};
  bool profiles_up_{false};
  // When the next reconnection attempt is due, and how long to wait after the
  // one after that. A device that is switched off must not be asked for
  // sixty times a second, and a device that has just been switched on should
  // not wait a minute.
  uint32_t reconnect_due_ms_{0};
  uint32_t reconnect_backoff_ms_{0};
  bool hid_open_{false};
  Remembered remembered_{};
  ESPPreferenceObject remembered_pref_;
  bool show_reports_{false};
  std::vector<Trigger<std::vector<uint8_t>> *> hid_report_triggers_;
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

  // Input reports, filled on Bluedroid's task and emptied by loop().
  //
  // THIS ONE DROPS THE OLDEST, which is the opposite of the queue above, and
  // the reason is the opposite too. An enumeration's FIRST event is the one
  // somebody is waiting to read about; a button's LAST event is the release,
  // and a release that never arrives leaves a key held down for ever. portall's
  // touch queue learned that at a cost of twenty seconds of apparent latency,
  // and a finger and a thumb are the same problem.
  static constexpr uint8_t REPORTS = 32;
  HidReport reports_[REPORTS];
  volatile uint8_t report_head_{0};
  volatile uint8_t report_tail_{0};
  uint32_t reports_lost_{0};
};

/* portall_bt.pair and portall_bt.forget.
 *
 * `void play(const Ts &...) override` is not a style: the base declares
 * `virtual void play(const Ts &...x) = 0`, and taking the pack BY VALUE
 * compiles perfectly as a NON-override that is never called. portall shipped
 * exactly that once, and nothing catches it -- `esphome config` validates the
 * YAML and the codegen and never compiles a line of C++. */
template<typename... Ts> class PairAction final : public Action<Ts...>, public Parented<PortallBT> {
 public:
  void play(const Ts &...) override { this->parent_->pair(); }
};

template<typename... Ts> class ForgetAction final : public Action<Ts...>, public Parented<PortallBT> {
 public:
  void play(const Ts &...) override { this->parent_->forget(); }
};

}  // namespace portall_bt
}  // namespace esphome
