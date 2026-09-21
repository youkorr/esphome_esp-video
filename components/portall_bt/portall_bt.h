#pragma once

#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/core/preferences.h"

#include <functional>
#include <new>
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
  /// Which live link it came in on. ESP_HIDH_DATA_IND_EVT carries a handle and
  /// no address at all, so this is the ONLY thing that says which of several
  /// paired devices pressed the button.
  uint8_t handle;
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
  /* 384, and the number is the panel's rather than a guess -- twice now.
   *
   * 64 was sized for "two sticks, two triggers, a hat and sixteen buttons with
   * room over" and a Shield said it wanted more. 192 was the round number that
   * followed, and the same Shield came back with the figure it actually wants:
   *
   *   described itself in 379 bytes and 339 fields, of which only 192 fit
   *
   * A controller declares far more than its buttons -- sticks at sixteen bits
   * each, a battery, NVIDIA's own host-command reports -- and a Report Count
   * of N makes N fields out of one item, so 379 bytes of descriptor really do
   * carry that many. Anything past the cap DOES NOT EXIST, so a report made
   * only of dropped fields decodes to nothing and reads from outside as a
   * device sending something unreadable.
   *
   * 24 bytes a field, so this is 9.0 KiB against the 4.5 it was. Worth saying
   * out loud because it is RAM on a board rather than a number in a file.
   *
   * It is still a cap, so `wanted` counts what the descriptor really declared
   * and the log prints both -- a limit that cannot say how far short it fell
   * is a limit nobody can raise correctly, which is what the first version of
   * this line was. */
  static constexpr uint16_t MAX_FIELDS = 384;
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

/// How much of a device's own name is kept, for a card somebody reads across
/// a room. The specification allows 248; a remote calls itself something far
/// shorter, and two full-length buffers would be half a kilobyte of RAM for
/// text nobody reads to the end.
static constexpr uint8_t MAX_REMOTE_NAME = 32;

/* HOW MANY INPUT DEVICES ONE PANEL KEEPS, and the number is a slot count
 * rather than a claim about the dongle.
 *
 * Asked for plainly -- "si je dispose de plus peripherique bluetooth que je
 * voudrais le connecter comment les text_sensor alors qu'il que que deux
 * text_sensor". One slot was never a design, it was the first version: a
 * household with a gamepad AND a remote had to choose, because pairing the
 * second silently replaced the first.
 *
 * Four covers a gamepad, a remote, a keyboard and one spare. What it does NOT
 * claim is that Bluedroid will carry four HID links at once -- that is its
 * own limit and nothing here has tried it. A slot that cannot connect simply
 * reads "paired, away", which is the honest answer either way.
 *
 * The SPEAKER stays at one, and that is structural rather than a matching
 * shortfall: A2DP source is a single stream with one encoder, and a second
 * would mean mixing and lip-syncing two of them. */
static constexpr uint8_t MAX_INPUTS = 4;

/// The input devices this panel is paired to, as it survives a restart.
///
/// SEPARATE FROM `Remembered` ON PURPOSE. An ESPHome preference is found by a
/// hash AND a size, so growing that struct would make every panel that has
/// ever paired forget what it is paired to. This one is its own record under
/// its own key, and `Remembered.hid` is kept as a MIRROR of the first slot so
/// that a firmware rolled back to a single-device build still finds a device
/// rather than an empty list.
struct RememberedInputs {
  uint8_t addr[MAX_INPUTS][6];
  uint8_t count;
} __attribute__((packed));

/* The four analog axes a gamepad is steered with, and the report SHAPES a
   device may name itself over -- both per device, which is why they are here
   rather than inside the component. */
static constexpr uint8_t PAD_AXES = 4;
static constexpr uint8_t SHAPES = 6;

/// Everything that is true of ONE input device rather than of the panel.
///
/// This is the whole of what several devices needed. ESP_HIDH_DATA_IND_EVT
/// carries a HANDLE and no address, so a report has to be routed to the
/// device it came from -- and decoding one controller's report against
/// another's descriptor is exactly the confidently-wrong answer this
/// component was rewritten to stop giving. The edge-detection state is the
/// same story one step down: two gamepads share a `buttons` word and each
/// one's press looks to the other like a release.
struct InputDevice {
  uint8_t addr[6]{};
  bool used{false};       ///< this slot names a device at all
  bool remembered{false}; ///< ...and it is in NVS, so it is paged for
  bool open{false};       ///< ...and it is connected right now
  uint8_t handle{0};      ///< what Bluedroid calls the live link
  char name[MAX_REMOTE_NAME + 1]{};

