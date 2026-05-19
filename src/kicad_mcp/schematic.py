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
        target = None
        for child in lib_root.children:
            if isinstance(child, SexpList) and child.tag == "symbol":
                if len(child.children) >= 2 and str(child.children[1]) == symbol_name:
                    target = child
                    break

        if target is None:
            return None

        # Check if the symbol uses (extends "ParentName")
        extends_node = target.find("extends")
        if extends_node and len(extends_node.children) >= 2:
            parent_name = str(extends_node.children[1])
            # Find the parent symbol and copy its sub-symbols (pins, graphics)
            # into the child so it's self-contained
            for child in lib_root.children:
                if isinstance(child, SexpList) and child.tag == "symbol":
                    if len(child.children) >= 2 and str(child.children[1]) == parent_name:
                        # Copy parent's sub-symbols into the target, renaming them
                        for parent_sub in child.find_all("symbol"):
                            if len(parent_sub.children) >= 2:
                                parent_sub_name = str(parent_sub.children[1])
                                # Rename e.g. "4538_1_1" -> "14528_1_1"
                                new_sub_name = parent_sub_name.replace(
                                    parent_name, symbol_name, 1
                                )
                                parent_sub.children[1] = QuotedString(new_sub_name)
                                target.children.append(parent_sub)
                        # Remove the extends node since we inlined the parent
                        target.remove_child(extends_node)
                        break

        # Re-tag with full lib_id for the schematic's lib_symbols block
        target.children[1] = QuotedString(lib_id)
        return target
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


def move_symbol(
    file_path: str, reference: str, x: float, y: float, rotation: float | None = None
) -> bool:
    """Move an existing schematic symbol to a new position. Returns True if found."""
    x = _snap_to_grid(x)
    y = _snap_to_grid(y)
    root = parse_file(file_path)

    for sym in root.find_all("symbol"):
        for prop in sym.find_all("property"):
            if len(prop.children) >= 3 and str(prop.children[1]) == "Reference" and str(prop.children[2]) == reference:
                at_node = sym.find("at")
                if at_node:
                    if rotation is not None:
                        at_node.children = ["at", x, y, rotation]
                    elif len(at_node.children) >= 4:
                        at_node.children = ["at", x, y, at_node.children[3]]
                    else:
                        at_node.children = ["at", x, y, 0]
                write_file(file_path, root)
                return True
    return False


def add_lib_symbol(
    file_path: str,
    lib_id: str,
    pins: list[dict],
    rectangle: dict | None = None,
    properties: dict | None = None,
) -> bool:
    """Add a custom symbol definition to the schematic's lib_symbols section.

    Args:
        lib_id: Library ID (e.g., "RF:CC1101").
        pins: List of pin dicts with keys: number, name, type, x, y, rotation.
              type is one of: input, output, passive, power_in, bidirectional, etc.
        rectangle: Optional body rectangle with x1, y1, x2, y2.
        properties: Optional dict of property name -> value (Reference, Value, etc.).

    Returns True on success.
    """
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_schematic()

    lib_symbols = root.find("lib_symbols")
    if lib_symbols is None:
        lib_symbols = SexpList(["lib_symbols"])
        root.children.insert(5, lib_symbols)

    # Check if already present
    for child in lib_symbols.children:
        if isinstance(child, SexpList) and child.tag == "symbol":
            if len(child.children) >= 2 and str(child.children[1]) == lib_id:
                return True  # already exists

    # Symbol name without library prefix for sub-symbols
    parts = lib_id.split(":")
    sym_name = parts[1] if len(parts) == 2 else lib_id

    # Build properties
    props = {"Reference": "U", "Value": sym_name, "Footprint": "", "Datasheet": "", "Description": ""}
    if properties:
        props.update(properties)

    prop_sexp_parts: list[str] = []
    for pname, pval in props.items():
        hide = "(hide yes)" if pname in ("Footprint", "Datasheet", "Description") else ""
        prop_sexp_parts.append(
            f'(property "{esc(pname)}" "{esc(pval)}" (at 0 0 0) '
            f'(effects (font (size 1.27 1.27)) {hide}))'
        )
    props_str = " ".join(prop_sexp_parts)

    # Build _0_1 sub-symbol (graphics)
    graphics = ""
    if rectangle:
        x1 = rectangle.get("x1", -5.08)
        y1 = rectangle.get("y1", -5.08)
        x2 = rectangle.get("x2", 5.08)
        y2 = rectangle.get("y2", 5.08)
        graphics = (
            f'(rectangle (start {x1} {y1}) (end {x2} {y2}) '
            f'(stroke (width 0.254) (type default)) (fill (type background)))'
        )

    # Build _1_1 sub-symbol (pins)
    pin_parts: list[str] = []
    for pin in pins:
        pnum = pin["number"]
        pname = pin.get("name", f"Pin_{pnum}")
        ptype = pin.get("type", "passive")
        px = pin.get("x", 0)
        py = pin.get("y", 0)
        prot = pin.get("rotation", 0)
        pin_parts.append(
            f'(pin {ptype} line (at {px} {py} {prot}) (length 2.54) '
            f'(name "{esc(pname)}" (effects (font (size 1.27 1.27)))) '
            f'(number "{esc(str(pnum))}" (effects (font (size 1.27 1.27)))))'
        )
    pins_str = " ".join(pin_parts)

    sym_sexp = (
        f'(symbol "{esc(lib_id)}" (in_bom yes) (on_board yes) '
        f'{props_str} '
        f'(symbol "{esc(sym_name)}_0_1" {graphics}) '
        f'(symbol "{esc(sym_name)}_1_1" {pins_str}))'
    )
    sym_node = parse(sym_sexp)
    lib_symbols.children.append(sym_node)

    write_file(file_path, root)
    return True


