# Pluggable Image Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hard-wired OpenAI image client with a declarative, config-driven HTTP engine so the pipeline can generate against any backend (SwarmUI, Automatic1111, OpenAI-compatible servers) with no per-backend Python.

**Architecture:** `imagegen.generate()` keeps its signature and exit-code-3 contract but is rewritten as a small engine: render a request body from a `${...}`-placeholder template, send an optional prep request then the main request via stdlib `urllib`, locate the image by json-path, and resolve it as inline base64 or a URL to fetch. The OpenAI `image:` config block is replaced by a generic `backend:` block; three presets ship under `templates/backends/`.

**Tech Stack:** Python 3, Pillow, PyYAML, stdlib `urllib`/`json`/`base64`/`re`, pytest.

## Global Constraints

- No emojis in code or docs.
- Author/handle in any project file is `MisterVitoPro`; never a real name or email.
- New runtime dependencies: none beyond existing PyYAML + Pillow; HTTP must use stdlib `urllib` (no `requests`, no jsonpath library).
- Strict config validation: unknown top-level keys and unknown keys within validated sections are rejected.
- Colors are `#RRGGBB` / `#RRGGBBAA`. Sprite dimensions are positive powers of two.
- Exit codes: `0` success, `1` validation failure, `2` environment/config error, `3` image backend unavailable.
- The bundled invocation path stays `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py"`.
- Generation flow depth: optional prep request + one main request + optional image fetch. No submit-then-poll loop.

---

## File Structure

- `scripts/config.py` (modify) -- remove `ImageConfig`; add `BackendConfig`, `PrepSpec`, `RequestSpec`, `ResponseSpec`, `AuthSpec`; rename `Config.image` -> `Config.backend`; update `DEFAULTS`, validation, strict-key checks.
- `scripts/imagegen.py` (modify) -- remove `_request_body`; add engine helpers `_render`, `_http`, `_extract`, `_auth_header`, `_resolve_image`, `_decode_b64`, `_is_base64`, sentinel `_DROP`, regex `_PLACEHOLDER`; rewrite `generate`.
- `scripts/render_sprites.py` (modify) -- `cfg.image` -> `cfg.backend` (call site of `generate`); `generate(pos, neg, cfg.backend, seed)`.
- `scripts/test_config.py` (modify) -- replace `image:`-based assertions with `backend:` schema tests.
- `scripts/test_imagegen.py` (modify) -- replace the HTTP-client tests with engine + flow tests; keep spec/prompt tests.
- `scripts/test_orchestrator.py` (modify) -- update the fixture `CONFIG_YAML` to the `backend:` schema.
- `scripts/test_presets.py` (create) -- assert each shipped preset file loads through `config.load_config`.
- `templates/backends/openai.yaml`, `templates/backends/a1111.yaml`, `templates/backends/swarmui.yaml` (create).
- `templates/pixel-sprite.config.yaml` (modify) -- generic `backend:` block.
- `commands/init.md` (modify) -- backend-selection interview.
- `README.md`, `skills/pixel-sprite-generator/SKILL.md`, `.claude-plugin/plugin.json` (modify) -- document backends; bump version to `0.3.0`.

Interdependency note: Task 1 (config) changes the schema, which transiently breaks `test_imagegen.py` and `test_orchestrator.py` until Tasks 2 and 3 update them. Full-suite green is restored at the end of Task 3. Each task's own tests pass at its commit.

---

### Task 1: Generic `BackendConfig` schema (`config.py`)

Replace the OpenAI-specific `ImageConfig` with a generic backend dataclass tree and rename `Config.image` -> `Config.backend`.

**Files:**
- Modify: `scripts/config.py`
- Modify: `scripts/test_config.py`

**Interfaces:**
- Produces: dataclasses `PrepSpec`, `RequestSpec`, `ResponseSpec`, `AuthSpec`, `BackendConfig`; `Config.backend: BackendConfig` (replaces `Config.image`); constants `IMAGE_KINDS = ("base64", "url", "auto")`.
- `RequestSpec`: `method:str`, `url:str`, `headers:dict`, `body` (any).
- `PrepSpec`: `method:str`, `url:str`, `headers:dict`, `body` (any), `capture:dict`.
- `ResponseSpec`: `image_path:str`, `image_kind:str`, `fetch_base:Optional[str]`.
- `AuthSpec`: `header:str`, `value:str`.
- `BackendConfig`: `model:Optional[str]`, `gen_size:int`, `timeout:int`, `prep:Optional[PrepSpec]`, `request:RequestSpec`, `response:ResponseSpec`, `auth:Optional[AuthSpec]`.
- Consumes: nothing new.

- [ ] **Step 1: Rewrite the config tests**

Replace the `image`-specific assertions in `scripts/test_config.py`. Keep `write_cfg`, the power-of-two, mode, override, and missing-config tests unchanged. Replace `MINIMAL` and `test_loads_minimal_and_fills_defaults`, and add backend tests:

