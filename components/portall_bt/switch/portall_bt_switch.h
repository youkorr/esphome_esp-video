#pragma once

#include "esphome/core/component.h"
#include "esphome/core/defines.h"

#ifdef USE_SWITCH

#include "esphome/components/switch/switch.h"
#include "../portall_bt.h"

namespace esphome {
namespace portall_bt {

/* Bluetooth off and on, as one thing somebody can press.
 *
 * What "off" means is narrower than the word and is documented on
 * PortallBT::set_bt_enabled: both profiles hang up and the panel stops paging
 * for them. The dongle stays enumerated and Bluedroid stays up -- taking
 * those apart at runtime is a teardown of a stack that took nine faults to
 * raise, and nothing here can compile it to find out what it breaks.
 *
 * What it buys is real all the same: a panel with a speaker that has left the
 * house pages for it on a 2 s -> 60 s clock for ever, and an inquiry-free page
 * is still radio time next to a C6 whose own antenna is centimetres away. */
class PortallBTSwitch : public switch_::Switch, public Component {
 public:
  void setup() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  void set_parent(PortallBT *parent) { this->parent_ = parent; }

 protected:
  void write_state(bool state) override;

  PortallBT *parent_{nullptr};
};

}  // namespace portall_bt
}  // namespace esphome

#endif  // USE_SWITCH