def _find_symbol_instances(root: SexpList, reference: str) -> list[SexpList]:
    """Find all symbol instances matching a reference designator."""
    matches: list[SexpList] = []
    for sym in root.find_all("symbol"):
        for prop in sym.find_all("property"):
            if len(prop.children) >= 3 and str(prop.children[1]) == "Reference" and str(prop.children[2]) == reference:
                matches.append(sym)
                break
    return matches


def _set_property_on_node(sym: SexpList, property_name: str, value: str) -> str | None:
    """Set or create a property on a symbol/lib_symbol node.

    Returns the old value if the property existed, or None if it was created.
    Updates the (instances ... reference ...) block if changing Reference.
    """
    for prop in sym.find_all("property"):
        if len(prop.children) >= 3 and str(prop.children[1]) == property_name:
            old_value = str(prop.children[2])
            prop.children[2] = QuotedString(value)
            # If changing Reference, also update the instances block
            if property_name == "Reference":
                instances = sym.find("instances")
                if instances:
                    for proj in instances.find_all("project"):
                        for path_node in proj.find_all("path"):
                            ref_node = path_node.find("reference")
                            if ref_node and len(ref_node.children) >= 2:
                                ref_node.children[1] = QuotedString(value)
            return old_value

    # Property doesn't exist — create it with hidden defaults
    new_prop = parse(
        f'(property "{esc(property_name)}" "{esc(value)}" (at 0 0 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes)))'
    )
    # Insert after the last existing property, before pins
    last_prop_idx = -1
    for i, child in enumerate(sym.children):
        if isinstance(child, SexpList) and child.tag == "property":
            last_prop_idx = i
    if last_prop_idx >= 0:
        sym.children.insert(last_prop_idx + 1, new_prop)
    else:
        sym.children.append(new_prop)
    return None


def set_symbol_property(
    file_path: str, reference: str, property_name: str, value: str, unit: int | None = None
) -> dict:
    """Set a property on a placed symbol instance, looked up by reference.

    Returns {updated: bool, old_value: str|None}. If multiple units share the
    reference (multi-unit symbol), pass `unit` to disambiguate.
    """
    root = parse_file(file_path)
    matches = _find_symbol_instances(root, reference)

    if not matches:
        return {"updated": False, "error": f"No symbol with reference {reference!r}"}

    if len(matches) > 1 and unit is None:
        units: list[int] = []
        for m in matches:
            u_node = m.find("unit")
            u = int(u_node.children[1]) if u_node and len(u_node.children) >= 2 else 1
            units.append(u)
        return {
            "updated": False,
            "error": f"Multiple units found for {reference!r}",
            "units": units,
        }

    target = matches[0]
    if len(matches) > 1:
        for m in matches:
            u_node = m.find("unit")
            u = int(u_node.children[1]) if u_node and len(u_node.children) >= 2 else 1
            if u == unit:
                target = m
                break

    old = _set_property_on_node(target, property_name, value)
    write_file(file_path, root)
    return {"updated": True, "old_value": old}