```python
MINIMAL = """
size: 16
backend:
  request:
    url: http://localhost:9000/v1/images/generations
    body: {prompt: "${prompt}"}
  response:
    image_path: data.0.b64_json
"""

def test_loads_minimal_and_fills_defaults(tmp_path):
    write_cfg(tmp_path, MINIMAL)
    c = cfg.load_config(tmp_path, None, {k: None for k in
        ("size","mode","sprites_dir","shapes_dir","palettes_dir","out_dir")})
    assert c.size == 16
    assert c.mode == "auto"
    assert c.backend.request.url.endswith("/v1/images/generations")
    assert c.backend.request.method == "POST"          # defaulted
    assert c.backend.gen_size == 512                    # defaulted
    assert c.backend.timeout == 120                     # defaulted
    assert c.backend.response.image_path == "data.0.b64_json"
    assert c.backend.response.image_kind == "auto"      # defaulted
    assert c.backend.prep is None
    assert c.backend.auth is None
    assert c.prompt.prefix                               # unchanged house style
    assert c.postprocess.background.method == "chroma"

def test_backend_requires_request_and_response(tmp_path):
    write_cfg(tmp_path, "size: 16\nbackend: {response: {image_path: x}}\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})
    write_cfg(tmp_path, "size: 16\nbackend: {request: {url: x, body: {}}}\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})

def test_invalid_image_kind_rejected(tmp_path):
    write_cfg(tmp_path, MINIMAL + "\n    image_kind: sideways\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})

def test_unknown_backend_key_rejected(tmp_path):
    write_cfg(tmp_path, MINIMAL + "\n  bogus: 1\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})

def test_prep_and_auth_parse(tmp_path):
    text = """
size: 16
backend:
  gen_size: 1024
  prep:
    url: http://h/API/GetNewSession
    capture: {session_id: session_id}
  request:
    url: http://h/API/GenerateText2Image
    body: {session_id: "${session_id}", prompt: "${prompt}"}
  response:
    image_path: images.0
    image_kind: url
    fetch_base: http://h/
  auth: {header: Authorization, value: "Bearer ${env:K}"}
"""
    write_cfg(tmp_path, text)
    c = cfg.load_config(tmp_path, None, {})
    assert c.backend.gen_size == 1024
    assert c.backend.prep.capture == {"session_id": "session_id"}
    assert c.backend.prep.method == "POST"
    assert c.backend.response.image_kind == "url"
    assert c.backend.response.fetch_base == "http://h/"
    assert c.backend.auth.header == "Authorization"
```

Delete the old `test_loads_minimal_and_fills_defaults` body that referenced `c.image.endpoint`/`c.image.timeout`. Update the `MINIMAL`-derived `test_unknown_top_level_key_rejected`, `test_invalid_mode_rejected`, and `test_cli_override_wins` to use the new `MINIMAL` (they append lines to it; ensure the appended top-level keys like `mode:`/`bogus:` are at column 0).

- [ ] **Step 2: Run config tests to verify they fail**

Run: `python -m pytest scripts/test_config.py -q`
Expected: FAIL (`AttributeError`/`ConfigError` mismatches; `BackendConfig` not present).

- [ ] **Step 3: Rewrite `config.py`**

Replace the `ImageConfig` dataclass and the `image` portions of `DEFAULTS`, `STRICT_SECTIONS`, validation, and `Config` construction. Full replacements:

Replace the constants block near the top:

```python
CONFIG_FILENAME = "pixel-sprite.config.yaml"
MODES = ("auto", "image", "grid")
DOWNSCALE = ("nearest", "box", "lanczos")
BG_METHODS = ("chroma", "alpha_threshold", "none")
IMAGE_KINDS = ("base64", "url", "auto")
```

Replace the `image` key inside `DEFAULTS` with `backend` (leave `size`, `mode`, the four dirs, `prompt`, `postprocess`, `pack` exactly as they are):

```python
    "backend": {
        "model": None,
        "gen_size": 512,
        "timeout": 120,
        "prep": None,
        "request": {
            "method": "POST",
            "url": "http://localhost:8080/v1/images/generations",
            "headers": {},
            "body": {},
        },
        "response": {
            "image_path": "data.0.b64_json",
            "image_kind": "auto",
            "fetch_base": None,
        },
        "auth": None,
    },
```

Update strict-section handling. The `backend` block has nested sections whose keys must be validated, but `request.body`, `request.headers`, `prep.body`, `prep.headers`, and `prep.capture` are free-form. Replace `STRICT_SECTIONS` and `_check_keys` with:

```python
# top-level sections whose immediate keys are validated strictly
STRICT_SECTIONS = {"prompt", "postprocess", "pack"}
# backend sub-sections whose immediate keys are validated strictly
BACKEND_ALLOWED = {"model", "gen_size", "timeout", "prep", "request", "response", "auth"}
PREP_ALLOWED = {"method", "url", "headers", "body", "capture"}
REQUEST_ALLOWED = {"method", "url", "headers", "body"}
RESPONSE_ALLOWED = {"image_path", "image_kind", "fetch_base"}
AUTH_ALLOWED = {"header", "value"}


def _reject_unknown(section_name, sub, allowed):
    if isinstance(sub, dict):
        bad = set(sub) - allowed
        if bad:
            raise ConfigError(f"unknown key(s) in '{section_name}': {sorted(bad)}")


def _check_keys(data: dict) -> None:
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"unknown config key(s): {sorted(unknown)}")
    for section in STRICT_SECTIONS:
        sub = data.get(section)
        if isinstance(sub, dict):
            bad = set(sub) - set(DEFAULTS[section])
            if bad:
                raise ConfigError(f"unknown key(s) in '{section}': {sorted(bad)}")
    backend = data.get("backend")
    if isinstance(backend, dict):
        _reject_unknown("backend", backend, BACKEND_ALLOWED)
        _reject_unknown("backend.prep", backend.get("prep"), PREP_ALLOWED)
        _reject_unknown("backend.request", backend.get("request"), REQUEST_ALLOWED)
        _reject_unknown("backend.response", backend.get("response"), RESPONSE_ALLOWED)
        _reject_unknown("backend.auth", backend.get("auth"), AUTH_ALLOWED)
```

