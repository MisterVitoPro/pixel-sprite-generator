# scripts/test_imagegen.py
from __future__ import annotations
import base64
import io
import json
import re
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
