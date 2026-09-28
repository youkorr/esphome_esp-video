// Links the SHIPPED canvas_tick_() / copy_to_canvas_() out of
// components/portall/portall.cpp against a real LVGL, and draws with it on a
// display that has no panel: what reaches the screen is read back from the
// flush callback. Run by tools/checkcanvas.py, which builds LVGL first.
#include "lvgl.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) (void) (x)
#define portEXIT_CRITICAL(x) (void) (x)
static int g_errors = 0, g_infos = 0;
#define ESP_LOGE(tag, ...) (g_errors++, printf("    E: " __VA_ARGS__), printf("\n"))
#define ESP_LOGI(tag, ...) (g_infos++, printf("    I: " __VA_ARGS__), printf("\n"))
static const char *const TAG = "portall";
// The cache write-back is counted rather than done: a workstation has one
// coherent memory, and the question is only whether it is asked for.
#define ESP_CACHE_MSYNC_FLAG_DIR_C2M 1
#define ESP_CACHE_MSYNC_FLAG_TYPE_DATA 2
#define ESP_CACHE_MSYNC_FLAG_UNALIGNED 4
static int g_msyncs = 0;
static int esp_cache_msync(void *, size_t, int) { return g_msyncs++, 0; }
static bool esp_ptr_external_ram(const void *) { return true; }
// The decode task's clock and its sleep. A sleep advances the clock by one
// millisecond and, when asked, runs LVGL's refresh -- which is what the loop
// does on the board while the decoder sleeps.
static uint32_t g_ms = 0;
static int g_delays = 0, g_refresh_after = -1;
static lv_display_t *g_disp = nullptr;
struct Portall;
static Portall *g_ticker = nullptr;  // whose loop runs at g_refresh_after
static uint32_t millis() { return g_ms; }
static void tick_loop();
static void vTaskDelay(uint32_t) {
  g_ms++;
  if (++g_delays == g_refresh_after)
    tick_loop();
}
struct Portall {
  void canvas_tick_();
  void copy_to_canvas_(const uint8_t *pixels, uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t src_stride_px);
  void canvas_wait_(uint16_t picture);
  bool allocate_canvas_pair_();
  bool canvas_settle_();
  uint8_t *canvas_back_() const { return this->canvas_pair_[this->canvas_front_ ^ 1]; }
  void canvas_publish_();
  bool canvas_zero_copy_() const { return this->canvas_buf_.load() == &this->canvas_shown_; }
  uint8_t *canvas_pair_[2]{nullptr, nullptr};
  size_t canvas_pair_len_{0};
  lv_draw_buf_t canvas_shown_{};
  uint8_t canvas_front_{0};
  std::atomic<int8_t> canvas_swap_{-1};
  std::atomic<uint32_t> canvas_swaps_{0};
  uint16_t padded_width_{0}, padded_height_{0};
  static void canvas_rendered_(lv_event_t *event);
  std::atomic<bool> canvas_inflight_{false};
  uint16_t canvas_picture_{0xFFFF};
  uint32_t canvas_wait_ms_{0};
  lv_obj_t *canvas_{nullptr};
  std::atomic<lv_draw_buf_t *> canvas_buf_{nullptr};
  bool canvas_refused_{false};
  portMUX_TYPE canvas_lock_ = portMUX_INITIALIZER_UNLOCKED;
  bool canvas_dirty_{false};
  lv_area_t canvas_dirty_area_{};
  uint16_t out_width_{0}, out_height_{0};
};
#include "shipped.inc"  // written by tools/checkcanvas.py from portall.cpp

// The board's allocator is jpeg_alloc_decoder_mem, which this machine does
// not have; what matters is the size, and that it can fail.
static bool g_pair_ok = true;
bool Portall::allocate_canvas_pair_() {
  if (!g_pair_ok)
    return false;
  this->canvas_pair_len_ = (size_t) this->padded_width_ * this->padded_height_ * 2;
  for (auto &b : this->canvas_pair_)
    b = (uint8_t *) aligned_alloc(64, (this->canvas_pair_len_ + 63) & ~(size_t) 63);
  return true;
}
// What the board's loop does between two of the decoder's sleeps: the
// component's tick, then LVGL's refresh.
static void tick_loop() {
  if (g_ticker != nullptr)
    g_ticker->canvas_tick_();
  lv_refr_now(g_disp);
}
// The JPEG engine's part: a whole picture of one colour, laid out in the
// decoder's padded rows, into whichever buffer it is given.
static void decode_whole(uint8_t *into, const Portall &p, uint16_t colour) {
  uint16_t *px = (uint16_t *) into;
  for (size_t i = 0; i < (size_t) p.padded_width_ * p.padded_height_; i++)
    px[i] = colour;
}

