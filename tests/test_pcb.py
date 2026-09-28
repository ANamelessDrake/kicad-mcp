"""Tests for PCB operations."""

import os
import re
import shutil
import tempfile

import pytest

from tests.conftest import needs_footprint_libs, needs_kicad_cli, needs_symbol_libs
from kicad_mcp.pcb import (
    add_mounting_hole,
    add_trace,
    add_via,
    add_zone,
    assign_net_to_pad,
    delete_by_uuid,
    move_footprint,
    place_footprint,
    place_footprint_array,
    read_pcb,
    set_board_outline,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def sample_pcb(tmp_dir):
    src = os.path.join(FIXTURES_DIR, "simple.kicad_pcb")
    dst = os.path.join(tmp_dir, "test.kicad_pcb")
    shutil.copy2(src, dst)
    return dst


class TestReadPCB:
    def test_read_fixture(self, sample_pcb):
        data = read_pcb(sample_pcb)
        assert len(data.nets) >= 3  # net 0, GND, 3V3
        assert len(data.footprints) == 1
        assert data.footprints[0].reference == "R1"
        assert data.footprints[0].value == "10K"

    def test_read_traces(self, sample_pcb):
        data = read_pcb(sample_pcb)
        assert len(data.traces) == 1
        assert data.traces[0].width == 0.25

    def test_read_vias(self, sample_pcb):
        data = read_pcb(sample_pcb)
        assert len(data.vias) == 1
        assert data.vias[0].size == 0.8

    def test_read_zones(self, sample_pcb):
        data = read_pcb(sample_pcb)
        assert len(data.zones) == 1
        assert data.zones[0].net_name == "GND"
        assert len(data.zones[0].outline) == 4

    def test_read_board_outline(self, sample_pcb):
        data = read_pcb(sample_pcb)
        assert len(data.board_outline) == 4

    def test_read_pads(self, sample_pcb):
        data = read_pcb(sample_pcb)
        fp = data.footprints[0]
        assert len(fp.pads) == 2
        pad_nets = {p.number: p.net_name for p in fp.pads}
        assert pad_nets["1"] == "3V3"
        assert pad_nets["2"] == "GND"


@needs_footprint_libs
class TestPlaceFootprint:
    def test_place_new(self, tmp_dir):
        path = os.path.join(tmp_dir, "new.kicad_pcb")
        uuid = place_footprint(path, "Resistor_SMD:R_0603_1608Metric", "R1", "10K", 25, 30)
        assert uuid
        data = read_pcb(path)
        assert len(data.footprints) == 1
        assert data.footprints[0].reference == "R1"

    def test_place_in_existing(self, sample_pcb):
        uuid = place_footprint(sample_pcb, "Capacitor_SMD:C_0603_1608Metric", "C1", "100nF", 35, 30)
        assert uuid
        data = read_pcb(sample_pcb)
        assert len(data.footprints) == 2


class TestMoveFootprint:
    def test_move(self, sample_pcb):
        ok = move_footprint(sample_pcb, "R1", 50, 50)
        assert ok
        data = read_pcb(sample_pcb)
        assert data.footprints[0].position.x == 50
        assert data.footprints[0].position.y == 50

    def test_move_nonexistent(self, sample_pcb):
        ok = move_footprint(sample_pcb, "R99", 50, 50)
        assert not ok


class TestAddTrace:
    def test_add_trace(self, sample_pcb):
        uuid = add_trace(sample_pcb, "3V3", "F.Cu", 0.25, [(10, 10), (20, 10), (20, 20)])
        assert uuid
        data = read_pcb(sample_pcb)
        assert len(data.traces) == 3  # 1 original + 2 new segments


class TestAddVia:
    def test_add_via(self, sample_pcb):
        uuid = add_via(sample_pcb, "3V3", 15, 15)
        assert uuid
        data = read_pcb(sample_pcb)
        assert len(data.vias) == 2


class TestAddZone:
    def test_add_zone(self, sample_pcb):
        uuid = add_zone(sample_pcb, "3V3", "F.Cu", [(0, 0), (35, 0), (35, 55), (0, 55)])
        assert uuid
        data = read_pcb(sample_pcb)
        assert len(data.zones) == 2


class TestBoardOutline:
    def test_set_outline(self, sample_pcb):
        ok = set_board_outline(sample_pcb, [(0, 0), (50, 0), (50, 50), (0, 50)])
        assert ok
        data = read_pcb(sample_pcb)
        assert len(data.board_outline) >= 4


class TestAssignNet:
    def test_assign_net(self, sample_pcb):
        ok = assign_net_to_pad(sample_pcb, "R1", "1", "GND")
        assert ok
        data = read_pcb(sample_pcb)
        pad1 = next(p for p in data.footprints[0].pads if p.number == "1")
        assert pad1.net_name == "GND"


@needs_footprint_libs
class TestMountingHole:
    def test_add_hole(self, sample_pcb):
        uuid = add_mounting_hole(sample_pcb, 5, 5)
        assert uuid
        data = read_pcb(sample_pcb)
        assert len(data.footprints) == 2


@needs_footprint_libs
class TestPlaceFootprintArray:
    def test_grid_array(self, tmp_dir):
        path = os.path.join(tmp_dir, "array.kicad_pcb")
        uuids = place_footprint_array(
            path, "Resistor_SMD:R_0603_1608Metric", "R", "10K",
            count=6, pattern="grid", start_x=10, start_y=10,
            spacing_x=5, spacing_y=5, columns=3,
        )
        assert len(uuids) == 6
        data = read_pcb(path)
        assert len(data.footprints) == 6
        refs = sorted(fp.reference for fp in data.footprints)
        assert refs == ["R1", "R2", "R3", "R4", "R5", "R6"]

    def test_grid_positions(self, tmp_dir):
        path = os.path.join(tmp_dir, "grid.kicad_pcb")
        place_footprint_array(
            path, "Resistor_SMD:R_0603_1608Metric", "R", "10K",
            count=4, pattern="grid", start_x=10, start_y=20,
            spacing_x=5, spacing_y=10, columns=2,
        )
        data = read_pcb(path)
        positions = {fp.reference: (fp.position.x, fp.position.y) for fp in data.footprints}
        assert positions["R1"] == (10.0, 20.0)
        assert positions["R2"] == (15.0, 20.0)
        assert positions["R3"] == (10.0, 30.0)
        assert positions["R4"] == (15.0, 30.0)

    def test_circular_array(self, tmp_dir):
        path = os.path.join(tmp_dir, "circle.kicad_pcb")
        uuids = place_footprint_array(
            path, "Resistor_SMD:R_0603_1608Metric", "R", "10K",
            count=4, pattern="circular", start_x=50, start_y=50, radius=20,
        )
        assert len(uuids) == 4
        data = read_pcb(path)
        assert len(data.footprints) == 4

    def test_invalid_pattern(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.kicad_pcb")
        with pytest.raises(ValueError, match="Unknown pattern"):
            place_footprint_array(path, "X:Y", "R", "1K", count=1, pattern="spiral")

    def test_start_index(self, tmp_dir):
        path = os.path.join(tmp_dir, "idx.kicad_pcb")
        place_footprint_array(
            path, "Resistor_SMD:R_0603_1608Metric", "R", "10K",
            count=3, start_index=5,
        )
        data = read_pcb(path)
        refs = sorted(fp.reference for fp in data.footprints)
        assert refs == ["R5", "R6", "R7"]


class TestDelete:
    def test_delete_trace(self, sample_pcb):
        data = read_pcb(sample_pcb)
        trace_uuid = data.traces[0].uuid
        assert delete_by_uuid(sample_pcb, trace_uuid)
        data2 = read_pcb(sample_pcb)
        assert len(data2.traces) == 0


# ── Regression tests for the review fixes ───────────────────────────────────

from kicad_mcp.pcb import (  # noqa: E402
    autoroute,
    delete_footprints_batch,
    flip_footprint,
)
from kicad_mcp.sexp_parser import parse_file  # noqa: E402

SOT23 = "Package_TO_SOT_SMD:SOT-23"


def _fp_node(path, ref):
    for fp in parse_file(path).find_all("footprint"):
        for prop in fp.find_all("property"):
            if str(prop.children[1]) == "Reference" and str(prop.children[2]) == ref:
                return fp
    raise AssertionError(ref)


def _pad(fp, number):
    pad = next(p for p in fp.find_all("pad") if str(p.children[1]) == number)
    at = [float(v) for v in pad.find("at").children[1:]]
    return at + [0.0] * (3 - len(at)), [str(v) for v in pad.find("layers").children[1:]]


def _ref_text(fp):
    prop = next(p for p in fp.find_all("property") if str(p.children[1]) == "Reference")
    justify = prop.find("effects").find("justify")
    mirrored = justify is not None and "mirror" in [str(c) for c in justify.children]
    return [float(v) for v in prop.find("at").children[1:]], mirrored


@needs_footprint_libs
class TestFootprintGeometry:
    """Expected values were produced by pcbnew (KiCad 8.0.9) for the same operations."""

    def test_move_keeps_rotation(self, tmp_dir):
        path = os.path.join(tmp_dir, "b.kicad_pcb")
        place_footprint(path, SOT23, "Q1", "x", 50, 50, 30)
        move_footprint(path, "Q1", 60, 40)
        fp = read_pcb(path).footprints[0]
        assert (fp.position.x, fp.position.y, fp.rotation) == (60, 40, 30)

    def test_rotation_updates_pad_and_text_angles(self, tmp_dir):
        path = os.path.join(tmp_dir, "b.kicad_pcb")
        place_footprint(path, SOT23, "Q1", "x", 50, 50)
        move_footprint(path, "Q1", 50, 50, 200)
        fp = _fp_node(path, "Q1")
        assert _pad(fp, "1")[0] == [-0.9375, -0.95, 200]
        assert _ref_text(fp)[0] == [0, -2.4, -160]

    def test_absolute_pad_positions_account_for_rotation(self, tmp_dir):
        path = os.path.join(tmp_dir, "b.kicad_pcb")
        place_footprint(path, SOT23, "Q1", "x", 50, 50, 30)
        pad1 = next(p for p in read_pcb(path).footprints[0].pads if p.number == "1")
        assert (round(pad1.absolute_position.x, 4), round(pad1.absolute_position.y, 4)) == (48.7131, 49.646)

    def test_place_on_back_mirrors_and_moves_pads(self, tmp_dir):
        path = os.path.join(tmp_dir, "b.kicad_pcb")
        place_footprint(path, SOT23, "Q1", "x", 50, 50, 0, "B.Cu")
        fp = _fp_node(path, "Q1")
        at, layers = _pad(fp, "1")
        assert at[:2] == [-0.9375, 0.95]
        assert layers == ["B.Cu", "B.Paste", "B.Mask"]
        pad1 = next(p for p in read_pcb(path).footprints[0].pads if p.number == "1")
        assert (pad1.absolute_position.x, pad1.absolute_position.y) == (49.0625, 50.95)

    def test_flip_rotated_footprint(self, tmp_dir):
        path = os.path.join(tmp_dir, "b.kicad_pcb")
        place_footprint(path, SOT23, "Q1", "x", 50, 50, 30)
        assert flip_footprint(path, "Q1", "B.Cu")
        fp = _fp_node(path, "Q1")
        assert read_pcb(path).footprints[0].rotation == 150
        assert _pad(fp, "1") == ([-0.9375, 0.95, 150], ["B.Cu", "B.Paste", "B.Mask"])
        assert _ref_text(fp) == ([0, 2.4, 150], True)

    def test_flip_twice_restores_front(self, tmp_dir):
        path = os.path.join(tmp_dir, "b.kicad_pcb")
        place_footprint(path, SOT23, "Q1", "x", 50, 50, 30)
        original = parse_file(path)
        flip_footprint(path, "Q1", "B.Cu")
        flip_footprint(path, "Q1", "F.Cu")
        restored = _fp_node(path, "Q1")
        orig_fp = original.find("footprint")
        assert _pad(restored, "1") == _pad(orig_fp, "1")
        assert _ref_text(restored) == _ref_text(orig_fp)

    def test_unknown_footprint_raises(self, tmp_dir):
        with pytest.raises(ValueError, match="not found"):
            place_footprint(os.path.join(tmp_dir, "b.kicad_pcb"), "Nope:Nothing", "R1", "x", 0, 0)

    def test_duplicate_reference_raises(self, sample_pcb):
        with pytest.raises(ValueError, match="already exists"):
            place_footprint(sample_pcb, "Resistor_SMD:R_0603_1608Metric", "R1", "x", 0, 0)


class TestBatchDelete:
    @needs_footprint_libs
    def test_results_per_request(self, sample_pcb):
        u = place_footprint(sample_pcb, "Resistor_SMD:R_0603_1608Metric", "R2", "x", 30, 10)
        results = delete_footprints_batch(sample_pcb, references=["R1", "R9"], uuids=[u, "missing"])
        assert results == [
            {"deleted": True, "reference": "R1"},
            {"deleted": False, "reference": "R9"},
            {"deleted": True, "uuid": u, "reference": "R2"},
            {"deleted": False, "uuid": "missing"},
        ]
        assert read_pcb(sample_pcb).footprints == []


class TestMountingHoles:
    def test_unique_references(self, sample_pcb):
        add_mounting_hole(sample_pcb, 5, 5)
        add_mounting_hole(sample_pcb, 30, 50)
        refs = sorted(f.reference for f in read_pcb(sample_pcb).footprints if f.reference.startswith("H"))
        assert refs == ["H1", "H2"]

    @needs_footprint_libs
    def test_uses_library_footprint(self, sample_pcb):
        add_mounting_hole(sample_pcb, 5, 5)
        hole = next(f for f in read_pcb(sample_pcb).footprints if f.reference == "H1")
        assert hole.footprint_lib == "MountingHole:MountingHole_3.2mm_M3_Pad"

    def test_custom_size_is_generated(self, sample_pcb):
        add_mounting_hole(sample_pcb, 5, 5, drill_size=2.7, pad_size=5.1)
        fp = _fp_node(sample_pcb, "H1")
        pad = fp.find("pad")
        assert [float(v) for v in pad.find("size").children[1:]] == [5.1, 5.1]
        assert float(pad.find("drill").children[1]) == 2.7


class TestZones:
    def test_hatch_fill(self, sample_pcb):
        uuid = add_zone(sample_pcb, "GND", "F.Cu", [(0, 0), (10, 0), (10, 10)], "hatch")
        zone = next(z for z in parse_file(sample_pcb).find_all("zone")
                    if str(z.find("uuid").children[1]) == uuid)
        assert str(zone.find("fill").find("mode").children[1]) == "hatch"

    def test_invalid_fill_type_raises(self, sample_pcb):
        with pytest.raises(ValueError, match="fill_type"):
            add_zone(sample_pcb, "GND", "F.Cu", [(0, 0), (10, 0), (10, 10)], "checkerboard")


class TestOutline:
    def test_lines_are_chained_in_order(self, tmp_dir):
        path = os.path.join(tmp_dir, "b.kicad_pcb")
        set_board_outline(path, [(0, 0), (40, 0), (40, 30), (0, 30)])
        # Shuffle the line order and reverse one line; the outline must still chain
        root = parse_file(path)
        lines = [c for c in root.children if getattr(c, "tag", None) == "gr_line"]
        for line in lines:
            root.remove_child(line)
        start, end = lines[1].find("start"), lines[1].find("end")
        start.children[1:], end.children[1:] = end.children[1:], start.children[1:]
        for line in (lines[2], lines[0], lines[3], lines[1]):
            root.append(line)
        from kicad_mcp.sexp_parser import write_file
        write_file(path, root)
        outline = [(p.x, p.y) for p in read_pcb(path).board_outline]
        assert len(outline) == 4
        assert set(outline) == {(0, 0), (40, 0), (40, 30), (0, 30)}
        # consecutive points share an edge (axis-aligned rectangle)
        for (x1, y1), (x2, y2) in zip(outline, outline[1:] + outline[:1]):
            assert x1 == x2 or y1 == y2


@needs_footprint_libs
class TestSimpleAutoroute:
    def test_idempotent_and_uses_rotated_positions(self, tmp_dir):
        path = os.path.join(tmp_dir, "b.kicad_pcb")
        place_footprint(path, "Resistor_SMD:R_0603_1608Metric", "R1", "x", 10, 10, 90)
        place_footprint(path, "Resistor_SMD:R_0603_1608Metric", "R2", "x", 20, 10)
        assign_net_to_pad(path, "R1", "1", "SIG")
        assign_net_to_pad(path, "R2", "1", "SIG")
        first = autoroute(path, strategy="simple")
        assert first["traces_added"] >= 1
        pads = {f.reference: next(p for p in f.pads if p.number == "1").absolute_position
                for f in read_pcb(path).footprints}
        ends = {(round(t.start.x, 4), round(t.start.y, 4)) for t in read_pcb(path).traces}
        ends |= {(round(t.end.x, 4), round(t.end.y, 4)) for t in read_pcb(path).traces}
        assert (round(pads["R1"].x, 4), round(pads["R1"].y, 4)) in ends
        second = autoroute(path, strategy="simple")
        assert (second["traces_added"], second["vias_added"]) == (0, 0)

    def test_unknown_strategy_raises(self, sample_pcb):
        with pytest.raises(ValueError, match="strategy"):
            autoroute(sample_pcb, strategy="magic")


@needs_footprint_libs
@needs_symbol_libs
class TestSyncFromSchematic:
    @pytest.fixture
    def project(self, tmp_dir):
        from kicad_mcp import schematic

        sch = os.path.join(tmp_dir, "p.kicad_sch")
        schematic.place_symbol(sch, "Device:R", "R1", "1k", "Resistor_SMD:R_0603_1608Metric", 50.8, 50.8)
        schematic.place_symbol(sch, "Device:R", "R2", "1k", "", 76.2, 50.8)
        pins = {p["pin_number"]: p for p in schematic.get_pin_positions(sch, "R1")}
        schematic.add_label(sch, "SIG", pins["1"]["x"], pins["1"]["y"])
        schematic.add_power_symbol(sch, "GND", pins["2"]["x"], pins["2"]["y"])
        return sch, os.path.join(tmp_dir, "p.kicad_pcb")

    @needs_kicad_cli
    def test_sync_reports_only_new_nets(self, project):
        from kicad_mcp.pcb import sync_from_schematic

        sch, board = project
        first = sync_from_schematic(sch, board)
        assert first["netlist_source"] == "kicad-cli"
        assert "GND" in first["added_nets"] and "/SIG" in first["added_nets"]
        assert first["added_footprints"] == ["R1"]
        assert first["skipped_footprints"] == [{"ref": "R2", "reason": "no footprint assigned"}]
        assert first["updated_pads"] == 2
        second = sync_from_schematic(sch, board)
        assert second["added_nets"] == [] and second["added_footprints"] == []

    def test_fallback_is_reported(self, project, monkeypatch):
        from kicad_mcp import cli
        from kicad_mcp.pcb import sync_from_schematic

        def fail(*_a, **_k):
            raise RuntimeError("kicad-cli not found.")

        monkeypatch.setattr(cli, "export_netlist", fail)
        sch, board = project
        result = sync_from_schematic(sch, board)
        assert result["netlist_source"] == "schematic"
        assert result["updated_pads"] == 0
        assert "pad nets were not updated" in result["warnings"][0]
        assert result["added_footprints"] == ["R1"]


class TestProjectFootprintLibrary:
    def _make_project_lib(self, tmp_dir):
        lib = os.path.join(tmp_dir, "proj.pretty")
        os.makedirs(lib)
        with open(os.path.join(lib, "Pads2.kicad_mod"), "w") as f:
            f.write(
                '(footprint "Pads2" (version 20240108) (generator "pcbnew") (layer "F.Cu")\n'
                '  (property "Reference" "REF**" (at 0 -2 0) (layer "F.SilkS"))\n'
                '  (property "Value" "Pads2" (at 0 2 0) (layer "F.Fab"))\n'
                '  (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")'
                ' (uuid "11111111-1111-1111-1111-111111111111"))\n'
                '  (pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")'
                ' (uuid "22222222-2222-2222-2222-222222222222"))\n'
                ')\n'
            )
        with open(os.path.join(tmp_dir, "fp-lib-table"), "w") as f:
            f.write(
                '(fp_lib_table\n  (version 7)\n'
                '  (lib (name "proj")(type "KiCad")(uri "${KIPRJMOD}/proj.pretty")(options "")(descr ""))\n)\n'
            )

    def test_place_from_project_library(self, sample_pcb, tmp_dir):
        self._make_project_lib(tmp_dir)
        place_footprint(sample_pcb, "proj:Pads2", "J1", "pads", 10, 10)
        fp = next(f for f in read_pcb(sample_pcb).footprints if f.reference == "J1")
        assert sorted(p.number for p in fp.pads) == ["1", "2"]

    def test_copies_get_distinct_pad_uuids(self, sample_pcb, tmp_dir):
        self._make_project_lib(tmp_dir)
        place_footprint_array(sample_pcb, "proj:Pads2", "J", "pads", count=2)
        with open(sample_pcb) as f:
            text = f.read()
        assert "11111111-1111-1111-1111-111111111111" not in text
        uuids = re.findall(r'\(uuid "([^"]+)"\)', text)
        assert len(uuids) == len(set(uuids))

    def test_unknown_project_library_still_raises(self, sample_pcb, tmp_dir):
        self._make_project_lib(tmp_dir)
        with pytest.raises(ValueError):
            place_footprint(sample_pcb, "proj:Missing", "J1", "x", 0, 0)