def set_lib_symbol_property(
    file_path: str, lib_id: str, property_name: str, value: str
) -> dict:
    """Set a property on a lib_symbols definition (the default for new instances)."""
    root = parse_file(file_path)
    lib_symbols = root.find("lib_symbols")
    if not lib_symbols:
        return {"updated": False, "error": "no lib_symbols section"}

    for child in lib_symbols.children:
        if isinstance(child, SexpList) and child.tag == "symbol":
            if len(child.children) >= 2 and str(child.children[1]) == lib_id:
                old = _set_property_on_node(child, property_name, value)
                write_file(file_path, root)
                return {"updated": True, "old_value": old}

    return {"updated": False, "error": f"lib_symbol {lib_id!r} not found"}


def list_symbols(file_path: str) -> list[dict]:
    """Return a thin listing of all placed symbols with key fields only."""
    root = parse_file(file_path)
    results: list[dict] = []

    for sym in root.find_all("symbol"):
        lib_id_node = sym.find("lib_id")
        lib_id = str(lib_id_node.children[1]) if lib_id_node and len(lib_id_node.children) >= 2 else ""

        at_node = sym.find("at")
        x = float(at_node.children[1]) if at_node and len(at_node.children) >= 2 else 0.0
        y = float(at_node.children[2]) if at_node and len(at_node.children) >= 3 else 0.0
        rot = float(at_node.children[3]) if at_node and len(at_node.children) >= 4 else 0.0

        uuid_node = sym.find("uuid")
        sym_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""

        unit_node = sym.find("unit")
        unit = int(unit_node.children[1]) if unit_node and len(unit_node.children) >= 2 else 1

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

        results.append({
            "reference": ref,
            "value": value,
            "lib_id": lib_id,
            "footprint": footprint,
            "x": x,
            "y": y,
            "rotation": rot,
            "unit": unit,
            "uuid": sym_uuid,
        })

    return results


def rename_label(file_path: str, old_name: str, new_name: str) -> dict:
    """Rename labels matching old_name to new_name, preserving UUIDs.

    Affects both (label ...) and (global_label ...) nodes.
    Returns {renamed: int} with the count.
    """
    root = parse_file(file_path)
    count = 0

    for tag in ("label", "global_label"):
        for lbl in root.find_all(tag):
            if len(lbl.children) >= 2 and str(lbl.children[1]) == old_name:
                lbl.children[1] = QuotedString(new_name)
                count += 1

    if count:
        write_file(file_path, root)
    return {"renamed": count}


def delete_lib_symbol(file_path: str, lib_id: str) -> dict:
    """Delete a lib_symbol definition if no instances reference it."""
    root = parse_file(file_path)

    # Check if any symbol instances reference this lib_id
    for sym in root.find_all("symbol"):
        lid = sym.find("lib_id")
        if lid and len(lid.children) >= 2 and str(lid.children[1]) == lib_id:
            return {"deleted": False, "reason": "still in use"}

    lib_symbols = root.find("lib_symbols")
    if not lib_symbols:
        return {"deleted": False, "reason": "not found"}

    for child in list(lib_symbols.children):
        if isinstance(child, SexpList) and child.tag == "symbol":
            if len(child.children) >= 2 and str(child.children[1]) == lib_id:
                lib_symbols.remove_child(child)
                write_file(file_path, root)
                return {"deleted": True, "lib_id": lib_id}

    return {"deleted": False, "reason": "not found"}


