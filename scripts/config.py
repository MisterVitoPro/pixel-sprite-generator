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
