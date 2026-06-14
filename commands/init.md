---
description: Scaffold the current project to use pixel-sprite-generator (config, dirs, example).
---

# Initialize pixel-sprite-generator for this project

Scaffold the current project (`${CLAUDE_PROJECT_DIR}`) so it can render pixel sprites with the
bundled engine. Do the following, in order:

1. **Config file.** If `${CLAUDE_PROJECT_DIR}/pixel-sprite.config.json` does NOT exist, copy
   `${CLAUDE_PLUGIN_ROOT}/templates/pixel-sprite.config.json` to it. If it already exists, do
   NOT overwrite it -- report the existing values and continue.

2. **Read the resolved config** (`size`, `shapes_dir`, `palettes_dir`, `out_dir`).

3. **Create directories** under the project root: `shapes_dir`, `palettes_dir`, and `out_dir`
   (create parents as needed; skip any that already exist).

4. **Copy the worked example** into the project (only if the destination files do not already
   exist):
   - `${CLAUDE_PLUGIN_ROOT}/templates/palettes/example.json` -> `<palettes_dir>/example.json`
   - `${CLAUDE_PLUGIN_ROOT}/templates/shapes/gem.json` -> `<shapes_dir>/gem.json`

5. **Verify** by validating (writes nothing):
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --check`
   Report the result. If Pillow is missing, tell the user to run `pip install Pillow`.

6. **Print next steps:**
   - Author a shape grid at `<shapes_dir>/<id>.json` (the `pixel-sprite-generator` skill explains
     the schema and conventions).
   - Render with: `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only <id>`
   - Render everything with no `--only`.
   - PNGs are written to `<out_dir>`.

Keep the output concise: list what you created vs. skipped, and the render command to use next.
