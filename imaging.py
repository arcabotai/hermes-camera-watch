"""PNG encode/decode and a small grayscale differencer. No third-party image library.

Mock scenes are drawn as PNG. HTTP PNG snapshots are decoded here. JPEG and RTSP go through
ffmpeg in ``backend_live`` because a pure-Python JPEG/H.264 decoder is not worth the risk.
"""
from __future__ import annotations

import struct
import zlib
from typing import Optional, Tuple

GRAY_W = 32
GRAY_H = 24
MAX_PIXELS = 2_000_000
MAX_DIM = 4096


def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    if len(rgb) != width * height * 3:
        raise ValueError("rgb buffer does not match width*height*3")
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def sniff(data: bytes) -> Tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    raise ValueError("not a PNG or JPEG")


def jpeg_size(data: bytes) -> Optional[Tuple[int, int]]:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return width, height
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if index + 4 > len(data):
            break
        index += 2 + struct.unpack(">H", data[index + 2:index + 4])[0]
    return None


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return up_left


def decode_png(data: bytes) -> Tuple[int, int, bytes]:
    """Return ``(width, height, rgb)`` for an 8-bit non-interlaced PNG."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a png")
    pos = 8
    width = height = color = depth = interlace = None
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        pos += 8
        if length < 0 or pos + length + 4 > len(data):
            raise ValueError("truncated png")
        chunk = data[pos:pos + length]
        pos += length + 4
        if tag == b"IHDR":
            width, height, depth, color, comp, filt, interlace = struct.unpack(">IIBBBBB", chunk[:13])
            if comp != 0 or filt != 0 or interlace != 0 or depth != 8:
                raise ValueError("unsupported png")
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
        elif tag == b"PLTE":
            raise ValueError("palette png is not decoded")
    if not width or not height or color not in (0, 2, 4, 6):
        raise ValueError("incomplete png")
    if width > MAX_DIM or height > MAX_DIM or width * height > MAX_PIXELS:
        raise ValueError("image is too large")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows = []
    src = 0
    for _y in range(height):
        if src >= len(raw):
            raise ValueError("truncated png rows")
        filt = raw[src]
        src += 1
        row = bytearray(raw[src:src + stride])
        src += stride
        prev = rows[-1] if rows else bytearray(stride)
        if filt == 1:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + left) & 255
        elif filt == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif filt == 3:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + prev[i]) // 2)) & 255
        elif filt == 4:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                up_left = prev[i - channels] if i >= channels else 0
                row[i] = (row[i] + _paeth(left, prev[i], up_left)) & 255
        elif filt != 0:
            raise ValueError("unknown png filter")
        rows.append(row)
    rgb = bytearray(width * height * 3)
    out = 0
    for row in rows:
        if channels == 3:
            rgb[out:out + stride] = row
            out += stride
        elif channels == 1:
            for value in row:
                rgb[out:out + 3] = bytes((value, value, value))
                out += 3
        elif channels == 4:
            for i in range(0, len(row), 4):
                rgb[out:out + 3] = row[i:i + 3]
                out += 3
        else:
            for i in range(0, len(row), 2):
                value = row[i]
                rgb[out:out + 3] = bytes((value, value, value))
                out += 3
    return width, height, bytes(rgb)


def to_gray_small(rgb: bytes, width: int, height: int, gw: int = GRAY_W, gh: int = GRAY_H) -> bytes:
    if len(rgb) != width * height * 3:
        raise ValueError("rgb buffer does not match the frame size")
    out = bytearray(gw * gh)
    for gy in range(gh):
        y0 = gy * height // gh
        y1 = max(y0 + 1, (gy + 1) * height // gh)
        for gx in range(gw):
            x0 = gx * width // gw
            x1 = max(x0 + 1, (gx + 1) * width // gw)
            total = count = 0
            for y in range(y0, y1):
                row = y * width
                for x in range(x0, x1):
                    i = (row + x) * 3
                    total += (rgb[i] * 3 + rgb[i + 1] * 4 + rgb[i + 2]) // 8
                    count += 1
            out[gy * gw + gx] = total // count
    return bytes(out)


def changed_fraction(before: bytes, after: bytes, delta: int) -> float:
    if not before or len(before) != len(after):
        raise ValueError("frames differ in size")
    changed = sum(1 for left, right in zip(before, after) if abs(left - right) >= delta)
    return changed / len(before)


def parse_pgm(data: bytes, gw: int = GRAY_W, gh: int = GRAY_H) -> bytes:
    """Parse a binary PGM written by ffmpeg's gray scaler."""
    if not data.startswith(b"P5"):
        raise ValueError("ffmpeg did not return a gray frame")
    index = 2
    tokens = []
    while len(tokens) < 3:
        while index < len(data) and data[index] in b" \t\r\n":
            index += 1
        if index < len(data) and data[index] == ord("#"):
            while index < len(data) and data[index] != ord("\n"):
                index += 1
            continue
        start = index
        while index < len(data) and data[index] not in b" \t\r\n":
            index += 1
        if start == index:
            raise ValueError("short pgm header")
        tokens.append(data[start:index])
    width, height, maxv = (int(token) for token in tokens)
    if (width, height, maxv) != (gw, gh, 255):
        raise ValueError("unexpected gray frame size")
    if index < len(data) and data[index] in b" \t\r\n":
        index += 1
    raw = data[index:index + width * height]
    if len(raw) != width * height:
        raise ValueError("short gray frame")
    return raw
