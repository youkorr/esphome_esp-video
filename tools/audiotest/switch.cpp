/* audio.cpp switching between mono and stereo, driven through a recording speaker. Built by tools/checkstereo.py. */
#include "portall.h"
#include <cstdio>
#include <vector>
namespace esphome { uint32_t millis() { return 0; } }
using namespace esphome;
struct Fake : speaker::Speaker {
  std::vector<size_t> plays; int stops = 0;
  size_t play(const uint8_t *, size_t n) override { plays.push_back(n); return n; }
  void start() override { state_ = speaker::STATE_RUNNING; }
  void stop() override { stops++; state_ = speaker::STATE_STOPPING; }
  bool has_buffered_data() const override { return false; }
  void finish_stopping() { state_ = speaker::STATE_STOPPED; }
};
static int fails = 0;
static void check(const char *what, bool ok) { printf("\n  %s  %s\n", ok ? "ok   " : "ECHEC", what); if (!ok) fails++; }
int main() {
  Fake spk; portall::Portall p; p.set_speaker(&spk); p.setup_speaker_();
  check("mono by default, and told so at setup", spk.get_audio_stream_info().get_channels() == 1);
  check("a mono block is 10 ms: 960 bytes", p.audio_block_size_ == 960);
  std::vector<uint8_t> pcm(1920, 1);
  // A stereo stream arriving at a speaker that never started: told at once.
  p.on_audio_samples(pcm.data(), 1920, 2);
  check("stereo arriving at a stopped speaker re-tells it two channels", spk.get_audio_stream_info().get_channels() == 2);
  check("and the block grows to 1920 bytes", p.audio_block_size_ == 1920);
  check("and the first stereo block reaches it whole", spk.plays.size() == 1 && spk.plays[0] == 1920);
  // Back to mono while it plays: stopped first, nothing sent in the old shape.
  spk.plays.clear();
  p.on_audio_samples(pcm.data(), 960, 1);
  check("mono arriving while it plays stereo stops it first", spk.stops == 1);
  check("and sends nothing in the wrong shape meanwhile", spk.plays.empty() && spk.get_audio_stream_info().get_channels() == 2);
  spk.finish_stopping();
  p.on_audio_samples(pcm.data(), 960, 1);
  check("once stopped it is re-told one channel", spk.get_audio_stream_info().get_channels() == 1);
  check("and mono flows again in 960-byte blocks", spk.plays.size() == 1 && spk.plays[0] == 960);
  // The default argument: USB's path, and every sender before stereo.
  spk.plays.clear();
  p.on_audio_samples(pcm.data(), 960);
  check("a call with no channel count is mono, as before", spk.plays.size() == 1 && spk.plays[0] == 960);
  printf(fails ? "%d problem(s)\n" : "All good.\n", fails);
  return fails ? 1 : 0;
}
