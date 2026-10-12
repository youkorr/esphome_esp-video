/* The decode and the draw, overlapped -- run on a workstation.
 *
 * This links the SHIPPED portall.cpp and pipeline.cpp: the real feed_()
 * parser, the real run_decode_task(), the real draw task and the real
 * report_stats_(). What is not real is the hardware under them -- a JPEG
 * engine, an accelerator and a display that each take a set time, sleeping
 * the way the real ones block on their DMA, and that write and read a
 * picture number in the buffers they are handed.
 *
 * So it can say four things nothing else here can:
 *   - every picture reaches the display, once, in the order it arrived,
 *     whole pictures and rectangles alike, with decode failures in between;
 *   - no decode buffer is written while the display (or the accelerator) is
 *     still reading it -- the one way two buffers could show a torn picture;
 *   - the two engines really work at the same time, measured as the time
 *     their busy intervals overlap, and with decode_buffers: 1 they never do;
 *   - what that is worth in pictures a second, against decode_buffers: 1 on
 *     the same fake engines.
 *
 * What it cannot say is anything about the P4: its memory bandwidth, how the
 * 2D-DMA shares its channels, or what the board's own stats line will read.
 * That is one flash away. */
#include "portall.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using namespace esphome;
using Clock = std::chrono::steady_clock;

static const Clock::time_point T0 = Clock::now();
static uint64_t now_us() { return std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - T0).count(); }
static void sleep_us(uint32_t us) { std::this_thread::sleep_for(std::chrono::microseconds(us)); }

namespace esphome {
uint32_t micros() { return (uint32_t) now_us(); }
uint32_t millis() { return (uint32_t) (now_us() / 1000); }
uint64_t millis_64() { return now_us() / 1000; }
void delay(uint32_t ms) { sleep_us(ms * 1000); }
void delayMicroseconds(uint32_t us) { sleep_us(us); }
uint32_t arch_get_cpu_cycle_count() { return micros(); }

static std::mutex g_log_lock;
static std::vector<std::string> g_log;
void esp_log_printf_(int, const char *, int, const char *format, ...) {
  char line[512];
  va_list args;
  va_start(args, format);
  vsnprintf(line, sizeof(line), format, args);
  va_end(args);
  std::lock_guard<std::mutex> lock(g_log_lock);
  g_log.emplace_back(line);
  if (getenv("PIPETEST_LOG"))
    printf("    | %s\n", line);
}
}  // namespace esphome

// ---------------------------------------------------------------------------
// The engines, timed
// ---------------------------------------------------------------------------
static std::atomic<uint32_t> g_decode_us{10000}, g_ppa_us{0}, g_draw_us{8000};
static std::atomic<uint32_t> g_torn{0};
struct Busy {
  uint64_t from, to;
};
static std::mutex g_busy_lock;
static std::vector<Busy> g_decoding, g_drawing;
static void busy(std::vector<Busy> &into, uint64_t from) {
  std::lock_guard<std::mutex> lock(g_busy_lock);
  into.push_back({from, now_us()});
}

static uint32_t read_seq(const void *p) {
  uint32_t seq;
  memcpy(&seq, p, 4);
  return seq;
}
static const uint32_t BEING_WRITTEN = 0xEEEEEEEE;

extern "C" {
const char *esp_err_to_name(esp_err_t) { return "ESP_FAIL"; }
esp_err_t jpeg_new_decoder_engine(const jpeg_decode_engine_cfg_t *, jpeg_decoder_handle_t *out) {
  *out = (jpeg_decoder_handle_t) 1;
  return ESP_OK;
}
void *jpeg_alloc_decoder_mem(size_t size, const jpeg_decode_memory_alloc_cfg_t *, size_t *allocated) {
  size = (size + 63) & ~(size_t) 63;
  *allocated = size;
  return aligned_alloc(64, size);
}
// The "JPEG" is a picture number and a byte that says whether to fail.
esp_err_t jpeg_decoder_process(jpeg_decoder_handle_t, const jpeg_decode_cfg_t *, const uint8_t *in, uint32_t,
                               uint8_t *out, uint32_t, uint32_t *out_size) {
  const uint64_t from = now_us();
  memcpy(out, &BEING_WRITTEN, 4);
  sleep_us(g_decode_us);
  busy(g_decoding, from);
  if (in[4] == 0xBA)
    return 1;
  memcpy(out, in, 4);
  *out_size = 4;
  return ESP_OK;
}
int ppa_register_client(const ppa_client_config_t *, ppa_client_handle_t *out) {
  *out = (ppa_client_handle_t) 1;
  return ESP_OK;
}
int ppa_do_scale_rotate_mirror(ppa_client_handle_t, const ppa_srm_oper_config_t *cfg) {
  const uint64_t from = now_us();
  const uint32_t before = read_seq(cfg->in.buffer);
  sleep_us(g_ppa_us);
  if (read_seq(cfg->in.buffer) != before)
    g_torn++;
  memcpy(cfg->out.buffer, &before, 4);
  busy(g_drawing, from);
  return ESP_OK;
}
size_t heap_caps_get_free_size(unsigned) { return 0; }
void *heap_caps_aligned_alloc(size_t align, size_t size, unsigned) {
  return aligned_alloc(align, (size + align - 1) & ~(align - 1));
}
void heap_caps_free(void *p) { free(p); }
}

