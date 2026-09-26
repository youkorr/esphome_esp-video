/* audio.cpp against a speaker that will not stay started -- a mixer source its
 * mixer refuses -- and against three that are fine. Built by
 * tools/checkrefuse.py.
 *
 * The first speaker is the reported fault: every play() on a stopped ESPHome
 * speaker starts it, and a refused mixer source goes back to stopped inside
 * one turn of the loop, making a ring buffer and throwing it away each time.
 * start() is counted as that ring buffer. */
#include "portall.h"
#include <cstdio>
#include <vector>
namespace esphome {
static uint32_t now_ms = 0;
uint32_t millis() { return now_ms; }
}  // namespace esphome
using namespace esphome;

// Refuses for ever, or until `ok_after_ms`, like a mixer source at the wrong rate.
struct Refused : speaker::Speaker {
  int starts = 0, plays = 0;
  uint32_t ok_after_ms = UINT32_MAX;
  size_t taken = 0;
  size_t play(const uint8_t *, size_t n) override {
    plays++;
    if (millis() >= ok_after_ms) {
      state_ = speaker::STATE_RUNNING;
      taken += n;
      return n;
    }
    if (state_ == speaker::STATE_STOPPED)
      start();
    return 0;
  }
  void start() override {
    starts++;
    if (millis() >= ok_after_ms)
      state_ = speaker::STATE_RUNNING;
  }
  void stop() override { state_ = speaker::STATE_STOPPED; }
  bool has_buffered_data() const override { return false; }
};

// Starts a few blocks late, as a mixer source does from its own loop.
struct Slow : speaker::Speaker {
  int refused_left = 5;
  size_t taken = 0;
  size_t play(const uint8_t *, size_t n) override {
    if (refused_left > 0) {
      refused_left--;
      return 0;
    }
    state_ = speaker::STATE_RUNNING;
    taken += n;
    return n;
  }
  void start() override {}
  void stop() override { state_ = speaker::STATE_STOPPED; }
  bool has_buffered_data() const override { return false; }
};

// Running and full: behind, not refusing. Nothing new should touch it.
struct Full : speaker::Speaker {
  int plays = 0;
  size_t play(const uint8_t *, size_t) override {
    plays++;
    return 0;
  }
  void start() override { state_ = speaker::STATE_RUNNING; }
  void stop() override { state_ = speaker::STATE_STOPPED; }
  bool has_buffered_data() const override { return true; }
};

static int fails = 0;
static void check(const char *what, bool ok) {
  printf("\n  %s  %s\n", ok ? "ok   " : "ECHEC", what);
  if (!ok)
    fails++;
}

// The page's sound as the network hands it over: 20 ms packets, mono 48 kHz.
static void feed(portall::Portall &p, uint32_t ms) {
  static std::vector<uint8_t> pcm(1920, 1);
  for (uint32_t t = 0; t < ms; t += 20) {
    p.on_audio_samples(pcm.data(), pcm.size(), 1);
    now_ms += 20;
  }
}

int main() {
  {
    now_ms = 1000;
    Refused spk;
    portall::Portall p;
    p.set_speaker(&spk);
    p.setup_speaker_();
    feed(p, 10000);
    printf("\n  (a refused speaker was started %d times in 10 s)\n", spk.starts);
    check("a speaker that will not stay started is not started fifty times a second", spk.starts < 100);
    check("and it is still tried again, later", spk.starts > 20);
  }
  {
    now_ms = 1000;
    Refused spk;
    spk.ok_after_ms = now_ms + 1500;
    portall::Portall p;
    p.set_speaker(&spk);
    p.setup_speaker_();
    feed(p, 10000);
    check("once the speaker accepts, the sound comes back", spk.taken > 0);
    check("and plays on without another hold", spk.taken >= 960u * 2 * 300);
    check("and the next refusal starts again from the short hold", p.audio_hold_ms_ == 0);
  }
  {
    now_ms = 1000;
    Slow spk;
    portall::Portall p;
    p.set_speaker(&spk);
    p.setup_speaker_();
    feed(p, 2000);
    check("a speaker that starts a few blocks late is not held off", p.audio_hold_until_ms_ == 0);
    check("and plays everything after it started", spk.taken == 960u * (200 - 5));
  }
  {
    now_ms = 1000;
    Full spk;
    portall::Portall p;
    p.set_speaker(&spk);
    p.setup_speaker_();
    feed(p, 2000);
    check("a speaker that is running but full is not held off", p.audio_hold_until_ms_ == 0);
    check("and is offered every block, as before", spk.plays == 200);
  }
  printf(fails ? "%d problem(s)\n" : "All good.\n", fails);
  return fails ? 1 : 0;
}
