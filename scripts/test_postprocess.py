# scripts/test_postprocess.py
from __future__ import annotations
import sys
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
import postprocess as pp  # noqa: E402

def solid(w, h, rgba):
    return Image.new("RGBA", (w, h), rgba)

def test_downscale_to_exact_dims():
    out = pp.downscale(solid(64, 64, (10, 20, 30, 255)), 16, 16, "nearest")
    assert out.size == (16, 16)

def test_chroma_key_makes_background_transparent():
    img = solid(4, 4, (255, 0, 255, 255))   # all magenta
    img.putpixel((1, 1), (200, 50, 50, 255))  # one red pixel
    out = pp.remove_background(img, "chroma", "#FF00FF", 20)
    assert out.getpixel((0, 0))[3] == 0       # magenta -> transparent
    assert out.getpixel((1, 1))[3] == 255     # red kept

def test_quantize_reduces_color_count():
    img = Image.new("RGBA", (8, 8))
    for i in range(8):
        for j in range(8):
            img.putpixel((i, j), (i * 30 % 256, j * 30 % 256, 0, 255))
    out = pp.quantize(img, 4)
    opaque = {p[:3] for p in out.getdata() if p[3] == 255}
    assert len(opaque) <= 4

def test_recolor_maps_onto_target_ramp():
    img = solid(2, 2, (100, 100, 100, 255))
    img.putpixel((0, 0), (200, 200, 200, 255))
    target = [(0, 0, 0, 255), (255, 255, 255, 255)]
    out = pp.recolor(img, target)
    colors = {p[:3] for p in out.getdata()}
    assert colors <= {(0, 0, 0), (255, 255, 255)}

def test_add_outline_adds_dark_border_pixels():
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    img.putpixel((1, 1), (255, 0, 0, 255))
    out = pp.add_outline(img, "#000000")
    assert out.getpixel((0, 1))[3] == 255  # neighbor became outline
