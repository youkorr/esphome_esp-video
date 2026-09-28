// Links the SHIPPED canvas_tick_() / copy_to_canvas_() out of
// components/portall/portall.cpp against a real LVGL, and draws with it on a
// display that has no panel: what reaches the screen is read back from the
// flush callback. Run by tools/checkcanvas.py, which builds LVGL first.
#include "lvgl.h"
#include <atomic>
#include <cstdio>
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
struct Portall {
  void canvas_tick_();
  void copy_to_canvas_(const uint8_t *pixels, uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t src_stride_px);
  lv_obj_t *canvas_{nullptr};
  std::atomic<lv_draw_buf_t *> canvas_buf_{nullptr};
  bool canvas_refused_{false};
  portMUX_TYPE canvas_lock_ = portMUX_INITIALIZER_UNLOCKED;
  bool canvas_dirty_{false};
  lv_area_t canvas_dirty_area_{};
  uint16_t out_width_{0}, out_height_{0};
};
#include "shipped.inc"  // written by tools/checkcanvas.py from portall.cpp

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
  p.canvas_tick_();
  ok(p.canvas_buf_.load() != nullptr && g_infos == 1, "a canvas of the right size and format is taken");

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

  // A rectangle past the edge writes nothing.
  lv_draw_buf_t *buf = p.canvas_buf_.load();
  std::vector<uint8_t> before(buf->data, buf->data + buf->data_size);
  p.copy_to_canvas_((const uint8_t *) src.data(), 190, 10, w, h, w + pad);
  ok(memcmp(before.data(), buf->data, buf->data_size) == 0, "a rectangle past the canvas's edge writes nothing");

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
