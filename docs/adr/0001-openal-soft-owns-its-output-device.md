# OpenAL Soft owns its output device

The audio engine is **OpenAL Soft**, and it opens the output device itself via `alcOpenDevice`,
running its own C mixer thread inside NVDA's process. `nvwave.WavePlayer` is not in the path:
measurement showed the engine was never the bottleneck — a *Python* feeder cannot hold a bounded
output queue through `nvwave`, so after any main-thread stall it must either burst (243 ms of
permanently inflated onset under fast navigation) or leave a gap, and `nvwave`'s only depth
telemetry misreports under exactly that load. With the device owned directly, `alSourcePlay`
returns in 0.09 ms on the calling thread and no Python code sits in the audio path at all.

## Considered options

**`nvwave.WavePlayer` owns the stream.** Rejected for the queue behaviour above. It bought three
things for free, and all three turned out cheap to own: ducking is void because role sounds never
assert it, volume is one `alListenerf(AL_GAIN, …)` at 0.086 ms, and device-follow is NVDA's stored
`[audio] outputDevice` passed straight through to `alcOpenDevice` — `"default"` becomes NULL and
any other value is used verbatim, with no name matching — plus a non-destructive
`alcReopenDeviceSOFT` and `ALC_SOFT_system_events` for change notification.

**A Rust audio core owning its stream.** Architecturally the same property, and it ties or loses
every column. It does not lower the Windows latency floor (`cpal` is shared-mode-only, the same
period class as OpenAL Soft's backend); it measured ~62 ms event→air against the owned OpenAL
stream's 52.4 ms; its HRTF layer is a ~3-year-stale, bus-factor-1 crate against OpenAL Soft's
actively maintained one; it needs a toolchain, a second build artifact and CI from zero; and it
requires `catch_unwind` at *every* FFI export, where a single miss takes the user's screen reader
down. Its one advantage was a listening test in which it localized ±45° better — but that test
judged OpenAL's loopback configuration, which runs at 44100 and therefore resamples the built-in
HRTF down to a 59-tap filter. The owned device runs at the endpoint's native 48000 and uses the
64-tap filter with no output resampler, so the comparison does not describe the option chosen here.

**An off-the-shelf native engine.** Closed on its own evidence: Synthizer is archived and already
failed in this addon's lineage (device switching, squealing, 64-bit breakage), miniaudio has
neither HRTF nor reverb, SoLoud has no HRTF and a stalled release cadence, and FMOD's license does
not fit an open NVDA addon.

**The engine in a separate process.** Buys crash isolation and nothing else — it is latency-neutral,
because the triggering event is born inside NVDA's process and still waits on the GIL to be noticed,
and an IPC hop costs tens of microseconds. Its sharpest argument was the Rust panic hazard, which
choosing OpenAL retires; what remains is a `soft_oal.dll` fault, which is the status quo this addon
has already shipped for years. Not worth process lifecycle management, orphan reaping after an NVDA
crash, a device-follow split across the boundary, and two artifacts to build, package and sign.

## Consequences

- **The Sound Player seam is commands** — with no callbacks into Python and no shared buffers —
  which keeps a later retreat behind a process boundary a relocation rather than a redesign.
  This decision originally named those commands `play(slot, position) -> voice`,
  `move(voice, position)` and `stop(voice)`; **[ADR 0003](0003-sound-player-seam-and-module-layout.md)
  supersedes that list**: the seam is four fire-and-forget commands, `play` returns nothing, and
  `move` and `stop` are dropped for want of callers, taking the voice handle with them.
  Fire-and-forget commands satisfy the constraint more strictly, since a `play` returning a handle
  would force a synchronous round trip across any future process boundary, on the latency path.
- **There is no fallback output path.** When the owned stream cannot open, the addon has nowhere to
  fall back to, so the failure and degraded-mode behaviour must be specified deliberately.
- **The buffer is the one latency knob**, and it works 1:1 with onset. `ALSOFT_CONF`
  `[general] periods = 2` yields a 22 ms buffer — the floor, since the backend clamps 960 frames up
  to 1056 — against 32 ms at the default of 3. `ALC_REFRESH` is ignored; the 10 ms period is pinned
  by WASAPI shared mode. The addon must write `ALSOFT_CONF` into `os.environ` *before* the DLL
  loads. This bullet originally concluded that the write "constrains module initialisation order";
  [ADR 0003](0003-sound-player-seam-and-module-layout.md) found it constrains nothing outside the
  player's own constructor, and records the process-global caveat that does bite. The glitch soak in
  [#40](https://github.com/akj/unspoken-ng/issues/40) kept `periods = 2` as the shipped default; the
  run evidence lives on that issue.
- **`ALSOFT_CONF` also carries the HRTF requirements**, because it is the highest-priority config
  source on Windows and therefore the only place the addon can state them and not be overridden.
  The `ALC_HRTF_SOFT` context attribute is necessary but *not* sufficient: an explicit user-level
  `alsoft.ini` outranks an application's request, so the config restates it as
  `stereo-encoding = hrtf` (the 1.23+ spelling; `hrtf = true` is deprecated). Alongside it,
  `hrtf-mode = full` buys per-source HRIR filtering — the ambisonic modes exist to give a
  many-source scene a fixed cost and pay for it in exactly the elevation and front/back cues this
  addon sells, which a 12-voice pool has no reason to trade away — and `channels = stereo` keeps
  HRTF reachable at all, since it is declined outright on an endpoint Windows has configured as
  5.1/7.1. Whether it ended up on is not assumed: `ALC_HRTF_STATUS_SOFT` is logged at construction
  and after every reopen, so a denial names its cause.
- **`alcReopenDeviceSOFT` blocks for 31–470 ms** and must never run on NVDA's main thread.
- **The loopback justification is discharged.** `openal_audio.py`'s docstring defends loopback as
  "preserving NVDA ducking and device routing"; ducking no longer applies on any branch, and device
  routing is now handled directly. The docstring goes with the loopback path.
- **The latency budget remains dispatch-bound**: ~10 ms from event dispatch to the sound being handed
  to the output stream, with COM extraction off the critical path. True event→air rides the
  platform's shared-mode floor, which this decision minimises but does not own.
