"""Generate the app's home-screen icons (PNG) with the standard library only.

Design: a white open ring ("C" for cerebellar) with a centre dot on the app's teal.
The motif stays inside the central 60% so the same image also works as a "maskable" icon.

Run from the "MSAC Application" folder:  python tools/make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "msac" / "web" / "static" / "icons"
TEAL = (14, 102, 112)
WHITE = (255, 255, 255)
SIZES = {"apple-touch-icon.png": 180, "icon-192.png": 192, "icon-512.png": 512}
SUPERSAMPLE = 4


def _inside(x: float, y: float) -> bool:
    """Motif test in unit coordinates centred on (0, 0), with the icon spanning -1..1."""
    r = math.hypot(x, y)
    ring_mid, ring_half = 0.50, 0.11
    gap = math.radians(42)                      # opening of the "C", facing right
    angle = abs(math.atan2(y, x))
    if abs(r - ring_mid) <= ring_half and angle >= gap:
        return True
    for sign in (1, -1):                        # rounded ends of the ring
        ex, ey = ring_mid * math.cos(gap), sign * ring_mid * math.sin(gap)
        if math.hypot(x - ex, y - ey) <= ring_half:
            return True
    return r <= 0.15                            # centre dot


def render(size: int) -> bytes:
    rows = []
    n = SUPERSAMPLE
    for py in range(size):
        row = bytearray([0])                    # PNG filter type 0
        for px in range(size):
            hits = 0
            for sy in range(n):
                for sx in range(n):
                    x = ((px + (sx + 0.5) / n) / size) * 2 - 1
                    y = ((py + (sy + 0.5) / n) / size) * 2 - 1
                    hits += _inside(x, y)
            a = hits / (n * n)
            row += bytes(round(TEAL[i] * (1 - a) + WHITE[i] * a) for i in range(3))
        rows.append(bytes(row))
    return _png(size, size, b"".join(rows))


def _png(w: int, h: int, raw: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB, opaque (iOS fills transparency with black)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in SIZES.items():
        (OUT / name).write_bytes(render(size))
        print(f"wrote {OUT / name} ({size}x{size})")
