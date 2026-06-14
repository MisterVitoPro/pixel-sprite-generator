"""Tests for render_textures.py — the JSON pixel-grid -> 16x16 PNG converter.

Run with: python -m pytest scripts/test_render_textures.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_sprites as rs  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

BLANK_ROWS = ["." * 16 for _ in range(16)]


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def art(tmp_path: Path) -> dict:
    shapes = tmp_path / "shapes"
    palettes = tmp_path / "palettes"
    out = tmp_path / "out"
    shapes.mkdir()
    palettes.mkdir()
    out.mkdir()
    # a couple of baseline palettes
    write_json(palettes / "iron.json", {"colors": {"B": "#C8C8C8", "b": "#8B8B8B", "a": "#6E6E6E"}})
    write_json(palettes / "leather.json", {"colors": {"B": "#5A3E22", "a": "#000000"}})
    write_json(palettes / "leather_diamond.json", {"extends": "leather", "colors": {"a": "#7DF2EE"}})
    return {"shapes": shapes, "palettes": palettes, "out": out}


def diagonal_rows() -> list[str]:
    rows = ["." * 16 for _ in range(16)]
    grid = [list(r) for r in rows]
    for i in range(16):
        grid[i][i] = "B"
    return ["".join(r) for r in grid]


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #

def test_render_produces_16x16_rgba(art):
    shape = {"id": "thing", "size": 16, "outputs": {"thing": "iron"}, "rows": diagonal_rows()}
    write_json(art["shapes"] / "thing.json", shape)
    rs.render_file(art["shapes"] / "thing.json", art["palettes"], art["out"])
    png = art["out"] / "thing.png"
    assert png.exists()
    with Image.open(png) as img:
        assert img.size == (16, 16)
        assert img.mode == "RGBA"
        # diagonal pixel is iron highlight, off-diagonal is transparent
        assert img.getpixel((0, 0)) == (0xC8, 0xC8, 0xC8, 255)
        assert img.getpixel((5, 0)) == (0, 0, 0, 0)


def test_multiple_outputs_one_shape(art):
    write_json(art["palettes"] / "diamond.json", {"colors": {"B": "#7DF2EE", "b": "#4DD2D6", "a": "#6E6E6E"}})
    shape = {"id": "thing", "size": 16,
             "outputs": {"thing": "iron", "thing_diamond": "diamond"},
             "rows": diagonal_rows()}
    write_json(art["shapes"] / "thing.json", shape)
    rs.render_file(art["shapes"] / "thing.json", art["palettes"], art["out"])
    assert (art["out"] / "thing.png").exists()
    with Image.open(art["out"] / "thing_diamond.png") as img:
        assert img.getpixel((0, 0)) == (0x7D, 0xF2, 0xEE, 255)


def test_dot_is_transparent(art):
    shape = {"id": "thing", "size": 16, "outputs": {"thing": "iron"}, "rows": BLANK_ROWS}
    write_json(art["shapes"] / "thing.json", shape)
    rs.render_file(art["shapes"] / "thing.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "thing.png") as img:
        assert img.getpixel((8, 8)) == (0, 0, 0, 0)


def test_8digit_hex_alpha(art):
    write_json(art["palettes"] / "ghost.json", {"colors": {"B": "#C8C8C880"}})
    shape = {"id": "g", "size": 16, "outputs": {"g": "ghost"}, "rows": diagonal_rows()}
    write_json(art["shapes"] / "g.json", shape)
    rs.render_file(art["shapes"] / "g.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "g.png") as img:
        assert img.getpixel((0, 0)) == (0xC8, 0xC8, 0xC8, 0x80)


# --------------------------------------------------------------------------- #
# palette inheritance
# --------------------------------------------------------------------------- #

def test_extends_overrides_single_char(art):
    resolved = rs.resolve_palette("leather_diamond", art["palettes"])
    assert resolved["a"] == "#7DF2EE"   # overridden
    assert resolved["B"] == "#5A3E22"   # inherited


def test_extends_cycle_raises(art):
    write_json(art["palettes"] / "p1.json", {"extends": "p2", "colors": {}})
    write_json(art["palettes"] / "p2.json", {"extends": "p1", "colors": {}})
    with pytest.raises(rs.RenderError, match="cycle"):
        rs.resolve_palette("p1", art["palettes"])


def test_extends_missing_raises(art):
    write_json(art["palettes"] / "p.json", {"extends": "nope", "colors": {}})
    with pytest.raises(rs.RenderError, match="nope"):
        rs.resolve_palette("p", art["palettes"])


# --------------------------------------------------------------------------- #
# strict validation — hard fail
# --------------------------------------------------------------------------- #

def test_bad_hex_raises(art):
    write_json(art["palettes"] / "bad.json", {"colors": {"B": "#ZZZ"}})
    with pytest.raises(rs.RenderError, match="hex"):
        rs.resolve_palette("bad", art["palettes"])


def test_wrong_row_count_raises(art):
    shape = {"id": "x", "size": 16, "outputs": {"x": "iron"}, "rows": ["." * 16 for _ in range(15)]}
    write_json(art["shapes"] / "x.json", shape)
    with pytest.raises(rs.RenderError, match="16 rows"):
        rs.render_file(art["shapes"] / "x.json", art["palettes"], art["out"])


def test_wrong_row_length_raises(art):
    rows = diagonal_rows()
    rows[4] = "." * 17
    shape = {"id": "x", "size": 16, "outputs": {"x": "iron"}, "rows": rows}
    write_json(art["shapes"] / "x.json", shape)
    with pytest.raises(rs.RenderError, match="row 4"):
        rs.render_file(art["shapes"] / "x.json", art["palettes"], art["out"])


def test_undefined_char_raises(art):
    rows = diagonal_rows()
    rows[4] = rows[4][:9] + "x" + rows[4][10:]
    shape = {"id": "x", "size": 16, "outputs": {"x": "iron"}, "rows": rows}
    write_json(art["shapes"] / "x.json", shape)
    with pytest.raises(rs.RenderError, match="'x'"):
        rs.render_file(art["shapes"] / "x.json", art["palettes"], art["out"])


def test_size_not_16_raises(art):
    shape = {"id": "x", "size": 32, "outputs": {"x": "iron"}, "rows": diagonal_rows()}
    write_json(art["shapes"] / "x.json", shape)
    with pytest.raises(rs.RenderError, match="size"):
        rs.render_file(art["shapes"] / "x.json", art["palettes"], art["out"])


def test_id_mismatch_raises(art):
    shape = {"id": "wrong", "size": 16, "outputs": {"wrong": "iron"}, "rows": diagonal_rows()}
    write_json(art["shapes"] / "x.json", shape)
    with pytest.raises(rs.RenderError, match="id"):
        rs.render_file(art["shapes"] / "x.json", art["palettes"], art["out"])


def test_missing_palette_for_output_raises(art):
    shape = {"id": "x", "size": 16, "outputs": {"x": "doesnotexist"}, "rows": diagonal_rows()}
    write_json(art["shapes"] / "x.json", shape)
    with pytest.raises(rs.RenderError, match="doesnotexist"):
        rs.render_file(art["shapes"] / "x.json", art["palettes"], art["out"])


# --------------------------------------------------------------------------- #
# --check / validate-all
# --------------------------------------------------------------------------- #

def test_validate_all_ok(art):
    shape = {"id": "thing", "size": 16, "outputs": {"thing": "iron"}, "rows": diagonal_rows()}
    write_json(art["shapes"] / "thing.json", shape)
    errors = rs.validate_all(art["shapes"], art["palettes"])
    assert errors == []


def test_validate_all_collects_errors(art):
    bad = {"id": "thing", "size": 16, "outputs": {"thing": "iron"}, "rows": ["." * 16 for _ in range(15)]}
    write_json(art["shapes"] / "thing.json", bad)
    errors = rs.validate_all(art["shapes"], art["palettes"])
    assert len(errors) == 1
    assert "thing" in errors[0]


# --------------------------------------------------------------------------- #
# gradients — a palette char maps to {from, to, axis}; converter interpolates
# --------------------------------------------------------------------------- #

def _full_grid(char: str) -> list[str]:
    return [char * 16 for _ in range(16)]


def _single_column(char: str, col: int) -> list[str]:
    grid = [list("." * 16) for _ in range(16)]
    for y in range(16):
        grid[y][col] = char
    return ["".join(r) for r in grid]


def _single_row(char: str, row: int) -> list[str]:
    grid = [list("." * 16) for _ in range(16)]
    for x in range(16):
        grid[row][x] = char
    return ["".join(r) for r in grid]


def test_gradient_y_axis_interpolates(art):
    write_json(art["palettes"] / "grad.json",
               {"colors": {"g": {"from": "#000000", "to": "#FFFFFF", "axis": "y"}}})
    shape = {"id": "gr", "size": 16, "outputs": {"gr": "grad"}, "rows": _single_column("g", 8)}
    write_json(art["shapes"] / "gr.json", shape)
    rs.render_file(art["shapes"] / "gr.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "gr.png") as img:
        assert img.getpixel((8, 0)) == (0, 0, 0, 255)          # t=0 -> from
        assert img.getpixel((8, 15)) == (255, 255, 255, 255)   # t=1 -> to
        assert img.getpixel((8, 8))[0] == round(8 / 15 * 255)  # midpoint channel


def test_gradient_x_axis(art):
    write_json(art["palettes"] / "gx.json",
               {"colors": {"g": {"from": "#000000", "to": "#FFFFFF", "axis": "x"}}})
    shape = {"id": "gx", "size": 16, "outputs": {"gx": "gx"}, "rows": _full_grid("g")}
    write_json(art["shapes"] / "gx.json", shape)
    rs.render_file(art["shapes"] / "gx.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "gx.png") as img:
        assert img.getpixel((0, 5)) == (0, 0, 0, 255)
        assert img.getpixel((15, 5)) == (255, 255, 255, 255)


def test_gradient_diag_axis(art):
    write_json(art["palettes"] / "gd.json",
               {"colors": {"g": {"from": "#000000", "to": "#FFFFFF", "axis": "diag"}}})
    shape = {"id": "gd", "size": 16, "outputs": {"gd": "gd"}, "rows": _full_grid("g")}
    write_json(art["shapes"] / "gd.json", shape)
    rs.render_file(art["shapes"] / "gd.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "gd.png") as img:
        assert img.getpixel((0, 0)) == (0, 0, 0, 255)            # coord x+y = 0 (min)
        assert img.getpixel((15, 15)) == (255, 255, 255, 255)    # coord 30 (max)


def test_gradient_adiag_axis(art):
    write_json(art["palettes"] / "ga.json",
               {"colors": {"g": {"from": "#000000", "to": "#FFFFFF", "axis": "adiag"}}})
    shape = {"id": "ga", "size": 16, "outputs": {"ga": "ga"}, "rows": _full_grid("g")}
    write_json(art["shapes"] / "ga.json", shape)
    rs.render_file(art["shapes"] / "ga.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "ga.png") as img:
        assert img.getpixel((0, 15)) == (0, 0, 0, 255)           # coord x-y = -15 (min)
        assert img.getpixel((15, 0)) == (255, 255, 255, 255)     # coord 15 (max)


def test_gradient_single_line_extent_resolves_to_from(art):
    # a horizontal line under a y-axis gradient: every pixel shares y -> span 0 -> from
    write_json(art["palettes"] / "gl.json",
               {"colors": {"g": {"from": "#112233", "to": "#FFFFFF", "axis": "y"}}})
    shape = {"id": "gl", "size": 16, "outputs": {"gl": "gl"}, "rows": _single_row("g", 7)}
    write_json(art["shapes"] / "gl.json", shape)
    rs.render_file(art["shapes"] / "gl.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "gl.png") as img:
        assert img.getpixel((3, 7)) == (0x11, 0x22, 0x33, 255)


def test_gradient_alpha_interpolates(art):
    write_json(art["palettes"] / "galpha.json",
               {"colors": {"g": {"from": "#FFFFFF00", "to": "#FFFFFFFF", "axis": "y"}}})
    shape = {"id": "gp", "size": 16, "outputs": {"gp": "galpha"}, "rows": _single_column("g", 8)}
    write_json(art["shapes"] / "gp.json", shape)
    rs.render_file(art["shapes"] / "gp.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "gp.png") as img:
        assert img.getpixel((8, 0))[3] == 0
        assert img.getpixel((8, 15))[3] == 255


def test_gradient_flat_colors_still_work(art):
    # regression: a palette mixing flat + gradient chars renders flats unchanged
    write_json(art["palettes"] / "mix.json",
               {"colors": {"B": "#C8C8C8", "g": {"from": "#000000", "to": "#FFFFFF", "axis": "y"}}})
    grid = [list("." * 16) for _ in range(16)]
    grid[0][0] = "B"
    grid[15][15] = "g"
    shape = {"id": "mix", "size": 16, "outputs": {"mix": "mix"}, "rows": ["".join(r) for r in grid]}
    write_json(art["shapes"] / "mix.json", shape)
    rs.render_file(art["shapes"] / "mix.json", art["palettes"], art["out"])
    with Image.open(art["out"] / "mix.png") as img:
        assert img.getpixel((0, 0)) == (0xC8, 0xC8, 0xC8, 255)  # flat unchanged
        # single gradient pixel -> span 0 -> from
        assert img.getpixel((15, 15)) == (0, 0, 0, 255)


def test_gradient_invalid_axis_raises(art):
    write_json(art["palettes"] / "bad.json",
               {"colors": {"g": {"from": "#000000", "to": "#FFFFFF", "axis": "radial"}}})
    with pytest.raises(rs.RenderError, match="axis"):
        rs.resolve_palette("bad", art["palettes"])


def test_gradient_invalid_hex_raises(art):
    write_json(art["palettes"] / "bad2.json",
               {"colors": {"g": {"from": "#ZZZ", "to": "#FFFFFF", "axis": "x"}}})
    with pytest.raises(rs.RenderError, match="hex"):
        rs.resolve_palette("bad2", art["palettes"])


def test_gradient_wrong_keys_raises(art):
    write_json(art["palettes"] / "bad3.json",
               {"colors": {"g": {"from": "#000000", "axis": "x"}}})
    with pytest.raises(rs.RenderError, match="from"):
        rs.resolve_palette("bad3", art["palettes"])


# --------------------------------------------------------------------------- #
# configurable size (power of two)
# --------------------------------------------------------------------------- #

def _diagonal_rows_n(n: int) -> list[str]:
    grid = [list("." * n) for _ in range(n)]
    for i in range(n):
        grid[i][i] = "B"
    return ["".join(r) for r in grid]


def test_size_configurable_32(art):
    shape = {"id": "big", "size": 32, "outputs": {"big": "iron"}, "rows": _diagonal_rows_n(32)}
    write_json(art["shapes"] / "big.json", shape)
    rs.render_file(art["shapes"] / "big.json", art["palettes"], art["out"], size=32)
    with Image.open(art["out"] / "big.png") as img:
        assert img.size == (32, 32)
        assert img.getpixel((0, 0)) == (0xC8, 0xC8, 0xC8, 255)


def test_is_power_of_two():
    assert rs.is_power_of_two(16)
    assert rs.is_power_of_two(1)
    assert rs.is_power_of_two(256)
    assert not rs.is_power_of_two(12)
    assert not rs.is_power_of_two(0)
    assert not rs.is_power_of_two(-16)
    assert not rs.is_power_of_two(20)
    assert not rs.is_power_of_two(True)   # bool is not a valid size
    assert not rs.is_power_of_two("16")   # str is not a valid size


def test_non_power_of_two_size_raises_config(tmp_path):
    (tmp_path / "art" / "shapes").mkdir(parents=True)
    (tmp_path / "art" / "palettes").mkdir(parents=True)
    write_json(tmp_path / "pixel-sprite.config.json", {"size": 12})
    with pytest.raises(rs.ConfigError, match="power of two"):
        rs.load_config(tmp_path, None, _no_overrides())


def test_non_power_of_two_size_raises_cli(art, tmp_path):
    write_json(tmp_path / "pixel-sprite.config.json", {})
    with pytest.raises(rs.ConfigError, match="power of two"):
        rs.load_config(tmp_path, None, {"size": 20, "shapes_dir": None,
                                        "palettes_dir": None, "out_dir": None})


def test_zero_and_negative_size_raise(tmp_path):
    write_json(tmp_path / "pixel-sprite.config.json", {"size": 0})
    with pytest.raises(rs.ConfigError, match="power of two"):
        rs.load_config(tmp_path, None, _no_overrides())


# --------------------------------------------------------------------------- #
# config loading + CLI overrides
# --------------------------------------------------------------------------- #

def _no_overrides() -> dict:
    return {"size": None, "shapes_dir": None, "palettes_dir": None, "out_dir": None}


def test_config_loaded_from_project_root(tmp_path):
    write_json(tmp_path / "pixel-sprite.config.json",
               {"size": 16, "shapes_dir": "g/shapes", "palettes_dir": "g/palettes",
                "out_dir": "build/sprites"})
    cfg = rs.load_config(tmp_path, None, _no_overrides())
    assert cfg.size == 16
    assert cfg.shapes_dir == (tmp_path / "g" / "shapes").resolve()
    assert cfg.palettes_dir == (tmp_path / "g" / "palettes").resolve()
    assert cfg.out_dir == (tmp_path / "build" / "sprites").resolve()


def test_cli_overrides_config(tmp_path):
    write_json(tmp_path / "pixel-sprite.config.json",
               {"size": 16, "out_dir": "fromfile"})
    cfg = rs.load_config(tmp_path, None,
                         {"size": 32, "shapes_dir": None, "palettes_dir": None,
                          "out_dir": "fromcli"})
    assert cfg.size == 32
    assert cfg.out_dir == (tmp_path / "fromcli").resolve()


def test_missing_config_without_cli_fallback_errors(tmp_path):
    with pytest.raises(rs.ConfigError, match="No pixel-sprite.config.json"):
        rs.load_config(tmp_path, None, _no_overrides())


def test_cli_only_no_config_file_ok(tmp_path):
    cfg = rs.load_config(tmp_path, None,
                         {"size": None, "shapes_dir": "s", "palettes_dir": "p",
                          "out_dir": "o"})
    assert cfg.size == 16  # default fills in
    assert cfg.shapes_dir == (tmp_path / "s").resolve()


def test_explicit_config_path_anchors_relative_paths(tmp_path):
    cfgdir = tmp_path / "cfg"
    cfgdir.mkdir()
    write_json(cfgdir / "pixel-sprite.config.json",
               {"shapes_dir": "shapes", "out_dir": "out"})
    cfg = rs.load_config(tmp_path, cfgdir / "pixel-sprite.config.json", _no_overrides())
    # relative paths resolve against the config file's directory, not project_root
    assert cfg.shapes_dir == (cfgdir / "shapes").resolve()


def test_explicit_config_path_missing_raises(tmp_path):
    with pytest.raises(rs.ConfigError, match="config file not found"):
        rs.load_config(tmp_path, tmp_path / "nope.json", _no_overrides())


def test_unknown_config_key_rejected(tmp_path):
    write_json(tmp_path / "pixel-sprite.config.json", {"size": 16, "bogus": 1})
    with pytest.raises(rs.ConfigError, match="unknown config key"):
        rs.load_config(tmp_path, None, _no_overrides())


def test_config_not_object_rejected(tmp_path):
    (tmp_path / "pixel-sprite.config.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(rs.ConfigError, match="object"):
        rs.load_config(tmp_path, None, _no_overrides())


# --------------------------------------------------------------------------- #
# main() end-to-end via cwd
# --------------------------------------------------------------------------- #

def _project(tmp_path) -> Path:
    (tmp_path / "art" / "shapes").mkdir(parents=True)
    (tmp_path / "art" / "palettes").mkdir(parents=True)
    write_json(tmp_path / "art" / "palettes" / "iron.json",
               {"colors": {"B": "#C8C8C8", "b": "#8B8B8B", "a": "#6E6E6E"}})
    write_json(tmp_path / "art" / "shapes" / "thing.json",
               {"id": "thing", "size": 16, "outputs": {"thing": "iron"},
                "rows": _diagonal_rows_n(16)})
    write_json(tmp_path / "pixel-sprite.config.json",
               {"size": 16, "shapes_dir": "art/shapes", "palettes_dir": "art/palettes",
                "out_dir": "out"})
    return tmp_path


def test_main_renders_to_configured_out_dir(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.chdir(proj)
    rc = rs.main([])
    assert rc == 0
    assert (proj / "out" / "thing.png").exists()


def test_main_cli_out_dir_override(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.chdir(proj)
    rc = rs.main(["--out-dir", "elsewhere"])
    assert rc == 0
    assert (proj / "elsewhere" / "thing.png").exists()
    assert not (proj / "out" / "thing.png").exists()


def test_main_check_passes(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.chdir(proj)
    rc = rs.main(["--check"])
    assert rc == 0
    assert not (proj / "out" / "thing.png").exists()  # --check writes nothing


def test_main_missing_config_returns_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = rs.main([])
    assert rc == 2


def test_main_bad_size_returns_2(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    write_json(proj / "pixel-sprite.config.json",
               {"size": 12, "shapes_dir": "art/shapes", "palettes_dir": "art/palettes",
                "out_dir": "out"})
    monkeypatch.chdir(proj)
    rc = rs.main([])
    assert rc == 2
