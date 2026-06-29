---
name: pixel-sprite-generator
description: Use when generating a square pixel-art sprite/icon/texture for a 2D game or app. Generates from a subject prompt spec (image-gen first) with a deterministic grid as fallback, driven by pixel-sprite.config.yaml. Triggers on requests like "generate the player-idle sprite", "make a 32x32 icon for X", "render the <id> sprite", "pixel art for X", or any ask pairing a sprite id with image/texture/icon generation in a project that uses pixel-sprite.config.yaml.
---

# Pixel Sprite Generator

## Overview

This skill produces square RGBA PNG sprites through an image-generation-first pipeline: it
calls a local OpenAI-compatible image model and post-processes the result into a small pixel-art
PNG. If the backend is unreachable it exits with code 3 and you stop to ask the user about the
deterministic grid fallback.

```
  you author                            scripts/render_sprites.py
  art/sprites/<id>.yaml          ->     image model (default)        ->  <out_dir>/<output>.png
    (subject prompt + outputs)          OR
  art/shapes/<id>.json (fallback)       deterministic grid renderer
  art/palettes/<name>.json              (on --mode grid / --fallback-grid)
```

**Pick the size to fit the subject.** Tiles, props, and item icons are 16x16; a standing
character needs 16x32 (a person crammed into 16x16 reads squished). Bump to 32x32 for
large/detailed subjects.

The script is bundled with this plugin. Invoke it from the project root as:
`python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" [flags]`

## Project configuration

The renderer reads `pixel-sprite.config.yaml` from the project root. A minimal config:

```yaml
size: 16
sprites_dir: art/sprites
shapes_dir: art/shapes
palettes_dir: art/palettes
out_dir: assets/sprites
```

CLI flags (`--size`, `--sprites-dir`, `--shapes-dir`, `--palettes-dir`, `--out-dir`,
`--config`) override the file per run. If a project has no config yet, run
`/pixel-sprite-generator:init` to scaffold the config, directories, and a worked example.

## The workflow (follow every time)

1. **Resolve a `sprite_id`.** If ambiguous, ask once.
2. **Author or locate `art/sprites/<id>.yaml`** -- a subject `prompt` plus optional `size`,
   `negative`, `gen`, and `outputs` map (see "Sprite spec schema" below).
3. **Run the renderer:**
   ```
   python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only <id>
   ```
4. **Vision review loop** -- do not skip:
   ```python
   from PIL import Image
   Image.open("<out_dir>/<id>.png").resize((256, 256), Image.NEAREST).save(".tmp_preview.png")
   # then Read .tmp_preview.png
   ```
   Upscale and LOOK at the PNG. If off-model, revise the spec's `prompt`, `negative`, or
   `gen.seed` and re-run. Delete any `.tmp_*` preview files when done.
5. **On exit code 3** (backend unavailable): STOP. Ask the user whether to render the grid
   fallback. Grid fallback requires `art/shapes/<id>.json`; if absent, offer to author one (see
   "Grid fallback" below). Then run:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only <id> --mode grid
   ```
   or, to auto-fallback on any future backend failure:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only <id> --fallback-grid
   ```
6. **Report the written PNG paths.**

Use `--check` at any time to validate config and sources without writing:
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --check
```

Do NOT hand the user a prompt to paste elsewhere. The deliverable is the authored spec plus the
rendered PNG(s).

## Sprite spec schema (`art/sprites/<id>.yaml`)

```yaml
id: gem
size: 16                       # optional; inherits project default
prompt: "single gemstone, octagonal facets, top-down view, glowing cyan teal core"
negative: "blur, photorealistic, 3d render"
gen:
  seed: 42                     # optional; omit for random
outputs:
  gem: {}                      # output PNG basename -> options
  gem_rare:
    recolor: gem_rare_palette  # cheap palette swap, same silhouette
  gem_cursed:
    regenerate: true           # fresh image call; different form allowed
    seed: 99
