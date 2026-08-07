# Unspoken-ng smoke test

The parts of this addon that can only be wrong inside a running screen reader.
Everything above the Sound Player seam that *can* be tested off NVDA already is
(`pytest tests/`); this checklist is the rest, and it is deliberately a
checklist rather than a harness — NVDA has no supported headless addon-test
rig, and the risks it covers are commissioning risks, not regression risks.

**Run it whole against every NVDA beta.** That re-run is the stated detection
mechanism for a future NVDA release moving one of our hook points: nothing else
would notice `TextInfo.getControlFieldSpeech` being renamed until users did.

Executable by a human or by a computer-use agent. Each item states what to do
and what "pass" sounds like.

## Before you start

| | |
|---|---|
| Build | `scons` in the repo root, then install the `.nvda-addon` and restart NVDA |
| Addon files | `%APPDATA%\nvda\addons\Unspoken-ng\globalPlugins\Unspoken\` |
| Log | `%TEMP%\nvda.log`, at log level **Debug** (NVDA menu → Preferences → Settings → General → Logging level) |
| Panel | NVDA menu → Preferences → Settings → **Unspoken-ng** |
| Headphones | Required. The whole point is left/right/up/down placement; laptop speakers will not show it |
| Test page | **`docs/smoke-test-page.html`** in this repo — open it in a browser |

Reset between runs where a step says so: quit NVDA, edit or delete the
`[unspoken]` section of `%APPDATA%\nvda\nvda.ini`, start NVDA.

If `docs/smoke-test-page.html` is unavailable, any page carrying all of this
will do — the inventory is what the steps below need, not the file:

- links, buttons and checkboxes in rows of three, pinned to the **left edge,
  the centre and the right edge** of the window (azimuth is unfalsifiable if
  every control sits in the same place)
- a combo box, a slider and a text input
- at least two headings and a list of three items, one containing an inline link
- several paragraphs of plain prose, with a link, a button and a checkbox
  **inside** the flow, so a say all crosses controls rather than ending at them
- enough vertical extent that some controls are near the top of the screen and
  some near the bottom (elevation, same argument as azimuth)

---

## 1. Startup

**1.1 Clean start.** Start NVDA with the addon installed.

- Pass: no error dialog, no spoken message from the addon. The log contains one
  `Unspoken-ng ready:` line reporting 14 slots, the configured theme and reverb
  preset, and `degraded=False`.
- Fail: any dialog, any startup speech from the addon, `degraded=True`, or a
  traceback mentioning `Unspoken`.

**1.2 Config spec.** Open the Unspoken-ng settings panel.

- Pass: exactly five controls, in order — Sound theme, Role announcement,
  Reverb, Sound volume, Silence role sounds during say all. Tab reaches each
  one and NVDA announces its label.
- Fail: any leftover control from the pre-rebuild panel, an unlabelled control,
  or focus landing somewhere other than the category list when the dialog opens.

---

## 2. The five entry points

### 2.1 Focus navigation (`event_gainFocus`)

Open a dialog with mixed controls (NVDA's own Settings dialog will do). Tab
through it.

- Pass: one sound per control, immediately, before or with the spoken name.
  Different sounds for button / checkbox / combo box / edit field. The role
  itself is **not** spoken (the name and state still are).
- Fail: silence on any control that has a sound; the role spoken as well as
  sounded; audible lag behind speech; two sounds for one Tab press.

Hold Tab down so it repeats fast.

- Pass: sounds overlap and ring out; no clicks, no cut-off tails, no lag that
  accumulates.
- Fail: clicking, sounds cutting each other off, or the sounds falling
  progressively further behind the keypresses.

### 2.2 Browse-mode Tab (`event_gainFocus`, again)

In a browser, with browse mode on, Tab between links spread across the page's
full width.

- Pass: the link sound, positioned where the link is on screen — a link on the
  left is heard on the left. **Position tracks the link, not the caret.**
- Fail: every link sounding from the same place; positions that do not match
  the visual layout.

### 2.3 Browse-mode reading (`getControlFieldSpeech` hook)

Same page, Down Arrow through lines that contain links, buttons and checkboxes.

- Pass: a sound as each control is entered, positioned at the control.
  Ordinary paragraph text produces **no** sound.
- Fail: a sound on every line regardless of content; a sound repeating for a
  control the caret is already inside; no sound at all on links (this path is
  the one that did not exist before 2.0).

Arrow onto a line containing several controls (a row of the three-across
control grid will do).

- Pass: the sounds arrive **spread across the line's speech**, each as its
  control is spoken — not as one burst when the line starts. (A control the
  caret lands *inside* — one starting the line — may rightly sound at once;
  it is the controls further along the line that must wait their turn.)
- Fail: all the line's sounds at once at the start of the line (that is
  build-time play returning — #52 / ADR 0002).

### 2.4 Quick navigation

Press `k` repeatedly to jump between links.

- Pass: one link sound per press, positioned at the link, **immediately** —
  the jumped-into field leads speech like a focus change does (ADR 0002).
- Fail: silence; two sounds per press (a double fire between the hook and
  `event_gainFocus`); or the sound waiting for the speech to start — that
  sluggishness is the entered-field split failing.

### 2.5 Say all — sounds on

Ensure **Silence role sounds during say all** is unchecked. Press
`NVDA+Down Arrow` on the test page.

- Pass: sounds continue through the read, one per control encountered,
  positioned, and **timed to speech**: an inline link's sound plays as the
  link text is spoken, not when its line is queued. Speech is uninterrupted.
- Fail: no sounds; sounds that stutter the speech; or sounds leading the
  spoken control by more than about a second — say-all queues lines ahead of
  the synth, so an early sound means build-time play is back (#52).

Start the say all again and press Control a paragraph or so **before** an
upcoming control.

- Pass: no sound for controls speech never reached; anything already sounding
  rings out naturally.
- Fail: the sound of a control that was never spoken arriving after the
  interrupt.

### 2.6 Say all — sounds silenced

Check **Silence role sounds during say all**, press OK, press `NVDA+Down Arrow`.

- Pass: no sounds at all during the say all. Ordinary Down Arrow reading
  (2.3) still sounds normally afterwards.
- Fail: sounds during say all; or the checkbox also silencing arrow-key reading.

### 2.7 Word caret

Open a Word document containing at least one hyperlink. Arrow through it.

- Pass: a sound when the caret enters the hyperlink, positioned near where the
  text sits on screen. Ordinary text lines produce **no** sound — in
  particular, no repeated edit-field sound on every line.
- Fail: a sound per line; two sounds per line.

### 2.8 Object navigation (`event_becomeNavigatorObject`)

With focus parked in a dialog, move the review cursor with
`NVDA+NumPad 4/6` (desktop) or `NVDA+Shift+Left/Right` (laptop).

- Pass: one sound per object moved to, positioned at that object.
- Fail: silence.

Now press Tab to move focus.

- Pass: **exactly one** sound. Focus moves the navigator object too, and the
  `isFocus` dedup is what stops that becoming two.
- Fail: two sounds per Tab press.

### 2.9 Mouse

Move the mouse slowly across a toolbar or a row of controls.

- Pass: one sound per control the pointer enters; no sound while the pointer
  stays on one control.
- Fail: a stream of sounds while stationary; silence while moving between
  controls.

### 2.10 Suppression matches playing

Set **Role announcement** to each value in turn and Tab through a dialog.

| Setting | Pass |
|---|---|
| Sounds | sound plays, role not spoken |
| Sounds and speech | sound plays, role also spoken |
| Speech only | no sound, role spoken |

- Fail: any combination where the role is neither spoken nor sounded. That is
  the bug this rebuild retires; it must not be reachable from the panel.

  One known exception, accepted and recorded in `playback.py` beside
  `PLAY_FIELD_TYPES`: a **single-line** editable field that NVDA announces
  through `speakWithinForLine` is suppressed with no sound. It did not occur
  anywhere in the #32 measurement run. If you hit it, note it — it is a known,
  accepted cost, not a new failure.

---

## 3. Settings, live

**3.1 Theme preview.** Open the panel, focus the Sound theme combo, arrow
through the entries.

- Pass: the combo's own navigation sounds change to the selected theme within
  about a third of a second of stopping. Arrowing fast does not stall NVDA's
  speech.
- Fail: NVDA stuttering or going briefly unresponsive on each keypress — that
  is the sound theme being decoded per keypress, which the debounce exists to
  prevent.

**3.2 Cancel reverts the preview.** Change the theme, then press Cancel.

- Pass: sounds return to the previous theme, and the panel shows the previous
  theme when reopened.

**3.3 Reverb.** Arrow through the Reverb combo.

- Pass: the tail of the sounds changes immediately per selection — dry on
  None, longest on Hall. No stall, no clicks.

**3.4 Reverb persists.** Choose Hall, press OK, restart NVDA.

- Pass: sounds still have the Hall tail; the panel still shows Hall.

**3.5 A user theme.** Create
`%APPDATA%\nvda\unspoken-ng\sound-themes\smoke\` and copy two WAVs from the
bundled default into it, renamed `link.wav` and `button.wav`. Reopen the panel.

- Pass: `smoke` appears in the Sound theme combo. Selecting it plays those two
  sounds for links and buttons and the bundled default's for everything else
  (sparse themes fall back per slot, silently — the log says so at info level).
- Fail: the folder not appearing; an error dialog; silence on the unprovided
  slots.

---

## 4. Devices

**4.1 Unplug.** With USB headphones or a USB DAC selected in NVDA's audio
settings, unplug it while navigating.

- Pass: NVDA's speech follows its own fallback; role sounds go quiet without a
  dialog, a spoken message, or NVDA freezing. The log has **one** warning about
  dropping plays, not one per sound.
- Fail: a freeze, a dialog, a per-sound log flood, or a crash.

**4.2 Replug.** Plug it back in.

- Pass: role sounds return, on the reconnected device, without restarting NVDA.
  The first sound after the change may be lost — that is accepted (ADR 0003).
- Fail: silence until restart.

**4.3 Default device moves.** With NVDA's output device set to the system
default, change the Windows default output device (Settings → System → Sound)
while navigating.

- Pass: role sounds follow to the new default within a sound or two. No freeze
  at the moment of the change.

**4.4 Named device chosen while absent.** Select the unplugged device in NVDA's
audio settings, navigate (silence expected), then plug it in.

- Pass: sounds start arriving on it, without a restart.

---

## 5. Degraded mode

Quit NVDA. Rename
`%APPDATA%\nvda\addons\Unspoken-ng\globalPlugins\Unspoken\soft_oal.dll` to
`soft_oal.dll.bak`. Start NVDA.

- Pass, all four:
  1. NVDA starts normally — no error dialog, no traceback dialog.
  2. A few seconds after startup, NVDA says **"Unspoken: audio unavailable,
     speaking roles instead."** and the same text appears on the braille
     display.
  3. Tabbing through a dialog **speaks** every control's role and plays no
     sounds — regardless of what Role announcement is set to.
  4. The log has one `Unspoken: running speech-only this session` error naming
     the cause.
- Fail: silent suppression (roles neither spoken nor sounded) — this is the
  failure mode the whole fallback exists to prevent. Also fail: a dialog, a
  message repeated more than once, or the saved Role announcement setting
  having been rewritten.

Restore the DLL name and restart.

- Pass: `degraded=False` in the log and sounds are back, with Role announcement
  still set to whatever it was before.

---

## 6. Migration from Unspoken 1.x

Quit NVDA. Replace the `[unspoken]` section of `%APPDATA%\nvda\nvda.ini` with a
1.x one:

```ini
[unspoken]
	sayAll = True
	speakRoles = False
	noSounds = False
	HRTF = True
	volumeAdjust = True
	Reverb = True
	RoomSize = 10
	Damping = 100
	WetLevel = 9
	DryLevel = 30
	Width = 100
