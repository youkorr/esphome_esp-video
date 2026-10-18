// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// usb_bluetooth, from Portall: https://github.com/youkorr/esphome_esp-video
#pragma once

#include <cstddef>
#include <cstdint>

namespace esphome {
namespace usb_bluetooth {

/* What Bluedroid has said to the controller, and what it is still waiting to
 * hear back -- kept so that a controller that goes away does not take
 * Bluedroid down with it.
 *
 * A dongle can go: pulled out, knocked, or resetting itself. Read in ESP-IDF
 * v5.5.5 (bluedroid/hci/hci_layer.c), what Bluedroid then does is nothing at
 * all. A command sent to it has taken the one credit Bluedroid holds, and only
 * a Command Complete or a Command Status gives it back -- command_timed_out()
 * logs after eight seconds and, in its own words, "We shouldn't try to recover
 * the stack from this command timeout." Every later command waits behind it
 * for good. A link that was up stays up in Bluedroid's tables, holding the ACL
 * credits it had in flight. So the panel's Bluetooth was dead until a restart,
 * whatever happened to the dongle afterwards.
 *
 * This keeps exactly what is needed to answer for a controller that is not
 * there, and to bring a new one to where the old one was:
 *
 *   - the commands still waiting for an answer, which lost() answers;
 *   - the operations a Command Status started and whose end has not come
 *     (an inquiry, a page, a name request), which lost() ends;
 *   - the links that are up, which lost() closes, and closing them is also
 *     what gives Bluedroid its ACL credits back (l2cu_release_lcb() adds a
 *     link's sent_not_acked to controller_xmit_window);
 *   - and the controller's SETTINGS, latest value each, in the order they were
 *     first given: everything Bluedroid sent while it started, and afterwards
 *     the writes that leave state in a controller -- whether it can be paged,
 *     its class, its name, its event masks. A controller that comes back is
 *     told them again, so Bluedroid finds it as it left it.
 *
 * Plain bytes in, plain bytes out, no RTOS and no lock: the transport in
 * usb_bluetooth.cpp holds the lock and decides when an event reaches Bluedroid.
 * That is what lets tools/bttest/ledger.cpp check every byte it produces. */
class HciLedger {
 public:
  // A synthetic event, as code, parameter length, parameters.
  using Emit = void (*)(const uint8_t *event, uint16_t len, void *arg);

  // The status a synthetic "no" carries: Command Disallowed, which is what a
  // controller says when it cannot do a thing now. Not Hardware Failure --
  // nothing failed, the controller is simply not there to ask.
  static constexpr uint8_t REFUSED = 0x0C;
  static constexpr uint8_t PAGE_TIMEOUT = 0x04;
  static constexpr uint8_t CONNECTION_TIMEOUT = 0x08;

  // Bluedroid holds as many commands in flight as the controller grants it,
  // which is one on both dongles measured here; eight is room.
  static constexpr size_t MAX_WAITING = 8;
  static constexpr size_t MAX_LINKS = 16;
  static constexpr size_t MAX_PENDING = 4;
  // Room for the settings. Bluedroid's startup is about thirty commands, nearly
  // all of them a few bytes, and the two long ones are the local name (251)
  // and the extended inquiry response (244).
  static constexpr size_t ARENA = 1536;
  // The longest return any BR/EDR command has: Read Local Name's 248 bytes of
  // name. A refused Command Complete carries that many after its status, zero
  // after what it echoes, so no handler that reads past the status before
  // checking it reads past the event.
  static constexpr size_t LONGEST_RETURN = 248;

  // From the moment the transport is attached until the stack is up, EVERY
  // command is kept: that is the controller's whole bring-up, and replaying it
  // is the one way to be sure nothing Bluedroid set at startup is missed.
  void set_recording_startup(bool on) { this->startup_ = on; }
  bool recording_startup() const { return this->startup_; }

  // A command on its way to the controller: opcode (little-endian), parameter
  // length, parameters.
  void note_sent(const uint8_t *cmd, uint16_t len, uint32_t now_ms);
  // An event on its way to Bluedroid: code, parameter length, parameters.
  void note_received(const uint8_t *event, uint16_t len);

  // The controller is gone. Answers every command still waiting, ends every
  // operation still running, closes every link -- in that order, so Bluedroid
  // has its credit back before it hears about the links. Returns how many
  // events it emitted.
  unsigned lost(Emit emit, void *arg);
  // While it is gone: a command is answered at once. A setting is kept and
  // told yes, because the controller that comes back will be given it; any
  // other command is told no, in the shape that command is answered in.
  void answer_for(const uint8_t *cmd, uint16_t len, Emit emit, void *arg);
  // Commands still waiting after `limit_ms` are answered for, one at a time,
  // as lost() would. `first` receives the first one's opcode, for the log.
  unsigned overdue(uint32_t now_ms, uint32_t limit_ms, Emit emit, void *arg, uint16_t *first);

  // The commands a returning controller is told, in order.
  size_t kept() const;
  // The i-th of them -- opcode, length, parameters -- into `out`, which must
  // hold 258 bytes. Returns its length, or 0 past the end.
  uint16_t kept_command(size_t i, uint8_t *out) const;
  // Moves every time a kept command is added or changed, so a replay can tell
  // whether something was set while it ran.
  uint32_t changes() const { return this->changes_; }
  // Settings that did not fit in ARENA. Said once by the transport.
  uint32_t overflowed() const { return this->overflowed_; }

  size_t links() const { return this->link_count_; }
  size_t waiting() const { return this->waiting_count_; }
  size_t pending() const { return this->pending_count_ + (this->inquiring_ ? 1 : 0); }

  // The three classifications, public so that a test can state them from the
  // specification rather than read them back out of this file.
  //
  // A setting: a write that leaves state in the controller and means the same
  // whenever it is given again.
  static bool is_setting(uint16_t opcode);
  // Answered by a Command Status rather than a Command Complete: the commands
  // that start something and finish it later (Core 5.4 Vol 4 Part E 7.1-7.8,
  // each command's "Event(s) generated").
  static bool answered_by_status(uint16_t opcode);
  // Not answered at all unless it fails: Host Number Of Completed Packets.
  static bool expects_no_answer(uint16_t opcode);

 private:
  struct Waiting {
    uint16_t opcode;
    uint32_t since;
    uint8_t head[6];  // the first parameters: a BD_ADDR or a handle
    uint8_t head_len;
  };
  struct Pending {
    uint16_t opcode;  // what started it: create, accept, or name request
    uint8_t addr[6];
  };

  void keep_(uint16_t opcode, const uint8_t *params, uint8_t len);
  void refuse_(const Waiting &w, Emit emit, void *arg) const;
  void forget_pending_(uint16_t opcode_a, uint16_t opcode_b, const uint8_t *addr);
  void add_link_(uint16_t handle);
  void drop_link_(uint16_t handle);

  bool startup_{false};

  Waiting waiting_[MAX_WAITING]{};
  size_t waiting_count_{0};

  Pending pending_[MAX_PENDING]{};
  size_t pending_count_{0};
  bool inquiring_{false};

  uint16_t links_[MAX_LINKS]{};
  size_t link_count_{0};

  // Kept commands back to back: opcode low, opcode high, length, parameters.
  uint8_t arena_[ARENA]{};
  size_t arena_used_{0};
  uint32_t changes_{0};
  uint32_t overflowed_{0};
};

}  // namespace usb_bluetooth
}  // namespace esphome
