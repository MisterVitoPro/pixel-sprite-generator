#!/usr/bin/env python3
"""
Render JSON pixel-grid sources into square RGBA PNG sprites.

You (or Claude) author a size-independent shape grid under <shapes_dir>/<id>.json using
semantic characters, plus reusable color palettes under <palettes_dir>/<name>.json that map
each character to a hex color (or a gradient). This converter cross-products a shape against
the palettes named in its `outputs` map and writes one square PNG per output into <out_dir>.

Paths and the sprite size come from a project config file (pixel-sprite.config.json in the
current working directory) and/or CLI flags. All paths resolve relative to the consuming
project, never to this script's bundled location.

Usage:
  python render_sprites.py                  # render every shape, using the project config
  python render_sprites.py --only gem
  python render_sprites.py --check          # validate all shapes+palettes, write nothing
  python render_sprites.py --out-dir build --size 32   # override config per run

Exit codes:
  0  success / all valid
  1  validation failure (a malformed shape or palette)
  2  environment error (Pillow missing, dirs missing, missing/invalid config)

Requires: Pillow (PIL fork). Install with: pip install Pillow
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import re
import sys
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:
    sys.stderr.write(
        "Error: Pillow is not installed. Install it with:\n  pip install Pillow\n"
    )
    sys.exit(2)

DEFAULT_SIZE = 16
TRANSPARENT = (0, 0, 0, 0)
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
TRANSPARENT_CHAR = "."
MAX_EXTENDS_DEPTH = 16
GRADIENT_AXES = ("x", "y", "diag", "adiag")

ATLAS_APP = "pixel-sprite-generator"
ATLAS_FORMAT = "RGBA8888"
DEFAULT_PACK_NAME = "spritesheet"
# An output named "<base>_f<n>" is treated as animation frame n of "<base>".
FRAME_SUFFIX = re.compile(r"^(?P<base>.+)_f(?P<index>\d+)$")

CONFIG_FILENAME = "pixel-sprite.config.json"
DEFAULT_CONFIG = {
    "size": DEFAULT_SIZE,
    "shapes_dir": "art/shapes",
    "palettes_dir": "art/palettes",
    "out_dir": "assets/sprites",
}
CONFIG_KEYS = set(DEFAULT_CONFIG)


class RenderError(Exception):
    """Raised on any invalid shape or palette. Carries a human-readable message."""


class ConfigError(Exception):
    """Raised on a missing/invalid configuration (environment error -> exit code 2)."""


# --------------------------------------------------------------------------- #
# palettes
# --------------------------------------------------------------------------- #

def hex_to_rgba(value: Optional[str]) -> tuple[int, int, int, int]:
    """Convert '#RRGGBB' / '#RRGGBBAA' (or None) to an RGBA tuple."""
    if value is None:
        return TRANSPARENT
    h = value.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return (r, g, b, a)


def validate_gradient(palette_name: str, char: str, obj: dict) -> None:
    """Strict-validate a gradient palette value {from, to, axis}. Raises RenderError."""
    keys = set(obj.keys())
    if keys != {"from", "to", "axis"}:
        raise RenderError(
            f"palette '{palette_name}': char '{char}' gradient must have exactly keys "
            f"from/to/axis, got {sorted(keys)}"
        )
    for endpoint in ("from", "to"):
        val = obj[endpoint]
        if not isinstance(val, str) or not HEX_RE.match(val):
            raise RenderError(
                f"palette '{palette_name}': char '{char}' gradient '{endpoint}' "
                f"has invalid hex '{val}'"
            )
    if obj["axis"] not in GRADIENT_AXES:
        raise RenderError(
            f"palette '{palette_name}': char '{char}' gradient axis '{obj['axis']}' "
            f"must be one of {'/'.join(GRADIENT_AXES)}"
        )


def axis_coord(x: int, y: int, axis: str) -> int:
    """Scalar position of a pixel along a linear gradient axis."""
    if axis == "x":
        return x
    if axis == "y":
        return y
    if axis == "diag":      # top-left -> bottom-right
        return x + y
    return x - y            # adiag: bottom-left -> top-right


def lerp_rgba(c0: tuple[int, int, int, int], c1: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    """Per-channel linear interpolation between two RGBA colors at fraction t in [0, 1]."""
    return tuple(round(c0[i] + t * (c1[i] - c0[i])) for i in range(4))  # type: ignore[return-value]


def resolve_palette(name: str, palettes_dir: Path, _seen: Optional[list[str]] = None) -> dict[str, object]:
    """Load a palette by name, resolving `extends` inheritance into a flat char->value map.

    A value is a hex string, ``None`` (transparent), or a gradient object
    ``{from, to, axis}``. Raises RenderError on a missing file, an `extends` cycle,
    excessive depth, a malformed hex value, or an invalid gradient object.
    """
    _seen = _seen or []
    if name in _seen:
        chain = " -> ".join(_seen + [name])
        raise RenderError(f"palette '{name}': extends cycle ({chain})")
    if len(_seen) > MAX_EXTENDS_DEPTH:
        raise RenderError(f"palette '{name}': extends chain exceeds depth {MAX_EXTENDS_DEPTH}")

    path = palettes_dir / f"{name}.json"
    if not path.is_file():
        raise RenderError(f"palette '{name}' not found at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"palette '{name}': invalid JSON: {exc}") from exc

    colors: dict[str, object] = {}
    base = data.get("extends")
    if base:
        colors.update(resolve_palette(base, palettes_dir, _seen + [name]))

    own = data.get("colors", {})
    if not isinstance(own, dict):
        raise RenderError(f"palette '{name}': 'colors' must be an object")
    for char, value in own.items():
        if isinstance(value, dict):
            validate_gradient(name, char, value)
        elif value is not None and not HEX_RE.match(str(value)):
            raise RenderError(f"palette '{name}': char '{char}' has invalid hex '{value}'")
        colors[char] = value
    return colors


# --------------------------------------------------------------------------- #
# shapes
# --------------------------------------------------------------------------- #

def is_power_of_two(n) -> bool:
    """True iff n is a positive integer power of two (rejects bools, non-ints, <= 0)."""
    return isinstance(n, int) and not isinstance(n, bool) and n > 0 and (n & (n - 1)) == 0


def resolve_dims(data: dict, default_size: int = DEFAULT_SIZE) -> tuple[int, int]:
    """Resolve a shape's (width, height) in pixels. Raises RenderError on invalid dims.

    A shape may declare its canvas as a square ``"size": N`` (shorthand) OR a rectangle
    ``"width": W, "height": H`` (e.g. a 16x32 character), but not both. If it declares
    neither, it inherits the project default ``default_size`` (square). Each dimension
    must independently be a positive power of two, so 16x16, 16x32, and 32x16 are all valid.
    """
    has_wh = ("width" in data) or ("height" in data)
    has_size = "size" in data
    if has_wh and has_size:
        raise RenderError("specify either 'size' or 'width'/'height', not both")
    if has_wh:
        w, h = data.get("width"), data.get("height")
        if w is None or h is None:
            raise RenderError("both 'width' and 'height' are required when either is given")
    elif has_size:
        w = h = data["size"]
    else:
        w = h = default_size
    for label, val in (("width", w), ("height", h)):
        if not is_power_of_two(val):
            raise RenderError(f"{label} must be a positive power of two, got {val!r}")
    return w, h


def load_shape(path: Path, default_size: int = DEFAULT_SIZE) -> dict:
    """Read and structurally validate a shape file. Raises RenderError on any problem."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"{path.name}: invalid JSON: {exc}") from exc

    stem = path.stem
    if data.get("id") != stem:
        raise RenderError(f"{path.name}: id '{data.get('id')}' must match filename stem '{stem}'")
    try:
        width, height = resolve_dims(data, default_size)
    except RenderError as exc:
        raise RenderError(f"{path.name}: {exc}") from exc

    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != height:
        raise RenderError(f"{path.name}: 'rows' must be exactly {height} rows, got {len(rows) if isinstance(rows, list) else type(rows).__name__}")
    for y, row in enumerate(rows):
        if not isinstance(row, str) or len(row) != width:
            raise RenderError(f"{path.name} row {y}: must be exactly {width} chars, got {len(row) if isinstance(row, str) else type(row).__name__}")

    outputs = data.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        raise RenderError(f"{path.name}: 'outputs' must be a non-empty object")
    return data


