import math

import pytest

import spatial


DESKTOP_RECT = (0, 0, 1920, 1080)


@pytest.mark.parametrize(
    "obj_rect",
    [
        (0, 0, 0, 0),
        (1920, 0, 0, 0),
        (0, 1080, 0, 0),
        (1920, 1080, 0, 0),
        (910, 515, 100, 50),
        (0, 490, 100, 100),
        (1820, 490, 100, 100),
        (910, 0, 100, 100),
        (910, 980, 100, 100),
        (-500, -300, 80, 60),
        (-200, -100, 2500, 1400),
    ],
)
def test_position_is_a_unit_vector_for_object_sweep(obj_rect):
    x, y, z = spatial.position_for(obj_rect, DESKTOP_RECT)

    assert math.sqrt(x * x + y * y + z * z) == pytest.approx(
        1.0, abs=1e-9
    )


@pytest.mark.parametrize(
    ("first_rect", "second_rect", "clamped_angle_x"),
    [
        ((5000, 540, 0, 0), (10000, 540, 0, 0), 90.0),
        ((-5000, 540, 0, 0), (-10000, 540, 0, 0), -90.0),
    ],
)
def test_azimuth_is_clamped(first_rect, second_rect, clamped_angle_x):
    angle_y = -15.0
    rad_x = math.radians(clamped_angle_x)
    rad_y = math.radians(angle_y)
    expected = (
        math.sin(rad_x) * math.cos(rad_y),
        math.sin(rad_y),
        -math.cos(rad_x) * math.cos(rad_y),
    )

    assert spatial.position_for(first_rect, DESKTOP_RECT) == pytest.approx(
        expected, abs=1e-9
    )
    assert spatial.position_for(second_rect, DESKTOP_RECT) == pytest.approx(
        expected, abs=1e-9
    )


@pytest.mark.parametrize(
    ("obj_rect", "expected"),
    [
        ((960, 3000, 0, 0), (0.0, -1.0, 0.0)),
        ((960, -2000, 0, 0), (0.0, 1.0, 0.0)),
    ],
)
def test_elevation_is_clamped(obj_rect, expected):
    # Within any desktop, elevation only spans -40deg..+10deg, so the
    # +-90deg clamp is only reachable for objects far off-screen. These
    # two cases were hand-checked on a 1920x1080 desktop.
    position = spatial.position_for(obj_rect, DESKTOP_RECT)

    assert position == pytest.approx(expected, abs=1e-9)


def test_zero_size_object_rect_is_well_defined():
    position = spatial.position_for((960, 540, 0, 0), DESKTOP_RECT)

    assert position == pytest.approx(
        (0.0, math.sin(math.radians(-15.0)), -math.cos(math.radians(-15.0))),
        abs=1e-9,
    )


def test_zero_height_desktop_rect_defaults_elevation_to_ear_level():
    # A zero-height desktop rect cannot yield a meaningful elevation
    # percentage (division by zero), so position_for falls back to 0deg
    # (ear level) rather than the -15deg a genuinely centred object
    # would otherwise get on this desktop -- this is a documented
    # fallback, not a claim that the object is "centered".
    position = spatial.position_for((1000, -500, 100, 100), (100, 200, 0, 0))

    assert position == pytest.approx((0.0, 0.0, -1.0), abs=1e-9)
    assert all(math.isfinite(component) for component in position)


def test_object_fully_outside_desktop_bounds_is_well_defined():
    position = spatial.position_for((-3000, 2000, 100, 100), DESKTOP_RECT)

    assert all(math.isfinite(component) for component in position)
    assert math.sqrt(sum(component * component for component in position)) == (
        pytest.approx(1.0, abs=1e-9)
    )


def test_desktop_offset_is_accounted_for():
    centered = spatial.position_for((1060, 690, 0, 0), (100, 150, 1920, 1080))

    assert centered == pytest.approx(
        (0.0, math.sin(math.radians(-15.0)), -math.cos(math.radians(-15.0))),
        abs=1e-9,
    )


@pytest.mark.parametrize(
    ("x", "left", "right"),
    [
        (0.0, 1.0, 1.0),  # straight ahead is exactly as loud as unpanned speech
        (-1.0, 1.0, 0.0),
        (1.0, 0.0, 1.0),
        (-5.0, 1.0, 0.0),  # clamped, like the azimuth
    ],
)
def test_pan_gains_at_the_landmarks(x, left, right):
    assert spatial.pan_gains((x, 0.0, -1.0)) == pytest.approx((left, right), abs=1e-9)


def test_pan_gains_never_exceed_unity_and_fall_away_monotonically():
    previous_right = -1.0
    for step in range(-20, 21):
        left, right = spatial.pan_gains((step / 20.0, 0.0, -1.0))
        assert 0.0 <= left <= 1.0 and 0.0 <= right <= 1.0
        assert right >= previous_right
        previous_right = right


def test_pan_gains_follow_the_role_sound_position():
    """Panning reads the same vector the earcon was placed with."""
    position = spatial.position_for((0, 490, 100, 100), DESKTOP_RECT)
    left, right = spatial.pan_gains(position)
    assert left == pytest.approx(1.0) and right < 0.2
