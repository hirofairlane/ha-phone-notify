# Android diagnostics

`CallAudioCapture.kt` is the exact test used to confirm "dead end #1"
in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md): it tries
`AudioSource.VOICE_DOWNLINK`, falls back to `AudioSource.VOICE_CALL`,
and writes whatever it captures to a WAV file, logging the max sample
amplitude seen — the fastest way to tell "silence" from "real audio"
without needing to actually listen to the file.

To use it against your own app/device:

1. Make your app hold `android.permission.CAPTURE_AUDIO_OUTPUT` (needs
   a systemless priv-app install via Magisk — see ARCHITECTURE.md) and
   runtime-grant `RECORD_AUDIO`.
2. Make sure whatever foreground service triggers this declares
   `microphone` in its `foregroundServiceType`.
3. Trigger `CallAudioCapture(outFile).start(seconds)` from anywhere in
   your app during a live call and check `maxAbsSample` in the logs.

If you get a non-zero `maxAbsSample` on a device this project hasn't
been tested on, please open an issue — that's a genuinely useful data
point.
