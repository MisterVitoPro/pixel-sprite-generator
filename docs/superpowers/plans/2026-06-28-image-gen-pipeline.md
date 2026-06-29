# Image-gen-first Pixel Sprite Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a local OpenAI-compatible image model the default sprite-generation path, with the existing deterministic JSON-grid renderer as a fallback, all driven by a YAML project config.

**Architecture:** The bundled `render_sprites.py` becomes a thin CLI/orchestrator over four focused modules: `config.py` (YAML config), `render_grid.py` (the existing deterministic renderer, moved), `imagegen.py` (prompt building + HTTP client), and `postprocess.py` (downscale/key/quantize/recolor). A sprite is described by `art/sprites/<id>.yaml`; the orchestrator builds a prompt, POSTs it to the local model, post-processes the result, and on backend failure exits with code 3 so the skill can ask the user before falling back to a grid render.

**Tech Stack:** Python 3, Pillow (existing), PyYAML (new), stdlib `urllib` for HTTP, pytest.

## Global Constraints

- No emojis in code or docs.
- Author/handle in any project file is `MisterVitoPro`; never a real name or email.
- New runtime dependency limited to **PyYAML** only; HTTP must use stdlib `urllib` (no `requests`).
- Project config format is `pixel-sprite.config.yaml` (YAML only; the old JSON config is dropped).
- The bundled invocation path stays `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py"`.
- Exit codes: `0` success, `1` validation failure, `2` environment/config error, `3` image backend unavailable.
- Colors are `#RRGGBB` / `#RRGGBBAA`. Sprite dimensions are positive powers of two.
- Strict config validation: unknown top-level config keys are rejected.

---

## File Structure

- `scripts/config.py` (new) -- YAML config load + strict validation -> `Config` dataclass tree.
- `scripts/render_grid.py` (new, moved) -- the existing deterministic renderer (palettes, shapes, render, pack) verbatim from today's `render_sprites.py`.
- `scripts/postprocess.py` (new) -- downscale, background removal, quantize, recolor, outline.
- `scripts/imagegen.py` (new) -- sprite-spec load, prompt building, OpenAI-compatible HTTP client.
- `scripts/render_sprites.py` (rewritten) -- CLI/orchestrator: mode selection, image path, fallback, `--pack`.
- `scripts/test_config.py`, `scripts/test_postprocess.py`, `scripts/test_imagegen.py`, `scripts/test_orchestrator.py` (new tests).
- `scripts/test_render_grid.py` (moved from `test_render_sprites.py`, imports updated to `render_grid`).
- `templates/pixel-sprite.config.yaml`, `templates/sprites/hero.yaml` (new); keep `templates/shapes/gem.json`, `templates/palettes/example.json`.
- `commands/init.md` (rewritten interactive interview).
- `examples/showcase/pixel-sprite.config.yaml` (replaces `.json`) + `examples/showcase/art/sprites/hero.yaml` (new sample).
- `skills/pixel-sprite-generator/SKILL.md`, `README.md`, `.claude-plugin/plugin.json` (updated).

---

### Task 1: Move the grid renderer into `render_grid.py`

Pure refactor: relocate the existing renderer so `render_sprites.py` can become the orchestrator. Keep behavior identical and tests green.

**Files:**
- Create: `scripts/render_grid.py`
- Rename: `scripts/test_render_sprites.py` -> `scripts/test_render_grid.py`
- Modify: `scripts/render_sprites.py` (temporarily re-export so nothing else breaks yet)

**Interfaces:**
- Produces (in `render_grid`): `RenderError`, `resolve_palette(name, palettes_dir)`, `load_shape(path, default_size)`, `resolve_dims(data, default_size)`, `render_shape(shape, palettes_dir, size)`, `render_file(shape_path, palettes_dir, out_dir, size, write=True, collect=None) -> list[str]`, `validate_all(shapes_dir, palettes_dir, size) -> list[str]`, `build_atlas(...)`, `write_pack(...)`, `is_power_of_two(n)`, constants `DEFAULT_SIZE`, `TRANSPARENT`.

- [ ] **Step 1: Copy the renderer body into `render_grid.py`**

Copy from today's `scripts/render_sprites.py` everything from the imports through the `write_pack` function (the palette, shape, render, and spritesheet-packing sections) into a new `scripts/render_grid.py`. Drop the `# configuration` and `# CLI` sections (`Config`, `_read_config_json`, `load_config`, `main`, `__main__`) -- those move to `config.py`/orchestrator. Keep the module docstring trimmed to describe only grid rendering. Keep `RenderError`, `ConfigError` is NOT needed here (it moves to `config.py`).

- [ ] **Step 2: Move and re-point the existing tests**

Rename `scripts/test_render_sprites.py` to `scripts/test_render_grid.py`. Change the import line:

```python
import render_grid as rg  # noqa: E402
```

and replace every `rs.` reference with `rg.` for the grid functions (`render_file`, `render_shape`, `resolve_palette`, `load_shape`, `resolve_dims`, `build_atlas`, `write_pack`, `validate_all`, `RenderError`). Delete any test that exercised `load_config`/`Config`/`main` (those are re-covered in Tasks 2 and 6); note their names so Task 2/6 re-add equivalents.

- [ ] **Step 3: Temporary re-export shim in `render_sprites.py`**

At the top of `scripts/render_sprites.py`, temporarily add `from render_grid import *  # noqa` so the file still imports. (Task 6 replaces this file entirely.)

- [ ] **Step 4: Run the moved tests**

Run: `python -m pytest scripts/test_render_grid.py -q`
Expected: PASS (same count as before, minus any config/CLI tests removed).

- [ ] **Step 5: Commit**

