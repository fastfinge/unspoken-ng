"""Spatial positioning for the audio pipeline rebuild (spec section 4.2)."""

import math


AZIMUTH_SPAN_DEGREES = 180.0
ELEVATION_MIN_DEGREES = -40.0
ELEVATION_MAGNITUDE_DEGREES = 50.0


def position_for(
    obj_rect: tuple[int, int, int, int],
    desktop_rect: tuple[int, int, int, int],
) -> tuple[float, float, float]:
    """Return the object's listener-relative position on the unit sphere.

    The result is a unit vector using the addon's convention: +x right,
    +y up, -z forward. Distance is fixed at 1.
    """
    obj_left, obj_top, obj_width, obj_height = obj_rect
    desktop_left, desktop_top, desktop_width, desktop_height = desktop_rect

    obj_x = obj_left + obj_width / 2.0
    obj_y = obj_top + obj_height / 2.0

    if desktop_width:
        desktop_center_x = desktop_left + desktop_width / 2.0
        angle_x = ((obj_x - desktop_center_x) / desktop_width) * AZIMUTH_SPAN_DEGREES
    else:
        angle_x = 0.0

    if desktop_height:
        desktop_bottom = desktop_top + desktop_height
        percent = (desktop_bottom - obj_y) / desktop_height
        angle_y = ELEVATION_MAGNITUDE_DEGREES * percent + ELEVATION_MIN_DEGREES
    else:
        angle_y = 0.0

    angle_x = max(-90.0, min(90.0, angle_x))
    angle_y = max(-90.0, min(90.0, angle_y))

    rad_x = math.radians(angle_x)
    rad_y = math.radians(angle_y)
    x = math.sin(rad_x) * math.cos(rad_y)
    y = math.sin(rad_y)
    z = -math.cos(rad_x) * math.cos(rad_y)
    return (x, y, z)


def pan_gains(position: tuple[float, float, float]) -> tuple[float, float]:
    """Left and right channel gains placing mono speech where `position` is.

    Panning only has left and right, so it reads the position's x -- the same
    vector the role sound was placed with, which is what keeps the two
    aligned. The law is equal-power, scaled so the centre is unity in both
    channels: speech straight ahead is exactly as loud as it is without
    panning, and the far channel falls away smoothly to silence at the edge
    rather than the near one ever exceeding full scale.
    """
    x = max(-1.0, min(1.0, float(position[0])))
    angle = (x + 1.0) * math.pi / 4.0
    left = min(1.0, math.sqrt(2.0) * math.cos(angle))
    right = min(1.0, math.sqrt(2.0) * math.sin(angle))
    return (max(0.0, left), max(0.0, right))
