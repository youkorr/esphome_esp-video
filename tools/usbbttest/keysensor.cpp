// binary_sensor: - platform: usb_bluetooth, driven through the SHIPPED decode
// (feed_avrc_key) and the SHIPPED sensor, with the clock moved by hand.
#define USE_BINARY_SENSOR
#define private public
#define protected public
#define main component_main_unused
#include "usb_bluetooth.cpp"
#define TAG KEY_TAG
#include "binary_sensor/usb_bluetooth_binary_sensor.cpp"
#undef TAG
#include "esp_avrc_api.h"
#include "esp_gap_bt_api.h"
#include "esp_hidh_api.h"
#undef main
#include <cstdio>
#include "linkstubs.h"

using esphome::usb_bluetooth::UsbBluetooth;
using esphome::usb_bluetooth::UsbBluetoothKey;

static int failures = 0;
static void ok(const char *what, bool passed) {
  std::printf("  %s  %s\n", passed ? "ok   " : "ECHEC", what);
  if (!passed)
    failures++;
}

static UsbBluetoothKey *key(UsbBluetooth *bt, uint16_t usage, bool home) {
  auto *k = new UsbBluetoothKey();
  k->set_parent(bt);
  k->add_usage(0x07, usage);
  k->set_home(home);
  k->set_hold(100);
  k->setup();
  return k;
}

int main() {
  std::printf("  binary_sensor keys\n");
  esphome::g_now_ms = 5000;
  auto *bt = new UsbBluetooth();
  auto *up = key(bt, 0x52, false);
  auto *down = key(bt, 0x51, false);
  auto *home = key(bt, 0x4A, true);

  bt->feed_avrc_key(ESP_AVRC_PT_CMD_UP);
  ok("a remote's UP turns key: up on", up->state && !down->state);
  esphome::g_now_ms += 60;
  up->loop();
  ok("and it is still on 60 ms later, so LVGL reads it", up->state);
  esphome::g_now_ms += 50;
  up->loop();
  ok("and off after the hold", !up->state);
  ok("one press is one ON and one OFF", up->published.size() == 2 && up->published[0] && !up->published[1]);

  up->published.clear();
  bt->feed_avrc_key(ESP_AVRC_PT_CMD_UP);
  esphome::g_now_ms += 40;
  bt->feed_avrc_key(ESP_AVRC_PT_CMD_UP);
  ok("a second press inside the hold is a second press",
     up->published.size() == 3 && up->published[0] && !up->published[1] && up->published[2]);
  esphome::g_now_ms += 60;
  up->loop();
  ok("and its hold starts again from the second press", up->state);
  esphome::g_now_ms += 50;
  up->loop();
  ok("then releases", !up->state);

  auto *next = key(bt, 0x51, false);
  next->add_usage(0x07, 0x4F);
  bt->feed_avrc_key(ESP_AVRC_PT_CMD_RIGHT);
  ok("key: [down, right] takes RIGHT", next->state);
  esphome::g_now_ms += 200;
  next->loop();
  down->loop();
  bt->feed_avrc_key(ESP_AVRC_PT_CMD_DOWN);
  ok("and DOWN, beside key: down", next->state && down->state);
  esphome::g_now_ms += 200;
  next->loop();
  down->loop();

  bt->feed_avrc_key(ESP_AVRC_PT_CMD_ROOT_MENU);
  ok("a remote's MENU turns key: home on", home->state && !up->state && !down->state);
  esphome::g_now_ms += 200;
  home->loop();
  bt->feed_avrc_key(ESP_AVRC_PT_CMD_PLAY);
  ok("PLAY is no key at all", !home->state && !up->state && !down->state);

  std::printf("%s\n", failures ? "  ECHEC" : "  tout est bon");
  return failures ? 1 : 0;
}
