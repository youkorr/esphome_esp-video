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
ARG BUNDLE=1.25.0
RUN echo "${BUNDLE}" > /app/BUNDLE

RUN pip install --no-cache-dir --upgrade playwright pillow numpy \
    && playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# Fetched rather than copied: a Home Assistant add-on builds with its own folder
# as the context and cannot reach the components directory beside it. Rebuilding
# the add-on is what picks up a newer sender, which is the same thing an update
# would do anyway.
ARG BRANCH=main
ARG RAW=https://raw.githubusercontent.com/youkorr/esphome_esp-video/${BRANCH}/components/usb_display

ADD ${RAW}/udisp_send.py /app/udisp_send.py
ADD ${RAW}/ha_send.py /app/ha_send.py
COPY run.py /app/run.py

WORKDIR /app
# Unbuffered, or the add-on log stays empty until something goes wrong.
CMD ["python", "-u", "run.py"]
