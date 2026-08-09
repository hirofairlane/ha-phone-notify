# Architecture & the story of how we got here

## The problem

Home Assistant can already send you a push notification, a Telegram
message, or ring a chime. What it can't easily do is call your phone,
say something out loud, and understand what you say back. That's the
gap this project fills.

The obvious approach — write an Android app that places a call and
uses the microphone/speaker APIs during it — runs straight into a wall
that isn't well documented anywhere in one place, which is the main
reason this write-up exists.

## Dead end #1: audio inside a real cellular call is not app-reachable

A normal cellular (circuit-switched, "CS") voice call on Android does
**not** route its audio through the app-level audio framework
(AudioFlinger). It's handled directly by the baseband/modem hardware.
Concretely:

- `AudioManager.MODE_IN_CALL` (the mode Android uses for a real SIM
  call) talks to the modem via a vendor-specific hardware path (QMI on
  Qualcomm chipsets) that bypasses `AudioFlinger`/ALSA entirely.
- `AudioManager.MODE_IN_COMMUNICATION` (the mode VoIP apps use) *does*
  go through `AudioFlinger` → ALSA → the real speaker/mic, which is
  why a VoIP call is fully accessible to app code and a cellular one
  mostly isn't.

We proved this the hard way on a real device (a Redmi Note 11,
Qualcomm Snapdragon 662-class chipset), root and all:

1. First tried `Intent.ACTION_CALL` from a background service —
   worked, but Android's Background Activity Launch restrictions
   silently blocked it in some contexts. Fixed with
   `TelecomManager.placeCall()`. Audio was still one-way (nothing
   reached the other end) — expected, this alone doesn't touch the
   audio path at all.