def cleanup_lib_symbols(file_path: str) -> list[str]:
    """Remove all orphaned lib_symbol definitions with no matching instances.

    Returns list of lib_ids that were removed.
    """
    root = parse_file(file_path)

    # Collect all lib_ids referenced by symbol instances
    used_lib_ids: set[str] = set()
    for sym in root.find_all("symbol"):
        lid = sym.find("lib_id")
        if lid and len(lid.children) >= 2:
            used_lib_ids.add(str(lid.children[1]))

    lib_symbols = root.find("lib_symbols")
    if not lib_symbols:
        return []

    removed: list[str] = []
    for child in list(lib_symbols.children):
        if isinstance(child, SexpList) and child.tag == "symbol":
            if len(child.children) >= 2:
                child_id = str(child.children[1])
                if child_id not in used_lib_ids:
                    lib_symbols.remove_child(child)
                    removed.append(child_id)

    if removed:
        write_file(file_path, root)

    return removed


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


def modify_lib_symbol_pin(
    file_path: str, lib_id: str, pin_number: str, pin_type: str
) -> bool:
    """Modify a pin's electrical type in the schematic's lib_symbols section.

    Valid pin_type values: input, output, bidirectional, tri_state, passive,
    free, unspecified, power_in, power_out, open_collector, open_emitter,
    no_connect.

    Returns True if the pin was found and modified.
    """
    root = parse_file(file_path)
    lib_symbols = root.find("lib_symbols")
    if not lib_symbols:
        return False

    # Find the lib_symbol
    lib_sym = None
    for child in lib_symbols.children:
        if isinstance(child, SexpList) and child.tag == "symbol":
            if len(child.children) >= 2 and str(child.children[1]) == lib_id:
                lib_sym = child
                break

    if not lib_sym:
        return False

    # Search all sub-symbols for the pin with matching number
    for sub in lib_sym.find_all("symbol"):
        for pin in sub.find_all("pin"):
            number_node = pin.find("number")
            if number_node and len(number_node.children) >= 2:
                if str(number_node.children[1]) == pin_number:
                    # Pin structure: (pin <type> <shape> (at ...) ...)
                    # children[0] = "pin", children[1] = type, children[2] = shape
                    if len(pin.children) >= 2:
                        pin.children[1] = pin_type
                        write_file(file_path, root)
                        return True

    return False


def annotate(file_path: str) -> dict:
    """Assign reference designators to unannotated symbols (those with '?' in reference).

    Finds all symbols with '?' in their reference, groups by prefix (R, C, U, etc.),
    and assigns sequential numbers avoiding any already in use.

    Returns dict with {prefix: [assigned_refs]} for all changes made.
    """
    import re

    root = parse_file(file_path)

    # Collect existing references and unannotated symbols
    used_refs: dict[str, set[int]] = {}  # prefix -> set of used numbers
    unannotated: list[tuple[SexpList, SexpList, str]] = []  # (symbol, ref_prop, prefix)

    for sym in root.find_all("symbol"):
        for prop in sym.find_all("property"):
            if len(prop.children) >= 3 and str(prop.children[1]) == "Reference":
                ref_str = str(prop.children[2])
                # Match prefix + optional number/question mark
                m = re.match(r"^(#?[A-Za-z]+?)(\d+)$", ref_str)
                if m:
                    prefix = m.group(1)
                    num = int(m.group(2))
                    used_refs.setdefault(prefix, set()).add(num)
                elif "?" in ref_str:
                    prefix = ref_str.replace("?", "")
                    used_refs.setdefault(prefix, set())
                    unannotated.append((sym, prop, prefix))

    if not unannotated:
        return {"changes": {}}

    # Assign sequential numbers
    changes: dict[str, list[str]] = {}
    for sym, prop, prefix in unannotated:
        used = used_refs[prefix]
        num = 1
        while num in used:
            num += 1
        used.add(num)

        new_ref = f"{prefix}{num}"
        prop.children[2] = QuotedString(new_ref)
        changes.setdefault(prefix, []).append(new_ref)

        # Also update the instances block if present
        instances = sym.find("instances")
        if instances:
            for proj in instances.find_all("project"):
                for path_node in proj.find_all("path"):
                    ref_node = path_node.find("reference")
                    if ref_node and len(ref_node.children) >= 2:
                        ref_node.children[1] = QuotedString(new_ref)

    write_file(file_path, root)
    return {"changes": changes}


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