static const int SW = 320, SH = 240;
static std::vector<uint16_t> g_screen(SW * SH);
static std::vector<lv_area_t> g_flushed;
static void flush(lv_display_t *d, const lv_area_t *a, uint8_t *px) {
  g_flushed.push_back(*a);
  int w = lv_area_get_width(a);
  for (int y = a->y1; y <= a->y2; y++)
    memcpy(&g_screen[y * SW + a->x1], px + (size_t) (y - a->y1) * w * 2, w * 2);
  lv_display_flush_ready(d);
}
static uint32_t g_tick = 0;
static uint32_t tick() { return g_tick; }
static int fails = 0;
static void ok(bool c, const char *what) { printf("  %s  %s\n", c ? "ok  " : "FAIL", what); if (!c) fails++; }

static lv_obj_t *make_canvas(int w, int h, lv_color_format_t cf, int x, int y) {
  lv_obj_t *c = lv_canvas_create(lv_screen_active());
  lv_draw_buf_t *buf = lv_draw_buf_create(w, h, cf, 0);
  lv_draw_buf_set_flag(buf, LV_IMAGE_FLAGS_MODIFIABLE);
  lv_canvas_set_draw_buf(c, buf);
  lv_canvas_fill_bg(c, lv_color_black(), LV_OPA_COVER);
  lv_obj_set_pos(c, x, y);
  return c;
}

