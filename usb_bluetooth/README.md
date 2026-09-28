# usb_bluetooth — Bluetooth Classic on an ESP32-P4, from a USB dongle

An ESPHome external component. It turns a USB Bluetooth dongle in an
ESP32-P4's USB host socket into a **Bluetooth Classic host**, which the
ESP32-C6 beside the P4 on these boards cannot be (the C6 is Bluetooth LE
only). It needs nothing but ESPHome: no add-on, no other component, and it
works with ESPHome's own `lvgl:`.

- **A2DP source**: `speaker: - platform: usb_bluetooth` is an ordinary ESPHome
  speaker. A media player, a voice assistant or a mixer plays through a
  Bluetooth speaker, headphones or a car receiver.
- **HID host**: a remote, a keyboard or a gamepad. Its buttons become
  `binary_sensor: - platform: usb_bluetooth` entities with a `key:`, which is
  exactly what `lvgl: keypads:` takes.
- **AVRCP**: the speaker's own buttons (play, pause, volume) arrive as
  `on_media_key` / `on_media_volume`, and its arrows and OK as keys.
- Pairing and forgetting are actions; paired devices are remembered and
  reconnected **by address** after a restart, with no scan.

This folder is a stand-alone copy of `portall_bt` from this repository, with
everything that reached into the `portall` screen component taken out.

## Install

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/youkorr/esphome_esp-video
      ref: main
      path: usb_bluetooth/components
    components: [usb_bluetooth]

usb_bluetooth:
  id: dongle
  controller: high_speed     # the OTG peripheral the USB-A socket is wired to
  host_stack: bluedroid
  audio: true                # A2DP speaker
  hid: true                  # remotes, keyboards, gamepads
```

A complete M5Stack Tab5 configuration, with an LVGL page driven by a remote
and a media player sounding through Bluetooth, is in
[`example/tab5-lvgl-bluetooth.yaml`](example/tab5-lvgl-bluetooth.yaml).

## Options

| option | default | |
|---|---|---|
| `controller` | `high_speed` | `high_speed` or `full_speed`: the P4 OTG peripheral the dongle's socket is on. |
| `host_stack` | `none` | `bluedroid` brings ESP-IDF's Classic host into the build. `none` only enumerates the dongle and runs an inquiry. |
| `audio` | `false` | A2DP source and AVRCP. |
| `hid` | `false` | HID host, up to four input devices at once. |
| `device_name` | the node name | What the board is called on the device being paired. |
| `pair_seconds` | `10s` | Length of a pairing scan. |
| `inquiry_seconds` | `10s` | A scan at boot that logs what it hears; `0s` turns it off. |
| `firmware` | built in | The Realtek RTL8761BU patch is carried and uploaded when such a dongle is found. `none` saves ~44 KB of flash on a board that will only see a Broadcom. |
| `show_reports` | `false` | Log every HID report and the whole report descriptor. |
| `test_tone` | `0` | A sine in Hz played when nothing else is, to prove the speaker link. |

Actions: `usb_bluetooth.pair`, `usb_bluetooth.forget`,
`usb_bluetooth.forget_speaker`, `usb_bluetooth.forget_input`.
Platforms: `speaker`, `binary_sensor` (`key:`), `switch` (Bluetooth on/off),
`text_sensor` (`speaker:` / `input:`, which device and whether it is
connected).

Up to four remotes and controllers are remembered, each in a slot. For a
screen that lists them one row each, `input:` takes `slot: 1` to `4` and
reports that slot alone (`none` when it is empty), and
`usb_bluetooth.forget_input` takes the same `slot:` to forget that one device
and leave the others. Without `slot:` both work on all of them. The example's
Settings page is built this way: a row per device with its own Forget.

## Keys, and LVGL

Every device is turned into one vocabulary in `keys.cpp`:

| `key:` | remote (AVRCP) | keyboard | gamepad |
|---|---|---|---|
| `up` `down` `left` `right` | arrows | arrows | hat or D-pad, left stick |
| `enter` | Select / OK | Enter | A |
| `esc` | Exit / Back | Esc | B, Back |
| `home` | Menu | Home | Home |
| `backspace` `del` `next` `end` `0`–`9` | — | the same keys | — |

`key:` takes a list, and a sensor stays ON for `hold:` (100 ms) because LVGL
reads a keypad on its own timer, every 30 ms, and would miss a shorter press.

**On an LVGL keypad only `next` and `prev` move the focus**; the arrows are
handed to the focused widget (a slider, a roller). So to walk a page of
buttons with a remote:

```yaml
binary_sensor:
  - platform: usb_bluetooth
    id: bt_next
    key: [down, right]
  - platform: usb_bluetooth
    id: bt_prev
    key: [up, left]
  - platform: usb_bluetooth
    id: bt_enter
    key: enter

lvgl:
  keypads:
    - next: bt_next
      prev: bt_prev
      enter: bt_enter
```

Gamepad buttons are read from the device's own HID report descriptor, not
from a table of devices, so a controller nobody here owns is usually decoded
correctly. One that is not says so in the log, with the report's shape.

## Dongles and devices proved

On M5Stack Tab5, Guition 10" and Waveshare 7B boards:

- **Broadcom BCM20702A1** (Bluetooth 4.0) — works as it comes.
- **TP-Link UB500, Realtek RTL8761BU** — needs its firmware, which is built in
  (`firmware/`, Realtek's own files, redistributed under their licence).
- An UGREEN car receiver (A2DP + AVRCP) and an NVIDIA Shield controller (HID).

## What to know

- **A pairing scan takes the Wi-Fi down for as long as it runs.** An inquiry
  sweeps the whole 2.4 GHz band at full power, beside the C6's antenna. The
  board scans only when asked to pair; reconnection is by address.
- **ESPHome's `usb_host` cannot be used at the same time**, nor `usb_uart`,
  which pulls it in. This component runs its own USB host stack (CherryUSB)
  and `usb_host` installs ESP-IDF's on the high-speed controller, so the two
  would own the same hardware; the build refuses it, the way
  esphome/esphome#16944 refuses a USB camera beside `usb_host`.
- **The C6's own Bluetooth cannot be used at the same time.** `esp32_ble`
  (`bluetooth_proxy`, `esp32_ble_tracker`, ...) attaches the C6 to the same
  Bluedroid; the build refuses both.
- **CherryUSB is patched at build time.** Its ESP port fixes
  `CONFIG_USBHOST_MAX_INTF_ALTSETTINGS` at 2 and a dongle's SCO interface has
  six, so nothing would enumerate. `components/usb_bluetooth/cherryusb_patch`
  raises it in the downloaded copy. SCO (a headset's microphone) is not
  carried.
- The C++ carries long comments from the project it came from; they record
  why each part is the way it is, measured on real panels.

## Status of this copy

The code is `portall_bt`'s, which has run on the boards above. This copy is
renamed, with the `portall` link removed and the `binary_sensor` platform
added. It has been checked with `esphome config` and the codegen on ESPHome
2026.10.0-dev, and its C++ with the repository's g++ harness against
stand-in headers: `portall_bt`'s whole test suite run against this copy,
plus a test of the keys, all passing (the tests live in the main repository's
`tools/`, not in this folder). It has
**not** been compiled by ESP-IDF or flashed as it stands.
