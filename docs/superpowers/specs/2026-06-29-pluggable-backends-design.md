# Pluggable Image Backends — Design Spec

**Date:** 2026-06-29
**Author:** MisterVitoPro
**Status:** Approved (design); pending implementation plan

## Goal

Make the pixel-sprite pipeline able to generate against **any** HTTP image backend
(SwarmUI, Automatic1111, OpenAI-compatible servers, etc.) driven entirely by the YAML
project config -- with **no per-backend Python code**. The current implementation is
hard-wired to the OpenAI `/v1/images/generations` request/response shape; this replaces
that single shape with a small declarative HTTP engine.

## Non-goals

- ComfyUI-style submit-then-poll loops (deferred). The supported flow is a single
  optional prep request + one main request + optional image fetch. SwarmUI (which wraps
  ComfyUI) is covered, so this is not a practical gap today.
- A backend plugin system in Python. Everything backend-specific is config.
- Changing the deterministic grid renderer, post-processing, prompt template, or the
  `art/sprites/<id>.yaml` spec format. Those are unchanged.

## Decisions (from brainstorming)

1. **Fully generic / config-driven** -- no per-backend Python; the config describes the
   request and response mapping.
2. **Flow depth: 2-step + fetch-by-URL** -- an optional `prep` request feeds the main
   `request`; the image is located by json-path and is either inline base64 or a
   relative/absolute URL the engine then GETs.
3. **Clean break + shipped presets** -- replace the OpenAI-specific `image:` block with a
   generic `backend:` block; ship `openai`, `a1111`, `swarmui` presets; bump to 0.3.0.

## Architecture

All changes live in `scripts/imagegen.py` and `scripts/config.py`. The orchestrator
(`scripts/render_sprites.py`) changes only the field name `cfg.image` -> `cfg.backend`;
the `generate(...)` call seam and the exit-code-3 contract are preserved.

### The engine (in `imagegen.py`)

`generate(positive, negative, backend_cfg, seed) -> PIL.Image` keeps its current
signature and behavior contract (returns an RGBA image; raises `BackendUnavailable` on
any failure -> exit code 3). Internally it is a declarative engine built from four
stdlib-only helpers:

- `_render(template, vars)` -- recursively walks a dict/list/str template substituting
  `${var}` placeholders from `vars`. Whole-value placeholders (a value that is exactly
  `"${x}"`) preserve the native type of the substituted value; embedded placeholders
  (`"a ${prompt}"`) stringify. A placeholder that resolves to `None` causes its key to be
  dropped from the enclosing mapping.
- `_http(method, url, headers, body, timeout) -> (status, bytes)` -- urllib request;
  returns raw bytes (JSON parsed by the caller). Connection/timeout/OSError ->
  `BackendUnavailable`.
- `_extract(obj, path)` -- dotted/indexed lookup into parsed JSON, e.g. `data.0.b64_json`
  or `images.0`. Missing path -> `BackendUnavailable`.
- Flow:
  1. If `prep` is present: render+send it, parse JSON, and `capture` named vars from its
     response (`var_name: <json-path>`), adding them to `vars`.
  2. Render and send `request`; parse JSON.
  3. `_extract(response, response.image_path)` -> an image reference.
  4. Resolve the reference per `response.image_kind`:
     - `base64`: strip an optional `data:*;base64,` prefix and decode.
     - `url`: join with `response.fetch_base` (if relative) and GET the bytes.
     - `auto`: data-URI or successfully-decodable string -> base64; otherwise -> url.
  5. `Image.open(BytesIO(bytes)).convert("RGBA")`.

`auth` (if present) contributes one header to every request and the fetch GET.

### Placeholder set

Available in all templates (`prep`, `request`, `auth`):

| Placeholder | Resolves to |
|---|---|
| `${prompt}`, `${negative}` | built positive / negative prompt strings |
| `${model}` | `backend.model` |
| `${gen_size}`, `${gen_width}`, `${gen_height}` | generation resolution from `backend.gen_size` (square) |
| `${seed}` | per-output seed or `None` (None -> key dropped) |
| `${env:NAME}` | environment variable `NAME`; unset -> empty |
| captured vars (e.g. `${session_id}`) | values pulled from the `prep` response via `capture` |

If `${env:NAME}` in an `auth.value` resolves to empty, the entire `auth` header is
dropped (so a keyless local server works without edits).

## Config schema (`backend:` block)

Replaces the `image:` block. Strict validation (unknown keys rejected) consistent with
the existing config policy.

