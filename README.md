# Pixel Sprite Generator

Generate square pixel-art sprites for 2D games from semantic JSON grids and color palettes.
Deterministic and self-contained -- it renders real PNGs with Pillow; there is no external
image model.

Author: MisterVitoPro

## What you get

- **Skill** `pixel-sprite-generator` -- how to author shape grids + palettes and render them.
- **Command** `/pixel-sprite-generator:init` -- scaffold a project (config, dirs, worked example).
- **Renderer** `scripts/render_sprites.py` -- config-driven JSON-grid -> PNG converter.
- **Templates** -- a default config plus an example gem sprite + palette.

## Requirements

- Python 3
- Pillow: `pip install Pillow`

## Quick start

1. Install this plugin's marketplace and enable the plugin (see the marketplace README).
2. In your game project, run `/pixel-sprite-generator:init` to create `pixel-sprite.config.json`,
   the `art/shapes` and `art/palettes` directories, an `assets/sprites` output directory, and a
   worked example.
3. Render the example: `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only gem`
4. Ask Claude to "generate the <id> sprite" -- the skill authors the grid and renders the PNG.

## Configuration (`pixel-sprite.config.json`)

```json
{
  "size": 16,
  "shapes_dir": "art/shapes",
  "palettes_dir": "art/palettes",
  "out_dir": "assets/sprites"
}
```

`size` must be a power of two (8, 16, 32, 64, ...). Every CLI flag (`--size`, `--shapes-dir`,
`--palettes-dir`, `--out-dir`, `--config`) overrides the file for a single run. Use `--check` to
validate all shapes and palettes without writing.
