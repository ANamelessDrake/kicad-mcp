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


def _snap_to_grid(value: float, grid: float = 1.27) -> float:
    """Snap a coordinate to the nearest KiCad grid point.

    KiCad requires exact coordinate matches for connections. The default
    schematic grid is 1.27mm (50 mil).
    """
    return round(value / grid) * grid


def _get_schematic_uuid(root: SexpList) -> str:
    """Get the root UUID of the schematic."""
    uuid_node = root.find("uuid")
    if uuid_node and len(uuid_node.children) >= 2:
        return str(uuid_node.children[1])
    return ""


def _get_project_name(file_path: str) -> str:
    """Derive the project name from the schematic file path."""
    return Path(file_path).stem


def _insert_before_symbol_instances(root: SexpList, node: SexpList) -> None:
    """Insert a node before symbol_instances, or at end if not present."""
    si = root.find("symbol_instances")
    if si:
        idx = root.children.index(si)
        root.children.insert(idx, node)
    else:
        root.children.append(node)


def _make_instances_block(
    project_name: str, schematic_uuid: str, reference: str, unit: int = 1
) -> str:
    """Build the (instances ...) S-expression that KiCad 8 requires on symbols."""
    return (
        f'(instances (project "{esc(project_name)}" '
        f'(path "/{esc(schematic_uuid)}" '
        f'(reference "{esc(reference)}") (unit {unit}))))'
    )


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
    x = _snap_to_grid(x)
    y = _snap_to_grid(y)

    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    _ensure_lib_symbol(root, lib_id)

    project = _get_project_name(file_path)
    sch_uuid = _get_schematic_uuid(root)
    sym_uuid = _new_uuid()
    pin1_uuid = _new_uuid()
    pin2_uuid = _new_uuid()

    instances = _make_instances_block(project, sch_uuid, reference)
    sym_sexp = (
        f'(symbol (lib_id "{esc(lib_id)}") (at {x} {y} {rotation}) (unit 1) '
        f'(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) '
        f'(uuid "{sym_uuid}") '
        f'(property "Reference" "{esc(reference)}" (at {x} {y - 2} 0) '
        f'(effects (font (size 1.27 1.27)))) '
        f'(property "Value" "{esc(value)}" (at {x} {y + 2} 0) '
        f'(effects (font (size 1.27 1.27)))) '
        f'(property "Footprint" "{esc(footprint)}" (at {x} {y} 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes))) '
        f'(property "Datasheet" "" (at {x} {y} 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes))) '
        f'(pin "1" (uuid "{pin1_uuid}")) '
        f'(pin "2" (uuid "{pin2_uuid}")) '
        f'{instances})'
    )

    sym_node = parse(sym_sexp)
    _insert_before_symbol_instances(root, sym_node)

    write_file(file_path, root)
    return sym_uuid


def add_wire(file_path: str, points: list[tuple[float, float]]) -> str:
    """Add a wire to the schematic. Returns the UUID of the last segment.

    KiCad wires are always 2-point segments, so a multi-point path is split
    into consecutive 2-point wires. Zero-length segments are rejected.
    """
    if len(points) < 2:
        raise ValueError("A wire requires at least 2 points.")

    # Snap all points to grid
    points = [(_snap_to_grid(x), _snap_to_grid(y)) for x, y in points]

    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

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
        _insert_before_symbol_instances(root, wire_node)

    if not last_uuid:
        raise ValueError("All wire segments were zero-length.")

    write_file(file_path, root)
    return last_uuid


def add_label(file_path: str, name: str, x: float, y: float, rotation: float = 0) -> str:
    """Add a net label to the schematic. Returns the UUID."""
    x = _snap_to_grid(x)
    y = _snap_to_grid(y)
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
    _insert_before_symbol_instances(root, lbl_node)

    write_file(file_path, root)
    return lbl_uuid


def add_global_label(file_path: str, name: str, x: float, y: float, rotation: float = 0) -> str:
    """Add a global label to the schematic. Returns the UUID."""
    x = _snap_to_grid(x)
    y = _snap_to_grid(y)

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
    _insert_before_symbol_instances(root, lbl_node)

    write_file(file_path, root)
    return lbl_uuid


