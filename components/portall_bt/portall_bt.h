#pragma once

#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/core/preferences.h"

#include <functional>
#include <string>
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

/// One field of one HID input report: which bits, and what they mean.
///
/// Built by walking the device's own report descriptor, so nothing here is a
/// byte offset anybody typed. The previous version of this component DID type
/// them -- a hat at the high nibble of byte 2, face buttons at byte 3, from a
/// measurement of one controller -- and a panel reported back that two of the
/// three were wrong. See the head of hid_descriptor.cpp.
struct HidField {
  uint16_t usage_page;
  uint16_t usage;      ///< for an array field, the FIRST usage of its range
  uint16_t usage_max;  ///< the last, for an array; equal to usage otherwise
  uint16_t bit_offset; ///< within the payload, after any report-id byte
  uint8_t report_id;   ///< 0 when the descriptor declares none
  uint8_t bit_size;
  int32_t logical_min;
  int32_t logical_max;
  /// An array field holds an INDEX into its usage range rather than a value --
  /// how a keyboard sends six keys in six bytes instead of a bit per key.
  bool array;
};

/// A device's report descriptor, parsed once, and the decode that follows.
///
/// Bluedroid hands the descriptor over whole on ESP_HIDH_GET_DSCP_EVT, so this
/// costs one parse per connection and a walk of a small table per report.
///
/// The limits are flat arrays rather than allocations because this is read
/// from Bluedroid's own task: sixty-four fields covers a gamepad with two
/// sticks, two triggers, a hat and sixteen buttons with room over, and a
/// descriptor larger than that is TRUNCATED and says so rather than being
/// silently half-read.
class HidReportMap {
 public:
  /* SIXTY-FOUR WAS NOT ENOUGH, AND A REAL CONTROLLER SAID SO IN ONE LINE:
     "input device 0955:7214 described itself: 379 bytes, 64 fields (and more
     than this can hold)". An NVIDIA Shield declares a gamepad, a consumer
     report, a battery, NVIDIA's own host-command channel and more besides, so
     a number chosen to cover "two sticks, two triggers, a hat and sixteen
     buttons" covered the first collection and silently dropped the rest.
     Whatever fell past the cap simply did not exist, which on a report this
     has no fields for reads as a device sending something unreadable.

     192 costs under four kilobytes and covers that descriptor with room over.
     It is still a cap, so `wanted` counts what the descriptor really declared
     and the log prints both -- a limit that cannot say how far short it fell
     is a limit nobody can raise correctly, which is what the first version of
     this line was. */
  static constexpr uint16_t MAX_FIELDS = 192;
  static constexpr uint8_t MAX_REPORT_IDS = 16;
  static constexpr uint8_t MAX_LOCAL_USAGES = 64;

  /// Walk a report descriptor. True when it yielded at least one field.
  bool parse(const uint8_t *desc, uint16_t len);
  void clear();

  bool ready() const { return this->count_ > 0; }
  uint16_t field_count() const { return this->count_; }
  /// How many input fields the descriptor declared, which is what field_count
  /// would be if nothing had been dropped.
  uint16_t wanted_fields() const { return this->wanted_; }
  const HidField &field(uint16_t n) const { return this->fields_[n]; }
  /// Whether the descriptor was bigger than this could hold. Worth saying out
  /// loud: a truncated map decodes the fields it kept perfectly and simply
  /// never mentions the rest, which is exactly the silent half-answer this
  /// repository keeps having to dig out of a log.
  bool truncated() const { return this->truncated_; }
  /// Whether the descriptor declared report ids at all -- which decides
  /// whether a report's first byte is an id or the first field's bits.
  bool uses_ids() const { return this->ids_; }

  /// Hand every field of this report to fn, with the value it carries.
  bool decode(const uint8_t *report, uint16_t len,
              const std::function<void(const HidField &, int32_t)> &fn) const;

  static bool field_value(const HidField &f, const uint8_t *payload, uint16_t len,
                          int32_t *out);

 protected:
  HidField fields_[MAX_FIELDS]{};
  uint16_t count_{0};
  uint16_t wanted_{0};
  bool ids_{false};
  bool truncated_{false};
};

