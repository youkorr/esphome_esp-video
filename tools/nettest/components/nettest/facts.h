// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// The line every answer of the bench carries: which build this is, what it
// was compiled with, its heap, its signal and the C6's own firmware version.
// Kept apart from nettest.cpp, and free of ESPHome, so the very same code
// that answers on the board answers on a workstation (tools/checknettest.py).
#pragma once

#include <cstddef>

namespace bench {

void set_label(const char *label);
void set_rssi(int rssi);
// A round trip to the C6; call it from a task that may wait. Does nothing
// in a build with no ESP32-C6 behind the P4.
void ask_c6_version();
// Asks the radio whether it sleeps between beacons. A round trip to the C6
// on a P4: call it from a task that may wait, never while a test runs.
void ask_power_save();
// Writes the line into `out`, at most `room` bytes counting its end, and
// returns how many characters it wrote.
int facts(char *out, size_t room);

}  // namespace bench
