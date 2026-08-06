import logging
import math
from pathlib import Path
import struct
import wave

import pytest

import themes


def _encode_24_bit(samples):
    encoded = bytearray()
    for sample in samples:
        if sample < 0:
            sample += 1 << 24
        encoded.extend(
            (
                sample & 0xFF,
                (sample >> 8) & 0xFF,
                (sample >> 16) & 0xFF,
            )
        )
    return bytes(encoded)


def _write_wav(path, samples, *, channels=1, sample_width=2, rate=22050):
    path.parent.mkdir(parents=True, exist_ok=True)
    if sample_width == 2:
        frames = struct.pack(f"<{len(samples)}h", *samples)
    elif sample_width == 3:
        frames = _encode_24_bit(samples)
    else:
        frames = bytes(samples)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(rate)
        wav_file.writeframes(frames)


def _decode_pcm(frames, sample_width):
    if sample_width == 2:
        return [sample[0] for sample in struct.iter_unpack("<h", frames)]

    samples = []
    for offset in range(0, len(frames), 3):
        chunk = frames[offset : offset + 3]
        value = chunk[0] | (chunk[1] << 8) | (chunk[2] << 16)
        if value & 0x800000:
            value -= 1 << 24
        samples.append(value)
    return samples


def _rms_dbfs(samples, sample_width):
    full_scale = 1 << (sample_width * 8 - 1)
    rms = math.sqrt(sum((sample / full_scale) ** 2 for sample in samples) / len(samples))
    return 20 * math.log10(rms)


@pytest.fixture
def theme_roots(tmp_path):
    bundled = tmp_path / "bundled" / "sound-themes"
    user = tmp_path / "user" / "unspoken-ng" / "sound-themes"
    bundled.mkdir(parents=True)
    return bundled, user


@pytest.fixture
def library(theme_roots):
    bundled, user = theme_roots
    return themes.SoundThemeLibrary(bundled, user)


def test_sparse_theme_merges_over_bundled_default(theme_roots, library):
    bundled, user = theme_roots
    default = bundled / "default"
    sparse = user / "sparse"
    _write_wav(default / "button.wav", [-1000, 1000])
    _write_wav(default / "link.wav", [-2000, 2000])
    _write_wav(sparse / "button.wav", [-3000, 3000])

    default_result = library.load("default")
    sparse_result = library.load("sparse")

    assert set(sparse_result) == {"button", "link"}
    assert sparse_result["link"] == default_result["link"]
    assert sparse_result["button"] != default_result["button"]


def test_stereo_is_downmixed_to_mono_before_normalization(theme_roots, library):
    _, user = theme_roots
    # Interleaved frames average to [2000, 0, -2000].
    _write_wav(
        user / "stereo" / "button.wav",
        [1000, 3000, 1000, -1000, -3000, -1000],
        channels=2,
        rate=44100,
    )

    frames, source_rate = library.load("stereo")["button"]
    samples = _decode_pcm(frames, 2)

    assert source_rate == 44100
    assert len(samples) == 3
    assert samples[1] == 0
    assert samples[0] == -samples[2]
    assert _rms_dbfs(samples, 2) == pytest.approx(-20.0, abs=0.01)


@pytest.mark.parametrize(
    ("manifest_gain", "effective_gain"),
    [
        (6.0, 6.0),
        (20.0, 12.0),
        (-50.0, -12.0),
    ],
)
def test_rms_normalization_and_manifest_gain_clamp(
    theme_roots,
    library,
    manifest_gain,
    effective_gain,
):
    _, user = theme_roots
    theme = user / f"gain-{manifest_gain}"
    _write_wav(theme / "button.wav", [-1000, 1000] * 8)
    (theme / "theme.ini").write_text(
        f"[theme]\ngain = {manifest_gain}\n",
        encoding="utf-8",
    )

    frames, _ = library.load(theme.name)["button"]
    samples = _decode_pcm(frames, 2)

    assert _rms_dbfs(samples, 2) == pytest.approx(
        -20.0 + effective_gain,
        abs=0.01,
    )


