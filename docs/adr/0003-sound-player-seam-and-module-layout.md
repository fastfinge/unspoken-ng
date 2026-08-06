# The Sound Player seam is four fire-and-forget commands

The rebuilt pipeline is six modules, and the seam between the addon and its audio is:

```
play(slot, position)        # returns nothing
set_theme({slot: (samples, source_rate)})
set_reverb(preset)
close()
```

Above it sit `spatial` (screen rects in, listener-relative unit vector out — pure, no NVDA and no
OpenAL), `themes` (a library constructed with the two directories it reads, owning every "no
usable theme" fallback as refined by [ADR 0005](0005-the-sound-theme-library-is-constructed-with-its-directories.md),
plus decode, mono downmix and per-theme RMS gain — pure stdlib, runs off-NVDA), a `roles` table
mapping the ~40 NVDA control roles onto the ~15 slots, and
`GlobalPlugin`, which reads `obj.role` and `obj.location` on the main thread and wires the rest
together. Below it is one module — the Sound Player — whose implementation *is* the OpenAL Soft
adapter: DLL load, `ALSOFT_CONF`, the output device and its reopen worker, the source pool and
voice stealing, the shared reverb bus, and the listener gain. Nothing above the seam knows the
engine is OpenAL, and nothing below it knows what a control role is.

## Considered options

**A Sound Player layered above a separate audio engine module**, as the 2026-07-19 architecture
review drew it. Rejected because every invariant the Sound Player was extracted to own has since
been deleted: the generation counter and the `stop`/`feed`/`idle` ducking pairing by
[ADR-adjacent decision #10](https://github.com/akj/unspoken-ng/issues/10) (voices overlap, and role
sounds never duck), the worker thread and the wave-player lock by
[ADR 0001](0001-openal-soft-owns-its-output-device.md) (`alSourcePlay` costs 0.09 ms on the calling
thread, and there is no `nvwave` to serialise). Their replacements — a voice pool with oldest-voice
stealing, ramped stops, a shared reverb bus — are all native OpenAL behaviour: sources *are* voices
and the auxiliary effect slot *is* the bus. The upper module would have forwarded calls, which is
the shallow shape the review set out to eliminate.

**`move(voice, position)` in the interface**, so a sound can fire at event dispatch and be
positioned when COM extraction completes. Rejected on the bundled sounds' own durations: they run
11–492 ms with a median around 90 ms, against the 60–170 ms extraction measured in
[#2](https://github.com/akj/unspoken-ng/issues/2). For 10 of the 14 slots the sound is over before
a position could arrive — `treeviewitem` (41 ms) and `button` (52 ms) would never be positioned at
all — and the four that are still ringing would be spatially wrong for their first third and then
jump. A role sound that plays centred is this addon's value proposition switched off. Position is
resolved before the voice exists, which puts the latency budget on the cost of extraction instead.
That cost was unmeasured per property when this was decided, and was ticketed for it:
[#28](https://github.com/akj/unspoken-ng/issues/28) since found extraction costs 0.13 ms at p50
outside browse mode, and the 88 ms browse-mode figure to be one uncached property read taken seven
times. The layout below stands on that result.

**A voice handle returned from `play`.** Rejected for want of a caller. With `move` gone, the
handle exists only for `stop`, and in-flight voices are never cut: speech cancellation does not
flush them, interrupts overlap rather than interrupt, tails ring out unconditionally, voice
stealing is internal to the source pool, the settings panel's "speech only" and "silence during say
all" suppress sounds from *starting*, and teardown is `close()`. A handle would also be the one
shape forcing a synchronous round trip across a future process boundary, on the latency path.

**The sound library below the seam**, with `set_theme(theme_id)` and discovery inside the player.
Rejected because the settings panel scans for themes on panel open and labels them from manifest
names, entirely independently of playback — so the panel would have to enumerate themes *through*
the audio module, an interface method existing solely for the GUI. Keeping the library above also
keeps decode and file I/O out of the audio module and lets the pure part run in tests with no NVDA
and no DLL.

**Angles at the seam** (azimuth/elevation in, trigonometry below). Rejected because it leaves the
unit-vector normalisation — load-bearing, since un-normalised coordinates put the source far enough
away that distance attenuation silenced it — a file away from the display constants that feed it.
That split is exactly the seam that produced the silent-audio bug.

**Resampling above the seam**, with the library conforming to the player's rate. Rejected once
device-follow landed: an `alcReopenDeviceSOFT` onto a different-rate endpoint leaves every loaded
buffer at the wrong rate, and because the reopen runs asynchronously on the player's own worker,
re-conforming would require a callback into Python across the seam — the shape ADR 0001 ruled out.

**`set_device` and `set_volume` on the interface**, driven by the plugin. Rejected because NVDA has
no `outputDeviceChanged` extension point, so *somebody* must actively watch config, and
`alcReopenDeviceSOFT` blocks for 31–470 ms and must never touch the main thread. Putting that in
the plugin makes "never reopen on the main thread" a rule every caller has to remember, which is
the class of caller-visible invariant this seam exists to abolish.

## Consequences

- **The player owns device-follow and volume**, through an injected settings provider exposing the
  current output device and sound volume. It compares both on every `play` — two `config.conf` dict
  lookups, free on the latency path — applying a volume change inline as one `alListenerf` and
  handing a device change to its own worker thread. This registers no extension points, works
  unchanged across NVDA 2025.1–2026.x, and self-heals: any missed change corrects at the next
  sound. The accepted cost is that the first sound after a device change can be lost.
- **Position is a listener-relative unit vector**, +x right, +y up, −z forward, distance fixed at 1,
  with `AL_SOURCE_RELATIVE` set explicitly rather than relying on the default listener pose. The
  convention is the addon's own and is documented in `CONTEXT.md`; it happens to coincide with
  OpenAL's, so no transform is needed, but nothing above the seam has to read an OpenAL spec.
- **`ALSOFT_CONF` is written by the player's constructor**, immediately before it loads the DLL —
  which today happens inside the constructor rather than at module import, so the ordering
  constraint is satisfiable entirely within the module and imposes no import-order rule on anyone
  else. The variable is process-global: another addon loading OpenAL Soft in the same NVDA would
  inherit our config file.
- **Buffers carry their source rate**, and OpenAL resamples per source during mixing. A device
  reopen therefore needs no re-conform, the engine's rate never appears in the interface, and
  [#23](https://github.com/akj/unspoken-ng/issues/23) — `link.wav` playing flat because the
  pre-rebuild code handed every file to `alBufferData` as 44100 — cannot recur. That is how #23
  was resolved: by construction, not by editing the asset, which is still 48 kHz.
- **One auxiliary effect slot is shared by every voice**, so a reverb tail outlives its source,
  including a stolen one. That is the mechanism behind "no cut-off tails". Reverb preset changes
  apply immediately and may alter an in-flight tail's character.
- **Two adapters exist at the seam**, not one: the OpenAL implementation and a silent one, which
  the no-DLL degraded mode requires anyway and which lets the pipeline be tested off-NVDA.
- **`openal_audio.py` stops being a seam**, and with it go the generation counter, the wave-player
  lock, thread-per-sound, `stop()`, the reverb tail-frame arithmetic, the `+0.25` HRTF gain hack,
  `_compute_volume`, `create_wave_player`, and the `sounds` / `sound_files` globals.
