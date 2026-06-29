# Design: Image-gen-first pixel sprite pipeline

Date: 2026-06-28
Author: MisterVitoPro
Status: Approved (brainstorming) -- pending implementation plan

## Summary

Today `pixel-sprite-generator` is a fully deterministic JSON-grid -> PNG renderer
configured by `pixel-sprite.config.json`, with no external image model. This change
makes a **local image-generation model the default** generation path, keeps the existing
deterministic grid renderer as a **fallback**, and moves project configuration to **YAML**.
It also adds a prompt-template system, a vision review loop in the skill, and rewritten
authoring guidance.

A sprite is now described by a **per-sprite prompt spec** (`art/sprites/<id>.yaml`). The
orchestrator builds a final prompt from a configurable house-style template plus the
sprite's subject, POSTs it to a local OpenAI-compatible HTTP image endpoint, then
post-processes the returned image into a true small game sprite (downscale -> background
removal -> palette quantize). If the backend is unreachable or errors, the skill stops and
asks the user before rendering the JSON-grid fallback.

## Goals

- Image generation via a local OpenAI-compatible HTTP model is the default path.
- The existing deterministic grid renderer remains as a fallback, unchanged in behavior.
- Project config moves to `pixel-sprite.config.yaml` (hard cut; JSON config dropped).
- A configurable prompt-template system applies a reusable house style across sprites.
- A vision review loop and stronger prompt-authoring guidance in the skill.
- `/init` becomes interactive: it interviews the user to personalize the backend config
  per machine.

## Non-goals

- No MCP integration. The pipeline stays a standalone script that anyone (user, Claude, CI)
  can run directly; HTTP keeps that property.
- No support for non-OpenAI-compatible backends in this iteration (ComfyUI/A1111 native
  schemas). The client targets `/v1/images/generations`-style request/response.
- No automatic, silent grid fallback in `auto` mode -- a human checkpoint is required
  (except via the explicit `--fallback-grid` flag for CI).

## Architecture

The bundled script becomes an **orchestrator** that selects a generation path per sprite:

```
art/sprites/<id>.yaml ──► [IMAGE PATH]  build prompt ─► POST local HTTP model
  (prompt + params)         (default)    ─► post-process (downscale→key→quantize) ─► PNG
                                                 │ backend unreachable / errors
                                                 ▼  (exit 3 → skill asks user)
art/shapes/<id>.json  ──► [GRID PATH]  existing deterministic renderer ─► PNG
  (semantic grid)          (fallback)
```

### Module layout (`scripts/`)

The current ~600-line `render_sprites.py` is purely the grid renderer. To keep files
focused, split into cohesive modules:

- `render_sprites.py` -- thin **CLI / orchestrator** and the stable bundled entry point
  (`python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py"`). Owns mode selection,
  fallback handling, and `--pack`.
- `render_grid.py` -- the existing deterministic renderer (palettes, shapes, render, pack)
  moved here essentially unchanged.
- `config.py` -- YAML config loading + strict validation -> a `Config` dataclass.
- `imagegen.py` -- prompt builder (template + spec) and the OpenAI-compatible HTTP client
  (stdlib `urllib`, no `requests` dependency).
- `postprocess.py` -- downscale, background -> transparency, palette quantize, recolor.

New dependency: **PyYAML** (only addition beyond Pillow; HTTP uses stdlib `urllib`).

## Configuration: `pixel-sprite.config.yaml`

Hard cut from JSON; the loader reads YAML only. `size` and each gen/post-process step is
configurable. Unknown top-level keys are rejected (strict validation, matching the current
config behavior).

