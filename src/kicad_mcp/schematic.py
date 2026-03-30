"""Schematic file (.kicad_sch) operations.

Provides functions to read, create, and modify KiCad 8 schematic files
by manipulating their S-expression representation.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .sexp_parser import (
    QuotedString,
    SexpList,
    escape_sexp_string as esc,
    parse,
    parse_file,
    write_file,
)
from .types import LabelInfo, PinInfo, Point, SchematicData, SymbolInstance, WireInfo


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _make_empty_schematic() -> SexpList:
    """Create a minimal empty schematic S-expression tree."""
    return parse(
        '(kicad_sch (version 20231120) (generator "kicad_mcp") '
        f'(generator_version "8.0") (uuid "{_new_uuid()}") '
        "(paper \"A4\") "
        "(lib_symbols) "
        "(symbol_instances))"
    )


def read_schematic(file_path: str) -> SchematicData:
    """Read and parse a .kicad_sch file into structured data."""
    root = parse_file(file_path)
    data = SchematicData()

    for sym in root.find_all("symbol"):
        lib_id_node = sym.find("lib_id")
        lib_id = str(lib_id_node.children[1]) if lib_id_node and len(lib_id_node.children) >= 2 else ""

        at_node = sym.find("at")
        x = float(at_node.children[1]) if at_node and len(at_node.children) >= 2 else 0.0
        y = float(at_node.children[2]) if at_node and len(at_node.children) >= 3 else 0.0
        rot = float(at_node.children[3]) if at_node and len(at_node.children) >= 4 else 0.0

        uuid_node = sym.find("uuid")
        sym_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""

        ref = value = footprint = ""
        for prop in sym.find_all("property"):
            if len(prop.children) >= 3:
                pname = str(prop.children[1])
                pval = str(prop.children[2])
                if pname == "Reference":
                    ref = pval
                elif pname == "Value":
                    value = pval
                elif pname == "Footprint":
                    footprint = pval

        pins: list[PinInfo] = []
        for pin in sym.find_all("pin"):
            if len(pin.children) >= 2:
                pin_uuid_node = pin.find("uuid")
                pin_uuid = str(pin_uuid_node.children[1]) if pin_uuid_node and len(pin_uuid_node.children) >= 2 else ""
                pins.append(PinInfo(number=str(pin.children[1]), uuid=pin_uuid))

        data.symbols.append(SymbolInstance(
            uuid=sym_uuid, lib_id=lib_id, reference=ref, value=value,
            footprint=footprint, position=Point(x, y), rotation=rot, pins=pins,
        ))

    for wire in root.find_all("wire"):
        pts_node = wire.find("pts")
        points: list[Point] = []
        if pts_node:
            for xy_node in pts_node.find_all("xy"):
                if len(xy_node.children) >= 3:
                    points.append(Point(float(xy_node.children[1]), float(xy_node.children[2])))
        uuid_node = wire.find("uuid")
        wire_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""
        data.wires.append(WireInfo(uuid=wire_uuid, points=points))

    for label_tag in ("label", "global_label"):
        for lbl in root.find_all(label_tag):
            if len(lbl.children) >= 2:
                name = str(lbl.children[1])
                at_node = lbl.find("at")
                x = float(at_node.children[1]) if at_node and len(at_node.children) >= 2 else 0.0
                y = float(at_node.children[2]) if at_node and len(at_node.children) >= 3 else 0.0
                rot = float(at_node.children[3]) if at_node and len(at_node.children) >= 4 else 0.0
                uuid_node = lbl.find("uuid")
                lbl_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""
                data.labels.append(LabelInfo(
                    uuid=lbl_uuid, name=name, position=Point(x, y),
                    rotation=rot, label_type=label_tag,
                ))

    return data


def _ensure_lib_symbol(root: SexpList, lib_id: str) -> None:
    """Ensure the lib_symbols block contains the definition for lib_id.

    If not present, attempt to load it from KiCad's installed libraries.
    Falls back to a minimal stub if the library file is not found.
    """
    lib_symbols = root.find("lib_symbols")
    if lib_symbols is None:
        lib_symbols = SexpList(["lib_symbols"])
        # Insert after header elements
        root.children.insert(5, lib_symbols)

    # Check if already present
    for child in lib_symbols.children:
        if isinstance(child, SexpList) and child.tag == "symbol":
            if len(child.children) >= 2 and str(child.children[1]) == lib_id:
                return

    # Try to load from installed KiCad libraries
    symbol_def = _load_lib_symbol_from_disk(lib_id)
    if symbol_def:
        lib_symbols.children.append(symbol_def)
        return

    # Fallback: create a minimal stub symbol definition
    parts = lib_id.split(":")
    symbol_name = parts[1] if len(parts) == 2 else lib_id

    stub = parse(
        f'(symbol "{esc(lib_id)}" (in_bom yes) (on_board yes) '
        f'(symbol "{esc(symbol_name)}_0_1"))'
    )
    lib_symbols.children.append(stub)


def _load_lib_symbol_from_disk(lib_id: str) -> SexpList | None:
    """Try to load a symbol definition from KiCad's installed library files."""
    import os
    symbol_dir = os.environ.get("KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols")

    parts = lib_id.split(":")
    if len(parts) != 2:
        return None

    lib_name, symbol_name = parts
    lib_file = Path(symbol_dir) / f"{lib_name}.kicad_sym"

    if not lib_file.exists():
        return None

    try:
        lib_root = parse_file(str(lib_file))
        for child in lib_root.children:
            if isinstance(child, SexpList) and child.tag == "symbol":
                if len(child.children) >= 2 and str(child.children[1]) == symbol_name:
                    # Re-tag with full lib_id for the schematic's lib_symbols block
                    child.children[1] = QuotedString(lib_id)
                    return child
    except Exception:
        pass
    return None


