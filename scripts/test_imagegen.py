# scripts/test_imagegen.py
from __future__ import annotations
import base64
import io
import json
import sys
import urllib.error
from pathlib import Path
import pytest
from PIL import Image
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
