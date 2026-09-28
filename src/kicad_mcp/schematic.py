"""Schematic file (.kicad_sch) operations.

Provides functions to read, create, and modify KiCad 8 schematic files
by manipulating their S-expression representation.
"""

from __future__ import annotations

import copy
import math
import re
import uuid
from pathlib import Path

from . import library
from .sexp_parser import (
    QuotedString,
    SexpList,
    escape_sexp_string as esc,
    parse,
    parse_file,
    write_file,
)
from .types import LabelInfo, PinInfo, Point, SchematicData, SymbolInstance, WireInfo

# Default schematic grid: 1.27 mm (50 mil). KiCad requires exact coordinate
# matches for connections, and standard library pins sit on this grid.
_GRID = 1.27

PIN_TYPES = frozenset({
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
    "no_connect",
})

# Top-level nodes that precede drawable items in a KiCad 8 schematic.
_HEADER_TAGS = frozenset({
    "version", "generator", "generator_version", "uuid", "paper", "title_block",
    "lib_symbols",
})
# Top-level nodes that follow all drawable items.
_TRAILER_TAGS = ("sheet_instances", "symbol_instances", "embedded_fonts")


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _snap_to_grid(value: float, grid: float = _GRID) -> float:
    """Snap a coordinate to the nearest KiCad grid point."""
    return round(round(value / grid) * grid, 4)


def _snap(value: float, snap: bool) -> float:
    return _snap_to_grid(value) if snap else round(value, 4)


def _get_schematic_uuid(root: SexpList) -> str:
    """Get the root UUID of the schematic."""
    uuid_node = root.find("uuid")
    if uuid_node and len(uuid_node.children) >= 2:
        return str(uuid_node.children[1])
    return ""


def _get_project_name(file_path: str) -> str:
    """Derive the project name from the schematic file path."""
    return Path(file_path).stem


def _insert_item(root: SexpList, node: SexpList) -> None:
    """Insert a drawable item before the trailing instance blocks, or at the end."""
    for i, child in enumerate(root.children):
        if isinstance(child, SexpList) and child.tag in _TRAILER_TAGS:
            root.children.insert(i, node)
            return
    root.children.append(node)


# Kept for callers that used the old name.
_insert_before_symbol_instances = _insert_item


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
        '(paper "A4") '
        "(lib_symbols) "
        '(sheet_instances (path "/" (page "1"))))'
    )


def _load_or_create(file_path: str) -> SexpList:
    if Path(file_path).exists():
        return parse_file(file_path)
    return _make_empty_schematic()


def _read_at(node: SexpList | None) -> tuple[float, float, float]:
    """Return (x, y, angle) from a node's (at ...) child."""
    at_node = node.find("at") if node is not None else None
    if at_node is None:
        return 0.0, 0.0, 0.0
    c = at_node.children
    x = float(c[1]) if len(c) >= 2 else 0.0
    y = float(c[2]) if len(c) >= 3 else 0.0
    rot = float(c[3]) if len(c) >= 4 else 0.0
    return x, y, rot


def _int_value(node: SexpList, tag: str, default: int) -> int:
    child = node.find(tag)
    if child is not None and len(child.children) >= 2:
        try:
            return int(child.children[1])
        except (TypeError, ValueError):
            return default
    return default


def _symbol_unit(sym: SexpList) -> int:
    return _int_value(sym, "unit", 1)


def _symbol_body_style(sym: SexpList) -> int:
    return _int_value(sym, "body_style", _int_value(sym, "convert", 1))


def _symbol_mirror(sym: SexpList) -> str | None:
    node = sym.find("mirror")
    if node is not None and len(node.children) >= 2:
        return str(node.children[1])
    return None


def _symbol_reference(sym: SexpList) -> str:
    for prop in sym.find_all("property"):
        if len(prop.children) >= 3 and str(prop.children[1]) == "Reference":
            return str(prop.children[2])
    return ""


def _symbol_lib_id(sym: SexpList) -> str:
    node = sym.find("lib_id")
    return str(node.children[1]) if node and len(node.children) >= 2 else ""


def _split_lib_id(lib_id: str) -> tuple[str, str]:
    parts = lib_id.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid lib_id {lib_id!r}: expected 'Library:Symbol'.")
    return parts[0], parts[1]


def _symbol_name(lib_id: str) -> str:
    """Symbol name without the library prefix (e.g. "GND" from "power:GND")."""
    return lib_id.split(":", 1)[1] if ":" in lib_id else lib_id


# ── Reading ─────────────────────────────────────────────────────────────────


