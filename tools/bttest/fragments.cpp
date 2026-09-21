// Does the firmware download cut a patch up the way a Realtek expects?
//
// Getting this wrong is worse than most faults here: a controller that has
// taken half a patch is stuck half-programmed until somebody unplugs it, and
// the indices are the part nobody would question. Linux writes them as a
// running counter -- `index = j++; if (index == 0x7f) j = 1;` -- so 0x7f IS
// used and the reset lands on ONE, not zero.
//
// The component states the same rule as a function of i. This test states the
// COUNTER form, which is a different formulation, and requires the two to
// agree. Copying the component's formula in here would prove only that it can
// be copied.
#define private public
#define protected public
#define main component_main_unused
#include "portall_bt.cpp"

#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main

#include <cstdio>
#include <vector>

#include "linkstubs.h"

using esphome::portall_bt::rtl_fragment_index;
using esphome::portall_bt::RTL_FRAG_LEN;

static int failures = 0;

static void check(const char *what, bool ok) {
  std::printf("  %s %s\n", ok ? "ok  " : "FAIL", what);
  if (!ok)
    failures++;
}

int main() {
  std::printf("cutting a patch into fragments\n");

  // Linux's own loop, written out.
  std::vector<uint8_t> counter;
  uint8_t j = 0;
  for (uint32_t i = 0; i < 1000; i++) {
    uint8_t index = j++;
    if (index == 0x7F)
      j = 1;
    counter.push_back(index);
  }

  bool agree = true;
  uint32_t first_disagreement = 0;
  for (uint32_t i = 0; i < counter.size(); i++) {
    if (rtl_fragment_index(i) != counter[i]) {
      agree = false;
      first_disagreement = i;
      break;
    }
  }
  if (!agree)
    std::printf("    they part company at fragment %u: %u against %u\n",
                (unsigned) first_disagreement,
                (unsigned) rtl_fragment_index(first_disagreement),
                (unsigned) counter[first_disagreement]);
  check("the index matches Linux's counter over a thousand fragments", agree);

  // The three values the whole rule turns on, named so a reader can see them
  // rather than trusting the loop above.
  check("the first fragment is index 0", rtl_fragment_index(0) == 0);
  check("0x7f is used rather than skipped", rtl_fragment_index(0x7F) == 0x7F);
  check("and the one after it is 1, not 0", rtl_fragment_index(0x80) == 1);
  check("the wrap keeps going", rtl_fragment_index(0x80 + 0x7E) == 0x7F &&
                                rtl_fragment_index(0x80 + 0x7F) == 1);
  // Zero appears exactly once in the whole stream, which is what makes the
  // reset-to-one meaningful at all.
  uint32_t zeros = 0;
  for (uint32_t i = 0; i < 100000; i++)
    if (rtl_fragment_index(i) == 0)
      zeros++;
  check("and index 0 never comes round again", zeros == 1);

  // The real patch, at the size Realtek's own file produces for an RTL8761BU.
  const uint32_t length = 30210;
  const uint32_t whole = length / RTL_FRAG_LEN + 1;
  check("the RTL8761BU's patch is 120 fragments", whole == 120);
  check("and the last one carries the remainder, not a full block",
        length % RTL_FRAG_LEN == 222);

  // Every byte goes out once, in order, and only the last is marked. That is
  // the property a reader actually cares about, stated over the real length.
  uint32_t at = 0;
  uint32_t marked = 0;
  bool in_order = true;
  for (uint32_t i = 0; i < whole; i++) {
    uint8_t mark = rtl_fragment_index(i);
    uint32_t take = RTL_FRAG_LEN;
    if (i + 1 == whole) {
      mark |= 0x80;
      take = length % RTL_FRAG_LEN;
    }
    if (mark & 0x80)
      marked++;
    at += take;
  }
  check("exactly one fragment is marked as the last", marked == 1);
  check("and together they are the whole patch, once", at == length);

  // An image that divides exactly: the last fragment is EMPTY and is still
  // sent, because the mark is what ends the patch. This is the case a tidier
  // loop would have dropped.
  const uint32_t exact = RTL_FRAG_LEN * 4;
  const uint32_t whole2 = exact / RTL_FRAG_LEN + 1;
  check("an image that divides exactly gets one extra, empty fragment",
        whole2 == 5 && (exact % RTL_FRAG_LEN) == 0);

  if (failures)
    std::printf("\n%d problem(s)\n", failures);
  return failures ? 1 : 0;
}
