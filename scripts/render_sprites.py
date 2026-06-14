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


def load_shape(path: Path, size: int = DEFAULT_SIZE) -> dict:
    """Read and structurally validate a shape file. Raises RenderError on any problem."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"{path.name}: invalid JSON: {exc}") from exc

    stem = path.stem
    if data.get("id") != stem:
        raise RenderError(f"{path.name}: id '{data.get('id')}' must match filename stem '{stem}'")
    if data.get("size") != size:
        raise RenderError(f"{path.name}: size must be {size}, got {data.get('size')}")

    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != size:
        raise RenderError(f"{path.name}: 'rows' must be exactly {size} rows, got {len(rows) if isinstance(rows, list) else type(rows).__name__}")
    for y, row in enumerate(rows):
        if not isinstance(row, str) or len(row) != size:
            raise RenderError(f"{path.name} row {y}: must be exactly {size} chars, got {len(row) if isinstance(row, str) else type(row).__name__}")

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
    images: dict[str, Image.Image] = {}
    for output_name, palette_name in shape["outputs"].items():
        palette = resolve_palette(palette_name, palettes_dir)
        img = Image.new("RGBA", (size, size), TRANSPARENT)
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


def render_file(shape_path: Path, palettes_dir: Path, out_dir: Path, size: int = DEFAULT_SIZE, write: bool = True) -> list[str]:
    """Validate + render one shape file, writing PNGs into out_dir. Returns output names."""
    shape = load_shape(shape_path, size)
    images = render_shape(shape, palettes_dir, size)
    written: list[str] = []
    for output_name, img in images.items():
        if write:
            out_dir.mkdir(parents=True, exist_ok=True)
            img.save(out_dir / f"{output_name}.png", format="PNG", optimize=True)
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
    try:
        for shape_path in shape_paths:
            written = render_file(shape_path, cfg.palettes_dir, cfg.out_dir, cfg.size)
            for name in written:
                print(f"  rendered {name}.png")
            total += len(written)
    except RenderError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    print(f"\nDone. {total} sprite(s) written to {cfg.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
