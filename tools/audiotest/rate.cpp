/* audio.cpp with sample_rate: set, driven through a recording speaker. Built
 * by tools/checksamplerate.py.
 *
 * The rate is the one thing the panel and the add-on have to agree on, and it
 * crosses the wire twice: the board asks for it, and each block of sound says
 * what it is. These are the board's half: what it tells its speaker, how big
 * its blocks are, and what it does with sound at the wrong rate. */
#include "portall.h"
#include <cstdio>
#include <vector>
namespace esphome {
static uint32_t now_ms = 0;
uint32_t millis() { return now_ms; }
}  // namespace esphome
using namespace esphome;
struct Fake : speaker::Speaker {
  std::vector<size_t> plays;
  size_t play(const uint8_t *, size_t n) override { plays.push_back(n); return n; }
  void start() override { state_ = speaker::STATE_RUNNING; }
  void stop() override { state_ = speaker::STATE_STOPPED; }
  bool has_buffered_data() const override { return false; }
};
static int fails = 0;
static void check(const char *what, bool ok) {
  printf("\n  %s  %s\n", ok ? "ok   " : "ECHEC", what);
  if (!ok)
    fails++;
}
int main() {
  std::vector<uint8_t> pcm(3528, 1);
  {
    Fake spk;
    portall::Portall p;
    p.set_speaker(&spk);
    p.set_sample_rate(44100);
    p.setup_speaker_();
    check("the speaker is told 44100 at setup", spk.get_audio_stream_info().get_sample_rate() == 44100);
    check("a mono block is 10 ms at 44100: 441 frames, 882 bytes", p.audio_block_size_ == 882);
    // What a sender that has not switched yet sends: 48000, height 0.
    now_ms = 1000;
    p.on_audio_samples(pcm.data(), 1920);
    check("sound at 48000 is not played on a panel that asked for 44100", spk.plays.empty());
    now_ms = 1500;
    p.on_audio_samples(pcm.data(), 1920);
    check("and half a second of it is the start of a stream, said nowhere", !p.logged_rate_mismatch_);
    // The sender heard and switched.
    now_ms = 1600;
    p.on_audio_samples(pcm.data(), 1764, 1, 44100);
    check("sound at 44100 is played in 882-byte blocks",
          spk.plays.size() == 2 && spk.plays[0] == 882 && spk.plays[1] == 882);
    check("and the switch is still not reported", !p.logged_rate_mismatch_);
    // A sender that never switches: 48000 for longer than the patience.
    spk.plays.clear();
    for (now_ms = 5000; now_ms <= 8000; now_ms += 20)
      p.on_audio_samples(pcm.data(), 1920);
    check("a sender that never switches is played nothing", spk.plays.empty());
    check("and after two seconds of it the panel says why, once", p.logged_rate_mismatch_);
    // Stereo at the asked-for rate.
    now_ms = 9000;
    spk.stop();
    p.on_audio_samples(pcm.data(), 3528, 2, 44100);
    check("stereo at 44100 is told 44100 and two channels",
          spk.get_audio_stream_info().get_sample_rate() == 44100 &&
              spk.get_audio_stream_info().get_channels() == 2);
    check("in 1764-byte blocks", !spk.plays.empty() && spk.plays.back() == 1764);
  }
  {
    // No sample_rate: at all -- every panel before it, and every one on USB.
    Fake spk;
    portall::Portall p;
    p.set_speaker(&spk);
    p.setup_speaker_();
    check("without it the speaker is told 48000, as before", spk.get_audio_stream_info().get_sample_rate() == 48000);
    now_ms = 20000;
    p.on_audio_samples(pcm.data(), 1920);
    check("and a header with no rate plays at once, in 960-byte blocks",
          spk.plays.size() == 2 && spk.plays[0] == 960);
  }
  printf(fails ? "%d problem(s)\n" : "All good.\n", fails);
  return fails ? 1 : 0;
}
