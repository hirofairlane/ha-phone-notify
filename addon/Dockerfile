# WORK IN PROGRESS — not yet built or tested as an actual HA add-on image.
# This is a starting skeleton based on what's known to work as loose
# system packages (see ../docs/ARCHITECTURE.md); it hasn't been
# validated inside the HA Supervisor container environment yet.
ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache \
    python3 \
    py3-pip \
    bluez \
    pipewire \
    pipewire-pulse \
    wireplumber \
    git

RUN pip install --no-cache-dir --break-system-packages vosk

# HandsFree-Linux (Bluetooth HFP Hands-Free role implementation)
RUN git clone --depth 1 https://github.com/PavelTarlev1/handsfree-linux /opt/handsfree-linux

COPY ../scripts /opt/phone-notify/scripts
COPY run.sh /

CMD [ "/run.sh" ]
