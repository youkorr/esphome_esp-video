// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
#include "nettest.h"
#include "facts.h"
#include "servers.h"

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#ifdef USE_WIFI
#include "esphome/components/wifi/wifi_component.h"
#endif

namespace esphome::nettest {

static const char *const TAG = "nettest";
static uint16_t g_port = 5201;

static void sink_task(void *) {
  ::bench::serve_sink(g_port, ::bench::facts);
  vTaskDelete(nullptr);
}

static void source_task(void *) {
  ::bench::serve_source(g_port + 1);
  vTaskDelete(nullptr);
}

static void udp_task(void *) {
  // A round trip to the C6, so here rather than on the loop. The answer
  // only labels the results.
  ::bench::ask_c6_version();
  ::bench::serve_udp(g_port + 2, ::bench::facts);
  vTaskDelete(nullptr);
}

void NetTest::set_label(const char *label) { ::bench::set_label(label); }

void NetTest::setup() {
  g_port = this->port_;
  // Same stacks, priority and core in every build, so the servers cost the
  // same whichever Wi-Fi settings are being compared. Their 16 KB buffers
  // come from the heap, not the stack.
  xTaskCreatePinnedToCore(sink_task, "nt_sink", 6144, nullptr, 5, nullptr, 1);
  xTaskCreatePinnedToCore(source_task, "nt_source", 6144, nullptr, 5, nullptr, 1);
  xTaskCreatePinnedToCore(udp_task, "nt_udp", 6144, nullptr, 5, nullptr, 1);
}

void NetTest::loop() {
  const uint32_t now = millis();
#ifdef USE_WIFI
  // Read here, on the loop, and only copied out by the server tasks: the
  // Wi-Fi component is not something to call from another task. Never while
  // a test runs: on a P4 it is a question to the C6, and its answer is one
  // more packet to receive -- when that receive path is the thing failing,
  // the loop waits for an answer that was dropped, and the task watchdog
  // ends the board. Seen on a panel, both cores idle.
  if (now - this->last_rssi_ > 5000 && wifi::global_wifi_component != nullptr && !::bench::busy()) {
    this->last_rssi_ = now;
    ::bench::set_rssi(wifi::global_wifi_component->wifi_rssi());
  }
#endif
  if (now - this->last_heap_log_ > 10000) {
    this->last_heap_log_ = now;
    char line[256];
    ::bench::facts(line, sizeof line);
    ESP_LOGI(TAG, "%s", line);
  }
}

void NetTest::dump_config() {
  char line[256];
  ::bench::facts(line, sizeof line);
  ESP_LOGCONFIG(TAG,
                "Network test bench:\n"
                "  TCP %u: send to it, it times what arrives\n"
                "  TCP %u: it sends for as many seconds as asked\n"
                "  UDP %u: answers a ping and says what it is\n"
                "  %s",
                this->port_, this->port_ + 1, this->port_ + 2, line);
}

}  // namespace esphome::nettest
