#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2024-2026 youkorr
# Part of Portall: https://github.com/youkorr/esphome_esp-video
#
# Starts Portall's game streamer beside Steam in steam-headless.
#
# Put this file in steam-headless's ~/init.d folder -- on Unraid that is the
# container's home folder in appdata -- and it runs at every start of the
# container: it fetches the streamer the first time, keeps it current,
# installs what it needs into a folder of its own, and starts it in the
# background on the screen Steam is on. It does not hold the container's start
# up: the streamer waits for the screen by itself.
#
# Settings, all optional, as variables of the container (Unraid: "Add another
# Path, Port, Variable"):
#   PORTALL_GAME_ALLOW   the Home Assistant machine's address, so nothing else
#                        on the network can press keys on Steam's screen
#   PORTALL_GAME_PORT    5700 unless something else has it
#   PORTALL_BRANCH       which branch of the repository to fetch (main)

HOME_DIR="${USER_HOME:-/home/default}"
DIR="$HOME_DIR/portall-game"
BRANCH="${PORTALL_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/youkorr/esphome_esp-video/$BRANCH"
LOG="$DIR/portall-game.log"

mkdir -p "$DIR"
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# The two files, fetched each start so an update reaches the server too; a
# failed fetch keeps the copy already there.
for file in components/portall/udisp_send.py game/portall_game.py; do
    name="$(basename "$file")"
    if curl -fsSL --max-time 30 "$RAW/$file" -o "$DIR/$name.new"; then
        mv "$DIR/$name.new" "$DIR/$name"
    else
        rm -f "$DIR/$name.new"
        say "could not fetch $name, keeping the copy here"
    fi
done
if [ ! -f "$DIR/portall_game.py" ]; then
    say "no streamer to start: the first fetch needs the internet"
    exit 0
fi

# Pillow and mss in a folder of their own, beside the Python the container
# already has (with its numpy). Once; checked at every start.
if [ ! -x "$DIR/venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$DIR/venv" >> "$LOG" 2>&1
fi
if ! "$DIR/venv/bin/python" -c "import PIL, mss" 2> /dev/null; then
    "$DIR/venv/bin/pip" install --quiet pillow mss >> "$LOG" 2>&1 \
        || say "could not install pillow and mss"
fi

# The container's own account, which is the one that owns the screen.
if [ "$(id -u)" = "0" ] && id default > /dev/null 2>&1; then
    chown -R default "$DIR" 2> /dev/null
    RUN=(sudo -u default --preserve-env=DISPLAY,PULSE_SERVER,XDG_RUNTIME_DIR)
else
    RUN=()
fi

export DISPLAY="${DISPLAY:-:55}"
if [ -z "$PULSE_SERVER" ] && [ -S /tmp/pulse/pulse-socket ]; then
    export PULSE_SERVER=unix:/tmp/pulse/pulse-socket
fi

ARGS=(--port "${PORTALL_GAME_PORT:-5700}")
if [ -n "$PORTALL_GAME_ALLOW" ]; then
    ARGS+=(--allow "$PORTALL_GAME_ALLOW")
fi

say "starting the streamer on $DISPLAY"
setsid nohup "${RUN[@]}" "$DIR/venv/bin/python" -u "$DIR/portall_game.py" \
    "${ARGS[@]}" >> "$LOG" 2>&1 < /dev/null &
exit 0
