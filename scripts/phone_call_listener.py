#!/usr/bin/env python3
"""
phone_call_listener.py — waits for the Bluetooth HFP bridge to report a
live SCO audio link (reading the bridge's own log file, since guessing
a fixed delay is unreliable — call setup time varies a lot), plays a
prompt into the call, then listens to the call's downlink audio for a
spoken response matching one of the configured `response_actions`.

Prints one line on completion:
  RESULT: <action-name>   (a configured response was recognized)
  RESULT: TIMEOUT         (call connected, nobody said a matching phrase)
  RESULT: NO_CALL         (the call never connected within the wait window)

Intended to be invoked once per call attempt, with the call itself
triggered separately (e.g. via `adb shell am start -a
android.intent.action.CALL -d tel:<number>`, or however your dialer
app places calls). See ../addon/ for how this is meant to be wired
into a Home Assistant add-on.
"""
import argparse
import json
import os
import select
import subprocess
import sys
import time
import wave

import vosk


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--prompt-wav", required=True,
                         help="WAV file to play into the call once connected")
    parser.add_argument("--capture-wav", default=None,
                         help="Optional: save the raw captured downlink audio here for debugging")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)

    sample_rate = cfg.get("sample_rate", 16000)

    print("[phone_call_listener] loading speech model...", flush=True)
    vosk.SetLogLevel(-1)
    model = vosk.Model(cfg["vosk_model_path"])
    recognizer = vosk.KaldiRecognizer(model, sample_rate)
    print("[phone_call_listener] model ready", flush=True)

    with open(cfg["handsfree_log_path"], "r") as log_file:
        log_file.seek(0, os.SEEK_END)
        wait_timeout = cfg.get("call_wait_timeout_seconds", 60)
        print(f"[phone_call_listener] waiting for the call to connect (up to {wait_timeout}s)...", flush=True)
        if not wait_for_sco_start(log_file, wait_timeout):
            print("RESULT: NO_CALL", flush=True)
            return 1

        print("[phone_call_listener] call connected, playing prompt and listening", flush=True)
        subprocess.Popen(["paplay", f"--device={cfg['uplink_sink']}", args.prompt_wav])

        capture_proc = subprocess.Popen(
            ["parec", f"--device={cfg['downlink_source']}", "--rate", str(sample_rate),
             "--channels=1", "--format=s16le"],
            stdout=subprocess.PIPE,
        )

        wav_out = None
        if args.capture_wav:
            wav_out = wave.open(args.capture_wav, "wb")
            wav_out.setnchannels(1)
            wav_out.setsampwidth(2)
            wav_out.setframerate(sample_rate)

        listen_timeout = cfg.get("listen_timeout_seconds", 40)
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
                if wav_out:
                    wav_out.writeframes(data)

                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "")
                else:
                    text = json.loads(recognizer.PartialResult()).get("partial", "")

                if text:
                    print(f"[phone_call_listener] heard: '{text}'", flush=True)
                    for action_name, action_cfg in cfg["response_actions"].items():
                        if any(kw in text for kw in action_cfg["keywords"]):
                            result_action = action_name
                            break
                    if result_action:
                        break
        finally:
            capture_proc.terminate()
            if wav_out:
                wav_out.close()

    print(f"RESULT: {result_action or 'TIMEOUT'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