/// One button press from the speaker, on its way to the ESPHome loop.
///
/// A car receiver's steering-wheel buttons, a headphone's play/pause, a
/// volume knob. Queued rather than fired where it arrives, for the reason
/// every queue in this component exists: Bluedroid's callbacks run on its own
/// task and an ESPHome automation may not.
struct MediaKey {
  uint8_t code;
  bool pressed;
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
  /// How many bytes are waiting to be encoded. The speaker platform answers
  /// has_buffered_data() with this.
  uint32_t pcm_queued() const;
  /// Whether a sink is connected. PCM handed over with nothing at the other
  /// end has nowhere to go, and a speaker platform that says so in its own
  /// log is better than one that quietly fills a ring for ever.
  bool speaker_connected() const { return this->a2dp_open_; }
  void on_a2dp_ready();
  void on_a2dp_open(const uint8_t *addr);
  void on_a2dp_closed(bool abnormal);
  void on_a2dp_audio(bool started);
  /// AVRCP, from the speaker's own buttons. `code` is an esp_avrc_pt_cmd_t --
  /// 0x44 play, 0x46 pause, 0x4B next, 0x4C previous -- and the volume is a
  /// fraction, because AVRCP carries 0..127 and nobody wants to know that.
  void on_media_key(uint8_t code, bool pressed);
  void on_media_volume(float fraction);

  void add_media_key_trigger(Trigger<uint8_t, bool> *trigger) {
    this->media_key_triggers_.push_back(trigger);
  }
  void add_media_volume_trigger(Trigger<float> *trigger) {
    this->media_volume_triggers_.push_back(trigger);
  }
  void set_device_name(const char *name) { this->device_name_ = name; }
  void set_pair_seconds(uint16_t seconds) { this->pair_seconds_ = seconds; }
  void set_show_reports(bool wanted) { this->show_reports_ = wanted; }

  /* Where a decoded key goes -- portall, so it reaches the page the panel is
     showing.
   *
   * A std::function AND NOT A POINTER TO Portall, which is the whole reason
   * this compiles: portall_bt alone is a whole firmware and a board carrying
   * only it must still build, so this component includes portall's header
   * nowhere. Codegen emits the lambda into main.cpp, where both components'
   * headers are already in scope, and only when a YAML actually names one.
   *
   * What crosses is a HID usage page and usage, because that is what portall's
   * own wire message carries and one definition is worth more than a tidier
   * pair. */
  using KeySink = std::function<void(uint16_t, uint16_t)>;
  void set_key_sink(KeySink sink) { this->key_sink_ = std::move(sink); }

  /* Where the way OUT of a link goes -- portall's own ask_home().
   *
   * A second sink rather than one more usage through the first, because this
   * is not a key at all: no page is told anything, the browser is navigated
   * back to the panel's own url. A remote's Menu button is the only thing on
   * it that can leave a link -- until now the way home was a finger on the
   * top-left corner of the glass, which is no use to somebody sitting down
   * with a remote in their hand.
   *
   * Same std::function reasoning as above: this component includes portall's
   * header nowhere, and codegen emits the lambda where both are in scope. */
  using HomeSink = std::function<void()>;
  void set_home_sink(HomeSink sink) { this->home_sink_ = std::move(sink); }

  /* The one place that decides what an input device's button MEANS.
   *
   * Asked for as "le comportement du bluetooth doit gerer toutes les
   * peripherique qu'il dispose", after a first version put the mapping in a
   * household's YAML as a lambda over AVRCP command codes. That was wrong
   * twice: nobody should have to write ESP_AVRC_PT_CMD_FORWARD, and mapping
   * FORWARD to "down" was an INVENTION -- AVRCP has real UP, DOWN, LEFT,
   * RIGHT, SELECT and EXIT commands, so there was never anything to decide. */
  void feed_avrc_key(uint8_t code);
  void feed_hid_keys(const uint8_t *data, uint16_t len);
  /* The device's own report descriptor, which is what lets a gamepad work
     without anybody typing a byte offset. Bluedroid hands it over on
     ESP_HIDH_GET_DSCP_EVT; this is public beside the feeds so a test can put
     a real descriptor in and press real buttons at it. */
  /// One prepared Realtek patch: which ROM version it is for, and the bytes.
  /// Prepared by tools/rtlfw.py at codegen, never parsed on the board -- see
  /// send_realtek_firmware_ for why the split is there.
  struct RealtekImage {
    uint8_t rom_version;
    const uint8_t *data;
    uint32_t length;
  };
  static constexpr uint8_t MAX_RTL_IMAGES = 4;
  void add_realtek_firmware(uint8_t rom_version, const uint8_t *data, uint32_t length) {
    if (this->rtl_image_count_ < MAX_RTL_IMAGES)
      this->rtl_images_[this->rtl_image_count_++] = {rom_version, data, length};
  }

