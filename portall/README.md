# Portall

Home Assistant, and any web page, on an ESP32-P4 panel over Wi-Fi. The page is
rendered on the machine that is already on all the time, sent to the panel as
JPEG rectangles, and the panel's touches are replayed back into it. One
instance serves as many panels as you list; each gets its own browser, its own
process and its own prefix in the log, and is restarted on its own if it fails.

## This add-on is one half. The panel is the other

The board is not running Home Assistant and is not running a browser -- that
is the whole idea, and it is why a panel stays fast on hardware that could
never render a dashboard itself. What the board runs is the **`portall`
ESPHome component**, which listens on a TCP port, decodes the rectangles in
the **ESP32-P4's hardware JPEG decoder**, turns them with the **PPA** if the
panel is not mounted upright, and sends contacts back up the same socket.

So the panel needs firmware before this add-on has anything to talk to. In its
ESPHome YAML:

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/youkorr/esphome_esp-video
      ref: main
    components: [portall]

portall:
  display_id: main_screen     # your display: component
  touchscreen_id: my_touch    # your touchscreen: component
  port: 5000                  # what the add-on connects to
  width: 800                  # the panel's own size
  height: 1280
  rotation: 0
```

`port:` is what makes it a network panel: without it the component is
USB-only and this add-on cannot reach it.

Complete, working firmware for three boards is in the `yaml/` folder of the
repository, and each is the whole file rather than a fragment:

| board | file | size |
|---|---|---|
| Guition 10" | `GUITION_ PORTAL.yaml` | 800x1280 |
| M5Stack Tab5 | `tab5-portall-screen.yaml` | 720x1280 |
| Waveshare ESP32-P4-WIFI6-Touch-LCD-7B | `ws-usb-screen.yaml` | 1024x600 |

## What the board keeps doing

`portall` is an **addition** to the panel's ESPHome configuration, not a
replacement for it. Nothing else in that YAML stops working -- its speaker,
microphone, wake word, sensors and `media_player:` are all untouched, and the
panel remains an ordinary ESPHome device to the rest of Home Assistant.

- **Sound.** Whatever the page plays reaches the panel's own speaker, over the
  same socket as the picture -- `speaker_id:` in the `portall:` block. This is
  not particular to one board: it is an ESPHome `speaker:`, so it works on any
  ESP32-P4 panel that has one, and it has been used on several.

  Point `speaker_id:` at a **mixer input of its own** rather than straight at
  the I2S output, the way the examples do: a Home Assistant announcement then
  lands *over* the page instead of fighting it for the bus, and stopping one
  does not stop the other. A resampler in between is not optional -- the page
  arrives at 48 kHz because that is what a browser produces, and a mixer given
  two rates refuses the stream outright.

  Volume is `number: - platform: portall`, and `portall.set_volume` is there
  for a slider that should be remembered across restarts.
- **Touch.** Contacts go back up the same socket and are replayed into the
  browser, so a tap presses what is under the finger and a drag scrolls.
- **Standby.** The backlight and the timer stay the board's own business, in
  its YAML -- the Guition example has a *Veille de l'écran* slider and a
  `screen_timeout` script. Call **`portall.sleep`** beside turning the
  backlight off and the add-on stops rendering and transmitting for a screen
  nobody can see; **`portall.wake`** starts it again. Without that call the
  server keeps drawing a dashboard into the dark.
- **`portall.home`** brings a panel back to its own page from anywhere -- a
  button, an automation, a presence sensor or a voice command, none of which
  has to aim at a corner.

## How the two halves meet

Flash the board, note the address it takes, then list it here -- the `host:`
is that address and `width:`/`height:` must match the `portall:` block exactly.
Nothing else has to agree.

**Calibrate once per panel**, before anything else. There is no way for the
add-on to know which way a controller reports contacts: a GT911 on one board
mirrors both axes, the same part on another swaps them, a GSL3680 mirrors one.
It draws three targets, asks for a tap on each and prints the values to paste:

    python ha_send.py --calibrate --host <the panel's address> --port 5000 \
        --width 800 --height 1280

Everything else -- the options one by one, the launcher, the on-screen
keyboard, sound, the gestures and what it all costs -- is on the
**Documentation** tab at the top of this page.

## YouTube works, in television mode

Point a launcher link at `https://www.youtube.com/tv`, give that link a
smart-television `user_agent` and `quality: 20`, and sign in with a code typed
on your phone -- no password on the panel. Your phone then acts as the remote:
browse there and send the video to the panel. It is the only arrangement that
works, and the Documentation tab has the exact link to copy under **YouTube:
television mode, and the phone as its remote**.

## Coming from the old ESP32-P4 Panel add-on?

Its slug was `usb_display_panel` and this one is `portall`, so Home Assistant
sees a new add-on rather than an update and your options do not come across by
themselves. **Copy them with Edit in YAML before uninstalling anything** -- the
Documentation tab has the four steps under **Moving from the old add-on**.
