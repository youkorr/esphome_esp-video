/* The same screen, fed over the network instead of the cable.
 *
 * Everything below the transport is shared with the USB path: the bytes go to
 * feed_(), which assembles frames, and from there through the hardware JPEG
 * decoder, the rotation accelerator and the panel. This file only moves bytes.
 *
 * It adds to the USB interface rather than replacing it, so a board can stay
 * plugged in for its speaker -- which is a USB interface and has no network
 * equivalent here -- while the picture arrives over Wi-Fi.
 *
 * The socket carries traffic the other way too. Touches go back up it, so a
 * board with no cable at all is still an input device: whoever is drawing the
 * picture learns where the finger went and can redraw accordingly. Over USB
 * that job belongs to HID, which every operating system understands without
 * being told; over the network there is no such class, so the contacts are
 * sent as they are and the sender decides what they mean.
 */

#include "usb_display.h"

#include "esphome/core/log.h"
#include "esp_heap_caps.h"

#include <cerrno>
#include <cstring>

extern "C" {
#include "freertos/task.h"
#include <lwip/sockets.h>
#include <lwip/tcp.h>
}

namespace esphome {
namespace usb_display {

static const char *const TAG = "usb_display.net";

// OPTIMISATION : Augmentation de la taille de lecture (32 Ko) pour saturer le débit du C6/Wi-Fi
static constexpr size_t NET_READ_SIZE = 32768;
static constexpr int NET_RECV_TIMEOUT_S = 30;

#ifdef USE_TOUCHSCREEN
void USBDisplay::queue_touch_(const touchscreen::TouchPoints_t &points) {
  if (this->touch_queue_ == nullptr)
    return;
  
  if (this->asleep_)
    return;
    
  TouchEvent event = {};
  for (const auto &point : points) {
    if (event.count >= UDISP_NET_TOUCH_MAX)
      break;
    event.id[event.count] = point.id;
    event.x[event.count] = point.x;
    event.y[event.count] = point.y;
    event.count++;
  }

  if (this->last_touch_valid_ && std::memcmp(&event, &this->last_touch_, sizeof(event)) == 0)
    return;
    
  this->last_touch_ = event;
  this->last_touch_valid_ = true;

  if (xQueueSend(this->touch_queue_, &event, 0) != pdTRUE) {
    TouchEvent discarded;
    if (xQueueReceive(this->touch_queue_, &discarded, 0) == pdTRUE) {
      xQueueSend(this->touch_queue_, &event, 0);
    }
  }
}
#endif  // USE_TOUCHSCREEN

void USBDisplay::set_awake(bool awake) {
  if (this->asleep_ != !awake) {
    this->asleep_ = !awake;
    this->status_pending_ = true;
    ESP_LOGD(TAG, "Panel %s; telling the sender", awake ? "awake" : "asleep");
  }
}

void USBDisplay::send_queued_messages_(int client) {
  if (this->status_pending_) {
    this->status_pending_ = false;
    const uint8_t message[2] = {'S', (uint8_t) (this->asleep_ ? 0 : 1)};
    if (::send(client, message, sizeof(message), MSG_DONTWAIT) < 0)
      this->status_pending_ = true;
  }
#ifdef USE_TOUCHSCREEN
  if (this->touch_queue_ == nullptr)
    return;
    
  TouchEvent event;
  while (xQueuePeek(this->touch_queue_, &event, 0) == pdTRUE) {
    uint8_t message[2 + UDISP_NET_TOUCH_MAX * 5];
    message[0] = 'T';
    message[1] = event.count;
    size_t at = 2;
    for (uint8_t i = 0; i < event.count; i++) {
      message[at++] = event.id[i];
      message[at++] = (uint8_t) (event.x[i] & 0xFF);
      message[at++] = (uint8_t) (event.x[i] >> 8);
      message[at++] = (uint8_t) (event.y[i] & 0xFF);
      message[at++] = (uint8_t) (event.y[i] >> 8);
    }
    
    if (::send(client, message, at, MSG_DONTWAIT) < 0)
      return;
      
    xQueueReceive(this->touch_queue_, &event, 0);
  }
#endif  // USE_TOUCHSCREEN
}

void USBDisplay::setup_network_() {
  if (this->port_ == 0)
    return;
#ifdef USE_TOUCHSCREEN
  if (this->touchscreen_ != nullptr) {
    this->touch_queue_ = xQueueCreate(8, sizeof(TouchEvent));
  }
#endif
  // Utilisation de tskNO_AFFINITY pour répartir la charge réseau sur les deux cœurs RISC-V du P4
  xTaskCreatePinnedToCore(USBDisplay::network_task, "udispnet", 4096, this, 4, nullptr, tskNO_AFFINITY);
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
        break; 
      }

      ::setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
      
      struct timeval timeout = {.tv_sec = NET_RECV_TIMEOUT_S, .tv_usec = 0};
      ::setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
      
      ::setsockopt(client, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof(one));
      int keepidle = 10;
      int keepintvl = 5;
      int keepcnt = 3;
      ::setsockopt(client, IPPROTO_TCP, TCP_KEEPIDLE, &keepidle, sizeof(keepidle));
      ::setsockopt(client, IPPROTO_TCP, TCP_KEEPINTVL, &keepintvl, sizeof(keepintvl));
      ::setsockopt(client, IPPROTO_TCP, TCP_KEEPCNT, &keepcnt, sizeof(keepcnt));

      // OPTIMISATION HAUT DÉBIT : Fenêtre de réception TCP élargie à ~96 Ko (3 * 32 Ko)
      // Empêche le PC de s'arrêter d'envoyer pendant que le P4 décode la frame.
      int rcvbuf = 3 * (int) NET_READ_SIZE;
      if (::setsockopt(client, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf)) < 0) {
          ESP_LOGW(TAG, "Could not set SO_RCVBUF");
      }

      char peer_text[16] = {};
      ::inet_ntoa_r(peer.sin_addr, peer_text, sizeof(peer_text));
      ESP_LOGI(TAG, "Sender connected from %s", peer_text);

#ifdef USE_TOUCHSCREEN
      if (this->touch_queue_ != nullptr)
        xQueueReset(this->touch_queue_);
      this->last_touch_valid_ = false;
#endif
      this->status_pending_ = true;
      
      TickType_t last_recv_time = xTaskGetTickCount();

      while (true) {
        fd_set readable;
        FD_ZERO(&readable);
        FD_SET(client, &readable);
        
        struct timeval slice = {.tv_sec = 0, .tv_usec = 5000};
        int ready = ::select(client + 1, &readable, nullptr, nullptr, &slice);

        this->send_queued_messages_(client);

        if (ready == 0) {
          if ((xTaskGetTickCount() - last_recv_time) > pdMS_TO_TICKS(NET_RECV_TIMEOUT_S * 1000)) {
            ESP_LOGW(TAG, "Sender silent for %d seconds, disconnecting", NET_RECV_TIMEOUT_S);
            break;
          }
          continue; 
        }
        
        if (ready < 0) {
          ESP_LOGW(TAG, "select() failed: %s", strerror(errno));
          break;
        }

        int received = ::recv(client, buffer, NET_READ_SIZE, 0);
        if (received > 0) {
          last_recv_time = xTaskGetTickCount();
          this->feed_(buffer, (size_t) received, true);
          continue;
        }
        
        ESP_LOGI(TAG, "Sender disconnected%s", received == 0 ? "" : " (no data)");
        break;
      }

      this->reset_stream_();
      ::close(client);
    }
    ::close(listener);
  }
}

}  // namespace usb_display
}  // namespace esphome