```

- `id` must equal the filename stem.
- `outputs` maps each output PNG basename to options (`recolor`, `regenerate`, `seed`).
- Omit `size` to inherit the project default.

## Writing subject prompts

The `prompt.prefix`, `prompt.suffix`, and `negative` fields in `pixel-sprite.config.yaml`
carry the house style (pixel art, RGBA, NxN, etc.) so each spec holds **only the subject**.
Write for these qualities:

- **Silhouette**: name the defining shape first. "single sword, cruciform silhouette" is better
  than "cool weapon".
- **View angle**: state it. "front-facing", "3/4 overhead", or "side view". Pixel art is
  unforgiving about ambiguous perspective.
- **Palette/material words**: "glowing cyan", "rusted iron", "hand-painted wood grain". These
  drive color more reliably than color names alone.
- **Lighting**: implied upper-left source is conventional for pixel art; say "top-left lit" or
  "cell-shaded" if the house style does not already say so.
- **Small-size readability**: avoid fine detail that collapses to noise at 16x16. Prefer words
  like "bold silhouette", "minimal detail", "iconic shape".
- **Negative**: list anything the model reaches for that you don't want -- "blur, photorealistic,
  3d render, gradient background, shadow under".

### Variants

`recolor:` (cheap, silhouette-identical)
: Swaps pixel colors by mapping the base image's palette to a named target palette. The
  silhouette and texel positions are unchanged. Use for material or rarity tints where the form
  should be identical.

`regenerate: true` (fresh call, differing form)
: Issues a new image generation call with the same subject prompt. The resulting form may differ
  (different pose, slightly different silhouette). Use when you want genuine variation, not just
  a tint. Pair with a per-output `seed` for reproducibility.

## Grid fallback

When the image backend is unavailable (exit code 3) or the user prefers deterministic output,
render from a hand-authored shape grid instead. This path is unchanged from earlier versions
and requires `art/shapes/<id>.json`.

### Shape file schema (`art/shapes/<id>.json`)

```json
{
  "id": "gem",
  "size": 16,
  "outputs": { "gem": "example", "gem_rare": "example_rare" },
  "rows": ["................", ".......oo.......", "...14 more rows..."]
}
```

A non-square shape uses `width`/`height` instead of `size`:

```json
{ "id": "hero", "width": 16, "height": 32, "outputs": { "hero": "hero" }, "rows": ["...32 rows of 16 chars..."] }
```

- `id` must equal the filename stem.
- Dimensions: give EITHER `size` (square shorthand) OR `width` + `height` (rectangle), never
  both. Each must be a power of two.
- `outputs` maps each output PNG basename to a palette name.
- `rows` must be exactly `height` strings of exactly `width` chars. `.` is transparent.

The converter hard-fails (no silent fixes) on: wrong row count/length, an undefined char, a
non-power-of-two dimension, specifying both `size` and `width`/`height`, an `id` mismatch, a
missing palette, or an `extends` cycle.

### Semantic char convention (recommended starter vocabulary)

A char names a **role**, not a color -- the palette supplies the color per variant.

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

### Palettes (`art/palettes/<name>.json`)

```json
{ "extends": null, "colors": { "B": "#9BE7FF", "b": "#4FB8E8", "a": "#FFD27D" } }
```

Colors are `#RRGGBB` / `#RRGGBBAA` (or `null` to force transparent). `extends` names a base
palette to inherit then override -- use it so a one-accent variant does not duplicate a whole
palette.

#### Gradients

A char's value may be a gradient object; the converter interpolates a smooth ramp across that
char's cells:

```json
"g": { "from": "#9BE7FF", "to": "#2A6CA6", "axis": "diag" }
```

`axis` is one of: `x` (left->right), `y` (top->bottom), `diag` (top-left->bottom-right),
`adiag` (bottom-left->top-right). `null` is not allowed inside a gradient -- use `.` for
transparency. Reach for gradients on wide forms; keep flat `B`/`b`/`s` on thin 2px shapes.

### Forcing grid mode

To render grid sources for all sprites at once (skip image model entirely):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --mode grid
```

To render a single sprite's grid source:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only <id> --mode grid
```

## Batch requests

For "all the gems" / "every sprite in the set", author each spec file, then render all at once:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py"
```

Each spec's `outputs` produces its own PNG(s).

## Packing a spritesheet + atlas (`--pack`)

Real 2D games ship a single packed spritesheet plus a metadata atlas, not one PNG per sprite
at runtime. Add `--pack` to emit these alongside the individual PNGs:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --pack
```

This writes `<out_dir>/spritesheet.png` (every rendered sprite on a name-sorted, near-square
grid) and `<out_dir>/spritesheet.json` -- a TexturePacker / Aseprite JSON-hash atlas that
loads as-is in Phaser, PixiJS, Godot, and Unity. Flags: `--pack-name <basename>` (default
`spritesheet`) and `--pack-cols <n>` (default near-square). Use `--pack` WITHOUT `--only` so
the sheet contains the whole set.

**Animations:** name outputs `<base>_f0`, `<base>_f1`, ... and the packer groups contiguous
frames into an Aseprite `frameTags` entry automatically -- a multi-frame walk cycle becomes a
named animation in the atlas with no extra config.

## Common mistakes to avoid

- **Do not emit a prompt for an external image model.** Run render_sprites.py locally instead.
- **Do not skip the vision review loop.** `--check` proves the spec is valid, not that it looks
  right. Render, upscale, and look at it before reporting.
- **On exit code 3, stop and ask.** Do not silently fall back to the grid without user consent
  unless `--fallback-grid` was already requested.
- **Do not leave `.tmp_*` preview files.** Delete them after the review loop.
- **Do not inline one-off colors in a palette.** New material -> new palette file; use `extends`
  for single-accent variants.
- **Do not author off-size grids.** Exactly `height` rows of exactly `width` chars; the
  converter hard-fails.
- **Do not leave floating pixels in grid sources.** Every opaque cell must be 8-connected to
  the body. Trace connectivity row by row.
