// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// A stand-in for ESP-IDF's USB Host Library with a Bluetooth dongle plugged
// into it, for tools/checkidfusb.py. What the test can do to the dongle.
#pragma once

#include <cstdint>
#include <vector>

#include "usb/usb_host.h"

namespace fake {

enum class Dongle {
  BROADCOM,     // BCM20702A1: vendor class ff/01/01, Bluetooth 4.0, answers from ROM
  REALTEK_ROM,  // TP-Link UB500, RTL8761BU: e0/01/01, waits for its firmware
};

void use(Dongle dongle);
void plug();
void unplug();

// A command's answer arrives this many milliseconds after the command does.
void set_latency(uint16_t opcode, uint32_t ms);
// The inquiry is acknowledged and then never reports anything.
void set_silent_inquiry(bool on);
// The next transfer to complete on this IN endpoint ends with `status`, and
// the endpoint halts, as the library halts one.
void fail_next_in(uint8_t address, usb_transfer_status_t status);
// OUT transfers to the ACL endpoint are not accepted (the dongle NAKs them).
void set_out_stuck(bool on);
// An event the dongle sends of its own accord.
void queue_event(const std::vector<uint8_t> &event);
// ACL sent to the dongle comes straight back.
void set_acl_loopback(bool on);

std::vector<uint16_t> commands();
std::vector<std::vector<uint8_t>> acl_out();
std::vector<std::vector<uint8_t>> rtl_fragments();
unsigned peripheral_map();
unsigned halts(uint8_t address);
unsigned flushes(uint8_t address);
unsigned clears(uint8_t address);
unsigned set_interface_requests();
bool interface_claimed(uint8_t number);
bool closed();

}  // namespace fake
