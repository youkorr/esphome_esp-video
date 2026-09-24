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

#include "portall.h"

#include "esphome/core/log.h"
#include "esp_heap_caps.h"

#include <cerrno>
#include <cstring>

// Present only when the wifi component was built with runtime roaming
// suppression, which portall asks for from its schema whenever `port:` is set.
// Guarded on the define rather than on wifi being there at all: a panel on
// Ethernet, or an ESPHome older than the API, compiles none of this.
#if defined(USE_ESP32) && defined(USE_WIFI) && defined(USE_WIFI_RUNTIME_ROAMING_SUPPRESSION)
#include "esphome/components/wifi/wifi_component.h"
#define PORTALL_HOLDS_ROAMING 1
#endif

extern "C" {
#include "freertos/task.h"
#include <lwip/sockets.h>
#include <lwip/tcp.h>
}

namespace esphome {
namespace portall {

static const char *const TAG = "portall.net";

// OPTIMISATION : Augmentation de la taille de lecture (32 Ko) pour saturer le débit du C6/Wi-Fi
static constexpr size_t NET_READ_SIZE = 32768;
static constexpr int NET_RECV_TIMEOUT_S = 30;

// ESPHome looks for a better access point every five minutes while the signal
// is below -49 dBm, and the radio leaves its channel for most of a second to
// do it. Nothing arrives meanwhile, then everything arrives at once: a panel
// log put 200 dropped blocks of sound -- two seconds -- and a picture rate of
// 12 in the second after one "Roam scan", with "wifi took 642 ms" beside it.
//
// So roaming is held off while something is actually streaming, and let go
// once only heartbeats have arrived for ROAM_QUIET_MS. That is what sendspin
// does for its own stream. A still dashboard sends nothing but a heartbeat,
// so a panel sitting idle still roams -- its checks are simply moved to the
// moments when nobody is watching. A read no longer than one header is a
// heartbeat; anything longer carries a picture or sound.
static constexpr uint32_t ROAM_QUIET_MS = 15000;
static constexpr int HEARTBEAT_BYTES = sizeof(udisp_frame_header_t);

static void hold_roaming(bool &held, bool want) {
#ifdef PORTALL_HOLDS_ROAMING
  if (held == want || wifi::global_wifi_component == nullptr)
    return;
  // Counted by the wifi component, so every request must be released exactly
  // once or roaming stays off for good -- hence the flag, and the release on
  // every way out of a connection.
  if (want) {
    wifi::global_wifi_component->request_roaming_suppression();
  } else {
    wifi::global_wifi_component->release_roaming_suppression();
  }
  held = want;
  ESP_LOGD(TAG, "%s", want ? "Streaming: Wi-Fi roaming scans held off" : "Idle: Wi-Fi roaming scans allowed again");
#else
  (void) held;
  (void) want;
#endif
}

#ifdef USE_TOUCHSCREEN
void Portall::queue_touch_(const touchscreen::TouchPoints_t &points) {
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
    // Make room by dropping the OLDEST, and send the new one either way. The
    // last event of a press is the release, so a full queue that drops the
    // newest drops exactly that, and a sender left holding a button that was
    // let go does not act on it until some later release happens to get
    // through. That was worth twenty seconds of apparent latency once.
    TouchEvent discarded;
    xQueueReceive(this->touch_queue_, &discarded, 0);
    xQueueSend(this->touch_queue_, &event, 0);
  }
}
#endif  // USE_TOUCHSCREEN

void Portall::ask_home() {
  /* A latch rather than a queue: going home twice is going home once, and a
     panel asked before a sender has connected should go home when one does
     rather than forgetting it was asked. */
  this->home_pending_ = true;
  ESP_LOGD(TAG, "Asked to go back to this panel's own page");
}

void Portall::send_key(uint16_t usage_page, uint16_t usage) {
  if (this->key_queue_ == nullptr)
    return;
  const KeyEvent event{usage_page, usage};
  if (xQueueSend(this->key_queue_, &event, 0) != pdTRUE) {
    /* Drop the OLDEST, as the touch queue does. A full queue here means the
       link has gone away while somebody kept pressing, and the presses worth
       keeping are the last ones -- what a person pressed most recently is
       what they still want to happen. */
    KeyEvent discarded;
    xQueueReceive(this->key_queue_, &discarded, 0);
    xQueueSend(this->key_queue_, &event, 0);
  }
}

void Portall::set_awake(bool awake) {
  if (this->asleep_ != !awake) {
    this->asleep_ = !awake;
    this->status_pending_ = true;
    ESP_LOGD(TAG, "Panel %s; telling the sender", awake ? "awake" : "asleep");
  }
}

void Portall::send_queued_messages_(int client) {
  /* Two bytes, the same shape as 'T', because one definition of the wire
     format is worth more than a tidier message per kind of thing on it -- the
     sender's parser needs at least two to recognise anything. The second is
     the state itself.

     'H' was removed once, as a portall.home action nobody was calling, and the
     SENDER's half was deliberately kept -- a board is flashed by hand and the
     sender is fetched when the add-on's image is built, so the two are never
     updated together and the tolerant end is the one to keep. That patience
     paid: a remote with a Back button is what wanted it, and putting the board
     half back needed no change to any sender at all.

     'K' is the new one, and it is five bytes rather than two. */
  if (this->status_pending_) {
    this->status_pending_ = false;
    const uint8_t message[2] = {'S', (uint8_t) (this->asleep_ ? 0 : 1)};
    if (::send(client, message, sizeof(message), MSG_DONTWAIT) < 0)
      this->status_pending_ = true;
  }

  /* 'H' again, and this is the message the comment above says the sender still
     understands -- so a panel flashed with this and an add-on built any time
     in the last several releases already agree about it. Nothing on the sender
     side had to change for a remote's Back button to bring a panel home. */
  if (this->home_pending_) {
    this->home_pending_ = false;
    const uint8_t message[2] = {'H', 0};
    if (::send(client, message, sizeof(message), MSG_DONTWAIT) < 0)
      this->home_pending_ = true;
  }

  /* 'K', then a HID usage page and usage, both little-endian: five bytes, the
     same fixed shape as the two above rather than a length-prefixed thing of
     its own. The sender turns the usage into whatever its browser calls that
     key, which is why no name crosses here. */
  if (this->key_queue_ != nullptr) {
    KeyEvent key;
    while (xQueuePeek(this->key_queue_, &key, 0) == pdTRUE) {
      const uint8_t message[5] = {
          'K',
          (uint8_t) (key.page & 0xFF),  (uint8_t) (key.page >> 8),
          (uint8_t) (key.usage & 0xFF), (uint8_t) (key.usage >> 8),
      };
      if (::send(client, message, sizeof(message), MSG_DONTWAIT) < 0)
        return;
      xQueueReceive(this->key_queue_, &key, 0);
    }
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

void Portall::setup_network_() {
  if (this->port_ == 0)
    return;
#ifdef USE_TOUCHSCREEN
  if (this->touchscreen_ != nullptr) {
    this->touch_queue_ = xQueueCreate(8, sizeof(TouchEvent));
  }
#endif
  /* Not behind USE_TOUCHSCREEN: a key has nothing to do with a touch screen,
     and a panel driven entirely by a remote may have no digitizer at all. */
  this->key_queue_ = xQueueCreate(UDISP_NET_KEY_QUEUE, sizeof(KeyEvent));
  // Utilisation de tskNO_AFFINITY pour répartir la charge réseau sur les deux cœurs RISC-V du P4
  xTaskCreatePinnedToCore(Portall::network_task, "udispnet", 4096, this, 4, nullptr, tskNO_AFFINITY);
  ESP_LOGCONFIG(TAG, "Listening on port %u for frames", (unsigned) this->port_);
}

void Portall::network_task(void *param) { static_cast<Portall *>(param)->run_network_task(); }

void Portall::run_network_task() {
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

      // No SO_RCVBUF here on purpose. It is a ceiling on what one socket
      // will hold, and the TCP receive window is what actually governs how
      // much a sender may have in flight -- so a value set here can only ever
      // bind BELOW the window and throttle the very thing it looked like it
      // was helping. It was 98304, which was above the 64800 this component
      // used to ask for and would be far below the 512000 that ESPHome's
      // high-performance networking now sets. The window is the control;
      // there is nothing to add beside it.

      char peer_text[16] = {};
      ::inet_ntoa_r(peer.sin_addr, peer_text, sizeof(peer_text));
      ESP_LOGI(TAG, "Sender connected from %s", peer_text);
      this->net_client_seen_ = true;

#ifdef USE_TOUCHSCREEN
      if (this->touch_queue_ != nullptr)
        xQueueReset(this->touch_queue_);
      this->last_touch_valid_ = false;
#endif
      this->status_pending_ = true;
      
      TickType_t last_recv_time = xTaskGetTickCount();
      TickType_t last_stream_time = last_recv_time;
      bool roaming_held = false;

      while (true) {
        fd_set readable;
        FD_ZERO(&readable);
        FD_SET(client, &readable);
        
        struct timeval slice = {.tv_sec = 0, .tv_usec = 5000};
        int ready = ::select(client + 1, &readable, nullptr, nullptr, &slice);

        this->send_queued_messages_(client);

        if (roaming_held && (xTaskGetTickCount() - last_stream_time) > pdMS_TO_TICKS(ROAM_QUIET_MS))
          hold_roaming(roaming_held, false);

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
          if (received > HEARTBEAT_BYTES) {
            last_stream_time = last_recv_time;
            hold_roaming(roaming_held, true);
          }
          this->feed_(buffer, (size_t) received, true);
          continue;
        }
        
        ESP_LOGI(TAG, "Sender disconnected%s", received == 0 ? "" : " (no data)");
        break;
      }

      hold_roaming(roaming_held, false);
      this->reset_stream_();
      ::close(client);
    }
    ::close(listener);
  }
}

}  // namespace portall
}  // namespace esphome
