// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// Direct mode's geometry against the screen LVGL would have drawn.
//
// The reference is what the board shows today: the frame drawn into the UI
// the way lv_image centres it in the canvas, then the whole UI turned onto
// the panel by ESPHome's own lvgl draw_buffer_() loops, copied below from
// esphome 2026.10.0 (components/lvgl/lvgl_esphome.cpp). Direct mode crops
// the frame by plan_direct(), turns it with a model of the PPA at
// ppa_degrees(), and writes it at the panel rectangle plan_direct() names.
// The two panels must be the same, pixel for pixel.
//
// The PPA model is the one ppa-alternative's tests hold to youkorr/lvgl_9.5's
// loops: its angles turn counter-clockwise on the glass.

#include "direct_geometry.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

using namespace esphome::lvgl_camera_display;

static int failures = 0;
static int cases = 0;

// ESPHome's draw_buffer_() for one flushed area, draw_rounding 1. `ptr` is
// the area's pixels in the UI; returns where they land on a panel of
// native_w x native_h. width_/height_ in ESPHome are the PANEL's size.
static void esphome_flush(int rotation, int native_w, int native_h, int ax1, int ay1, int width, int height,
                          const std::vector<uint32_t> &area_px, std::vector<uint32_t> &panel) {
  std::vector<uint32_t> dst(area_px.size());
  const uint32_t *ptr = area_px.data();
  const int height_rounded = height;
  int x1 = ax1, y1 = ay1;
  const uint32_t *src = dst.data();
  switch (rotation) {
    case 90:
      for (int x = height; x-- != 0;)
        for (int y = 0; y != width; y++)
          dst[y * height_rounded + x] = *ptr++;
      break;
    case 180:
      for (int y = height; y-- != 0;)
        for (int x = width; x-- != 0;)
          dst[y * width + x] = *ptr++;
      break;
    case 270:
      for (int x = 0; x != height; x++)
        for (int y = width; y-- != 0;)
          dst[y * height_rounded + x] = *ptr++;
      break;
    default:
      src = area_px.data();
      break;
  }
  switch (rotation) {
    case 90:
      y1 = x1;
      x1 = native_w - ay1 - height;
      height = width;
      width = height_rounded;
      break;
    case 180:
      x1 = native_w - x1 - width;
      y1 = native_h - y1 - height;
      break;
    case 270:
      x1 = y1;
      y1 = native_h - ax1 - width;
      height = width;
      width = height_rounded;
      break;
    default:
      break;
  }
  for (int y = 0; y < height; y++)
    for (int x = 0; x < width; x++)
      panel[(size_t) (y1 + y) * native_w + x1 + x] = src[(size_t) y * width + x];
}

// The PPA's scale-rotate-mirror at scale 1 on a block of bw x bh: output
// pixel (mx, my) reads source (sx, sy). 90 turns counter-clockwise.
static void ppa_turn(int degrees, const std::vector<uint32_t> &in, int bw, int bh, std::vector<uint32_t> &out) {
  const bool swap = degrees == 90 || degrees == 270;
  const int ow = swap ? bh : bw, oh = swap ? bw : bh;
  out.assign((size_t) ow * oh, 0);
  for (int my = 0; my < oh; my++)
    for (int mx = 0; mx < ow; mx++) {
      int sx, sy;
      switch (degrees) {
        case 90: sx = bw - 1 - my; sy = mx; break;
        case 180: sx = bw - 1 - mx; sy = bh - 1 - my; break;
        case 270: sx = my; sy = bh - 1 - mx; break;
        default: sx = mx; sy = my; break;
      }
      out[(size_t) my * ow + mx] = in[(size_t) sy * bw + sx];
    }
}