Note `postprocess` nested keys (`background`, `quantize`) were previously validated only at the top level of the section; keep that behavior (no change to postprocess validation).

Replace the `ImageConfig` dataclass with:

```python
@dataclasses.dataclass
class RequestSpec:
    method: str
    url: str
    headers: dict
    body: object


@dataclasses.dataclass
class PrepSpec:
    method: str
    url: str
    headers: dict
    body: object
    capture: dict


@dataclasses.dataclass
class ResponseSpec:
    image_path: str
    image_kind: str
    fetch_base: Optional[str]


@dataclasses.dataclass
class AuthSpec:
    header: str
    value: str


@dataclasses.dataclass
class BackendConfig:
    model: Optional[str]
    gen_size: int
    timeout: int
    prep: Optional[PrepSpec]
    request: RequestSpec
    response: ResponseSpec
    auth: Optional[AuthSpec]
```

In the `Config` dataclass, change the field `image: ImageConfig` to `backend: BackendConfig`.

Add a builder helper above `load_config`:

```python
def _build_backend(merged_backend: dict) -> "BackendConfig":
    b = merged_backend
    req = b.get("request") or {}
    if "url" not in req or not req.get("url"):
        raise ConfigError("backend.request.url is required")
    resp = b.get("response") or {}
    if "image_path" not in resp or not resp.get("image_path"):
        raise ConfigError("backend.response.image_path is required")
    kind = resp.get("image_kind", "auto")
    if kind not in IMAGE_KINDS:
        raise ConfigError(f"backend.response.image_kind must be one of {IMAGE_KINDS}, got {kind!r}")
    prep = None
    if b.get("prep") is not None:
        p = b["prep"]
        if not p.get("url"):
            raise ConfigError("backend.prep.url is required when prep is given")
        prep = PrepSpec(
            method=p.get("method", "POST"), url=p["url"],
            headers=p.get("headers") or {}, body=p.get("body") if p.get("body") is not None else {},
            capture=p.get("capture") or {},
        )
    auth = None
    if b.get("auth") is not None:
        a = b["auth"]
        if not a.get("header") or not a.get("value"):
            raise ConfigError("backend.auth requires both 'header' and 'value'")
        auth = AuthSpec(header=a["header"], value=a["value"])
    return BackendConfig(
        model=b.get("model"),
        gen_size=b.get("gen_size", 512),
        timeout=b.get("timeout", 120),
        prep=prep,
        request=RequestSpec(
            method=req.get("method", "POST"), url=req["url"],
            headers=req.get("headers") or {},
            body=req.get("body") if req.get("body") is not None else {},
        ),
        response=ResponseSpec(
            image_path=resp["image_path"], image_kind=kind, fetch_base=resp.get("fetch_base"),
        ),
        auth=auth,
    )
```

In `load_config`, replace the `image=ImageConfig(**merged["image"])` line in the `Config(...)` construction with `backend=_build_backend(merged["backend"])`. Because `_deep_merge` deep-merges `DEFAULTS["backend"]` with the file's `backend`, a file that omits `prep`/`auth` keeps the default `None`, and a file that sets `request`/`response` partially still gets `method`/`image_kind` defaults via the merge.

Important merge detail: `_deep_merge` only recurses when BOTH sides are dicts. Since `DEFAULTS["backend"]["prep"]` is `None`, a file `prep:` mapping replaces it wholesale (good). Since `DEFAULTS["backend"]["request"]` is a dict, a file `request:` deep-merges (so `method` defaults apply). This is the intended behavior.

- [ ] **Step 4: Run config tests to verify they pass**

Run: `python -m pytest scripts/test_config.py -q`
Expected: PASS (all config tests green).

- [ ] **Step 5: Commit**

```bash
git add scripts/config.py scripts/test_config.py
git commit -m "feat: generic BackendConfig schema replacing OpenAI-specific image config"
```

---

### Task 2: Declarative HTTP engine (`imagegen.py`)

Replace `_request_body` and the OpenAI response parse with the template/extract/fetch engine. `generate` keeps its signature `generate(positive, negative, backend_cfg, seed)`.

**Files:**
- Modify: `scripts/imagegen.py`
- Modify: `scripts/test_imagegen.py`

**Interfaces:**
- Consumes: `config.BackendConfig` (and its nested specs) from Task 1.
- Produces: `generate(positive: str, negative: str, backend_cfg, seed) -> PIL.Image` (RGBA), raising `BackendUnavailable`. Internal: `_render(template, vars)`, `_http(method, url, headers, body, timeout) -> (status, bytes)`, `_extract(obj, path)`, `_auth_header(auth, vars) -> dict`, `_resolve_image(ref, backend_cfg, auth, vars) -> bytes`, `_decode_b64(ref) -> bytes`, `_is_base64(s) -> bool`, sentinel `_DROP`, `_PLACEHOLDER` regex. `SpriteSpec`, `load_spec`, `resolve_dims`, `build_prompt`, `SpecError`, `BackendUnavailable` are unchanged.