```yaml
size: 16
mode: auto                 # auto = image then ask-before-grid-fallback | image | grid
sprites_dir: art/sprites   # NEW: image-gen prompt specs
shapes_dir: art/shapes     # grid fallback sources
palettes_dir: art/palettes
out_dir: assets/sprites

image:
  endpoint: http://localhost:8080/v1/images/generations
  model: sd-pixel
  api_key_env: null        # optional env-var NAME to read a key from; never the key itself
  timeout: 120
  gen_size: 512            # model output resolution before downscale
  params: { steps: 30, cfg_scale: 7, sampler: euler_a, seed: null }

prompt:
  prefix: "pixel art sprite of"
  suffix: "centered, plain magenta background, crisp pixels, limited palette, no anti-aliasing"
  negative: "blurry, photorealistic, drop shadow, extra limbs, watermark, text"

postprocess:
  downscale: nearest                                              # nearest | box | lanczos
  background: { method: chroma, color: "#FF00FF", tolerance: 20 } # chroma | alpha_threshold | none
  quantize:   { enabled: true, colors: 16, palette: null }        # palette: optional fixed-palette file
  outline: false

pack: { enabled: false, name: spritesheet }
```

Notes:
- `mode: auto` = image path, then **stop and ask the user** before grid fallback on backend
  failure. `image` = image only (exit 3 on failure). `grid` = force the deterministic path.
- `api_key_env` names an environment variable; the key value never lives in the committed
  config. Most local backends need no key (leave `null`).
- `gen_size` is the model's output resolution; post-processing downscales it to `size`.

## Sprite spec: `art/sprites/<id>.yaml`

```yaml
id: hero                  # MUST equal filename stem
prompt: "a brave knight in a green tunic, front-facing idle pose"
size: 32                  # optional; or width/height. Overrides project size for this sprite
negative: "..."           # optional; merged over config.prompt.negative
gen: { seed: 12345 }      # optional per-sprite param overrides (merged over image.params)
outputs:                  # default when omitted: a single output named <id>
  hero: {}                                              # base generation
  hero_gold:   { recolor: golden }                      # post-process recolor of the base (no extra model call)
  hero_attack: { regenerate: true, prompt_suffix: "attacking pose", seed: 99 }
```

### Prompt assembly

Final positive prompt for an output =
`config.prompt.prefix` + " " + `spec.prompt` + (output `prompt_suffix` if any) + " " +
`config.prompt.suffix`.

Final negative prompt = `config.prompt.negative` merged with the spec's `negative`
(spec terms appended).

The **template** (prefix/suffix/negative) is the reusable house style in config; the
**subject** is the per-sprite `prompt`.

### Variants (two mechanisms)

- **Recolor variants (default for materials):** generate the base sprite once, then
  post-process recolors it to a named target palette under `palettes_dir`. Silhouette is
  identical across variants and costs **one** model call -- the grid path's
  one-source-many-materials trick applied to a generated base.
- **Regenerated variants:** an output with `regenerate: true` makes a fresh model call with
  its own `prompt_suffix`/`seed`. For variants that differ in form/pose, not just color;
  these are not silhouette-identical.

## Image backend (`imagegen.py`)

- OpenAI-compatible `POST <endpoint>` with a JSON body carrying `model`, `prompt`,
  `negative_prompt` (when supported), size, and merged params; expects a JSON response with
  base64 PNG image data (`/v1/images/generations` shape).
- Uses stdlib `urllib.request` with the configured `timeout`.
- Reads an API key from `image.api_key_env` if set (`Authorization: Bearer`).
- Distinct failure handling: connection refused / DNS / timeout / non-2xx / malformed
  response -> raised as a typed `BackendUnavailable` error that the orchestrator maps to
  **exit code 3**.

## Post-processing (`postprocess.py`)

Applied in order to the model's returned image to produce the final `size` x `size` RGBA PNG:

1. **Downscale** `gen_size` -> target dims using the configured resampling
   (`nearest` | `box` | `lanczos`).
2. **Background -> transparency**: `chroma` (key out `color` within `tolerance`),
   `alpha_threshold` (use existing alpha), or `none`.