2. Tried the legacy `AudioSource.VOICE_DOWNLINK` /
   `AudioSource.VOICE_CALL` capture APIs, which exist precisely for
   this ("capture what the other side of the call is saying"). Gated
   behind `android.permission.CAPTURE_AUDIO_OUTPUT`, a
   `signature|privileged` permission normal apps can't hold.
   - Discovered it's not even grantable via `pm grant` with root — the
     framework throws `SecurityException: Permission ... is managed by
     role`.
   - The one publicly-documented workaround (an LSPosed/Xposed module
     that unlocks Google Dialer's call-recording feature) turned out,
     on reading its source, to only flip *business-logic* checks
     (country/geofence restrictions) inside an app that **already**
     holds the permission natively as a system app. It grants nothing
     to a third-party app — a real dead end if you were hoping to
     reuse it.
   - The permission **is** obtainable by installing your own app as a
     systemless priv-app via Magisk (a `privapp-permissions-*.xml`
     allowlist entry + `/system/priv-app/`) — this part worked, and
     is genuinely useful if you need any `signature|privileged`
     Android permission on a rooted device you own.
   - Also needed: the calling app has to be a foreground service
     declaring the `microphone` `foregroundServiceType` (Android
     silently denies mic-class access to background-launched
     foreground services otherwise — an easy thing to miss, the
     failure mode looks identical to a permission problem but isn't),
     and `RECORD_AUDIO` has to be **runtime-granted**, not just
     declared in the manifest (this one *is* a normal grantable
     permission, unlike `CAPTURE_AUDIO_OUTPUT`).
3. With every permission finally in place, `AudioRecord` initializes
   successfully against `VOICE_DOWNLINK` — and returns **silence**.
   Confirmed with a raw WAV capture during a real live call: ~120,000
   samples, max absolute amplitude 0. The vendor audio HAL on this
   chipset accepts opening the recording session but never actually
   feeds it real samples from the call.

That last point is the real finding: this is a **hardware/vendor HAL**
limitation, not a permissions puzzle you can code your way out of. A
different chipset or a Pixel-class device with Google's own audio HAL
might behave differently — we haven't tested that, and you shouldn't
assume it'll work without checking on your specific device first.

## The fix: don't fight the modem, become a Bluetooth headset instead

Real call audio (both directions) *is* meant to be freely routed to a
Bluetooth Hands-Free headset — that's the entire point of the HFP
(Hands-Free Profile) Bluetooth spec, and Android fully supports it for
real cellular calls, unlike the app-level APIs above. So: make a
normal Linux box impersonate a Bluetooth HFP headset (the **HF**,
Hands-Free role; the phone is the **AG**, Audio Gateway), and the
phone will route call audio to it exactly as it would to a real
Bluetooth earpiece or car kit — because as far as the phone is
concerned, that's what it's talking to.

Implementation: [HandsFree-Linux](https://github.com/PavelTarlev1/handsfree-linux)
(BlueZ D-Bus + PipeWire), configured so PipeWire itself doesn't also
try to claim the `hfp_hf` role (they'll conflict —
`monitor.bluez.properties { bluez5.roles = [...] }` in a WirePlumber
config drop-in, excluding `hfp_hf`), with two PipeWire virtual
null-sinks acting as the injectable "microphone" (what gets sent to
the far end) and the capturable "speaker" (what the far end says).

Gotchas worth knowing before you hit them yourself:

- **`bluetoothctl` scripted pairing**: keep the agent registered for
  ~15-20s after confirming a passkey, or the bond silently fails right
  after apparently succeeding (visible with `btmon` HCI capture, not
  from `bluetoothctl`'s own output).
- **PipeWire's virtual null-sinks are not persistent.** They only
  exist in the running PipeWire daemon's memory — a PipeWire restart
  (including one triggered by OOM, which is a real risk if you're also
  running a large speech model on constrained hardware) silently wipes
  them. Anything still pointed at the now-missing sink name falls back
  to the **real hardware** speaker/mic without erroring — which,
  combined with a mic-and-speaker-both-open call, will make you think
  you found a bug in your keyword detection when actually you're
  hearing your own synthesized prompt bounce off the room. Recreate
  them on daemon start, don't assume they survive a restart.
- **Timing**: don't guess a fixed `sleep` for "the call should be
  connected by now" — tail the bridge's own log for the literal string
  it logs when the SCO audio link actually comes up, and gate
  everything on that instead. Call setup time is genuinely variable
  (10-30+ seconds observed).
- **Preload the speech model before you start waiting for the call**,
  not after it connects — a good offline Spanish model took ~100s to
  load on modest hardware, easily eating the whole usable window of a
  short test call if you load it reactively.
- **Model size matters more than you'd think.** A small (~40MB) Vosk
  model transcribed real narrowband (8kHz CVSD) call audio as
  garbage — heard speech, wrong words. Switching to the ~1.4GB model
  fixed it completely. If your recognition "isn't working" on a live
  call, try the bigger model before assuming your audio pipeline is
  broken.

## Bill of materials for the working prototype

| Piece | Role |
|---|---|
| Rooted Android phone, real SIM | Places the actual call |
| Linux box, Bluetooth adapter | Impersonates an HFP headset |
| [HandsFree-Linux](https://github.com/PavelTarlev1/handsfree-linux) | BT HFP-HF role implementation |
| PipeWire + WirePlumber | Audio routing, virtual devices |
| [Vosk](https://alphacephei.com/vosk/) | Offline speech recognition |
| [Piper](https://github.com/rhasspy/piper) | Offline TTS for the spoken prompt |

## Open questions / where this needs help

- Proper HA Supervisor add-on packaging (Docker image, `config.yaml`)
  hasn't been built yet — see `addon/` for a starting skeleton.
- The actual HA `notify` custom_component doesn't exist yet — today
  this is orchestrated by standalone scripts, see `addon/scripts/`.
- Whether the "everything runs inside the Android app, no external
  Bluetooth box at all" approach is viable on *any* device is genuinely
  unknown — we only tested one mid-range Qualcomm phone. If you try it
  on a Pixel or a different chipset and get real (non-silent) audio
  out of `VOICE_DOWNLINK`, please open an issue — that would change the
  whole architecture for the better.
