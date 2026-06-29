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
