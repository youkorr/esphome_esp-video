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
}

namespace esphome {
namespace usb_display {

static const char *const TAG = "usb_display.net";

// One read from the socket. Large enough that a frame is a handful of reads
// rather than hundreds, small enough to sit in internal RAM alongside a task.
static constexpr size_t NET_READ_SIZE = 4096;
// How long to wait for a client's next byte before deciding it has gone away
// without saying so.
//
// This has to allow for silence, because silence is the normal state. A sender
// that only transmits what changed sends nothing at all while nothing changes,
// and a dashboard can sit still for minutes; ten seconds of that used to be
// read as a sender that had died, and the connection was torn down and rebuilt
// every time the screen was quiet. What proves a sender is alive is its
// heartbeat -- an empty end-of-frame marker every few seconds -- so this only
// has to be comfortably longer than that.
static constexpr int NET_RECV_TIMEOUT_S = 30;

#ifdef USE_TOUCHSCREEN
void USBDisplay::queue_touch_(const touchscreen::TouchPoints_t &points) {
  if (this->touch_queue_ == nullptr)
    return;
  // A sleeping panel reports nothing. The touch that wakes it is meant for the
  // panel and not for the page: on anything with a screen that sleeps, the tap
  // that brings it back does not also press what was under the finger.
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

  // A touch screen is polled on an interval -- 20 ms is a common one -- so a
  // finger resting still says the same thing fifty times a second. None of
  // those repeats tell the sender anything it does not already know, and every
  // one of them costs a slot in the queue and a message on the wire.
  if (this->last_touch_valid_ && std::memcmp(&event, &this->last_touch_, sizeof(event)) == 0)
    return;
  this->last_touch_ = event;
  this->last_touch_valid_ = true;

  // Never block the loop for a sender that has stopped reading. When the queue
  // is full the oldest goes, not the newest: for input the latest position is
  // the true one, and -- far more important -- the last event of a press is the
  // release. Dropping the newest drops exactly that, and a sender left holding
  // a button that was let go does not act on it until some later release
  // happens to get through, which reads as seconds of lag rather than as a lost
  // event.
  if (xQueueSend(this->touch_queue_, &event, 0) != pdTRUE) {
    TouchEvent discarded;
    xQueueReceive(this->touch_queue_, &discarded, 0);
    xQueueSend(this->touch_queue_, &event, 0);
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
  // State first. A sender that learns of a wake in the same read as the
  // touches that follow it should act on the wake before them.
  if (this->status_pending_) {
    this->status_pending_ = false;
    const uint8_t message[2] = {'S', (uint8_t) (this->asleep_ ? 0 : 1)};
    if (::send(client, message, sizeof(message), MSG_DONTWAIT) < 0)
      this->status_pending_ = true;  // say it again next time round
  }
#ifdef USE_TOUCHSCREEN
  if (this->touch_queue_ == nullptr)
    return;
  TouchEvent event;
  while (xQueueReceive(this->touch_queue_, &event, 0) == pdTRUE) {
    // 'T', a count, then five bytes per contact.
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
    // Never block here. A sender that only pushes frames and never reads its
    // socket would otherwise fill its receive window and stall this task, and
    // with it the picture -- a steep price for a return channel it is ignoring.
    if (::send(client, message, at, MSG_DONTWAIT) < 0)
      return;  // the read side will notice if the connection is really gone
  }
#endif  // USE_TOUCHSCREEN
}

void USBDisplay::setup_network_() {
  if (this->port_ == 0)
    return;
#ifdef USE_TOUCHSCREEN
  if (this->touchscreen_ != nullptr) {
    // Deep enough to ride out a busy moment, shallow enough that a stale touch
    // is never delivered long after the finger left.
    this->touch_queue_ = xQueueCreate(8, sizeof(TouchEvent));
  }
#endif
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
      // A sender that is gone but whose machine never said so -- unplugged,
      // suspended, a router that forgot the flow -- leaves a connection that
      // looks open and delivers nothing. Keepalive is what notices, and it
      // notices without needing anything to be sent.
      ::setsockopt(client, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof(one));

      char peer_text[16] = {};
      ::inet_ntoa_r(peer.sin_addr, peer_text, sizeof(peer_text));
      ESP_LOGI(TAG, "Sender connected from %s", peer_text);

#ifdef USE_TOUCHSCREEN
      // Contacts made while nobody was connected are history, not input: a
      // sender that has just arrived must not be handed a press from before it
      // existed.
      if (this->touch_queue_ != nullptr)
        xQueueReset(this->touch_queue_);
      // With the queue empty there is nothing for the next event to be a
      // repeat of, whatever the finger was doing before.
      this->last_touch_valid_ = false;
#endif
      // A sender that has just arrived knows nothing about this panel, and
      // must not spend its first minutes drawing for a dark screen.
      this->status_pending_ = true;

      while (true) {
        // Wait briefly rather than blocking on the read: the same loop has to
        // get touches out, and they must not wait for the next frame to arrive.
        fd_set readable;
        FD_ZERO(&readable);
        FD_SET(client, &readable);
        struct timeval slice = {.tv_sec = 0, .tv_usec = 20000};
        int ready = ::select(client + 1, &readable, nullptr, nullptr, &slice);

        this->send_queued_messages_(client);

        if (ready == 0)
          continue;  // nothing to read yet; the sender is simply between frames
        if (ready < 0) {
          ESP_LOGW(TAG, "select() failed: %s", strerror(errno));
          break;
        }

        int received = ::recv(client, buffer, NET_READ_SIZE, 0);
        if (received > 0) {
          // This transport can be made to wait: not reading the socket for a
          // moment blocks the sender, which is the whole of the flow control
          // needed and loses nothing.
          this->feed_(buffer, (size_t) received, true);
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
