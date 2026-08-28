"""Tiny dependency-free PNG codec for SceneWorks-generated RGB screenshots.

The encoder writes only non-interlaced 8-bit RGB PNGs with filter type 0. The
decoder intentionally accepts only that exact profile because WP17 compares only
SceneWorks-generated screenshots/diffs, not arbitrary user images.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngError(ValueError):
    pass


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode_rgb(width: int, height: int, rgb: bytes) -> bytes:
    if width <= 0 or height <= 0:
        raise PngError("PNG dimensions must be positive")
    expected = width * height * 3
    if len(rgb) != expected:
        raise PngError(f"RGB payload has {len(rgb)} bytes; expected {expected}")
    stride = width * 3
    raw = b"".join(
        b"\x00" + rgb[row * stride : (row + 1) * stride]
        for row in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")


def decode_rgb(data: bytes) -> tuple[int, int, bytes]:
    if not data.startswith(PNG_SIGNATURE):
        raise PngError("artifact is not a PNG")
    offset = len(PNG_SIGNATURE)
    width = height = 0
    compressed = bytearray()
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        start = offset + 8
        end = start + length
        if end + 4 > len(data):
            raise PngError("truncated PNG chunk")
        payload = data[start:end]
        crc_expected = struct.unpack(">I", data[end : end + 4])[0]
        if (zlib.crc32(kind + payload) & 0xFFFFFFFF) != crc_expected:
            raise PngError("PNG CRC mismatch")
        if kind == b"IHDR":
            if len(payload) != 13:
                raise PngError("invalid IHDR")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if (bit_depth, color_type, compression, filtering, interlace) != (8, 2, 0, 0, 0):
                raise PngError("unsupported PNG profile")
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
        offset = end + 4
    if width <= 0 or height <= 0:
        raise PngError("PNG has no valid IHDR")
    raw = zlib.decompress(bytes(compressed))
    stride = width * 3
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise PngError("unexpected PNG scanline size")
    rows: list[bytes] = []
    for row in range(height):
        start = row * (stride + 1)
        if raw[start] != 0:
            raise PngError("unsupported PNG filter")
        rows.append(raw[start + 1 : start + 1 + stride])
    return width, height, b"".join(rows)
