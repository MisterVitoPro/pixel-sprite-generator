# scripts/test_config.py
from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

MINIMAL = """
size: 16
backend:
  request:
    url: http://localhost:9000/v1/images/generations
    body: {prompt: "${prompt}"}
  response:
    image_path: data.0.b64_json
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

def test_unknown_top_level_key_rejected(tmp_path):
    write_cfg(tmp_path, MINIMAL + "\nbogus: 1\n")
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(tmp_path, None, {})

def test_size_must_be_power_of_two(tmp_path):
    write_cfg(tmp_path, "size: 24\nbackend: {request: {url: x, body: {}}, response: {image_path: x}}\n")
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
