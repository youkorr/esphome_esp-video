// Does the HCI reader deliver a frame whose length is an exact multiple of the
// endpoint's packet size?
//
// That question cost a round trip to a board. The reader used to end a frame on
// a SHORT read, and `Read Local Extended Features` answers in exactly 16 bytes
// on a 16-byte endpoint, so Bluedroid's fifth command timed out against a
// reader still waiting for a packet the controller had no reason to send.
//
// This includes the component's own source so it exercises the SHIPPED
// event_length() and acl_length() rather than a copy of them -- a test that
// restates the arithmetic it is checking proves only that it can copy. The
// loop around them is modelled here, because the real one is wrapped around a
// blocking USB read there is no controller for.
#define main component_main_unused
#include "portall_bt.cpp"
#undef main

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unistd.h>
#include <vector>

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

using esphome::portall_bt::acl_length;
using esphome::portall_bt::event_length;

static int failures = 0;

// The reader's own loop, with the blocking read replaced by a chopper. Frames
// go in whole, arrive in packets of `mps`, and must come out whole again.
// `stall_every` injects a read that brought nothing -- a NAK, which is the
// ordinary answer of an endpoint with nothing to report -- after that many
// packets. `drop_on_stall` is the OLD behaviour: clear what has been read.
static void run(const char *what, const std::vector<std::vector<uint8_t>> &frames, uint32_t mps,
                uint32_t (*length)(const uint8_t *, uint32_t),
                const std::vector<size_t> &expected, uint32_t stall_every = 0,
                bool drop_on_stall = false) {
  std::vector<uint8_t> frame(2048);
  std::vector<size_t> delivered;
  uint32_t filled = 0;
  size_t bytes = 0;

  // Each frame is its own USB transfer, so a packet never carries the end of
  // one and the start of the next -- the reader depends on that and so must
  // anything claiming to model it. The first version of this chopper ran the
  // frames together, which made the reader look broken on a stream it will
  // never see.
  uint32_t packets = 0;
  for (const auto &one : frames) {
    bytes += one.size();
    for (size_t at = 0; at < one.size();) {
      if (stall_every != 0 && packets != 0 && packets % stall_every == 0) {
        // Nothing arrived this time round.
        if (drop_on_stall) {
          filled = 0;
        }
      }
      packets++;
      const uint32_t take = (uint32_t) ((one.size() - at) < mps ? (one.size() - at) : mps);
      memcpy(&frame[filled], &one[at], take);
      filled += take;
      at += take;

      const uint32_t want = length(frame.data(), filled);
      if (want == 0 || filled < want) {
        continue;
      }
      delivered.push_back(want);
      filled = 0;
    }
  }

  bool ok = delivered.size() == expected.size();
  for (size_t i = 0; ok && i < expected.size(); i++) {
    ok = delivered[i] == expected[i];
  }
  if (!ok) {
    failures++;
    printf("  ECHEC  %s: delivered %zu frames, wanted %zu\n", what, delivered.size(),
           expected.size());
    for (size_t i = 0; i < delivered.size(); i++) {
      printf("         frame %zu: %zu bytes\n", i, delivered[i]);
    }
    return;
  }
  printf("  ok     %s (%zu frames, %zu bytes, packets of %u)\n", what, delivered.size(), bytes,
         (unsigned) mps);
}

static std::vector<uint8_t> command_complete(uint16_t opcode, uint8_t params) {
  std::vector<uint8_t> e;
  e.push_back(0x0E);
  e.push_back((uint8_t) (3 + params));
  e.push_back(1);
  e.push_back((uint8_t) (opcode & 0xFF));
  e.push_back((uint8_t) (opcode >> 8));
  for (uint8_t i = 0; i < params; i++) {
    e.push_back((uint8_t) (0xA0 + i));
  }
  return e;
}

static std::vector<uint8_t> acl(uint16_t payload) {
  std::vector<uint8_t> a{0x01, 0x20, (uint8_t) (payload & 0xFF), (uint8_t) (payload >> 8)};
  for (uint16_t i = 0; i < payload; i++) {
    a.push_back((uint8_t) i);
  }
  return a;
}