def test_manifest_gain_allows_trailing_inline_comment(theme_roots, library):
    _, user = theme_roots
    theme = user / "inline-comment"
    _write_wav(theme / "button.wav", [-1000, 1000] * 8)
    (theme / "theme.ini").write_text(
        "[theme]\n"
        "name = Inline Comment\n"
        "gain = -2.5        ; optional dB offset, applied after auto normalization\n",
        encoding="utf-8",
    )

    assert themes._read_manifest(theme).gain_db == -2.5

    frames, _ = library.load(theme.name)["button"]
    samples = _decode_pcm(frames, 2)
    assert _rms_dbfs(samples, 2) == pytest.approx(-22.5, abs=0.01)


def test_malformed_wavs_are_rejected_and_fall_back(theme_roots, library):
    bundled, user = theme_roots
    default = bundled / "default"
    broken = user / "broken"
    _write_wav(default / "button.wav", [-1200, 1200])
    _write_wav(default / "link.wav", [-2400, 2400])
    _write_wav(broken / "icon.wav", [-500, 500])
    _write_wav(broken / "button.wav", [128, 128], sample_width=1)
    (broken / "link.wav").write_bytes(b"not a wav")

    default_result = library.load("default")
    broken_result = library.load("broken")

    assert broken_result["button"] == default_result["button"]
    assert broken_result["link"] == default_result["link"]
    assert "icon" in broken_result
    assert "broken" in {info.id for info in library.discover()}


@pytest.mark.parametrize("bad_gain", ["loud", "nan"])
def test_bad_manifest_gain_preserves_valid_metadata(theme_roots, library, bad_gain):
    _, user = theme_roots
    theme = user / f"bad-manifest-{bad_gain}"
    _write_wav(theme / "button.wav", [-1000, 1000])
    (theme / "theme.ini").write_text(
        "[theme]\n"
        "name = Preserved Name\n"
        "author = Somebody\n"
        "description = Still valid\n"
        f"gain = {bad_gain}\n",
        encoding="utf-8",
    )

    info = next(info for info in library.discover() if info.id == theme.name)
    result = library.load(theme.name)

    assert info.name == "Preserved Name"
    assert info.author == "Somebody"
    assert info.description == "Still valid"
    assert "button" in result
    samples = _decode_pcm(result["button"][0], 2)
    assert _rms_dbfs(samples, 2) == pytest.approx(-20.0, abs=0.01)


def test_structurally_bad_manifest_falls_back_to_folder_metadata(theme_roots, library):
    _, user = theme_roots
    theme = user / "broken-ini"
    _write_wav(theme / "button.wav", [-1000, 1000])
    (theme / "theme.ini").write_text(
        "[theme\nname = Should Not Be Used\n",
        encoding="utf-8",
    )

    info = next(info for info in library.discover() if info.id == theme.name)

    assert info.name == theme.name
    assert info.author is None
    assert info.description is None


def test_discover_creates_user_dir_drops_empty_and_prefers_user(theme_roots, library):
    bundled, user = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])
    _write_wav(bundled / "shared" / "button.wav", [-1000, 1000])

    assert not user.exists()
    first_result = library.discover()
    assert user.is_dir()
    assert {info.id for info in first_result} == {"default", "shared"}

    _write_wav(user / "normal" / "link.wav", [-1000, 1000])
    _write_wav(user / "shared" / "button.wav", [-1000, 1000])
    (user / "shared" / "theme.ini").write_text(
        "[theme]\nname = User Shared\n",
        encoding="utf-8",
    )
    (user / "empty").mkdir()
    (user / "empty" / "theme.ini").write_text(
        "[theme]\nname = Empty\n",
        encoding="utf-8",
    )

    result = {info.id: info for info in library.discover()}

    assert set(result) == {"default", "normal", "shared"}
    assert result["shared"].name == "User Shared"
    assert result["shared"].path == user / "shared"


def test_discovery_always_offers_the_bundled_default(theme_roots, library):
    discovered = library.discover()

    assert [info.id for info in discovered] == ["default"]
    assert discovered[0].name == "Default"