```bash
git add scripts/render_grid.py scripts/test_render_grid.py scripts/render_sprites.py
git rm scripts/test_render_sprites.py
git commit -m "refactor: extract grid renderer into render_grid.py"
```

---

### Task 2: YAML config loader (`config.py`)

**Files:**
- Create: `scripts/config.py`
- Test: `scripts/test_config.py`

**Interfaces:**
- Produces: dataclasses `ImageConfig`, `PromptConfig`, `BackgroundConfig`, `QuantizeConfig`, `PostprocessConfig`, `PackConfig`, `Config`; `ConfigError`; `CONFIG_FILENAME = "pixel-sprite.config.yaml"`; `load_config(project_root: Path, config_path: Optional[Path], overrides: dict) -> Config`.
- `Config` fields: `size:int`, `mode:str`, `sprites_dir:Path`, `shapes_dir:Path`, `palettes_dir:Path`, `out_dir:Path`, `image:ImageConfig`, `prompt:PromptConfig`, `postprocess:PostprocessConfig`, `pack:PackConfig`.
- `ImageConfig`: `endpoint:str`, `model:str`, `api_key_env:Optional[str]`, `timeout:int`, `gen_size:int`, `params:dict`.
- `PromptConfig`: `prefix:str`, `suffix:str`, `negative:str`.
- `BackgroundConfig`: `method:str`, `color:str`, `tolerance:int`.
- `QuantizeConfig`: `enabled:bool`, `colors:int`, `palette:Optional[str]`.
- `PostprocessConfig`: `downscale:str`, `background:BackgroundConfig`, `quantize:QuantizeConfig`, `outline:bool`.
- `PackConfig`: `enabled:bool`, `name:str`.
- `overrides` keys: `size`, `mode`, `sprites_dir`, `shapes_dir`, `palettes_dir`, `out_dir` (None values ignored).

- [ ] **Step 1: Write failing tests**

```python
# scripts/test_config.py
from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

MINIMAL = """
size: 16
image:
  endpoint: http://localhost:9000/v1/images/generations
  model: sd-pixel
"""

def write_cfg(tmp_path: Path, text: str) -> Path:
    p = tmp_path / cfg.CONFIG_FILENAME
    p.write_text(text, encoding="utf-8")
    return p

def test_loads_minimal_and_fills_defaults(tmp_path):
    write_cfg(tmp_path, MINIMAL)
    c = cfg.load_config(tmp_path, None, {k: None for k in
        ("size","mode","sprites_dir","shapes_dir","palettes_dir","out_dir")})
    assert c.size == 16
    assert c.mode == "auto"
    assert c.image.endpoint.endswith("/v1/images/generations")
    assert c.image.timeout == 120
    assert c.prompt.prefix  # default house style present
    assert c.postprocess.downscale == "nearest"
    assert c.postprocess.background.method == "chroma"
    assert c.postprocess.quantize.colors == 16
    assert c.sprites_dir.name == "sprites"

def test_unknown_top_level_key_rejected(tmp_path):
    write_cfg(tmp_path, MINIMAL + "\nbogus: 1\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})

def test_size_must_be_power_of_two(tmp_path):
    write_cfg(tmp_path, "size: 24\nimage: {endpoint: x, model: y}\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})

def test_invalid_mode_rejected(tmp_path):
    write_cfg(tmp_path, MINIMAL + "\nmode: sideways\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})

def test_cli_override_wins(tmp_path):
    write_cfg(tmp_path, MINIMAL)
    c = cfg.load_config(tmp_path, None, {"mode": "grid", "size": 32})
    assert c.mode == "grid" and c.size == 32

def test_missing_config_without_overrides_errors(tmp_path):
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_config.py -q`
Expected: FAIL (`No module named 'config'` / attribute errors).

- [ ] **Step 3: Implement `config.py`**