3. **Quantize**: reduce to at most `colors`, or remap to a fixed `palette` file when given.
4. **Recolor** (per recolor-variant): remap the quantized base to a named target palette.
5. **Outline** (optional): add a 1px dark outline around the opaque silhouette.

Each step is independently configurable and individually testable on synthetic PIL images.

## Fallback contract

- The script is **non-interactive**. On backend failure it exits **code 3** with a clear
  message naming the endpoint.
- `--fallback-grid` flag: on backend failure, automatically render the JSON-grid fallback
  instead of exiting 3 (for CI / unattended runs).
- The **skill** owns the human checkpoint: on exit 3 it stops and asks the user whether to
  render the grid fallback.
- Grid fallback for a sprite requires a matching `art/shapes/<id>.json`. If absent, the
  skill reports that the fallback source is missing and offers to author one.

### Exit codes

- `0` success / all valid
- `1` validation failure (malformed shape, spec, or palette)
- `2` environment/config error (Pillow/PyYAML missing, dirs missing, invalid config)
- `3` image backend unavailable (mode `auto`/`image`, no `--fallback-grid`)

## Skill changes (`SKILL.md`)

- **Default to the image path**: resolve a sprite id -> author/locate
  `art/sprites/<id>.yaml` -> run the orchestrator -> review.
- **Vision review loop**: after a successful generation, upscale and LOOK at the PNG; if
  off-model, revise the spec's `prompt`/`negative`/`seed` and re-run. Reuses the existing
  "LOOK at it" discipline, now feeding prompt edits.
- **Stronger prompt-authoring guidance**: concrete direction for subject prompts --
  silhouette, view angle, palette, lighting, and what reads at small sizes.
- **Fallback handling**: on exit 3, stop and ask the user before grid rendering; document
  the grid path as the fallback (its grid/palette schema guidance is retained).

## Command + templates changes

- `/init` becomes interactive: it interviews the user (image-gen available? endpoint URL,
  model, gen size, key params, output dirs), then writes a personalized
  `pixel-sprite.config.yaml`, creates `sprites_dir`/`shapes_dir`/`palettes_dir`/`out_dir`,
  and scaffolds an example sprite spec (plus the existing grid example as the fallback
  sample). Verifies with `--check`. Reports missing PyYAML/Pillow.
- `templates/`: add `pixel-sprite.config.yaml` and an example `sprites/<id>.yaml`; keep the
  grid example for the fallback path.

## Testing

pytest under `scripts/`:

- **config**: YAML load, default fill, strict unknown-key rejection, `size` power-of-two,
  invalid-mode/endpoint errors.
- **prompt building**: template + spec assembly, `prompt_suffix` and `negative` merging,
  per-output overrides.
- **post-process units** on synthetic PIL images: downscale -> exact NxN; chroma-key ->
  expected pixels transparent; quantize -> <= N colors; recolor -> target palette; outline.
- **HTTP client**: monkeypatch `urllib` to return a fake base64 image (assert request body
  shape) and to raise (assert `BackendUnavailable` -> exit 3).
- **orchestrator**: mode selection, `--fallback-grid` behavior, missing-fallback-source
  reporting.
- **grid path**: retain existing `render_grid` tests.

## Migration

- Convert `examples/showcase` to `pixel-sprite.config.yaml` and add a sample sprite spec
  alongside the existing grid sources (which now serve as fallback examples).
- Update `README.md` to document the image-first flow, YAML config, prompt template,
  post-processing, and the fallback contract.
- Bump plugin description/version as appropriate.

## Risks / open considerations

- Local backends vary; the OpenAI-compatible assumption may need a thin adapter later for
  ComfyUI/A1111 native APIs. Isolated in `imagegen.py` so a future backend is a localized
  change.
- Diffusion output quality at tiny sizes depends heavily on prompt + post-process tuning;
  the vision loop and configurable steps mitigate but do not eliminate this.
- Recolor variants assume a clean quantized base; noisy generations may recolor poorly --
  the quality is bounded by the base generation.