struct Drawn {
  uint32_t seq;
  int x, y, w, h;
};

class FakeDisplay : public display::Display {
 public:
  FakeDisplay(int w, int h) : w_(w), h_(h) {}
  void update() override {}
  void draw_pixel_at(int, int, Color) override {}
  display::DisplayType get_display_type() override { return display::DISPLAY_TYPE_COLOR; }
  int get_width_internal() override { return w_; }
  int get_height_internal() override { return h_; }
  void draw_pixels_at(int x, int y, int w, int h, const uint8_t *ptr, display::ColorOrder, display::ColorBitness,
                      bool, int, int, int) override {
    const uint64_t from = now_us();
    const uint32_t before = read_seq(ptr);
    sleep_us(g_draw_us);
    if (read_seq(ptr) != before)
      g_torn++;
    busy(g_drawing, from);
    std::lock_guard<std::mutex> lock(lock_);
    drawn_.push_back({before, x, y, w, h});
  }
  std::vector<Drawn> drawn() {
    std::lock_guard<std::mutex> lock(lock_);
    return drawn_;
  }

 private:
  int w_, h_;
  std::mutex lock_;
  std::vector<Drawn> drawn_;
};

// ---------------------------------------------------------------------------
// What portall.cpp calls in files this does not link
// ---------------------------------------------------------------------------
namespace esphome {
namespace portall {
void Portall::setup_network_() {}
}  // namespace portall
}  // namespace esphome

// ---------------------------------------------------------------------------
// Feeding it
// ---------------------------------------------------------------------------
static int g_faults = 0;
static void ok(bool good, const std::string &what) {
  printf("  %s %s\n", good ? "ok    " : "ECHEC ", what.c_str());
  if (!good)
    g_faults++;
}

struct Picture {
  uint32_t seq;
  uint16_t x, y, w, h;
  uint16_t id;
  bool bad;
};

static void send(portall::Portall *p, const Picture &pic) {
  uint8_t buf[sizeof(udisp_frame_header_t) + 16] = {};
  auto *h = (udisp_frame_header_t *) buf;
  h->type = UDISP_TYPE_JPG;
  h->x = pic.x;
  h->y = pic.y;
  h->width = pic.w;
  h->height = pic.h;
  h->frame_id = pic.id;
  h->payload_total = 16;
  uint8_t *payload = buf + sizeof(udisp_frame_header_t);
  memcpy(payload, &pic.seq, 4);
  payload[4] = pic.bad ? 0xBA : 0x00;
  // Paced, the way the network path is: a full set of frame buffers holds
  // the sender back rather than throwing a picture away.
  p->feed_(buf, sizeof(buf), true);
}

struct Run {
  FakeDisplay *display;
  portall::Portall *panel;
};

static const int W = 64, H = 32;

static Run start(uint8_t decode_buffers, uint16_t rotation) {
  {
    std::lock_guard<std::mutex> lock(g_busy_lock);
    g_decoding.clear();
    g_drawing.clear();
  }
  g_torn = 0;
  const bool quarter = rotation == 90 || rotation == 270;
  auto *display = new FakeDisplay(quarter ? H : W, quarter ? W : H);
  auto *panel = new portall::Portall();
  panel->set_display(display);
  // The resolution is what the host sends; a quarter turn lands it on a
  // panel the other way round.
  panel->set_resolution(W, H);
  panel->set_frame_buffers(4);
  panel->set_max_frame_bytes(1024);
  panel->set_rotation(rotation);
  panel->set_decode_buffers(decode_buffers);
  panel->setup();
  return {display, panel};
}

static bool wait_for(FakeDisplay *d, size_t n, uint32_t ms) {
  const uint64_t until = now_us() + ms * 1000ull;
  while (now_us() < until) {
    if (d->drawn().size() >= n)
      return true;
    sleep_us(1000);
  }
  return d->drawn().size() >= n;
}