def test_a_broken_default_still_appears_beside_the_user_themes(
    theme_roots,
    library,
):
    _, user = theme_roots
    _write_wav(user / "retro" / "button.wav", [-1000, 1000])

    assert [info.id for info in library.discover()] == ["default", "retro"]


def test_a_broken_default_loads_to_nothing_so_the_session_degrades(library):
    assert library.load("default") == {}
    assert library.load("anything") == {}

    import playback

    assert not playback.can_produce_role_sound(
        {"engine_ready": True, "device_open": True, "slots_loaded": 0}
    )


def test_discover_uses_bundled_collision_when_user_theme_is_unusable(
    theme_roots,
    library,
):
    bundled, user = theme_roots
    bundled_default = bundled / "default"
    _write_wav(bundled_default / "button.wav", [-1000, 1000])
    (user / "default").mkdir(parents=True)

    result = {info.id: info for info in library.discover()}

    assert result["default"].path == bundled_default
    assert "button" in library.load("default")


def test_discover_checks_wav_headers_without_reading_frames(
    theme_roots,
    library,
    monkeypatch,
):
    bundled, _ = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])

    def fail_if_called(self, frame_count):
        raise AssertionError("discover() must not read WAV frames")

    monkeypatch.setattr(wave.Wave_read, "readframes", fail_if_called)

    assert [info.id for info in library.discover()] == ["default"]


def test_24_bit_pcm_is_converted_to_the_seam_width(theme_roots, library):
    """A 24-bit asset must cross the seam as 16-bit, scaled, not reinterpreted.

    Core OpenAL has no 24-bit format: 3-byte frames handed to the Sound Player
    are uploaded as `AL_FORMAT_MONO16` and rendered as full-scale noise, and
    nothing reports an error. The conversion belongs here, after the gain
    stage, so the RMS pass still sees the asset's full resolution.
    """
    _, user = theme_roots
    _write_wav(
        user / "twenty-four-bit" / "button.wav",
        [-1_000_000, 500_000, 1_000_000, -500_000],
        sample_width=3,
        rate=48000,
    )

    frames, source_rate = library.load("twenty-four-bit")["button"]
    samples = _decode_pcm(frames, 2)

    assert source_rate == 48000, "the true rate still crosses the seam"
    assert len(frames) == len(samples) * 2, "two bytes per sample, not three"
    assert _rms_dbfs(samples, 2) == pytest.approx(-20.0, abs=0.01)
    assert max(abs(sample) for sample in samples) <= 32767
    # Normalized RMS of the input is 0.094243, so the theme gain is
    # 0.1 / 0.094243 = 1.061079; the width change divides by 2**8.
    # 1_000_000 * 1.061079 / 256 = 4144.84 -> 4145.
    assert samples == [-4145, 2072, 4145, -2072]
    # Intra-theme dynamics survive: the quiet samples stay half the loud ones.
    assert samples[0] == -samples[2]
    assert abs(samples[0]) == pytest.approx(2 * abs(samples[1]), rel=1e-3)


def test_every_slot_crosses_the_seam_as_mono_16_bit(theme_roots, library):
    """The seam has one width, whatever mixture of assets a theme is made of.

    Byte counts are the assertion, because they are what a width change moves:
    a 24-bit slot that stayed 24-bit is 50% longer, and a stereo slot that was
    not downmixed is twice as long. Anything weaker (an even length, a
    round-trip through the same decoder) is true of any byte string at all.
    """
    _, user = theme_roots
    mixed = user / "mixed-widths"
    _write_wav(mixed / "button.wav", [-1000, 500, 1000], sample_width=2, rate=44100)
    _write_wav(
        mixed / "link.wav",
        [-1_000_000, 500_000, 1_000_000, -500_000],
        sample_width=3,
        rate=48000,
    )
    # Six interleaved values: three stereo frames, so three mono samples.
    _write_wav(
        mixed / "tab.wav",
        [1000, 3000, -1000, -3000, 2000, 4000],
        channels=2,
        sample_width=2,
        rate=22050,
    )

    loaded = library.load("mixed-widths")

    assert {"button", "link", "tab"} <= set(loaded)
    assert {slot: len(loaded[slot][0]) for slot in ("button", "link", "tab")} == {
        "button": 3 * 2,  # 3 mono samples, was already 16-bit
        "link": 4 * 2,  # 4 mono samples, was 24-bit: 12 bytes if unconverted
        "tab": 3 * 2,  # 3 mono samples, was 6 stereo values
    }
    assert {slot: loaded[slot][1] for slot in ("button", "link", "tab")} == {
        "button": 44100,
        "link": 48000,
        "tab": 22050,
    }, "the true source rate still crosses the seam"