  void feed_hid_descriptor(const uint8_t *desc, uint16_t len, uint16_t vendor,
                           uint16_t product);
  /* One decoded field of one report, on its way to a key. Public for the same
     reason. */
  void feed_hid_usage(uint16_t page, uint16_t usage, int32_t value, int32_t logical_min,
                      int32_t logical_max);
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
  /// The device's report descriptor, straight off Bluedroid's own task.
  /// Copied and parsed later, for the reason the report queue exists.
  void on_hid_descriptor(const uint8_t *desc, uint16_t len, uint16_t vendor,
                         uint16_t product);

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

  /* Bluetooth off and on, as one switch a household can reach.
   *
   * WHAT "OFF" MEANS HERE, because the honest answer is narrower than the
   * word: it hangs up both profiles and stops PAGING for them. It does not
   * power the dongle down, does not stop CherryUSB and does not take
   * Bluedroid apart -- that is `esp_bluedroid_disable`, detaching the HCI
   * driver and stopping two reader tasks, on a stack that took nine separate
   * faults to bring up and that nothing here can compile. What off costs the
   * radio is the paging, which is the only part that was ever costing
   * anything while nothing was connected.
   *
   * AND IT HAS TO BE A FLAG RATHER THAN A CLEARED CLOCK. Zeroing
   * reconnect_backoff_ms_ is what pair() does for the length of a scan, and it
   * is not enough to stay off: the clock is re-armed in five other places --
   * every disconnect event in a2dp.cpp and hid.cpp puts it back -- so a switch
   * built that way would turn itself on again the first time a speaker walked
   * out of range, silently, which is the fault shape this component keeps
   * paying for. reconnect_tick_() asks this one flag instead. */
  void set_bt_enabled(bool on);
  bool bt_enabled() const { return !this->bt_off_; }

  /// Forget ONE remembered device rather than every bond at once.
  /// `speaker` picks which of the two slots -- see Remembered, which holds
  /// exactly one of each and is why there is no list here to page through.
  void forget_one(bool speaker);

