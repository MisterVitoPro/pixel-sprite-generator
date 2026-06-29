#!/usr/bin/env python3
"""Sprite-spec loading, prompt building, and the OpenAI-compatible HTTP image client."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

import base64
import io
import json
import os
import re
import urllib.error
import urllib.request

try:
    import yaml
except ImportError:  # pragma: no cover
    import sys
    sys.stderr.write("Error: PyYAML is not installed. Install it with:\n  pip install PyYAML\n")
    raise SystemExit(2)

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    import sys
    sys.stderr.write("Error: Pillow is not installed. Install it with:\n  pip install Pillow\n")
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


class BackendUnavailable(Exception):
    """The image backend could not be reached or returned an unusable response (exit code 3)."""


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
