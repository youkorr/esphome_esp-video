// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
#include "facts.h"
#include "servers.h"

#include <atomic>
#include <cstdio>
#include <cstring>

#include "esp_heap_caps.h"
#include "sdkconfig.h"

#ifdef USE_ESP32_HOSTED
#include <esp_hosted.h>
#endif
#ifdef ESP_PLATFORM
#include "esp_system.h"
#endif

namespace bench {

// -1 is "not set in this build", which is itself an answer: the option does
// not exist there, or the condition it hangs on was false.
#ifndef CONFIG_WIFI_RMT_STATIC_RX_BUFFER_NUM
#define CONFIG_WIFI_RMT_STATIC_RX_BUFFER_NUM -1
#endif
#ifndef CONFIG_WIFI_RMT_DYNAMIC_RX_BUFFER_NUM
#define CONFIG_WIFI_RMT_DYNAMIC_RX_BUFFER_NUM -1
#endif
#ifndef CONFIG_WIFI_RMT_RX_BA_WIN
#define CONFIG_WIFI_RMT_RX_BA_WIN -1
#endif
#ifndef CONFIG_WIFI_RMT_TX_BA_WIN
#define CONFIG_WIFI_RMT_TX_BA_WIN -1
#endif
#ifndef CONFIG_WIFI_RMT_CACHE_TX_BUFFER_NUM
#define CONFIG_WIFI_RMT_CACHE_TX_BUFFER_NUM -1
#endif
#ifndef CONFIG_LWIP_TCP_WND_DEFAULT
#define CONFIG_LWIP_TCP_WND_DEFAULT -1
#endif
#ifndef CONFIG_LWIP_TCP_SND_BUF_DEFAULT
#define CONFIG_LWIP_TCP_SND_BUF_DEFAULT -1
#endif

#if defined(CONFIG_WIFI_RMT_STATIC_TX_BUFFER)
static const char TX_KIND[] = "static";
static const int TX_COUNT = CONFIG_WIFI_RMT_STATIC_TX_BUFFER_NUM;
#elif defined(CONFIG_WIFI_RMT_DYNAMIC_TX_BUFFER)
static const char TX_KIND[] = "dynamic";
static const int TX_COUNT = CONFIG_WIFI_RMT_DYNAMIC_TX_BUFFER_NUM;
#else
static const char TX_KIND[] = "unknown";
static const int TX_COUNT = -1;
#endif

#ifdef CONFIG_LWIP_TCP_SACK_OUT
static const int SACK = 1;
#else
static const int SACK = 0;
#endif

// Where a plain malloc() goes when internal RAM is short, which is where
// esp-hosted's copy of every received packet goes.
#ifdef CONFIG_SPIRAM_USE_MALLOC
static const char MALLOC[] = "psram";
#else
static const char MALLOC[] = "internal";
#endif

// Why the board last started, so a run that stopped can tell a board that
// restarted from one that only stopped reading. The common ones by name,
// the rest by number.
static const char *reset_reason(char *spare, size_t room) {
#ifdef ESP_PLATFORM
  const int reason = static_cast<int>(esp_reset_reason());
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:
      return "poweron";
    case ESP_RST_SW:
      return "software";
    case ESP_RST_PANIC:
      return "panic";
    case ESP_RST_INT_WDT:
      return "int_wdt";
    case ESP_RST_TASK_WDT:
      return "task_wdt";
    case ESP_RST_WDT:
      return "wdt";
    case ESP_RST_BROWNOUT:
      return "brownout";
    default:
      snprintf(spare, room, "%d", reason);
      return spare;
  }
#else
  (void) spare;
  (void) room;
  return "host";
#endif
}

#ifndef ESP_PLATFORM
// On a workstation the clock does not start with the program.
static const int64_t g_started = now_us();
#endif

static unsigned uptime_s() {
#ifdef ESP_PLATFORM
  return static_cast<unsigned>(now_us() / 1000000);
#else
  return static_cast<unsigned>((now_us() - g_started) / 1000000);
#endif
}

static char g_label[16] = "unnamed";
static char g_c6[16] = "?";
static std::atomic<int> g_rssi{0};

void set_label(const char *label) {
  strncpy(g_label, label, sizeof g_label - 1);
  g_label[sizeof g_label - 1] = '\0';
}

void set_rssi(int rssi) { g_rssi = rssi; }

void ask_c6_version() {
#ifdef USE_ESP32_HOSTED
  // As ESPHome's own esp32_hosted update entity asks it.
  esp_hosted_coprocessor_fwver_t version;
  if (esp_hosted_get_coprocessor_fwversion(&version) == 0)
    snprintf(g_c6, sizeof g_c6, "%u.%u.%u", static_cast<unsigned>(version.major1),
             static_cast<unsigned>(version.minor1), static_cast<unsigned>(version.patch1));
  else
    snprintf(g_c6, sizeof g_c6, "unknown");
#endif
}

int facts(char *out, size_t room) {
  if (room == 0)
    return 0;
  char spare[8];
  int n = snprintf(out, room,
                   "build=%s c6=%s rx=%d/%d tx=%s%d cache=%d ba=rx%d/tx%d wnd=%d snd=%d sack=%d heap=%u/%u rssi=%d "
                   "up=%u reset=%s malloc=%s",
                   g_label, g_c6, CONFIG_WIFI_RMT_STATIC_RX_BUFFER_NUM, CONFIG_WIFI_RMT_DYNAMIC_RX_BUFFER_NUM, TX_KIND,
                   TX_COUNT, CONFIG_WIFI_RMT_CACHE_TX_BUFFER_NUM, CONFIG_WIFI_RMT_RX_BA_WIN, CONFIG_WIFI_RMT_TX_BA_WIN,
                   CONFIG_LWIP_TCP_WND_DEFAULT, CONFIG_LWIP_TCP_SND_BUF_DEFAULT, SACK,
                   static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
                   static_cast<unsigned>(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL)), g_rssi.load(),
                   uptime_s(), reset_reason(spare, sizeof spare), MALLOC);
  if (n < 0)
    return 0;
  return n < static_cast<int>(room) ? n : static_cast<int>(room) - 1;
}

}  // namespace bench