def read_schematic(file_path: str) -> SchematicData:
    """Read and parse a .kicad_sch file into structured data."""
    root = parse_file(file_path)
    data = SchematicData()

    for sym in root.find_all("symbol"):
        x, y, rot = _read_at(sym)

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
            uuid=sym_uuid, lib_id=_symbol_lib_id(sym), reference=ref, value=value,
            footprint=footprint, position=Point(x, y), rotation=rot, pins=pins,
            unit=_symbol_unit(sym), mirror=_symbol_mirror(sym),
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
                x, y, rot = _read_at(lbl)
                uuid_node = lbl.find("uuid")
                lbl_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""
                data.labels.append(LabelInfo(
                    uuid=lbl_uuid, name=name, position=Point(x, y),
                    rotation=rot, label_type=label_tag,
                ))

    return data


# ── Library symbols ─────────────────────────────────────────────────────────


def _find_lib_symbol(root: SexpList, lib_id: str) -> SexpList | None:
    lib_symbols = root.find("lib_symbols")
    if lib_symbols is None:
        return None
    for child in lib_symbols.children:
        if isinstance(child, SexpList) and child.tag == "symbol":
            if len(child.children) >= 2 and str(child.children[1]) == lib_id:
                return child
    return None


def _get_or_create_lib_symbols(root: SexpList) -> SexpList:
    lib_symbols = root.find("lib_symbols")
    if lib_symbols is not None:
        return lib_symbols
    lib_symbols = SexpList(["lib_symbols"])
    insert_at = 1
    for i, child in enumerate(root.children):
        if isinstance(child, SexpList) and child.tag in _HEADER_TAGS:
            insert_at = i + 1
    root.children.insert(insert_at, lib_symbols)
    return lib_symbols


def _ensure_lib_symbol(root: SexpList, lib_id: str) -> SexpList:
    """Ensure the lib_symbols block contains the definition for lib_id.

    Loads it from KiCad's installed libraries if the schematic doesn't have
    it yet. Raises ValueError if the symbol exists in neither place, rather
    than inserting a pin-less stub that would silently never connect.
    """
    existing = _find_lib_symbol(root, lib_id)
    if existing is not None:
        return existing

    symbol_def = _load_lib_symbol_from_disk(lib_id)
    if symbol_def is None:
        raise ValueError(_missing_symbol_message(lib_id))

    _get_or_create_lib_symbols(root).children.append(symbol_def)
    return symbol_def


def _missing_symbol_message(lib_id: str) -> str:
    msg = (
        f"Symbol {lib_id!r} not found in the schematic or in the KiCad symbol "
        f"libraries at {library.symbol_dir()}."
    )
    if ":" in lib_id:
        lib_name, name = lib_id.split(":", 1)
        if lib_name == "power" and not name.startswith(("+", "-")):
            alt = f"power:+{name}"
            if _load_lib_symbol_from_disk(alt) is not None:
                return msg + f" Did you mean {alt!r}?"
    msg += " Use list_symbols to search, or schematic_add_lib_symbol for a custom part."
    return msg


def _load_lib_symbol_from_disk(lib_id: str) -> SexpList | None:
    """Load a self-contained symbol definition from KiCad's installed libraries."""
    if ":" not in lib_id:
        return None
    lib_name, symbol_name = lib_id.split(":", 1)
    symbol = library.load_symbol(lib_name, symbol_name)
    if symbol is None:
        return None
    # Re-tag with full lib_id for the schematic's lib_symbols block
    symbol.children[1] = QuotedString(lib_id)
    return symbol


_SUB_SYMBOL_RE = re.compile(r"^(.*)_(\d+)_(\d+)$")


def _sub_symbols(lib_sym: SexpList, unit: int, body_style: int = 1) -> list[SexpList]:
    """Unit sub-symbols that apply to the given unit and body style.

    Sub-symbols are named NAME_U_S; U == 0 applies to every unit and
    S == 0 to every body style.
    """
    subs: list[SexpList] = []
    for sub in lib_sym.find_all("symbol"):
        if len(sub.children) < 2:
            continue
        m = _SUB_SYMBOL_RE.match(str(sub.children[1]))
        if not m:
            continue
        u, s = int(m.group(2)), int(m.group(3))
        if u in (0, unit) and s in (0, body_style):
            subs.append(sub)
    return subs


def _lib_symbol_units(lib_sym: SexpList) -> set[int]:
    """Unit numbers (>= 1) defined by a lib symbol; {1} for single-unit symbols."""
    units: set[int] = set()
    for sub in lib_sym.find_all("symbol"):
        if len(sub.children) >= 2:
            m = _SUB_SYMBOL_RE.match(str(sub.children[1]))
            if m and int(m.group(2)) > 0:
                units.add(int(m.group(2)))
    return units or {1}


