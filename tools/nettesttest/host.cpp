// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// The bench's servers and its facts line, the shipped files, on a workstation:
// tools/checknettest.py starts this and points nettest.py at it.
#include "facts.h"
#include "servers.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

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

// A board whose receive path has stopped: connections are accepted, and
// nothing is ever read from them.
static void stalled_sink(uint16_t port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  int yes = 1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
  sockaddr_in at{};
  at.sin_family = AF_INET;
  at.sin_port = htons(port);
  at.sin_addr.s_addr = htonl(INADDR_ANY);
  if (bind(fd, reinterpret_cast<sockaddr *>(&at), sizeof at) != 0 || listen(fd, 4) != 0)
    return;
  std::vector<int> held;
  for (;;) {
    int client = accept(fd, nullptr, nullptr);
    if (client >= 0)
      held.push_back(client);
  }
}

int main(int argc, char **argv) {
  if (argc < 3)
    return 2;
  const auto port = static_cast<uint16_t>(atoi(argv[1]));
  bench::set_label(argv[2]);
  bench::set_rssi(-48);
  bench::ask_c6_version();
  const bool stall = argc > 3 && strcmp(argv[3], "stall") == 0;
  std::thread sink([port, stall] {
    if (stall)
      stalled_sink(port);
    else
      bench::serve_sink(port, bench::facts);
  });
  std::thread source([port] { bench::serve_source(port + 1); });
  std::thread udp([port] { bench::serve_udp(port + 2, bench::facts, bench::ask_power_save); });
  printf("ready\n");
  fflush(stdout);
  sink.join();
  return 0;
}
