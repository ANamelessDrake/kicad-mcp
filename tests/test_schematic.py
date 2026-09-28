"""Tests for schematic operations."""

import os
import shutil
import tempfile

import pytest

from tests.conftest import needs_symbol_libs
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


@needs_symbol_libs
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


@needs_symbol_libs
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


# ── Regression tests for the review fixes ───────────────────────────────────

import re  # noqa: E402

from kicad_mcp import cli  # noqa: E402
from kicad_mcp.schematic import (  # noqa: E402
    _symbol_offset,
    add_lib_symbol,
    annotate,
    get_pin_positions,
    modify_lib_symbol_pin,
    move_symbol,
    set_symbol_property,
)
from kicad_mcp.sexp_parser import parse, parse_file, write_file  # noqa: E402
from tests.conftest import needs_kicad_cli  # noqa: E402

ASYM_PINS = [
    {"number": "1", "name": "A", "x": 2.54, "y": 5.08},
    {"number": "2", "name": "B", "x": -5.08, "y": 2.54},
    {"number": "3", "name": "C", "x": 7.62, "y": -2.54},
]


def _netlist_pairs(sch_path, tmp_dir):
    """(net name, pin) pairs for U1/R1 pins, from kicad-cli's netlist."""
    out = os.path.join(tmp_dir, "out.net")
    cli.export_netlist(sch_path, out)
    text = open(out).read()
    return re.findall(r'\(name "/([^"]+)"\)\s*\(node \(ref "[^"]+"\) \(pin "([^"]+)"', text)


class TestPinTransform:
    @needs_symbol_libs
    @pytest.mark.parametrize("rotation,pin1,pin2", [
        (0, (50.8, 46.99), (50.8, 54.61)),
        (90, (46.99, 50.8), (54.61, 50.8)),
        (180, (50.8, 54.61), (50.8, 46.99)),
        (270, (54.61, 50.8), (46.99, 50.8)),
    ])
    def test_resistor_pins_at_each_rotation(self, tmp_dir, rotation, pin1, pin2):
        path = os.path.join(tmp_dir, "r.kicad_sch")
        place_symbol(path, "Device:R", "R1", "1k", "", 50.8, 50.8, rotation)
        pins = {p["pin_number"]: (p["x"], p["y"]) for p in get_pin_positions(path, "R1")}
        assert pins["1"] == pin1
        assert pins["2"] == pin2

    def test_rotate_then_mirror(self):
        # Y flipped to (2.54, -5.08), rotated 90 CCW to (-5.08, -2.54),
        # then mirrored about X to (-5.08, 2.54).
        dx, dy = _symbol_offset(2.54, 5.08, 90, "x")
        assert (round(dx, 4), round(dy, 4)) == (-5.08, 2.54)

    @needs_kicad_cli
    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    @pytest.mark.parametrize("mirror", [None, "x", "y"])
    def test_matches_kicad_netlist(self, tmp_dir, rotation, mirror):
        path = os.path.join(tmp_dir, "m.kicad_sch")
        add_lib_symbol(path, "T:ASYM", ASYM_PINS)
        place_symbol(path, "T:ASYM", "U1", "x", "", 50.8, 50.8, rotation)
        if mirror:
            root = parse_file(path)
            root.find("symbol").children.insert(3, parse(f"(mirror {mirror})"))
            write_file(path, root)
        for p in get_pin_positions(path, "U1"):
            add_label(path, "N" + p["pin_number"], p["x"], p["y"])
        pairs = _netlist_pairs(path, tmp_dir)
        assert sorted(pairs) == [("N1", "1"), ("N2", "2"), ("N3", "3")]