def render_shape(shape: dict, palettes_dir: Path, size: int = DEFAULT_SIZE) -> dict[str, "Image.Image"]:
    """Build one RGBA image per output. Validates char coverage against each palette.

    Flat chars (hex / null) paint a single color. A char whose palette value is a
    gradient object is collected and painted in a second pass: its color is interpolated
    along the gradient axis across the extent of that char's own cells (so the ramp fills
    the form wherever the char is placed). A single-line extent resolves to `from`.
    """
    rows: list[str] = shape["rows"]
    width, height = resolve_dims(shape, size)
    images: dict[str, Image.Image] = {}
    for output_name, palette_name in shape["outputs"].items():
        palette = resolve_palette(palette_name, palettes_dir)
        img = Image.new("RGBA", (width, height), TRANSPARENT)
        px = img.load()
        gradient_pixels: dict[str, list[tuple[int, int]]] = {}
        for y, row in enumerate(rows):
            for x, char in enumerate(row):
                if char == TRANSPARENT_CHAR:
                    continue
                if char not in palette:
                    raise RenderError(
                        f"{shape['id']}.json row {y} col {x}: char '{char}' not defined in resolved palette '{palette_name}'"
                    )
                value = palette[char]
                if isinstance(value, dict):
                    gradient_pixels.setdefault(char, []).append((x, y))
                else:
                    px[x, y] = hex_to_rgba(value)
        for char, pixels in gradient_pixels.items():
            grad = palette[char]
            axis = grad["axis"]
            c_from = hex_to_rgba(grad["from"])
            c_to = hex_to_rgba(grad["to"])
            coords = [axis_coord(x, y, axis) for x, y in pixels]
            cmin, cmax = min(coords), max(coords)
            span = cmax - cmin
            for x, y in pixels:
                t = 0.0 if span == 0 else (axis_coord(x, y, axis) - cmin) / span
                px[x, y] = lerp_rgba(c_from, c_to, t)
        images[output_name] = img
    return images


