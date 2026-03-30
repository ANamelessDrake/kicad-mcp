"""Tests for schematic operations."""

import os
import shutil
import tempfile

import pytest

from kicad_mcp.schematic import (
    add_global_label,
    add_label,
    add_power_symbol,
    add_wire,
    delete_by_uuid,
    place_symbol,
    read_schematic,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def sample_sch(tmp_dir):
    src = os.path.join(FIXTURES_DIR, "simple.kicad_sch")
    dst = os.path.join(tmp_dir, "test.kicad_sch")
    shutil.copy2(src, dst)
    return dst


class TestReadSchematic:
    def test_read_fixture(self, sample_sch):
        data = read_schematic(sample_sch)
        assert len(data.symbols) == 1
        assert data.symbols[0].reference == "R1"
        assert data.symbols[0].value == "10K"
        assert data.symbols[0].lib_id == "Device:R"
        assert data.symbols[0].position.x == 100
        assert data.symbols[0].position.y == 50

    def test_read_wires(self, sample_sch):
        data = read_schematic(sample_sch)
        assert len(data.wires) == 1
        assert len(data.wires[0].points) == 2

    def test_read_labels(self, sample_sch):
        data = read_schematic(sample_sch)
        assert len(data.labels) == 1
        assert data.labels[0].name == "3V3"


class TestPlaceSymbol:
    def test_place_new(self, tmp_dir):
        path = os.path.join(tmp_dir, "new.kicad_sch")
        uuid = place_symbol(path, "Device:R", "R1", "4.7K", "Resistor_SMD:R_0402_1005Metric", 80, 60)
        assert uuid
        data = read_schematic(path)
        assert len(data.symbols) == 1
        assert data.symbols[0].reference == "R1"
        assert data.symbols[0].value == "4.7K"

    def test_place_in_existing(self, sample_sch):
        uuid = place_symbol(sample_sch, "Device:C", "C1", "100nF", "Capacitor_SMD:C_0603_1608Metric", 120, 50)
        assert uuid
        data = read_schematic(sample_sch)
        assert len(data.symbols) == 2
        refs = {s.reference for s in data.symbols}
        assert "R1" in refs
        assert "C1" in refs


class TestAddWire:
    def test_add_wire(self, sample_sch):
        uuid = add_wire(sample_sch, [(80, 50), (100, 50)])
        assert uuid
        data = read_schematic(sample_sch)
        assert len(data.wires) == 2

    def test_multi_point_splits_into_segments(self, sample_sch):
        uuid = add_wire(sample_sch, [(10, 10), (20, 10), (20, 20)])
        assert uuid
        data = read_schematic(sample_sch)
        # 1 original + 2 new segments
        assert len(data.wires) == 3

    def test_zero_length_skipped(self, sample_sch):
        uuid = add_wire(sample_sch, [(10, 10), (10, 10), (20, 10)])
        assert uuid
        data = read_schematic(sample_sch)
        # 1 original + 1 new (the zero-length one was skipped)
        assert len(data.wires) == 2

    def test_all_zero_length_raises(self, sample_sch):
        with pytest.raises(ValueError, match="zero-length"):
            add_wire(sample_sch, [(10, 10), (10, 10)])

    def test_too_few_points_raises(self, sample_sch):
        with pytest.raises(ValueError, match="at least 2"):
            add_wire(sample_sch, [(10, 10)])


class TestAddLabel:
    def test_add_label(self, sample_sch):
        uuid = add_label(sample_sch, "SDA", 80, 50)
        assert uuid
        data = read_schematic(sample_sch)
        assert len(data.labels) == 2
        names = {l.name for l in data.labels}
        assert "SDA" in names

    def test_add_global_label(self, sample_sch):
        uuid = add_global_label(sample_sch, "MOSI", 90, 60)
        assert uuid
        data = read_schematic(sample_sch)
        global_labels = [l for l in data.labels if l.label_type == "global_label"]
        assert len(global_labels) == 1
        assert global_labels[0].name == "MOSI"


class TestAddPowerSymbol:
    def test_add_power(self, sample_sch):
        uuid = add_power_symbol(sample_sch, "GND", 100, 60)
        assert uuid
        data = read_schematic(sample_sch)
        # Power symbols are placed as symbols, not labels
        assert len(data.symbols) == 2


class TestDelete:
    def test_delete_symbol(self, sample_sch):
        data = read_schematic(sample_sch)
        sym_uuid = data.symbols[0].uuid
        assert delete_by_uuid(sample_sch, sym_uuid)
        data2 = read_schematic(sample_sch)
        assert len(data2.symbols) == 0

    def test_delete_wire(self, sample_sch):
        data = read_schematic(sample_sch)
        wire_uuid = data.wires[0].uuid
        assert delete_by_uuid(sample_sch, wire_uuid)
        data2 = read_schematic(sample_sch)
        assert len(data2.wires) == 0

    def test_delete_nonexistent(self, sample_sch):
        assert not delete_by_uuid(sample_sch, "00000000-0000-0000-0000-000000000000")