def add_power_symbol(file_path: str, name: str, x: float, y: float, rotation: float = 0) -> str:
    """Add a power port symbol (GND, 3V3, 5V, etc.) to the schematic. Returns the UUID."""
    x = _snap_to_grid(x)
    y = _snap_to_grid(y)
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    power_lib_id = f"power:{name}"
    _ensure_lib_symbol(root, power_lib_id)

    project = _get_project_name(file_path)
    sch_uuid = _get_schematic_uuid(root)
    sym_uuid = _new_uuid()
    pin_uuid = _new_uuid()
    ref = f"#PWR?"

    instances = _make_instances_block(project, sch_uuid, ref)
    sym_sexp = (
        f'(symbol (lib_id "{esc(power_lib_id)}") (at {x} {y} {rotation}) (unit 1) '
        f'(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) '
        f'(uuid "{sym_uuid}") '
        f'(property "Reference" "{esc(ref)}" (at {x} {y - 2} 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes))) '
        f'(property "Value" "{esc(name)}" (at {x} {y + 2} 0) '
        f'(effects (font (size 1.27 1.27)))) '
        f'(property "Footprint" "" (at {x} {y} 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes))) '
        f'(property "Datasheet" "" (at {x} {y} 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes))) '
        f'(property "Description" "Power symbol creates a global label with name \\"{esc(name)}\\"" '
        f'(at {x} {y} 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes))) '
        f'(pin "1" (uuid "{pin_uuid}")) '
        f'{instances})'
    )
    sym_node = parse(sym_sexp)
    _insert_before_symbol_instances(root, sym_node)

    write_file(file_path, root)
    return sym_uuid


def add_no_connect(file_path: str, x: float, y: float) -> str:
    """Add a no-connect flag at a pin position. Returns the UUID."""
    x = _snap_to_grid(x)
    y = _snap_to_grid(y)
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    nc_uuid = _new_uuid()
    nc_sexp = f'(no_connect (at {x} {y}) (uuid "{nc_uuid}"))'
    nc_node = parse(nc_sexp)
    _insert_before_symbol_instances(root, nc_node)

    write_file(file_path, root)
    return nc_uuid


def add_no_connects_batch(file_path: str, positions: list[dict]) -> list[str]:
    """Add multiple no-connect flags in a single file read/write cycle.

    Each dict should have: x (float), y (float).
    Returns list of UUIDs.
    """
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    uuids: list[str] = []
    for pos in positions:
        x = _snap_to_grid(pos["x"])
        y = _snap_to_grid(pos["y"])
        nc_uuid = _new_uuid()
        uuids.append(nc_uuid)
        nc_sexp = f'(no_connect (at {x} {y}) (uuid "{nc_uuid}"))'
        nc_node = parse(nc_sexp)
        _insert_before_symbol_instances(root, nc_node)

    write_file(file_path, root)
    return uuids


# ── Pin position lookup ─────────────────────────────────────────────────────


def _transform_pin_to_schematic(
    comp_x: float, comp_y: float, comp_rot: float,
    pin_x: float, pin_y: float,
) -> tuple[float, float]:
    """Transform a pin position from symbol coordinates to schematic coordinates.

    KiCad symbol coordinates have Y-up, but schematic coordinates have Y-down.
    The component rotation is applied as:
      0°:   screen = (comp_x + pin_x, comp_y - pin_y)
      90°:  screen = (comp_x + pin_y, comp_y + pin_x)
      180°: screen = (comp_x - pin_x, comp_y + pin_y)
      270°: screen = (comp_x - pin_y, comp_y - pin_x)
    """
    rot = comp_rot % 360
    if rot == 0:
        sx = comp_x + pin_x
        sy = comp_y - pin_y
    elif rot == 90:
        sx = comp_x + pin_y
        sy = comp_y + pin_x
    elif rot == 180:
        sx = comp_x - pin_x
        sy = comp_y + pin_y
    elif rot == 270:
        sx = comp_x - pin_y
        sy = comp_y - pin_x
    else:
        # Non-orthogonal rotation — use trig
        import math
        rad = math.radians(rot)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)
        sx = comp_x + pin_x * cos_r + pin_y * sin_r
        sy = comp_y - (-pin_x * sin_r + pin_y * cos_r)
    return (round(sx, 4), round(sy, 4))