static uint64_t overlap_us() {
  std::lock_guard<std::mutex> lock(g_busy_lock);
  uint64_t total = 0;
  for (const auto &a : g_decoding)
    for (const auto &b : g_drawing) {
      const uint64_t from = std::max(a.from, b.from), to = std::min(a.to, b.to);
      if (to > from)
        total += to - from;
    }
  return total;
}

// Sixty whole pictures, sent as fast as the board takes them.
static double stream(Run &run, int count, uint32_t first_seq) {
  const uint64_t from = now_us();
  for (int i = 0; i < count; i++)
    send(run.panel, {first_seq + (uint32_t) i, 0, 0, W, H, (uint16_t) ((first_seq + i) & 0x3FF), false});
  wait_for(run.display, (size_t) count, 10000);
  return (now_us() - from) / 1e6;
}

static bool in_order(const std::vector<Drawn> &drawn, uint32_t first, size_t count) {
  if (drawn.size() != count)
    return false;
  for (size_t i = 0; i < count; i++)
    if (drawn[i].seq != first + i)
      return false;
  return true;
}

int main(int argc, char **argv) {
  const bool quick = argc > 1 && strcmp(argv[1], "--quick") == 0;
  const int N = 60;

  printf("Whole pictures, decode 10 ms and draw 8 ms:\n");
  g_decode_us = 10000;
  g_ppa_us = 0;
  g_draw_us = 8000;
  Run one = start(1, 0);
  const double one_s = stream(one, N, 1000);
  const auto one_drawn = one.display->drawn();
  const uint64_t one_overlap = overlap_us();
  ok(in_order(one_drawn, 1000, N), "decode_buffers: 1 draws every picture once, in order");
  ok(one_overlap == 0, "and the decoder and the display never work at the same time (" +
                           std::to_string(one_overlap / 1000) + " ms together)");

  Run two = start(2, 0);
  const double two_s = stream(two, N, 2000);
  const auto two_drawn = two.display->drawn();
  const uint64_t two_overlap = overlap_us();
  ok(in_order(two_drawn, 2000, N), "decode_buffers: 2 draws every picture once, in order");
  ok(g_torn == 0, "and no buffer is written while the display reads it");
  ok(two_overlap > (uint64_t) N * 4000, "the decoder works while the display copies (" +
                                            std::to_string(two_overlap / 1000) + " ms together over " +
                                            std::to_string(N) + " pictures)");
  char line[160];
  snprintf(line, sizeof(line), "and it is faster: %.1f pictures/s against %.1f (x%.2f; the arithmetic says 100 "
           "against 56)", N / two_s, N / one_s, one_s / two_s);
  ok(one_s / two_s > 1.4, line);

  // The case that can tear: with the draw the slower of the two, the
  // decoder is always ready for a buffer before the display has finished
  // with one, so a buffer handed back a moment early is written while it is
  // still being copied. With the decode slower it never would be, which is
  // why the case above cannot catch it on its own.
  printf("\nThe display slower than the decoder, decode 4 ms and draw 12 ms:\n");
  g_decode_us = 4000;
  g_ppa_us = 0;
  g_draw_us = 12000;
  Run slow1 = start(1, 0);
  const double slow1_s = stream(slow1, N, 6000);
  Run slow2 = start(2, 0);
  const double slow2_s = stream(slow2, N, 7000);
  ok(in_order(slow2.display->drawn(), 7000, N), "every picture drawn once, in order");
  ok(g_torn == 0, "no buffer is written while the display reads it, with the display the slower");
  snprintf(line, sizeof(line), "%.1f pictures/s against %.1f with one buffer (x%.2f; the arithmetic says 83 "
           "against 62)", N / slow2_s, N / slow1_s, slow1_s / slow2_s);
  ok(slow1_s / slow2_s > 1.2, line);

  printf("\nTurned 90 degrees, decode 10 ms, accelerator 12 ms, draw 3 ms:\n");
  g_decode_us = 10000;
  g_ppa_us = 12000;
  g_draw_us = 3000;
  Run turned1 = start(1, 90);
  const double t1 = stream(turned1, N, 3000);
  Run turned2 = start(2, 90);
  const double t2 = stream(turned2, N, 4000);
  ok(in_order(turned2.display->drawn(), 4000, N), "every picture drawn once, in order, through the accelerator");
  ok(g_torn == 0, "and the accelerator never reads a buffer being decoded into");
  snprintf(line, sizeof(line), "%.1f pictures/s against %.1f with one buffer (x%.2f; the arithmetic says 67 "
           "against 40)", N / t2, N / t1, t1 / t2);
  ok(t1 / t2 > 1.4, line);

  printf("\nRectangles and failures mixed in:\n");
  g_decode_us = 3000;
  g_ppa_us = 0;
  g_draw_us = 5000;
  Run mixed = start(2, 0);
  std::vector<Picture> sent;
  uint32_t seq = 5000;
  for (int k = 0; k < 10; k++) {
    // A picture of four rectangles, one id, then a failure, then a whole one.
    const uint16_t id = (uint16_t) (100 + k);
    for (int r = 0; r < 4; r++)
      sent.push_back({seq++, (uint16_t) ((r % 2) * 32), (uint16_t) ((r / 2) * 16), 32, 16, id, false});
    sent.push_back({seq++, 0, 0, W, H, (uint16_t) (200 + k), true});
    sent.push_back({seq++, 0, 0, W, H, (uint16_t) (300 + k), false});
  }
  for (const auto &pic : sent)
    send(mixed.panel, pic);
  size_t good = 0;
  for (const auto &pic : sent)
    good += pic.bad ? 0 : 1;
  wait_for(mixed.display, good, 5000);
  sleep_us(50000);
  const auto mixed_drawn = mixed.display->drawn();
  bool order = mixed_drawn.size() == good;
  bool place = true;
  size_t at = 0;
  for (const auto &pic : sent) {
    if (pic.bad)
      continue;
    if (at >= mixed_drawn.size() || mixed_drawn[at].seq != pic.seq) {
      order = false;
      break;
    }
    const auto &d = mixed_drawn[at++];
    if (d.x != pic.x || d.y != pic.y || d.w != pic.w || d.h != pic.h)
      place = false;
  }
  ok(order, "every good rectangle drawn once, in the order it arrived (" + std::to_string(mixed_drawn.size()) +
                " of " + std::to_string(good) + ")");
  ok(place, "each where it was sent");
  ok(g_torn == 0, "nothing torn");
  ok(true, "ten failed decodes gave their buffers back, or the eleventh picture would never have come");

  if (!quick) {
    // checkpipeline.py builds this with the stats interval at one second
    // rather than five -- the only line of portall.cpp it changes.
    printf("\nThe stats line (two seconds of pictures, both ways at once, decode 5 ms, draw 12 ms):\n");
    g_decode_us = 5000;
    g_ppa_us = 0;
    g_draw_us = 12000;
    {
      std::lock_guard<std::mutex> lock(g_log_lock);
      g_log.clear();
    }
    Run s1 = start(1, 0);
    Run s2 = start(2, 0);
    std::thread a([&] {
      for (uint32_t i = 0; i < 130; i++)
        send(s1.panel, {i, 0, 0, W, H, (uint16_t) (i & 0x3FF), false});
    });
    std::thread b([&] {
      for (uint32_t i = 0; i < 180; i++)
        send(s2.panel, {i, 0, 0, W, H, (uint16_t) (i & 0x3FF), false});
    });
    a.join();
    b.join();
    std::string serial, piped;
    {
      std::lock_guard<std::mutex> lock(g_log_lock);
      for (const auto &l : g_log) {
        if (l.find("us/decode") == std::string::npos)
          continue;
        if (l.find("decoder waited") != std::string::npos)
          piped = l;
        else
          serial = l;
      }
    }
    printf("    %s\n    %s\n", serial.c_str(), piped.c_str());
    // Read before the lines that report them: the arguments of one call are
    // evaluated in no promised order.
    unsigned dec = 0, waited = 0;
    const char *f = serial.empty() ? nullptr : strstr(serial.c_str(), "fps, ");
    const bool has_dec = f != nullptr && sscanf(f, "fps, %u us/decode", &dec) == 1;
    ok(has_dec && dec >= 4500 && dec < 8000,
       "one buffer: the line says how long a decode took (" + std::to_string(dec) + " us)");
    const char *w = piped.empty() ? nullptr : strstr(piped.c_str(), "decoder waited ");
    const bool has_wait = w != nullptr && sscanf(w, "decoder waited %u", &waited) == 1;
    // Draw 12 ms and decode 5: the decoder has nothing to do for 7 of every
    // 12 ms, which is the display setting the pace -- and the line has to say so.
    ok(has_wait && waited > 350 && waited < 800,
       "two buffers: and that the decoder waited for the display, here most of the time (" +
           std::to_string(waited) + " ms/s; the arithmetic says 580)");
    ok(serial.find("decoder waited") == std::string::npos, "and one buffer says nothing about waiting");
  }

  if (g_faults) {
    printf("\n%d problem(s).\n", g_faults);
    return 1;
  }
  printf("\nAll good.\n");
  return 0;
}
