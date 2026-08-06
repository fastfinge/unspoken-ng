from pathlib import Path, PurePosixPath
import zipfile


CANONICAL_SLOTS = {
    "button",
    "checkbox",
    "clock",
    "combobox",
    "editabletext",
    "icon",
    "link",
    "listitem",
    "menuitem",
    "radiobutton",
    "slider",
    "splitbutton",
    "tab",
    "treeviewitem",
}
THEME_PREFIX = "globalPlugins/Unspoken/sound-themes/default/"
MODULE_PREFIX = "globalPlugins/Unspoken/"
REQUIRED_FILES = {
    "globalPlugins/Unspoken/soft_oal.dll",
    f"{THEME_PREFIX}theme.ini",
    # Every module the plugin imports. A module that stops being packaged is a
    # crash on the first NVDA start after release, not a test failure.
    f"{MODULE_PREFIX}__init__.py",
    f"{MODULE_PREFIX}addonGui.py",
    f"{MODULE_PREFIX}debounce.py",
    f"{MODULE_PREFIX}migration.py",
    f"{MODULE_PREFIX}playback.py",
    f"{MODULE_PREFIX}player.py",
    f"{MODULE_PREFIX}preview.py",
    f"{MODULE_PREFIX}roles.py",
    f"{MODULE_PREFIX}spatial.py",
    f"{MODULE_PREFIX}themes.py",
    f"{MODULE_PREFIX}volume.py",
}
#: Retired modules. Shipping one again would mean a stale tree, not a new
#: file: openal_audio.py went with #38, wiring.py dissolved into playback.py,
#: debounce.py and volume.py with #64.
FORBIDDEN_FILES = {
    f"{MODULE_PREFIX}openal_audio.py",
    f"{MODULE_PREFIX}wiring.py",
}


def main():
    artifacts = sorted(Path.cwd().glob("*.nvda-addon"))
    if len(artifacts) != 1:
        raise AssertionError(
            f"Expected exactly one *.nvda-addon artifact, found {len(artifacts)}: "
            f"{artifacts}"
        )

    artifact = artifacts[0]
    with zipfile.ZipFile(artifact) as bundle:
        entries = set(bundle.namelist())

    missing_files = REQUIRED_FILES - entries
    if missing_files:
        raise AssertionError(f"Missing required files: {sorted(missing_files)}")

    shipped_forbidden = FORBIDDEN_FILES & entries
    if shipped_forbidden:
        raise AssertionError(f"Retired files still shipped: {sorted(shipped_forbidden)}")

    expected_wavs = {
        f"{THEME_PREFIX}{slot}.wav" for slot in CANONICAL_SLOTS
    }
    actual_wavs = {
        entry
        for entry in entries
        if entry.startswith(THEME_PREFIX)
        and entry.endswith(".wav")
        and "/" not in entry.removeprefix(THEME_PREFIX)
    }
    if actual_wavs != expected_wavs:
        raise AssertionError(
            "Canonical slot WAV mismatch: "
            f"missing={sorted(expected_wavs - actual_wavs)}, "
            f"unexpected={sorted(actual_wavs - expected_wavs)}"
        )

    pyc_entries = {
        entry for entry in entries if entry.lower().endswith(".pyc")
    }
    if pyc_entries:
        raise AssertionError(f"Bundle contains .pyc files: {sorted(pyc_entries)}")

    pycache_entries = {
        entry
        for entry in entries
        if "__pycache__" in PurePosixPath(entry).parts
    }
    if pycache_entries:
        raise AssertionError(
            f"Bundle contains __pycache__ content: {sorted(pycache_entries)}"
        )

    print(f"Artifact: {artifact}")
    print("Required files:")
    for entry in sorted(REQUIRED_FILES):
        print(f"  {entry}")
    print(f"Canonical slot WAVs ({len(actual_wavs)}):")
    for entry in sorted(actual_wavs):
        print(f"  {entry}")
    print(f"Retired files absent: {sorted(FORBIDDEN_FILES)}")
    print(f".pyc entries: {len(pyc_entries)}")
    print(f"__pycache__ entries: {len(pycache_entries)}")
    print("Artifact verification passed.")


if __name__ == "__main__":
    main()
