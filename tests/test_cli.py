"""Tests for kicad-cli wrappers and manufacturing export."""

import csv
import os
import zipfile

import pytest

from kicad_mcp import cli
from tests.conftest import needs_footprint_libs, needs_kicad_cli


def test_import_ses_runs_without_name_error(monkeypatch):
    scripts = []
    monkeypatch.setattr(cli, "_run_pcbnew", lambda script, timeout=60: scripts.append(script) or "ok")
    assert cli.import_ses("/tmp/board.kicad_pcb", "/tmp/board.ses") == "/tmp/board.kicad_pcb"
    assert "ImportSpecctraSES" in scripts[0]


def test_cpl_keeps_position_file_coordinates(tmp_path):
    pos = tmp_path / "pos.csv"
    pos.write_text(
        "Ref,Val,Package,PosX,PosY,Rot,Side\n"
        '"R1","10k","R_0603",20.000000,-20.000000,90.000000,top\n'
        '"R2","10k","R_0603",40.000000,-10.000000,0.000000,bottom\n'
    )
    out = tmp_path / "cpl.csv"
    cli._generate_cpl(str(pos), str(out), "jlcpcb")
    rows = list(csv.DictReader(out.open()))
    assert rows[0] == {"Designator": "R1", "Mid X": "20.0mm", "Mid Y": "-20.0mm",
                       "Rotation": "90.000000", "Layer": "Top"}
    assert rows[1]["Mid Y"] == "-10.0mm" and rows[1]["Layer"] == "Bottom"


def test_unknown_manufacturing_format_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "export_gerbers", lambda f, d: [])
    monkeypatch.setattr(cli, "export_position_file", lambda *a, **k: None)
    with pytest.raises(ValueError, match="Unknown format"):
        cli.export_manufacturing("b.kicad_pcb", str(tmp_path), "fabco")


@needs_kicad_cli
@needs_footprint_libs
def test_manufacturing_export_is_repeatable(tmp_path):
    from kicad_mcp import pcb

    board = tmp_path / "b.kicad_pcb"
    pcb.set_board_outline(str(board), [(0, 0), (30, 0), (30, 20), (0, 20)])
    pcb.place_footprint(str(board), "Resistor_SMD:R_0603_1608Metric", "R1", "10k", 10, 10)
    out = tmp_path / "mfg"
    out.mkdir()
    (out / "stale-from-other-board.gbr").write_text("old")

    first = cli.export_manufacturing(str(board), str(out))
    second = cli.export_manufacturing(str(board), str(out))

    names = zipfile.ZipFile(second["zip_path"]).namelist()
    assert len(names) == len(set(names))
    assert "manufacturing.zip" not in names
    assert "stale-from-other-board.gbr" not in names
    assert sorted(names) == sorted(os.path.basename(f) for f in first["files"][:-1])
    assert second["files"][-1] == second["zip_path"]
