"""Discover and decode Unspoken sound themes without depending on NVDA.

`SoundThemeLibrary.load()` returns what crosses the Sound Player seam: `slot -> (frames,
source_rate)`, where **frames are always mono 16-bit little-endian PCM** and
`source_rate` is whatever the file really was (spec §4.3 -- resampling happens
below the seam, per source, in OpenAL).

The width is part of the seam, not a detail of this module. Core OpenAL has no
24-bit buffer format, so 24-bit frames handed across would be uploaded as
`AL_FORMAT_MONO16` and rendered as full-scale broadband noise -- with no error
reported by anything, into the headphones of a user who cannot see what
happened. Assets may be 16- or 24-bit (spec §7); this module is where that
stops being true.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import logging
import math
from pathlib import Path
import struct
import wave


log = logging.getLogger(__name__)

_SLOTS = (
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
)
#: The shipped whole-theme normalization level. The loudness rig can choose a
#: different value through `SoundThemeLibrary` without mutating module state.
REFERENCE_RMS_DBFS = -20.0
#: The persisted ID shared with `settings.DEFAULTS["theme"]`. This pure stdlib
#: module is also loaded standalone by the rigs, so it cannot import settings.
DEFAULT_THEME_ID = "default"
#: An untranslated placeholder used only when the bundled default is unreadable.
_DEFAULT_THEME_NAME = "Default"
#: The seam's PCM format: mono 16-bit little-endian, whatever the asset was.
#: The struct code is the single source of truth -- the width and the sample
#: bounds are derived from it, so the packing and the clamping cannot desync.
_OUTPUT_FORMAT_CHAR = "h"
_OUTPUT_SAMPLE_WIDTH = struct.calcsize(f"<{_OUTPUT_FORMAT_CHAR}")
_OUTPUT_FULL_SCALE = float(1 << (_OUTPUT_SAMPLE_WIDTH * 8 - 1))
_OUTPUT_MINIMUM = -(1 << (_OUTPUT_SAMPLE_WIDTH * 8 - 1))
_OUTPUT_MAXIMUM = (1 << (_OUTPUT_SAMPLE_WIDTH * 8 - 1)) - 1
#: The sound themes shipped inside the addon.
BUNDLED_THEMES_DIR = Path(__file__).resolve().parent / "sound-themes"


@dataclass
class ThemeInfo:
    id: str
    name: str
    path: Path
    author: str | None = None
    description: str | None = None


@dataclass
class _Manifest:
    name: str
    author: str | None = None
    description: str | None = None
    gain_db: float = 0.0


@dataclass
class _DecodedWav:
    samples: list[int]
    sample_width: int
    source_rate: int


class SoundThemeLibrary:
    """The sound themes this installation can offer, and their decoded samples.

    Constructed with the two directories it reads -- the bundled one inside the
    addon and the user's, or None for "no user themes" -- so there is no
    ordering to get wrong: an instance that exists knows where to look. Every
    "no usable theme" fallback lives here (#66, ADR 0005); nothing above it
    has one of its own.

    `reference_rms_dbfs` is the level a whole theme is normalised to. The
    loudness rig (`tools/audition_loudness.py`) is the only caller that passes
    anything but the shipped value, and it does so here rather than by reaching
    into module privates.
    """

    def __init__(
        self,
        bundled_dir,
        user_dir,
        *,
        reference_rms_dbfs=REFERENCE_RMS_DBFS,
    ):
        self._bundled_dir = Path(bundled_dir)
        self._user_dir = None if user_dir is None else Path(user_dir)
        self._reference_rms_dbfs = reference_rms_dbfs

    def discover(self) -> list[ThemeInfo]:
        """Return the usable sound themes by ID; best-effort and never empty.

        The user directory is created on first discovery. A usable user folder
        wins an ID collision; otherwise the bundled folder with that ID does.
        The bundled default is always in the list even when its folder is
        missing or unreadable, because an empty combo box is a dead end for a
        keyboard user and `load` answers for that ID whatever state the folder
        is in. That is why the settings panel synthesises nothing.
        """

        try:
            candidates: dict[str, list[Path]] = {}
            for path in _theme_directories(self._bundled_dir):
                candidates[path.name] = [path]

            if self._user_dir is not None:
                try:
                    self._user_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    log.warning(
                        "Could not create user sound-themes directory %s",
                        self._user_dir,
                        exc_info=True,
                    )
                else:
                    for path in _theme_directories(self._user_dir):
                        candidates.setdefault(path.name, []).insert(0, path)

            discovered = []
            for theme_id in sorted(candidates):
                for path in candidates[theme_id]:
                    try:
                        manifest = _read_manifest(path)
                        if not _theme_has_usable_slot(path):
                            continue
                        discovered.append(
                            ThemeInfo(
                                id=theme_id,
                                name=manifest.name,
                                path=path,
                                author=manifest.author,
                                description=manifest.description,
                            )
                        )
                        break
                    except Exception:
                        log.warning(
                            "Skipping malformed sound theme folder %s",
                            path,
                            exc_info=True,
                        )
            if not any(info.id == DEFAULT_THEME_ID for info in discovered):
                discovered.append(self._default_entry())
            return sorted(discovered, key=lambda info: info.id)
        except Exception:
            log.warning("Sound theme discovery failed", exc_info=True)
            return [self._default_entry()]

    def _default_entry(self) -> ThemeInfo:
        path = self._bundled_dir / DEFAULT_THEME_ID
        name = _read_manifest(path).name
        return ThemeInfo(
            id=DEFAULT_THEME_ID,
            name=_DEFAULT_THEME_NAME if name == DEFAULT_THEME_ID else name,
            path=path,
        )

    def load(self, theme_id: str) -> dict[str, tuple[bytes, int]]:
        """Load a theme, structurally filling its missing slots from the default.

        The merge is not a recovery path that can be skipped: a theme that
        decodes to nothing gets every slot from the default. This returns `{}`
        only when the bundled default itself is unusable. That emptiness is
        load-bearing: `GlobalPlugin` counts the slots and
        `playback.can_produce_role_sound` turns zero into degraded mode, so it
        is deliberately not papered over here.

        The samples are mono 16-bit little-endian PCM frames paired with their
        true source rate, whatever width the asset was authored at.
        """

        try:
            requested_path = self._find_requested(theme_id)
            if requested_path is None:
                log.warning(
                    "Sound theme %r was not found; using the bundled default",
                    theme_id,
                )
                return self._load_default()
            # Equal paths stop the bundled default being decoded twice on startup.
            if requested_path == _find_theme(self._bundled_dir, DEFAULT_THEME_ID):
                return _load_processed_theme(
                    requested_path,
                    self._reference_rms_dbfs,
                )
            return _merge_over_default(
                _load_processed_theme(requested_path, self._reference_rms_dbfs),
                self._load_default(),
                theme_id,
            )
        except Exception:
            log.warning("Could not load sound theme %r", theme_id, exc_info=True)
            return {}

    def _load_default(self) -> dict[str, tuple[bytes, int]]:
        path = _find_theme(self._bundled_dir, DEFAULT_THEME_ID)
        if path is None:
            log.warning("The bundled default sound theme is unavailable")
            return {}
        return _load_processed_theme(path, self._reference_rms_dbfs)

    def _find_requested(self, theme_id: str) -> Path | None:
        user_theme = _find_theme(self._user_dir, theme_id)
        if user_theme is not None:
            return user_theme
        return _find_theme(self._bundled_dir, theme_id)


def _merge_over_default(
    requested: dict[str, tuple[bytes, int]],
    default: dict[str, tuple[bytes, int]],
    theme_id: str,
) -> dict[str, tuple[bytes, int]]:
    """Fill the slots `requested` has no usable sample for; one log line per filled slot."""

    merged = dict(requested)
    for slot in _SLOTS:
        if slot in requested:
            continue
        if slot in default:
            log.info(
                "Sound theme %r has no usable %s slot; falling back to default",
                theme_id,
                slot,
            )
            merged[slot] = default[slot]
    return merged


def _theme_directories(root: Path) -> list[Path]:
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        return []
    except Exception:
        log.warning("Could not inspect sound-themes directory %s", root, exc_info=True)
        return []

    directories = []
    for entry in entries:
        try:
            if entry.is_dir():
                directories.append(entry)
        except Exception:
            log.warning("Could not inspect sound theme candidate %s", entry, exc_info=True)
    return directories


def _find_theme(root: Path | None, theme_id: str) -> Path | None:
    if root is None:
        return None
    for path in _theme_directories(root):
        if path.name == theme_id:
            return path
    return None


def _read_manifest(theme_path: Path) -> _Manifest:
    fallback = _Manifest(name=theme_path.name)
    manifest_path = theme_path / "theme.ini"

    try:
        if not manifest_path.is_file():
            return fallback
        parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            parser.read_file(manifest_file)
        if not parser.has_section("theme"):
            raise configparser.Error("missing [theme] section")
    except Exception:
        log.warning(
            "Ignoring malformed sound theme manifest %s",
            manifest_path,
            exc_info=True,
        )
        return fallback

    section = parser["theme"]

    def read_text(key: str, default: str) -> str:
        try:
            return section.get(key, default).strip()
        except Exception:
            log.warning(
                "Ignoring invalid %s field in sound theme manifest %s",
                key,
                manifest_path,
                exc_info=True,
            )
            return default

    try:
        gain_db = float(section.get("gain", "0"))
        if not math.isfinite(gain_db):
            raise ValueError("gain must be finite")
    except Exception:
        log.warning(
            "Ignoring invalid gain field in sound theme manifest %s",
            manifest_path,
            exc_info=True,
        )
        gain_db = 0.0

    return _Manifest(
        name=read_text("name", theme_path.name) or theme_path.name,
        author=read_text("author", "") or None,
        description=read_text("description", "") or None,
        gain_db=gain_db,
    )


def _theme_has_usable_slot(theme_path: Path) -> bool:
    for slot in _SLOTS:
        wav_path = theme_path / f"{slot}.wav"
        try:
            exists = wav_path.is_file()
        except Exception:
            log.warning("Could not inspect sound theme file %s", wav_path, exc_info=True)
            continue
        if exists and _has_usable_wav_header(wav_path):
            return True
    return False


def _has_usable_wav_header(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            source_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            if wav_file.getcomptype() != "NONE":
                raise ValueError("compressed WAV data is not supported")
            if channels not in (1, 2):
                raise ValueError(f"unsupported channel count: {channels}")
            if sample_width not in (2, 3):
                raise ValueError(f"unsupported sample width: {sample_width}")
            if source_rate <= 0:
                raise ValueError(f"invalid sample rate: {source_rate}")
            if frame_count <= 0:
                raise ValueError("WAV contains no audio frames")
        return True
    except Exception:
        log.warning("Rejecting malformed sound theme WAV %s", path, exc_info=True)
        return False


def _read_theme_wavs(theme_path: Path) -> dict[str, _DecodedWav]:
    decoded = {}
    for slot in _SLOTS:
        wav_path = theme_path / f"{slot}.wav"
        try:
            exists = wav_path.is_file()
        except Exception:
            log.warning("Could not inspect sound theme file %s", wav_path, exc_info=True)
            continue
        if not exists:
            continue
        wav = _decode_wav(wav_path)
        if wav is not None:
            decoded[slot] = wav
    return decoded


def _decode_wav(path: Path) -> _DecodedWav | None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            source_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            if wav_file.getcomptype() != "NONE":
                raise ValueError("compressed WAV data is not supported")
            if channels not in (1, 2):
                raise ValueError(f"unsupported channel count: {channels}")
            if sample_width not in (2, 3):
                raise ValueError(f"unsupported sample width: {sample_width}")
            if source_rate <= 0:
                raise ValueError(f"invalid sample rate: {source_rate}")

            frames = wav_file.readframes(frame_count)

        expected_size = frame_count * channels * sample_width
        if len(frames) != expected_size:
            raise ValueError(
                f"truncated WAV data: expected {expected_size} bytes, got {len(frames)}"
            )
        if not frames:
            raise ValueError("WAV contains no audio frames")

        if sample_width == 2:
            samples = [sample[0] for sample in struct.iter_unpack("<h", frames)]
        else:
            samples = [
                _decode_24_bit(frames[offset : offset + 3])
                for offset in range(0, len(frames), 3)
            ]

        if channels == 2:
            samples = [
                _average_samples(samples[index], samples[index + 1])
                for index in range(0, len(samples), 2)
            ]

        return _DecodedWav(samples, sample_width, source_rate)
    except Exception:
        log.warning("Rejecting malformed sound theme WAV %s", path, exc_info=True)
        return None


def _decode_24_bit(sample: bytes) -> int:
    return int.from_bytes(sample, byteorder="little", signed=True)


def _average_samples(left: int, right: int) -> int:
    # Python integers cannot overflow; round .5 ties to the nearest even value
    # so positive and negative stereo pairs receive symmetric treatment.
    return round((left + right) / 2)


def _load_processed_theme(
    theme_path: Path,
    reference_rms_dbfs: float,
) -> dict[str, tuple[bytes, int]]:
    try:
        manifest = _read_manifest(theme_path)
        decoded = _read_theme_wavs(theme_path)
        return _process_theme(
            decoded,
            manifest.gain_db,
            theme_path.name,
            reference_rms_dbfs,
        )
    except Exception:
        log.warning("Could not process sound theme %s", theme_path, exc_info=True)
        return {}


def _process_theme(
    decoded: dict[str, _DecodedWav],
    manifest_gain_db: float,
    theme_id: str,
    reference_rms_dbfs: float = REFERENCE_RMS_DBFS,
) -> dict[str, tuple[bytes, int]]:
    if not decoded:
        return {}

    square_sum = 0.0
    sample_count = 0
    for wav in decoded.values():
        full_scale = float(1 << (wav.sample_width * 8 - 1))
        square_sum += math.fsum(
            (sample / full_scale) ** 2 for sample in wav.samples
        )
        sample_count += len(wav.samples)

    rms = math.sqrt(square_sum / sample_count) if sample_count else 0.0
    if rms:
        reference_rms = 10.0 ** (reference_rms_dbfs / 20.0)
        normalization_factor = reference_rms / rms
        clamped_gain_db = max(-12.0, min(12.0, manifest_gain_db))
        if clamped_gain_db != manifest_gain_db:
            log.info(
                "Clamped sound theme %r manifest gain from %s dB to %s dB",
                theme_id,
                manifest_gain_db,
                clamped_gain_db,
            )
        gain_factor = normalization_factor * (10.0 ** (clamped_gain_db / 20.0))
    else:
        log.info("Sound theme %r is silent; skipping RMS normalization", theme_id)
        gain_factor = 1.0

    gain_factor = _limit_to_full_scale(decoded, gain_factor, theme_id)

    processed = {}
    clipped_sample_count = 0
    peak_overshoot_ratio = 1.0
    for slot, wav in decoded.items():
        source_full_scale = float(1 << (wav.sample_width * 8 - 1))
        # One conversion, at the end: the theme gain and the width change are a
        # single floating-point scale, so a 24-bit asset keeps its full
        # resolution through the RMS pass above and is quantized exactly once.
        # Clamping happens only here, so there is no intermediate overflow and
        # nothing clips twice.
        scale = gain_factor * _OUTPUT_FULL_SCALE / source_full_scale
        samples = []
        for sample in wav.samples:
            scaled = sample * scale
            if scaled < _OUTPUT_MINIMUM or scaled > _OUTPUT_MAXIMUM:
                clipped_sample_count += 1
                peak_overshoot_ratio = max(
                    peak_overshoot_ratio,
                    abs(scaled) / _OUTPUT_FULL_SCALE,
                )
            samples.append(max(_OUTPUT_MINIMUM, min(_OUTPUT_MAXIMUM, round(scaled))))
        processed[slot] = (_encode_samples(samples), wav.source_rate)
    if clipped_sample_count:
        peak_overshoot_db = 20.0 * math.log10(peak_overshoot_ratio)
        log.warning(
            "Sound theme %r clipped %d samples; peak overshoot %.2f dB",
            theme_id,
            clipped_sample_count,
            peak_overshoot_db,
        )
    return processed


def _limit_to_full_scale(
    decoded: dict[str, _DecodedWav],
    gain_factor: float,
    theme_id: str,
) -> float:
    """Back the theme gain off if the loudness target would not fit (#57).

    A loudness target and a full-scale ceiling cannot both be honoured by a
    theme without the crest headroom to reach the target: something has to
    give. Backing the gain off is strictly better than clipping, which
    truncates the waveform and distorts audibly, so the reference level is an
    upper bound rather than a promise.

    This is the addon's business rather than the theme author's, because it is
    the pooled normalization that creates the overshoot: one hot slot lifts the
    whole theme's peak while barely moving its RMS. The bundled theme is the
    worked example -- `combobox.wav` sits ~13 dB above the set's median, and at
    the shipped -20 dBFS reference it took four slots over full scale.

    The bound is `_OUTPUT_MAXIMUM`, not `_OUTPUT_FULL_SCALE`: two's complement
    has one more code below zero than above it, and rounding a peak that landed
    exactly on full scale would clip the positive side by one.
    """
    peak_ratio = 0.0
    for wav in decoded.values():
        source_full_scale = float(1 << (wav.sample_width * 8 - 1))
        for sample in wav.samples:
            magnitude = abs(sample) / source_full_scale
            if magnitude > peak_ratio:
                peak_ratio = magnitude

    if peak_ratio <= 0.0:
        return gain_factor

    ceiling = _OUTPUT_MAXIMUM / _OUTPUT_FULL_SCALE
    headroom = ceiling / (peak_ratio * gain_factor)
    if headroom >= 1.0:
        return gain_factor

    log.info(
        "Sound theme %r would peak %.2f dB over full scale at the reference "
        "level; backing its gain off by that much instead of clipping",
        theme_id,
        -20.0 * math.log10(headroom),
    )
    return gain_factor * headroom


def _encode_samples(samples: list[int]) -> bytes:
    """Pack into the seam's one format: mono 16-bit little-endian PCM."""

    return struct.pack(f"<{len(samples)}{_OUTPUT_FORMAT_CHAR}", *samples)
