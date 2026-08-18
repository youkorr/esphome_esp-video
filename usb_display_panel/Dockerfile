# The sender needs a browser, and a browser that renders a modern dashboard is
# not small. Playwright's own installer is what pulls the shared libraries
# Chromium needs on Debian; doing it by hand is how you find out about the one
# you missed six months later.
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir playwright pillow numpy \
    && playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# Fetched rather than copied: a Home Assistant add-on builds with its own folder
# as the context and cannot reach the components directory beside it. Rebuilding
# the add-on is what picks up a newer sender, which is the same thing an update
# would do anyway.
ARG BRANCH=main
ARG RAW=https://raw.githubusercontent.com/youkorr/esphome_esp-video/${BRANCH}/components/usb_display

# Bump this with the version in config.yaml.
#
# ADD from a URL is cached on the URL alone, and this URL never changes: an
# add-on rebuilt after an update would happily reuse the layer holding the
# senders it fetched weeks ago. Nothing fails -- it simply runs the old code,
# which is a great deal harder to notice than a build error. Writing the
# version into the image first invalidates everything after it, so a new
# version really does fetch new senders.
ARG BUNDLE=1.9.1
RUN echo "${BUNDLE}" > /app/BUNDLE

ADD ${RAW}/udisp_send.py /app/udisp_send.py
ADD ${RAW}/ha_send.py /app/ha_send.py
ADD ${RAW}/keyboard.js /app/keyboard.js
COPY run.py /app/run.py

WORKDIR /app
# Unbuffered, or the add-on log stays empty until something goes wrong.
CMD ["python", "-u", "run.py"]
