// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// The bench's servers and its facts line, the shipped files, on a workstation:
// tools/checknettest.py starts this and points nettest.py at it.
#include "facts.h"
#include "servers.h"

#include <cstdio>
#include <cstdlib>
#include <thread>

#include "esp_heap_caps.h"
#include "esp_hosted.h"

extern "C" size_t heap_caps_get_free_size(unsigned) { return 123456; }
extern "C" size_t heap_caps_get_minimum_free_size(unsigned) { return 98765; }
extern "C" int esp_hosted_get_coprocessor_fwversion(esp_hosted_coprocessor_fwver_t *v) {
  v->major1 = 2;
  v->minor1 = 12;
  v->patch1 = 13;
  return 0;
}

int main(int argc, char **argv) {
  if (argc < 3)
    return 2;
  const auto port = static_cast<uint16_t>(atoi(argv[1]));
  bench::set_label(argv[2]);
  bench::set_rssi(-48);
  bench::ask_c6_version();
  std::thread sink([port] { bench::serve_sink(port, bench::facts); });
  std::thread source([port] { bench::serve_source(port + 1); });
  std::thread udp([port] { bench::serve_udp(port + 2, bench::facts); });
  printf("ready\n");
  fflush(stdout);
  sink.join();
  return 0;
}
