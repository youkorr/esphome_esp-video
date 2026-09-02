# The sender needs a browser, and a browser that renders a modern dashboard is
# not small. Playwright's own installer is what pulls the shared libraries
# Chromium needs on Debian; doing it by hand is how you find out about the one
# you missed six months later.
FROM python:3.12-slim

WORKDIR /app

# Bump this with the version in config.yaml, and note where it sits: FIRST,
# ahead of everything that downloads anything.
#
# Docker caches a layer on its command string alone, and every string below is
# fixed -- the pip install never changes, and the ADD URLs never change. So a
# box that built this image once went on reusing all of it however many times
# the add-on was updated: the same senders, and the same Chromium it happened
# to download that day. That second one is not cosmetic. The on-screen keyboard
# is drawn in the browser's top layer through the popover API, which arrived in
# Chromium 114; on anything older it falls back to being drawn in the page,
# where Home Assistant's own shell covers it -- invisible, while the keys go on
# working, which is a fault that takes a long time to recognise.
#
# Writing the version out here invalidates every layer after it, so an add-on
# update really does fetch a current Playwright, a current Chromium and current
# senders. It costs the browser download on each update, which is the price of
# not running a year-old browser without knowing it.
ARG BUNDLE=2.3.1
RUN echo "${BUNDLE}" > /app/BUNDLE

RUN pip install --no-cache-dir --upgrade playwright pillow numpy \
    && playwright install --with-deps chromium \
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

WORKDIR /app
# Unbuffered, or the add-on log stays empty until something goes wrong.
CMD ["python", "-u", "run.py"]
