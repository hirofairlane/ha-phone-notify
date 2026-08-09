#!/usr/bin/env python3
"""
doctor.py — checks every external dependency this add-on needs and
prints a clear pass/fail per check, with the specific fix for anything
that's broken. Run manually any time something isn't working, or check
the add-on's startup log — run.sh runs this automatically on boot.

Deliberately dependency-free (stdlib only) so it always runs even if
something upstream is broken enough that Python packages didn't
install correctly.
"""
import json
import os
import shutil
import socket
import subprocess
import sys

OPTIONS_PATH = "/data/options.json"
VOSK_MODEL_DIR = "/data/vosk-model"

PASS = "✅"
FAIL = "❌"


def load_options():
    if not os.path.exists(OPTIONS_PATH):
        return {}
    with open(OPTIONS_PATH) as f:
        return json.load(f)


def check(name, ok, fix_hint=""):
    status = PASS if ok else FAIL
    print(f"{status} {name}")
    if not ok and fix_hint:
        print(f"   → {fix_hint}")
    return ok


def check_adb(options):
    address = options.get("phone_adb_address", "")
    if not address:
        return check("adb: phone_adb_address configured", False,
                      "Set phone_adb_address in the add-on config (see docs/SETUP.md step 1).")
    if shutil.which("adb") is None:
        return check("adb: binary available", False,
                      "adb is missing from the image — this is a bug in the add-on, please open an issue.")
    subprocess.run(["adb", "connect", address], capture_output=True, timeout=10)
    result = subprocess.run(["adb", "-s", address, "get-state"],
                             capture_output=True, text=True, timeout=10)
    ok = result.returncode == 0 and "device" in result.stdout
    return check(f"adb: phone reachable at {address}", ok,
                 "Phone unreachable. Common cause: the wireless-debugging port changed after the "
                 "phone reconnected to WiFi or rebooted — re-check it in Developer options and "
                 "update phone_adb_address. Also confirm the phone and this host are on the same network.")


def check_bluetooth_adapter(options):
    adapter = options.get("bluetooth_adapter", "hci0")
    result = subprocess.run(["hciconfig", adapter], capture_output=True, text=True)
    ok = result.returncode == 0 and "UP RUNNING" in result.stdout
    return check(f"bluetooth: adapter {adapter} present and up", ok,
                 f"'{adapter}' not found or down. If this is a Proxmox VM, check the USB passthrough "
                 "(see docs/SETUP.md, 'Proxmox passthrough') and that the VM was rebooted after adding it.")


def check_bluetooth_paired():
    result = subprocess.run(["bluetoothctl", "devices", "Paired"],
                             capture_output=True, text=True, timeout=5)
    has_paired = bool(result.stdout.strip())
    return check("bluetooth: at least one paired device", has_paired,
                 "No paired devices found. Pair the phone's Bluetooth with this host — see "
                 "docs/SETUP.md step 2.")


def check_mqtt(options):
    host = options.get("mqtt_host", "core-mosquitto")
    port = int(options.get("mqtt_port", 1883))
    try:
        with socket.create_connection((host, port), timeout=5):
            return check(f"mqtt: broker reachable at {host}:{port}", True)
    except OSError as e:
        return check(f"mqtt: broker reachable at {host}:{port}", False,
                     f"Connection failed ({e}). Confirm the Mosquitto add-on (or your broker) is "
                     "running and mqtt_host/mqtt_port are correct.")


def check_vosk_model():
    ok = os.path.isdir(VOSK_MODEL_DIR) and os.path.exists(
        os.path.join(VOSK_MODEL_DIR, "conf", "model.conf"))
    return check("speech model: downloaded and extracted", ok,
                 "Model not found. It downloads automatically on first start (can take a few "
                 "minutes depending on your connection) — check the log for download errors, or "
                 "verify vosk_model_url is reachable from this host.")


def main():
    options = load_options()
    results = [
        check_adb(options),
        check_bluetooth_adapter(options),
        check_bluetooth_paired(),
        check_mqtt(options),
        check_vosk_model(),
    ]
    print()
    if all(results):
        print(f"{PASS} All checks passed.")
        return 0
    else:
        failed = len([r for r in results if not r])
        print(f"{FAIL} {failed} check(s) failed — see fixes above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
