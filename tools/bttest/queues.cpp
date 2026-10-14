// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
/* The two queues from Bluedroid's task to the loop, driven from two threads.
 *
 * Both drop the OLDEST entry when full, and both used to do it by moving the
 * loop's own read position from the producer's side. That is a race: the loop
 * may be half way through copying out the very slot the producer has decided
 * to write over, and it then advances a position the producer has already
 * moved. It shows only once the loop is a whole queue behind -- which is
 * exactly when nobody is reading the log.
 *
 * A race cannot be shown by stepping through code one call at a time, so
 * these run a producer thread as fast as it will go against the real
 * on_hid_report() and on_media_key(), and the loop's real drain on the main
 * thread, and look at every report that reached the YAML: whole, in order,
 * never twice, and the last one sent always delivered. Against the queue this
 * replaces, the first three fail within a fraction of a second.
 */
#define private public
#define protected public
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main

#include <atomic>
#include <cstdio>
#include <fcntl.h>
#include <unistd.h>
#include <cstring>
#include <thread>
#include <vector>

#include "linkstubs.h"

using esphome::Trigger;
using esphome::portall_bt::PortallBT;

static int failures = 0;

static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

/// Byte k of report n. Every byte depends on n, so a report assembled from two
/// different reports cannot pass for either.
static uint8_t pattern(uint32_t n, unsigned k) {
  uint32_t x = n * 2654435761u + k * 40503u;
  x ^= x >> 13;
  return (uint8_t) (x * 0x5bd1e995u >> 24);
}

static constexpr unsigned REPORT_LEN = 32;

/// stdout is the log here, and a drain that loses reports says so every time
/// it is called -- which is right on a panel and a flood in a test. Silenced
/// by descriptor, so the pipe a check reads is put back exactly as it was.
struct Quiet {
  int saved;
  Quiet() {
    std::fflush(stdout);
    saved = dup(1);
    const int null = open("/dev/null", O_WRONLY);
    dup2(null, 1);
    close(null);
  }
  ~Quiet() {
    std::fflush(stdout);
    dup2(saved, 1);
    close(saved);
  }
};

static void make_report(uint32_t n, uint8_t *out) {
  std::memcpy(out, &n, 4);
  for (unsigned k = 4; k < REPORT_LEN; k++)
    out[k] = pattern(n, k);
}

int main() {
  std::printf("Queues from Bluedroid's task to the loop\n");

  {
    // One thread, to pin down what the queue is FOR before racing it: full,
    // it keeps the newest, and the very last report -- the release -- is
    // always among what comes out.
    PortallBT bt;
    Trigger<std::vector<uint8_t>> seen;
    std::vector<uint32_t> got;
    seen.observer = [&](std::vector<uint8_t> bytes) {
      uint32_t n = 0;
      std::memcpy(&n, bytes.data(), 4);
      got.push_back(n);
    };
    bt.hid_report_triggers_.push_back(&seen);
    uint8_t report[REPORT_LEN];
    for (uint32_t n = 0; n < 40; n++) {
      make_report(n, report);
      bt.on_hid_report(report, REPORT_LEN, 0);
    }
    {
      Quiet quiet;
      bt.drain_reports_();
    }
    ok("forty reports into a queue of thirty-two: the newest come out",
       !got.empty() && got.back() == 39 && got.front() >= 8);
    bool ascending = true;
    for (size_t i = 1; i < got.size(); i++)
      ascending = ascending && got[i] == got[i - 1] + 1;
    ok("and in order, with nothing twice", ascending);
  }

  {
    PortallBT bt;
    Trigger<std::vector<uint8_t>> seen;
    bool torn = false, backwards = false;
    uint32_t delivered = 0, last = 0;
    bool any = false;
    seen.observer = [&](std::vector<uint8_t> bytes) {
      uint32_t n = 0;
      std::memcpy(&n, bytes.data(), 4);
      for (unsigned k = 4; k < REPORT_LEN; k++)
        if (bytes.size() != REPORT_LEN || bytes[k] != pattern(n, k))
          torn = true;
      if (any && n <= last)
        backwards = true;
      last = n;
      any = true;
      delivered++;
    };
    bt.hid_report_triggers_.push_back(&seen);

    static constexpr uint32_t SENT = 3000000;
    std::atomic<bool> done{false};
    std::thread producer([&] {
      uint8_t report[REPORT_LEN];
      for (uint32_t n = 0; n < SENT; n++) {
        make_report(n, report);
        bt.on_hid_report(report, REPORT_LEN, 0);
      }
      done.store(true);
    });
    {
      Quiet quiet;
      while (!done.load())
        bt.drain_reports_();
      producer.join();
      bt.drain_reports_();
    }

    std::printf("    (%u of %u reports reached the YAML while racing)\n", (unsigned) delivered,
                (unsigned) SENT);
    ok("racing the producer: no report is half of one and half of another", !torn);
    ok("and none comes out twice or out of order", !backwards);
    ok("and the last one sent -- the release -- is delivered", any && last == SENT - 1);
  }

  {
    // The media buttons ride the same kind of queue. A MediaKey is two bytes,
    // so the count is carried in nine bits -- the code and the pressed flag --
    // and the producer is held to within 200 of what the loop has taken, so a
    // step forward is never mistaken for a wrap.
    PortallBT bt;
    bt.a2dp_ = true;
    Trigger<uint8_t, bool> seen;
    std::atomic<uint32_t> taken{0};
    bool backwards = false;
    uint32_t full = 0;
    bool any = false;
    seen.observer = [&](uint8_t code, bool pressed) {
      const uint32_t v = (uint32_t) code | (pressed ? 0x100u : 0u);
      const uint32_t step = (v - (full & 0x1FF)) & 0x1FF;
      if (any && (step == 0 || step > 256))
        backwards = true;
      full = any ? full + step : v;
      any = true;
      taken.store(full);
    };
    bt.media_key_triggers_.push_back(&seen);

    static constexpr uint32_t SENT = 1000000;
    std::atomic<bool> done{false};
    std::thread producer([&] {
      for (uint32_t n = 0; n < SENT; n++) {
        while (n > taken.load() + 200)
          std::this_thread::yield();
        bt.on_media_key((uint8_t) (n & 0xFF), (n & 0x100) != 0);
      }
      done.store(true);
    });
    {
      Quiet quiet;
      while (!done.load())
        bt.drain_media_();
      producer.join();
      bt.drain_media_();
    }

    ok("media buttons, raced: never twice, never backwards", !backwards);
    ok("and the last button sent is delivered", any && full == SENT - 1);
  }

  return failures == 0 ? 0 : 1;
}
