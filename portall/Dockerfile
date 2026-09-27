# The sender needs a browser, and a browser that renders a modern dashboard is
# not small. Playwright's own installer is what pulls the shared libraries
# Chromium needs on Debian; doing it by hand is how you find out about the one
# you missed six months later.
FROM python:3.12-slim

WORKDIR /app

# Everything heavy comes first, and is keyed on a PINNED Playwright.
#
# Docker reuses a layer when its command string has not changed, and an add-on
# update is a rebuild on the household's own box. With the version line first,
# as it used to be, every update threw all of this away and fetched it again:
# Playwright's Chromium, its system libraries, Chrome or the distribution's
# Chromium, PulseAudio -- several hundred megabytes and minutes of unpacking on
# a Raspberry Pi, for an update that had changed a few lines of Python.
#
# It was put first for a real reason: with an unpinned `pip install playwright`
# the command never changed, so a box that had built the image once kept the
# Chromium it happened to download that day for ever, and the on-screen
# keyboard needs Chromium 114. The pin answers that instead. Playwright ships
# ONE Chromium per release, so the version below IS the browser version: move
# it and every layer from here down is fetched fresh, leave it and an update
# reuses them. The second browser below rides on the same key, since a layer
# is only reused when every layer above it was. present_browser() still prints
# the Chromium version at startup and warns below 114.
#
# 1.62.0 is the version every check in tools/ ran against. HAP-python is pinned
# to the 5.0.0 homekit.py was written and tested on, for the same reason.

# HAP-python is what Home Assistant's own HomeKit bridge is built on, so
# the accessory this serves is the same library talking to the same iPhone.
# The [QRCode] extra is not decoration: it prints a scannable code into the
# add-on's log, which turns pairing into pointing a camera at the screen
# instead of typing eight digits found in a wall of text.
#
# Allowed to fail, like the second browser below: a panel that shows a
# dashboard is worth more than one that can be driven from a telephone, and
# run.py steps over a missing homekit module and says so.
RUN pip install --no-cache-dir "HAP-python[QRCode]==5.0.0" ; true

# --no-shell, and it is worth 323 MB of image.
#
# "playwright install chromium" fetches TWO browsers: the full Chromium (597 MB
# measured) and the headless shell (323 MB), and the shell is what a plain
# headless launch() picks by default. The sender no longer wants it: it reaches
# for playwright.chromium.executable_path, which names the FULL build, because
# the shell is a cut-down one and looks like it to any site -- no
# window.chrome, no plugins, its PDF viewer off and its notification permission
# already denied, all of which matter to anybody signing in from a panel.
#
# So the shell was a fallback below a fallback: a system browser is preferred
# above both, and the full Chromium above the shell. Dropping it costs nothing
# that is reachable and takes a third of a gigabyte off an image that often
# lands on a Raspberry Pi's SD card.
#
# Written to degrade rather than fail: an older Playwright that has never heard
# of --no-shell installs both, exactly as before.
RUN pip install --no-cache-dir playwright==1.62.0 pillow numpy \
    && (playwright install --with-deps chromium --no-shell \
        || playwright install --with-deps chromium) \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# A second browser, for one reason: the Chromium Playwright downloads is built
# without the proprietary codecs. Measured on 141.0.7390.37 -- H.264 no, AAC
# no, HLS no, and requestMediaKeySystemAccess does not even exist, so no DRM of
# any kind. A dashboard never notices. YouTube does: its player chooses its
# formats by asking the browser what it can decode, and one it cannot decode
# ends as "un probleme est survenu" a few seconds in.
#
# Chrome carries the codecs and Widevine both, but Google publishes no arm64
# Linux build, so a Home Assistant box on a Pi falls to the distribution's
# Chromium -- which Debian builds with ffmpeg_branding=Chrome and which does
# have them. The sender prefers whichever of these is installed and says in its
# log which one it started and what it can decode.
#
# Nothing here is allowed to fail the build. A panel that shows a dashboard is
# worth more than a panel that plays video, so if neither browser can be had,
# the image is still the working image it was before -- the sender falls back
# to Playwright's own, exactly as it did.
RUN (playwright install --with-deps chrome \
     || (apt-get update \
         && apt-get install -y --no-install-recommends chromium) \
     || echo "no codec-capable browser here; Playwright's own will be used") \
    ; apt-get clean \
    ; rm -rf /var/lib/apt/lists/* /root/.cache/pip \
    ; true

# A sound card that goes nowhere, so the browser has somewhere to play.
#
# Chromium's debugging protocol does not offer audio, so the only way to hear
# what a page is playing is to give the browser an output and read it back. A
# PulseAudio null sink is that output: no hardware, no permissions, and it
# works for any page. Without this the panel shows a video and stays silent.
#
# Not allowed to fail the build either -- a panel that shows a dashboard is
# worth more than one that has sound, and the sender says plainly when there
# is no PulseAudio to capture through.
RUN (apt-get update \
     && apt-get install -y --no-install-recommends pulseaudio pulseaudio-utils \
     || echo "no PulseAudio here; panels will be silent") \
    ; apt-get clean \
    ; rm -rf /var/lib/apt/lists/* \
    ; true

# Bump this with the version in config.yaml. It sits AFTER the browsers and
# BEFORE the senders: an update refetches the two senders below and copies the
# add-on's own files, and reuses everything above -- see the note on the pin.
ARG BUNDLE=4.30.1
RUN echo "${BUNDLE}" > /app/BUNDLE

# Fetched rather than copied: a Home Assistant add-on builds with its own folder
# as the context and cannot reach the components directory beside it. Rebuilding
# the add-on is what picks up a newer sender, which is the same thing an update
# would do anyway.
ARG BRANCH=main
ARG RAW=https://raw.githubusercontent.com/youkorr/esphome_esp-video/${BRANCH}/components/portall

ADD ${RAW}/udisp_send.py /app/udisp_send.py
ADD ${RAW}/ha_send.py /app/ha_send.py
# Both of these, and it is worth saying why the second is easy to forget: it
# was. launcher.py was added beside run.py, imported at the top of it, and
# never copied -- so the image built, started, and died on
# "ModuleNotFoundError: No module named 'launcher'" before a single panel was
# served. Anything run.py imports has to be listed here.
COPY run.py /app/run.py
COPY launcher.py /app/launcher.py
COPY logos.py /app/logos.py
# The image has to be told about every file, which is not the same as the
# repository having one -- launcher.py was written, imported and tested
# and the add-on died on ModuleNotFoundError before serving a panel.
COPY homekit.py /app/homekit.py
COPY voice.py /app/voice.py
COPY voicelinks.py /app/voicelinks.py

WORKDIR /app
# Unbuffered, or the add-on log stays empty until something goes wrong.
CMD ["python", "-u", "run.py"]
