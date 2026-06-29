---
description: Scaffold the current project to use pixel-sprite-generator (interactive interview, dirs, example files).
---

# Initialize pixel-sprite-generator for this project

Scaffold `${CLAUDE_PROJECT_DIR}` so it can render pixel sprites with the bundled engine.
Work through the steps below in order.

---

## Step 1: Check for an existing config

Look for `${CLAUDE_PROJECT_DIR}/pixel-sprite.config.yaml`.

- **If it already exists:** read and display all its values. Do NOT overwrite it. Skip to Step 3.
- **If it is absent:** continue to Step 2 (the interview).

---

## Step 2: Interview the user (one question at a time)

Ask each question separately, waiting for the answer before moving on.
Use the default shown if the user does not provide a value.

1. "Is a local image-generation backend available right now? (yes / no, default: no)"
   - If **no**: set `mode: grid` and skip the endpoint / model / gen_size questions.
   - If **yes**: continue with the next questions.

2. "What is the endpoint URL for the image API?
   (default: http://localhost:8080/v1/images/generations)"

3. "What model name should be requested?
   (default: sd-pixel)"

4. "What generation resolution should be requested from the model in pixels?
   (default: 512)"

5. "What output sprite size in pixels should the final PNGs be?
   Must be a positive power of two: 8, 16, 32, 64 ...
   (default: 16)"

6. "Where should sprite spec YAML files live (relative to the project root)?
   (default: art/sprites)"
   Store as `sprites_dir`.

7. "Where should shape grid JSON files live?
   (default: art/shapes)"
   Store as `shapes_dir`.

8. "Where should palette JSON files live?
   (default: art/palettes)"
   Store as `palettes_dir`.

9. "Where should rendered PNG output files be written?
   (default: assets/sprites)"
   Store as `out_dir`.

After collecting answers, write `${CLAUDE_PROJECT_DIR}/pixel-sprite.config.yaml`.
Use only the keys that differ from the template defaults; always include `size` and `mode`.
For image-gen enabled configs also include the `image` block with `endpoint`, `model`, and `gen_size`.

---

## Step 3: Create project directories

Create the following directories under `${CLAUDE_PROJECT_DIR}` (create parents as needed;
skip any that already exist):

- `sprites_dir`
- `shapes_dir`
- `palettes_dir`
- `out_dir`

---

## Step 4: Copy example files

Copy each of the following only if the destination does not already exist (never overwrite):

- `${CLAUDE_PLUGIN_ROOT}/templates/sprites/hero.yaml` -> `<sprites_dir>/hero.yaml`
- `${CLAUDE_PLUGIN_ROOT}/templates/palettes/example.json` -> `<palettes_dir>/example.json`
- `${CLAUDE_PLUGIN_ROOT}/templates/shapes/gem.json` -> `<shapes_dir>/gem.json`

---

## Step 5: Verify the setup

Run the bundled orchestrator in check mode (validates config and sources; writes nothing):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --check
```

Report the output. If the command fails with an import error:
- Missing **Pillow**: `pip install Pillow`
- Missing **PyYAML**: `pip install PyYAML`

---

## Step 6: Print next steps

After a successful check, print a concise summary of what was created vs. skipped, then:

```
Next steps:
  1. Author a sprite spec at <sprites_dir>/<id>.yaml
     (see the example at <sprites_dir>/hero.yaml)
  2. Render a single sprite:
       python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only <id>
  3. Render all sprites:
       python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py"
  4. For the deterministic shape-grid path (no backend needed), use --mode grid.
  PNGs are written to <out_dir>.
```