  /* THE MAP IS ALLOCATED WHEN A DESCRIPTOR FIRST ARRIVES, never before.
     One costs 9 KiB -- 384 fields at 24 bytes -- and four slots inline would
     be 37 KiB of a board's internal RAM standing idle on every panel that
     pairs a speaker and nothing else. Allocated once per slot and kept: a
     device that reconnects reuses it, and freeing and retaking 9 KiB every
     eleven seconds is how a heap gets fragmented. */
  HidReportMap *map{nullptr};
  uint16_t desc_len{0};
  uint32_t desc_sum{0};
  bool said_no_descriptor{false};

  uint8_t held[6]{};
  uint8_t dpad{0xFF};
  uint32_t buttons{0};
  uint32_t said{0};
  uint8_t consumer{0};
  int8_t axis[PAD_AXES]{};
  uint8_t axis_dir[PAD_AXES]{};
  uint8_t axis_live{0};
  uint16_t said_shapes[SHAPES]{};
  uint8_t said_shape_count{0};
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
  /* EVERY ONE OF THESE CARRIES A HANDLE, because with more than one device a
     report has to be routed to the device that sent it. ESP_HIDH_OPEN_EVT is
     the only one of the four that carries a bd_addr as well, which is what
     ties a handle to a slot in the first place; CLOSE and DATA_IND carry a
     handle and nothing else, checked in the header rather than assumed. The
     defaults are for a caller that has one device and no handles -- the
     tests, which press real buttons at this from a workstation. */
  void on_hid_open(const uint8_t *addr, uint8_t handle = 0);
  /// -1 when the event said nothing about WHICH link went away -- a page that
  /// never opened reports itself here too, and that must not hang up a device
  /// that is perfectly well connected.
  void on_hid_closed(int handle = -1);
  void on_hid_report(const uint8_t *data, uint16_t len, uint8_t handle = 0);
  /// The device's report descriptor, straight off Bluedroid's own task.
  /// Copied and parsed later, for the reason the report queue exists.
  void on_hid_descriptor(const uint8_t *desc, uint16_t len, uint16_t vendor,
                         uint16_t product, uint8_t handle = 0);

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

  /* One device an inquiry heard, sorted into what to do with it.
   *
   * A member rather than the body of the GAP callback so a test can hand it
   * devices: the decision this makes -- take it, skip it, or say which option
   * is off -- is the whole of what pressing Pair does, and it used to live in
   * a `static` function no test could reach.
   *
   * `cod` is the Class of Device as the controller reported it; `name` may be
   * null, because a device that publishes no extended inquiry response has
   * none to give. */
  void heard_device(const uint8_t *addr, uint32_t cod, const char *name);
  /// How many of the devices heard were passed over because this panel
  /// already has them. Zero and non-zero need different next steps, which is
  /// why it is counted rather than inferred.
  uint16_t skipped_known() const { return this->skipped_known_; }

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

  /// Forget by ROLE rather than every bond at once: the speaker, or every
  /// input device. There is no per-device action and that is deliberate --
  /// one would need an index a household has no way to read, which is the
  /// mechanism-instead-of-a-name this component keeps being corrected into
  /// not building.
  void forget_one(bool speaker);

