// Checks canvas_turn_() -- network.cpp's glass-to-LVGL conversion -- against
// the rotation ESPHome's LVGL really performs. The loops below are copied from
// LvglComponent::draw_buffer_ in esphome/components/lvgl/lvgl_esphome.cpp
// (2026.8.2), for one whole-screen area: a logical picture is turned the way
// the panel receives it, and every pixel's position on the glass is fed back
// through canvas_turn_(), which must name the logical pixel it came from.
#include <cstdint>
#include <cstdio>
#include <vector>
#include "turn.inc"  // written by tools/checkcanvas.py from network.cpp

static int fails = 0;

static void check(int degrees, int W, int H) {
  // Logical picture: each pixel holds its own index.
  std::vector<uint32_t> src(W * H), phys(W * H, 0xFFFFFFFF);
  for (int i = 0; i < W * H; i++) src[i] = i;
  const int width = W, height = H, height_rounded = H;
  // The panel's own size, which is what width_/height_ are in draw_buffer_.
  const int pw = (degrees == 90 || degrees == 270) ? H : W;
  const int ph = (degrees == 90 || degrees == 270) ? W : H;
  std::vector<uint32_t> dst(W * H);
  const uint32_t *ptr = src.data();
  int x1 = 0, y1 = 0, dw = W, dh = H;
  switch (degrees) {
    case 90:
      for (int x = height; x-- != 0;)
        for (int y = 0; y != width; y++) dst[y * height_rounded + x] = *ptr++;
      y1 = 0; x1 = pw - 0 - height; dh = width; dw = height_rounded;
      break;
    case 180:
      for (int y = height; y-- != 0;)
        for (int x = width; x-- != 0;) dst[y * width + x] = *ptr++;
      x1 = pw - 0 - width; y1 = ph - 0 - height;
      break;
    case 270:
      for (int x = 0; x != height; x++)
        for (int y = width; y-- != 0;) dst[y * height_rounded + x] = *ptr++;
      x1 = 0; y1 = ph - 0 - width; dh = width; dw = height_rounded;
      break;
    default:
      dst = src;
  }
  for (int r = 0; r < dh; r++)
    for (int c = 0; c < dw; c++) phys[(y1 + r) * pw + (x1 + c)] = dst[r * dw + c];

  int bad = 0;
  for (int py = 0; py < ph; py++)
    for (int px = 0; px < pw; px++) {
      int32_t x = px, y = py;
      canvas_turn_(degrees, W, H, x, y);
      const uint32_t want = phys[py * pw + px];
      if (x < 0 || y < 0 || x >= W || y >= H || (uint32_t) (y * W + x) != want) bad++;
    }
  printf("  %s  %3d degrees, %dx%d logical: every touch lands on the pixel drawn there%s\n", bad ? "FAIL" : "ok  ",
         degrees, W, H, bad ? "" : "");
  if (bad) {
    printf("        %d of %d points wrong\n", bad, W * H);
    fails++;
  }
}

int main() {
  check(0, 64, 40);
  check(90, 40, 64);
  check(180, 64, 40);
  check(270, 1280, 720);   // a Tab5 at 270, as lvgl: rotation: 270 gives it
  check(270, 64, 40);
  check(90, 1280, 720);
  printf(fails ? "%d FAILED\n" : "all passed\n", fails);
  return fails != 0;
}