- [ ] **Step 1: Replace the HTTP-client tests**

In `scripts/test_imagegen.py`, KEEP the 5 spec/prompt tests (`test_load_spec_*`, `test_build_prompt_*`, `test_resolve_dims_*`) and the `PROMPT_CFG` fixture unchanged. REMOVE the old HTTP tests (`IMG_CFG`, `_fake_b64_png`, `test_request_body_shape`, `test_generate_decodes_image`, `test_generate_raises_backend_unavailable_on_urlerror`) and the imports tied to them. Add at the end:

```python
import base64, io, json, re
import urllib.error
import pytest
from PIL import Image
import config as cfg   # noqa: E402


def _b64_png(rgba=(1, 2, 3, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", (4, 4), rgba).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _png_bytes(rgba=(9, 8, 7, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", (4, 4), rgba).save(buf, format="PNG")
    return buf.getvalue()


def _backend(**over):
    base = dict(model="m", gen_size=64, timeout=5, prep=None,
                request=cfg.RequestSpec(method="POST", url="http://x/gen", headers={},
                                        body={"prompt": "${prompt}", "size": "${gen_width}x${gen_height}",
                                              "seed": "${seed}"}),
                response=cfg.ResponseSpec(image_path="data.0.b64_json", image_kind="auto", fetch_base=None),
                auth=None)
    base.update(over)
    return cfg.BackendConfig(**base)


# --- _render ---
def test_render_whole_value_preserves_type_and_drops_none():
    out = ig._render({"w": "${gen_width}", "seed": "${seed}", "p": "${prompt}"},
                     {"gen_width": 64, "seed": None, "prompt": "knight"})
    assert out == {"w": 64, "p": "knight"}   # seed None -> key dropped; w stays int

def test_render_embedded_stringifies():
    out = ig._render("size ${gen_width}x${gen_height}", {"gen_width": 64, "gen_height": 64})
    assert out == "size 64x64"

def test_render_env_token(monkeypatch):
    monkeypatch.setenv("MYKEY", "sekret")
    assert ig._render("Bearer ${env:MYKEY}", {}) == "Bearer sekret"

# --- _extract ---
def test_extract_dotted_and_indexed():
    obj = {"data": [{"b64_json": "abc"}], "images": ["p.png"]}
    assert ig._extract(obj, "data.0.b64_json") == "abc"
    assert ig._extract(obj, "images.0") == "p.png"

def test_extract_missing_path_raises():
    with pytest.raises(ig.BackendUnavailable):
        ig._extract({"data": []}, "data.0.b64_json")

# --- generate: inline base64 (OpenAI/A1111 shape) ---
def test_generate_inline_base64(monkeypatch):
    payload = json.dumps({"data": [{"b64_json": _b64_png()}]}).encode()
    class R:
        status = 200
        def read(self, *a): return payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(ig.urllib.request, "urlopen", lambda req, timeout=None: R())
    img = ig.generate("a knight", "blurry", _backend(), seed=7)
    assert img.size == (4, 4) and img.mode == "RGBA"

# --- generate: 2-step prep -> generate -> fetch-by-url (SwarmUI shape) ---
def test_generate_two_step_fetch_url(monkeypatch):
    calls = []
    prep_json = json.dumps({"session_id": "S1"}).encode()
    gen_json = json.dumps({"images": ["View/local/out.png"]}).encode()
    png = _png_bytes()
    class R:
        def __init__(self, data): self._d = data; self.status = 200
        def read(self, *a): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append((req.get_method(), url))
        if url.endswith("/session"):
            return R(prep_json)
        if url.endswith("/gen"):
            # session must have been captured into the body
            assert b"S1" in (req.data or b"")
            return R(gen_json)
        if url == "http://x/View/local/out.png":
            return R(png)
        raise AssertionError("unexpected url " + url)
    monkeypatch.setattr(ig.urllib.request, "urlopen", fake_urlopen)
    b = _backend(
        prep=cfg.PrepSpec(method="POST", url="http://x/session", headers={}, body={},
                          capture={"session_id": "session_id"}),
        request=cfg.RequestSpec(method="POST", url="http://x/gen", headers={},
                                body={"session_id": "${session_id}", "prompt": "${prompt}"}),
        response=cfg.ResponseSpec(image_path="images.0", image_kind="url", fetch_base="http://x/"),
    )
    img = ig.generate("a knight", "", b, seed=None)
    assert img.size == (4, 4)
    assert ("GET", "http://x/View/local/out.png") in calls

# --- auth: present, and dropped when env unset ---
def test_auth_header_present(monkeypatch):
    monkeypatch.setenv("K", "tok")
    hdr = ig._auth_header(cfg.AuthSpec(header="Authorization", value="Bearer ${env:K}"), {})
    assert hdr == {"Authorization": "Bearer tok"}

def test_auth_header_dropped_when_env_unset(monkeypatch):
    monkeypatch.delenv("K", raising=False)
    hdr = ig._auth_header(cfg.AuthSpec(header="Authorization", value="Bearer ${env:K}"), {})
    assert hdr == {}

# --- failure modes -> BackendUnavailable (exit 3) ---
def test_generate_unreachable(monkeypatch):
    def boom(req, timeout=None): raise urllib.error.URLError("refused")
    monkeypatch.setattr(ig.urllib.request, "urlopen", boom)
    with pytest.raises(ig.BackendUnavailable):
        ig.generate("x", "", _backend(), seed=None)

def test_generate_missing_image_path(monkeypatch):
    payload = json.dumps({"data": []}).encode()
    class R:
        status = 200
        def read(self, *a): return payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(ig.urllib.request, "urlopen", lambda req, timeout=None: R())
    with pytest.raises(ig.BackendUnavailable):
        ig.generate("x", "", _backend(), seed=None)

def test_resolve_image_rejects_non_http_url():
    b = _backend(response=cfg.ResponseSpec(image_path="images.0", image_kind="url", fetch_base=None))
    with pytest.raises(ig.BackendUnavailable):
        ig._resolve_image("file:///etc/passwd", b, {}, {})
```

