// SPDX-License-Identifier: MIT
#include "portall_speedtest.h"
#include "protocol.h"
#include "esphome/core/log.h"
#include "esphome/components/network/util.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <arpa/inet.h>
#include <unistd.h>

namespace esphome::portall_speedtest {
static const char *const TAG = "portall_speedtest";
void SpeedTest::loop() {
  // Dependencies order code generation, not setup. In particular, lwIP's
  // TCP/IP mailbox and semaphores may not exist during component setup.
  // Only start the socket worker after ESPHome reports a connected network.
  if (started_ || !network::is_connected())
    return;
  if (xTaskCreatePinnedToCore(task, "portall_test", 4096, this, 3, nullptr, 1) != pdPASS) {
    ESP_LOGE(TAG, "Could not create speedtest task");
    this->mark_failed();
    return;
  }
  started_ = true;
  ESP_LOGI(TAG, "Speedtest network task started on TCP %u", port_);
  this->disable_loop();
}
void SpeedTest::dump_config() {
  ESP_LOGCONFIG(TAG, "Portall LAN speedtest: TCP %u (authenticated, opt-in)", port_);
}
void SpeedTest::task(void *arg) {
  static_cast<SpeedTest *>(arg)->serve();
  vTaskDelete(nullptr);
}
void SpeedTest::serve() {
  for (;;) {
    int listener = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener >= 0) {
      int yes = 1;
      setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
      sockaddr_in address{};
      address.sin_family = AF_INET;
      address.sin_port = htons(port_);
      address.sin_addr.s_addr = htonl(INADDR_ANY);
      if (bind(listener, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0 &&
          listen(listener, 1) == 0) {
        for (;;) {
          int client = accept(listener, nullptr, nullptr);
          if (client < 0) break;
          // No persistent 16 KiB staging buffer while the test is idle.
          auto *buffer = static_cast<char *>(heap_caps_malloc(CHUNK, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
          if (!buffer) buffer = static_cast<char *>(heap_caps_malloc(CHUNK, MALLOC_CAP_8BIT));
          if (buffer) {
            serve_client(client, key_.c_str(), buffer);
            heap_caps_free(buffer);
          }
          shutdown(client, SHUT_RDWR);
          close(client);
          vTaskDelay(1);
        }
      }
      close(listener);
    }
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}
}  // namespace esphome::portall_speedtest