  /// One line for a text sensor: the address and whether it is connected, or
  /// "none". The ADDRESS rather than the name, because Remembered stores six
  /// bytes and nothing else -- adding a name would change a struct that is
  /// already in NVS on every panel that has paired.
  std::string describe_role(bool speaker) const;
  /// Hang up whatever is connected. An inquiry cannot find a device that is
  /// already talking to this panel, so pairing and forgetting both start here.
  void drop_links_();
  /// Count a device heard during a pair scan, so the end of one can say
  /// whether it heard anything at all.
  void note_heard() { this->heard_++; }
  uint16_t heard() const { return this->heard_; }
  /// Put the reconnection clock back after a pair scan.
  void resume_reconnect();
  /* Say how the pairing went once the Wi-Fi is back.
   *
   * An inquiry sweeps the whole 2.4 GHz band and takes this panel's own link
   * down for exactly as long as it runs -- measured twice on this board, on
   * two different channels. So every line a scan produces is written into a
   * link that is not there: what was heard, what it paired with, whether it
   * found nothing. A panel reported a pairing as total silence, and this is at
   * least half of why. The outcome is therefore repeated a few seconds later,
   * when there is something to carry it. */
  void pair_report_tick_();
  /// Ask for that line, three seconds after a scan ends -- long enough for the
  /// radio to have come back, short enough that nobody has walked away.
  void say_pairing_later();
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
  void say_if_nothing_arrived_();
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
  void drain_media_();

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
  float media_volume_{1.0f};
  bool media_volume_fresh_{false};
  std::vector<Trigger<uint8_t, bool> *> media_key_triggers_;
  KeySink key_sink_{};
  HomeSink home_sink_{};
  /* The six keycodes a boot-protocol keyboard report carries, as they were
     last time: a key still held is in every report, and sending it again on
     each one would repeat it fifty times a second. Only what is NEW counts. */
  uint8_t held_[6]{};
  /* A gamepad's last hat position and button byte, for the same reason: a
     thumb resting on the d-pad sends the same report a hundred times a
     second, and only a CHANGE is a press. 0x0F is not a hat position, so the
     first real report always counts as a change. */
  /* The descriptor as Bluedroid handed it over, waiting for loop().
     Bluedroid's callback runs on its own BTC task and the map is read from
     drain_reports_ on ESPHome's, so the bytes are COPIED there and parsed
     here -- the same split every other thing this component takes off that
     task already makes. 512 is generous for a gamepad and a descriptor past
     it is refused out loud rather than half-read. */
  static constexpr uint16_t MAX_DESC = 512;
  uint8_t pending_desc_[MAX_DESC]{};
  uint16_t pending_desc_len_{0};
  uint16_t pending_desc_vendor_{0};
  uint16_t pending_desc_product_{0};
  bool desc_pending_{false};

  /* The device's report descriptor, parsed once when it connects. With one
     of these there are no byte offsets in this component at all: the device
     says which bits are its hat and which are its buttons, which is the only
     version of this that can work for a device nobody here owns. */
  HidReportMap hid_map_;
  /* Which descriptor is already in there -- its length and a plain sum of its
     bytes. A controller that reconnects sends the same one again, and this
     panel's own log shows a Shield reconnecting every eleven seconds, so
     re-parsing and re-announcing it each time buries whatever else the log is
     trying to say. Same descriptor: reset the edge-detection state, keep the
     map, say nothing. */
  uint16_t desc_seen_len_{0};
  uint32_t desc_seen_sum_{0};
  bool said_no_descriptor_{false};
  /// When sound was last put into the ring, and the quiet a stream is allowed
  /// before it is suspended. Ten seconds rather than the half second a mixer
  /// source defaults to: restarting costs an AVDTP round trip, so consecutive
  /// announcements should stay in one stream, and what this is for is the
  /// stream that would otherwise run for ever.
  static constexpr uint32_t A2DP_IDLE_MS = 10000;
  uint32_t pcm_fed_at_{0};
  bool a2dp_ctrl_asked_{false};
  void a2dp_idle_tick_();