def _resolve_extends(root: SexpList, lib_sym: SexpList, lib_id: str) -> SexpList:
    """Follow a top-level (extends ...) to its parent inside lib_symbols."""
    extends_node = lib_sym.find("extends")
    if extends_node is None or len(extends_node.children) < 2:
        return lib_sym
    parent_name = str(extends_node.children[1])
    candidates = [parent_name]
    if ":" in lib_id and ":" not in parent_name:
        candidates.insert(0, f"{lib_id.split(':', 1)[0]}:{parent_name}")
    for cand in candidates:
        parent = _find_lib_symbol(root, cand)
        if parent is not None:
            return parent
    return lib_sym


def _get_lib_symbol_pins(
    root: SexpList, lib_id: str, unit: int = 1, body_style: int = 1
) -> list[dict]:
    """Extract pin positions from a lib_symbols definition.

    Returns list of {pin_number, pin_name, x, y} in symbol-local coordinates
    (Y up). Includes pins shared by all units (the NAME_0_S sub-symbols).
    """
    lib_sym = _find_lib_symbol(root, lib_id)
    if lib_sym is None:
        return []
    lib_sym = _resolve_extends(root, lib_sym, lib_id)

    pins: list[dict] = []
    for sub in _sub_symbols(lib_sym, unit, body_style):
        pins.extend(_extract_pins_from_subsymbol(sub))
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


# ── Coordinate transforms ───────────────────────────────────────────────────


def _symbol_offset(
    px: float, py: float, rotation: float, mirror: str | None = None
) -> tuple[float, float]:
    """Map a symbol-local point (Y up) to an offset from the symbol origin.

    Matches KiCad (verified against kicad-cli netlists): the Y axis is
    flipped, then the symbol is rotated counterclockwise on screen by
    `rotation`, then mirrored ("x" flips vertically, "y" horizontally).
    """
    x, y = px, -py
    rad = math.radians(rotation % 360)
    c = round(math.cos(rad), 12)
    s = round(math.sin(rad), 12)
    x, y = x * c + y * s, -x * s + y * c
    if mirror == "x":
        y = -y
    elif mirror == "y":
        x = -x
    return x, y


def _transform_pin_to_schematic(
    comp_x: float, comp_y: float, comp_rot: float,
    pin_x: float, pin_y: float, mirror: str | None = None,
) -> tuple[float, float]:
    """Transform a pin position from symbol coordinates to schematic coordinates."""
    dx, dy = _symbol_offset(pin_x, pin_y, comp_rot, mirror)
    return (round(comp_x + dx, 4), round(comp_y + dy, 4))


# ── Placement ───────────────────────────────────────────────────────────────


_DEFAULT_EFFECTS = "(effects (font (size 1.27 1.27)))"
_HIDDEN_EFFECTS = "(effects (font (size 1.27 1.27)) (hide yes))"
_INSTANCE_FIELDS = ("Reference", "Value", "Footprint", "Datasheet", "Description")


def _lib_property(lib_sym: SexpList, name: str) -> SexpList | None:
    for prop in lib_sym.find_all("property"):
        if len(prop.children) >= 3 and str(prop.children[1]) == name:
            return prop
    return None


def _flag(lib_sym: SexpList, tag: str, default: str) -> str:
    node = lib_sym.find(tag)
    if node is not None and len(node.children) >= 2:
        return str(node.children[1])
    return default


def _build_symbol_node(
    root: SexpList,
    lib_sym: SexpList,
    lib_id: str,
    reference: str,
    value: str,
    footprint: str | None,
    x: float,
    y: float,
    rotation: float,
    unit: int,
    project: str,
) -> SexpList:
    """Build a placed-symbol node from its lib_symbols definition.

    Field positions and visibility come from the library definition
    (transformed like the pins), and a (pin ...) entry is created for every
    pin of the chosen unit.
    """
    sym_uuid = _new_uuid()
    field_values = {
        "Reference": reference,
        "Value": value,
        "Footprint": footprint,
        "Datasheet": None,
        "Description": None,
    }

    node = parse(
        f'(symbol (lib_id "{esc(lib_id)}") (at {x} {y} {rotation}) (unit {unit}) '
        f'(exclude_from_sim {_flag(lib_sym, "exclude_from_sim", "no")}) '
        f'(in_bom {_flag(lib_sym, "in_bom", "yes")}) '
        f'(on_board {_flag(lib_sym, "on_board", "yes")}) (dnp no) '
        f'(uuid "{sym_uuid}"))'
    )
    for name in _INSTANCE_FIELDS:
        lib_prop = _lib_property(lib_sym, name)
        val = field_values[name]
        if val is None:
            val = str(lib_prop.children[2]) if lib_prop is not None else ""
        effects_node = lib_prop.find("effects") if lib_prop is not None else None
        if lib_prop is not None:
            fx, fy, fangle = _read_at(lib_prop)
            dx, dy = _symbol_offset(fx, fy, rotation)
        else:
            dx, dy, fangle = 0.0, 0.0, 0.0
        prop = parse(
            f'(property "{name}" "{esc(val)}" '
            f'(at {round(x + dx, 4)} {round(y + dy, 4)} {fangle}))'
        )
        if effects_node is not None:
            prop.append(copy.deepcopy(effects_node))
        elif name in ("Reference", "Value"):
            prop.append(parse(_DEFAULT_EFFECTS))
        else:
            prop.append(parse(_HIDDEN_EFFECTS))
        node.append(prop)

    seen: set[str] = set()
    for sub in _sub_symbols(_resolve_extends(root, lib_sym, lib_id), unit):
        for pin in _extract_pins_from_subsymbol(sub):
            num = pin["pin_number"]
            if num and num not in seen:
                seen.add(num)
                node.append(parse(f'(pin "{esc(num)}" (uuid "{_new_uuid()}"))'))

    node.append(parse(
        _make_instances_block(project, _get_schematic_uuid(root), reference, unit)
    ))
    return node


