// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// The three servers a network measurement talks to, in plain BSD sockets so
// the very same file runs on a workstation (tools/checknettest.py) and on an
// ESP32 through lwIP. Nothing here knows about ESPHome or FreeRTOS.
#pragma once

#include <cstddef>
#include <cstdint>

namespace bench {

// Writes what the device wants every reply to carry (its compiled Wi-Fi
// buffer settings, heap, signal) into `out`, at most `room` bytes, and
// returns how many it wrote.
using Facts = int (*)(char *out, size_t room);

// TCP, `port`: reads everything a client sends until it shuts down its side,
// then answers one line -- "bytes=N us=T gap=G " and the facts -- and
// closes. T is first byte to last byte, measured here, which is what the
// device received; G is the longest wait between two reads, in ms.
void serve_sink(uint16_t port, Facts facts);

// TCP, `port`: reads a number of seconds on one line, sends a pattern for
// that long, then closes. The client measures what arrived.
void serve_source(uint16_t port);

// UDP, `port`: counts datagrams. "?" answers "count=N " and the facts, and
// starts the count again; "p" answers "p" at once (a ping); "i" answers the
// facts alone.
void serve_udp(uint16_t port, Facts facts);

// Microseconds from an arbitrary start, steady.
int64_t now_us();

}  // namespace bench