def test_the_width_change_happens_after_the_gain_stage(theme_roots, library):
    """Quiet 24-bit detail must survive normalization, not be quantized away.

    The conversion is folded into the gain scale and applied once, at the end,
    so the RMS pass sees the asset's full 24-bit resolution. Converting first
    would round these samples to [-1, 0, 1, 0] before the gain ever ran: the
    quiet samples would come out silent and the loud ones full of quantization
    error. With this fixture the two orderings disagree -- correct gives
    [-4396, 1465, 4396, -1465], keeping the 3:1 ratio the asset had; converting
    early gives [-4634, 0, 4634, 0].
    """
    _, user = theme_roots
    _write_wav(
        user / "quiet-24-bit" / "button.wav",
        [-300, 100, 300, -100],
        sample_width=3,
        rate=48000,
    )

    frames, _ = library.load("quiet-24-bit")["button"]
    samples = _decode_pcm(frames, 2)

    assert samples == [-4396, 1465, 4396, -1465]
    assert abs(samples[0]) == pytest.approx(3 * abs(samples[1]), rel=1e-3)
    assert _rms_dbfs(samples, 2) == pytest.approx(-20.0, abs=0.01)


def test_the_reference_level_is_a_constructor_choice(theme_roots):
    bundled, user = theme_roots
    _write_wav(user / "level" / "button.wav", [-1000, 1000] * 8)
    louder = themes.SoundThemeLibrary(
        bundled,
        user,
        reference_rms_dbfs=-20.0,
    ).load("level")
    quieter = themes.SoundThemeLibrary(
        bundled,
        user,
        reference_rms_dbfs=-30.0,
    ).load("level")

    louder_rms = _rms_dbfs(_decode_pcm(louder["button"][0], 2), 2)
    quieter_rms = _rms_dbfs(_decode_pcm(quieter["button"][0], 2), 2)
    assert louder_rms - quieter_rms == pytest.approx(10.0, abs=0.01)


def test_a_user_default_folder_still_merges_the_bundled_default(
    theme_roots,
    library,
):
    bundled, user = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])
    _write_wav(bundled / "default" / "link.wav", [-2000, 2000])
    _write_wav(bundled / "default" / "tab.wav", [-3000, 3000])
    _write_wav(user / "default" / "button.wav", [-500, 0, 500])

    bundled_default = themes.SoundThemeLibrary(bundled, None).load("default")
    loaded = library.load("default")

    assert set(loaded) == {"button", "link", "tab"}
    assert loaded["button"] != bundled_default["button"]
    assert loaded["link"] == bundled_default["link"]
    assert loaded["tab"] == bundled_default["tab"]


def test_merge_over_default_fills_only_missing_slots():
    requested = {"button": (b"requested-button", 22050)}
    default = {
        "button": (b"default-button", 22050),
        "link": (b"default-link", 48000),
    }

    assert themes._merge_over_default(requested, default, "sparse") == {
        "button": requested["button"],
        "link": default["link"],
    }


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        (b"", 0),
        (b"\x01", 1),
        (b"\x40\xe2\x01", 123456),
        (b"\xff\xff\x7f", 8388607),
        (b"\x00\x00\x80", -8388608),
        (b"\xff\xff\xff", -1),
    ],
)
def test_decode_24_bit_sample_vectors(encoded, expected):
    assert themes._decode_24_bit(encoded) == expected


