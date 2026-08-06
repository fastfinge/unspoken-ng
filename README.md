# unspoken-ng

Unspoken for modern NVDA, using OpenAL Soft

The v1 series of unspoken-ng used steam audio. However, this required compiling C++ sourcecode to build the addon, and made the addon quite large. The v2 series now uses OpenAL Soft instead, shrinking the size of the addon, and removing the need for libverb.

## Why?

Unfortunately, previous versions of Unspoken had many serious problems due to the fact they depended on an unmaintained audio library:
* the output device of the sounds could not be changed
* after running for several hours, the audio device would begin to make a squealing sound
* When NVDA upgrades to 64-bit, or beyond Python 3.11, the library can no longer be used — which has now happened

## Requirements

* **NVDA 2026.1 or later**, 64-bit. The addon bundles the x64 build of OpenAL Soft and is marked incompatible below 2026.1; older NVDA will refuse to enable it.
* **Headphones**, for anything but the plainest use. The whole point is left/right and up/down placement, and laptop speakers will not reproduce it.
* If the original **Unspoken 1.x** is still installed, remove it. It is incompatible with this NVDA anyway, and both addons patch NVDA's speech path. Note that upgrading migrates your old settings to the four below and then deletes the legacy keys, which the ancestor also used.

## The Solution

This version of Unspoken now uses OpenAL Soft, loaded directly via ctypes (the bundled soft_oal.dll). OpenAL Soft is a well documented library, used in many applications. That means the library is battle tested, debugged, and maintained.  

## Credits

In the case of this project, I'm really just the releaser, documenter, and contact guy.  Unspoken-ng wouldn't be possible without:
* Bryan Smart: the original work on two versions of the Unspoken addon
* Masonasons: updating the Unspoken addon with the API changes in 2023 and 2024
* Ambro86: maintaining modern Python bindings for synthizer, as well as contributing some code to unspoken
* Tyler Spivey: for sitting down, figuring out steam audio, and creating Python bindings that do what we need
* AKJ, for converting everything to OpenAL Soft
* Me: for really needing this functionality, doing what I could to keep it going, and bothering other people to help with all the hard bits

## Using the addon

Once installed, the addon adds an **Unspoken-ng** category under NVDA menu → Preferences → Settings. It has four settings:

* **Sound theme** — which set of sounds to play. Ships with one, *Default*; any theme you install (see below) appears in this list. The selected theme is loaded as you browse the list, so the next control sound you hear — tabbing on through the panel, for instance — is already the new one. Cancelling the dialog puts your previous theme back.
* **Role announcement** — how control roles are announced:
  * *Sounds* (the default) — a sound instead of the spoken role
  * *Sounds and speech* — a sound **and** the spoken role, useful while you are learning the theme
  * *Speech only* — no sounds; NVDA speaks roles as it would without the addon
* **Reverb** — the space the sounds are placed in: *None*, *Small room* (the default), *Medium room*, or *Hall*. Larger spaces make position easier to hear and the sound itself slightly longer.
* **Silence role sounds during say all** — off by default. Turn it on if you find the sounds distracting while reading continuously.

If the addon cannot produce sound at all — no audio device, or a broken install — it says so once, shortly after NVDA starts, and then runs speech-only for the session. Your settings are left alone, so fixing the problem restores sounds with nothing to set again.

## Sound themes

A sound theme is just a folder of correctly-named WAV files. To install one, drop its folder into:

```
%APPDATA%\nvda\unspoken-ng\sound-themes\
```

That is the path for an installed copy of NVDA; on a portable copy it is `unspoken-ng\sound-themes\` inside the portable folder — it always sits beside your `nvda.ini`. The addon creates that folder on startup. Restart NVDA, or reopen the settings panel, and the theme appears in the **Sound theme** list. The folder name is the theme's ID.

To write one, name a file for each slot you want to cover:

`button`, `checkbox`, `clock`, `combobox`, `editabletext`, `icon`, `link`, `listitem`, `menuitem`, `radiobutton`, `slider`, `splitbutton`, `tab`, `treeviewitem`

So `button.wav`, `link.wav`, and so on. The addon maps NVDA's control roles onto these slots itself — you do not have to know which roles map where.

**Themes may be sparse.** Any slot you leave out falls back to the bundled default theme, so a theme that only replaces `button.wav` is perfectly valid.

Files must be uncompressed PCM WAV, mono or stereo, 16- or 24-bit. Any sample rate works — stereo is downmixed to mono and the rate is handled during playback. A malformed file is skipped with a line in the log rather than taking the theme down.

Optionally add a `theme.ini` next to the WAVs:

```ini
[theme]
name = My Theme
author = Your Name
description = What it sounds like
gain = 0
```

Only `name` affects what you see in the settings list. `gain` is a level trim in decibels, clamped to ±12, applied on top of the loudness normalisation the addon does across the whole theme — reach for it only if your theme sits noticeably louder or quieter than the default.

**Level-match your slots.** The addon normalises a theme as a whole, deliberately preserving the balance you chose between its sounds. The cost is that one unusually loud file sets the ceiling for everything: if the theme cannot reach the target level without that file clipping, the addon quietly turns the whole theme down rather than distorting it, and every other sound gets quieter too. A theme whose slots sit within a few dB of each other reaches the intended loudness; one with a single hot slot will not.

## Building

Build the NVDA addon using scons.  The addon bundles the official OpenAL Soft Windows x64 build (soft_oal.dll); no native code needs to be compiled.

The addon itself has no dependencies — it runs on NVDA's own Python. The off-NVDA test suite needs only pytest:

```
python -m pip install pytest
python -m pytest tests/
```

## Known Issues

If you would like to fix any of these issues, pull requests will be happily and gratefully accepted:
1. No translation support: it's unclear to me what needs to happen here. I need to make some kind of cloud account for some sort of crowd service or something?

Fixed in 2.0: Unspoken-ng used to play no sound while arrowing through some controls on the web, because a control's position was unavailable until focus moved to it and NVDA no longer moves system focus with the browse cursor. Browse-mode reading now has its own path and does not wait on focus.

## Maintenance commitment

I, Samuel Proulx AKA fastfinge, publicly commit to maintaining the currently existing functionality of all addon features present in the fastfinge/unspoken-ng repository going forward, in order to keep up with API changes to NVDA.  Should I be unable to do so, I will hire someone else to do so on my behalf.  I depend on this functionality for some critical workflows myself.  However, the addon meets my needs as it stands.  Should you wish to tackle any of the known issues above, you are warmly welcomed and invited to submit a PR.  When I accept it, I will maintain the added functionality.  But these issues do not impact my workflow, so I will not work on the above issues myself.