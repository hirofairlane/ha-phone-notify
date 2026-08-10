#!/usr/bin/env python3
"""
mqtt_bridge.py — the add-on's main process. Loads the speech model once
at startup (loading it per-call would eat most of the call window, see
docs/ARCHITECTURE.md), then listens on MQTT for call requests and
processes them one at a time (there's only one phone, calls can't
overlap anyway).

Protocol:
  Subscribe  {prefix}/call            → trigger a call, see below
  Publish    {prefix}/result/<id>     → {"id": ..., "result": "<action-name>|TIMEOUT|NO_CALL"}
  Publish    {prefix}/availability    → "online" / "offline" (LWT)

Request payload (published to {prefix}/call):
  {
    "id": "any string you can correlate later",
    "phone_number": "+1234567890",
    "message": "text to speak, in the TTS voice's language",
    "response_actions": {
      "cancel":  {"keywords": ["cancel", "false alarm"]},
      "confirm": {"keywords": ["confirm"]}
    },
    "call_wait_timeout_seconds": 60,   // optional, default 60
    "listen_timeout_seconds": 40       // optional, default 40
  }
"""
import argparse
import json
import os
import queue
import select
import subprocess
import sys
import time
import wave

import paho.mqtt.client as mqtt
import vosk

MIC_SINK = "phone_notify_mic"
SPEAKER_SOURCE = "phone_notify_speaker.monitor"


def wait_for_sco_start(log_file, max_wait):
    start = time.time()
    while time.time() - start < max_wait:
        line = log_file.readline()
        if not line:
            time.sleep(0.1)
            continue
        if "SCO audio running" in line:
            return True
    return False


def sco_has_stopped(log_file):
    line = log_file.readline()
    return bool(line) and "SCO audio: stopping" in line


def synthesize_prompt(text, voice_model_path, out_wav_path):
    subprocess.run(
        ["python3", "-m", "piper", "-m", voice_model_path, "-f", out_wav_path],
        input=text.encode("utf-8"), check=True,
    )


def _tail_new_lines(path, since_pos):
    with open(path, "r") as f:
        f.seek(since_pos)
        data = f.read()
        return data, f.tell()


def ensure_bluetooth_connected(phone_bt_mac, handsfree_log_path, max_wait=25):
    """The HFP link (SLC) can drop after a period of idle time (a known
    issue being tracked, see docs/ARCHITECTURE.md) — reconnect it before
    every call rather than assuming it is already up, otherwise the call
    goes out over the SIM with no Bluetooth audio path attached at all.

    Waits for the FULL HFP stack (SLC established, from HandsFree-Linux's
    own log) rather than just the base Bluetooth ACL link — the base link
    coming up is not sufficient, SLC negotiation takes a few more seconds
    on top of that, and dialing before SLC is ready means the phone has
    already committed to the earpiece as the call's audio route by the
    time SLC finishes (it does not retroactively switch to Bluetooth).
    """
    if not phone_bt_mac:
        return
    result = subprocess.run(
        ["bluetoothctl", "info", phone_bt_mac], capture_output=True, text=True, timeout=5,
    )
    already_connected = "Connected: yes" in result.stdout

    log_pos = os.path.getsize(handsfree_log_path) if os.path.exists(handsfree_log_path) else 0

    if not already_connected:
        print(f"[mqtt_bridge] Bluetooth link to {phone_bt_mac} is down, reconnecting...", flush=True)
        subprocess.run(
            ["bluetoothctl", "connect", phone_bt_mac], capture_output=True, timeout=max_wait,
        )

    print("[mqtt_bridge] waiting for SLC (full HFP handshake)...", flush=True)
    start = time.time()
    while time.time() - start < max_wait:
        new_data, log_pos = _tail_new_lines(handsfree_log_path, log_pos)
        if "SLC established" in new_data:
            print("[mqtt_bridge] SLC established, ready to dial", flush=True)
            return
        time.sleep(0.5)
    print("[mqtt_bridge] WARNING: SLC not confirmed within timeout, dialing anyway", flush=True)


def trigger_call(adb_address, phone_number, phone_bt_mac, handsfree_log_path):
    ensure_bluetooth_connected(phone_bt_mac, handsfree_log_path)
    subprocess.run(["adb", "connect", adb_address], capture_output=True, timeout=10)
    subprocess.run(
        ["adb", "-s", adb_address, "shell", "am", "start",
         "-a", "android.intent.action.CALL", "-d", f"tel:{phone_number}"],
        capture_output=True, timeout=10,
    )