def render_file(shape_path: Path, palettes_dir: Path, out_dir: Path, size: int = DEFAULT_SIZE,
                write: bool = True, collect: Optional[dict[str, "Image.Image"]] = None) -> list[str]:
    """Validate + render one shape file, writing PNGs into out_dir. Returns output names.

    If ``collect`` is given, each rendered image is also stored into it by output name
    (used to gather every sprite for a packed spritesheet without rendering twice).
    """
    shape = load_shape(shape_path, size)
    images = render_shape(shape, palettes_dir, size)
    written: list[str] = []
    for output_name, img in images.items():
        if write:
            out_dir.mkdir(parents=True, exist_ok=True)
            img.save(out_dir / f"{output_name}.png", format="PNG", optimize=True)
        if collect is not None:
            collect[output_name] = img
        written.append(output_name)
    return written


def validate_all(shapes_dir: Path, palettes_dir: Path, size: int = DEFAULT_SIZE) -> list[str]:
    """Validate every shape (and the palettes it references) without writing. Returns error messages."""
    errors: list[str] = []
    for shape_path in sorted(shapes_dir.glob("*.json")):
        try:
            render_file(shape_path, palettes_dir, out_dir=Path("."), size=size, write=False)
        except RenderError as exc:
            errors.append(str(exc))
    return errors


# --------------------------------------------------------------------------- #
# spritesheet packing (TexturePacker / Aseprite-compatible JSON atlas)
# --------------------------------------------------------------------------- #

def _frame_tags(names: list[str]) -> list[dict]:
    """Group contiguous ``<base>_f<n>`` frames into forward animation tags.

    Names are assumed already sorted, so frames of one animation are adjacent and in
    index order. A run of >= 2 frames sharing a base becomes one frameTag spanning the
    frame indices it occupies (Aseprite/TexturePacker convention).
    """
    tags: list[dict] = []
    i, n = 0, len(names)
    while i < n:
        m = FRAME_SUFFIX.match(names[i])
        if not m:
            i += 1
            continue
        base = m.group("base")
        j = i + 1
        while j < n:
            mj = FRAME_SUFFIX.match(names[j])
            if not mj or mj.group("base") != base:
                break
            j += 1
        if j - i >= 2:
            tags.append({"name": base, "from": i, "to": j - 1, "direction": "forward"})
        i = j
    return tags