def _check_unit(lib_sym: SexpList, lib_id: str, unit: int) -> None:
    units = _lib_symbol_units(lib_sym)
    if unit not in units:
        raise ValueError(
            f"Unit {unit} does not exist on {lib_id!r}; available units: {sorted(units)}."
        )


def place_symbol(
    file_path: str,
    lib_id: str,
    reference: str,
    value: str,
    footprint: str,
    x: float,
    y: float,
    rotation: float = 0,
    unit: int = 1,
) -> str:
    """Place a component symbol in the schematic. Returns the UUID.

    Raises ValueError if the symbol can't be found, the unit doesn't exist,
    or the same reference and unit are already placed.
    """
    _split_lib_id(lib_id)
    x = _snap_to_grid(x)
    y = _snap_to_grid(y)

    root = _load_or_create(file_path)
    lib_sym = _ensure_lib_symbol(root, lib_id)
    _check_unit(lib_sym, lib_id, unit)

    if "?" not in reference:
        for existing in _find_symbol_instances(root, reference):
            if _symbol_unit(existing) == unit:
                raise ValueError(
                    f"Reference {reference!r} unit {unit} is already placed in the schematic."
                )

    sym_node = _build_symbol_node(
        root, lib_sym, lib_id, reference, value, footprint, x, y, rotation, unit,
        _get_project_name(file_path),
    )
    _insert_item(root, sym_node)

    write_file(file_path, root)
    return str(sym_node.find("uuid").children[1])


def add_wire(file_path: str, points: list[tuple[float, float]], snap: bool = True) -> str:
    """Add a wire to the schematic. Returns the UUID of the last segment.

    KiCad wires are always 2-point segments, so a multi-point path is split
    into consecutive 2-point wires. Zero-length segments are rejected.
    """
    if len(points) < 2:
        raise ValueError("A wire requires at least 2 points.")

    points = [(_snap(x, snap), _snap(y, snap)) for x, y in points]

    root = _load_or_create(file_path)

    last_uuid = ""
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        if x1 == x2 and y1 == y2:
            continue  # skip zero-length segments

        wire_uuid = _new_uuid()
        last_uuid = wire_uuid
        wire_sexp = (
            f'(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) '
            f'(stroke (width 0) (type default)) (uuid "{wire_uuid}"))'
        )
        _insert_item(root, parse(wire_sexp))

    if not last_uuid:
        raise ValueError("All wire segments were zero-length.")

    write_file(file_path, root)
    return last_uuid


def _label_node(name: str, x: float, y: float, rotation: float) -> SexpList:
    return parse(
        f'(label "{esc(name)}" (at {x} {y} {rotation}) '
        f'(effects (font (size 1.27 1.27)) (justify left bottom)) '
        f'(uuid "{_new_uuid()}"))'
    )


def add_label(
    file_path: str, name: str, x: float, y: float, rotation: float = 0, snap: bool = True
) -> str:
    """Add a net label to the schematic. Returns the UUID."""
    root = _load_or_create(file_path)
    node = _label_node(name, _snap(x, snap), _snap(y, snap), rotation)
    _insert_item(root, node)
    write_file(file_path, root)
    return str(node.find("uuid").children[1])


def add_global_label(
    file_path: str, name: str, x: float, y: float, rotation: float = 0,
    snap: bool = True, shape: str = "input",
) -> str:
    """Add a global label to the schematic. Returns the UUID."""
    if shape not in ("input", "output", "bidirectional", "tri_state", "passive"):
        raise ValueError(f"Invalid global label shape {shape!r}.")
    x = _snap(x, snap)
    y = _snap(y, snap)

    root = _load_or_create(file_path)

    lbl_uuid = _new_uuid()
    lbl_sexp = (
        f'(global_label "{esc(name)}" (shape {shape}) (at {x} {y} {rotation}) '
        f'(effects (font (size 1.27 1.27)) (justify left)) (uuid "{lbl_uuid}") '
        f'(property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {x} {y} 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes))))'
    )
    _insert_item(root, parse(lbl_sexp))

    write_file(file_path, root)
    return lbl_uuid


