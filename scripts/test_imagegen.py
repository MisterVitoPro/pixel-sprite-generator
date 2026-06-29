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
