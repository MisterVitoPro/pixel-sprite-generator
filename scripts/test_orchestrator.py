# scripts/test_orchestrator.py
from __future__ import annotations
import sys, io, base64, json
from pathlib import Path
import pytest
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_sprites as rs   # noqa: E402
import imagegen as ig         # noqa: E402

CONFIG_YAML = """
size: 16
sprites_dir: art/sprites
shapes_dir: art/shapes
palettes_dir: art/palettes
out_dir: out
backend:
  request: {url: "http://x/v1/images/generations", body: {prompt: "${prompt}"}}
  response: {image_path: data.0.b64_json}
postprocess: {background: {method: none}, quantize: {enabled: false}}
"""

@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "art/sprites").mkdir(parents=True)
    (tmp_path / "art/shapes").mkdir(parents=True)
    (tmp_path / "art/palettes").mkdir(parents=True)
    (tmp_path / "pixel-sprite.config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    (tmp_path / "art/sprites/hero.yaml").write_text("id: hero\nprompt: a knight\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path

def _fake_image(*a, **k):
    return Image.new("RGBA", (32, 32), (10, 20, 30, 255))

def test_image_path_writes_png(project, monkeypatch):
    monkeypatch.setattr(ig, "generate", _fake_image)
    rc = rs.main(["--only", "hero"])
    assert rc == 0
    assert (project / "out/hero.png").is_file()
    assert Image.open(project / "out/hero.png").size == (16, 16)

def test_backend_failure_exits_3(project, monkeypatch):
    def boom(*a, **k):
        raise ig.BackendUnavailable("nope")
    monkeypatch.setattr(ig, "generate", boom)
    rc = rs.main(["--only", "hero"])
    assert rc == 3

def test_fallback_grid_used_when_flagged(project, monkeypatch):
    def boom(*a, **k):
        raise ig.BackendUnavailable("nope")
    monkeypatch.setattr(ig, "generate", boom)
    # provide a grid fallback source + palette
    (project / "art/palettes/iron.json").write_text(
        json.dumps({"colors": {"B": "#C8C8C8"}}), encoding="utf-8")
    rows = ["." * 16 for _ in range(16)]
    rows[0] = "B" + "." * 15
    (project / "art/shapes/hero.json").write_text(
        json.dumps({"id": "hero", "size": 16, "outputs": {"hero": "iron"}, "rows": rows}),
        encoding="utf-8")
    rc = rs.main(["--only", "hero", "--fallback-grid"])
    assert rc == 0
    assert (project / "out/hero.png").is_file()
