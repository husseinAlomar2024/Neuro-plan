"""Draw the NeuroPlan app icon and write assets/neuroplan.ico. Standard library only.

    .venv\\Scripts\\python assets\\make_icon.py

The icon is a rounded square in the app's accent blue with a white "N". Every size is drawn on its own
(4x4 supersampling) instead of being scaled down from one image, so the small sizes stay sharp.
The committed neuroplan.ico is the output of this script; re-run it only to change the design."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 256)
SAMPLES = 4                                     # supersampling per axis
TOP = (124, 156, 245)                           # theme accent, dark mode (#7C9CF5)
BOTTOM = (79, 111, 224)                         # theme accent, light mode (#4F6FE0)
WHITE = (255, 255, 255)
RADIUS = 0.22                                   # corner radius of the square, as a share of its side
N_LEFT, N_RIGHT, N_TOP, N_BOTTOM = 0.27, 0.73, 0.24, 0.76
STROKE = 0.125                                  # width of the strokes of the "N"

Pixel = tuple[int, int, int, int]               # R, G, B, A


def in_square(x: float, y: float) -> bool:
    """Inside the rounded square that fills the unit square."""
    dx = max(RADIUS - x, x - (1 - RADIUS), 0.0)
    dy = max(RADIUS - y, y - (1 - RADIUS), 0.0)
    return dx * dx + dy * dy <= RADIUS * RADIUS


def in_letter(x: float, y: float) -> bool:
    """Inside the "N": two vertical strokes joined by a diagonal one."""
    if not (N_TOP <= y <= N_BOTTOM and N_LEFT <= x <= N_RIGHT):
        return False
    if x <= N_LEFT + STROKE or x >= N_RIGHT - STROKE:
        return True
    start = N_LEFT + (y - N_TOP) / (N_BOTTOM - N_TOP) * (N_RIGHT - N_LEFT - STROKE)
    return start <= x <= start + STROKE


def render(size: int) -> list[list[Pixel]]:
    """Rows of RGBA pixels, top row first."""
    rows: list[list[Pixel]] = []
    step = 1.0 / (size * SAMPLES)
    for py in range(size):
        row: list[Pixel] = []
        for px in range(size):
            red = green = blue = covered = 0
            for sy in range(SAMPLES):
                y = (py * SAMPLES + sy + 0.5) * step
                for sx in range(SAMPLES):
                    x = (px * SAMPLES + sx + 0.5) * step
                    if not in_square(x, y):
                        continue
                    colour = WHITE if in_letter(x, y) else tuple(
                        round(TOP[i] + (BOTTOM[i] - TOP[i]) * y) for i in range(3))
                    red, green, blue, covered = red + colour[0], green + colour[1], blue + colour[2], covered + 1
            if covered:
                row.append((round(red / covered), round(green / covered), round(blue / covered),
                            round(255 * covered / (SAMPLES * SAMPLES))))
            else:
                row.append((0, 0, 0, 0))
        rows.append(row)
    return rows


def png_bytes(rows: list[list[Pixel]]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    size = len(rows)
    raw = b"".join(b"\x00" + bytes(value for pixel in row for value in pixel) for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def dib_bytes(rows: list[list[Pixel]]) -> bytes:
    """32-bit bitmap as stored inside an .ico: header, BGRA rows bottom-up, then an (empty) 1-bit mask."""
    size = len(rows)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
    pixels = b"".join(bytes((b, g, r, a)) for row in reversed(rows) for r, g, b, a in row)
    mask = bytes(((size + 31) // 32) * 4) * size
    return header + pixels + mask


def ico_bytes(sizes: tuple[int, ...] = SIZES) -> bytes:
    """Small sizes as bitmaps (read by everything), 256 px as PNG (the usual form for that size)."""
    images = [png_bytes(render(size)) if size >= 256 else dib_bytes(render(size)) for size in sizes]
    out = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for size, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


def main() -> None:
    target = Path(__file__).resolve().parent / "neuroplan.ico"
    target.write_bytes(ico_bytes())
    print(f"wrote {target} ({target.stat().st_size} bytes, sizes {', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    main()
