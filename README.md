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
* If the original **Unspoken 1.x** is still installed, remove it. It is incompatible with this NVDA anyway, and both addons patch NVDA's speech path. Note that upgrading migrates your old settings to the five below and then deletes the legacy keys, which the ancestor also used.

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

Once installed, the addon adds an **Unspoken-ng** category under NVDA menu → Preferences → Settings. It has five settings:

* **Sound theme** — which set of sounds to play. Ships with one, *Default*; any theme you install (see below) appears in this list. The selected theme is loaded as you browse the list, so the next control sound you hear — tabbing on through the panel, for instance — is already the new one. Cancelling the dialog puts your previous theme back.
* **Role announcement** — how control roles are announced:
  * *Sounds* (the default) — a sound instead of the spoken role
  * *Sounds and speech* — a sound **and** the spoken role, useful while you are learning the theme
  * *Speech only* — no sounds; NVDA speaks roles as it would without the addon
* **Reverb** — the space the sounds are placed in: *None*, *Small room* (the default), *Medium room*, or *Hall*. Larger spaces make position easier to hear and the sound itself slightly longer.
* **Sound volume** — how loud role sounds are, from 0 to 100 with a default of 100. This sits on top of NVDA's own rules: role sounds already follow *Volume of NVDA sounds*, or your voice volume when *Volume of NVDA sounds follows voice volume* is on in NVDA's Audio settings, and this slider trims relative to that. Changes apply as the slider moves, so the next control sound you hear is already at the new level.
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

`pyproject.toml` groups the tooling so [uv](https://docs.astral.sh/uv/) can fetch it on demand: `uv run --group test pytest tests/`, `uv run --group build scons`.

## Translating

Every user-visible string is gettext-wrapped, so translations are ordinary gettext catalogs, contributed by pull request:

1. Get the string catalog. Every release attaches `Unspoken-ng.pot`; or build it yourself with `scons pot` (requires [GNU gettext](https://mlocati.github.io/articles/gettext-iconv-windows.html) on your PATH, as does building the addon once translations exist).
2. Create `addon/locale/<language>/LC_MESSAGES/nvda.po` from the catalog, where `<language>` is the language code NVDA uses (`de`, `fr`, `pt_BR`, ...). A PO editor such as [Poedit](https://poedit.net/) works well, as does `msginit`.
3. Translate. The catalog includes the add-on's summary and description from `buildVars.py`; translating those two strings is what localizes the add-on's name and description in NVDA's Add-on Store and add-ons manager.
4. Optionally, translate the user guide: place a translated copy of this readme at `addon/doc/<language>/readme.md`.
5. Open a pull request with the `.po` file (and translated readme, if any). Don't commit compiled `.mo` files or generated `manifest.ini` files — the build produces those, and git ignores them.

To update an existing translation after strings change, merge the new catalog into it (`msgmerge --update nvda.po Unspoken-ng.pot`, or Poedit's "Update from POT file") and fill in what's new.

## Releasing

Publish a GitHub release whose tag is the bare version, no `v` prefix (e.g. `2.1`, matching the NVDA add-on store's `X.Y` convention) — that's the whole process. The addon version is stamped from `git describe --tags` at build time, so the tag *is* the version: CI rebuilds the addon from the tag — running the test suite and artifact verification first — and attaches the `.nvda-addon` to the release. Nothing is bumped, built, or uploaded by hand; local builds between tags get a version like `2.0-51-g3b71875`.

## Known Issues

If you would like to fix any of these issues, pull requests will be happily and gratefully accepted:
1. Translations exist in the code but not yet in practice: every user-visible string is gettext-wrapped and the build generates a `.pot` catalog, but no translation platform is wired up, so no translated catalogs ship yet. What remains is a place for translators to contribute (for instance the NVDA add-on community's translation workflow).

Fixed in 2.0: Unspoken-ng used to play no sound while arrowing through some controls on the web, because a control's position was unavailable until focus moved to it and NVDA no longer moves system focus with the browse cursor. Browse-mode reading now has its own path and does not wait on focus.

## Maintenance commitment

I, Samuel Proulx AKA fastfinge, publicly commit to maintaining the currently existing functionality of all addon features present in the fastfinge/unspoken-ng repository going forward, in order to keep up with API changes to NVDA.  Should I be unable to do so, I will hire someone else to do so on my behalf.  I depend on this functionality for some critical workflows myself.  However, the addon meets my needs as it stands.  Should you wish to tackle any of the known issues above, you are warmly welcomed and invited to submit a PR.  When I accept it, I will maintain the added functionality.  But these issues do not impact my workflow, so I will not work on the above issues myself.