- [ ] **Step 2: Run imagegen tests to verify they fail**

Run: `python -m pytest scripts/test_imagegen.py -q`
Expected: FAIL (engine helpers/`_render`/`_extract` not defined; old `generate` signature still references `_request_body`).

- [ ] **Step 3: Rewrite the HTTP client in `imagegen.py`**

Remove `_request_body` and the body of `generate` (the OpenAI path). Add `import re` to the stdlib imports at the top (alongside `base64`, `io`, `json`, `os`, `urllib.error`, `urllib.request`). Keep `SpecError`, `SpriteSpec`, `load_spec`, `resolve_dims`, `build_prompt`, and the `BackendUnavailable` class. Replace everything from `def _request_body` through the end of `generate` with:

```python
_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
_DROP = object()
_MAX_IMAGE_BYTES = 64 * 1024 * 1024


def _resolve_token(token: str, variables: dict):
    """Return (value, found) for a single ${...} token name."""
    token = token.strip()
    if token.startswith("env:"):
        return os.environ.get(token[4:], ""), True
    if token in variables:
        return variables[token], True
    return None, False


def _render(template, variables):
    """Substitute ${...} placeholders recursively.

    A value that is exactly "${x}" preserves the substituted value's native type; an
    embedded placeholder stringifies. A mapping value (or whole-value placeholder) that
    resolves to None or an unknown name is dropped from its enclosing mapping/list.
    """
    if isinstance(template, dict):
        out = {}
        for k, v in template.items():
            r = _render(v, variables)
            if r is _DROP:
                continue
            out[k] = r
        return out
    if isinstance(template, list):
        return [r for r in (_render(v, variables) for v in template) if r is not _DROP]
    if isinstance(template, str):
        whole = _PLACEHOLDER.fullmatch(template.strip())
        if whole:
            value, found = _resolve_token(whole.group(1), variables)
            if not found or value is None:
                return _DROP
            return value

        def repl(match):
            value, found = _resolve_token(match.group(1), variables)
            return "" if (not found or value is None) else str(value)

        return _PLACEHOLDER.sub(repl, template)
    return template


def _http(method: str, url: str, headers: dict, body, timeout):
    """Send an HTTP request; return (status, raw_bytes). Failures -> BackendUnavailable."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read(_MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(2048).decode("utf-8", "replace")
        except Exception:
            pass
        raise BackendUnavailable(f"{url} returned HTTP {exc.code}: {detail[:500]}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise BackendUnavailable(f"image backend unreachable at {url}: {exc}") from exc
    if len(raw) > _MAX_IMAGE_BYTES:
        raise BackendUnavailable(f"{url} response exceeds size cap ({_MAX_IMAGE_BYTES} bytes)")
    return status, raw


def _extract(obj, path):
    """Dotted/indexed lookup into parsed JSON, e.g. 'data.0.b64_json'."""
    cur = obj
    for part in str(path).split("."):
        try:
            if isinstance(cur, dict):
                cur = cur[part]
            elif isinstance(cur, (list, tuple)):
                cur = cur[int(part)]
            else:
                raise KeyError(part)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise BackendUnavailable(
                f"response has no value at path '{path}' (failed at '{part}')"
            ) from exc
    return cur


def _auth_header(auth, variables) -> dict:
    """Render the single auth header. If any ${env:NAME} token is unset/empty, drop it."""
    if not auth:
        return {}
    for match in _PLACEHOLDER.finditer(auth.value):
        tok = match.group(1).strip()
        if tok.startswith("env:") and not os.environ.get(tok[4:]):
            return {}
    value = _render(auth.value, variables)
    if value is _DROP or not value:
        return {}
    return {auth.header: value}


def _is_base64(s: str) -> bool:
    if len(s) < 16 or len(s) % 4 != 0:
        return False
    try:
        base64.b64decode(s, validate=True)
        return True
    except Exception:
        return False


def _decode_b64(ref: str) -> bytes:
    if ref.startswith("data:"):
        ref = ref.split(",", 1)[-1]
    try:
        return base64.b64decode(ref, validate=True)
    except Exception as exc:
        raise BackendUnavailable(f"could not base64-decode image: {exc}") from exc


def _resolve_image(ref, backend_cfg, auth: dict, variables) -> bytes:
    if not isinstance(ref, str):
        raise BackendUnavailable(f"image reference is not a string: {type(ref).__name__}")
    kind = backend_cfg.response.image_kind
    looks_b64 = ref.startswith("data:") or _is_base64(ref)
    if kind == "base64" or (kind == "auto" and looks_b64):
        return _decode_b64(ref)
    url = ref
    base = backend_cfg.response.fetch_base
    if base and not re.match(r"^https?://", ref):
        url = base.rstrip("/") + "/" + ref.lstrip("/")
    if not re.match(r"^https?://", url):
        raise BackendUnavailable(f"refusing to fetch non-http(s) image url: {url!r}")
    _, raw = _http("GET", url, dict(auth), None, backend_cfg.timeout)
    return raw


def generate(positive: str, negative: str, backend_cfg, seed) -> "Image.Image":
    gen = backend_cfg.gen_size
    variables = {
        "prompt": positive,
        "negative": negative,
        "model": backend_cfg.model,
        "gen_size": gen,
        "gen_width": gen,
        "gen_height": gen,
        "seed": seed,
    }
    auth = _auth_header(backend_cfg.auth, variables)

    if backend_cfg.prep is not None:
        p = backend_cfg.prep
        headers = {"Content-Type": "application/json", **_render(p.headers, variables), **auth}
        _, raw = _http(p.method, _render(p.url, variables), headers, _render(p.body, variables),
                       backend_cfg.timeout)
        try:
            prep_json = json.loads(raw)
        except ValueError as exc:
            raise BackendUnavailable(f"prep request to {p.url} returned non-JSON: {exc}") from exc
        for name, jpath in (p.capture or {}).items():
            variables[name] = _extract(prep_json, jpath)

    r = backend_cfg.request
    headers = {"Content-Type": "application/json", **_render(r.headers, variables), **auth}
    _, raw = _http(r.method, _render(r.url, variables), headers, _render(r.body, variables),
                   backend_cfg.timeout)
    try:
        resp_json = json.loads(raw)
    except ValueError as exc:
        raise BackendUnavailable(f"backend at {r.url} returned non-JSON: {exc}") from exc

    ref = _extract(resp_json, backend_cfg.response.image_path)
    img_bytes = _resolve_image(ref, backend_cfg, auth, variables)
    try:
        return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    except Exception as exc:
        raise BackendUnavailable(f"backend returned undecodable image data: {exc}") from exc
```

