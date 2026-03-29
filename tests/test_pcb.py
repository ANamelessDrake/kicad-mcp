"""Tests for PCB operations."""

import os
import shutil
import tempfile

import pytest

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


class TestMountingHole:
    def test_add_hole(self, sample_pcb):
        uuid = add_mounting_hole(sample_pcb, 5, 5)
        assert uuid
        data = read_pcb(sample_pcb)
        assert len(data.footprints) == 2


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
