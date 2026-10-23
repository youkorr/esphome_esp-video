# Portall for games

A game running on a server, shown on a Portall screen like any of its links.

The Portall add-on's launcher takes a link whose address is `game://<server>`:

```yaml
links:
  - name: Steam
    url: game://192.168.1.3
    icon: steam
```

Tapped, it does not open a web page. The add-on connects to
`portall_game.py` on that server, which sends the server's screen -- Steam,
say -- and plays the screen's touches as its mouse, the remote's or gamepad's
buttons as its keys, and sends its sound to the screen's speaker. Holding the
top-left corner, or pressing the remote's Home, comes back to the launcher;
the game goes on running.

## On Unraid, with steam-headless

1. Install **steam-headless** from *Apps*. Give it the server's Intel graphics
   (`/dev/dri`) as its documentation describes; without any graphics only
   very light games run.
2. Copy `portall-game.sh` into the container's `init.d` folder, in its home
   folder under appdata, and restart the container. The first start fetches
   `portall_game.py` and `udisp_send.py` from this repository and installs
   Pillow and mss into `portall-game/venv`; every start after that fetches
   them again, so an update reaches the server too.
3. Optional, as variables of the container:
   - `PORTALL_GAME_ALLOW` -- the Home Assistant machine's address. **Set it**:
     otherwise anything on the network can press keys on Steam's screen.
   - `PORTALL_GAME_PORT` -- 5700 unless something else has it.
4. In Steam: *Settings > Interface > Start Steam in Big Picture mode*, for the
   Steam Deck's interface on the screen.

Its log is `portall-game/portall-game.log` in the container's home folder.

## Anywhere else

Any Linux machine with an X screen:

```
pip install pillow mss
python3 portall_game.py --display :0 --allow 192.168.1.10
```

with `udisp_send.py` beside it (from `components/portall/`). The sound needs
PulseAudio's `parec`, or GStreamer's `gst-launch-1.0`; without either the game
is shown without it.

## What crosses the wire

Defined once, in `components/portall/udisp_send.py` (`GAME_*`), for both
ends. The add-on asks for a picture; this sends one whole JPEG of the screen,
at the size asked for, only once the screen has changed -- so a still menu
costs the network nothing. The add-on does the rest exactly as it does for a
web page: the turn, the differences, the notifications on top, the pace.

`tools/checkgame.py` checks the whole way with a real X screen, the real
launcher, the add-on's real sender and a fake panel.