def _add_power_symbol_to_root(
    root: SexpList, project: str, name: str, x: float, y: float, rotation: float
) -> str:
    power_lib_id = f"power:{name}"
    lib_sym = _ensure_lib_symbol(root, power_lib_id)
    node = _build_symbol_node(
        root, lib_sym, power_lib_id, "#PWR?", name, "", x, y, rotation, 1, project,
    )
    _insert_item(root, node)
    return str(node.find("uuid").children[1])


def add_power_symbol(
    file_path: str, name: str, x: float, y: float, rotation: float = 0, snap: bool = True
) -> str:
    """Add a power port symbol (GND, +3V3, +5V, etc.) to the schematic. Returns the UUID."""
    root = _load_or_create(file_path)
    sym_uuid = _add_power_symbol_to_root(
        root, _get_project_name(file_path), name, _snap(x, snap), _snap(y, snap), rotation,
    )
    write_file(file_path, root)
    return sym_uuid


def _no_connect_node(x: float, y: float) -> SexpList:
    return parse(f'(no_connect (at {x} {y}) (uuid "{_new_uuid()}"))')


def add_no_connect(file_path: str, x: float, y: float, snap: bool = True) -> str:
    """Add a no-connect flag at a pin position. Returns the UUID."""
    root = _load_or_create(file_path)
    node = _no_connect_node(_snap(x, snap), _snap(y, snap))
    _insert_item(root, node)
    write_file(file_path, root)
    return str(node.find("uuid").children[1])


def add_no_connects_batch(
    file_path: str, positions: list[dict], snap: bool = True
) -> list[str]:
    """Add multiple no-connect flags in a single file read/write cycle.

    Each dict should have: x (float), y (float).
    Returns list of UUIDs.
    """
    root = _load_or_create(file_path)

    uuids: list[str] = []
    for pos in positions:
        node = _no_connect_node(_snap(pos["x"], snap), _snap(pos["y"], snap))
        _insert_item(root, node)
        uuids.append(str(node.find("uuid").children[1]))

    write_file(file_path, root)
    return uuids


def _select_unit(matches: list[SexpList], reference: str, unit: int | None) -> SexpList:
    """Pick one symbol instance among the units sharing a reference."""
    if unit is not None:
        for m in matches:
            if _symbol_unit(m) == unit:
                return m
        raise ValueError(
            f"{reference!r} has no unit {unit}; placed units: "
            f"{sorted(_symbol_unit(m) for m in matches)}."
        )
    if len(matches) > 1:
        raise ValueError(
            f"{reference!r} has multiple placed units "
            f"{sorted(_symbol_unit(m) for m in matches)}; pass unit to choose one."
        )
    return matches[0]


