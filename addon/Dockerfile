# Deliberately NOT based on the standard $BUILD_FROM Alpine/musl HA base
# images: Vosk's PyPI wheels are manylinux (glibc) only, no musllinux
# build exists, so this needs a glibc base. Debian slim keeps the image
# reasonably small while giving us real apt packages for PipeWire/BlueZ.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    pkg-config \
    libdbus-1-dev \
    libglib2.0-dev \
    pipewire \
    pipewire-pulse \
    wireplumber \
    bluez \
    dbus \
    git \
    curl \
    unzip \
    jq \
    adb \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir vosk paho-mqtt piper-tts

# HandsFree-Linux: Bluetooth HFP Hands-Free role implementation
RUN git clone --depth 1 https://github.com/PavelTarlev1/handsfree-linux /opt/handsfree-linux \
    && pip install --no-cache-dir -r /opt/handsfree-linux/requirements.txt

COPY scripts /opt/phone-notify/scripts
COPY run.sh wireplumber-no-hfp-hf.conf /opt/phone-notify/
RUN chmod +x /opt/phone-notify/run.sh

CMD [ "/opt/phone-notify/run.sh" ]