static void check(const char *name, int rotation, int ui_w, int ui_h, int cx, int cy, int cw, int ch, int fw,
                  int fh) {
  cases++;
  const bool swap = rotation == 90 || rotation == 270;
  const int nw = swap ? ui_h : ui_w, nh = swap ? ui_w : ui_h;
  std::vector<uint32_t> frame((size_t) fw * fh);
  for (size_t i = 0; i < frame.size(); i++)
    frame[i] = (uint32_t) i + 1;

  // Reference: the UI as LVGL draws it, then ESPHome's flush in bands of 40
  // rows (a partial-mode LVGL), so the placement formula is used many times.
  std::vector<uint32_t> ui((size_t) ui_w * ui_h, 0);
  const int fx = cx + (cw - fw) / 2, fy = cy + (ch - fh) / 2;
  for (int y = 0; y < ui_h; y++)
    for (int x = 0; x < ui_w; x++) {
      if (x < cx || x >= cx + cw || y < cy || y >= cy + ch)
        continue;
      int sx = x - fx, sy = y - fy;
      if (sx >= 0 && sx < fw && sy >= 0 && sy < fh)
        ui[(size_t) y * ui_w + x] = frame[(size_t) sy * fw + sx];
    }
  std::vector<uint32_t> want((size_t) nw * nh, 0);
  for (int by = 0; by < ui_h; by += 40) {
    int bh = by + 40 > ui_h ? ui_h - by : 40;
    std::vector<uint32_t> band(ui.begin() + (size_t) by * ui_w, ui.begin() + (size_t) (by + bh) * ui_w);
    esphome_flush(rotation, nw, nh, 0, by, ui_w, bh, band, want);
  }

  // Direct mode.
  std::vector<uint32_t> got((size_t) nw * nh, 0);
  DirectPlan p = plan_direct(rotation, ui_w, ui_h, cx, cy, cx + cw - 1, cy + ch - 1, fw, fh);
  if (p.visible) {
    std::vector<uint32_t> block((size_t) p.w * p.h);
    for (int y = 0; y < p.h; y++)
      for (int x = 0; x < p.w; x++)
        block[(size_t) y * p.w + x] = frame[(size_t) (p.src_y + y) * fw + p.src_x + x];
    std::vector<uint32_t> out;
    ppa_turn(ppa_degrees(rotation), block, p.w, p.h, out);
    if (p.px < 0 || p.py < 0 || p.px + p.pw > nw || p.py + p.ph > nh) {
      printf("FAIL %s: panel rectangle %d,%d %dx%d is off a %dx%d panel\n", name, p.px, p.py, p.pw, p.ph, nw, nh);
      failures++;
      return;
    }
    for (int y = 0; y < p.ph; y++)
      for (int x = 0; x < p.pw; x++)
        got[(size_t) (p.py + y) * nw + p.px + x] = out[(size_t) y * p.pw + x];
  }
  size_t diffs = 0, shown = 0;
  for (size_t i = 0; i < want.size(); i++) {
    diffs += want[i] != got[i];
    shown += want[i] != 0;
  }
  if (diffs) {
    printf("FAIL %s: %zu of %zu panel pixels differ\n", name, diffs, want.size());
    failures++;
  } else {
    printf("ok   %s (%zu pixels of the frame on the glass)\n", name, shown);
  }
}

int main() {
  // The three boards, as their YAMLs have them.
  check("Tab5, lvgl 270, 1280x720 into a 1100x720 canvas", 270, 1280, 720, 0, 0, 1100, 720, 1280, 720);
  check("Guition, lvgl 90, 1280x720 into an 800x600 canvas at 80,0", 90, 1280, 800, 80, 0, 800, 600, 1280, 720);
  check("Waveshare, lvgl 180, 1280x960 into the whole screen", 180, 1024, 600, 0, 0, 1024, 600, 1280, 960);
  check("rotation 0, frame smaller than the canvas", 0, 1024, 600, 10, 20, 900, 500, 640, 480);
  // Edges: a canvas running off the screen, a frame narrower than its
  // canvas, odd sizes, a canvas wholly off the screen.
  check("270, canvas off the right edge", 270, 1280, 720, 900, 100, 600, 400, 640, 480);
  check("90, canvas off the top-left", 90, 1280, 800, -50, -30, 500, 400, 640, 480);
  check("180, odd sizes", 180, 1024, 600, 3, 7, 501, 333, 479, 271);
  check("90, frame narrower, odd", 90, 1280, 800, 17, 11, 777, 555, 321, 123);
  check("270, canvas off the screen", 270, 1280, 720, 1300, 0, 200, 200, 64, 64);
  srand(1);
  int rots[] = {0, 90, 180, 270};
  for (int i = 0; i < 40; i++) {
    char name[96];
    int r = rots[rand() % 4];
    int uw = 64 + rand() % 200, uh = 64 + rand() % 200;
    int cx = rand() % uw - 30, cy = rand() % uh - 30, cw = 1 + rand() % uw, ch = 1 + rand() % uh;
    int fw = 1 + rand() % 250, fh = 1 + rand() % 250;
    snprintf(name, sizeof name, "random %d: rot %d ui %dx%d canvas %d,%d %dx%d frame %dx%d", i, r, uw, uh, cx, cy,
             cw, ch, fw, fh);
    check(name, r, uw, uh, cx, cy, cw, ch, fw, fh);
  }
  printf("%d case(s), %d failure(s)\n", cases, failures);
  return failures ? 1 : 0;
}