```yaml
backend:
  model: "sd_xl_base_1.0"     # optional; ${model}
  gen_size: 1024              # generation resolution; postprocess still downscales to sprite size
  timeout: 120

  prep:                       # OPTIONAL first request (omit for OpenAI/A1111)
    method: POST
    url: "http://127.0.0.1:7801/API/GetNewSession"
    headers: {}
    body: {}
    capture:                  # var_name: <json-path in prep response>
      session_id: "session_id"

  request:                    # REQUIRED generate call
    method: POST
    url: "http://127.0.0.1:7801/API/GenerateText2Image"
    headers: { Content-Type: application/json }
    body:                     # free-form template
      session_id: "${session_id}"
      images: 1
      prompt: "${prompt}"
      negativeprompt: "${negative}"
      width: ${gen_width}
      height: ${gen_height}
      seed: ${seed}

  response:                   # REQUIRED
    image_path: "images.0"    # json-path to image ref
    image_kind: auto          # base64 | url | auto
    fetch_base: "http://127.0.0.1:7801/"  # prepended to relative urls when image_kind resolves to url

  auth:                       # OPTIONAL; applied to every request + fetch
    header: "Authorization"
    value: "Bearer ${env:OPENAI_API_KEY}"
```

### Dataclasses (`config.py`)

`ImageConfig` is removed. New: `BackendConfig` with nested `PrepSpec`, `RequestSpec`,
`ResponseSpec`, `AuthSpec`.

- `RequestSpec`: `method:str`, `url:str`, `headers:dict`, `body` (dict/any template).
- `PrepSpec`: `method`, `url`, `headers`, `body`, `capture:dict[str,str]`.
- `ResponseSpec`: `image_path:str`, `image_kind:str` (one of base64|url|auto),
  `fetch_base:Optional[str]`.
- `AuthSpec`: `header:str`, `value:str`.
- `BackendConfig`: `model:Optional[str]`, `gen_size:int`, `timeout:int`,
  `prep:Optional[PrepSpec]`, `request:RequestSpec`, `response:ResponseSpec`,
  `auth:Optional[AuthSpec]`.

`Config.image` becomes `Config.backend`. DEFAULTS updated so a minimal config still
loads (a backend `request`+`response` are required; `prep`/`auth` optional).

## Shipped presets (`templates/backends/`)

- `openai.yaml` -- single POST, inline base64 at `data.0.b64_json` (reproduces today's
  behavior); optional `Bearer ${env:IMAGE_API_KEY}` auth (dropped if unset).
- `a1111.yaml` -- `POST /sdapi/v1/txt2img`, base64 at `images.0`.
- `swarmui.yaml` -- `prep` GetNewSession -> capture `session_id` ->
  GenerateText2Image -> image ref at `images.0` fetched via `fetch_base`.

The rewritten `init` interview asks which backend and copies the chosen preset's
`backend:` block into the generated `pixel-sprite.config.yaml`.

## Error handling & security

- Runtime failure -> `BackendUnavailable` -> exit 3, message naming the stage
  (unreachable/timeout, non-2xx with status + truncated body, missing json-path,
  undecodable image). Config error -> `ConfigError` -> exit 2 at load time.
- No `eval`: substitution is literal value/string replacement into JSON. `${env:}` only
  reads the environment.
- `auth.value` is redacted in error output. Keys come only from env, never the file.
- Fetch-by-URL must resolve to `http`/`https` (reject `file://` etc.); decoded/fetched
  bytes capped (~64 MB).

## Testing

TDD, stdlib monkeypatching of `urllib.request.urlopen`:

- `_render`: type preservation, None-drops-key, `${env:}` resolution, embedded vs
  whole-value, captured vars.
- `_extract`: dotted + indexed access; missing path raises.
- `generate` flows: (a) inline-base64 OpenAI/A1111 shape; (b) 2-step SwarmUI
  prep -> generate -> fetch-by-URL (two POSTs + one GET mocked); (c) auth header present,
  and dropped when `${env:}` unset; (d) `BackendUnavailable` on non-2xx, missing
  image_path, and unreachable host; (e) fetch URL scheme guard rejects `file://`.
- `config.py`: `BackendConfig` parse, required `request`/`response`, `image_kind` enum,
  strict unknown-key rejection.
- `test_orchestrator` stays green (monkeypatches `generate`; signature unchanged).

## Migration / packaging

- `config.py`, `imagegen.py`, `render_sprites.py` updated as above.
- `templates/pixel-sprite.config.yaml` rewritten with the generic `backend:` block;
  `templates/backends/{openai,a1111,swarmui}.yaml` added; `init` interview updated.
- README + SKILL.md document the backend schema, placeholder set, and preset table.
- `plugin.json` version -> `0.3.0`.
- Showcase unchanged (`mode: grid`).
- Constraints retained: PyYAML + Pillow + stdlib only (no `requests`, no jsonpath lib).

## Constraints (unchanged from the original plan)

- No emojis in code or docs. Author/handle `MisterVitoPro`.
- New runtime deps: none beyond existing PyYAML + Pillow; HTTP via stdlib `urllib`.
- Colors `#RRGGBB`/`#RRGGBBAA`; sprite dims positive powers of two.
- Strict config validation: unknown keys rejected.
