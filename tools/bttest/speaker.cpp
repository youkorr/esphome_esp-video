// Does the panel's own sound reach the Bluetooth speaker with both channels
// carrying it, and does a sample split across two calls survive?
//
// portall produces 48000 Hz 16-bit MONO and A2DP takes 44100 Hz 16-bit STEREO.
// ESPHome's resampler does the rate and REFUSES the channel change --
// AudioResampler::start() returns ESP_ERR_NOT_SUPPORTED when the counts differ
// -- so the duplication is this component's, and it is the one piece of new
// arithmetic on the path. A syntax check cannot see arithmetic, which is why
// this links against the shipped play() rather than describing it.
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main

#include "speaker/portall_bt_speaker.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <unistd.h>
#include <vector>

#include "linkstubs.h"

using esphome::portall_bt::PortallBT;
using esphome::portall_bt::PortallBTSpeaker;

static int failures = 0;

static void check(const char *what, bool ok) {
  printf("  %s     %s\n", ok ? "ok   " : "ECHEC", what);
  if (!ok)
    failures++;
}

// Everything waiting in the component's ring, read back through the SHIPPED
// fill_pcm -- which is what Bluedroid's encoder calls, so this is the path the
// sink really hears. Only what is queued is taken: fill_pcm pads a short read
// with silence by design, and asking for more than there is would measure the
// padding rather than the sound.
static std::vector<uint8_t> drain(PortallBT &bt) {
  const uint32_t queued = bt.pcm_queued();
  std::vector<uint8_t> out(queued);
  if (queued == 0)
    return out;
  bt.fill_pcm(out.data(), queued);
  return out;
}

static std::vector<uint8_t> mono(const std::vector<int16_t> &samples) {
  std::vector<uint8_t> bytes;
  for (int16_t sample : samples) {
    bytes.push_back((uint8_t) (sample & 0xFF));
    bytes.push_back((uint8_t) ((sample >> 8) & 0xFF));
  }
  return bytes;
}

static std::vector<int16_t> as_samples(const std::vector<uint8_t> &bytes) {
  std::vector<int16_t> out;
  for (size_t i = 0; i + 1 < bytes.size(); i += 2)
    out.push_back((int16_t) ((uint16_t) bytes[i] | ((uint16_t) bytes[i + 1] << 8)));
  return out;
}

