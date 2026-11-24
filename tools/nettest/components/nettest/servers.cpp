// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
#include "servers.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

#ifdef ESP_PLATFORM
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#else
#include <time.h>
#endif

namespace bench {

// One read or write at a time, the size of a write a sender makes in
// practice. Big enough that the per-call cost is nothing beside the link.
static const size_t CHUNK = 16384;

int64_t now_us() {
#ifdef ESP_PLATFORM
  return esp_timer_get_time();
#else
  timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return static_cast<int64_t>(t.tv_sec) * 1000000 + t.tv_nsec / 1000;
#endif
}

static void pause_ms(int ms) {
#ifdef ESP_PLATFORM
  vTaskDelay(pdMS_TO_TICKS(ms));
#else
  usleep(ms * 1000);
#endif
}

// A socket that will not wait for ever on a client that went away.
static void time_out(int fd, int seconds) {
  timeval t{};
  t.tv_sec = seconds;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &t, sizeof t);
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &t, sizeof t);
}

static int bound(int type, uint16_t port) {
  int fd = socket(AF_INET, type, 0);
  if (fd < 0)
    return -1;
  int yes = 1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
  sockaddr_in at{};
  at.sin_family = AF_INET;
  at.sin_port = htons(port);
  at.sin_addr.s_addr = htonl(INADDR_ANY);
  if (bind(fd, reinterpret_cast<sockaddr *>(&at), sizeof at) != 0 ||
      (type == SOCK_STREAM && listen(fd, 1) != 0)) {
    close(fd);
    return -1;
  }
  return fd;
}

// A listener that could not be made is tried again: the network may not be
// up yet when the task starts.
static int listening(int type, uint16_t port) {
  for (;;) {
    int fd = bound(type, port);
    if (fd >= 0)
      return fd;
    pause_ms(1000);
  }
}

static bool send_all(int fd, const char *data, size_t len) {
  while (len > 0) {
    ssize_t sent = send(fd, data, len, 0);
    if (sent <= 0)
      return false;
    data += sent;
    len -= static_cast<size_t>(sent);
  }
  return true;
}

void serve_sink(uint16_t port, Facts facts) {
  int listener = listening(SOCK_STREAM, port);
  char *buffer = static_cast<char *>(malloc(CHUNK));
  if (buffer == nullptr)
    return;
  for (;;) {
    int client = accept(listener, nullptr, nullptr);
    if (client < 0) {
      pause_ms(100);
      continue;
    }
    time_out(client, 10);
    long long total = 0;
    int64_t first = 0, last = 0, gap = 0;
    for (;;) {
      ssize_t got = recv(client, buffer, CHUNK, 0);
      if (got <= 0)
        break;
      int64_t at = now_us();
      if (total == 0)
        first = at;
      else if (at - last > gap)
        gap = at - last;
      last = at;
      total += got;
    }
    // gap: the longest the board waited between two reads that brought
    // something. A stream that arrives evenly keeps it to a few ms; a stall
    // of the radio or of the SDIO link shows up here whatever the average.
    char reply[384];
    int len = snprintf(reply, sizeof reply, "bytes=%lld us=%lld gap=%lld ", total,
                       static_cast<long long>(last - first), static_cast<long long>(gap / 1000));
    if (len < 0 || len >= static_cast<int>(sizeof reply) - 2)
      len = 0;
    len += facts(reply + len, sizeof reply - 2 - len);
    reply[len++] = '\n';
    send_all(client, reply, static_cast<size_t>(len));
    close(client);
  }
}

void serve_source(uint16_t port) {
  int listener = listening(SOCK_STREAM, port);
  char *buffer = static_cast<char *>(malloc(CHUNK));
  if (buffer == nullptr)
    return;
  for (size_t i = 0; i < CHUNK; i++)
    buffer[i] = static_cast<char>('a' + i % 26);
  for (;;) {
    int client = accept(listener, nullptr, nullptr);
    if (client < 0) {
      pause_ms(100);
      continue;
    }
    time_out(client, 10);
    char line[16] = {0};
    size_t have = 0;
    while (have < sizeof line - 1) {
      ssize_t got = recv(client, line + have, 1, 0);
      if (got <= 0 || line[have] == '\n')
        break;
      have++;
    }
    int seconds = atoi(line);
    if (seconds < 1)
      seconds = 1;
    if (seconds > 120)
      seconds = 120;
    const int64_t until = now_us() + static_cast<int64_t>(seconds) * 1000000;
    while (now_us() < until && send_all(client, buffer, CHUNK)) {
    }
    shutdown(client, SHUT_WR);
    close(client);
  }
}

void serve_udp(uint16_t port, Facts facts) {
  int fd = listening(SOCK_DGRAM, port);
  char buffer[2048];
  unsigned count = 0;
  for (;;) {
    sockaddr_in from{};
    socklen_t size = sizeof from;
    ssize_t got = recvfrom(fd, buffer, sizeof buffer, 0, reinterpret_cast<sockaddr *>(&from), &size);
    if (got <= 0)
      continue;
    if (got == 1 && (buffer[0] == '?' || buffer[0] == 'p' || buffer[0] == 'i')) {
      char reply[384];
      int len = 0;
      if (buffer[0] == 'p') {
        reply[0] = 'p';
        len = 1;
      } else {
        if (buffer[0] == '?') {
          len = snprintf(reply, sizeof reply, "count=%u ", count);
          count = 0;
        }
        if (len < 0)
          len = 0;
        len += facts(reply + len, sizeof reply - len);
      }
      sendto(fd, reply, static_cast<size_t>(len), 0, reinterpret_cast<sockaddr *>(&from), size);
      continue;
    }
    count++;
  }
}

}  // namespace bench
