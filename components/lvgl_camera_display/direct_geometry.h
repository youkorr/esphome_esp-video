// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
#pragma once

// Where a camera frame lands on the glass in direct mode, with nothing from
// ESP-IDF or ESPHome in it, so tools/checkcamdirect.py can run it on a PC
// against ESPHome's own rotation loops.
//
// Two coordinate systems. The UI is what LVGL sees: a 1280x720 screen on a
// Tab5 held landscape. The panel is the glass as it is wired: 720x1280. The
// lvgl: block's `rotation:` turns one into the other CLOCKWISE (ESPHome's
// meaning), and the place a UI rectangle lands on the panel is the formula
// ESPHome's lvgl draw_buffer_() uses for every flush -- the same one
// portall's turn_rect_() uses for its video mode.

#include <cstdint>

namespace esphome {
namespace lvgl_camera_display {

struct DirectPlan {
  bool visible{false};
  // The part of the frame that is shown, in the frame's own pixels.
  int32_t src_x{0}, src_y{0}, w{0}, h{0};
  // Where that part lands on the panel, after the turn.
  int32_t px{0}, py{0}, pw{0}, ph{0};
};

// `rotation` is the lvgl: block's, in degrees clockwise. `ui_w` x `ui_h` is
// the UI's size. The canvas covers x1..x2, y1..y2 inclusive (lv_area_t), and
// the frame is centred in it the way an lv_image draws its source
// (LV_IMAGE_ALIGN_CENTER, the default): a frame wider than the canvas is
// cropped equally on both sides, a narrower one leaves the canvas's own
// background around it. Whatever lies off the screen is cut too.
inline DirectPlan plan_direct(int rotation, int32_t ui_w, int32_t ui_h, int32_t x1, int32_t y1, int32_t x2,
                              int32_t y2, int32_t frame_w, int32_t frame_h) {
  DirectPlan p;
  const int32_t cw = x2 - x1 + 1;
  const int32_t ch = y2 - y1 + 1;
  if (cw <= 0 || ch <= 0 || frame_w <= 0 || frame_h <= 0 || ui_w <= 0 || ui_h <= 0)
    return p;
  // The frame's top-left in UI coordinates. LVGL centres with integer halves
  // the same way: (canvas - image) / 2, rounded towards zero.
  const int32_t fx = x1 + (cw - frame_w) / 2;
  const int32_t fy = y1 + (ch - frame_h) / 2;
  // The visible part: frame AND canvas AND screen.
  int32_t vx1 = fx, vy1 = fy, vx2 = fx + frame_w - 1, vy2 = fy + frame_h - 1;
  if (vx1 < x1) vx1 = x1;
  if (vy1 < y1) vy1 = y1;
  if (vx2 > x2) vx2 = x2;
  if (vy2 > y2) vy2 = y2;
  if (vx1 < 0) vx1 = 0;
  if (vy1 < 0) vy1 = 0;
  if (vx2 > ui_w - 1) vx2 = ui_w - 1;
  if (vy2 > ui_h - 1) vy2 = ui_h - 1;
  if (vx2 < vx1 || vy2 < vy1)
    return p;
  p.visible = true;
  p.w = vx2 - vx1 + 1;
  p.h = vy2 - vy1 + 1;
  p.src_x = vx1 - fx;
  p.src_y = vy1 - fy;
  // UI rectangle -> panel rectangle, as ESPHome's lvgl draw_buffer_() places
  // a flushed area: 90 puts UI x along the panel's y and UI y, reversed,
  // along its x; 270 the other way round.
  switch (rotation) {
    case 90:
      p.px = ui_h - vy1 - p.h;
      p.py = vx1;
      p.pw = p.h;
      p.ph = p.w;
      break;
    case 180:
      p.px = ui_w - vx1 - p.w;
      p.py = ui_h - vy1 - p.h;
      p.pw = p.w;
      p.ph = p.h;
      break;
    case 270:
      p.px = vy1;
      p.py = ui_w - vx1 - p.w;
      p.pw = p.h;
      p.ph = p.w;
      break;
    default:
      p.px = vx1;
      p.py = vy1;
      p.pw = p.w;
      p.ph = p.h;
      break;
  }
  return p;
}

// The accelerator's angle for an lvgl: rotation. The PPA turns
// COUNTER-clockwise on the glass and ESPHome's rotation is clockwise, so 90
// is the PPA's 270 and the other way round -- what ESPHome's lvgl and
// youkorr/lvgl_9.5 both hand it. Returned in degrees; the .cpp maps it onto
// ppa_srm_rotation_angle_t.
inline int ppa_degrees(int rotation) {
  switch (rotation) {
    case 90:
      return 270;
    case 270:
      return 90;
    case 180:
      return 180;
    default:
      return 0;
  }
}

}  // namespace lvgl_camera_display
}  // namespace esphome