@needs_symbol_libs
class TestPlacement:
    def test_unknown_symbol_raises(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        with pytest.raises(ValueError, match="not found"):
            place_symbol(path, "Device:NoSuchPart", "R1", "1k", "", 0, 0)

    def test_power_name_suggestion(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        with pytest.raises(ValueError, match=r"power:\+3V3"):
            add_power_symbol(path, "3V3", 0, 0)

    def test_pins_come_from_library(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        place_symbol(path, "Amplifier_Operational:LM358", "U1", "LM358", "", 0, 0, unit=3)
        sym = read_schematic(path).symbols[0]
        assert sym.unit == 3
        assert sorted(p.number for p in sym.pins) == ["4", "8"]

    def test_invalid_unit_raises(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        with pytest.raises(ValueError, match="Unit 9"):
            place_symbol(path, "Device:R", "R1", "1k", "", 0, 0, unit=9)

    def test_duplicate_reference_raises(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        place_symbol(path, "Device:R", "R1", "1k", "", 0, 0)
        with pytest.raises(ValueError, match="already placed"):
            place_symbol(path, "Device:R", "R1", "1k", "", 10, 0)

    def test_new_schematic_has_kicad8_trailer(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        place_symbol(path, "Device:R", "R1", "1k", "", 0, 0)
        root = parse_file(path)
        assert root.find("symbol_instances") is None
        assert root.children[-1].tag == "sheet_instances"

    def test_snap_can_be_disabled(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        add_label(path, "A", 10.16, 3.3, snap=False)
        add_label(path, "B", 10.16, 3.3)
        pos = {l.name: (l.position.x, l.position.y) for l in read_schematic(path).labels}
        assert pos["A"] == (10.16, 3.3)
        assert pos["B"] == (10.16, 3.81)


@needs_symbol_libs
class TestMultiUnit:
    def _place_units(self, path, ref="U?"):
        for unit, x in ((1, 50.8), (2, 101.6)):
            place_symbol(path, "Amplifier_Operational:LM358", ref, "LM358", "", x, 50.8, unit=unit)

    def test_annotate_packs_units(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        self._place_units(path)
        place_symbol(path, "Amplifier_Operational:LM358", "U?", "LM358", "", 0, 0, unit=1)
        assert annotate(path) == {"changes": {"U": ["U1", "U2"]}}
        refs = sorted((s.reference, s.unit) for s in read_schematic(path).symbols)
        assert refs == [("U1", 1), ("U1", 2), ("U2", 1)]

    def test_rename_reference_applies_to_all_units(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        self._place_units(path, "U1")
        result = set_symbol_property(path, "U1", "Reference", "U7")
        assert result["updated"] and result["units"] == [1, 2]
        assert {s.reference for s in read_schematic(path).symbols} == {"U7"}

    def test_missing_unit_is_an_error(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        self._place_units(path, "U1")
        result = set_symbol_property(path, "U1", "Value", "X", unit=7)
        assert result["updated"] is False
        assert result["units"] == [1, 2]
        assert {s.value for s in read_schematic(path).symbols} == {"LM358"}

    def test_pin_positions_cover_all_units(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        self._place_units(path, "U1")
        pins = get_pin_positions(path, "U1")
        assert {p["unit"] for p in pins} == {1, 2}
        assert len(get_pin_positions(path, "U1", unit=2)) == 3

    def test_move_requires_unit_when_ambiguous(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        self._place_units(path, "U1")
        with pytest.raises(ValueError, match="unit"):
            move_symbol(path, "U1", 0, 0)
        assert move_symbol(path, "U1", 0, 0, unit=2)


@needs_symbol_libs
class TestMoveSymbol:
    def test_fields_move_with_symbol(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        place_symbol(path, "Device:R", "R1", "1k", "", 50.8, 50.8)

        def field_positions():
            sym = parse_file(path).find("symbol")
            return {
                str(p.children[1]): (float(p.find("at").children[1]), float(p.find("at").children[2]))
                for p in sym.find_all("property")
            }

        before = field_positions()
        assert move_symbol(path, "R1", 76.2, 63.5)
        after = field_positions()
        for name, (x, y) in before.items():
            assert after[name] == (round(x + 25.4, 4), round(y + 12.7, 4))


class TestPinValidation:
    def test_add_lib_symbol_rejects_bad_type(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        with pytest.raises(ValueError, match="Invalid pin type"):
            add_lib_symbol(path, "T:X", [{"number": "1", "type": "input) (evil"}])

    def test_modify_rejects_bad_type(self, sample_sch):
        with pytest.raises(ValueError, match="Invalid pin type"):
            modify_lib_symbol_pin(sample_sch, "Device:R", "1", "bogus")

    def test_add_lib_symbol_reports_existing(self, tmp_dir):
        path = os.path.join(tmp_dir, "x.kicad_sch")
        assert add_lib_symbol(path, "T:X", ASYM_PINS) is True
        assert add_lib_symbol(path, "T:X", ASYM_PINS) is False
