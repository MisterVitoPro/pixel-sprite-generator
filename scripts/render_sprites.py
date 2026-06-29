#!/usr/bin/env python3
"""Orchestrate sprite generation: local image model first, deterministic grid as fallback.

For each sprite the default (image) path loads its art/sprites/<id>.yaml spec, builds a
prompt from the project's prompt template, calls the local OpenAI-compatible image model,
and post-processes the result into a small RGBA PNG. When the backend is unreachable the
process exits with code 3 (or, with --fallback-grid, renders art/shapes/<id>.json instead).

Usage:
  python render_sprites.py                 # generate every sprite spec
  python render_sprites.py --only hero
  python render_sprites.py --mode grid     # force the deterministic grid renderer
  python render_sprites.py --check         # validate config + grid sources, write nothing
  python render_sprites.py --pack          # also emit a packed spritesheet + atlas

Exit codes: 0 ok, 1 validation failure, 2 environment/config error, 3 backend unavailable.
Requires: Pillow, PyYAML.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.stderr.write("Error: Pillow is not installed. Install it with:\n  pip install Pillow\n")
    sys.exit(2)

import config as cfgmod
import imagegen as ig
import postprocess as ppmod
import render_grid as rg


def generate_sprite(spec: "ig.SpriteSpec", cfg, collect: Optional[dict] = None) -> list[str]:
    """Image path for one spec. Generates the base once; recolors cheap material variants,
    regenerates only outputs flagged regenerate:true. Returns written output names."""
    width, height = ig.resolve_dims(spec, cfg.size)
    base_seed = spec.gen.get("seed", cfg.image.params.get("seed"))
    base_img: Optional[Image.Image] = None
    written: list[str] = []
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    for name, opts in spec.outputs.items():
        opts = opts or {}
        if opts.get("regenerate"):
            pos, neg = ig.build_prompt(spec, opts, cfg.prompt)
            raw = ig.generate(pos, neg, cfg.image, opts.get("seed", base_seed))
            img = ppmod.process(raw, cfg.postprocess, width, height, cfg.palettes_dir)
        else:
            if base_img is None:
                pos, neg = ig.build_prompt(spec, {}, cfg.prompt)
                raw = ig.generate(pos, neg, cfg.image, base_seed)
                base_img = ppmod.process(raw, cfg.postprocess, width, height, cfg.palettes_dir)
            recolor_name = opts.get("recolor")
            if recolor_name:
                img = ppmod.recolor(base_img.copy(), ppmod.load_target_palette(cfg.palettes_dir, recolor_name))
            else:
                img = base_img
        img.save(cfg.out_dir / f"{name}.png", format="PNG", optimize=True)
        if collect is not None:
            collect[name] = img
        written.append(name)
        print(f"  generated {name}.png")
    return written


def _grid_fallback(sprite_id: str, cfg, collect) -> list[str]:
    shape_path = cfg.shapes_dir / f"{sprite_id}.json"
    if not shape_path.is_file():
        raise rg.RenderError(
            f"no grid fallback source for '{sprite_id}' at {shape_path}")
    return rg.render_file(shape_path, cfg.palettes_dir, cfg.out_dir, cfg.size, collect=collect)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Generate pixel sprites (image model first, grid fallback).")
    p.add_argument("--config")
    p.add_argument("--only", metavar="ID")
    p.add_argument("--check", action="store_true")
    p.add_argument("--mode", choices=cfgmod.MODES)
    p.add_argument("--fallback-grid", action="store_true",
                   help="on backend failure, render the grid source instead of exiting 3")
    p.add_argument("--size", type=int)
    p.add_argument("--sprites-dir")
    p.add_argument("--shapes-dir")
    p.add_argument("--palettes-dir")
    p.add_argument("--out-dir")
    p.add_argument("--pack", action="store_true")
    p.add_argument("--pack-name", default=rg.DEFAULT_PACK_NAME)
    p.add_argument("--pack-cols", type=int, default=None)
    args = p.parse_args(argv)

    overrides = {
        "size": args.size, "mode": args.mode, "sprites_dir": args.sprites_dir,
        "shapes_dir": args.shapes_dir, "palettes_dir": args.palettes_dir, "out_dir": args.out_dir,
    }
    try:
        cfg = cfgmod.load_config(Path.cwd(), Path(args.config) if args.config else None, overrides)
    except cfgmod.ConfigError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 2

    if args.check:
        return _run_check(cfg)

    mode = cfg.mode
    collected: Optional[dict] = {} if args.pack else None

    if mode == "grid":
        return _run_grid(cfg, args, collected)

    # image-first path
    if not cfg.sprites_dir.is_dir():
        sys.stderr.write(f"Error: sprites directory not found: {cfg.sprites_dir}\n")
        return 2
    if args.only:
        spec_paths = [cfg.sprites_dir / f"{args.only}.yaml"]
        if not spec_paths[0].is_file():
            sys.stderr.write(f"Error: sprite spec not found: {spec_paths[0]}\n")
            return 2
    else:
        spec_paths = sorted(cfg.sprites_dir.glob("*.yaml"))
        if not spec_paths:
            print(f"No sprite specs in {cfg.sprites_dir}")
            return 0

    total = 0
    try:
        for sp in spec_paths:
            spec = ig.load_spec(sp)
            try:
                total += len(generate_sprite(spec, cfg, collected))
            except ig.BackendUnavailable as exc:
                if args.fallback_grid:
                    sys.stderr.write(f"Warning: {exc}\n  falling back to grid for {spec.id}\n")
                    total += len(_grid_fallback(spec.id, cfg, collected))
                else:
                    sys.stderr.write(
                        f"Error: {exc}\n"
                        f"  Run with --mode grid or --fallback-grid to render the grid source.\n")
                    return 3
        if args.pack and collected:
            png, js = rg.write_pack(collected, cfg.out_dir, cfg.size, args.pack_name, args.pack_cols)
            print(f"  packed {len(collected)} frame(s) -> {png.name} + {js.name}")
    except (ig.SpecError, rg.RenderError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    print(f"\nDone. {total} sprite(s) written to {cfg.out_dir}")
    return 0


def _run_grid(cfg, args, collected) -> int:
    if not cfg.shapes_dir.is_dir():
        sys.stderr.write(f"Error: shapes directory not found: {cfg.shapes_dir}\n")
        return 2
    if args.only:
        paths = [cfg.shapes_dir / f"{args.only}.json"]
        if not paths[0].is_file():
            sys.stderr.write(f"Error: shape not found: {paths[0]}\n")
            return 2
    else:
        paths = sorted(cfg.shapes_dir.glob("*.json"))
        if not paths:
            print(f"No shape files in {cfg.shapes_dir}")
            return 0
    total = 0
    try:
        for sp in paths:
            total += len(rg.render_file(sp, cfg.palettes_dir, cfg.out_dir, cfg.size, collect=collected))
        if args.pack and collected:
            rg.write_pack(collected, cfg.out_dir, cfg.size, args.pack_name, args.pack_cols)
    except rg.RenderError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    print(f"\nDone. {total} sprite(s) written to {cfg.out_dir}")
    return 0


def _run_check(cfg) -> int:
    problems: list[str] = []
    if cfg.shapes_dir.is_dir():
        problems += rg.validate_all(cfg.shapes_dir, cfg.palettes_dir, cfg.size)
    if cfg.sprites_dir.is_dir():
        for sp in sorted(cfg.sprites_dir.glob("*.yaml")):
            try:
                spec = ig.load_spec(sp)
                ig.resolve_dims(spec, cfg.size)
            except ig.SpecError as exc:
                problems.append(str(exc))
    if problems:
        for e in problems:
            sys.stderr.write(f"  {e}\n")
        sys.stderr.write(f"\n{len(problems)} source(s) invalid.\n")
        return 1
    print("All sources valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
