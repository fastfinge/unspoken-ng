# Spatial speech routes nvwave per utterance

A **Spatial speech** setting places the synthesizer's speech where the role sound came from, so the
earcon and the words about the control arrive from one place. It has three values: `none` (the
default; speech exactly as without the addon), `panning` (left/right, still on nvwave) and `hrtf`
("Panning and HRTF": the role sounds' own OpenAL device, HRTF, reverb bus and clock).

It is built as a **speech router** (`speechrouter.py`) that patches five methods of
`nvwave.WavePlayer` — `feed`, `stop`, `sync`, `idle`, `pause` — and gives every mono speech player a
lane whose route is chosen at each **utterance boundary**: the first `feed` after the player was
created, stopped or idled. `panning` feeds a companion stereo `WavePlayer` on the same device with
the mono audio duplicated into both channels and its channel volumes set from the earcon's x;
`hrtf` feeds an `OpenALSpeechStream` opened from the Sound Player. Speech is placed at the last
**arrival** — an object event, or the field a caret move or quick-nav landed inside
(`playback.is_arrival`) — which is the same moment ADR 0002 lets a sound lead, read with the same
position the earcon got.

## Considered options

**Ask synthesizer authors to do it** (as the Dengjen add-on does in its own driver). Rejected as the
primary answer: it reaches one synth at a time and can never place speech where *our* earcon was,
because the synth does not know. Interception reaches every in-process synth — eSpeak, OneCore,
SAPI 5 and most add-on drivers construct `nvwave.WavePlayer` — with one mechanism.

**NVDA's sound split, or per-session channel volume.** Rejected: it pans NVDA's whole audio session,
which includes the Sound Player's own OpenAL stream, so it would fight the earcons' HRTF placement
rather than match it.

**`WavePlayer.setVolume(left=, right=)` on the synth's own player.** The cheapest pan, and
unavailable: the synth opened a mono stream, whose `IAudioStreamVolume` has one channel. Hence the
companion stereo player; its channel volumes are then set once per utterance, so the per-chunk cost
is a byte-sliced upmix and no per-sample arithmetic.

**Replace the synth's player** (a `WavePlayer` subclass, or recreating its C++ stream in stereo).
Rejected: the synth is loaded before global plugins, so its player already exists, and recreating
it would mean duplicating nvwave's construction or forcing a synth reload. Patching the class
methods reaches existing players as they stand and is undone by restoring five attributes.

**Choose the route per call, or switch it the moment the setting changes.** Rejected: an utterance
whose first half was on one output and second half on another would split one sentence across two
streams, and a stale feed from a cancelled utterance could land on a route that was never told to
stop. Per-utterance routing makes "an utterance finishes where it started" true by construction,
and the setting can still be written through live from the panel (as volume is, ADR 0007): the
newly selected choice's own announcement is the first speech placed by it.

**HRTF speech through a separate OpenAL context or device.** Rejected: a second context has its own
listener and its own effect slots, so speech would not share the earcons' reverb bus, and a second
device would not share their clock. Speech therefore lives in the Sound Player's context, as a
dedicated streaming source outside the voice pool.

**Fire index callbacks from `AL_SOFT_events`.** Considered and not taken: it moves `onDone` delivery
onto an OpenAL C thread whose trampoline must be registered, kept alive and unregistered exactly as
the device-event one is, for a precision the synth cannot use. A per-stream service thread that
recycles buffers every 5 ms while — and only while — audio is queued is simpler, and lands index
callbacks well inside a synth's own index granularity.

## Consequences

- **The Sound Player seam gains a fifth method, and it returns something.** This amends
  [ADR 0003](0003-sound-player-seam-and-module-layout.md), which fixed the seam at four
  fire-and-forget commands. `open_speech_stream(rate)` returns a `SpeechStream` (or None) because
  speech is a stream with a synth blocked on the other end: it keeps nvwave's contract — `feed`
  blocks while 400 ms is queued (NVDA's own `BUFFER_MS`), `stop` drops pending callbacks and
  releases a blocked `feed`, `sync` waits for playback *and* callbacks. The role-sound commands
  are unchanged; the silent adapter returns None, so a degraded session's "Panning and HRTF" falls
  back to panning.
- **Role-sound volume is per voice, not the listener gain.** This amends
  [ADR 0001](0001-openal-soft-owns-its-output-device.md)'s "volume is one `alListenerf`": the
  listener scales every source in the context, so it would have turned speech down with the Sound
  volume slider. `play` writes the volume as the voice's own gain (the write it already made to
  reset a stolen voice), the listener stays at unity, and a steal ramps down from the gain the voice
  started at. A volume change now applies to the next sound rather than to sounds already ringing.
- **The HRTF route does by hand what it bypasses.** nvwave's leading-silence trimming lives in its
  C++ player, so the route trims to the same threshold (full scale / 2¹⁰) under the original
  player's own `trimLeadingSilence` and BreakCommand state, and it asserts the original player's
  audio ducker. The panned route needs neither: its companion is an nvwave speech player.
- **A lost device completes speech instead of stranding it.** While the Sound Player is
  disconnected, a speech stream drops its audio and fires its callbacks at once: the speech
  manager waits on those indexes, and say-all must not hang on a device nobody can hear. During a
  reopen (31–470 ms) the source and its queue survive; a `stop` from the main thread may wait for
  the reopen, which is the one way this feature can touch the main thread's latency.
- **Placement is per utterance.** Speech stays at the last arrival through say-all and through
  announcements that are not about an arrival (typed characters, `ui.message`), and an arrival that
  lands mid-utterance applies from the next one. With spatial speech on, an arrival reads the
  position even when no sound plays — quick-nav to a heading has no role sound — which on the
  browse-mode reading path is the 6.6–8.9 ms identifier materialisation of spec section 13, once
  per keypress, and costs nothing with the setting at `none`.
- **Out-of-process synths are not placed.** SAPI 4 and 32-bit SAPI 5 voices play in
  `synthDriverHost32`; their audio never reaches this process's `nvwave`.
- **Five more patched attributes to give back.** `terminate` uninstalls the router before closing
  the player, restoring only methods that are still ours, and closes every route; a synth left
  mid-utterance on the HRTF route is back on nvwave's own methods before its stream goes away.