int main() {
  // A connected sink. Sound handed over with nothing at the other end is
  // deliberately dropped, so every case below needs this first -- and the last
  // case checks that it IS dropped.
  PortallBT bt;
  esp_bd_addr_t address = {0x46, 0xE8, 0x1C, 0x8A, 0x88, 0xDD};

  PortallBTSpeaker speaker;
  speaker.set_parent(&bt);
  speaker.set_audio_stream_info(esphome::audio::AudioStreamInfo(16, 1, 44100));

  // Nothing paired yet: this must be accepted and thrown away, never refused.
  // Returning 0 would make portall print its "the speaker is refusing this
  // stream" diagnosis, which names resamplers and mixers and would be a
  // confident explanation of the wrong fault.
  speaker.start();
  const std::vector<uint8_t> before = mono({100, 200, 300});
  const size_t took = speaker.play(before.data(), before.size());
  check("with nothing paired the sound is accepted and dropped",
        took == before.size() && bt.pcm_queued() == 0);

  bt.on_a2dp_open(address);
  check("the sink reads as connected", bt.speaker_connected());

  // One mono sample must come out as two identical ones.
  speaker.start();
  const std::vector<uint8_t> four = mono({1000, -2000, 3000, -4000});
  speaker.play(four.data(), four.size());
  std::vector<int16_t> got = as_samples(drain(bt));
  const std::vector<int16_t> want = {1000, 1000, -2000, -2000, 3000, 3000, -4000, -4000};
  check("mono goes out on both channels, in order", got == want);

  // The odd byte. Nothing in this project splits a sample today -- portall
  // flushes 1920-byte blocks and the resampler emits whole frames -- but half
  // a sample kept as a whole one would swap the two channels for the rest of
  // the stream, which from a car is a fault nobody can diagnose.
  speaker.start();
  const std::vector<uint8_t> whole = mono({0x1234, 0x5678});
  speaker.play(whole.data(), 3);   // one sample and the low byte of the next
  speaker.play(whole.data() + 3, 1);  // and its high byte, next call
  got = as_samples(drain(bt));
  const std::vector<int16_t> both = {0x1234, 0x1234, 0x5678, 0x5678};
  check("a sample split across two calls is not torn", got == both);

  // Two channels already: the bytes are the wire format as they stand and
  // nothing may be inserted.
  speaker.set_audio_stream_info(esphome::audio::AudioStreamInfo(16, 2, 44100));
  speaker.start();
  const std::vector<uint8_t> stereo = mono({11, 22, 33, 44});
  speaker.play(stereo.data(), stereo.size());
  check("stereo passes through untouched", drain(bt) == stereo);

  /* The mistake a household will really make: speaker_id: pointed straight at
   * this platform with no resampler between, so it is handed portall's own
   * 48000 Hz. Nothing here can resample, so the sound reaches the sink and is
   * decoded at 44100 anyway -- everything fast and high, which from a car
   * reads as the panel being broken rather than as one missing YAML block.
   * The log line is the ONLY thing that will tell anybody, so it is checked
   * rather than assumed, and it has to name the resampler by name. */
  {
    PortallBTSpeaker wrong;
    wrong.set_parent(&bt);
    wrong.set_audio_stream_info(esphome::audio::AudioStreamInfo(16, 1, 48000));
    wrong.start();

    char path[] = "/tmp/portall_bt_rateXXXXXX";
    const int hole = mkstemp(path);
    const int saved = dup(fileno(stdout));
    fflush(stdout);
    dup2(hole, fileno(stdout));
    const std::vector<uint8_t> any = mono({1, 2});
    wrong.play(any.data(), any.size());
    fflush(stdout);
    dup2(saved, fileno(stdout));
    close(saved);
    close(hole);

    std::string said;
    FILE *back = fopen(path, "rb");
    char buf[512];
    size_t n;
    while (back != nullptr && (n = fread(buf, 1, sizeof(buf), back)) > 0)
      said.append(buf, n);
    if (back != nullptr)
      fclose(back);
    remove(path);

    check("a rate A2DP cannot carry is named, with the way out",
          said.find("48000") != std::string::npos && said.find("44100") != std::string::npos &&
              said.find("resampler") != std::string::npos);
    drain(bt);
  }

  /* THE "SHUUUT". A ring that drops or copies BYTES puts its indexes off the
   * four-byte frame grid, and from then on every sample the encoder is handed
   * is the high byte of one and the low byte of the next -- full-scale hiss,
   * reported from a car as a "shuuut" noise with audio in between. This fills
   * the ring past its capacity, which is the only thing that ever triggered
   * it: the test tone is generated when the ring is EMPTY, so the one path
   * anybody had listened to could not overrun. */
  {
    PortallBTSpeaker fast;
    fast.set_parent(&bt);
    fast.set_audio_stream_info(esphome::audio::AudioStreamInfo(16, 1, 44100));
    fast.start();
    drain(bt);

    /* A ramp, so a misalignment is unmistakable: consecutive frames differ by
     * one, and a stream read half a sample out reads as huge alternating
     * values instead. Far more than the ring can hold, in blocks that are a
     * whole number of mono samples but NOT a whole number of A2DP frames --
     * 1922 bytes is 961 samples, which is what makes the old code drop an odd
     * count and lose the grid. */
    std::vector<int16_t> ramp;
    for (int i = 0; i < 961; i++)
      ramp.push_back((int16_t) (i & 0x3FFF));
    const std::vector<uint8_t> block = mono(ramp);
    for (int round = 0; round < 40; round++)
      fast.play(block.data(), block.size());

    const std::vector<uint8_t> got = drain(bt);
    check("an overrun leaves a whole number of frames", got.size() % 4 == 0);

    // Every frame's two channels must still be the same sample. They are only
    // ever written as a pair, so anything else is the grid having slipped.
    bool paired = !got.empty();
    for (size_t i = 0; i + 3 < got.size(); i += 4)
      paired = paired && got[i] == got[i + 2] && got[i + 1] == got[i + 3];
    check("and no frame has its two channels out of step", paired);

    // The ring holds what it says it holds, rather than growing without limit.
    check("and the ring did not grow past its own size", got.size() <= 35280);
  }

  /* THE VOLUME. `portall.set_volume` hands the value down the speaker chain
   * and every block passes it on, so it arrives here -- where the base class
   * would give it to an audio_dac that a Bluetooth speaker does not have.
   * Reported as a slider that moved and did nothing. */
  {
    PortallBTSpeaker quiet;
    quiet.set_parent(&bt);
    quiet.set_audio_stream_info(esphome::audio::AudioStreamInfo(16, 1, 44100));
    quiet.start();
    drain(bt);

    quiet.set_volume(0.5f);
    const std::vector<uint8_t> loud = mono({1000, -1000});
    quiet.play(loud.data(), loud.size());
    const std::vector<uint8_t> half = drain(bt);
    const int16_t first = (int16_t) ((uint16_t) half[0] | ((uint16_t) half[1] << 8));
    const int16_t second = (int16_t) ((uint16_t) half[4] | ((uint16_t) half[5] << 8));
    check("half volume halves the samples", half.size() == 8 && first == 500 && second == -500);

    quiet.set_volume(1.0f);
    quiet.play(loud.data(), loud.size());
    const std::vector<uint8_t> full = drain(bt);
    const int16_t back = (int16_t) ((uint16_t) full[0] | ((uint16_t) full[1] << 8));
    check("and full volume leaves them alone", back == 1000);

    quiet.set_mute_state(true);
    quiet.play(loud.data(), loud.size());
    const std::vector<uint8_t> muted = drain(bt);
    bool silent = muted.size() == 8;
    for (uint8_t byte : muted)
      silent = silent && byte == 0;
    check("mute is silence, not a quiet noise", silent);

    /* And unmuting returns to the VOLUME, not to full -- which is why mute is
     * its own flag rather than a gain of zero. */
    quiet.set_volume(0.5f);
    quiet.set_mute_state(false);
    quiet.play(loud.data(), loud.size());
    const std::vector<uint8_t> after = drain(bt);
    const int16_t restored = (int16_t) ((uint16_t) after[0] | ((uint16_t) after[1] << 8));
    check("and unmuting comes back to the volume that was set", restored == 500);
  }

  // And a starved read is silence rather than a short answer, because A2DP has
  // a clock at the other end: fewer bytes than asked for is a gap in a stream
  // being decoded at a fixed rate, which is a click.
  uint8_t ask[64];
  memset(ask, 0xAA, sizeof(ask));
  const uint32_t filled = bt.fill_pcm(ask, sizeof(ask));
  bool all_silent = filled == sizeof(ask);
  for (uint8_t byte : ask)
    all_silent = all_silent && byte == 0;
  check("an empty ring answers in full, with silence", all_silent);

  return failures == 0 ? 0 : 1;
}