def _get_lib_symbol_pins(root: SexpList, lib_id: str, unit: int = 1) -> list[dict]:
    """Extract pin positions from a lib_symbols definition.

    Returns list of {pin_number, pin_name, x, y} in symbol-local coordinates.
    Handles the (extends "ParentSymbol") pattern.
    """
    lib_symbols = root.find("lib_symbols")
    if not lib_symbols:
        return []

    # Find the top-level lib_symbol matching lib_id
    lib_sym = None
    for child in lib_symbols.children:
        if isinstance(child, SexpList) and child.tag == "symbol":
            if len(child.children) >= 2 and str(child.children[1]) == lib_id:
                lib_sym = child
                break

    if not lib_sym:
        return []

    # The symbol name without library prefix (e.g., "GND" from "power:GND")
    parts = lib_id.split(":")
    sym_name = parts[1] if len(parts) == 2 else lib_id

    # Look for pins in sub-symbol "{sym_name}_{unit}_1"
    # If that sub-symbol uses (extends "Parent"), resolve the parent
    target_sub = f"{sym_name}_{unit}_1"

    pins: list[dict] = []
    for sub in lib_sym.find_all("symbol"):
        if len(sub.children) < 2:
            continue
        sub_name = str(sub.children[1])
        if sub_name != target_sub:
            continue

        # Check for extends
        extends_node = sub.find("extends")
        if extends_node and len(extends_node.children) >= 2:
            parent_name = str(extends_node.children[1])
            parent_target = f"{parent_name}_{unit}_1"
            for parent_sub in lib_sym.find_all("symbol"):
                if len(parent_sub.children) >= 2 and str(parent_sub.children[1]) == parent_target:
                    pins.extend(_extract_pins_from_subsymbol(parent_sub))
                    break
        else:
            pins.extend(_extract_pins_from_subsymbol(sub))
        break

    return pins


def _extract_pins_from_subsymbol(sub: SexpList) -> list[dict]:
    """Extract pin info from a sub-symbol node."""
    pins: list[dict] = []
    for pin in sub.find_all("pin"):
        at_node = pin.find("at")
        if not at_node or len(at_node.children) < 3:
            continue
        px = float(at_node.children[1])
        py = float(at_node.children[2])

        number_node = pin.find("number")
        pin_number = str(number_node.children[1]) if number_node and len(number_node.children) >= 2 else ""

        name_node = pin.find("name")
        pin_name = str(name_node.children[1]) if name_node and len(name_node.children) >= 2 else ""

        pins.append({
            "pin_number": pin_number,
            "pin_name": pin_name,
            "x": px,
            "y": py,
        })
    return pins


def get_pin_positions(file_path: str, reference: str) -> list[dict]:
    """Get actual pin endpoint positions for a component in schematic coordinates.

    Reads the component's position and rotation from the schematic, looks up
    pin offsets from the lib_symbols definition, and applies the rotation
    transform.

    Returns list of {pin_number, pin_name, x, y} in schematic coordinates.
    """
    root = parse_file(file_path)

    # Find the symbol instance by reference
    for sym in root.find_all("symbol"):
        ref = ""
        for prop in sym.find_all("property"):
            if len(prop.children) >= 3 and str(prop.children[1]) == "Reference":
                ref = str(prop.children[2])
                break

        if ref != reference:
            continue

        lib_id_node = sym.find("lib_id")
        if not lib_id_node or len(lib_id_node.children) < 2:
            continue
        lib_id = str(lib_id_node.children[1])

        at_node = sym.find("at")
        comp_x = float(at_node.children[1]) if at_node and len(at_node.children) >= 2 else 0.0
        comp_y = float(at_node.children[2]) if at_node and len(at_node.children) >= 3 else 0.0
        comp_rot = float(at_node.children[3]) if at_node and len(at_node.children) >= 4 else 0.0

        unit_node = sym.find("unit")
        unit = int(unit_node.children[1]) if unit_node and len(unit_node.children) >= 2 else 1

        # Get pin offsets from lib_symbols
        lib_pins = _get_lib_symbol_pins(root, lib_id, unit)

        result: list[dict] = []
        for pin in lib_pins:
            sx, sy = _transform_pin_to_schematic(
                comp_x, comp_y, comp_rot, pin["x"], pin["y"]
            )
            result.append({
                "pin_number": pin["pin_number"],
                "pin_name": pin["pin_name"],
                "x": sx,
                "y": sy,
            })
        return result

    return []