def move_symbol(
    file_path: str,
    reference: str,
    x: float,
    y: float,
    rotation: float | None = None,
    unit: int | None = None,
) -> bool:
    """Move an existing schematic symbol to a new position. Returns True if found.

    The symbol's fields (Reference, Value, ...) move with it. For multi-unit
    parts, `unit` selects which placed unit to move.
    """
    x = _snap_to_grid(x)
    y = _snap_to_grid(y)
    root = parse_file(file_path)

    matches = _find_symbol_instances(root, reference)
    if not matches:
        return False
    sym = _select_unit(matches, reference, unit)

    old_x, old_y, old_rot = _read_at(sym)
    new_rot = old_rot if rotation is None else rotation
    at_node = sym.find("at")
    if at_node is None:
        sym.children.insert(2, parse(f"(at {x} {y} {new_rot})"))
    else:
        at_node.children = ["at", x, y, new_rot]

    # Schematic field positions are absolute, so carry them along.
    dx, dy = x - old_x, y - old_y
    for prop in sym.find_all("property"):
        prop_at = prop.find("at")
        if prop_at is not None and len(prop_at.children) >= 3:
            prop_at.children[1] = round(float(prop_at.children[1]) + dx, 4)
            prop_at.children[2] = round(float(prop_at.children[2]) + dy, 4)

    write_file(file_path, root)
    return True


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
              type is one of PIN_TYPES; rotation one of 0, 90, 180, 270.
        rectangle: Optional body rectangle with x1, y1, x2, y2.
        properties: Optional dict of property name -> value (Reference, Value, etc.).

    Returns True if added, False if a symbol with this lib_id already exists.
    Raises ValueError on invalid pin definitions.
    """
    _split_lib_id(lib_id)
    if not pins:
        raise ValueError("A symbol needs at least one pin.")
    numbers = [str(p.get("number", "")) for p in pins]
    if any(not n for n in numbers):
        raise ValueError("Every pin needs a non-empty 'number'.")
    if len(set(numbers)) != len(numbers):
        raise ValueError("Pin numbers must be unique.")
    for pin in pins:
        ptype = pin.get("type", "passive")
        if ptype not in PIN_TYPES:
            raise ValueError(
                f"Invalid pin type {ptype!r} for pin {pin['number']}; "
                f"valid types: {', '.join(sorted(PIN_TYPES))}."
            )
        if float(pin.get("rotation", 0)) % 360 not in (0, 90, 180, 270):
            raise ValueError(f"Pin {pin['number']} rotation must be 0, 90, 180 or 270.")

    root = _load_or_create(file_path)
    lib_symbols = _get_or_create_lib_symbols(root)

    if _find_lib_symbol(root, lib_id) is not None:
        return False

    sym_name = _symbol_name(lib_id)

    props = {"Reference": "U", "Value": sym_name, "Footprint": "", "Datasheet": "", "Description": ""}
    if properties:
        props.update({str(k): str(v) for k, v in properties.items()})

    prop_sexp_parts: list[str] = []
    for pname, pval in props.items():
        hide = "(hide yes)" if pname not in ("Reference", "Value") else ""
        prop_sexp_parts.append(
            f'(property "{esc(pname)}" "{esc(pval)}" (at 0 0 0) '
            f'(effects (font (size 1.27 1.27)) {hide}))'
        )
    props_str = " ".join(prop_sexp_parts)

    graphics = ""
    if rectangle:
        x1 = float(rectangle.get("x1", -5.08))
        y1 = float(rectangle.get("y1", -5.08))
        x2 = float(rectangle.get("x2", 5.08))
        y2 = float(rectangle.get("y2", 5.08))
        graphics = (
            f'(rectangle (start {x1} {y1}) (end {x2} {y2}) '
            f'(stroke (width 0.254) (type default)) (fill (type background)))'
        )

    pin_parts: list[str] = []
    for pin in pins:
        pnum = str(pin["number"])
        pname = str(pin.get("name", f"Pin_{pnum}"))
        ptype = pin.get("type", "passive")
        px = float(pin.get("x", 0))
        py = float(pin.get("y", 0))
        prot = float(pin.get("rotation", 0)) % 360
        pin_parts.append(
            f'(pin {ptype} line (at {px} {py} {prot}) (length 2.54) '
            f'(name "{esc(pname)}" (effects (font (size 1.27 1.27)))) '
            f'(number "{esc(pnum)}" (effects (font (size 1.27 1.27)))))'
        )
    pins_str = " ".join(pin_parts)

    sym_sexp = (
        f'(symbol "{esc(lib_id)}" (exclude_from_sim no) (in_bom yes) (on_board yes) '
        f'{props_str} '
        f'(symbol "{esc(sym_name)}_0_1" {graphics}) '
        f'(symbol "{esc(sym_name)}_1_1" {pins_str}))'
    )
    lib_symbols.children.append(parse(sym_sexp))

    write_file(file_path, root)
    return True


def _find_symbol_instances(root: SexpList, reference: str) -> list[SexpList]:
    """Find all symbol instances matching a reference designator."""
    return [sym for sym in root.find_all("symbol") if _symbol_reference(sym) == reference]


def _set_property_on_node(sym: SexpList, property_name: str, value: str) -> str | None:
    """Set or create a property on a symbol/lib_symbol node.

    Returns the old value if the property existed, or None if it was created.
    Updates the (instances ... reference ...) block if changing Reference.
    """
    for prop in sym.find_all("property"):
        if len(prop.children) >= 3 and str(prop.children[1]) == property_name:
            old_value = str(prop.children[2])
            prop.children[2] = QuotedString(value)
            if property_name == "Reference":
                _set_instance_reference(sym, value)
            return old_value

    # Property doesn't exist: create it with hidden defaults, at the symbol origin
    x, y, _rot = _read_at(sym)
    new_prop = parse(
        f'(property "{esc(property_name)}" "{esc(value)}" (at {x} {y} 0) '
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


def _set_instance_reference(sym: SexpList, reference: str) -> None:
    instances = sym.find("instances")
    if instances:
        for proj in instances.find_all("project"):
            for path_node in proj.find_all("path"):
                ref_node = path_node.find("reference")
                if ref_node and len(ref_node.children) >= 2:
                    ref_node.children[1] = QuotedString(reference)


def set_symbol_property(
    file_path: str, reference: str, property_name: str, value: str, unit: int | None = None
) -> dict:
    """Set a property on a placed symbol, looked up by reference.

    For multi-unit parts, the change applies to every placed unit unless
    `unit` is given (KiCad keeps Value/Footprint in sync across units).
    Renaming the Reference always applies to all units, so the part isn't
    split into two designators.

    Returns {updated: True, old_value, units} or {updated: False, error}.
    """
    root = parse_file(file_path)
    matches = _find_symbol_instances(root, reference)

    if not matches:
        return {"updated": False, "error": f"No symbol with reference {reference!r}"}

    if property_name == "Reference" and value != reference:
        for sym in root.find_all("symbol"):
            if _symbol_reference(sym) == value:
                return {"updated": False, "error": f"Reference {value!r} is already in use"}

    if unit is not None and property_name != "Reference":
        try:
            targets = [_select_unit(matches, reference, unit)]
        except ValueError as e:
            return {"updated": False, "error": str(e),
                    "units": sorted(_symbol_unit(m) for m in matches)}
    else:
        targets = matches

    old = None
    for target in targets:
        prev = _set_property_on_node(target, property_name, value)
        if old is None:
            old = prev
    write_file(file_path, root)
    return {
        "updated": True,
        "old_value": old,
        "units": sorted(_symbol_unit(t) for t in targets),
    }


def set_lib_symbol_property(
    file_path: str, lib_id: str, property_name: str, value: str
) -> dict:
    """Set a property on a lib_symbols definition (the default for new instances)."""
    root = parse_file(file_path)
    if root.find("lib_symbols") is None:
        return {"updated": False, "error": "no lib_symbols section"}

    lib_sym = _find_lib_symbol(root, lib_id)
    if lib_sym is None:
        return {"updated": False, "error": f"lib_symbol {lib_id!r} not found"}

    old = _set_property_on_node(lib_sym, property_name, value)
    write_file(file_path, root)
    return {"updated": True, "old_value": old}


def list_symbols(file_path: str) -> list[dict]:
    """Return a thin listing of all placed symbols with key fields only."""
    data = read_schematic(file_path)
    return [
        {
            "reference": s.reference,
            "value": s.value,
            "lib_id": s.lib_id,
            "footprint": s.footprint,
            "x": s.position.x,
            "y": s.position.y,
            "rotation": s.rotation,
            "mirror": s.mirror,
            "unit": s.unit,
            "uuid": s.uuid,
        }
        for s in data.symbols
    ]


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

    for sym in root.find_all("symbol"):
        if _symbol_lib_id(sym) == lib_id:
            return {"deleted": False, "reason": "still in use"}

    lib_sym = _find_lib_symbol(root, lib_id)
    if lib_sym is None:
        return {"deleted": False, "reason": "not found"}

    root.find("lib_symbols").remove_child(lib_sym)
    write_file(file_path, root)
    return {"deleted": True, "lib_id": lib_id}


def cleanup_lib_symbols(file_path: str) -> list[str]:
    """Remove all orphaned lib_symbol definitions with no matching instances.

    Returns list of lib_ids that were removed.
    """
    root = parse_file(file_path)

    used_lib_ids = {_symbol_lib_id(sym) for sym in root.find_all("symbol")}

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


def get_pin_positions(
    file_path: str, reference: str, unit: int | None = None
) -> list[dict]:
    """Get actual pin endpoint positions for a component in schematic coordinates.

    Reads each placed unit's position, rotation and mirroring, looks up pin
    offsets from the lib_symbols definition, and applies the transform.
    For multi-unit parts every placed unit is included unless `unit` is given.

    Returns list of {pin_number, pin_name, unit, x, y} in schematic coordinates.
    """
    root = parse_file(file_path)

    result: list[dict] = []
    for sym in _find_symbol_instances(root, reference):
        sym_unit = _symbol_unit(sym)
        if unit is not None and sym_unit != unit:
            continue
        lib_id = _symbol_lib_id(sym)
        if not lib_id:
            continue

        comp_x, comp_y, comp_rot = _read_at(sym)
        mirror = _symbol_mirror(sym)
        lib_pins = _get_lib_symbol_pins(root, lib_id, sym_unit, _symbol_body_style(sym))

        for pin in lib_pins:
            sx, sy = _transform_pin_to_schematic(
                comp_x, comp_y, comp_rot, pin["x"], pin["y"], mirror
            )
            result.append({
                "pin_number": pin["pin_number"],
                "pin_name": pin["pin_name"],
                "unit": sym_unit,
                "x": sx,
                "y": sy,
            })
    return result


def modify_lib_symbol_pin(
    file_path: str, lib_id: str, pin_number: str, pin_type: str
) -> bool:
    """Modify a pin's electrical type in the schematic's lib_symbols section.

    Valid pin_type values are listed in PIN_TYPES. Every pin with the given
    number is changed (a number can appear in several body styles).

    Returns True if the pin was found and modified. Raises ValueError for an
    invalid pin_type.
    """
    if pin_type not in PIN_TYPES:
        raise ValueError(
            f"Invalid pin type {pin_type!r}; valid types: {', '.join(sorted(PIN_TYPES))}."
        )

    root = parse_file(file_path)
    lib_sym = _find_lib_symbol(root, lib_id)
    if lib_sym is None:
        return False

    changed = False
    for sub in lib_sym.find_all("symbol"):
        for pin in sub.find_all("pin"):
            number_node = pin.find("number")
            if number_node and len(number_node.children) >= 2:
                if str(number_node.children[1]) == pin_number and len(pin.children) >= 2:
                    # Pin structure: (pin <type> <shape> (at ...) ...)
                    pin.children[1] = pin_type
                    changed = True

    if changed:
        write_file(file_path, root)
    return changed


_REF_RE = re.compile(r"^(#?[A-Za-z_]+?)(\d+)$")


def annotate(file_path: str) -> dict:
    """Assign reference designators to unannotated symbols (those with '?' in reference).

    Groups by prefix (R, C, U, ...) and assigns the lowest free numbers.
    Units of a multi-unit part are packed together the way KiCad does:
    unannotated symbols with the same lib_id and value share a designator
    until one of their unit numbers repeats (U?A + U?B -> U1A + U1B).

    Returns dict with {prefix: [assigned_refs]} for all changes made.
    """
    root = parse_file(file_path)

    used_refs: dict[str, set[int]] = {}  # prefix -> set of used numbers
    unannotated: list[tuple[SexpList, SexpList, str]] = []  # (symbol, ref_prop, prefix)

    for sym in root.find_all("symbol"):
        for prop in sym.find_all("property"):
            if len(prop.children) >= 3 and str(prop.children[1]) == "Reference":
                ref_str = str(prop.children[2])
                m = _REF_RE.match(ref_str)
                if m:
                    used_refs.setdefault(m.group(1), set()).add(int(m.group(2)))
                elif "?" in ref_str:
                    prefix = ref_str.replace("?", "")
                    used_refs.setdefault(prefix, set())
                    unannotated.append((sym, prop, prefix))
                break

    if not unannotated:
        return {"changes": {}}

    # key -> list of [reference, units already taken]
    open_parts: dict[tuple[str, str, str], list[tuple[str, set[int]]]] = {}
    changes: dict[str, list[str]] = {}

    for sym, prop, prefix in unannotated:
        value = ""
        for p in sym.find_all("property"):
            if len(p.children) >= 3 and str(p.children[1]) == "Value":
                value = str(p.children[2])
        key = (prefix, _symbol_lib_id(sym), value)
        unit = _symbol_unit(sym)

        new_ref = None
        for ref, units in open_parts.get(key, []):
            if unit not in units:
                units.add(unit)
                new_ref = ref
                break
        if new_ref is None:
            used = used_refs[prefix]
            num = 1
            while num in used:
                num += 1
            used.add(num)
            new_ref = f"{prefix}{num}"
            open_parts.setdefault(key, []).append((new_ref, {unit}))
            changes.setdefault(prefix, []).append(new_ref)

        prop.children[2] = QuotedString(new_ref)
        _set_instance_reference(sym, new_ref)

    write_file(file_path, root)
    return {"changes": changes}


# ── Batch operations ────────────────────────────────────────────────────────


def add_power_symbols_batch(
    file_path: str,
    symbols: list[dict],
    snap: bool = True,
) -> list[str]:
    """Add multiple power symbols in a single file read/write cycle.

    Each dict should have: name (str), x (float), y (float),
    and optionally rotation (float, default 0).
    Returns list of UUIDs.
    """
    root = _load_or_create(file_path)
    project = _get_project_name(file_path)

    uuids = [
        _add_power_symbol_to_root(
            root, project, sym["name"], _snap(sym["x"], snap), _snap(sym["y"], snap),
            sym.get("rotation", 0),
        )
        for sym in symbols
    ]

    write_file(file_path, root)
    return uuids


def add_labels_batch(
    file_path: str,
    labels: list[dict],
    snap: bool = True,
) -> list[str]:
    """Add multiple labels in a single file read/write cycle.

    Each label dict should have: name, x, y, and optionally rotation.
    Returns list of UUIDs.
    """
    root = _load_or_create(file_path)

    uuids: list[str] = []
    for lbl in labels:
        node = _label_node(
            lbl["name"], _snap(lbl["x"], snap), _snap(lbl["y"], snap), lbl.get("rotation", 0)
        )
        _insert_item(root, node)
        uuids.append(str(node.find("uuid").children[1]))

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
                        continue
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
