# Setup guide

## What you need

| Requirement | Notes |
|---|---|
| A spare Android phone, voice-capable SIM | Doesn't need a data plan, just calling. Any old phone works — this is a great use for a retired one. |
| Android 11+ on that phone | For Wireless debugging (adb over WiFi). Older Android works too but needs a USB cable for the initial pairing each reboot — see below. |
| **Root is NOT required** | Only the abandoned in-app-capture experiment (see `docs/ARCHITECTURE.md`) needed root. The working approach only needs `adb`, which is a normal, unprivileged developer feature. |
| A Bluetooth adapter on whatever runs this add-on | Most NUC/mini-PC Home Assistant installs already have one built in. If you're on Proxmox like the original prototype, you'll need to pass a USB Bluetooth adapter through to the HAOS VM — see "Proxmox passthrough" below. |
| ~2GB free RAM | For the large (accurate) speech model. A smaller, less accurate model is available if your hardware is tight — see "Choosing a speech model" below. |
| An MQTT broker | The official Mosquitto add-on works — one click in the HA add-on store if you don't have one already. |

## Step 1 — enable Wireless debugging on the phone

1. Settings → About phone → tap "Build number" 7 times to unlock Developer
   options.
2. Settings → System → Developer options → enable **Wireless debugging**.
3. Tap "Wireless debugging" → "Pair device with pairing code", note the
   IP:port and 6-digit code shown.
4. From the machine running this add-on (or any machine on the same
   network, once — pairing only needs to happen once):
   ```
   adb pair <ip>:<pairing-port>
   # enter the 6-digit code when prompted
   ```
5. Wireless debugging shows a second IP:port (not the pairing one) — that's
   the one that goes in the add-on's `phone_adb_address` option, e.g.
   `192.168.1.50:41231`. **This port can change if the phone reboots or
   WiFi reconnects** — if calls stop working, check this first.

> Older Android (< 11): no Wireless debugging toggle. Enable plain USB
> debugging, connect the phone by cable once, run `adb tcpip 5555`, then
> disconnect the cable — the phone stays reachable over WiFi on port 5555
> until its next reboot, at which point you'll need to reconnect the
> cable and repeat `adb tcpip 5555`.

## Step 2 — pair the phone's Bluetooth to the add-on host

This add-on makes the machine it runs on pretend to be a Bluetooth
Hands-Free headset. Pair them like you would any Bluetooth headset:

1. On the phone: Settings → Bluetooth → pair with the device (it'll show
   up with whatever hostname the add-on's machine has).
2. Accept the pairing prompt on both sides.

## Step 3 — configure and start the add-on

Set `phone_adb_address` (from step 1), your MQTT broker details, and
start the add-on. Check the add-on log — it runs a startup diagnostic
(`scripts/doctor.py`) and will tell you clearly what's missing if
anything is.

## Choosing a speech model

The default `vosk_model_url` points at the large, accurate Spanish
model (~1.4GB download, ~2GB RAM to load, ~1-2 minutes to load on
modest hardware). If your speech recognition seems to mishear
everything, this is very likely already the right model — the small
model genuinely isn't accurate enough on real phone-call audio quality
(narrowband, compressed) to be usable; this was tested and confirmed,
see `docs/ARCHITECTURE.md`.

If ~2GB RAM genuinely isn't available, browse
[available models](https://alphacephei.com/vosk/models) for your
language and set `vosk_model_url` to a smaller one, accepting worse
accuracy — or consider disabling voice recognition and using DTMF
(touch-tone) responses instead once that's implemented (see the open
issues in the repo).

## Proxmox passthrough

If (like the original prototype) you run HAOS as a Proxmox VM without
its own Bluetooth hardware, pass a USB Bluetooth adapter through to
that VM:

```
qm set <vmid> -usb0 host=<vendor>:<product>   # e.g. host=8087:0026
qm reboot <vmid>
```

Find `<vendor>:<product>` with `lsusb` on the Proxmox host. This
requires a reboot of the VM — plan it for a moment that won't disrupt
anyone relying on Home Assistant.

## Troubleshooting

Run the diagnostic manually from the add-on's log tab, or via the
add-on's shell:

```
python3 /opt/phone-notify/scripts/doctor.py
```

It checks, in order: adb reachability to the phone, Bluetooth adapter
presence, whether the phone is paired, MQTT broker connectivity, and
whether the speech model is downloaded — printing a clear ✅/❌ per
check with the specific fix for anything that fails.