# ── Batch operations ────────────────────────────────────────────────────────


def add_power_symbols_batch(
    file_path: str,
    symbols: list[dict],
) -> list[str]:
    """Add multiple power symbols in a single file read/write cycle.

    Each dict should have: name (str), x (float), y (float),
    and optionally rotation (float, default 0).
    Returns list of UUIDs.
    """
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    project = _get_project_name(file_path)
    sch_uuid = _get_schematic_uuid(root)

    uuids: list[str] = []
    for sym in symbols:
        x = _snap_to_grid(sym["x"])
        y = _snap_to_grid(sym["y"])
        rotation = sym.get("rotation", 0)
        name = sym["name"]

        power_lib_id = f"power:{name}"
        _ensure_lib_symbol(root, power_lib_id)

        sym_uuid = _new_uuid()
        uuids.append(sym_uuid)
        pin_uuid = _new_uuid()
        ref = "#PWR?"

        instances = _make_instances_block(project, sch_uuid, ref)
        sym_sexp = (
            f'(symbol (lib_id "{esc(power_lib_id)}") (at {x} {y} {rotation}) (unit 1) '
            f'(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) '
            f'(uuid "{sym_uuid}") '
            f'(property "Reference" "{esc(ref)}" (at {x} {y - 2} 0) '
            f'(effects (font (size 1.27 1.27)) (hide yes))) '
            f'(property "Value" "{esc(name)}" (at {x} {y + 2} 0) '
            f'(effects (font (size 1.27 1.27)))) '
            f'(property "Footprint" "" (at {x} {y} 0) '
            f'(effects (font (size 1.27 1.27)) (hide yes))) '
            f'(property "Datasheet" "" (at {x} {y} 0) '
            f'(effects (font (size 1.27 1.27)) (hide yes))) '
            f'(property "Description" "Power symbol creates a global label with name \\"{esc(name)}\\"" '
            f'(at {x} {y} 0) '
            f'(effects (font (size 1.27 1.27)) (hide yes))) '
            f'(pin "1" (uuid "{pin_uuid}")) '
            f'{instances})'
        )
        sym_node = parse(sym_sexp)
        _insert_before_symbol_instances(root, sym_node)

    write_file(file_path, root)
    return uuids


def add_labels_batch(
    file_path: str,
    labels: list[dict],
) -> list[str]:
    """Add multiple labels in a single file read/write cycle.

    Each label dict should have: name, x, y, and optionally rotation.
    Returns list of UUIDs.
    """
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    uuids: list[str] = []
    for lbl in labels:
        x = _snap_to_grid(lbl["x"])
        y = _snap_to_grid(lbl["y"])
        rotation = lbl.get("rotation", 0)
        name = lbl["name"]
        lbl_uuid = _new_uuid()
        uuids.append(lbl_uuid)

        lbl_sexp = (
            f'(label "{esc(name)}" (at {x} {y} {rotation}) (uuid "{lbl_uuid}") '
            f'(effects (font (size 1.27 1.27))))'
        )
        lbl_node = parse(lbl_sexp)
        _insert_before_symbol_instances(root, lbl_node)

    write_file(file_path, root)
    return uuids


def delete_many(file_path: str, target_uuids: list[str]) -> list[bool]:
    """Delete multiple schematic elements by UUID in a single file read/write cycle.

    Returns a list of booleans indicating whether each UUID was found and deleted.
    """
    root = parse_file(file_path)
    target_set = set(target_uuids)
    found: dict[str, bool] = {u: False for u in target_uuids}

    def _search_and_remove(parent: SexpList) -> None:
        for child in list(parent.children):
            if isinstance(child, SexpList):
                uuid_node = child.find("uuid")
                if uuid_node and len(uuid_node.children) >= 2:
                    child_uuid = str(uuid_node.children[1])
                    if child_uuid in target_set:
                        parent.remove_child(child)
                        found[child_uuid] = True
                        if all(found.values()):
                            return
                _search_and_remove(child)
                if all(found.values()):
                    return

    _search_and_remove(root)

    if any(found.values()):
        write_file(file_path, root)

    return [found[u] for u in target_uuids]


def delete_by_uuid(file_path: str, target_uuid: str) -> bool:
    """Delete any schematic element by UUID. Returns True if found and deleted."""
    results = delete_many(file_path, [target_uuid])
    return results[0]