int main() {
  // The seven Bluedroid sends while it starts, in order, on a 16-byte
  // endpoint. 0x1004 is the one that is exactly one packet.
  {
    std::vector<std::vector<uint8_t>> frames;
    std::vector<size_t> want;
    // Return parameters per the specification: status and whatever the command
    // answers with. 0x1004 is status, page, maximum page and eight bytes of
    // features, which with the event's own five bytes is exactly sixteen.
    const struct {
      uint16_t opcode;
      uint8_t params;
    } startup[] = {{0x0C03, 1},  {0x1001, 8}, {0x1002, 65}, {0x1003, 9},
                   {0x1004, 11}, {0x1005, 8}, {0x1009, 7}};
    for (auto &c : startup) {
      auto e = command_complete(c.opcode, c.params);
      frames.push_back(e);
      want.push_back(e.size());
    }
    run("Bluedroid's startup commands", frames, 16, event_length, want);

    // And 0x1004 entirely on its own, which is the exact case that hung.
    auto one = command_complete(0x1004, 11);
    if (one.size() != 16) {
      printf("  ECHEC  the premise: 0x1004 answers in %zu bytes, not 16\n", one.size());
      failures++;
    }
    run("0x1004 alone, exactly one packet", {one}, 16, event_length, {16});
  }

  // The probe's own four, which is the set that already worked, so the new
  // rule must not break them.
  {
    std::vector<std::vector<uint8_t>> frames;
    std::vector<size_t> want;
    for (auto &e : {command_complete(0x0C03, 1), command_complete(0x1001, 8),
                    command_complete(0x1009, 7), command_complete(0x0C14, 249)}) {
      frames.push_back(e);
      want.push_back(e.size());
    }
    run("the probe's four, Read Local Name included", frames, 16, event_length, want);
  }

  // Every event length there is, one at a time, so no size is special.
  {
    bool ok = true;
    for (uint8_t params = 0; params <= 250; params++) {
      auto e = command_complete(0x1001, params);
      std::vector<size_t> want{e.size()};
      std::vector<uint8_t> frame(2048);
      uint32_t filled = 0;
      size_t got = 0;
      for (size_t at = 0; at < e.size();) {
        const uint32_t take = (uint32_t) ((e.size() - at) < 16 ? (e.size() - at) : 16);
        memcpy(&frame[filled], &e[at], take);
        filled += take;
        at += take;
        const uint32_t n = event_length(frame.data(), filled);
        if (n != 0 && filled >= n) {
          got = n;
          filled = 0;
        }
      }
      if (got != e.size()) {
        ok = false;
        printf("  ECHEC  an event of %zu bytes came out as %zu\n", e.size(), got);
        break;
      }
    }
    if (ok) {
      printf("  ok     every event length from 5 to 255 bytes, one packet at a time\n");
    } else {
      failures++;
    }
  }

  // ACL on a 64-byte bulk endpoint, including the payloads that fill it
  // exactly -- 60, 124, 188 -- which the old rule would have hung on too.
  {
    std::vector<std::vector<uint8_t>> frames;
    std::vector<size_t> want;
    for (uint16_t payload : {0, 1, 59, 60, 61, 124, 188, 339}) {
      auto a = acl(payload);
      frames.push_back(a);
      want.push_back(a.size());
    }
    run("ACL, the exact multiples of 64 included", frames, 64, acl_length, want);
  }

  // The opcode the log prints, checked against the twelve frames a panel
  // really sent. `0e 05 01 0f 20` is LE Read White List Size and a reader had
  // to work that out by hand from four hex bytes, which is why say_frame
  // prints it as a number now. Captured off stdout rather than restated here:
  // a test that recomputes `frame[3] | frame[4] << 8` proves only that it can
  // copy the line it is checking.
  {
    const struct {
      uint8_t bytes[5];
      const char *expect;
    } cases[] = {
        {{0x0E, 0x04, 0x01, 0x03, 0x0C}, "opcode 0c03"},  // Reset
        {{0x0E, 0x0B, 0x01, 0x05, 0x10}, "opcode 1005"},  // Read Buffer Size
        {{0x0E, 0x44, 0x01, 0x02, 0x10}, "opcode 1002"},  // Supported Commands
        {{0x0E, 0x0E, 0x01, 0x04, 0x10}, "opcode 1004"},  // Extended Features
        {{0x0E, 0x05, 0x01, 0x0F, 0x20}, "opcode 200f"},  // LE White List Size
    };

    char path[] = "/tmp/portall_bt_sayXXXXXX";
    const int hole = mkstemp(path);
    const int saved = dup(fileno(stdout));
    fflush(stdout);
    dup2(hole, fileno(stdout));
    esphome::portall_bt::g_frames_said = 0;
    for (auto &c : cases) {
      esphome::portall_bt::say_frame("event", c.bytes, sizeof(c.bytes));
    }
    fflush(stdout);
    dup2(saved, fileno(stdout));
    close(saved);
    close(hole);

    std::string said;
    {
      FILE *back = fopen(path, "rb");
      char buf[512];
      size_t n;
      while (back != nullptr && (n = fread(buf, 1, sizeof(buf), back)) > 0) {
        said.append(buf, n);
      }
      if (back != nullptr) {
        fclose(back);
      }
    }
    remove(path);

    bool ok = true;
    for (auto &c : cases) {
      if (said.find(c.expect) == std::string::npos) {
        printf("  ECHEC  the log never said \"%s\"\n", c.expect);
        ok = false;
      }
    }
    if (ok) {
      printf("  ok     the log names the opcode for all five real frames\n");
    } else {
      failures++;
    }
  }

  // A NAK in the middle of a multi-packet frame. Keeping what has been read
  // costs nothing; clearing it loses the frame AND leaves its remaining
  // packets to be assembled as a fresh one, which is the desynchronisation
  // Bluedroid refused with an assert about parameter_length.
  {
    std::vector<std::vector<uint8_t>> frames;
    std::vector<size_t> want;
    for (auto &e : {command_complete(0x1002, 65), command_complete(0x1001, 8),
                    command_complete(0x1003, 9)}) {
      frames.push_back(e);
      want.push_back(e.size());
    }
    run("a NAK every other packet, keeping what was read", frames, 16, event_length, want, 2,
        false);

    // And the same stream with the old behaviour, which must NOT come out
    // whole -- otherwise this test is not exercising the fault.
    std::vector<uint8_t> scratch(2048);
    uint32_t filled = 0, packets = 0;
    std::vector<size_t> got;
    for (const auto &one : frames) {
      for (size_t at = 0; at < one.size();) {
        if (packets != 0 && packets % 2 == 0) {
          filled = 0;  // the old rule: a refusal voids what was read
        }
        packets++;
        const uint32_t take = (uint32_t) ((one.size() - at) < 16 ? (one.size() - at) : 16);
        memcpy(&scratch[filled], &one[at], take);
        filled += take;
        at += take;
        const uint32_t n = event_length(scratch.data(), filled);
        if (n != 0 && filled >= n) {
          got.push_back(n);
          filled = 0;
        }
      }
    }
    if (got == want) {
      printf("  ECHEC  dropping on a NAK still delivered every frame, so this is not the fault\n");
      failures++;
    } else {
      printf("  ok     dropping on a NAK loses the stream (%zu frames out of %zu, wrong sizes)\n",
             got.size(), want.size());
    }
  }

  // The OLD rule, kept so the fault stays reproduced rather than remembered:
  // end a frame on a read shorter than the packet size. Against the event that
  // is exactly one packet it delivers NOTHING, for ever, which is what
  // `command_timed_out ... opcode: 0x1004` was.
  {
    auto one = command_complete(0x1004, 11);
    uint32_t filled = 0;
    int delivered = 0;
    for (size_t at = 0; at < one.size();) {
      const uint32_t take = (uint32_t) ((one.size() - at) < 16 ? (one.size() - at) : 16);
      filled += take;
      at += take;
      if (take == 16) {
        continue;  // "a full packet means there is more of this event to come"
      }
      delivered++;
      filled = 0;
    }
    if (delivered == 0) {
      printf("  ok     the old short-packet rule delivers nothing for 0x1004, as reported\n");
    } else {
      printf("  ECHEC  the old rule delivered %d frames, so this is not the fault that was\n",
             delivered);
      failures++;
    }
  }

  if (failures != 0) {
    printf("\n  %d failed\n", failures);
    return 1;
  }
  return 0;
}
