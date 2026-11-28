// SPDX-License-Identifier: MIT
// Bounded single-client TCP benchmark, shared with the native host test.
#pragma once
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <cstdio>
#include <cstring>
#include <cstdint>

namespace esphome::portall_speedtest {
constexpr size_t CHUNK = 16384;
constexpr uint64_t LIMIT = 64ULL * 1024 * 1024;
constexpr int64_t SECOND = 1000000;
inline int64_t micros_now() {
  timespec t{};
  clock_gettime(CLOCK_MONOTONIC, &t);
  return int64_t(t.tv_sec) * SECOND + t.tv_nsec / 1000;
}
inline bool send_all(int fd, const char *data, size_t len, int64_t until) {
  while (len && micros_now() < until) {
    auto n = send(fd, data, len, 0);
    if (n <= 0) return false;
    data += n;
    len -= n;
  }
  return len == 0;
}
inline void serve_client(int fd, const char *key, char *buffer) {
  timeval timeout{2, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  // Fixed length header: PST1 + space + 64 hex digits + space + operation + LF.
  char header[72];
  size_t have = 0;
  const auto auth_end = micros_now() + 3 * SECOND;
  while (have < sizeof(header) && micros_now() < auth_end) {
    auto n = recv(fd, header + have, sizeof(header) - have, 0);
    if (n <= 0) return;
    have += n;
  }
  if (have != sizeof(header) || memcmp(header, "PST1 ", 5) || header[69] != ' ' || header[71] != '\n') return;
  unsigned diff = 0;
  for (size_t i = 0; i < 64; i++) diff |= uint8_t(header[5+i]) ^ uint8_t(key[i]);
  const char op = header[70];
  if (diff || (op != 'P' && op != 'R' && op != 'T')) return;
  if (!send_all(fd, "OK\n", 3, micros_now() + SECOND)) return;
  if (op == 'P') return;
  uint64_t bytes = 0;
  const auto start = micros_now();
  int64_t first = 0, last = 0;
  if (op == 'R') {
    const auto end = start + 8 * SECOND;
    bool eof = false;
    while (bytes < LIMIT && micros_now() < end) {
      auto n = recv(fd, buffer, CHUNK, 0);
      if (n == 0) { eof = true; break; }
      if (n < 0 || uint64_t(n) > LIMIT - bytes) return;
      last = micros_now();
      if (!bytes) first = last;
      bytes += n;
    }
    // Require an orderly EOF, including when exactly the byte cap was sent.
    if (!eof && bytes == LIMIT && micros_now() < end) eof = recv(fd, buffer, 1, 0) == 0;
    if (!eof || last <= first) return;
    char result[80];
    int len = snprintf(result, sizeof(result), "RX %llu %lld\n",
                       (unsigned long long)bytes, (long long)(last - first));
    if (len > 0 && size_t(len) < sizeof(result))
      send_all(fd, result, len, micros_now() + SECOND);
  } else {
    // No data from the application, no compression or JPEG work.
    memset(buffer, 0xA5, CHUNK);
    const auto end = start + 5 * SECOND;
    while (bytes < LIMIT && micros_now() < end) {
      const size_t count = LIMIT - bytes < CHUNK ? size_t(LIMIT - bytes) : CHUNK;
      auto n = send(fd, buffer, count, 0);
      if (n <= 0) return;
      bytes += n;
    }
    // A completion marker distinguishes a successful test from a reset or
    // a socket timeout after a partial stream. No payload byte equals 'E'.
    char footer[24];
    const int len = snprintf(footer, sizeof(footer), "END %016llx\n", (unsigned long long)bytes);
    if (len == 21) send_all(fd, footer, len, micros_now() + SECOND);
  }
}
}  // namespace esphome::portall_speedtest
