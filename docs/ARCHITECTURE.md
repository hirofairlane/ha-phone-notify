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

## RESOLVED: PipeWire audio streams never completed in the packaged Docker+VM add-on — root cause is nested virtualization

**Root cause found, confirmed, and worked around.** The exact same
`paplay`/`pw-play` command that reliably timed out at ~30s inside the
Docker container (itself running inside the HAOS VM, itself running on
Proxmox — three layers deep) completed in **2.007 seconds** when run
natively on the Proxmox host's own Debian OS, no Docker, no VM,
otherwise identical PulseAudio/PipeWire setup. This was confirmed by
literally moving the whole stack (PulseAudio, HandsFree-Linux, the
Vosk/Piper models) off the HAOS VM and onto the Proxmox host directly
— see "Deployment pivot: run natively, not as a Supervisor add-on"
below for the full story and why this changes the recommended
deployment target for this whole project.

**Practical takeaway: don't run this stack's audio path inside
Docker-inside-a-VM.** If your Home Assistant is a Supervisor
installation (HAOS VM or bare metal with the HA OS), install the
scripts directly on a Debian/Ubuntu Linux host on your network instead
of trying to make this a container-based Supervisor add-on — see the
deployment section below. The debugging trail that led here is kept
below for anyone who hits a similar stall in a different nested-VM
setup and needs to rule things out systematically.

### What this bug looked like at the time

