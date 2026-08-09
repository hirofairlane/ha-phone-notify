# ha-phone-notify

A Home Assistant `notify.phone_call` platform: place a **real phone
call**, speak a message with TTS, and listen for a spoken response
(e.g. "cancel" / "confirm") to trigger a follow-up automation — using
an old rooted Android phone as the calling device.

```yaml
service: notify.phone_call
data:
  message: "A water leak was detected in the kitchen"
  target:
    - person.alice
  data:
    response_actions:
      cancel:
        keywords: ["cancel", "false alarm"]
      confirm:
        keywords: ["confirm", "call emergency"]
    max_cycles: 3
    call_timeout: 45
```

Any automation can react to the spoken response the same way it
reacts to any other HA event — this isn't tied to any specific alarm
system. Use it for leak detection, medication reminders that need a
"yes I took it", garage-door-left-open confirmations, or a real
security alarm cascade with escalation.

> **Status: early / experimental, but wired end-to-end.** This
> started as a weekend project to solve a very specific problem (see
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full story,
> including two dead ends worth knowing about before you try to build
> on this yourself). The add-on (`addon/`) talks MQTT in, places the
> call over `adb`, plays a TTS prompt, listens for the response, and
> publishes the result back over MQTT — including a startup
> self-diagnostic (`scripts/doctor.py`) that checks every external
> dependency and tells you exactly what to fix if something's wrong.
> **Not yet done:** a real HA `notify` custom_component wrapping the
> MQTT protocol (right now you'd call it by publishing MQTT messages
> yourself — see the protocol in `scripts/mqtt_bridge.py`'s docstring),
> and this hasn't been through a full install-from-the-add-on-store
> test yet. Contributions welcome.

## Why a phone call, and not a push notification?

Push notifications get missed, snoozed, or silenced. A ringing phone
call is much harder to ignore — and unlike most "smart" call/notify
integrations, this one can **listen to what you say back** and act on
it, not just deliver a message one-way.

## How it works

```
HA automation
   │  notify.phone_call
   ▼
Backend (HA add-on, planned) ── MQTT/REST ──► orchestrates the cascade
   │
   │  Bluetooth HFP (phone thinks it's talking to a wireless headset)
   ▼
Rooted Android phone ── real SIM call ──► the person you're calling
   │
   ▲  same Bluetooth link, both directions
   │
Backend: plays the TTS message into the call, listens to the
response with a local speech-recognition model (Vosk), matches it
against your configured `response_actions`, fires an HA event.
```

The key trick: Android **isolates real cellular call audio from
apps** by design — you can't just record or inject into a phone call
from your own app, even with root (see `docs/ARCHITECTURE.md` for the
gory details of *why*, confirmed on real hardware). Making the
computer running this add-on impersonate a **Bluetooth Hands-Free
headset** sidesteps that entirely: Android happily routes call audio
to a real (or fake) Bluetooth headset in both directions, because
that's exactly the API surface that's meant to carry live call audio.

## What's proven (tested on real hardware, real calls)

- Real two-way audio in a live cellular call via a software Bluetooth
  HFP Hands-Free bridge (no app modification, no exotic radio hardware
  — just a Linux box with a normal Bluetooth adapter).
- Local, offline speech recognition (Vosk) on the call's incoming
  audio, distinguishing between two different spoken responses
  ("cancel" vs. "confirm") and branching the automation accordingly.
- A "confirm" response automatically escalating to call other people
  in sequence with a different, informational-only message.

## What's *not* proven / not built yet

- The actual HA `notify` custom_component (config schema, events) —
  today this is standalone scripts, see `scripts/`.
- Packaging as a proper HA Supervisor add-on (Docker image following
  the add-on schema) — `addon/` has a starting skeleton, untested.
- Capturing/injecting call audio **from inside the Android app
  itself**, without an external Bluetooth bridge device — investigated
  in depth, hit a real hardware wall on the one device tested. Full
  write-up in `docs/ARCHITECTURE.md` — worth reading before you try it
  on your own phone, it'll save you a day.

## Hardware you need

- A Home Assistant instance with a Bluetooth adapter available to it
  (built-in on most mini-PC installs; a USB adapter passed through if
  you're on a VM — see `docs/SETUP.md`).
- An old Android phone with a voice-capable SIM. **Root is not
  required** — the working approach only needs `adb` (Wireless
  debugging), a normal non-root developer feature. Doesn't need to be
  anyone's daily driver — a retired phone works great, that's the
  whole point.
- An MQTT broker (the official Mosquitto add-on works).

Full setup instructions, including the exact Wireless-debugging pairing
steps and Proxmox passthrough if that applies to you:
[`docs/SETUP.md`](docs/SETUP.md).

## License

MIT — see `LICENSE`.