```python
#!/usr/bin/env python3
"""Load and strict-validate the YAML project config into a Config dataclass tree."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    import sys
    sys.stderr.write("Error: PyYAML is not installed. Install it with:\n  pip install PyYAML\n")
    raise SystemExit(2)

CONFIG_FILENAME = "pixel-sprite.config.yaml"
MODES = ("auto", "image", "grid")
DOWNSCALE = ("nearest", "box", "lanczos")
BG_METHODS = ("chroma", "alpha_threshold", "none")

DEFAULTS = {
    "size": 16,
    "mode": "auto",
    "sprites_dir": "art/sprites",
    "shapes_dir": "art/shapes",
    "palettes_dir": "art/palettes",
    "out_dir": "assets/sprites",
    "image": {
        "endpoint": "http://localhost:8080/v1/images/generations",
        "model": "sd-pixel",
        "api_key_env": None,
        "timeout": 120,
        "gen_size": 512,
        "params": {"steps": 30, "cfg_scale": 7, "sampler": "euler_a", "seed": None},
    },
    "prompt": {
        "prefix": "pixel art sprite of",
        "suffix": "centered, plain magenta background, crisp pixels, limited palette, no anti-aliasing",
        "negative": "blurry, photorealistic, drop shadow, extra limbs, watermark, text",
    },
    "postprocess": {
        "downscale": "nearest",
        "background": {"method": "chroma", "color": "#FF00FF", "tolerance": 20},
        "quantize": {"enabled": True, "colors": 16, "palette": None},
        "outline": False,
    },
    "pack": {"enabled": False, "name": "spritesheet"},
}
# sections whose nested keys are validated strictly (params is free-form)
STRICT_SECTIONS = {"image", "prompt", "postprocess", "pack"}


class ConfigError(Exception):
    """Missing/invalid configuration (environment error -> exit code 2)."""


@dataclasses.dataclass
class ImageConfig:
    endpoint: str
    model: str
    api_key_env: Optional[str]
    timeout: int
    gen_size: int
    params: dict


@dataclasses.dataclass
class PromptConfig:
    prefix: str
    suffix: str
    negative: str


@dataclasses.dataclass
class BackgroundConfig:
    method: str
    color: str
    tolerance: int


@dataclasses.dataclass
class QuantizeConfig:
    enabled: bool
    colors: int
    palette: Optional[str]


@dataclasses.dataclass
class PostprocessConfig:
    downscale: str
    background: BackgroundConfig
    quantize: QuantizeConfig
    outline: bool


@dataclasses.dataclass
class PackConfig:
    enabled: bool
    name: str


@dataclasses.dataclass
class Config:
    size: int
    mode: str
    sprites_dir: Path
    shapes_dir: Path
    palettes_dir: Path
    out_dir: Path
    image: ImageConfig
    prompt: PromptConfig
    postprocess: PostprocessConfig
    pack: PackConfig


def _is_pow2(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n > 0 and (n & (n - 1)) == 0


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _check_keys(data: dict) -> None:
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"unknown config key(s): {sorted(unknown)}")
    for section in STRICT_SECTIONS:
        sub = data.get(section)
        if isinstance(sub, dict):
            allowed = set(DEFAULTS[section])
            bad = set(sub) - allowed
            if bad:
                raise ConfigError(f"unknown key(s) in '{section}': {sorted(bad)}")


def load_config(project_root: Path, config_path: Optional[Path], overrides: dict) -> Config:
    file_data: dict = {}
    found = False
    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(f"config file not found: {config_path}")
        anchor = config_path.resolve().parent
        file_data = _load_yaml(config_path)
        found = True
    else:
        default_path = project_root / CONFIG_FILENAME
        anchor = project_root
        if default_path.is_file():
            file_data = _load_yaml(default_path)
            found = True

    has_cli = any(v is not None for v in overrides.values())
    if not found and not has_cli:
        raise ConfigError(
            f"No {CONFIG_FILENAME} found in {project_root} and no CLI overrides given. "
            f"Run /pixel-sprite-generator:init to scaffold one."
        )

    _check_keys(file_data)
    merged = _deep_merge(DEFAULTS, file_data)
    for key in ("size", "mode", "sprites_dir", "shapes_dir", "palettes_dir", "out_dir"):
        if overrides.get(key) is not None:
            merged[key] = overrides[key]

    if not _is_pow2(merged["size"]):
        raise ConfigError(f"size must be a positive power of two, got {merged['size']!r}")
    if merged["mode"] not in MODES:
        raise ConfigError(f"mode must be one of {MODES}, got {merged['mode']!r}")
    pp = merged["postprocess"]
    if pp["downscale"] not in DOWNSCALE:
        raise ConfigError(f"postprocess.downscale must be one of {DOWNSCALE}, got {pp['downscale']!r}")
    if pp["background"]["method"] not in BG_METHODS:
        raise ConfigError(f"postprocess.background.method must be one of {BG_METHODS}")

    return Config(
        size=merged["size"],
        mode=merged["mode"],
        sprites_dir=(anchor / merged["sprites_dir"]).resolve(),
        shapes_dir=(anchor / merged["shapes_dir"]).resolve(),
        palettes_dir=(anchor / merged["palettes_dir"]).resolve(),
        out_dir=(anchor / merged["out_dir"]).resolve(),
        image=ImageConfig(**merged["image"]),
        prompt=PromptConfig(**merged["prompt"]),
        postprocess=PostprocessConfig(
            downscale=pp["downscale"],
            background=BackgroundConfig(**pp["background"]),
            quantize=QuantizeConfig(**pp["quantize"]),
            outline=pp["outline"],
        ),
        pack=PackConfig(**merged["pack"]),
    )


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: config must be a YAML mapping")
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/test_config.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/config.py scripts/test_config.py
git commit -m "feat: YAML project config loader with strict validation"
```

---

### Task 3: Post-processing pipeline (`postprocess.py`)

**Files:**
- Create: `scripts/postprocess.py`
- Test: `scripts/test_postprocess.py`