int main() {
  lv_init();
  lv_tick_set_cb(tick);
  lv_display_t *d = lv_display_create(SW, SH);
  g_disp = d;
  static uint16_t draw[SW * SH];
  lv_display_set_buffers(d, draw, nullptr, sizeof(draw), LV_DISPLAY_RENDER_MODE_PARTIAL);
  lv_display_set_flush_cb(d, flush);
  lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(0x00ff00), 0);

  // A canvas of 200x150 at (50,60), as the example puts one under a bar.
  lv_obj_t *canvas = make_canvas(200, 150, LV_COLOR_FORMAT_RGB565, 50, 60);
  lv_refr_now(d);

  Portall p;
  p.canvas_ = canvas;
  p.out_width_ = 200;
  p.out_height_ = 150;
  p.padded_width_ = 208;
  p.padded_height_ = 160;
  // Something already on the canvas, which the pair must carry across.
  lv_canvas_set_px(canvas, 5, 5, lv_color_hex(0x0000ff), LV_OPA_COVER);
  lv_refr_now(d);
  p.canvas_tick_();
  ok(p.canvas_buf_.load() != nullptr && g_infos == 2, "a canvas of the right size and format is taken");
  ok(p.canvas_zero_copy_() && p.canvas_shown_.data == p.canvas_pair_[0] &&
         lv_canvas_get_draw_buf(canvas) == &p.canvas_shown_,
     "and LVGL is given the first of two buffers of the decoder's own");
  ok(p.canvas_shown_.header.stride == 208 * 2 && p.canvas_pair_len_ >= (size_t) 208 * 160 * 2,
     "laid out in the decoder's padded rows, 150 rows rounded up to 160");
  ok(((uint16_t *) p.canvas_pair_[0])[5 * 208 + 5] == 0x001F, "carrying what the canvas already showed");
  g_flushed.clear();
  lv_obj_invalidate(canvas);
  lv_refr_now(d);
  ok(g_screen[65 * SW + 55] == 0x001F && g_screen[66 * SW + 56] == 0x0000,
     "which is still on the screen once LVGL reads the new buffer");

  // A red 16x8 rectangle at (10,20) of the canvas, from a source whose rows
  // carry 4 pixels of padding -- the decoder's x_pad.
  const int w = 16, h = 8, pad = 4;
  std::vector<uint16_t> src((w + pad) * h, 0xF800);
  g_flushed.clear();
  p.copy_to_canvas_((const uint8_t *) src.data(), 10, 20, w, h, w + pad);
  ok(p.canvas_dirty_, "the copy marks an area");
  lv_refr_now(d);
  ok(g_flushed.empty(), "nothing is redrawn until the loop tells LVGL");
  p.canvas_tick_();
  ok(!p.canvas_dirty_, "the tick takes the area");
  lv_refr_now(d);
  bool covered = false;
  for (auto &a : g_flushed)
    if (a.x1 <= 60 && a.y1 <= 80 && a.x2 >= 75 && a.y2 >= 87) covered = true;
  ok(covered, "LVGL redraws the rectangle at the canvas's place on the screen");
  ok(g_screen[80 * SW + 60] == 0xF800 && g_screen[87 * SW + 75] == 0xF800, "the rectangle's corners are red on the screen");
  ok(g_screen[80 * SW + 76] == 0x0000 && g_screen[79 * SW + 60] == 0x0000, "and the canvas beside it is untouched");

  // Two rectangles before one tick are joined into one redraw.
  p.copy_to_canvas_((const uint8_t *) src.data(), 0, 0, w, h, w + pad);
  p.copy_to_canvas_((const uint8_t *) src.data(), 180, 140, w - 12, h - 6, w + pad);
  ok(p.canvas_dirty_area_.x1 == 0 && p.canvas_dirty_area_.y1 == 0 && p.canvas_dirty_area_.x2 == 183 &&
         p.canvas_dirty_area_.y2 == 141, "two copies before a tick join into one area");
  p.canvas_tick_();
  lv_refr_now(d);
  ok(g_screen[60 * SW + 50] == 0xF800 && g_screen[201 * SW + 233] == 0xF800, "both reach the screen");

  // Flow control: a picture handed to LVGL is in flight until LVGL has drawn it.
  p.copy_to_canvas_((const uint8_t *) src.data(), 10, 20, w, h, w + pad);
  p.canvas_tick_();
  ok(p.canvas_inflight_.load(), "an area handed to LVGL is in flight");
  lv_refr_now(d);
  ok(!p.canvas_inflight_.load(), "and LVGL's own REFR_READY clears it once drawn");

  // The decoder waits at a NEW picture until then -- here LVGL draws after the
  // decoder's third sleep.
  p.copy_to_canvas_((const uint8_t *) src.data(), 10, 20, w, h, w + pad);
  p.canvas_tick_();
  g_delays = 0;
  g_refresh_after = 3;
  uint32_t t0 = g_ms;
  p.canvas_wait_(7);
  ok(g_delays == 3 && g_ms - t0 == 3 && !p.canvas_inflight_.load(),
     "a new picture waits until LVGL has drawn the last one, and no longer");
  ok(p.canvas_wait_ms_ == 3, "and the wait is counted");

  // The rest of that picture follows without waiting, even with an area out.
  p.copy_to_canvas_((const uint8_t *) src.data(), 30, 20, w, h, w + pad);
  p.canvas_tick_();
  g_delays = 0;
  g_refresh_after = -1;
  p.canvas_wait_(7);
  ok(g_delays == 0, "a later rectangle of the same picture does not wait");

  // An LVGL that never draws -- paused, or the canvas on a page not shown --
  // costs a second a picture, not the decoder.
  t0 = g_ms;
  p.canvas_wait_(8);
  ok(g_ms - t0 == CANVAS_WAIT_MS && p.canvas_inflight_.load(),
     "an LVGL that never draws holds a picture for the bound and no longer");

  // Nothing in flight, nothing to wait for.
  lv_refr_now(d);
  g_delays = 0;
  p.canvas_wait_(9);
  ok(g_delays == 0, "with nothing in flight a new picture goes straight in");

  // A rectangle past the edge writes nothing.
  lv_draw_buf_t *buf = p.canvas_buf_.load();
  std::vector<uint8_t> before(buf->data, buf->data + buf->data_size);
  p.copy_to_canvas_((const uint8_t *) src.data(), 190, 10, w, h, w + pad);
  ok(memcmp(before.data(), buf->data, buf->data_size) == 0, "a rectangle past the canvas's edge writes nothing");

  // ---- Zero copy -------------------------------------------------------
  // A whole picture decoded straight into the back buffer, then handed over.
  lv_refr_now(d);
  uint8_t *front0 = p.canvas_shown_.data;
  uint8_t *back0 = p.canvas_back_();
  ok(back0 != front0 && back0 != nullptr, "the back buffer is the one LVGL is not showing");
  decode_whole(back0, p, 0x07E0 ^ 0xFFFF);  // magenta-ish, 0xF81F
  int m = g_msyncs;
  p.canvas_publish_();
  ok(p.canvas_swap_.load() >= 0 && p.canvas_shown_.data == front0,
     "publishing asks for the swap and leaves LVGL's buffer alone until the loop");
  g_flushed.clear();
  lv_refr_now(d);
  ok(g_screen[60 * SW + 50] != 0xF81F, "nothing reaches the screen until the loop runs");
  p.canvas_tick_();
  ok(p.canvas_shown_.data == back0 && p.canvas_swap_.load() < 0 && p.canvas_swaps_.load() == 1,
     "the loop makes the back the front, and counts it");
  ok(p.canvas_inflight_.load(), "and the whole picture is in flight");
  lv_refr_now(d);
  ok(g_screen[60 * SW + 50] == 0xF81F && g_screen[209 * SW + 249] == 0xF81F && g_screen[59 * SW + 50] == 0x07E0,
     "LVGL draws the new buffer over the whole canvas, and only there");
  ok(!p.canvas_inflight_.load(), "and REFR_READY clears it");
  ok(g_msyncs == m, "no copy and no write-back for a whole picture");
  ok(p.canvas_back_() == front0, "the old front is the next back");

  // A partial rectangle after the swap lands in the NEW front.
  // (At 100,100: the old front has red at 10,20 from the cases above.)
  p.copy_to_canvas_((const uint8_t *) src.data(), 100, 100, w, h, w + pad);
  ok(((uint16_t *) back0)[100 * 208 + 100] == 0xF800 && ((uint16_t *) front0)[100 * 208 + 100] != 0xF800,
     "a rectangle after the swap is copied into the buffer now shown");
  ok(g_msyncs == m + 1, "and written back out of the cache");
  p.canvas_tick_();
  lv_refr_now(d);
  ok(g_screen[160 * SW + 150] == 0xF800 && g_screen[160 * SW + 166] == 0xF81F,
     "and reaches the screen over the picture");

  // The decoder waits for a swap the loop has not taken -- here the loop runs
  // at its second sleep.
  decode_whole(p.canvas_back_(), p, 0x001F);
  p.canvas_publish_();
  g_ticker = &p;
  g_delays = 0;
  g_refresh_after = 2;
  t0 = g_ms;
  ok(p.canvas_settle_() && g_delays == 2 && p.canvas_swap_.load() < 0,
     "the next picture waits until the loop has taken the last swap, and no longer");
  ok(g_screen[60 * SW + 50] == 0x001F, "which reached the screen");
  g_delays = 0;
  ok(p.canvas_settle_() && g_delays == 0, "with no swap waiting, nothing waits");

  // A loop that never runs costs the picture, never a buffer LVGL reads.
  g_ticker = nullptr;
  g_refresh_after = -1;
  decode_whole(p.canvas_back_(), p, 0xFFFF);
  p.canvas_publish_();
  t0 = g_ms;
  ok(!p.canvas_settle_() && g_ms - t0 == CANVAS_WAIT_MS && p.canvas_swap_.load() >= 0,
     "a loop that never runs refuses the picture after the bound");
  p.canvas_tick_();
  lv_refr_now(d);
  ok(g_screen[60 * SW + 50] == 0xFFFF, "and the swap still happens when the loop comes back");

  // No pair to be had: the mode copies into LVGL's own buffer, as before.
  g_pair_ok = false;
  Portall f;
  f.canvas_ = make_canvas(200, 150, LV_COLOR_FORMAT_RGB565, 0, 0);
  f.out_width_ = 200;
  f.out_height_ = 150;
  f.padded_width_ = 208;
  f.padded_height_ = 160;
  lv_draw_buf_t *own = lv_canvas_get_draw_buf(f.canvas_);
  f.canvas_tick_();
  ok(f.canvas_buf_.load() == own && !f.canvas_zero_copy_(), "without two buffers, LVGL's own is copied into");
  f.copy_to_canvas_((const uint8_t *) src.data(), 10, 20, w, h, w + pad);
  ok(((uint16_t *) own->data)[20 * (own->header.stride / 2) + 10] == 0xF800, "and a rectangle lands in it");
  lv_obj_delete(f.canvas_);
  g_pair_ok = true;

  // Wrong size, wrong format: refused once, said once, never written.
  Portall q;
  q.canvas_ = canvas;
  q.out_width_ = 320;
  q.out_height_ = 150;
  int e = g_errors;
  q.canvas_tick_();
  q.canvas_tick_();
  ok(q.canvas_buf_.load() == nullptr && g_errors == e + 1, "a canvas of another size is refused, and said once");
  q.copy_to_canvas_((const uint8_t *) src.data(), 0, 0, w, h, w + pad);
  ok(!q.canvas_dirty_, "and nothing is copied into it");

  Portall r;
  r.canvas_ = make_canvas(200, 150, LV_COLOR_FORMAT_ARGB8888, 0, 0);
  r.out_width_ = 200;
  r.out_height_ = 150;
  e = g_errors;
  r.canvas_tick_();
  ok(r.canvas_buf_.load() == nullptr && g_errors == e + 1, "a transparent (ARGB8888) canvas is refused");

  printf(fails ? "%d FAILED\n" : "all passed\n", fails);
  return fails != 0;
}