def _pack_layout(names: list[str], dims: dict[str, tuple[int, int]],
                 cols: Optional[int]) -> tuple[list[tuple[str, int, int]], int, int]:
    """Compute (name, x, y) placements + sheet size for the packed sheet.

    When every sprite is the same size, lay them on a near-square grid (honoring an
    explicit ``cols``) for a tidy uniform sheet. When sizes differ (e.g. 16x16 tiles
    mixed with a 16x32 character), fall back to deterministic left-to-right shelf
    packing wrapped near a square overall width.
    """
    if len(set(dims.values())) == 1:
        w, h = dims[names[0]]
        n = len(names)
        if not cols or cols < 1:
            cols = max(1, math.ceil(math.sqrt(n)))
        rows = math.ceil(n / cols)
        placements = [(name, (i % cols) * w, (i // cols) * h) for i, name in enumerate(names)]
        return placements, cols * w, rows * h

    total_area = sum(w * h for w, h in dims.values())
    target_w = max(max(w for w, _ in dims.values()), math.ceil(math.sqrt(total_area)))
    placements: list[tuple[str, int, int]] = []
    x = y = shelf_h = sheet_w = 0
    for name in names:
        w, h = dims[name]
        if x > 0 and x + w > target_w:
            y += shelf_h
            x = shelf_h = 0
        placements.append((name, x, y))
        x += w
        shelf_h = max(shelf_h, h)
        sheet_w = max(sheet_w, x)
    return placements, sheet_w, y + shelf_h


def build_atlas(images: dict[str, "Image.Image"], size: Optional[int] = None,
                cols: Optional[int] = None,
                image_name: str = f"{DEFAULT_PACK_NAME}.png") -> tuple["Image.Image", dict]:
    """Pack sprites (any mix of sizes) into one sheet + a JSON atlas dict.

    Sprites are placed in name-sorted (deterministic) order -- a uniform set goes on a
    near-square grid (override columns with ``cols``); a mixed-size set is shelf-packed.
    The atlas matches the TexturePacker / Aseprite JSON-hash schema (loads as-is in
    Phaser, PixiJS, Godot, Unity), carrying each frame's true rect plus `frameTags`
    derived from any `<base>_f<n>` frame names. ``size`` is accepted for call
    compatibility but per-frame dimensions are read from the images themselves.
    """
    names = sorted(images)
    if not names:
        raise RenderError("no sprites to pack")
    dims = {name: images[name].size for name in names}
    placements, sheet_w, sheet_h = _pack_layout(names, dims, cols)

    sheet = Image.new("RGBA", (sheet_w, sheet_h), TRANSPARENT)
    frames: dict[str, dict] = {}
    for name, x, y in placements:
        w, h = dims[name]
        sheet.paste(images[name], (x, y))
        frames[name] = {
            "frame": {"x": x, "y": y, "w": w, "h": h},
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h},
            "sourceSize": {"w": w, "h": h},
            "duration": 100,
        }
    atlas = {
        "frames": frames,
        "meta": {
            "app": ATLAS_APP,
            "version": "1.0",
            "image": image_name,
            "format": ATLAS_FORMAT,
            "size": {"w": sheet_w, "h": sheet_h},
            "scale": "1",
            "frameTags": _frame_tags(names),
        },
    }
    return sheet, atlas


def write_pack(images: dict[str, "Image.Image"], out_dir: Path, size: int,
               pack_name: str = DEFAULT_PACK_NAME, cols: Optional[int] = None) -> tuple[Path, Path]:
    """Build + write the packed sheet PNG and its JSON atlas into out_dir. Returns both paths."""
    image_name = f"{pack_name}.png"
    sheet, atlas = build_atlas(images, size, cols, image_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / image_name
    json_path = out_dir / f"{pack_name}.json"
    sheet.save(png_path, format="PNG", optimize=True)
    json_path.write_text(json.dumps(atlas, indent=2) + "\n", encoding="utf-8")
    return png_path, json_path


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class Config:
    """Resolved render configuration. Dirs are absolute Paths."""
    size: int
    shapes_dir: Path
    palettes_dir: Path
    out_dir: Path


def _read_config_json(path: Path) -> dict:
    """Read + strict-validate a config JSON object. Raises ConfigError."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: config must be a JSON object")
    unknown = set(data) - CONFIG_KEYS
    if unknown:
        raise ConfigError(f"{path.name}: unknown config key(s): {sorted(unknown)}")
    return data


def load_config(project_root: Path, config_path: Optional[Path], overrides: dict) -> Config:
    """Resolve effective configuration from an optional file plus CLI overrides.

    project_root: directory that relative paths resolve against by default (the CWD).
    config_path:  explicit --config path; if given it must exist, and its parent directory
                  anchors relative paths in the file. If None, look for pixel-sprite.config.json
                  in project_root.
    overrides:    dict with keys size/shapes_dir/palettes_dir/out_dir; None values are ignored.

    A missing config file is an error UNLESS at least one CLI override is supplied. Built-in
    defaults fill any key not set by the file or CLI. `size` must be a positive power of two.
    """
    file_data: dict = {}
    found = False
    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(f"config file not found: {config_path}")
        file_data = _read_config_json(config_path)
        found = True
        anchor = config_path.resolve().parent
    else:
        default_path = project_root / CONFIG_FILENAME
        if default_path.is_file():
            file_data = _read_config_json(default_path)
            found = True
        anchor = project_root

    has_cli = any(v is not None for v in overrides.values())
    if not found and not has_cli:
        raise ConfigError(
            f"No {CONFIG_FILENAME} found in {project_root} and no CLI overrides given. "
            f"Run /pixel-sprite-generator:init to scaffold one, or pass --config / --out-dir etc."
        )

    merged = dict(DEFAULT_CONFIG)
    merged.update(file_data)
    merged.update({k: v for k, v in overrides.items() if v is not None})

    size = merged["size"]
    if not is_power_of_two(size):
        raise ConfigError(f"size must be a positive power of two, got {size!r}")

    return Config(
        size=size,
        shapes_dir=(anchor / merged["shapes_dir"]).resolve(),
        palettes_dir=(anchor / merged["palettes_dir"]).resolve(),
        out_dir=(anchor / merged["out_dir"]).resolve(),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render shape JSON grids into square PNG sprites.")
    parser.add_argument("--config", metavar="PATH", help="path to a pixel-sprite.config.json (default: ./pixel-sprite.config.json)")
    parser.add_argument("--only", metavar="ID", help="render a single shape by id (filename stem)")
    parser.add_argument("--check", action="store_true", help="validate all shapes+palettes; write nothing")
    parser.add_argument("--size", type=int, help="override sprite size (must be a power of two)")
    parser.add_argument("--shapes-dir", help="override shapes directory")
    parser.add_argument("--palettes-dir", help="override palettes directory")
    parser.add_argument("--out-dir", help="override output directory")
    parser.add_argument("--pack", action="store_true",
                        help="also pack the rendered sprites into one spritesheet PNG + a "
                             "TexturePacker/Aseprite-compatible JSON atlas")
    parser.add_argument("--pack-name", default=DEFAULT_PACK_NAME,
                        help=f"basename for the packed sheet + atlas (default: {DEFAULT_PACK_NAME})")
    parser.add_argument("--pack-cols", type=int, default=None,
                        help="columns in the packed sheet (default: near-square)")
    args = parser.parse_args(argv)

    project_root = Path.cwd()
    overrides = {
        "size": args.size,
        "shapes_dir": args.shapes_dir,
        "palettes_dir": args.palettes_dir,
        "out_dir": args.out_dir,
    }
    try:
        cfg = load_config(project_root, Path(args.config) if args.config else None, overrides)
    except ConfigError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 2

    if not cfg.shapes_dir.is_dir():
        sys.stderr.write(f"Error: shapes directory not found: {cfg.shapes_dir}\n")
        return 2
    if not cfg.palettes_dir.is_dir():
        sys.stderr.write(f"Error: palettes directory not found: {cfg.palettes_dir}\n")
        return 2

    if args.check:
        errors = validate_all(cfg.shapes_dir, cfg.palettes_dir, cfg.size)
        if errors:
            for e in errors:
                sys.stderr.write(f"  {e}\n")
            sys.stderr.write(f"\n{len(errors)} shape(s) invalid.\n")
            return 1
        count = len(list(cfg.shapes_dir.glob("*.json")))
        print(f"All {count} shape(s) in {cfg.shapes_dir} are valid.")
        return 0

    if args.only:
        shape_path = cfg.shapes_dir / f"{args.only}.json"
        if not shape_path.is_file():
            sys.stderr.write(f"Error: shape not found: {shape_path}\n")
            return 2
        shape_paths = [shape_path]
    else:
        shape_paths = sorted(cfg.shapes_dir.glob("*.json"))
        if not shape_paths:
            print(f"No shape files in {cfg.shapes_dir}")
            return 0

    total = 0
    collected: Optional[dict[str, "Image.Image"]] = {} if args.pack else None
    try:
        for shape_path in shape_paths:
            written = render_file(shape_path, cfg.palettes_dir, cfg.out_dir, cfg.size, collect=collected)
            for name in written:
                print(f"  rendered {name}.png")
            total += len(written)
        if args.pack and collected:
            png_path, json_path = write_pack(collected, cfg.out_dir, cfg.size,
                                             args.pack_name, args.pack_cols)
            print(f"  packed {len(collected)} frame(s) -> {png_path.name} + {json_path.name}")
    except RenderError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    print(f"\nDone. {total} sprite(s) written to {cfg.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