A second, separate bug found while validating the packaged add-on for
real (Docker container, inside a HAOS VM, on Proxmox — as opposed to
the loose system packages the rest of this doc describes). Once the
SLC-flakiness bug above got fixed enough to reach a genuinely stable
Bluetooth link (confirmed via a live AT+CIND keepalive exchange running
for minutes without dropping), and SCO audio itself started correctly
(`SCO audio running`, confirmed with real bidirectional pacat/parec
loops from HandsFree-Linux's own side) — **`paplay`/`pw-play` still
never actually deliver the prompt.** Every playback attempt against the
virtual `phone_notify_mic` null-sink (and even the system default sink,
ruling out a device-selection bug) fails after **exactly ~30 seconds**
with `Stream error: Timeout`.

### What was ruled out, with evidence

- **Not a device-selection bug.** Reproduced identically with an
  explicit `--device=phone_notify_mic` and with no `--device` flag at
  all (system default sink).
- **Not the container's PipeWire being dead/OOM-killed.** `pactl list
  sinks` returns the sinks fine, `ps`/`/proc` confirm `pipewire` and
  `pipewire-pulse` processes are alive and running throughout. (An
  earlier debugging session wrongly concluded PipeWire had crashed —
  that was a **self-inflicted false alarm**: manually running `pactl`
  via a fresh `docker exec` doesn't inherit run.sh's exported
  `$XDG_RUNTIME_DIR`, so it was connecting to the wrong/nonexistent
  socket path. Always pass `XDG_RUNTIME_DIR=/tmp/run` explicitly when
  debugging manually from outside run.sh's own process tree.)
- **Not a missing ALSA/dummy clock driver.** `pw-dump` shows a
  `Dummy-Driver` and `Freewheel-Driver` node present in the graph.
  Explicitly mounting the host's `/dev/snd` (which exists on the HAOS
  host but has no real sound card, only `seq`/`timer`) into the
  container made no difference.
- **Not a clock-rate/quantum negotiation problem.** Forcing explicit
  `default.clock.rate` / `default.clock.quantum` /
  `support.dummy-driver` context properties via a
  `pipewire.conf.d` drop-in made no difference — still fails at
  exactly ~30.0s every time.
- **Confirmed via `strace -f -tt` on the hanging `paplay` process**:
  the client successfully connects to `pipewire-pulse`, completes the
  `CREATE_PLAYBACK_STREAM` protocol handshake (`SCM_CREDENTIALS` shows
  the correct peer PID), then sits in `ppoll()` waiting on the stream's
  fd for further server events that never arrive — for exactly the
  ~30s the client-side protocol timeout allows, then gives up. This is
  a real, client-observable stall in stream lifecycle progress on the
  **server** side (`pipewire-pulse` accepts the stream but never
  drives it to a playable/draining state), not a client bug and not an
  infinite hang.

### Confirmed root cause (this is what actually happened)

The nested-virtualization hypothesis below was correct. Running the
identical `paplay --device=phone_notify_mic test.wav` command:

- Inside the Docker container, inside the HAOS VM, on Proxmox: hangs,
  fails at ~30.0s every time, no exceptions.
- Natively on the Proxmox host's own Debian OS (same PulseAudio
  version, same null-sink setup, no Docker, no VM): **completes in
  2.007 seconds.**

This wasn't a targeted fix for a specific PipeWire scheduling bug —
it was validated by removing the nested-virtualization layer entirely
and observing the exact same operation succeed immediately. Whether
the underlying trigger is specifically "Docker inside QEMU/KVM inside
Proxmox" or something narrower (a specific `io_uring`/virtio-net
interaction, a clock-source quirk under nested KVM, etc.) was not
narrowed down further, since a working deployment path (run natively)
was already in hand and higher priority than root-causing the VM
internals for their own sake.

### Original leads (kept for reference — largely superseded by the above)

- This smells like a `pipewire-pulse` (or core PipeWire graph
  scheduling) bug specific to genuinely headless environments with
  *zero* real audio hardware **and** nested virtualization (Docker
  inside a QEMU/KVM VM inside Proxmox) — each layer individually is
  common and well-supported, but this specific combination may be
  under-tested upstream.
- Worth checking PipeWire's own GitLab issue tracker for existing
  reports matching "stream never completes docker no sound card"
  before debugging further — this write-up alone represents several
  hours of systematic elimination and might just be reproducing a
  known, already-reported bug.
- Worth testing on real hardware (a physical Raspberry Pi, no nested
  virtualization, no Docker) to isolate whether nested virtualization
  specifically is the trigger, or whether it's purely about the
  complete absence of real audio hardware.
- `PIPEWIRE_DEBUG=3` (or higher) environment variable on the
  `pipewire` process itself (not just the client) would likely show
  server-side graph scheduling detail that client-side `strace` can't
  reveal — not yet tried, and now moot given the working native path.
- Given the ~30s figure is suspiciously exact and round, check
  `pipewire-pulse`'s own source for a hardcoded stream-creation
  timeout constant — this might be a deliberate fallback timeout
  rather than an emergent scheduling stall, which would point
  debugging effort in a very different (and probably faster)
  direction.

## Known issue: the HFP link (SLC) can be flaky under BlueZ

While building the actual HA add-on (Docker image, MQTT bridge, real
Supervisor deployment — as opposed to the loose system packages the
rest of this doc describes), a real, still-open bug surfaced: the HFP
Service Level Connection (SLC — the RFCOMM/AT-command session on top
of the base Bluetooth link, separate from and slower to establish
than basic pairing/connection) sometimes:

- drops on its own after roughly 1-2 minutes idle (no active call),
  logged as `Send error: [Errno 104] Connection reset by peer` or
  `[Errno 107] Transport endpoint is not connected`;
- fails to (re-)establish at all on a given attempt, with no error
  beyond a timeout — sometimes the very next attempt succeeds cleanly
  within a couple of seconds.

Consequence if unhandled: dialing while the SLC happens to be down (or
mid-negotiation) means the call goes out over the SIM as a completely
normal, audio-less phone call — nothing is broken from the caller's
perspective (it rings, connects, sounds completely ordinary), but no
TTS prompt is heard and nothing is captured, because the Bluetooth
audio path was never actually attached to it.

Mitigations implemented so far (`addon/scripts/mqtt_bridge.py`):

1. Before every dial, check `bluetoothctl info <mac>` and explicitly
   `bluetoothctl connect <mac>` if the base link is down.
2. **Also wait for `SLC established` in HandsFree-Linux's own log**
   before dialing, not just the base BT connection — the base link
   coming up is necessary but not sufficient; SLC negotiation is a
   separate, slower step on top of it, and dialing before SLC is
   actually ready means the phone has already committed to the
   earpiece as the call's audio route by the time SLC finishes (it
   does not retroactively switch to Bluetooth mid-call).

This reduced but did not eliminate the failure rate at the time. Two
further root causes were since found, both on the "your own host may
have hidden competition for the HFP-HF role" and "the phone's own BT
stack degrades under heavy test-cycling" axes:

### Root cause #1: other services silently competing for the same BlueZ D-Bus profile

BlueZ only lets **one** process register the `hfp_hf` (Handsfree,
UUID `0000111e`) profile at a time. On a host that already runs other
things, there can be more claimants than you'd expect, and BlueZ gives
no obviously-labelled error pointing at the culprit — HandsFree-Linux
just logs `HFP UUID already claimed by another process`. On the
Proxmox host used for the native deployment (see below), **three**
separate pre-existing services turned out to be squatting on it,
found and stopped one at a time via `busctl list --system` (look for
unexpected D-Bus name owners) and `bluetoothctl show`'s UUID list
(the "Handsfree" entry should disappear once nothing else holds it):

1. `btproxy-audio.service` — a systemd unit for an unrelated PulseAudio
   stack (`backend=ofono`), enabled and running since long before this
   project, whose own `ofono.service` dependency was masked/inactive
   — so it wasn't doing anything *useful*, but was still holding the
   registration.
2. A different Linux user's own systemd `--user` PulseAudio instance
   (`pulseaudio.service` **and** `pulseaudio.socket` — you have to stop
   both, the socket unit respawns the service on the next connection
   attempt otherwise) with Bluetooth modules auto-loaded.
3. **BlueALSA** (`bluealsa.service` + `bluealsa-aplay.service`) — a
   completely separate Bluetooth-audio daemon, unrelated to PulseAudio/
   PipeWire, that also implements HFP-HF.

A red herring worth flagging so nobody else wastes time on it: `bluetoothd
--noplugin=hfp-hf` looks like the obvious fix if you `strings` the
`bluetoothd` binary and see `hfp-hf`/`hfp-ag` — but those strings are
just BlueZ's UUID-to-display-name lookup table, not a loadable plugin.
Confirmed by running `bluetoothd --nodetach --debug` and checking its
own plugin-loading log: no `hfp` plugin is ever listed as loaded in the
first place, so there's nothing for `--noplugin` to disable.

**If you hit "HFP UUID already claimed," don't assume it's
HandsFree-Linux/PipeWire's own WirePlumber config — audit the whole
host for other Bluetooth-audio services first**, especially on a
Proxmox/homelab box that's accumulated services over time.

### Root cause #2: the phone's own Bluetooth stack degrades under repeated test cycles

Separately, **the phone side genuinely degrades**, not just the Linux
side. After enough repeated pair/connect/disconnect cycles in a single
testing session, the Redmi's own Bluetooth stack got bad enough that
its Settings app itself crashed ("Settings isn't responding"), and SLC
establishment became unreliable regardless of what was fixed on the
Linux side. **A full phone reboot fixed it completely** — SLC became
rock-solid afterward, verified via a live `AT+CIND?` keepalive exchange
running for minutes with zero drops. If you've fixed every known
Linux-side cause and SLC is still flaky, reboot the phone before
digging further — it's a five-second test that can save hours.

### Leads still open

- Capture `btmon` across several SLC-establishment attempts (both
  fast-success and slow/failed ones) and diff the HCI-level traffic —
  this project already used that technique successfully to root-cause
  an unrelated scripted-pairing bug (see the pairing gotchas above),
  it's the right tool here too.
- Check whether `preferred_codec = "cvsd"` vs `"msbc"` in
  `handsfree/config.toml` (and whether `libsbc1` is installed) affects
  failure rate — tested inconclusively (both codecs failed at least
  once, both also succeeded at least once) but not exhaustively.
- Consider whether periodic keepalive traffic on the RFCOMM channel
  (instead of only reconnecting reactively before a call) would
  prevent the idle-drop in the first place, rather than recovering
  from it after the fact.
- Test on a second phone/chipset to isolate whether the stack
  degradation described above is Redmi/MediaTek-specific or general.

## Deployment pivot: run natively on a Debian/Ubuntu host, not as a Supervisor add-on

The `addon/` skeleton (Docker image, `config.yaml`, meant to install
via `ha store add` / `ha apps install` on a HAOS Supervisor install)
was the original plan, and still builds and installs cleanly. But
because of the PipeWire nested-virtualization bug above, **it does not
reliably deliver audio if your HAOS install is itself a VM** (the
common case for a Proxmox homelab, and likely for most non-bare-metal
Home Assistant Yellow/Green setups too). Running the exact same code
directly on the VM host's own Linux OS — not inside Docker, not
inside the VM — is the only configuration that's actually been proven
to work end-to-end with real audio.

Concretely, this means: instead of installing this as an HA add-on
through the Supervisor, install `addon/scripts/*.py` and their Python
dependencies directly on a Debian/Ubuntu box on your network (which
could be the Proxmox host itself, a Raspberry Pi, or any other always-on
Linux machine with a Bluetooth adapter) and point it at your existing
HA instance's MQTT broker over the network — the MQTT protocol
(`addon/scripts/mqtt_bridge.py`'s docstring) doesn't care whether the
process publishing to it is a Supervisor add-on or a plain systemd
service. `docs/SETUP.md` needs a rewrite to reflect this as the
primary installation path — the add-on skeleton is kept for anyone
running bare-metal HAOS (no nested VM) where it should work fine, but
is no longer the recommended path for VM-based installs.

### First real end-to-end success (native, this session)

Confirmation this actually works, not just in theory — a full run
against a live cellular call, using the exact `addon/scripts/mqtt_bridge.py`
code (not a simplified test harness):

```
[mqtt_bridge] dialing +34679411512
[mqtt_bridge] Bluetooth link already up, confirming SLC...
...CIEV: call=1
audio.manager: SCO audio running  out=phone_notify_speaker  in=phone_notify_mic.monitor
[mqtt_bridge] heard: 'cancelar'
[mqtt_bridge] request native_test9 -> cancel
```

adb-triggered dial → SLC confirmed → SCO audio bridge live → Piper TTS
prompt played into the call → the far end's spoken "cancelar" captured
and correctly transcribed by Vosk → matched against the configured
`response_actions` → result published → **call automatically hung up**
(see bug fix below). This is the first time in the project every stage
of the pipeline was validated together against a real phone call, on
the actual add-on codebase.

### Bug found and fixed during this test: calls were never hung up

`process_call()` in `mqtt_bridge.py` had no hangup step anywhere —
once dialing finished, the function only ever *stopped listening*
(on a keyword match, on the listen timeout, or if the far end hung up
first) and returned a result. If the far end never hung up on their
side, the call stayed connected (`mCallState=OFFHOOK`) indefinitely
after the add-on considered the interaction "done." Fixed by wrapping
the whole dial→listen sequence in `trigger_call`/`process_call` in a
`try/finally` that always ends with `adb shell input keyevent
KEYCODE_ENDCALL`, regardless of which of the three outcomes
(matched action / `TIMEOUT` / `NO_CALL`) occurred.

### Operational note: "NO_CALL" isn't always a bug — check for radio-level failures too

While testing repeatedly in a short window, several attempts returned
`NO_CALL` (SCO audio never started) with the exact same ~32s delay
each time. `adb shell dumpsys telecom` showed the actual cause:
`DisconnectCause [ Code: (ERROR) ... Reason: (Radio is out of
service, ERROR_UNSPECIFIED) ]` — the calling phone's modem was
failing CS-fallback/VoLTE at the moment of dialing (an MVNO SIM with
marginal indoor voice coverage, confirmed via `dumpsys
telephony.registry`'s `mVoiceRegState`), even though data (LTE)
registration looked fine seconds earlier. **If you see intermittent
`NO_CALL` results, check `adb shell dumpsys telecom` for the
disconnect reason before assuming it's a software bug** — a
`Radio is out of service` reason means the calling phone's carrier
connection is the problem, not this project's code.

### Operational note: Vosk model size and memory, on a bigger host too

The small (~40MB) Vosk model mis-transcribed real telephone-quality
(narrowband CVSD) audio badly enough to produce false keyword matches
even on a host with plenty of headroom — this isn't just a
resource-constrained-hardware problem, the small model is simply not
accurate enough on 8kHz call audio regardless of the machine it runs
on. The ~2.3GB `vosk-model-es-0.42` model fixed it, transcribing
`"cancelar"` cleanly. Loading it uses a few GB of RAM briefly; on a
host that's already running other services near its memory ceiling
(e.g. a Proxmox box also running VMs and an NVR), check `free -h`
before the first load — if available memory is tight, loading a ~2GB
model on top of existing swap pressure is worth watching rather than
assuming it'll just work.

## Open questions / where this needs help

- The actual HA `notify` custom_component doesn't exist yet — today
  this is orchestrated by standalone scripts, see `addon/scripts/`.
  In progress.
- `docs/SETUP.md` still documents the Supervisor add-on install path
  as primary — needs a rewrite reflecting the native-deployment pivot
  above as the recommended path for VM-based HAOS installs.
- The Supervisor add-on packaging (Docker image, `config.yaml`) works
  and installs cleanly, but is only known to deliver working audio on
  bare-metal HAOS (no nested VM) — untested on that specific
  configuration, since the development/test environment is a VM.
- Whether the "everything runs inside the Android app, no external
  Bluetooth box at all" approach is viable on *any* device is genuinely
  unknown — we only tested one mid-range Qualcomm phone. If you try it
  on a Pixel or a different chipset and get real (non-silent) audio
  out of `VOICE_DOWNLINK`, please open an issue — that would change the
  whole architecture for the better.
