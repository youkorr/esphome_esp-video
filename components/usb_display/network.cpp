/* The same screen, fed over the network instead of the cable.
 *
 * Everything below the transport is shared with the USB path: the bytes go to
 * feed_(), which assembles frames, and from there through the hardware JPEG
 * decoder, the rotation accelerator and the panel. This file only moves bytes.
 *
 * It adds to the USB interface rather than replacing it, so a board can stay
 * plugged in for its touch screen and its speaker -- both of which are USB
 * interfaces and have no network equivalent here -- while the picture arrives
 * over Wi-Fi.
 */

#include "usb_display.h"

#include "esphome/core/log.h"

#include "esp_heap_caps.h"

#include <cerrno>
#include <cstring>

extern "C" {
#include "freertos/task.h"
#include <lwip/sockets.h>
}

namespace esphome {
namespace usb_display {

static const char *const TAG = "usb_display.net";

// One read from the socket. Large enough that a frame is a handful of reads
// rather than hundreds, small enough to sit in internal RAM alongside a task.
static constexpr size_t NET_READ_SIZE = 4096;
// How long to wait for a client's next byte before deciding it has gone away
// without saying so. A sender at any frame rate at all beats this easily.
static constexpr int NET_RECV_TIMEOUT_S = 10;

void USBDisplay::setup_network_() {
  if (this->port_ == 0)
    return;
  xTaskCreatePinnedToCore(USBDisplay::network_task, "udispnet", 4096, this, 4, nullptr, 0);
  ESP_LOGCONFIG(TAG, "Listening on port %u for frames", (unsigned) this->port_);
}

void USBDisplay::network_task(void *param) { static_cast<USBDisplay *>(param)->run_network_task(); }

void USBDisplay::run_network_task() {
  auto *buffer = (uint8_t *) heap_caps_malloc(NET_READ_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (buffer == nullptr) {
    ESP_LOGE(TAG, "Could not allocate the %u byte receive buffer", (unsigned) NET_READ_SIZE);
    vTaskDelete(nullptr);
    return;
  }

  while (true) {
    int listener = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener < 0) {
      ESP_LOGE(TAG, "socket() failed: %s", strerror(errno));
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }

    // Without this a restart cannot rebind while the old connection is still
    // winding down, and the listener is dead for a minute for no reason.
    int one = 1;
    ::setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    struct sockaddr_in addr = {};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(this->port_);

    if (::bind(listener, (struct sockaddr *) &addr, sizeof(addr)) < 0 || ::listen(listener, 1) < 0) {
      ESP_LOGE(TAG, "Could not listen on port %u: %s", (unsigned) this->port_, strerror(errno));
      ::close(listener);
      vTaskDelay(pdMS_TO_TICKS(5000));
      continue;
    }

    while (true) {
      struct sockaddr_in peer = {};
      socklen_t peer_len = sizeof(peer);
      int client = ::accept(listener, (struct sockaddr *) &peer, &peer_len);
      if (client < 0) {
        ESP_LOGW(TAG, "accept() failed: %s", strerror(errno));
        break;  // rebuild the listener
      }

      // Nagle would hold a partial frame back waiting for more to say, which is
      // latency spent on nothing: the sender already writes whole frames.
      ::setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
      struct timeval timeout = {.tv_sec = NET_RECV_TIMEOUT_S, .tv_usec = 0};
      ::setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

      char peer_text[16] = {};
      ::inet_ntoa_r(peer.sin_addr, peer_text, sizeof(peer_text));
      ESP_LOGI(TAG, "Sender connected from %s", peer_text);

      while (true) {
        int received = ::recv(client, buffer, NET_READ_SIZE, 0);
        if (received > 0) {
          this->feed_(buffer, (size_t) received);
          continue;
        }
        // Zero is an orderly close; anything else is the sender gone or silent
        // past the timeout. Neither is worth more than a line.
        ESP_LOGI(TAG, "Sender disconnected%s", received == 0 ? "" : " (no data)");
        break;
      }

      // A half-sent frame must not be prepended to the next connection's first
      // one, which would put a header in the middle of a payload.
      this->reset_stream_();
      ::close(client);
    }

    ::close(listener);
  }
}

}  // namespace usb_display
}  // namespace esphome