def place_symbol(
    file_path: str,
    lib_id: str,
    reference: str,
    value: str,
    footprint: str,
    x: float,
    y: float,
    rotation: float = 0,
) -> str:
    """Place a component symbol in the schematic. Returns the UUID."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    _ensure_lib_symbol(root, lib_id)

    sym_uuid = _new_uuid()
    pin1_uuid = _new_uuid()
    pin2_uuid = _new_uuid()

    sym_sexp = (
        f'(symbol (lib_id "{esc(lib_id)}") (at {x} {y} {rotation}) (unit 1) '
        f'(uuid "{sym_uuid}") '
        f'(property "Reference" "{esc(reference)}" (at {x} {y - 2} 0) '
        f'(effects (font (size 1.27 1.27)))) '
        f'(property "Value" "{esc(value)}" (at {x} {y + 2} 0) '
        f'(effects (font (size 1.27 1.27)))) '
        f'(property "Footprint" "{esc(footprint)}" (at {x} {y} 0) '
        f'(effects hide)) '
        f'(pin "1" (uuid "{pin1_uuid}")) '
        f'(pin "2" (uuid "{pin2_uuid}")))'
    )

    sym_node = parse(sym_sexp)

    # Insert before symbol_instances or at end
    si = root.find("symbol_instances")
    if si:
        idx = root.children.index(si)
        root.children.insert(idx, sym_node)
    else:
        root.children.append(sym_node)

    write_file(file_path, root)
    return sym_uuid


def add_wire(file_path: str, points: list[tuple[float, float]]) -> str:
    """Add a wire to the schematic. Returns the UUID of the last segment.

    KiCad wires are always 2-point segments, so a multi-point path is split
    into consecutive 2-point wires. Zero-length segments are rejected.
    """
    if len(points) < 2:
        raise ValueError("A wire requires at least 2 points.")

    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    si = root.find("symbol_instances")

    last_uuid = ""
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        if x1 == x2 and y1 == y2:
            continue  # skip zero-length segments

        wire_uuid = _new_uuid()
        last_uuid = wire_uuid
        wire_sexp = f'(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (uuid "{wire_uuid}"))'
        wire_node = parse(wire_sexp)

        if si:
            idx = root.children.index(si)
            root.children.insert(idx, wire_node)
        else:
            root.children.append(wire_node)

    if not last_uuid:
        raise ValueError("All wire segments were zero-length.")

    write_file(file_path, root)
    return last_uuid


def add_label(file_path: str, name: str, x: float, y: float, rotation: float = 0) -> str:
    """Add a net label to the schematic. Returns the UUID."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    lbl_uuid = _new_uuid()
    lbl_sexp = (
        f'(label "{esc(name)}" (at {x} {y} {rotation}) (uuid "{lbl_uuid}") '
        f'(effects (font (size 1.27 1.27))))'
    )
    lbl_node = parse(lbl_sexp)

    si = root.find("symbol_instances")
    if si:
        idx = root.children.index(si)
        root.children.insert(idx, lbl_node)
    else:
        root.children.append(lbl_node)

    write_file(file_path, root)
    return lbl_uuid