**Interfaces:**
- Consumes: `config.PostprocessConfig`, `config.BackgroundConfig`, `config.QuantizeConfig`.
- Produces:
  - `downscale(img, width, height, method="nearest") -> Image.Image`
  - `remove_background(img, method, color, tolerance) -> Image.Image`
  - `quantize(img, colors) -> Image.Image`
  - `load_target_palette(palettes_dir, name) -> list[tuple[int,int,int,int]]` (reads a grid palette JSON, returns its non-null colors sorted by luminance)
  - `recolor(img, target) -> Image.Image` (luminance-ordered remap of the image's opaque colors onto `target`)
  - `add_outline(img, color="#000000") -> Image.Image`
  - `process(img, pp: PostprocessConfig, width, height, palettes_dir, recolor_name=None) -> Image.Image` (runs downscale -> background -> quantize -> recolor -> outline)

- [ ] **Step 1: Write failing tests**

```python
# scripts/test_postprocess.py
from __future__ import annotations
import sys
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
import postprocess as pp  # noqa: E402

def solid(w, h, rgba):
    return Image.new("RGBA", (w, h), rgba)

def test_downscale_to_exact_dims():
    out = pp.downscale(solid(64, 64, (10, 20, 30, 255)), 16, 16, "nearest")
    assert out.size == (16, 16)

def test_chroma_key_makes_background_transparent():
    img = solid(4, 4, (255, 0, 255, 255))   # all magenta
    img.putpixel((1, 1), (200, 50, 50, 255))  # one red pixel
    out = pp.remove_background(img, "chroma", "#FF00FF", 20)
    assert out.getpixel((0, 0))[3] == 0       # magenta -> transparent
    assert out.getpixel((1, 1))[3] == 255     # red kept

def test_quantize_reduces_color_count():
    img = Image.new("RGBA", (8, 8))
    for i in range(8):
        for j in range(8):
            img.putpixel((i, j), (i * 30 % 256, j * 30 % 256, 0, 255))
    out = pp.quantize(img, 4)
    opaque = {p[:3] for p in out.getdata() if p[3] == 255}
    assert len(opaque) <= 4

def test_recolor_maps_onto_target_ramp():
    img = solid(2, 2, (100, 100, 100, 255))
    img.putpixel((0, 0), (200, 200, 200, 255))
    target = [(0, 0, 0, 255), (255, 255, 255, 255)]
    out = pp.recolor(img, target)
    colors = {p[:3] for p in out.getdata()}
    assert colors <= {(0, 0, 0), (255, 255, 255)}

def test_add_outline_adds_dark_border_pixels():
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    img.putpixel((1, 1), (255, 0, 0, 255))
    out = pp.add_outline(img, "#000000")
    assert out.getpixel((0, 1))[3] == 255  # neighbor became outline
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_postprocess.py -q`
Expected: FAIL (`No module named 'postprocess'`).

- [ ] **Step 3: Implement `postprocess.py`**

```python
#!/usr/bin/env python3
"""Turn a model's full-res image into a small game-ready RGBA sprite."""
from __future__ import annotations

import json
from pathlib import Path
from PIL import Image

_RESAMPLE = {
    "nearest": Image.NEAREST,
    "box": Image.BOX,
    "lanczos": Image.LANCZOS,
}
TRANSPARENT = (0, 0, 0, 0)


def _hex_to_rgba(value: str) -> tuple[int, int, int, int]:
    h = value.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return (r, g, b, a)


def _luma(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def downscale(img: "Image.Image", width: int, height: int, method: str = "nearest") -> "Image.Image":
    return img.convert("RGBA").resize((width, height), _RESAMPLE[method])


def remove_background(img: "Image.Image", method: str, color: str, tolerance: int) -> "Image.Image":
    img = img.convert("RGBA")
    if method == "none":
        return img
    if method == "alpha_threshold":
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                r, g, b, a = px[x, y]
                px[x, y] = (r, g, b, a) if a >= tolerance else TRANSPARENT
        return img
    # chroma
    kr, kg, kb, _ = _hex_to_rgba(color)
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if abs(r - kr) <= tolerance and abs(g - kg) <= tolerance and abs(b - kb) <= tolerance:
                px[x, y] = TRANSPARENT
    return img


def quantize(img: "Image.Image", colors: int) -> "Image.Image":
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    rgb = img.convert("RGB").quantize(colors=max(1, colors), method=Image.MEDIANCUT).convert("RGB")
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def load_target_palette(palettes_dir: Path, name: str) -> list[tuple[int, int, int, int]]:
    path = palettes_dir / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    colors = [v for v in data.get("colors", {}).values()
              if isinstance(v, str) and v.startswith("#")]
    ramp = [_hex_to_rgba(c) for c in colors]
    ramp.sort(key=lambda c: _luma(c))
    if not ramp:
        raise ValueError(f"target palette '{name}' has no flat hex colors to recolor onto")
    return ramp


def recolor(img: "Image.Image", target: list[tuple[int, int, int, int]]) -> "Image.Image":
    img = img.convert("RGBA")
    uniq = sorted({p[:3] for p in img.getdata() if p[3] == 255}, key=_luma)
    if not uniq:
        return img
    mapping: dict[tuple[int, int, int], tuple[int, int, int, int]] = {}
    n = len(uniq)
    for i, src in enumerate(uniq):
        ti = 0 if n == 1 else round(i / (n - 1) * (len(target) - 1))
        mapping[src] = target[ti]
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a == 255 and (r, g, b) in mapping:
                px[x, y] = mapping[(r, g, b)]
    return img


def add_outline(img: "Image.Image", color: str = "#000000") -> "Image.Image":
    img = img.convert("RGBA")
    out = img.copy()
    src = img.load()
    dst = out.load()
    oc = _hex_to_rgba(color)
    for y in range(img.height):
        for x in range(img.width):
            if src[x, y][3] != 0:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < img.width and 0 <= ny < img.height and src[nx, ny][3] == 255:
                    dst[x, y] = oc
                    break
    return out


def process(img: "Image.Image", pp, width: int, height: int, palettes_dir: Path,
            recolor_name: "str | None" = None) -> "Image.Image":
    out = downscale(img, width, height, pp.downscale)
    out = remove_background(out, pp.background.method, pp.background.color, pp.background.tolerance)
    if pp.quantize.enabled:
        out = quantize(out, pp.quantize.colors)
    if recolor_name:
        out = recolor(out, load_target_palette(palettes_dir, recolor_name))
    if pp.outline:
        out = add_outline(out)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/test_postprocess.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/postprocess.py scripts/test_postprocess.py
git commit -m "feat: image post-processing pipeline (downscale/key/quantize/recolor/outline)"
```

---

### Task 4: Sprite spec + prompt building (`imagegen.py`, part 1)

**Files:**
- Create: `scripts/imagegen.py`
- Test: `scripts/test_imagegen.py`

**Interfaces:**
- Consumes: `config.PromptConfig`.
- Produces:
  - `SpriteSpec` dataclass: `id:str`, `prompt:str`, `size:Optional[int]`, `width:Optional[int]`, `height:Optional[int]`, `negative:Optional[str]`, `gen:dict`, `outputs:dict[str,dict]`.
  - `SpecError(Exception)`.
  - `load_spec(path: Path) -> SpriteSpec` (id must equal filename stem; `outputs` defaults to `{id: {}}`).
  - `build_prompt(spec, output_opts: dict, prompt_cfg) -> tuple[str, str]` returning `(positive, negative)`.
  - `resolve_dims(spec, default_size) -> tuple[int,int]` (mirrors grid rule: `size` xor `width`/`height`, else default; powers of two).

- [ ] **Step 1: Write failing tests**

```python
# scripts/test_imagegen.py
from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import imagegen as ig  # noqa: E402
import config as cfg   # noqa: E402

PROMPT_CFG = cfg.PromptConfig(prefix="pixel art sprite of", suffix="crisp pixels", negative="blurry")

def write(path: Path, text: str):
    path.write_text(text, encoding="utf-8")

def test_load_spec_defaults_outputs_to_id(tmp_path):
    p = tmp_path / "hero.yaml"
    write(p, "id: hero\nprompt: a knight\n")
    spec = ig.load_spec(p)
    assert spec.id == "hero"
    assert spec.outputs == {"hero": {}}

def test_load_spec_id_must_match_filename(tmp_path):
    p = tmp_path / "hero.yaml"
    write(p, "id: villain\nprompt: x\n")
    with pytest.raises(ig.SpecError):
        ig.load_spec(p)

def test_build_prompt_assembles_template_and_subject(tmp_path):
    spec = ig.SpriteSpec(id="hero", prompt="a knight", size=None, width=None,
                         height=None, negative=None, gen={}, outputs={"hero": {}})
    pos, neg = ig.build_prompt(spec, {}, PROMPT_CFG)
    assert pos == "pixel art sprite of a knight, crisp pixels"
    assert neg == "blurry"

def test_build_prompt_merges_suffix_and_negative(tmp_path):
    spec = ig.SpriteSpec(id="hero", prompt="a knight", size=None, width=None,
                         height=None, negative="extra arms", gen={}, outputs={})
    pos, neg = ig.build_prompt(spec, {"prompt_suffix": "golden armor"}, PROMPT_CFG)
    assert "a knight, golden armor" in pos
    assert pos.endswith("crisp pixels")
    assert neg == "blurry, extra arms"

def test_resolve_dims_size_xor_wh(tmp_path):
    spec = ig.SpriteSpec(id="h", prompt="x", size=32, width=None, height=None,
                         negative=None, gen={}, outputs={})
    assert ig.resolve_dims(spec, 16) == (32, 32)
    bad = ig.SpriteSpec(id="h", prompt="x", size=32, width=16, height=16,
                        negative=None, gen={}, outputs={})
    with pytest.raises(ig.SpecError):
        ig.resolve_dims(bad, 16)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_imagegen.py -q`
Expected: FAIL (`No module named 'imagegen'`).

- [ ] **Step 3: Implement spec + prompt building in `imagegen.py`**

```python
#!/usr/bin/env python3
"""Sprite-spec loading, prompt building, and the OpenAI-compatible HTTP image client."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    import sys
    sys.stderr.write("Error: PyYAML is not installed. Install it with:\n  pip install PyYAML\n")
    raise SystemExit(2)


class SpecError(Exception):
    """Raised on an invalid sprite spec (validation failure -> exit code 1)."""


@dataclasses.dataclass
class SpriteSpec:
    id: str
    prompt: str
    size: Optional[int]
    width: Optional[int]
    height: Optional[int]
    negative: Optional[str]
    gen: dict
    outputs: dict


def _is_pow2(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n > 0 and (n & (n - 1)) == 0


def load_spec(path: Path) -> SpriteSpec:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SpecError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{path.name}: spec must be a YAML mapping")
    stem = path.stem
    if data.get("id") != stem:
        raise SpecError(f"{path.name}: id '{data.get('id')}' must match filename stem '{stem}'")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SpecError(f"{path.name}: 'prompt' must be a non-empty string")
    outputs = data.get("outputs")
    if outputs is None:
        outputs = {stem: {}}
    elif not isinstance(outputs, dict) or not outputs:
        raise SpecError(f"{path.name}: 'outputs' must be a non-empty mapping")
    return SpriteSpec(
        id=stem,
        prompt=prompt.strip(),
        size=data.get("size"),
        width=data.get("width"),
        height=data.get("height"),
        negative=data.get("negative"),
        gen=data.get("gen") or {},
        outputs=outputs,
    )


def resolve_dims(spec: SpriteSpec, default_size: int) -> tuple[int, int]:
    has_wh = spec.width is not None or spec.height is not None
    has_size = spec.size is not None
    if has_wh and has_size:
        raise SpecError(f"{spec.id}: specify either 'size' or 'width'/'height', not both")
    if has_wh:
        if spec.width is None or spec.height is None:
            raise SpecError(f"{spec.id}: both 'width' and 'height' are required when either is given")
        w, h = spec.width, spec.height
    elif has_size:
        w = h = spec.size
    else:
        w = h = default_size
    for label, val in (("width", w), ("height", h)):
        if not _is_pow2(val):
            raise SpecError(f"{spec.id}: {label} must be a positive power of two, got {val!r}")
    return w, h


def build_prompt(spec: SpriteSpec, output_opts: dict, prompt_cfg) -> tuple[str, str]:
    subject = spec.prompt
    suffix_extra = output_opts.get("prompt_suffix")
    if suffix_extra:
        subject = f"{subject}, {suffix_extra}"
    positive = f"{prompt_cfg.prefix} {subject}, {prompt_cfg.suffix}"
    negatives = [prompt_cfg.negative]
    if spec.negative:
        negatives.append(spec.negative)
    negative = ", ".join(n for n in negatives if n)
    return positive, negative
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/test_imagegen.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/imagegen.py scripts/test_imagegen.py
git commit -m "feat: sprite spec loading and prompt template assembly"
```

---

### Task 5: HTTP image client (`imagegen.py`, part 2)

**Files:**
- Modify: `scripts/imagegen.py`
- Test: `scripts/test_imagegen.py` (add cases)

**Interfaces:**
- Consumes: `config.ImageConfig`.
- Produces:
  - `BackendUnavailable(Exception)`.
  - `generate(positive: str, negative: str, image_cfg, seed: Optional[int]) -> Image.Image` -- POSTs an OpenAI-compatible body `{model, prompt, negative_prompt, size, n=1, response_format="b64_json", ...params}` to `image_cfg.endpoint`; decodes `data[0].b64_json` (base64 PNG) -> PIL Image. Raises `BackendUnavailable` on connection error, timeout, non-2xx, or malformed/empty response.
  - `_request_body(positive, negative, image_cfg, seed) -> dict` (split out for testing the request shape).

- [ ] **Step 1: Write failing tests (mock urllib)**

```python
# add to scripts/test_imagegen.py
import base64, io, json
import urllib.error
from PIL import Image

IMG_CFG = cfg.ImageConfig(endpoint="http://localhost:9/v1/images/generations",
                          model="sd-pixel", api_key_env=None, timeout=5,
                          gen_size=64, params={"steps": 20, "seed": None})

def _fake_b64_png():
    buf = io.BytesIO()
    Image.new("RGBA", (4, 4), (1, 2, 3, 255)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

def test_request_body_shape():
    body = ig._request_body("a knight", "blurry", IMG_CFG, seed=7)
    assert body["model"] == "sd-pixel"
    assert body["prompt"] == "a knight"
    assert body["negative_prompt"] == "blurry"
    assert body["size"] == "64x64"
    assert body["seed"] == 7
    assert body["steps"] == 20

def test_generate_decodes_image(monkeypatch):
    payload = json.dumps({"data": [{"b64_json": _fake_b64_png()}]}).encode()
    class FakeResp:
        status = 200
        def read(self): return payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(ig.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    img = ig.generate("a knight", "blurry", IMG_CFG, seed=None)
    assert img.size == (4, 4)

def test_generate_raises_backend_unavailable_on_urlerror(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(ig.urllib.request, "urlopen", boom)
    with pytest.raises(ig.BackendUnavailable):
        ig.generate("x", "", IMG_CFG, seed=None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_imagegen.py -k "request_body or generate" -q`
Expected: FAIL (`module 'imagegen' has no attribute 'urllib'` / `generate`).

- [ ] **Step 3: Add the HTTP client to `imagegen.py`**

Append these imports near the top (after the yaml import block): `import base64`, `import io`, `import json`, `import os`, `import urllib.error`, `import urllib.request`, and `from PIL import Image`. Then add:

```python
class BackendUnavailable(Exception):
    """The image backend could not be reached or returned an unusable response (exit code 3)."""


def _request_body(positive: str, negative: str, image_cfg, seed) -> dict:
    body = {
        "model": image_cfg.model,
        "prompt": positive,
        "negative_prompt": negative,
        "size": f"{image_cfg.gen_size}x{image_cfg.gen_size}",
        "n": 1,
        "response_format": "b64_json",
    }
    for k, v in (image_cfg.params or {}).items():
        if v is not None:
            body[k] = v
    if seed is not None:
        body["seed"] = seed
    return body


def generate(positive: str, negative: str, image_cfg, seed) -> "Image.Image":
    body = _request_body(positive, negative, image_cfg, seed)
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if image_cfg.api_key_env:
        key = os.environ.get(image_cfg.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(image_cfg.endpoint, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=image_cfg.timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise BackendUnavailable(f"image backend unreachable at {image_cfg.endpoint}: {exc}") from exc
    try:
        parsed = json.loads(raw)
        b64 = parsed["data"][0]["b64_json"]
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        return img.convert("RGBA")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise BackendUnavailable(
            f"image backend at {image_cfg.endpoint} returned an unusable response: {exc}"
        ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/test_imagegen.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/imagegen.py scripts/test_imagegen.py
git commit -m "feat: OpenAI-compatible HTTP image client with backend-unavailable handling"
```

---

### Task 6: Orchestrator CLI (`render_sprites.py`)

**Files:**
- Modify (rewrite): `scripts/render_sprites.py`
- Test: `scripts/test_orchestrator.py`

**Interfaces:**
- Consumes: `config.load_config`, `config.ConfigError`; `imagegen.load_spec/build_prompt/resolve_dims/generate/SpecError/BackendUnavailable`; `postprocess.process`; `render_grid.render_file/validate_all/write_pack/RenderError`.
- Produces: `generate_sprite(spec, cfg, collect=None) -> list[str]` (image path for one spec; writes PNGs into `cfg.out_dir`; honors recolor vs regenerate per output); `main(argv=None) -> int`.
- CLI flags: `--config`, `--only ID`, `--check`, `--mode {auto,image,grid}`, `--fallback-grid`, `--size`, `--sprites-dir`, `--shapes-dir`, `--palettes-dir`, `--out-dir`, `--pack`, `--pack-name`, `--pack-cols`.

- [ ] **Step 1: Write failing tests (image path + fallback, generation mocked)**

```python
# scripts/test_orchestrator.py
from __future__ import annotations
import sys, io, base64, json
from pathlib import Path
import pytest
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_sprites as rs   # noqa: E402
import imagegen as ig         # noqa: E402

CONFIG_YAML = """
size: 16
sprites_dir: art/sprites
shapes_dir: art/shapes
palettes_dir: art/palettes
out_dir: out
image: {endpoint: "http://x/v1/images/generations", model: m}
postprocess: {background: {method: none}, quantize: {enabled: false}}
"""

@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "art/sprites").mkdir(parents=True)
    (tmp_path / "art/shapes").mkdir(parents=True)
    (tmp_path / "art/palettes").mkdir(parents=True)
    (tmp_path / "pixel-sprite.config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    (tmp_path / "art/sprites/hero.yaml").write_text("id: hero\nprompt: a knight\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path

def _fake_image(*a, **k):
    return Image.new("RGBA", (32, 32), (10, 20, 30, 255))

def test_image_path_writes_png(project, monkeypatch):
    monkeypatch.setattr(ig, "generate", _fake_image)
    rc = rs.main(["--only", "hero"])
    assert rc == 0
    assert (project / "out/hero.png").is_file()
    assert Image.open(project / "out/hero.png").size == (16, 16)

def test_backend_failure_exits_3(project, monkeypatch):
    def boom(*a, **k):
        raise ig.BackendUnavailable("nope")
    monkeypatch.setattr(ig, "generate", boom)
    rc = rs.main(["--only", "hero"])
    assert rc == 3

def test_fallback_grid_used_when_flagged(project, monkeypatch):
    def boom(*a, **k):
        raise ig.BackendUnavailable("nope")
    monkeypatch.setattr(ig, "generate", boom)
    # provide a grid fallback source + palette
    (project / "art/palettes/iron.json").write_text(
        json.dumps({"colors": {"B": "#C8C8C8"}}), encoding="utf-8")
    rows = ["." * 16 for _ in range(16)]
    rows[0] = "B" + "." * 15
    (project / "art/shapes/hero.json").write_text(
        json.dumps({"id": "hero", "size": 16, "outputs": {"hero": "iron"}, "rows": rows}),
        encoding="utf-8")
    rc = rs.main(["--only", "hero", "--fallback-grid"])
    assert rc == 0
    assert (project / "out/hero.png").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_orchestrator.py -q`
Expected: FAIL (current `render_sprites.py` is the shim from Task 1; `main`/`generate_sprite` behavior absent).

- [ ] **Step 3: Rewrite `render_sprites.py` as the orchestrator**

```python
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
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest scripts/ -q`
Expected: PASS (orchestrator + config + postprocess + imagegen + grid all green).

- [ ] **Step 5: Commit**

```bash
git add scripts/render_sprites.py scripts/test_orchestrator.py
git commit -m "feat: image-first orchestrator with grid fallback and exit-code-3 contract"
```

---

### Task 7: Templates + interactive `/init` + showcase migration

**Files:**
- Create: `templates/pixel-sprite.config.yaml`, `templates/sprites/hero.yaml`
- Delete: `templates/pixel-sprite.config.json`
- Modify (rewrite): `commands/init.md`
- Create: `examples/showcase/pixel-sprite.config.yaml`, `examples/showcase/art/sprites/hero.yaml`
- Delete: `examples/showcase/pixel-sprite.config.json`

- [ ] **Step 1: Write the YAML config template**

Create `templates/pixel-sprite.config.yaml` with the full annotated schema from the spec (Configuration section): `size`, `mode`, the four dirs, and the `image`, `prompt`, `postprocess`, `pack` blocks with the default values shown in `config.DEFAULTS`. Add a one-line `#` comment over each block. No emojis.

- [ ] **Step 2: Write the example sprite spec template**

Create `templates/sprites/hero.yaml`:

```yaml
id: hero
prompt: a brave knight in a green tunic, front-facing idle pose, simple readable silhouette
size: 32
outputs:
  hero: {}
  hero_gold: { recolor: example }   # silhouette-identical recolor of the base (no extra model call)
```

- [ ] **Step 3: Delete the JSON config template**

Run: `git rm templates/pixel-sprite.config.json`

- [ ] **Step 4: Rewrite `commands/init.md` as an interactive interview**

Replace the body so the command:
1. If `pixel-sprite.config.yaml` is absent, **interview** the user for machine-specific values (one question at a time): image-gen available? endpoint URL (default `http://localhost:8080/v1/images/generations`), model name, `gen_size`, `size`, and output dirs. Use the template defaults for anything not answered. Write the answers into `${CLAUDE_PROJECT_DIR}/pixel-sprite.config.yaml`. If it exists, report values and do not overwrite.
2. Create `sprites_dir`, `shapes_dir`, `palettes_dir`, `out_dir`.
3. Copy `templates/sprites/hero.yaml` -> `<sprites_dir>/hero.yaml`, `templates/palettes/example.json` -> `<palettes_dir>/example.json`, and `templates/shapes/gem.json` -> `<shapes_dir>/gem.json` (skip existing).
4. Verify with `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --check`; report missing Pillow/PyYAML (`pip install Pillow PyYAML`).
5. Print next steps: author a spec at `<sprites_dir>/<id>.yaml`, then `render_sprites.py --only <id>`; note `--mode grid` for the deterministic path.

- [ ] **Step 5: Migrate the showcase example to YAML**

Create `examples/showcase/pixel-sprite.config.yaml` mirroring the old JSON values (`size: 16`, the showcase dirs) plus a `mode: grid` line (the showcase ships hand-authored grids and no live backend, so it renders deterministically and stays reproducible in CI). Run `git rm examples/showcase/pixel-sprite.config.json`. Add `examples/showcase/art/sprites/hero.yaml` as a documentation sample spec (it is not rendered by the grid-mode showcase but shows the new format).

- [ ] **Step 6: Verify the showcase still renders**

Run: `cd examples/showcase && python ../../scripts/render_sprites.py --check && python ../../scripts/render_sprites.py --mode grid`
Expected: validates, then re-renders the existing PNGs with no diff.

- [ ] **Step 7: Commit**

```bash
git add templates/ commands/init.md examples/showcase/
git rm templates/pixel-sprite.config.json examples/showcase/pixel-sprite.config.json
git commit -m "feat: YAML templates, interactive init interview, showcase migration"
```

---

### Task 8: Rewrite `SKILL.md` for the image-first flow

**Files:**
- Modify (rewrite): `skills/pixel-sprite-generator/SKILL.md`

- [ ] **Step 1: Update the frontmatter description**

Rewrite the `description:` so triggers reflect image-gen-first generation from a prompt spec, with a deterministic grid fallback, driven by `pixel-sprite.config.yaml`. Keep the existing trigger phrases ("generate the player-idle sprite", "make a 32x32 icon for X", etc.).

- [ ] **Step 2: Rewrite the Overview + workflow**

Document the new default workflow:
1. Resolve a `sprite_id`. Author/locate `art/sprites/<id>.yaml` (a subject `prompt` plus optional `size`, `negative`, `gen`, `outputs`).
2. Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py" --only <id>`.
3. **Vision review loop**: upscale and LOOK at the PNG (reuse the existing PIL upscale snippet). If off-model, revise the spec's `prompt`/`negative`/`seed` and re-run. Delete `.tmp_*` previews.
4. **On exit code 3** (backend unavailable): STOP and ask the user whether to render the grid fallback (`--mode grid` / `--fallback-grid`). Grid fallback needs a matching `art/shapes/<id>.json`; if absent, offer to author one.
5. Report the written PNG paths.

- [ ] **Step 3: Add prompt-authoring guidance**

Add a "Writing subject prompts" section: describe silhouette, view angle (front/3-4/side), palette/material words, lighting (implied upper-left source), and what reads at small sizes; note that `prompt.prefix/suffix/negative` in config carry the house style so specs hold only the subject. Add a "Variants" subsection covering `recolor:` (cheap, silhouette-identical) vs `regenerate: true` (fresh call, differing form).

- [ ] **Step 4: Preserve the grid-path reference**

Keep a condensed "Grid fallback" section retaining the shape-grid schema, semantic char convention, palette format, and gradients (the deterministic path is unchanged) -- but frame it as the fallback, not the default. Keep the `--pack` spritesheet/atlas section.

- [ ] **Step 5: Verify references**

Confirm every command in the skill resolves: `--only`, `--check`, `--mode grid`, `--fallback-grid`, `--pack`. No emojis.

- [ ] **Step 6: Commit**

```bash
git add skills/pixel-sprite-generator/SKILL.md
git commit -m "docs: rewrite SKILL for image-first generation with grid fallback"
```

---

### Task 9: Update `README.md`, `plugin.json`, and `.gitignore`

**Files:**
- Modify: `README.md`, `.claude-plugin/plugin.json`, `.gitignore`

- [ ] **Step 1: README**

Rewrite the intro + add sections for: the image-first flow, `pixel-sprite.config.yaml`, the prompt template, post-processing steps, the fallback contract (exit code 3 / `--fallback-grid`), and the PyYAML + Pillow requirements (`pip install Pillow PyYAML`). Keep the Showcase (now rendered via `--mode grid`) and the `--pack` section. Author line stays `MisterVitoPro`.

- [ ] **Step 2: plugin.json**

Update `description` to mention local image-model-first generation with deterministic grid fallback and YAML config; bump `version` to `0.2.0`; add keyword `image-generation`. Keep `author.name` `MisterVitoPro`.

- [ ] **Step 3: .gitignore**

Ensure `.tmp_*`, `__pycache__/`, and `.pytest_cache/` are ignored (add any missing).

- [ ] **Step 4: Final full-suite run**

Run: `python -m pytest scripts/ -q`
Expected: PASS (all tasks' tests green).

- [ ] **Step 5: Commit**

```bash
git add README.md .claude-plugin/plugin.json .gitignore
git commit -m "docs: README + plugin metadata for image-gen pipeline; bump to 0.2.0"
```

---

## Self-Review

**Spec coverage:**
- Two-path architecture + module split -> Tasks 1-6.
- YAML config, hard cut, strict validation -> Task 2 (+ template Task 7).
- Sprite spec + prompt template -> Task 4.
- HTTP backend + exit-code-3 -> Tasks 5, 6.
- Post-processing (downscale/key/quantize/recolor/outline) -> Task 3.
- Variants (recolor vs regenerate) -> Task 6 `generate_sprite`, template Task 7, skill Task 8.
- Fallback contract + `--fallback-grid` -> Task 6.
- Interactive `/init` -> Task 7.
- Vision loop + prompt guidance -> Task 8.
- Migration (showcase, README, plugin) -> Tasks 7, 9.
- PyYAML-only dependency, stdlib HTTP -> Tasks 2, 5 (constraints in header).

**Placeholder scan:** No TBD/TODO; code steps carry full code; doc/skill/template steps specify exact content to write.

**Type consistency:** `Config`/`ImageConfig`/`PromptConfig`/`PostprocessConfig` field names match across `config.py`, `imagegen.build_prompt`, `postprocess.process`, and the orchestrator. `generate(positive, negative, image_cfg, seed)`, `build_prompt(spec, output_opts, prompt_cfg) -> (pos, neg)`, `process(img, pp, width, height, palettes_dir, recolor_name=None)`, and `render_file(...)` signatures are consistent between definition and call sites. `BackendUnavailable` -> exit 3, `SpecError`/`RenderError` -> exit 1, `ConfigError` -> exit 2 are consistent across Tasks 5/6.

**Note for implementer:** Task 1 removes config/CLI tests from the old `test_render_sprites.py`; their replacements live in Tasks 2 (config) and 6 (orchestrator). Do not re-add them against `render_grid`.