- [ ] **Step 4: Run imagegen tests to verify they pass**

Run: `python -m pytest scripts/test_imagegen.py -q`
Expected: PASS (5 spec/prompt + engine/flow tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/imagegen.py scripts/test_imagegen.py
git commit -m "feat: declarative HTTP image engine (template/extract/fetch) replacing OpenAI client"
```

---

### Task 3: Orchestrator field rename + restore full-suite green

Point the orchestrator at `cfg.backend` and update the orchestrator test fixture to the new schema.

**Files:**
- Modify: `scripts/render_sprites.py`
- Modify: `scripts/test_orchestrator.py`

**Interfaces:**
- Consumes: `config.Config.backend`, `imagegen.generate(positive, negative, backend_cfg, seed)`.
- Produces: no new public surface; `generate_sprite` and `main` behavior unchanged except the config field name.

- [ ] **Step 1: Update the orchestrator test fixture**

In `scripts/test_orchestrator.py`, replace the `CONFIG_YAML` `image:` line with the generic `backend:` block. Change:

```python
image: {endpoint: "http://x/v1/images/generations", model: m}
```

to:

```python
backend:
  request: {url: "http://x/v1/images/generations", body: {prompt: "${prompt}"}}
  response: {image_path: data.0.b64_json}
```

Keep the rest of `CONFIG_YAML` (size, dirs, `postprocess`) and all three tests unchanged. They monkeypatch `ig.generate`, so the backend body is never actually sent.

- [ ] **Step 2: Run orchestrator tests to verify they fail**

Run: `python -m pytest scripts/test_orchestrator.py -q`
Expected: FAIL (current `render_sprites.py` reads `cfg.image`; also `cfg.image` no longer exists -> `AttributeError`).

- [ ] **Step 3: Update `render_sprites.py`**

In `generate_sprite`, replace the two references to `cfg.image` with `cfg.backend`. Specifically, change:

```python
    base_seed = spec.gen.get("seed", cfg.image.params.get("seed"))
```

to:

```python
    base_seed = spec.gen.get("seed")
```

(The per-backend default seed previously came from `image.params`; seeds now live in the spec or the backend's `request.body` template, so the orchestrator only needs the spec seed. `None` means "no seed", which `_render` drops from the body.)

And change both `ig.generate(pos, neg, cfg.image, ...)` call sites to pass `cfg.backend`:

```python
            raw = ig.generate(pos, neg, cfg.backend, opts.get("seed", base_seed))
```
```python
                raw = ig.generate(pos, neg, cfg.backend, base_seed)
```

No other changes (the `--mode grid`, `--check`, `--fallback-grid`, and `--pack` paths do not touch the backend).

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest scripts/ -q`
Expected: PASS (config + imagegen + orchestrator + postprocess + grid all green).

- [ ] **Step 5: Commit**

```bash
git add scripts/render_sprites.py scripts/test_orchestrator.py
git commit -m "feat: orchestrator uses cfg.backend; full suite green on the new engine"
```

---

### Task 4: Ship backend presets + rewrite the config template

Provide copy-ready presets and a generic default config template. Validate every preset loads through `config.load_config`.

**Files:**
- Create: `templates/backends/openai.yaml`, `templates/backends/a1111.yaml`, `templates/backends/swarmui.yaml`
- Modify (rewrite): `templates/pixel-sprite.config.yaml`
- Create: `scripts/test_presets.py`

**Interfaces:**
- Consumes: `config.load_config`.
- Produces: preset files whose `backend:` blocks parse without error.

- [ ] **Step 1: Write the preset-loading test**

```python
# scripts/test_presets.py
from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BACKENDS = ROOT / "templates" / "backends"

@pytest.mark.parametrize("name", ["openai", "a1111", "swarmui"])
def test_preset_backend_loads(tmp_path, name):
    preset = (BACKENDS / f"{name}.yaml").read_text(encoding="utf-8")
    # a preset file contains a top-level `backend:` block; wrap with a size for a full config
    (tmp_path / cfg.CONFIG_FILENAME).write_text("size: 16\n" + preset, encoding="utf-8")
    c = cfg.load_config(tmp_path, None, {})
    assert c.backend.request.url
    assert c.backend.response.image_path

def test_default_template_loads(tmp_path):
    text = (ROOT / "templates" / "pixel-sprite.config.yaml").read_text(encoding="utf-8")
    (tmp_path / cfg.CONFIG_FILENAME).write_text(text, encoding="utf-8")
    c = cfg.load_config(tmp_path, None, {})
    assert c.backend.request.url
```

- [ ] **Step 2: Run the preset test to verify it fails**

Run: `python -m pytest scripts/test_presets.py -q`
Expected: FAIL (`templates/backends/*.yaml` do not exist yet).

- [ ] **Step 3: Create the preset files**

`templates/backends/openai.yaml`:

```yaml
# OpenAI-compatible image server (LocalAI, llama.cpp image, etc.)
backend:
  model: "sd-pixel"
  gen_size: 512
  timeout: 120
  request:
    method: POST
    url: "http://localhost:8080/v1/images/generations"
    headers: { Content-Type: application/json }
    body:
      model: "${model}"
      prompt: "${prompt}"
      negative_prompt: "${negative}"
      size: "${gen_width}x${gen_height}"
      n: 1
      response_format: b64_json
      seed: ${seed}
  response:
    image_path: "data.0.b64_json"
    image_kind: base64
  auth:
    header: "Authorization"
    value: "Bearer ${env:IMAGE_API_KEY}"
```

`templates/backends/a1111.yaml`:

```yaml
# Automatic1111 / Forge / SD.Next txt2img API
backend:
  gen_size: 1024
  timeout: 180
  request:
    method: POST
    url: "http://127.0.0.1:7860/sdapi/v1/txt2img"
    headers: { Content-Type: application/json }
    body:
      prompt: "${prompt}"
      negative_prompt: "${negative}"
      width: ${gen_width}
      height: ${gen_height}
      steps: 30
      cfg_scale: 7
      sampler_name: "Euler a"
      seed: ${seed}
  response:
    image_path: "images.0"
    image_kind: base64
```

`templates/backends/swarmui.yaml`:

```yaml
# SwarmUI (two-step: GetNewSession then GenerateText2Image, image fetched by path)
backend:
  model: "OfficialStableDiffusion/sd_xl_base_1.0"
  gen_size: 1024
  timeout: 180
  prep:
    method: POST
    url: "http://127.0.0.1:7801/API/GetNewSession"
    headers: { Content-Type: application/json }
    body: {}
    capture:
      session_id: "session_id"
  request:
    method: POST
    url: "http://127.0.0.1:7801/API/GenerateText2Image"
    headers: { Content-Type: application/json }
    body:
      session_id: "${session_id}"
      images: 1
      prompt: "${prompt}"
      negativeprompt: "${negative}"
      model: "${model}"
      width: ${gen_width}
      height: ${gen_height}
      steps: 30
      cfgscale: 7
      seed: ${seed}
  response:
    image_path: "images.0"
    image_kind: url
    fetch_base: "http://127.0.0.1:7801/"
```

- [ ] **Step 4: Rewrite `templates/pixel-sprite.config.yaml`**

Replace the old `image:` block with the generic `backend:` block (use the OpenAI preset's backend block as the default, since it matches the prior bundled default). Keep the existing `size`, `mode`, the four dirs, and the `prompt`, `postprocess`, `pack` blocks exactly as they are. Add a one-line `#` comment over the `backend:` block: `# image backend: copy a preset from templates/backends/ (openai | a1111 | swarmui)`. No emojis.

- [ ] **Step 5: Run the preset test to verify it passes**

Run: `python -m pytest scripts/test_presets.py -q`
Expected: PASS (3 presets + default template load).

- [ ] **Step 6: Commit**

```bash
git add templates/backends/ templates/pixel-sprite.config.yaml scripts/test_presets.py
git commit -m "feat: ship openai/a1111/swarmui backend presets and generic config template"
```

---

### Task 5: Rewrite the `/init` interview for backend selection

**Files:**
- Modify (rewrite): `commands/init.md`

- [ ] **Step 1: Update the interview**

Rewrite the backend portion of `commands/init.md` so that, when scaffolding a new `pixel-sprite.config.yaml`, the command:
1. Asks which backend the user wants: `openai` (default), `a1111`, or `swarmui`.
2. Copies the chosen preset's `backend:` block from `templates/backends/<choice>.yaml` into the generated `${CLAUDE_PROJECT_DIR}/pixel-sprite.config.yaml` (alongside `size`, `mode`, the four dirs, and the `prompt`/`postprocess`/`pack` blocks from the main template).
3. Interviews for the machine-specific values inside the chosen backend block: `request.url`, `model`, and `gen_size` (use the preset defaults for anything not answered). For `openai`, also mention the optional `IMAGE_API_KEY` env var. Do NOT write secrets into the file.
4. Keeps the existing directory-creation, template-copy (hero.yaml, example.json, gem.json), `--check` verification, and next-steps printing steps unchanged.

Preserve the bundled invocation path `python "${CLAUDE_PLUGIN_ROOT}/scripts/render_sprites.py"`. No emojis.

- [ ] **Step 2: Verify references**

Confirm the three preset filenames referenced in `init.md` exist under `templates/backends/`. Confirm no emojis.

- [ ] **Step 3: Commit**

```bash
git add commands/init.md
git commit -m "feat: interactive init selects and configures an image backend preset"
```

---

### Task 6: Update README, SKILL.md, and plugin metadata

**Files:**
- Modify: `README.md`, `skills/pixel-sprite-generator/SKILL.md`, `.claude-plugin/plugin.json`

- [ ] **Step 1: README**

Replace the OpenAI-specific backend documentation with a "Backends" section that explains: the generic `backend:` block (the `prep` / `request` / `response` / `auth` keys), the placeholder set (`${prompt}`, `${negative}`, `${model}`, `${gen_width}`/`${gen_height}`/`${gen_size}`, `${seed}`, `${env:NAME}`, and prep-captured vars), the type-preservation and None-drops-key rules, the `image_kind` base64/url/auto behavior with `fetch_base`, and a table of the three shipped presets (openai, a1111, swarmui) with one line each. Note keys come from env only (`${env:NAME}`), never the file, and that an unset `${env:}` in `auth.value` drops the auth header. Keep the Showcase (`--mode grid`) and `--pack` sections. Author line stays `MisterVitoPro`. No emojis.

- [ ] **Step 2: SKILL.md**

In `skills/pixel-sprite-generator/SKILL.md`, update the backend references: the default flow still runs `render_sprites.py --only <id>`, but document that the backend is configured via the `backend:` block in `pixel-sprite.config.yaml` (copy a preset from `templates/backends/`). Update any sentence that named the OpenAI `/v1/images/generations` endpoint as the only option to instead say "the configured backend"; keep the exit-code-3 STOP-and-ask-about-grid-fallback behavior and the `prompt.prefix`/`prompt.suffix`/`prompt.negative` guidance. No emojis.

- [ ] **Step 3: plugin.json**

In `.claude-plugin/plugin.json`: update `description` to mention pluggable/config-driven image backends (SwarmUI, Automatic1111, OpenAI-compatible) with a deterministic grid fallback; bump `version` from `0.2.0` to `0.3.0`; keep `author.name` `MisterVitoPro`; keep the existing keywords (including `image-generation`). Keep valid JSON.

- [ ] **Step 4: Final full-suite run**

Run: `python -m pytest scripts/ -q`
Expected: PASS (config, imagegen, orchestrator, postprocess, grid, presets all green).

- [ ] **Step 5: Commit**

```bash
git add README.md skills/pixel-sprite-generator/SKILL.md .claude-plugin/plugin.json
git commit -m "docs: document pluggable backends; bump to 0.3.0"
```

---

## Self-Review

**Spec coverage:**
- Declarative engine (render/http/extract/fetch) -> Task 2.
- 2-step prep + fetch-by-URL flow -> Task 2 (`generate`, `_resolve_image`), tested in `test_generate_two_step_fetch_url`.
- Generic `backend:` schema + dataclasses + strict validation -> Task 1.
- Placeholder set, type preservation, None-drops-key, `${env:}` -> Task 2 (`_render`, `_resolve_token`), tested.
- `image_kind` base64/url/auto + `fetch_base` + http(s)-only guard -> Task 2 (`_resolve_image`), tested.
- Auth header + drop-when-env-unset + redaction -> Task 2 (`_auth_header`); secrets never logged (the engine never prints `auth`).
- Clean break + presets (openai/a1111/swarmui) + config template -> Task 4.
- init interview -> Task 5.
- README/SKILL/plugin 0.3.0 -> Task 6.
- Exit-code contract (3 backend / 2 config / 1 spec) -> preserved in Tasks 1-3.
- stdlib-only, no new deps -> Tasks 1-2 (urllib/json/base64/re).

**Placeholder scan:** No TBD/TODO; every code step carries full code; doc/template steps specify exact content.

**Type consistency:** `BackendConfig`/`RequestSpec`/`PrepSpec`/`ResponseSpec`/`AuthSpec` field names are identical across `config.py` (Task 1), the `imagegen` engine usage `backend_cfg.request.url`/`backend_cfg.response.image_path`/`backend_cfg.prep.capture`/`backend_cfg.auth.value` (Task 2), the orchestrator `cfg.backend` (Task 3), and the test fixtures. `generate(positive, negative, backend_cfg, seed)` signature is consistent between definition (Task 2), the orchestrator call sites (Task 3), and the monkeypatched tests. `_render`/`_extract`/`_auth_header`/`_resolve_image` names match between definition and tests. `IMAGE_KINDS` is defined in Task 1 and consumed by validation there.

**Note for implementer:** Task 1 leaves `test_imagegen.py` and `test_orchestrator.py` transiently red (they still reference the removed `ImageConfig`/`image:` schema); Tasks 2 and 3 update them, and full-suite green is asserted at Task 3 Step 4 and again at Task 6 Step 4.