  /// One line for a text sensor: what the device calls itself, its address,
  /// and whether it is connected -- or "none".
  ///
  /// The name leads because that is what somebody reading a card wants, and
  /// the address stays beside it because two remotes of one model share a
  /// name and it is the address a pair or forget acts on. A panel that has
  /// just booted shows the address alone until the name arrives; see
  /// note_remote_name below for why it is not stored.
  /// The speaker is one device; the input side is a LIST, joined with ", ".
  /// Bounded, because a text sensor's state is not unlimited and four names
  /// can reach past it -- what is dropped is said rather than cut off.
  std::string describe_role(bool speaker) const;
  /* Remember what a device CALLS itself, so the entity above is readable.
   *
   * `Remembered` stays six bytes per role and is deliberately not touched:
   * it is already sitting in the NVS of every panel that has ever paired, and
   * changing its layout would make each of them forget what it is paired to.
   * So the name lives in RAM instead, for as long as the panel is up, and a
   * panel that has just booted shows the address until the name arrives.
   *
   * The name is free at pairing -- ESP_BT_GAP_AUTH_CMPL_EVT carries it and it
   * was being logged and thrown away. On a RECONNECT there is no pairing and
   * ESP_HIDH_OPEN_EVT carries no name at all, so it is asked for: that is a
   * Remote Name Request over a link that is already open, NOT an inquiry, so
   * it does not sweep the band and does not take this panel's Wi-Fi down with
   * it. Reasoned from what the command is rather than measured here.
   */
  void note_remote_name(const uint8_t *addr, const char *name);
  /// Ask a device what it calls itself, once it is connected.
  void ask_remote_name_(const uint8_t *addr);
  /// Hang up whatever is connected. An inquiry cannot find a device that is
  /// already talking to this panel, so pairing and forgetting both start here.
  void drop_links_();
  /* Hang up ONE address, whatever this component believes about it.
   *
   * Gated on nothing: `a2dp_open_` and `InputDevice::open` are this
   * component's OPINION of the link, and the stack's state is the fact. A
   * disconnect for something that is not connected costs an error code
   * nobody reads; a disconnect that was skipped because a flag said "not
   * open" leaves a live ACL whose key has just been removed -- which is the
   * "even Forget does not help" this component has already paid for once. */
  void drop_link_to_(const uint8_t *addr, bool speaker);
  /// How many devices a pair scan heard, so the end of one can say whether it
  /// heard anything at all. Counted in heard_device, which is the only thing
  /// an inquiry result reaches.
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
  void save_inputs_();
  /// Which slot holds this address / this live handle, or -1.
  int8_t slot_for_addr_(const uint8_t *addr) const;
  int8_t slot_for_handle_(uint8_t handle) const;
  /// Which slot a report carrying this handle belongs to, or -1 for nowhere.
  ///
  /// The handle is the answer whenever a device is open under it. The
  /// fallbacks are for the two cases that are not a several-device question
  /// at all: exactly one device connected (so the handle can only be its
  /// own, whatever Bluedroid numbered it) and a caller with no handles --
  /// the tests, which press buttons at slot 0 the way they always have.
  ///
  /// -1 IS A REAL ANSWER AND NOT A FAILURE TO GUESS. A device that connected
  /// when every slot was already taken has no map, no hat and no button word
  /// of its own, and decoding its report against the nearest device's is the
  /// confidently-wrong answer this whole path exists to stop giving. Its
  /// bytes still reach on_hid_report and `show_reports:`; they simply become
  /// no key.
  int8_t route_(uint8_t handle) const;
  /// A slot for a device that has just connected: its own if it has one, else
  /// a free one. -1 when every slot is taken, which is said out loud rather
  /// than quietly dropping somebody's newest device.
  int8_t claim_slot_(const uint8_t *addr);
  bool any_input_open_() const;
  bool remembered_input_(const uint8_t *addr) const;
  /// Forget what a device was holding. A controller that hung up and came
  /// back is not still holding whatever it was holding, and a stale hat
  /// position would swallow the first press.
  static void reset_decode_(InputDevice &d);

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
  /* EVERY INPUT DEVICE THIS PANEL KNOWS ABOUT, and which of them a report is
     being decoded against right now.
     `cur_input_` is set from the report's own handle in drain_reports_ and
     read by the whole of keys.cpp, which is why the feeds stay one-argument:
     a test presses buttons at slot 0 exactly as it always did. */
  InputDevice inputs_[MAX_INPUTS]{};
  uint8_t cur_input_{0};
  InputDevice &dev_() { return this->inputs_[this->cur_input_]; }
  const InputDevice &dev_() const { return this->inputs_[this->cur_input_]; }
  /// The current device's report map, allocated on first use. Null when the
  /// board had no room for one, which is said once and costs the buttons
  /// rather than the panel.
  HidReportMap *map_();

