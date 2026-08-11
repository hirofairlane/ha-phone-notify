#!/usr/bin/env bash
set -e

# adb's client identity (~/.android/adbkey*) needs to persist across
# container rebuilds, or the phone has to manually re-authorize a "new"
# computer every single time. Scoped to ADB_HOME (passed explicitly to
# adb invocations only, see mqtt_bridge.py) rather than the global
# $HOME — HandsFree-Linux ALSO reads its config from ~/.config, so
# changing $HOME globally silently resets it to defaults on every boot,
# which is a real regression that happened here once already.
export ADB_HOME=/data/adb_home
mkdir -p "$ADB_HOME"

OPTIONS=/data/options.json
VOSK_MODEL_URL=$(jq -r '.vosk_model_url' "$OPTIONS")
TTS_VOICE_URL=$(jq -r '.tts_voice_url' "$OPTIONS")
TTS_VOICE_CONFIG_URL=$(jq -r '.tts_voice_config_url' "$OPTIONS")
BT_ADAPTER=$(jq -r '.bluetooth_adapter' "$OPTIONS")
MQTT_PREFIX=$(jq -r '.mqtt_topic_prefix' "$OPTIONS")
MQTT_HOST=$(jq -r '.mqtt_host' "$OPTIONS")
MQTT_PORT=$(jq -r '.mqtt_port' "$OPTIONS")
MQTT_USERNAME=$(jq -r '.mqtt_username' "$OPTIONS")
MQTT_PASSWORD=$(jq -r '.mqtt_password' "$OPTIONS")
PHONE_ADB_ADDRESS=$(jq -r '.phone_adb_address' "$OPTIONS")
echo "$PHONE_ADB_ADDRESS" > /data/adb_address
PHONE_BT_MAC=$(jq -r '.phone_bt_mac' "$OPTIONS")

export XDG_RUNTIME_DIR=/tmp/run
mkdir -p "$XDG_RUNTIME_DIR"

echo "[run.sh] starting pipewire..."
pipewire &
for i in $(seq 1 50); do
  [ -S "$XDG_RUNTIME_DIR/pipewire-0" ] && break
  sleep 0.2
done

mkdir -p "$XDG_RUNTIME_DIR/wireplumber/wireplumber.conf.d"
cp /opt/phone-notify/wireplumber-no-hfp-hf.conf "$XDG_RUNTIME_DIR/wireplumber/wireplumber.conf.d/51-no-hfp-hf.conf"
wireplumber &
sleep 2

pipewire-pulse &
for i in $(seq 1 50); do
  [ -S "$XDG_RUNTIME_DIR/pulse/native" ] && break
  sleep 0.2
done

echo "[run.sh] creating virtual audio devices..."
for i in $(seq 1 30); do
  pactl load-module module-null-sink sink_name=phone_notify_mic sink_properties=device.description=PhoneNotifyMic 2>/dev/null && break
  sleep 0.5
done
pactl load-module module-null-sink sink_name=phone_notify_speaker sink_properties=device.description=PhoneNotifySpeaker

VOSK_MODEL_DIR=/data/vosk-model
if [ ! -d "$VOSK_MODEL_DIR" ]; then
  echo "[run.sh] downloading speech model (first run only, this can take a while)..."
  curl -sL -o /tmp/vosk-model.zip "$VOSK_MODEL_URL"
  mkdir -p /tmp/vosk-extract
  unzip -q /tmp/vosk-model.zip -d /tmp/vosk-extract
  mv /tmp/vosk-extract/*/* "$VOSK_MODEL_DIR" 2>/dev/null || mv /tmp/vosk-extract/* "$VOSK_MODEL_DIR"
  rm -rf /tmp/vosk-model.zip /tmp/vosk-extract
fi

TTS_VOICE_DIR=/data/tts-voice
if [ ! -f "$TTS_VOICE_DIR/voice.onnx" ]; then
  echo "[run.sh] downloading TTS voice (first run only)..."
  mkdir -p "$TTS_VOICE_DIR"
  curl -sL -o "$TTS_VOICE_DIR/voice.onnx" "$TTS_VOICE_URL"
  curl -sL -o "$TTS_VOICE_DIR/voice.onnx.json" "$TTS_VOICE_CONFIG_URL"
fi

mkdir -p /data/handsfree-logs /root/.config/handsfree
cat > /root/.config/handsfree/config.toml << EOF
[bluetooth]
adapter = "$BT_ADAPTER"
auto_connect = true
preferred_codec = "cvsd"

[audio]
sco_routing = "pipewire"
call_output_device = "phone_notify_speaker"
call_input_device  = "phone_notify_mic.monitor"
call_volume = 80
ring_volume = 80

[ui]
show_main_window_on_start = false
EOF

echo "[run.sh] starting handsfree-linux..."
cd /opt/handsfree-linux
QT_QPA_PLATFORM=offscreen python3 main.py > /data/handsfree-logs/handsfree.log 2>&1 &
cd /

sleep 3
echo "[run.sh] running startup diagnostics..."
python3 /opt/phone-notify/scripts/doctor.py || echo "[run.sh] some checks failed, see above — starting anyway, fix and restart the add-on"

echo "[run.sh] starting MQTT bridge..."
exec python3 /opt/phone-notify/scripts/mqtt_bridge.py \
  --vosk-model-path "$VOSK_MODEL_DIR" \
  --handsfree-log-path /data/handsfree-logs/handsfree.log \
  --mqtt-topic-prefix "$MQTT_PREFIX" \
  --voice-model-path "$TTS_VOICE_DIR/voice.onnx" \
  --adb-address-file /data/adb_address \
  --phone-bt-mac "$PHONE_BT_MAC" \
  --mqtt-host "$MQTT_HOST" \
  --mqtt-port "$MQTT_PORT" \
  --mqtt-username "$MQTT_USERNAME" \
  --mqtt-password "$MQTT_PASSWORD"