  RealtekImage rtl_images_[MAX_RTL_IMAGES]{};
  uint8_t rtl_image_count_{0};
  bool send_realtek_firmware_(struct usbh_hubport *hport, uint8_t intf,
                              struct usb_endpoint_descriptor *events, uint8_t rom_version);
  /* The d-pad's last position, as the eight compass points HID's Hat Switch
     uses, with 0xFF for centred. A thumb resting on it sends the same report
     a hundred times a second, so only a CHANGE is a press -- the same rule
     the keyboard path above lives under, and the same one portall's touch
     queue had to learn. */
  uint8_t pad_dpad_{0xFF};
  /* Buttons already down, one bit per Button-page usage 1..32, and which of
     them have already named themselves in the log. */
  uint32_t pad_buttons_{0};
  uint32_t pad_said_{0};
  uint8_t consumer_held_{0};
  /* Six keycodes from one report, compared against the last six. Shared by
     the descriptor path and the plain boot-keyboard fallback, because a
     key held down is a key held down either way. */
  void note_keyboard_keys_(const uint8_t *now);
  /* Every byte of the report descriptor, under `show_reports:`. */
  void say_descriptor_(const uint8_t *desc, uint16_t len);
  void say_unreadable_report_(const uint8_t *data, uint16_t len);
  /* The report SHAPES already named -- length in the high byte, report id in
     the low one.
     ONE LINE PER SHAPE, not one line in total, and the difference is the
     whole usefulness of it. A Shield sends its gamepad state on one report
     and its Home button on ANOTHER, so a single line spent on whichever
     arrived first would leave the one button somebody is hunting for
     permanently silent. Capped, because the cap is what keeps this from
     becoming show_reports. */
  static constexpr uint8_t SHAPES = 6;
  uint16_t said_shapes_[SHAPES]{};
  uint8_t said_shape_count_{0};
  /* The last report `show_reports:` actually printed, and how many identical
     ones have been swallowed since.
     A CONTROLLER AT REST REPEATS ONE REPORT ABOUT A HUNDRED TIMES A SECOND,
     so printing every one buries the four lines somebody is hunting for
     under thousands that say nothing. What a reader needs is the moments the
     bytes CHANGED, which is exactly what a button is. */
  uint8_t last_shown_[63]{};
  uint8_t last_shown_len_{0};
  uint16_t shown_repeats_{0};
  std::vector<Trigger<float> *> media_volume_triggers_;
  bool profiles_up_{false};
  // When the next reconnection attempt is due, and how long to wait after the
  // one after that. A device that is switched off must not be asked for
  // sixty times a second, and a device that has just been switched on should
  // not wait a minute.
  uint32_t reconnect_due_ms_{0};
  uint32_t reconnect_backoff_ms_{0};
  bool hid_open_{false};
  /* The address of whatever is connected RIGHT NOW, which is a different
   * question from what this panel remembers. `remembered_` survives a restart
   * and is cleared by forget(); these two are the live links, and a link has
   * to be dropped by address. Keeping them apart is what lets forget() hang up
   * BEFORE it throws the key away -- removing a bond while the ACL is still up
   * leaves a connection with nothing behind it, which is the worst of both. */
  uint8_t open_sink_[6]{};
  uint8_t open_hid_[6]{};
  /* Pairing suspends the reconnection clock and this is what puts it back, so
   * a scan that finds nothing does not cost a paired device its way home. */
  bool reconnect_paused_{false};
  /// See set_bt_enabled: a flag, because the clock is re-armed elsewhere.
  bool bt_off_{false};
  uint16_t heard_{0};
  uint32_t pair_report_due_ms_{0};
  Remembered remembered_{};
  ESPPreferenceObject remembered_pref_;
  bool show_reports_{false};
  std::vector<Trigger<std::vector<uint8_t>> *> hid_report_triggers_;
  bool stack_up_{false};
  bool probing_{false};
  bool high_speed_{true};
  bool started_{false};
  /* Whether anything has ever enumerated, and when the host came up.
   *
   * An ESP32-P4 has two USB OTG peripherals and a board wires each of its
   * sockets to one of them, so `controller:` is a guess until a device
   * answers on it. Before this, a dongle on the OTHER controller produced one
   * hopeful line at boot and then nothing at all, for ever -- the same
   * silence as a dead dongle, a dead socket and an unpowered port. Saying so
   * once, with the other controller named, is what turns that into one more
   * flash rather than a diagnosis. */
  bool seen_device_{false};
  bool said_nothing_{false};
  uint32_t host_up_ms_{0};

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
  // Buttons, filled on Bluedroid's task and emptied by loop(). Sixteen is
  // generous: a thumb is not a thumbstick.
  static constexpr uint8_t MEDIA_KEYS = 16;
  MediaKey keys_[MEDIA_KEYS];
  volatile uint8_t key_head_{0};
  volatile uint8_t key_tail_{0};

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

/* One action per role rather than one action taking a role, which is this
 * project's own pattern: a household reads `portall_bt.forget_speaker` and
 * knows what the button does without looking anything up. */
template<typename... Ts> class ForgetSpeakerAction final : public Action<Ts...>, public Parented<PortallBT> {
 public:
  void play(const Ts &...) override { this->parent_->forget_one(true); }
};

template<typename... Ts> class ForgetInputAction final : public Action<Ts...>, public Parented<PortallBT> {
 public:
  void play(const Ts &...) override { this->parent_->forget_one(false); }
};

}  // namespace portall_bt
}  // namespace esphome