#: One very loud sample among a thousand silent ones: a huge peak that barely
#: moves the pooled RMS, so normalizing to the reference level asks for far
#: more gain than the waveform has headroom for. This is the shape of #57.
_PEAKY_THEME = {
    "button": themes._DecodedWav(
        samples=[32767] + [0] * 999,
        sample_width=2,
        source_rate=22050,
    ),
    "link": themes._DecodedWav(
        samples=[0] * 1000,
        sample_width=2,
        source_rate=22050,
    ),
}


def test_a_peaky_theme_is_backed_off_rather_than_clipped(caplog):
    """#57: the gain gives way, not the waveform."""
    with caplog.at_level(logging.DEBUG, logger=themes.__name__):
        processed = themes._process_theme(dict(_PEAKY_THEME), 0.0, "peaky-theme")

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "backing the gain off should make the clipping warning unreachable"
    )
    assert any(
        "backing its gain off" in record.getMessage() for record in caplog.records
    ), "the backoff must be visible in the log, not silent"

    peak = max(
        abs(value)
        for frames, _ in processed.values()
        for value in struct.unpack(f"<{len(frames) // 2}h", frames)
    )
    assert peak == themes._OUTPUT_MAXIMUM, (
        f"peak {peak} should sit exactly on the ceiling, neither over nor under"
    )


def test_the_backoff_is_only_as_deep_as_it_has_to_be():
    """A theme with headroom keeps the reference level it asked for."""
    roomy = {
        "button": themes._DecodedWav(
            samples=[-8000, 8000] * 500, sample_width=2, source_rate=22050
        ),
    }
    processed = themes._process_theme(roomy, 0.0, "roomy-theme")
    frames, _ = processed["button"]
    values = struct.unpack(f"<{len(frames) // 2}h", frames)
    rms = math.sqrt(sum((v / themes._OUTPUT_FULL_SCALE) ** 2 for v in values) / len(values))
    assert 20.0 * math.log10(rms) == pytest.approx(themes.REFERENCE_RMS_DBFS, abs=0.05)


def test_the_bundled_theme_does_not_clip_at_the_shipped_reference(caplog):
    """The #57 regression itself, against the real assets."""
    with caplog.at_level(logging.WARNING, logger=themes.__name__):
        processed = themes.SoundThemeLibrary(themes.BUNDLED_THEMES_DIR, None).load(
            "default"
        )

    assert processed, "the bundled theme should load"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], [
        r.getMessage() for r in caplog.records
    ]


def test_processing_without_clipping_logs_no_warning(caplog):
    decoded = {
        "button": themes._DecodedWav(
            samples=[-1000, 1000] * 8,
            sample_width=2,
            source_rate=22050,
        ),
    }

    with caplog.at_level(logging.WARNING, logger=themes.__name__):
        themes._process_theme(decoded, 0.0, "clean-theme")

    assert not caplog.records


def test_sparse_fallback_logs_only_slots_actually_loaded(
    theme_roots,
    library,
    caplog,
):
    bundled, user = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])
    _write_wav(user / "sparse" / "icon.wav", [-1000, 1000])

    with caplog.at_level(logging.INFO, logger=themes.__name__):
        library.load("sparse")

    fallback_messages = [
        record.getMessage()
        for record in caplog.records
        if "falling back to default" in record.getMessage()
    ]
    assert fallback_messages == [
        "Sound theme 'sparse' has no usable button slot; falling back to default"
    ]


def test_discover_and_load_work_without_configured_user_dir(tmp_path):
    bundled = tmp_path / "bundled" / "sound-themes"
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])
    library = themes.SoundThemeLibrary(bundled, None)

    discovered = library.discover()
    loaded = library.load("default")

    assert [info.id for info in discovered] == ["default"]
    assert "button" in loaded


def test_unknown_theme_returns_default(theme_roots, library):
    bundled, _ = theme_roots
    _write_wav(bundled / "default" / "button.wav", [-1000, 1000])

    assert library.load("missing") == library.load("default")
