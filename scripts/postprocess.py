#!/usr/bin/env python3
"""Turn a model's full-res image into a small game-ready RGBA sprite."""
from __future__ import annotations

import json
from pathlib import Path
from PIL import Image

_RESAMPLE = {
    "nearest": Image.NEAREST,
    "box": Image.BOX,
    "lanczos": Image.LANCZOS,
}
TRANSPARENT = (0, 0, 0, 0)


def _hex_to_rgba(value: str) -> tuple[int, int, int, int]:
    h = value.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return (r, g, b, a)


def _luma(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def downscale(img: "Image.Image", width: int, height: int, method: str = "nearest") -> "Image.Image":
    return img.convert("RGBA").resize((width, height), _RESAMPLE[method])


def remove_background(img: "Image.Image", method: str, color: str, tolerance: int) -> "Image.Image":
    img = img.convert("RGBA")
    if method == "none":
        return img
    if method == "alpha_threshold":
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                r, g, b, a = px[x, y]
                px[x, y] = (r, g, b, a) if a >= tolerance else TRANSPARENT
        return img
    # chroma
    kr, kg, kb, _ = _hex_to_rgba(color)
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if abs(r - kr) <= tolerance and abs(g - kg) <= tolerance and abs(b - kb) <= tolerance:
                px[x, y] = TRANSPARENT
    return img


def quantize(img: "Image.Image", colors: int) -> "Image.Image":
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    rgb = img.convert("RGB").quantize(colors=max(1, colors), method=Image.MEDIANCUT).convert("RGB")
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def load_target_palette(palettes_dir: Path, name: str) -> list[tuple[int, int, int, int]]:
    path = palettes_dir / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    colors = [v for v in data.get("colors", {}).values()
              if isinstance(v, str) and v.startswith("#")]
    ramp = [_hex_to_rgba(c) for c in colors]
    ramp.sort(key=lambda c: _luma(c))
    if not ramp:
        raise ValueError(f"target palette '{name}' has no flat hex colors to recolor onto")
    return ramp


def recolor(img: "Image.Image", target: list[tuple[int, int, int, int]]) -> "Image.Image":
    img = img.convert("RGBA")
    uniq = sorted({p[:3] for p in img.getdata() if p[3] == 255}, key=_luma)
    if not uniq:
        return img
    mapping: dict[tuple[int, int, int], tuple[int, int, int, int]] = {}
    n = len(uniq)
    for i, src in enumerate(uniq):
        ti = 0 if n == 1 else round(i / (n - 1) * (len(target) - 1))
        mapping[src] = target[ti]
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a == 255 and (r, g, b) in mapping:
                px[x, y] = mapping[(r, g, b)]
    return img


def add_outline(img: "Image.Image", color: str = "#000000") -> "Image.Image":
    img = img.convert("RGBA")
    out = img.copy()
    src = img.load()
    dst = out.load()
    oc = _hex_to_rgba(color)
    for y in range(img.height):
        for x in range(img.width):
            if src[x, y][3] != 0:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < img.width and 0 <= ny < img.height and src[nx, ny][3] == 255:
                    dst[x, y] = oc
                    break
    return out


def process(img: "Image.Image", pp, width: int, height: int, palettes_dir: Path,
            recolor_name: "str | None" = None) -> "Image.Image":
    out = downscale(img, width, height, pp.downscale)
    out = remove_background(out, pp.background.method, pp.background.color, pp.background.tolerance)
    if pp.quantize.enabled:
        out = quantize(out, pp.quantize.colors)
    if recolor_name:
        out = recolor(out, load_target_palette(palettes_dir, recolor_name))
    if pp.outline:
        out = add_outline(out)
    return out
