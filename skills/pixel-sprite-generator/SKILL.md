---
name: pixel-sprite-generator
description: Use when generating a square pixel-art sprite/icon/texture for a 2D game or app from a semantic JSON grid. Authors a shape grid (<shapes_dir>/<id>.json) plus color palettes and renders them to real PNGs via the bundled scripts/render_sprites.py -- no external image model. Triggers on requests like "generate the player-idle sprite", "make a 32x32 icon for X", "render the <id> sprite", "pixel art for X", or any ask pairing a sprite id with image/texture/icon generation in a project that uses pixel-sprite.config.json.
---

# Pixel Sprite Generator

## Overview

This skill produces real square RGBA PNG sprites through a deterministic, self-contained
pipeline -- there is NO external image model and NO manual downscale/chroma-key step:

```
  you author                          converter renders
  <shapes_dir>/<id>.json        +     scripts/render_sprites.py   ->  <out_dir>/<output>.png
    (semantic char grid)              (validates, maps chars->hex,     (one PNG per `outputs`
  <palettes_dir>/<name>.json           writes strict NxN RGBA)          entry)
    (char -> hex color / gradient)
```

You author a JSON pixel-grid by hand; the Python converter turns it into the final PNG(s).
Every output is exactly `size` x `size` (a power of two from the project config; default 16) --
the converter hard-fails on anything else.

The script is bundled with this plugin. Invoke it from the project root as:
`python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" [flags]`

## Project configuration

The renderer reads `pixel-sprite.config.json` from the project root:

```json
{ "size": 16, "shapes_dir": "art/shapes", "palettes_dir": "art/palettes", "out_dir": "assets/sprites" }
```

`size` must be a power of two. CLI flags (`--size`, `--shapes-dir`, `--palettes-dir`,
`--out-dir`, `--config`) override the file per run. If a project has no config yet, run
`/pixel-sprite-generator:init` to scaffold the config, directories, and a worked example.

## The workflow (follow every time)

1. **Resolve inputs**: a `sprite_id` and which palette(s)/variants to produce. If ambiguous,
   ask once.
2. **Author the shape grid** at `<shapes_dir>/<sprite_id>.json`: a `size` x `size` grid using
   semantic chars (see the char convention). Set the `outputs` map: one entry per output PNG,
   naming the palette for each.
3. **Render**: `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only <sprite_id>`.
4. **LOOK at it** (do not skip -- `--check` only proves it is NxN with valid chars, NOT that it
   reads as the subject). Upscale and view it, e.g.:
   ```python
   from PIL import Image
   Image.open("<out_dir>/<id>.png").resize((256, 256), Image.NEAREST).save(".tmp_preview.png")
   # then Read .tmp_preview.png
   ```
   Confirm it reads as the intended subject and has NO detached/floating pixels. Revise and
   re-render if it does not. Delete any `.tmp_*` preview files when done.
5. **Report**: confirm the PNG paths written.

Do NOT hand the user a prompt to paste elsewhere. Do NOT open an image editor. The deliverable
is the committed shape JSON plus the rendered PNG(s).

## Shape file schema (`<shapes_dir>/<id>.json`)

```json
{
  "id": "gem",
  "size": 16,
  "outputs": { "gem": "example", "gem_rare": "example_rare" },
  "rows": ["................", ".......oo.......", "...14 more rows..."]
}
```

- `id` MUST equal the filename stem.
- `size` MUST equal the configured size.
- `outputs` maps each output PNG basename (no extension) to a palette name. One shape can
  produce several PNGs (e.g. material/rarity variants) from one grid.
- `rows` MUST be exactly `size` strings of exactly `size` chars. `.` is transparent.

The converter hard-fails (no silent fixes) on: wrong row count/length, a char not in the
resolved palette, a `size` mismatch, an `id` that does not match the filename, a missing
palette, or an `extends` cycle. Run `render_sprites.py --check` to validate without writing.

## Semantic char convention (recommended starter vocabulary)

A char names a **role**, not a color -- the palette supplies the color per variant, so one grid
renders to every variant. This table is a sensible default for shaded objects; adopt, extend,
or replace it for your game's needs (just define every non-`.` char you use in each referenced
palette).

| char | role |
|---|---|
| `.` | transparent |
| `B` | primary highlight |
| `b` | primary midtone |
| `s` | primary shadow (lower-right edge) |
| `o` | dark outline (object border) |
| `g` | body gradient, diagonal (light top-left -> dark bottom-right) |
| `v` | body gradient, vertical (light top -> dark bottom) |
| `a` | accent / inlay |

Only `.` may appear without being defined in the palette.

## Palettes (`<palettes_dir>/<name>.json`)

```json
{ "extends": null, "colors": { "B": "#9BE7FF", "b": "#4FB8E8", "a": "#FFD27D" } }
```

Colors are `#RRGGBB` / `#RRGGBBAA` (or `null` to force transparent). `extends` names a base
palette whose colors are inherited then overridden -- use it so a variant that changes one
accent does not duplicate a whole palette.

### Gradients

A char's value may be a **gradient object** instead of a flat hex; the converter interpolates a
smooth ramp across that char's cells:

```json
"g": { "from": "#9BE7FF", "to": "#2A6CA6", "axis": "diag" }
```

- `axis` is one of: `x` (left->right), `y` (top->bottom), `diag` (top-left->bottom-right),
  `adiag` (bottom-left->top-right). `from`/`to` are `#RRGGBB` / `#RRGGBBAA` (alpha interpolates;
  `null` is not allowed inside a gradient -- use `.` for transparency).
- The ramp is measured over the extent of that char's own cells along the axis: the cell nearest
  the axis start gets `from`, the farthest gets `to`, the rest interpolate. A single-line extent
  resolves to `from`. Reach for gradients on wide forms; keep flat `B`/`b`/`s` on thin 2px shapes.

## Composition & readability (hard-won lessons)

`--check` passing means the grid is valid, not that it looks like the subject. After rendering,
LOOK at the upscaled PNG and apply these:

- **Cell-shaded with an implied upper-left light source:** `B` highlight on the top/left edge of
  a form, `b` for the body, `s` on the bottom/right. Stair-step diagonals one pixel at a time; no
  smooth curves.
- **It must read as ITS subject, not a generic blob.** Give distinctive parts enough cells to be
  recognizable (a 1-2 cell "head" is ambiguous).
- **No detached/floating pixels.** Every opaque cell must be 8-connected to the body. Stray
  accents, gaps between a part and the body, and floating details are the most common failures --
  trace connectivity row by row.
- **Thin is fine when the subject is thin;** don't pad genuinely slender shapes. Abstract icons
  may be blobby -- they have no "correct" silhouette.
- **Match visual weight across a family** so sprites meant to sit together don't look broken.
- **Keep it readable at small sizes:** 4-6 colors per material region.

## Batch requests

For "all the gems" / "every rarity variant", author each shape file, then render all at once:
`python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py"` (no `--only`). Each shape's `outputs`
produces its own PNG(s).

## Common mistakes to avoid

- **Don't emit a prompt for an external image model.** This skill renders the PNG locally.
- **Don't author off-size grids.** Exactly `size` rows x `size` chars; the converter hard-fails.
- **Don't use an undefined char.** Every non-`.` char must exist in the referenced palette(s).
- **Don't inline one-off colors.** New material -> new palette file (use `extends` for single-
  accent variants).
- **Don't ship a grid you haven't LOOKED at.** `--check` passes off-model art happily. Render,
  upscale, and view it before reporting.
- **Don't leave floating pixels.** Every opaque cell must touch the body (8-connected).