def add_global_label(file_path: str, name: str, x: float, y: float, rotation: float = 0) -> str:
    """Add a global label to the schematic. Returns the UUID."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    lbl_uuid = _new_uuid()
    lbl_sexp = (
        f'(global_label "{esc(name)}" (shape input) (at {x} {y} {rotation}) (uuid "{lbl_uuid}") '
        f'(effects (font (size 1.27 1.27))) '
        f'(property "Intersheetrefs" "{{{{1}}}}" (at {x} {y} 0) '
        f'(effects (font (size 1.27 1.27)) hide)))'
    )
    lbl_node = parse(lbl_sexp)

    si = root.find("symbol_instances")
    if si:
        idx = root.children.index(si)
        root.children.insert(idx, lbl_node)
    else:
        root.children.append(lbl_node)

    write_file(file_path, root)
    return lbl_uuid


def add_power_symbol(file_path: str, name: str, x: float, y: float, rotation: float = 0) -> str:
    """Add a power port symbol (GND, 3V3, 5V, etc.) to the schematic. Returns the UUID."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    power_lib_id = f"power:{name}"
    _ensure_lib_symbol(root, power_lib_id)

    sym_uuid = _new_uuid()
    pin_uuid = _new_uuid()

    sym_sexp = (
        f'(symbol (lib_id "{esc(power_lib_id)}") (at {x} {y} {rotation}) (unit 1) '
        f'(uuid "{sym_uuid}") '
        f'(property "Reference" "#{esc(name)}" (at {x} {y - 2} 0) '
        f'(effects (font (size 1.27 1.27)) hide)) '
        f'(property "Value" "{esc(name)}" (at {x} {y + 2} 0) '
        f'(effects (font (size 1.27 1.27)))) '
        f'(property "Footprint" "" (at {x} {y} 0) '
        f'(effects hide)) '
        f'(pin "1" (uuid "{pin_uuid}")))'
    )
    sym_node = parse(sym_sexp)

    si = root.find("symbol_instances")
    if si:
        idx = root.children.index(si)
        root.children.insert(idx, sym_node)
    else:
        root.children.append(sym_node)

    write_file(file_path, root)
    return sym_uuid


def delete_by_uuid(file_path: str, target_uuid: str) -> bool:
    """Delete any schematic element by UUID. Returns True if found and deleted."""
    root = parse_file(file_path)

    def _search_and_remove(parent: SexpList) -> bool:
        for child in list(parent.children):
            if isinstance(child, SexpList):
                uuid_node = child.find("uuid")
                if uuid_node and len(uuid_node.children) >= 2:
                    if str(uuid_node.children[1]) == target_uuid:
                        parent.remove_child(child)
                        return True
                if _search_and_remove(child):
                    return True
        return False

    found = _search_and_remove(root)
    if found:
        write_file(file_path, root)
    return found
