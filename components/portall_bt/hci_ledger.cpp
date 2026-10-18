// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// What hci_ledger.h describes. Every table below is transcribed from the
// Bluetooth Core Specification 5.4, Vol 4 Part E, section 7 -- each command's
// opcode and the event it says the command generates -- and every event shape
// from section 7.7. None of it is read out of a particular controller.
#include "hci_ledger.h"

#include <cstring>

namespace esphome {
namespace portall_bt {

namespace {

// Events, Vol 4 Part E 7.7.
constexpr uint8_t EV_INQUIRY_COMPLETE = 0x01;
constexpr uint8_t EV_CONNECTION_COMPLETE = 0x03;
constexpr uint8_t EV_DISCONNECTION_COMPLETE = 0x05;
constexpr uint8_t EV_REMOTE_NAME_COMPLETE = 0x07;
constexpr uint8_t EV_COMMAND_COMPLETE = 0x0E;
constexpr uint8_t EV_COMMAND_STATUS = 0x0F;
constexpr uint8_t EV_SYNC_CONNECTION_COMPLETE = 0x2C;
constexpr uint8_t EV_LE_META = 0x3E;
constexpr uint8_t LE_CONNECTION_COMPLETE = 0x01;
constexpr uint8_t LE_ENHANCED_CONNECTION_COMPLETE = 0x0A;

// Commands this file has to name rather than only classify.
constexpr uint16_t OP_INQUIRY = 0x0401;
constexpr uint16_t OP_INQUIRY_CANCEL = 0x0402;
constexpr uint16_t OP_CREATE_CONNECTION = 0x0405;
constexpr uint16_t OP_ACCEPT_CONNECTION = 0x0409;
constexpr uint16_t OP_REMOTE_NAME_REQUEST = 0x0419;
constexpr uint16_t OP_RESET = 0x0C03;
constexpr uint16_t OP_HOST_NUM_COMPLETED = 0x0C35;

bool in(uint16_t opcode, const uint16_t *list, size_t count) {
  for (size_t i = 0; i < count; i++)
    if (list[i] == opcode)
      return true;
  return false;
}

}  // namespace

bool HciLedger::is_setting(uint16_t opcode) {
  static const uint16_t SETTINGS[] = {
      0x0C01,  // Set Event Mask
      0x0C13,  // Write Local Name
      0x0C16,  // Write Connection Accept Timeout
      0x0C18,  // Write Page Timeout
      0x0C1A,  // Write Scan Enable -- whether anything can page the panel at all
      0x0C1C,  // Write Page Scan Activity
      0x0C1E,  // Write Inquiry Scan Activity
      0x0C20,  // Write Authentication Enable
      0x0C24,  // Write Class Of Device
      0x0C26,  // Write Voice Setting
      0x0C31,  // Set Controller To Host Flow Control
      0x0C33,  // Host Buffer Size
      0x0C3A,  // Write Current IAC LAP
      0x0C43,  // Write Inquiry Scan Type
      0x0C45,  // Write Inquiry Mode
      0x0C47,  // Write Page Scan Type
      0x0C52,  // Write Extended Inquiry Response
      0x0C56,  // Write Simple Pairing Mode
      0x0C63,  // Set Event Mask Page 2
      0x0C6D,  // Write LE Host Support
      0x0C7A,  // Write Secure Connections Host Support
      0x080F,  // Write Default Link Policy Settings
      0x2001,  // LE Set Event Mask
      0x2005,  // LE Set Random Address
      0x2024,  // LE Write Suggested Default Data Length
      0x202D,  // LE Set Address Resolution Enable
      0x202E,  // LE Set Resolvable Private Address Timeout
      0x2031,  // LE Set Default PHY
  };
  return in(opcode, SETTINGS, sizeof(SETTINGS) / sizeof(SETTINGS[0]));
}

bool HciLedger::answered_by_status(uint16_t opcode) {
  static const uint16_t STATUS[] = {
      0x0401,  // Inquiry
      0x0405,  // Create Connection
      0x0406,  // Disconnect
      0x0409,  // Accept Connection Request
      0x040A,  // Reject Connection Request
      0x040F,  // Change Connection Packet Type
      0x0411,  // Authentication Requested
      0x0413,  // Set Connection Encryption
      0x0415,  // Change Connection Link Key
      0x0417,  // Link Key Selection
      0x0419,  // Remote Name Request
      0x041B,  // Read Remote Supported Features
      0x041C,  // Read Remote Extended Features
      0x041D,  // Read Remote Version Information
      0x041F,  // Read Clock Offset
      0x0428,  // Setup Synchronous Connection
      0x0429,  // Accept Synchronous Connection Request
      0x042A,  // Reject Synchronous Connection Request
      0x043D,  // Enhanced Setup Synchronous Connection
      0x043E,  // Enhanced Accept Synchronous Connection Request
      0x0801,  // Hold Mode
      0x0803,  // Sniff Mode
      0x0804,  // Exit Sniff Mode
      0x0807,  // QoS Setup
      0x080B,  // Switch Role
      0x0810,  // Flow Specification
      0x0C5F,  // Enhanced Flush
      0x200D,  // LE Create Connection
      0x2013,  // LE Connection Update
      0x2016,  // LE Read Remote Features
      0x2019,  // LE Enable Encryption
      0x2025,  // LE Read Local P-256 Public Key
      0x2026,  // LE Generate DHKey
      0x2032,  // LE Set PHY
      0x2043,  // LE Extended Create Connection
      0x2044,  // LE Periodic Advertising Create Sync
      0x205E,  // LE Generate DHKey v2
  };
  return in(opcode, STATUS, sizeof(STATUS) / sizeof(STATUS[0]));
}

bool HciLedger::expects_no_answer(uint16_t opcode) { return opcode == OP_HOST_NUM_COMPLETED; }

void HciLedger::note_sent(const uint8_t *cmd, uint16_t len, uint32_t now_ms) {
  if (cmd == nullptr || len < 3)
    return;
  const uint16_t opcode = (uint16_t) (cmd[0] | (cmd[1] << 8));
  uint8_t plen = cmd[2];
  if (plen > len - 3)
    plen = (uint8_t) (len - 3);
  const uint8_t *params = cmd + 3;

  if (!expects_no_answer(opcode)) {
    if (this->waiting_count_ == MAX_WAITING) {
      // Eight unanswered commands is a controller that has stopped, and the
      // watchdog will be answering for the oldest already. Make room.
      memmove(&this->waiting_[0], &this->waiting_[1], sizeof(Waiting) * (MAX_WAITING - 1));
      this->waiting_count_--;
    }
    Waiting &w = this->waiting_[this->waiting_count_++];
    w.opcode = opcode;
    w.since = now_ms;
    w.head_len = plen < sizeof(w.head) ? plen : (uint8_t) sizeof(w.head);
    memcpy(w.head, params, w.head_len);
  }

  // Kept for a controller that comes back. At startup that is everything but
  // the reset (the probe has just reset the new one), the one command that is
  // never answered, and anything that starts an operation rather than setting
  // something; afterwards it is the settings alone.
  const bool startup_kind = this->startup_ && opcode != OP_RESET && !expects_no_answer(opcode) &&
                            !answered_by_status(opcode);
  if (startup_kind || is_setting(opcode))
    this->keep_(opcode, params, plen);
}

void HciLedger::note_received(const uint8_t *event, uint16_t len) {
  if (event == nullptr || len < 2)
    return;
  const uint8_t code = event[0];
  uint8_t plen = event[1];
  if (plen > len - 2)
    plen = (uint8_t) (len - 2);
  const uint8_t *p = event + 2;

  switch (code) {
    case EV_COMMAND_COMPLETE: {
      if (plen < 3)
        return;
      const uint16_t opcode = (uint16_t) (p[1] | (p[2] << 8));
      const uint8_t status = plen >= 4 ? p[3] : 0;
      for (size_t i = 0; i < this->waiting_count_; i++) {
        if (this->waiting_[i].opcode != opcode)
          continue;
        memmove(&this->waiting_[i], &this->waiting_[i + 1], sizeof(Waiting) * (this->waiting_count_ - i - 1));
        this->waiting_count_--;
        break;
      }
      if (opcode == OP_INQUIRY_CANCEL && status == 0)
        this->inquiring_ = false;
      return;
    }
    case EV_COMMAND_STATUS: {
      if (plen < 4)
        return;
      const uint8_t status = p[0];
      const uint16_t opcode = (uint16_t) (p[2] | (p[3] << 8));
      for (size_t i = 0; i < this->waiting_count_; i++) {
        const Waiting w = this->waiting_[i];
        if (w.opcode != opcode)
          continue;
        memmove(&this->waiting_[i], &this->waiting_[i + 1], sizeof(Waiting) * (this->waiting_count_ - i - 1));
        this->waiting_count_--;
        if (status != 0)
          break;
        // Started, and owing an event of its own later.
        if (opcode == OP_INQUIRY) {
          this->inquiring_ = true;
        } else if ((opcode == OP_CREATE_CONNECTION || opcode == OP_ACCEPT_CONNECTION ||
                    opcode == OP_REMOTE_NAME_REQUEST) &&
                   w.head_len == 6 && this->pending_count_ < MAX_PENDING) {
          Pending &owed = this->pending_[this->pending_count_++];
          owed.opcode = opcode;
          memcpy(owed.addr, w.head, 6);
        }
        break;
      }
      return;
    }
    case EV_INQUIRY_COMPLETE:
      this->inquiring_ = false;
      return;
    case EV_CONNECTION_COMPLETE:
      if (plen < 9)
        return;
      this->forget_pending_(OP_CREATE_CONNECTION, OP_ACCEPT_CONNECTION, &p[3]);
      if (p[0] == 0)
        this->add_link_((uint16_t) ((p[1] | (p[2] << 8)) & 0x0FFF));
      return;
    case EV_SYNC_CONNECTION_COMPLETE:
      if (plen >= 3 && p[0] == 0)
        this->add_link_((uint16_t) ((p[1] | (p[2] << 8)) & 0x0FFF));
      return;
    case EV_DISCONNECTION_COMPLETE:
      if (plen >= 3 && p[0] == 0)
        this->drop_link_((uint16_t) ((p[1] | (p[2] << 8)) & 0x0FFF));
      return;
    case EV_REMOTE_NAME_COMPLETE:
      if (plen >= 7)
        this->forget_pending_(OP_REMOTE_NAME_REQUEST, OP_REMOTE_NAME_REQUEST, &p[1]);
      return;
    case EV_LE_META:
      if (plen >= 4 && (p[0] == LE_CONNECTION_COMPLETE || p[0] == LE_ENHANCED_CONNECTION_COMPLETE) &&
          p[1] == 0)
        this->add_link_((uint16_t) ((p[2] | (p[3] << 8)) & 0x0FFF));
      return;
    default:
      return;
  }
}

// "No", in the shape the command is answered in. A Command Status is four
// bytes. A Command Complete carries its status and then what the command
// would have returned: the first parameters echoed -- most returns begin with
// the BD_ADDR or the handle the command named -- and zeros after them, as long
// as the longest return there is.
void HciLedger::refuse_(const Waiting &w, Emit emit, void *arg) const {
  if (answered_by_status(w.opcode)) {
    const uint8_t ev[6] = {EV_COMMAND_STATUS, 4, REFUSED, 1, (uint8_t) (w.opcode & 0xFF),
                           (uint8_t) (w.opcode >> 8)};
    emit(ev, sizeof(ev), arg);
    return;
  }
  uint8_t ev[2 + 4 + LONGEST_RETURN] = {};
  ev[0] = EV_COMMAND_COMPLETE;
  ev[1] = (uint8_t) (4 + LONGEST_RETURN);
  ev[2] = 1;
  ev[3] = (uint8_t) (w.opcode & 0xFF);
  ev[4] = (uint8_t) (w.opcode >> 8);
  ev[5] = REFUSED;
  memcpy(&ev[6], w.head, w.head_len);
  emit(ev, sizeof(ev), arg);
}

unsigned HciLedger::lost(Emit emit, void *arg) {
  unsigned emitted = 0;

  // The credit first: nothing Bluedroid sends next can go anywhere until it
  // has an answer for what it is waiting on.
  for (size_t i = 0; i < this->waiting_count_; i++) {
    const Waiting &w = this->waiting_[i];
    if (is_setting(w.opcode)) {
      // Kept already, and the controller that comes back will be told it.
      const uint8_t ev[6] = {EV_COMMAND_COMPLETE, 4, 1, (uint8_t) (w.opcode & 0xFF), (uint8_t) (w.opcode >> 8), 0x00};
      emit(ev, sizeof(ev), arg);
    } else {
      this->refuse_(w, emit, arg);
    }
    emitted++;
  }
  this->waiting_count_ = 0;

  // Then the operations that would have ended with an event of their own.
  if (this->inquiring_) {
    const uint8_t ev[3] = {EV_INQUIRY_COMPLETE, 1, 0x00};
    emit(ev, sizeof(ev), arg);
    this->inquiring_ = false;
    emitted++;
  }
  for (size_t i = 0; i < this->pending_count_; i++) {
    const Pending &owed = this->pending_[i];
    if (owed.opcode == OP_REMOTE_NAME_REQUEST) {
      uint8_t ev[2 + 255] = {};
      ev[0] = EV_REMOTE_NAME_COMPLETE;
      ev[1] = 255;
      ev[2] = PAGE_TIMEOUT;
      memcpy(&ev[3], owed.addr, 6);
      emit(ev, sizeof(ev), arg);
    } else {
      uint8_t ev[2 + 11] = {};
      ev[0] = EV_CONNECTION_COMPLETE;
      ev[1] = 11;
      ev[2] = owed.opcode == OP_CREATE_CONNECTION ? PAGE_TIMEOUT : CONNECTION_TIMEOUT;
      memcpy(&ev[5], owed.addr, 6);
      ev[11] = 0x01;  // ACL
      emit(ev, sizeof(ev), arg);
    }
    emitted++;
  }
  this->pending_count_ = 0;

  // And the links, which is also what hands back the ACL credits they held.
  for (size_t i = 0; i < this->link_count_; i++) {
    const uint16_t h = this->links_[i];
    const uint8_t ev[6] = {EV_DISCONNECTION_COMPLETE, 4, 0x00, (uint8_t) (h & 0xFF), (uint8_t) (h >> 8),
                           CONNECTION_TIMEOUT};
    emit(ev, sizeof(ev), arg);
    emitted++;
  }
  this->link_count_ = 0;
  return emitted;
}

void HciLedger::answer_for(const uint8_t *cmd, uint16_t len, Emit emit, void *arg) {
  if (cmd == nullptr || len < 3)
    return;
  const uint16_t opcode = (uint16_t) (cmd[0] | (cmd[1] << 8));
  if (expects_no_answer(opcode))
    return;
  uint8_t plen = cmd[2];
  if (plen > len - 3)
    plen = (uint8_t) (len - 3);
  if (is_setting(opcode)) {
    this->keep_(opcode, cmd + 3, plen);
    const uint8_t ev[6] = {EV_COMMAND_COMPLETE, 4, 1, (uint8_t) (opcode & 0xFF), (uint8_t) (opcode >> 8), 0x00};
    emit(ev, sizeof(ev), arg);
    return;
  }
  Waiting w{};
  w.opcode = opcode;
  w.head_len = plen < sizeof(w.head) ? plen : (uint8_t) sizeof(w.head);
  memcpy(w.head, cmd + 3, w.head_len);
  this->refuse_(w, emit, arg);
}

unsigned HciLedger::overdue(uint32_t now_ms, uint32_t limit_ms, Emit emit, void *arg, uint16_t *first) {
  unsigned answered = 0;
  size_t keep = 0;
  for (size_t i = 0; i < this->waiting_count_; i++) {
    const Waiting w = this->waiting_[i];
    if ((uint32_t) (now_ms - w.since) < limit_ms) {
      this->waiting_[keep++] = w;
      continue;
    }
    // Refused, a setting included: the controller is still there and has not
    // said whether it took it, so yes would be a guess.
    this->refuse_(w, emit, arg);
    if (answered == 0 && first != nullptr)
      *first = w.opcode;
    answered++;
  }
  this->waiting_count_ = keep;
  return answered;
}

void HciLedger::keep_(uint16_t opcode, const uint8_t *params, uint8_t len) {
  size_t at = 0;
  while (at < this->arena_used_) {
    const uint16_t op = (uint16_t) (this->arena_[at] | (this->arena_[at + 1] << 8));
    const size_t size = 3u + this->arena_[at + 2];
    if (op != opcode) {
      at += size;
      continue;
    }
    if (this->arena_[at + 2] == len && memcmp(&this->arena_[at + 3], params, len) == 0)
      return;  // said again, the same: nothing changed
    // The latest value, where the first one was: the order a controller was
    // first told things in is the order it is told them again.
    const size_t want = 3u + len;
    if (this->arena_used_ - size + want > ARENA) {
      this->overflowed_++;
      return;
    }
    memmove(&this->arena_[at + want], &this->arena_[at + size], this->arena_used_ - at - size);
    this->arena_used_ = this->arena_used_ - size + want;
    this->arena_[at + 2] = len;
    memcpy(&this->arena_[at + 3], params, len);
    this->changes_++;
    return;
  }
  if (this->arena_used_ + 3u + len > ARENA) {
    this->overflowed_++;
    return;
  }
  this->arena_[this->arena_used_] = (uint8_t) (opcode & 0xFF);
  this->arena_[this->arena_used_ + 1] = (uint8_t) (opcode >> 8);
  this->arena_[this->arena_used_ + 2] = len;
  memcpy(&this->arena_[this->arena_used_ + 3], params, len);
  this->arena_used_ += 3u + len;
  this->changes_++;
}

size_t HciLedger::kept() const {
  size_t count = 0;
  for (size_t at = 0; at < this->arena_used_; at += 3u + this->arena_[at + 2])
    count++;
  return count;
}

uint16_t HciLedger::kept_command(size_t i, uint8_t *out) const {
  size_t at = 0;
  for (size_t k = 0; at < this->arena_used_; k++) {
    const size_t size = 3u + this->arena_[at + 2];
    if (k == i) {
      memcpy(out, &this->arena_[at], size);
      return (uint16_t) size;
    }
    at += size;
  }
  return 0;
}

void HciLedger::forget_pending_(uint16_t opcode_a, uint16_t opcode_b, const uint8_t *addr) {
  size_t keep = 0;
  for (size_t i = 0; i < this->pending_count_; i++) {
    const Pending &owed = this->pending_[i];
    const bool done = (owed.opcode == opcode_a || owed.opcode == opcode_b) && memcmp(owed.addr, addr, 6) == 0;
    if (!done)
      this->pending_[keep++] = owed;
  }
  this->pending_count_ = keep;
}

void HciLedger::add_link_(uint16_t handle) {
  for (size_t i = 0; i < this->link_count_; i++)
    if (this->links_[i] == handle)
      return;
  if (this->link_count_ < MAX_LINKS)
    this->links_[this->link_count_++] = handle;
}

void HciLedger::drop_link_(uint16_t handle) {
  for (size_t i = 0; i < this->link_count_; i++) {
    if (this->links_[i] != handle)
      continue;
    this->links_[i] = this->links_[--this->link_count_];
    return;
  }
}

}  // namespace portall_bt
}  // namespace esphome