def process_call(request, recognizer_model, sample_rate, handsfree_log_path,
                  voice_model_path, adb_address, phone_bt_mac):
    request_id = request.get("id", "unknown")
    phone_number = request["phone_number"]
    message = request["message"]
    response_actions = request.get("response_actions", {})
    call_wait_timeout = request.get("call_wait_timeout_seconds", 60)
    listen_timeout = request.get("listen_timeout_seconds", 40)

    prompt_wav = f"/tmp/prompt_{request_id}.wav"
    print(f"[mqtt_bridge] synthesizing prompt for request {request_id}", flush=True)
    synthesize_prompt(message, voice_model_path, prompt_wav)

    recognizer = vosk.KaldiRecognizer(recognizer_model, sample_rate)

    print(f"[mqtt_bridge] dialing {phone_number}", flush=True)
    trigger_call(adb_address, phone_number, phone_bt_mac, handsfree_log_path)

    with open(handsfree_log_path, "r") as log_file:
        log_file.seek(0, os.SEEK_END)
        if not wait_for_sco_start(log_file, call_wait_timeout):
            return "NO_CALL"

        subprocess.Popen(["paplay", f"--device={MIC_SINK}", prompt_wav])
        capture_proc = subprocess.Popen(
            ["parec", f"--device={SPEAKER_SOURCE}", "--rate", str(sample_rate),
             "--channels=1", "--format=s16le"],
            stdout=subprocess.PIPE,
        )

        result_action = None
        start = time.time()
        try:
            while time.time() - start < listen_timeout:
                if sco_has_stopped(log_file):
                    break
                ready, _, _ = select.select([capture_proc.stdout], [], [], 0.3)
                if not ready:
                    continue
                data = capture_proc.stdout.read(4000)
                if not data:
                    break
                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "")
                else:
                    text = json.loads(recognizer.PartialResult()).get("partial", "")
                if text:
                    print(f"[mqtt_bridge] heard: '{text}'", flush=True)
                    for action_name, action_cfg in response_actions.items():
                        if any(kw in text for kw in action_cfg["keywords"]):
                            result_action = action_name
                            break
                    if result_action:
                        break
        finally:
            capture_proc.terminate()

    os.remove(prompt_wav)
    return result_action or "TIMEOUT"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vosk-model-path", required=True)
    parser.add_argument("--handsfree-log-path", required=True)
    parser.add_argument("--mqtt-topic-prefix", default="phone_notify")
    parser.add_argument("--voice-model-path", default="/data/tts-voice/voice.onnx")
    parser.add_argument("--adb-address-file", default="/data/adb_address")
    parser.add_argument("--phone-bt-mac", default="")
    parser.add_argument("--mqtt-host", default="core-mosquitto")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-username", default="")
    parser.add_argument("--mqtt-password", default="")
    parser.add_argument("--sample-rate", type=int, default=16000)
    args = parser.parse_args()

    print("[mqtt_bridge] loading speech model (this takes a while, done once)...", flush=True)
    vosk.SetLogLevel(-1)
    model = vosk.Model(args.vosk_model_path)
    print("[mqtt_bridge] speech model ready", flush=True)

    with open(args.adb_address_file) as f:
        adb_address = f.read().strip()

    work_queue: "queue.Queue[dict]" = queue.Queue()
    prefix = args.mqtt_topic_prefix

    def on_connect(client, userdata, flags, reason_code, properties=None):
        print(f"[mqtt_bridge] connected to MQTT ({reason_code})", flush=True)
        client.subscribe(f"{prefix}/call")
        client.publish(f"{prefix}/availability", "online", retain=True)

    def on_message(client, userdata, msg):
        try:
            request = json.loads(msg.payload.decode("utf-8"))
            work_queue.put(request)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[mqtt_bridge] bad request on {msg.topic}: {e}", flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if args.mqtt_username:
        client.username_pw_set(args.mqtt_username, args.mqtt_password)
    client.will_set(f"{prefix}/availability", "offline", retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    client.loop_start()

    print("[mqtt_bridge] ready, waiting for call requests", flush=True)
    while True:
        request = work_queue.get()
        request_id = request.get("id", "unknown")
        try:
            result = process_call(
                request, model, args.sample_rate, args.handsfree_log_path,
                args.voice_model_path, adb_address, args.phone_bt_mac,
            )
        except Exception as e:
            print(f"[mqtt_bridge] request {request_id} failed: {e}", flush=True)
            result = "ERROR"
        client.publish(f"{prefix}/result/{request_id}",
                        json.dumps({"id": request_id, "result": result}))
        print(f"[mqtt_bridge] request {request_id} -> {result}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
