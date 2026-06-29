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
