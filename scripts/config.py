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
IMAGE_KINDS = ("base64", "url", "auto")

DEFAULTS = {
    "size": 16,
    "mode": "auto",
    "sprites_dir": "art/sprites",
    "shapes_dir": "art/shapes",
    "palettes_dir": "art/palettes",
    "out_dir": "assets/sprites",
    "backend": {
        "model": None,
        "gen_size": 512,
        "timeout": 120,
        "prep": None,
        "request": {
            "method": "POST",
            "url": None,
            "headers": {},
            "body": {},
        },
        "response": {
            "image_path": None,
            "image_kind": "auto",
            "fetch_base": None,
        },
        "auth": None,
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


class ConfigError(Exception):
    """Missing/invalid configuration (environment error -> exit code 2)."""


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
    backend: BackendConfig
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
        backend=_build_backend(merged["backend"]),
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
