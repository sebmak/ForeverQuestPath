#!/usr/bin/env python3
"""Build true 32-bit RGBA .tga/.png in-game icons and WowUp/GitHub preview assets."""

from __future__ import annotations

import argparse
import math
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path


# Rules for IconAssetBuilder:
# 1. Uses macOS `sips` + Python stdlib only (no external Pillow dependency).
# 2. Produces uncompressed 32-bit BGRA TGA textures matching WoW Forever `16001`
#    expectations (18-byte header `(0, 0, 2, 0, 0, 0, 0, 0, W, H, 32, 8)` written
#    bottom-to-top with 26-byte `TRUEVISION-XFILE.\x00` footer = 65,580 bytes at
#    128x128) for both square (`Icon.tga`) and circular (`IconRound.tga`) icons.
# 3. Produces true 32-bit RGBA PNG (`color_type=6`) at 128x128 (`addon/Media/Icon.png`)
#    and 512x512 (`.previews/icon.png`), plus `< 1 MB` 1280x640 GitHub Social Preview
#    (`.previews/social-preview.jpg` and `.previews/social-preview.png`).
class IconAssetBuilder:
    """Generates and validates in-game TGA/PNG icons and WowUp/GitHub preview images."""

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    ADDON_MEDIA_DIR = PROJECT_ROOT / "addon" / "Media"
    PREVIEWS_DIR = PROJECT_ROOT / ".previews"
    TGA_SIZE = 128
    PREVIEW_ICON_SIZE = 512
    SOCIAL_WIDTH = 1280
    SOCIAL_HEIGHT = 640
    MAX_SOCIAL_JPG_BYTES = 950_000
    TGA_FOOTER = b"\x00" * 8 + b"TRUEVISION-XFILE.\x00"

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = (project_root or self.PROJECT_ROOT).resolve()
        self.media_dir = self.project_root / "addon" / "Media"
        self.previews_dir = self.project_root / ".previews"

    @staticmethod
    def _sips_load_rgba_rows(src_path: Path, width: int, height: int) -> list[list[tuple[int, int, int, int]]]:
        """Resize `src_path` to `(width, height)` via `sips` BMP export and return top-down RGBA rows."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bmp_path = Path(tmp_dir) / "scaled.bmp"
            subprocess.run(
                ["sips", "-s", "format", "bmp", "-z", str(height), str(width), str(src_path), "--out", str(bmp_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            raw = bmp_path.read_bytes()

        if raw[:2] != b"BM":
            raise RuntimeError(f"Unexpected BMP header from sips for {src_path}")

        bf_off_bits = struct.unpack_from("<I", raw, 10)[0]
        bmp_w, bmp_h, _planes, bpp, compression = struct.unpack_from("<iiHH I", raw, 18)
        if bmp_w != width or abs(bmp_h) != height:
            raise RuntimeError(f"Unexpected BMP size {(bmp_w, bmp_h)}, expected {(width, height)}")
        if bpp not in (24, 32) or compression not in (0, 3):
            raise RuntimeError(f"Unsupported BMP pixel layout: bpp={bpp}, compression={compression}")

        top_down = bmp_h < 0
        bytes_per_px = bpp // 8
        row_stride = ((width * bytes_per_px + 3) // 4) * 4
        rows: list[list[tuple[int, int, int, int]]] = [None] * height  # type: ignore[list-item]

        for raw_row in range(height):
            target_y = raw_row if top_down else (height - 1 - raw_row)
            row_start = bf_off_bits + raw_row * row_stride
            row_pixels: list[tuple[int, int, int, int]] = []
            for x in range(width):
                px = row_start + x * bytes_per_px
                b = raw[px]
                g = raw[px + 1]
                r = raw[px + 2]
                a = raw[px + 3] if bytes_per_px == 4 else 255
                row_pixels.append((r, g, b, a))
            rows[target_y] = row_pixels
        return rows

    @staticmethod
    def _apply_rounded_square_mask(
        rows: list[list[tuple[int, int, int, int]]],
        radius: float = 8.0,
    ) -> list[list[tuple[int, int, int, int]]]:
        """Apply a subtle anti-aliased rounded corner alpha mask to square icon rows."""
        h = len(rows)
        w = len(rows[0])
        inner_x0, inner_x1 = radius, w - 1 - radius
        inner_y0, inner_y1 = radius, h - 1 - radius
        out: list[list[tuple[int, int, int, int]]] = []
        for y in range(h):
            cx_y = min(max(float(y), inner_y0), inner_y1)
            new_row: list[tuple[int, int, int, int]] = []
            for x in range(w):
                r, g, b, a = rows[y][x]
                cx_x = min(max(float(x), inner_x0), inner_x1)
                dist = math.hypot(x - cx_x, y - cx_y)
                if dist <= radius - 1.0:
                    mask = 1.0
                elif dist >= radius + 0.5:
                    mask = 0.0
                else:
                    mask = max(0.0, min(1.0, (radius + 0.5 - dist) / 1.5))
                alpha = int(round(a * mask))
                if alpha == 0:
                    new_row.append((0, 0, 0, 0))
                else:
                    new_row.append((r, g, b, alpha))
            out.append(new_row)
        return out

    @staticmethod
    def _apply_circle_mask(
        rows: list[list[tuple[int, int, int, int]]],
        feather_width: float = 2.0,
    ) -> list[list[tuple[int, int, int, int]]]:
        """Apply a smooth circular alpha mask so `IconRound.tga` fits the minimap frame."""
        h = len(rows)
        w = len(rows[0])
        cx = (w - 1) * 0.5
        cy = (h - 1) * 0.5
        outer_r = min(w, h) * 0.5 - 1.0
        inner_r = outer_r - feather_width
        out: list[list[tuple[int, int, int, int]]] = []
        for y in range(h):
            new_row: list[tuple[int, int, int, int]] = []
            for x in range(w):
                r, g, b, a = rows[y][x]
                dist = math.hypot(x - cx, y - cy)
                if dist <= inner_r:
                    mask = 1.0
                elif dist >= outer_r:
                    mask = 0.0
                else:
                    mask = max(0.0, min(1.0, (outer_r - dist) / feather_width))
                alpha = int(round(a * mask))
                if alpha == 0:
                    new_row.append((0, 0, 0, 0))
                else:
                    new_row.append((r, g, b, alpha))
            out.append(new_row)
        return out

    @classmethod
    def _write_tga32(cls, dst_path: Path, rows: list[list[tuple[int, int, int, int]]]) -> None:
        """Write uncompressed 32-bit BGRA bottom-to-top TGA with 8 alpha bits (`desc=0x08`)."""
        h = len(rows)
        w = len(rows[0])
        header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 32, 8)
        payload = bytearray()
        for y in range(h - 1, -1, -1):
            for r, g, b, a in rows[y]:
                payload.extend((b, g, r, a))
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_bytes(header + bytes(payload) + cls.TGA_FOOTER)

    @staticmethod
    def _png_chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    @classmethod
    def _write_rgba_png(cls, dst_path: Path, rows: list[list[tuple[int, int, int, int]]]) -> None:
        """Write true 32-bit RGBA PNG (`color_type=6`, `bit_depth=8`)."""
        h = len(rows)
        w = len(rows[0])
        raw_scanlines = bytearray()
        for row in rows:
            raw_scanlines.append(0)
            for r, g, b, a in row:
                raw_scanlines.extend((r, g, b, a))

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
        idat = zlib.compress(bytes(raw_scanlines), level=9)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + cls._png_chunk(b"IHDR", ihdr)
            + cls._png_chunk(b"IDAT", idat)
            + cls._png_chunk(b"IEND", b"")
        )
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_bytes(png_bytes)

    def build_social_preview(self, banner_src: Path) -> None:
        """Build 1280x640 `.previews/social-preview.png` and `< 1 MB` `.previews/social-preview.jpg`."""
        self.previews_dir.mkdir(parents=True, exist_ok=True)
        png_out = self.previews_dir / "social-preview.png"
        jpg_out = self.previews_dir / "social-preview.jpg"

        if banner_src.resolve() != png_out.resolve():
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_png = Path(tmp_dir) / "wide.png"
                subprocess.run(
                    ["sips", "-s", "format", "png", "--resampleWidth", str(self.SOCIAL_WIDTH), str(banner_src), "--out", str(tmp_png)],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["sips", "-c", str(self.SOCIAL_HEIGHT), str(self.SOCIAL_WIDTH), str(tmp_png), "--out", str(png_out)],
                    check=True,
                    capture_output=True,
                )

        for quality in (85, 78, 70):
            subprocess.run(
                ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality), str(png_out), "--out", str(jpg_out)],
                check=True,
                capture_output=True,
            )
            if jpg_out.stat().st_size <= self.MAX_SOCIAL_JPG_BYTES:
                break

        if jpg_out.stat().st_size > self.MAX_SOCIAL_JPG_BYTES:
            raise RuntimeError(f"{jpg_out} exceeds GitHub upload safety limit: {jpg_out.stat().st_size} bytes")

    def build_icons(self, icon_src: Path, banner_src: Path | None = None) -> None:
        """Generate `Icon.tga`, `IconRound.tga`, `Icon.png`, `.previews/icon.png`, and social previews."""
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.previews_dir.mkdir(parents=True, exist_ok=True)

        rows_128 = self._sips_load_rgba_rows(icon_src, self.TGA_SIZE, self.TGA_SIZE)
        square_128 = self._apply_rounded_square_mask(rows_128, radius=8.0)
        round_128 = self._apply_circle_mask(rows_128, feather_width=2.0)

        self._write_tga32(self.media_dir / "Icon.tga", square_128)
        self._write_tga32(self.media_dir / "IconRound.tga", round_128)
        self._write_rgba_png(self.media_dir / "Icon.png", square_128)

        rows_512 = self._sips_load_rgba_rows(icon_src, self.PREVIEW_ICON_SIZE, self.PREVIEW_ICON_SIZE)
        square_512 = self._apply_rounded_square_mask(rows_512, radius=32.0)
        self._write_rgba_png(self.previews_dir / "icon.png", square_512)

        resolved_banner = banner_src or (self.previews_dir / "social-preview.png")
        if resolved_banner.is_file():
            self.build_social_preview(resolved_banner)

        print(f"Built {self.media_dir / 'Icon.tga'} ({(self.media_dir / 'Icon.tga').stat().st_size} bytes)")
        print(f"Built {self.media_dir / 'IconRound.tga'} ({(self.media_dir / 'IconRound.tga').stat().st_size} bytes)")
        print(f"Built {self.media_dir / 'Icon.png'} ({(self.media_dir / 'Icon.png').stat().st_size} bytes)")
        print(f"Built {self.previews_dir / 'icon.png'} ({(self.previews_dir / 'icon.png').stat().st_size} bytes)")
        if (self.previews_dir / "social-preview.jpg").is_file():
            print(f"Built {self.previews_dir / 'social-preview.jpg'} ({(self.previews_dir / 'social-preview.jpg').stat().st_size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 32-bit RGBA TGA/PNG icons and preview assets.")
    parser.add_argument("--src", type=Path, required=True, help="Source square emblem image")
    parser.add_argument("--banner", type=Path, default=None, help="Optional source banner image for social preview")
    args = parser.parse_args()
    builder = IconAssetBuilder()
    builder.build_icons(args.src, args.banner)


if __name__ == "__main__":
    main()