  /* The descriptors as Bluedroid handed them over, waiting for loop().
     Bluedroid's callback runs on its own BTC task and a map is read from
     drain_reports_ on ESPHome's, so the bytes are COPIED there and parsed
     here -- the same split every other thing this component takes off that
     task already makes. 512 is generous for a gamepad and a descriptor past
     it is refused out loud rather than half-read.

     A RING RATHER THAN ONE BUFFER, sized to the slots. A device sends its
     descriptor once per connection, so MAX_INPUTS of them is provably enough
     for every connected device to have one waiting; a single buffer would
     have been fine almost always, and "almost always" is how every silent
     fault in this file started. */
  static constexpr uint16_t MAX_DESC = 512;
  struct PendingDesc {
    uint8_t bytes[MAX_DESC];
    uint16_t len;
    uint16_t vendor;
    uint16_t product;
    uint8_t handle;
  };
  PendingDesc pending_desc_[MAX_INPUTS]{};
  uint8_t pending_desc_head_{0};
  uint8_t pending_desc_tail_{0};
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
  /* The d-pad's last position, the buttons already down, the analog axes and
     the keycodes still held all moved into InputDevice above -- see its own
     comment for why. Two gamepads sharing one button word is one device's
     press reading as the other's release. */
  /* THE ANALOG STICKS, which are four axes rather than four buttons.
     Generic Desktop X and Y are the left stick and Z and Rz are the right
     one -- bluepad32's Android parser, which the button numbering above
     already follows. Rx and Ry are deliberately NOT here: they are where a
     great many controllers put their TRIGGERS, and a trigger read as an axis
     rests at one end of its range for ever, which is a direction nobody can
     let go of.

     Per axis: the deflection last seen as a percentage of full travel,
     which way it is currently counted as pushed (0 none, 1 negative, 2
     positive), and whether it has ever been seen near its own centre. That
     last one is the guard that makes the Rx/Ry rule unnecessary rather than
     merely likely to hold: a stick reports its centre constantly and a
     trigger never does, so an axis that has not been centred is not steered
     with. */
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
  /* Said ONCE for the whole panel rather than once per device: it is about a
     setting nobody turned on, not about the device that noticed. It used to
     borrow the shape list's own counter to remember it had spoken, which
     spent a shape slot on a line that is not a shape. */
  bool said_no_sink_{false};
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
  /* The address of whatever is connected RIGHT NOW, which is a different
   * question from what this panel remembers. `remembered_` survives a restart
   * and is cleared by forget(); this is the live link, and a link has to be
   * dropped by address. Keeping them apart is what lets forget() hang up
   * BEFORE it throws the key away -- removing a bond while the ACL is still up
   * leaves a connection with nothing behind it, which is the worst of both.
   * The input side's equivalent is InputDevice::open, one per slot. */
  uint8_t open_sink_[6]{};
  /* Pairing suspends the reconnection clock and this is what puts it back, so
   * a scan that finds nothing does not cost a paired device its way home. */
  bool reconnect_paused_{false};
  /// See set_bt_enabled: a flag, because the clock is re-armed elsewhere.
  bool bt_off_{false};
  uint16_t heard_{0};
  uint16_t skipped_known_{0};
  /* WHICH device this pairing run actually reached for, and whether it
     reached for one at all.
     Needed the moment pairing stopped hanging everything up: "a device is
     connected" was true of the speaker that had been playing all along, so a
     run that found nothing new would have announced success. What somebody
     pressing Pair wants to know is whether the NEW thing arrived. */
  uint8_t pair_target_[6]{};
  bool pair_took_{false};
  bool is_open_(const uint8_t *addr) const;
  /* WHAT WAS CONNECTED WHILE THE SCAN RAN, which is the one half of "nothing
     answered" a panel can report about itself.
     A scan that hears nothing has two causes and they need different next
     steps: the device was not discoverable -- switched off, out of range, in
     somebody's car -- or this panel's own radio was busy. An inquiry and an
     established link share one controller, and the dongle's antenna is
     centimetres from the C6's, so a link that was up is the only thing on
     this side that could have cost the scan anything. From a log the two used
     to be the same sentence.
     Filled in pair() rather than read when the report is printed: by then a
     device may have come or gone, and the question is about the scan. */
  char pair_links_[96]{};
  uint8_t pair_link_count_{0};
  void note_links_for_scan_();
  uint32_t pair_report_due_ms_{0};
  Remembered remembered_{};
  ESPPreferenceObject remembered_pref_;
  RememberedInputs remembered_inputs_{};
  ESPPreferenceObject inputs_pref_;
  /* Which remembered input device gets asked for next. A page is 5.12 s of
     radio by default, and this component already had to learn that overlapping
     pages starve an inquiry -- so one device is asked per tick and the turn
     moves on, rather than four pages going out together. */
  uint8_t reconnect_next_{0};
  // What each remembered device calls itself, for as long as this panel is up.
  // Empty until a pairing or a Remote Name Request fills it, and cleared when
  // the device is forgotten -- a stale name beside a new address is worse than
  // no name, because it reads as correct.
  char sink_name_[MAX_REMOTE_NAME + 1]{};
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
