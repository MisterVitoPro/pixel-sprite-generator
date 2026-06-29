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