```

Start NVDA, open the panel, close it, quit NVDA, reopen `nvda.ini`.

- Pass: the `[unspoken]` section now contains only `theme`,
  `roleAnnouncement = sounds`, `reverb = smallRoom`,
  `silenceDuringSayAll = True`. **All eleven legacy keys are gone from the
  file** — that is the half that only works when the migration runs against the
  raw profile section and the result is saved.
- Fail: legacy keys still present; `roleAnnouncement` missing; the new keys
  present in memory but absent from the file.

Now change a setting in the panel, press OK, restart NVDA twice.

- Pass: the setting sticks. (If the legacy keys had survived, the migration
  would overwrite the choice on the next start.)

Repeat with `noSounds = True` and no other legacy keys.

- Pass: `roleAnnouncement = speechOnly`.

---

## 7. Co-installed ancestor

Install the original **Unspoken** 1.x addon alongside this one (NVDA will
report it as incompatible and leave it disabled — that is fine and expected).
Restart NVDA.

- Pass: one warning line in the log naming the ancestor and its version, and
  nothing else: no dialog, no spoken message, no change in behaviour.
- Fail: more than one line, per-event lines, or any user-visible notice.

---

## 8. Teardown

Press `NVDA+Control+F3` (reload plugins) a few times, then navigate.

- Pass: sounds still play, exactly once each; no growing pile of duplicate
  sounds per keypress; no traceback in the log. This is what proves the two
  speech-path patches are being handed back rather than stacked.
- Fail: two or more sounds per keypress after a reload, or NVDA's role
  suppression persisting after the addon is uninstalled.

Disable the addon in the Add-on Store and restart.

- Pass: NVDA speaks control roles normally again.

---

## Recording a run

Note the NVDA version, the addon version, the date, and any item that failed.
A failure belongs in a GitHub issue on `akj/unspoken-ng` with the relevant
`nvda.log` excerpt attached — not in this file, which describes what should
happen rather than what did.
