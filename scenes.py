"""Scripted mock pictures. The package scene is a doorway that gains a parcel on the second look.

Nothing here is a camera, a network call, or a learned detector. The pictures are so the mock
demo, the dashboard, and the tests can show a real still without hardware.
"""
from __future__ import annotations

from .imaging import encode_png

WIDTH = 96
HEIGHT = 64

CLEAR_CAPTION = "The doorway is clear."
PACKAGE_CAPTION = "A package is at the door."
PACKAGE_EVENT = "A package has appeared at the door."
QUIET_CAPTION = "The room is quiet; nothing is happening."

# Pixel the tests use to tell the empty stoop from the parcel.
PROBE = (20, 50)
PACKAGE_RECT = (12, 36, 56, 62)


def _canvas(color: tuple) -> bytearray:
    pixel = bytes(color)
    return bytearray(pixel * (WIDTH * HEIGHT))


def _rect(buf: bytearray, x0: int, y0: int, x1: int, y1: int, color: tuple) -> None:
    pixel = bytes(color)
    for y in range(max(0, y0), min(HEIGHT, y1)):
        row = y * WIDTH
        for x in range(max(0, x0), min(WIDTH, x1)):
            i = (row + x) * 3
            buf[i:i + 3] = pixel


def porch(package: bool) -> bytes:
    buf = _canvas((176, 188, 198))
    _rect(buf, 36, 4, 64, 46, (96, 62, 40))
    _rect(buf, 58, 24, 62, 28, (220, 190, 90))
    _rect(buf, 0, 46, WIDTH, HEIGHT, (110, 108, 102))
    if package:
        _rect(buf, *PACKAGE_RECT, (198, 150, 72))
        _rect(buf, 12, 46, 56, 50, (120, 86, 40))
    return bytes(buf)


def quiet_room() -> bytes:
    buf = _canvas((236, 230, 216))
    _rect(buf, 20, 10, 70, 36, (120, 168, 196))
    _rect(buf, 44, 10, 46, 36, (236, 230, 216))
    _rect(buf, 20, 22, 70, 24, (236, 230, 216))
    _rect(buf, 0, 48, WIDTH, HEIGHT, (160, 130, 90))
    return bytes(buf)


def render(scene: str, step: int) -> tuple:
    """Return ``(rgb, still_caption, event_caption)`` for this mock step."""
    if scene == "quiet":
        return quiet_room(), QUIET_CAPTION, ""
    if scene == "package":
        has_package = step >= 1
        caption = PACKAGE_CAPTION if has_package else CLEAR_CAPTION
        event = PACKAGE_EVENT if has_package else ""
        return porch(has_package), caption, event
    raise ValueError(f"unknown scene {scene!r}")


def render_png(scene: str, step: int) -> tuple:
    rgb, caption, event = render(scene, step)
    return encode_png(WIDTH, HEIGHT, rgb), caption, event


def pixel(rgb: bytes, x: int, y: int) -> tuple:
    i = (y * WIDTH + x) * 3
    return tuple(rgb[i:i + 3])
