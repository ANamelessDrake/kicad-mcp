"""MCP server entry point — registers all KiCad tools.

Run with: python -m kicad_mcp.server
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from . import cli, jlcpcb, library, pcb, project, schematic


def _configure_logging() -> logging.Logger:
    _logger = logging.getLogger("kicad_mcp")
    level_str = os.environ.get("KICAD_MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_str, logging.INFO)
    _logger.setLevel(level)

    # Log to stderr — MCP uses stdout for JSON-RPC protocol
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    _logger.addHandler(stderr_handler)
    # Don't also emit through the root logger (FastMCP configures one)
    _logger.propagate = False

    # Optional file logging
    log_file = os.environ.get("KICAD_MCP_LOG_FILE")
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        ))
        _logger.addHandler(file_handler)

    return _logger


logger = _configure_logging()

mcp = FastMCP(
    "kicad",
    instructions="KiCad 8 schematic and PCB design tools",
)


# ── Schematic Tools ─────────────────────────────────────────────────────────


@mcp.tool()
def schematic_read(file_path: str) -> dict:
    """Read and parse a .kicad_sch file.

    Returns structured JSON with components (ref, value, footprint, position, pins),
    wires, and labels.
    """
    logger.info("schematic_read: %s", file_path)
    data = schematic.read_schematic(file_path)
    return asdict(data)


@mcp.tool()
def schematic_place_symbol(
    file_path: str,
    lib_id: str,
    reference: str,
    value: str,
    footprint: str,
    x: float,
    y: float,
    rotation: float = 0,
    unit: int = 1,
) -> dict:
    """Place a component symbol in the schematic.

    Args:
        file_path: Path to the .kicad_sch file (created if it doesn't exist).
        lib_id: KiCad library symbol ID (e.g., "Device:R", "Device:C").
        reference: Reference designator (e.g., "R1", "C1").
        value: Component value (e.g., "10K", "100nF").
        footprint: Footprint library ID (e.g., "Resistor_SMD:R_0603_1608Metric").
        x: X position in mm (snapped to the 1.27 mm grid).
        y: Y position in mm (snapped to the 1.27 mm grid).
        rotation: Rotation in degrees, counterclockwise (default 0).
        unit: Unit to place for multi-unit parts (e.g. 2 for the second op-amp
              of an LM358). Place each unit with the same reference.

    Fails if the symbol isn't in the schematic or KiCad's libraries, or if
    the same reference and unit are already placed.

    Returns the UUID of the placed symbol.
    """
    logger.info("schematic_place_symbol: %s %s at (%s,%s)", lib_id, reference, x, y)
    uuid = schematic.place_symbol(
        file_path, lib_id, reference, value, footprint, x, y, rotation, unit
    )
    return {"uuid": uuid}


@mcp.tool()
def schematic_add_wire(
    file_path: str, points: list[list[float]], snap: bool = True
) -> dict:
    """Add a wire between a series of (x, y) points.

    Args:
        file_path: Path to the .kicad_sch file.
        points: List of [x, y] coordinate pairs in mm.
        snap: Snap coordinates to the 1.27 mm grid (default true). Pass false
              when targeting off-grid pins reported by schematic_get_pin_positions.

    Returns the UUID of the last wire segment.
    """
    pts = [(p[0], p[1]) for p in points]
    uuid = schematic.add_wire(file_path, pts, snap)
    return {"uuid": uuid}


@mcp.tool()
def schematic_add_label(
    file_path: str, name: str, x: float, y: float, rotation: float = 0, snap: bool = True
) -> dict:
    """Add a net label at a position in the schematic.

    Args:
        file_path: Path to the .kicad_sch file.
        name: Net name (e.g., "3V3", "SDA").
        x: X position in mm.
        y: Y position in mm.
        rotation: Rotation in degrees (default 0).
        snap: Snap coordinates to the 1.27 mm grid (default true). Pass false
              when targeting off-grid pins reported by schematic_get_pin_positions.

    Returns the UUID of the label.
    """
    uuid = schematic.add_label(file_path, name, x, y, rotation, snap)
    return {"uuid": uuid}


@mcp.tool()
def schematic_add_power_symbol(
    file_path: str, name: str, x: float, y: float, rotation: float = 0, snap: bool = True
) -> dict:
    """Add a power port symbol (GND, +3V3, +5V, etc.) at a position.

    Args:
        file_path: Path to the .kicad_sch file.
        name: Symbol name in KiCad's power library (e.g., "GND", "+3V3", "+5V").
        x: X position in mm.
        y: Y position in mm.
        rotation: Rotation in degrees (default 0).
        snap: Snap coordinates to the 1.27 mm grid (default true). Pass false
              when targeting off-grid pins reported by schematic_get_pin_positions.

    Returns the UUID of the power symbol.
    """
    uuid = schematic.add_power_symbol(file_path, name, x, y, rotation, snap)
    return {"uuid": uuid}


@mcp.tool()
def schematic_add_global_label(
    file_path: str,
    name: str,
    x: float,
    y: float,
    rotation: float = 0,
    snap: bool = True,
    shape: str = "input",
) -> dict:
    """Add a global label for inter-sheet connectivity.

    Args:
        file_path: Path to the .kicad_sch file.
        name: Global label name.
        x: X position in mm.
        y: Y position in mm.
        rotation: Rotation in degrees (default 0).
        snap: Snap coordinates to the 1.27 mm grid (default true). Pass false
              when targeting off-grid pins reported by schematic_get_pin_positions.
        shape: input, output, bidirectional, tri_state or passive (default input).

    Returns the UUID of the global label.
    """
    uuid = schematic.add_global_label(file_path, name, x, y, rotation, snap, shape)
    return {"uuid": uuid}


@mcp.tool()
def schematic_delete(file_path: str, uuid: str) -> dict:
    """Delete any schematic element by UUID.

    Args:
        file_path: Path to the .kicad_sch file.
        uuid: UUID of the element to delete.

    Returns whether the element was found and deleted.
    """
    found = schematic.delete_by_uuid(file_path, uuid)
    return {"deleted": found}


@mcp.tool()
def schematic_add_labels(file_path: str, labels: list[dict], snap: bool = True) -> dict:
    """Add multiple net labels in a single operation (one file read/write cycle).

    Args:
        file_path: Path to the .kicad_sch file.
        labels: List of label dicts, each with keys: name (str), x (float), y (float),
                and optionally rotation (float, default 0).
        snap: Snap coordinates to the 1.27 mm grid (default true). Pass false
              when targeting off-grid pins reported by schematic_get_pin_positions.

    Returns JSON with list of UUIDs.
    """
    logger.info("schematic_add_labels: %d labels", len(labels))
    uuids = schematic.add_labels_batch(file_path, labels, snap)
    return {"uuids": uuids, "count": len(uuids)}


@mcp.tool()
def schematic_delete_many(file_path: str, uuids: list[str]) -> dict:
    """Delete multiple schematic elements by UUID in a single operation.

    Args:
        file_path: Path to the .kicad_sch file.
        uuids: List of UUIDs to delete.

    Returns JSON with list of booleans indicating which were found and deleted.
    """
    logger.info("schematic_delete_many: %d uuids", len(uuids))
    results = schematic.delete_many(file_path, uuids)
    return {"results": results, "deleted_count": sum(results)}


@mcp.tool()
def schematic_add_power_symbols(
    file_path: str, symbols: list[dict], snap: bool = True
) -> dict:
    """Add multiple power symbols in a single operation (one file read/write cycle).

    Args:
        file_path: Path to the .kicad_sch file.
        symbols: List of dicts, each with keys: name (str, e.g. "GND", "+3V3"),
                 x (float), y (float), and optionally rotation (float, default 0).
        snap: Snap coordinates to the 1.27 mm grid (default true). Pass false
              when targeting off-grid pins reported by schematic_get_pin_positions.

    Returns JSON with list of UUIDs.
    """
    logger.info("schematic_add_power_symbols: %d symbols", len(symbols))
    uuids = schematic.add_power_symbols_batch(file_path, symbols, snap)
    return {"uuids": uuids, "count": len(uuids)}


@mcp.tool()
def schematic_get_pin_positions(
    file_path: str, reference: str, unit: int | None = None
) -> dict:
    """Get the actual pin endpoint positions for a component in schematic coordinates.

    Reads the component's position, rotation and mirroring, looks up pin
    offsets from the lib_symbols definition, and applies the same transform
    KiCad does. Use this to find the exact coordinates where labels and wires
    should connect to a component.

    Args:
        file_path: Path to the .kicad_sch file.
        reference: Reference designator (e.g., "R1", "U1", "J1").
        unit: For multi-unit parts, only this unit (default: all placed units).

    Returns JSON list of {pin_number, pin_name, unit, x, y} for each pin.
    """
    logger.info("schematic_get_pin_positions: %s in %s", reference, file_path)
    pins = schematic.get_pin_positions(file_path, reference, unit)
    return {"pins": pins, "count": len(pins)}


@mcp.tool()
def schematic_add_no_connect(
    file_path: str, x: float, y: float, snap: bool = True
) -> dict:
    """Add a no-connect (X) flag at a pin position.

    Args:
        file_path: Path to the .kicad_sch file.
        x: X position in mm.
        y: Y position in mm.
        snap: Snap coordinates to the 1.27 mm grid (default true). Pass false
              when targeting off-grid pins reported by schematic_get_pin_positions.

    Returns the UUID of the no-connect flag.
    """
    uuid = schematic.add_no_connect(file_path, x, y, snap)
    return {"uuid": uuid}


@mcp.tool()
def schematic_add_no_connects(
    file_path: str, positions: list[dict], snap: bool = True
) -> dict:
    """Add multiple no-connect flags in a single operation.

    Args:
        file_path: Path to the .kicad_sch file.
        positions: List of dicts with keys: x (float), y (float).
        snap: Snap coordinates to the 1.27 mm grid (default true). Pass false
              when targeting off-grid pins reported by schematic_get_pin_positions.

    Returns JSON with list of UUIDs.
    """
    logger.info("schematic_add_no_connects: %d positions", len(positions))
    uuids = schematic.add_no_connects_batch(file_path, positions, snap)
    return {"uuids": uuids, "count": len(uuids)}


@mcp.tool()
def schematic_modify_lib_symbol_pin(
    file_path: str, lib_id: str, pin_number: str, pin_type: str
) -> dict:
    """Modify a pin's electrical type in the schematic's lib_symbols section.

    Use this to fix ERC conflicts by changing a pin's type (e.g., changing an
    output pin to passive to resolve a conflict with another output).

    Args:
        file_path: Path to the .kicad_sch file.
        lib_id: Library symbol ID (e.g., "Interface_Optical:TSOP382xx").
        pin_number: Pin number to modify (e.g., "1").
        pin_type: New electrical type. Valid values: input, output, bidirectional,
                  tri_state, passive, free, unspecified, power_in, power_out,
                  open_collector, open_emitter, no_connect.

    Returns whether the pin was found and modified; an invalid pin_type is an error.
    """
    logger.info("schematic_modify_lib_symbol_pin: %s pin %s -> %s", lib_id, pin_number, pin_type)
    ok = schematic.modify_lib_symbol_pin(file_path, lib_id, pin_number, pin_type)
    return {"modified": ok}


@mcp.tool()
def schematic_annotate(file_path: str) -> dict:
    """Assign reference designators to unannotated symbols.

    Finds all symbols with '?' in their reference (e.g., R?, C?, U?), and assigns
    sequential numbers per prefix, avoiding numbers already in use. Units of a
    multi-unit part (same lib_id and value) share one designator, like KiCad.

    Args:
        file_path: Path to the .kicad_sch file.

    Returns JSON with changes made, e.g. {"changes": {"R": ["R1", "R2"], "C": ["C1"]}}.
    """
    logger.info("schematic_annotate: %s", file_path)
    result = schematic.annotate(file_path)
    return result


@mcp.tool()
def schematic_move_symbol(
    file_path: str,
    reference: str,
    x: float,
    y: float,
    rotation: float | None = None,
    unit: int | None = None,
) -> dict:
    """Move an existing schematic symbol to a new position.

    Args:
        file_path: Path to the .kicad_sch file.
        reference: Reference designator (e.g., "U1", "R1").
        x: New X position in mm (snapped to 1.27mm grid).
        y: New Y position in mm (snapped to 1.27mm grid).
        rotation: New rotation in degrees (optional, keeps current if not specified).
        unit: Which unit to move, required when a multi-unit part has several
              placed units.

    The symbol's fields (reference, value text) move with it.

    Returns whether the symbol was found and moved.
    """
    ok = schematic.move_symbol(file_path, reference, x, y, rotation, unit)
    return {"moved": ok}


@mcp.tool()
def schematic_add_lib_symbol(
    file_path: str,
    lib_id: str,
    pins: list[dict],
    rectangle: dict | None = None,
    properties: dict | None = None,
) -> dict:
    """Add a custom symbol definition to the schematic's lib_symbols section.

    Use this for components not in KiCad's standard libraries. After adding,
    use schematic_place_symbol to place instances referencing the same lib_id.

    Args:
        file_path: Path to the .kicad_sch file.
        lib_id: Library ID (e.g., "RF:CC1101").
        pins: List of pin dicts, each with:
              - number (str): Pin number
              - name (str): Pin name
              - type (str): Electrical type: input, output, bidirectional, tri_state,
                passive, free, unspecified, power_in, power_out, open_collector,
                open_emitter or no_connect
              - x (float): X position relative to symbol center
              - y (float): Y position relative to symbol center
              - rotation (float): Pin rotation in degrees (0=right, 90=up, 180=left, 270=down)
        rectangle: Optional body rectangle {x1, y1, x2, y2} for the symbol outline.
        properties: Optional dict of property name -> value (Reference, Value, Footprint, etc.).

    Returns {added: true}, or {added: false} if the lib_id already exists.
    Invalid pin definitions are an error.
    """
    logger.info("schematic_add_lib_symbol: %s with %d pins", lib_id, len(pins))
    ok = schematic.add_lib_symbol(file_path, lib_id, pins, rectangle, properties)
    return {"added": ok}


@mcp.tool()
def schematic_delete_lib_symbol(file_path: str, lib_id: str) -> dict:
    """Delete an unused symbol definition from the schematic's lib_symbols section.

    Refuses to delete if any symbol instances still reference it.

    Args:
        file_path: Path to the .kicad_sch file.
        lib_id: Library ID to delete (e.g., "Connector_Generic:Conn_01x08").

    Returns {deleted: true, lib_id} or {deleted: false, reason}.
    """
    result = schematic.delete_lib_symbol(file_path, lib_id)
    return result


@mcp.tool()
def schematic_cleanup_lib_symbols(file_path: str) -> dict:
    """Remove all orphaned lib_symbol definitions with no matching instances.

    Finds lib_symbols that are defined but not referenced by any placed symbol,
    and removes them. Useful after deleting components to clean up phantom ERC errors.

    Args:
        file_path: Path to the .kicad_sch file.

    Returns JSON with list of removed lib_ids.
    """
    logger.info("schematic_cleanup_lib_symbols: %s", file_path)
    removed = schematic.cleanup_lib_symbols(file_path)
    return {"removed": removed, "count": len(removed)}


@mcp.tool()
def schematic_set_symbol_property(
    file_path: str,
    reference: str,
    property_name: str,
    value: str,
    unit: int | None = None,
) -> dict:
    """Set a property on a placed symbol instance without disturbing pins/wires.

    Looks up the symbol by reference and replaces the named property's value.
    Preserves the property's position and effects. Creates the property if it
    doesn't exist (hidden by default).

    If changing "Reference", the (instances ... reference ...) block is also
    updated to match.

    Args:
        file_path: Path to the .kicad_sch file.
        reference: Reference designator (e.g., "U4", "R1").
        property_name: "Footprint", "Value", "Reference", "Datasheet",
                       "Description", or any custom field.
        value: New value.
        unit: For multi-unit symbols, change only this unit. By default the change
              applies to all placed units; Reference always applies to all units.

    Returns {updated, old_value, units} or {updated: false, error, [units]}.
    """
    logger.info("schematic_set_symbol_property: %s.%s = %r", reference, property_name, value)
    result = schematic.set_symbol_property(file_path, reference, property_name, value, unit)
    return result


@mcp.tool()
def schematic_set_lib_symbol_property(
    file_path: str,
    lib_id: str,
    property_name: str,
    value: str,
) -> dict:
    """Set a property on a lib_symbol definition (the default for new instances).

    Args:
        file_path: Path to the .kicad_sch file.
        lib_id: Library symbol ID (e.g., "Custom:AM32_ESC").
        property_name: Property name to set.
        value: New value.

    Returns {updated, old_value} or {updated: false, error}.
    """
    logger.info("schematic_set_lib_symbol_property: %s.%s = %r", lib_id, property_name, value)
    result = schematic.set_lib_symbol_property(file_path, lib_id, property_name, value)
    return result


@mcp.tool()
def schematic_list_symbols(file_path: str) -> dict:
    """Return a thin listing of all placed symbols (no pin details).

    Cheaper than schematic_read when you only need {reference, value, lib_id,
    footprint, x, y, rotation, unit, uuid} per symbol.

    Args:
        file_path: Path to the .kicad_sch file.

    Returns JSON list of symbol summaries.
    """
    symbols = schematic.list_symbols(file_path)
    return {"symbols": symbols, "count": len(symbols)}


@mcp.tool()
def schematic_rename_label(file_path: str, old_name: str, new_name: str) -> dict:
    """Rename all labels matching old_name to new_name, preserving UUIDs.

    Affects both local labels and global labels.

    Args:
        file_path: Path to the .kicad_sch file.
        old_name: Current label text to match.
        new_name: New label text.

    Returns {renamed: int} with the count of labels renamed.
    """
    logger.info("schematic_rename_label: %r -> %r", old_name, new_name)
    result = schematic.rename_label(file_path, old_name, new_name)
    return result


@mcp.tool()
def schematic_run_erc(file_path: str) -> dict:
    """Run Electrical Rules Check (ERC) on a schematic using kicad-cli.

    Returns {violations: [{severity, message, location}], count}.
    Requires KiCad 8 to be installed.
    """
    violations = cli.run_erc(file_path)
    return {"violations": [asdict(v) for v in violations], "count": len(violations)}


# ── PCB Tools ────────────────────────────────────────────────────────────────


@mcp.tool()
def pcb_read(file_path: str) -> dict:
    """Read and parse a .kicad_pcb file.

    Returns structured JSON with footprints (ref, position, rotation, layer, pad nets),
    traces, vias, zones, board outline, and net list.
    """
    logger.info("pcb_read: %s", file_path)
    data = pcb.read_pcb(file_path)
    return asdict(data)


@mcp.tool()
def pcb_place_footprint(
    file_path: str,
    footprint_lib: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    rotation: float = 0,
    layer: str = "F.Cu",
) -> dict:
    """Place a footprint on the PCB.

    Args:
        file_path: Path to the .kicad_pcb file (created if it doesn't exist).
        footprint_lib: Footprint library ID (e.g., "Resistor_SMD:R_0603_1608Metric").
        reference: Reference designator (e.g., "R1").
        value: Component value (e.g., "10K").
        x: X position in mm.
        y: Y position in mm.
        rotation: Footprint orientation in degrees (default 0).
        layer: "F.Cu" or "B.Cu" (default "F.Cu"); back-side parts are mirrored
               like KiCad's flip.

    Fails if the footprint isn't in KiCad's libraries or the reference exists.

    Returns the UUID of the placed footprint.
    """
    logger.info("pcb_place_footprint: %s %s at (%s,%s)", footprint_lib, reference, x, y)
    uuid = pcb.place_footprint(file_path, footprint_lib, reference, value, x, y, rotation, layer)
    return {"uuid": uuid}


@mcp.tool()
def pcb_move_footprint(
    file_path: str, reference: str, x: float, y: float, rotation: float | None = None
) -> dict:
    """Move an existing footprint to a new position.

    Args:
        file_path: Path to the .kicad_pcb file.
        reference: Reference designator of the footprint to move.
        x: New X position in mm.
        y: New Y position in mm.
        rotation: New rotation in degrees (optional, keeps current if not specified).

    Returns whether the footprint was found and moved.
    """
    found = pcb.move_footprint(file_path, reference, x, y, rotation)
    return {"moved": found}


@mcp.tool()
def pcb_add_trace(
    file_path: str,
    net_name: str,
    layer: str,
    width: float,
    points: list[list[float]],
) -> dict:
    """Add copper trace segments between points.

    Args:
        file_path: Path to the .kicad_pcb file.
        net_name: Net name for the trace.
        layer: Copper layer (e.g., "F.Cu", "B.Cu").
        width: Trace width in mm.
        points: List of [x, y] coordinate pairs in mm.

    Returns the UUID of the last trace segment.
    """
    pts = [(p[0], p[1]) for p in points]
    uuid = pcb.add_trace(file_path, net_name, layer, width, pts)
    return {"uuid": uuid}


@mcp.tool()
def pcb_add_via(
    file_path: str,
    net_name: str,
    x: float,
    y: float,
    size: float = 0.8,
    drill: float = 0.4,
) -> dict:
    """Add a via at a position.

    Args:
        file_path: Path to the .kicad_pcb file.
        net_name: Net name for the via.
        x: X position in mm.
        y: Y position in mm.
        size: Via pad size in mm (default 0.8).
        drill: Via drill size in mm (default 0.4).

    Returns the UUID of the via.
    """
    uuid = pcb.add_via(file_path, net_name, x, y, size, drill)
    return {"uuid": uuid}


@mcp.tool()
def pcb_add_zone(
    file_path: str,
    net_name: str,
    layer: str,
    outline_points: list[list[float]],
    fill_type: str = "solid",
) -> dict:
    """Add a copper zone/pour.

    Args:
        file_path: Path to the .kicad_pcb file.
        net_name: Net name for the zone (e.g., "GND").
        layer: Copper layer (e.g., "B.Cu").
        outline_points: List of [x, y] coordinate pairs defining the zone boundary.
        fill_type: "solid" (default) or "hatch".

    Returns the UUID of the zone.
    """
    pts = [(p[0], p[1]) for p in outline_points]
    uuid = pcb.add_zone(file_path, net_name, layer, pts, fill_type)
    return {"uuid": uuid}


@mcp.tool()
def pcb_set_board_outline(file_path: str, outline_points: list[list[float]]) -> dict:
    """Set the board outline on the Edge.Cuts layer.

    Args:
        file_path: Path to the .kicad_pcb file.
        outline_points: List of [x, y] coordinate pairs defining the board boundary.

    Returns whether the outline was set successfully.
    """
    pts = [(p[0], p[1]) for p in outline_points]
    ok = pcb.set_board_outline(file_path, pts)
    return {"success": ok}


@mcp.tool()
def pcb_assign_net_to_pad(
    file_path: str, footprint_ref: str, pad_number: str, net_name: str
) -> dict:
    """Assign a net to a specific pad on a footprint.

    Args:
        file_path: Path to the .kicad_pcb file.
        footprint_ref: Reference designator of the footprint.
        pad_number: Pad number (e.g., "1", "2").
        net_name: Net name to assign.

    Returns whether the pad was found and updated.
    """
    ok = pcb.assign_net_to_pad(file_path, footprint_ref, pad_number, net_name)
    return {"assigned": ok}


@mcp.tool()
def pcb_add_mounting_hole(
    file_path: str, x: float, y: float, drill_size: float = 3.2, pad_size: float | None = None
) -> dict:
    """Add a plated mounting hole footprint (references H1, H2, ...).

    Uses KiCad's MountingHole library footprint when one matches (a 3.2 mm
    drill gives MountingHole_3.2mm_M3_Pad), otherwise generates one.

    Args:
        file_path: Path to the .kicad_pcb file.
        x: X position in mm.
        y: Y position in mm.
        drill_size: Drill diameter in mm (default 3.2).
        pad_size: Pad diameter in mm (default: the library footprint's, or 2x drill).

    Returns the UUID of the mounting hole.
    """
    uuid = pcb.add_mounting_hole(file_path, x, y, drill_size, pad_size)
    return {"uuid": uuid}


@mcp.tool()
def pcb_place_footprint_array(
    file_path: str,
    footprint_lib: str,
    reference_prefix: str,
    value: str,
    count: int,
    pattern: str = "grid",
    start_x: float = 50.0,
    start_y: float = 50.0,
    spacing_x: float = 5.0,
    spacing_y: float = 5.0,
    columns: int | None = None,
    radius: float = 20.0,
    rotation: float = 0,
    layer: str = "F.Cu",
    start_index: int = 1,
) -> dict:
    """Place an array of identical footprints in a grid or circular pattern.

    Args:
        file_path: Path to .kicad_pcb file.
        footprint_lib: Footprint library ID (e.g., "Resistor_SMD:R_0603_1608Metric").
        reference_prefix: Reference prefix (e.g., "R" produces R1, R2, ...).
        value: Component value for all instances.
        count: Number of footprints to place.
        pattern: "grid" or "circular" (default "grid").
        start_x: Origin X for grid, or center X for circular.
        start_y: Origin Y for grid, or center Y for circular.
        spacing_x: Grid X spacing in mm (grid only).
        spacing_y: Grid Y spacing in mm (grid only).
        columns: Grid columns before wrapping (default: single row).
        radius: Circle radius in mm (circular only).
        rotation: Base rotation in degrees.
        layer: Placement layer (default "F.Cu").
        start_index: Starting reference number (default 1).

    Returns JSON with list of UUIDs.
    """
    logger.info("pcb_place_footprint_array: %s x%d %s", footprint_lib, count, pattern)
    uuids = pcb.place_footprint_array(
        file_path, footprint_lib, reference_prefix, value, count,
        pattern, start_x, start_y, spacing_x, spacing_y, columns,
        radius, rotation, layer, start_index,
    )
    return {"uuids": uuids, "count": len(uuids)}


@mcp.tool()
def project_list_net_classes(file_path: str) -> dict:
    """List net classes and their net-name patterns from the project file.

    Args:
        file_path: Path to the .kicad_pro, .kicad_pcb or .kicad_sch file.

    Returns {classes: [{name, clearance, track_width, via_diameter, via_drill, ...}],
    patterns: [{netclass, pattern}]}.
    """
    return project.list_net_classes(file_path)


@mcp.tool()
def project_set_net_class(
    file_path: str,
    name: str,
    nets: list[str] | None = None,
    clearance: float | None = None,
    track_width: float | None = None,
    via_diameter: float | None = None,
    via_drill: float | None = None,
) -> dict:
    """Create or update a net class and assign nets to it (mm).

    A new class starts as a copy of Default. Nets are matched by exact name
    (as they appear on the board, e.g. "/VBAT", "GND") and removed from other
    classes. Passing nets replaces the class's list; omitting it keeps it.
    Freerouting and DRC use these widths and clearances.

    Args:
        file_path: Path to the .kicad_pro, .kicad_pcb or .kicad_sch file.
        name: Net class name (e.g. "Power").
        nets: Net names to assign to this class.
        clearance, track_width, via_diameter, via_drill: Values in mm.

    Returns the class settings and its nets.
    """
    logger.info("project_set_net_class: %s nets=%s", name, nets)
    return project.set_net_class(
        file_path, name, nets, clearance, track_width, via_diameter, via_drill,
    )


@mcp.tool()
def project_set_design_rules(file_path: str, rules: dict[str, float]) -> dict:
    """Set board-wide constraints (KiCad Board Setup > Constraints), in mm.

    Valid keys: min_clearance, min_connection, min_copper_edge_clearance,
    min_hole_clearance, min_hole_to_hole, min_microvia_diameter,
    min_microvia_drill, min_resolved_spokes, min_silk_clearance,
    min_text_height, min_text_thickness, min_through_hole_diameter,
    min_track_width, min_via_annular_width, min_via_diameter,
    solder_mask_to_copper_clearance.

    Args:
        file_path: Path to the .kicad_pro, .kicad_pcb or .kicad_sch file.
        rules: Mapping of rule key to value.

    Returns the full set of stored constraints.
    """
    logger.info("project_set_design_rules: %s", rules)
    return project.set_design_rules(file_path, rules)


@mcp.tool()
def project_set_custom_rules(file_path: str, rules: str) -> dict:
    """Write the project's custom DRC rules file (<project>.kicad_dru).

    Uses KiCad's custom rule syntax; replaces the whole file. Example:
    (rule "U1 thermal vias" (condition "A.memberOfFootprint('U1')")
      (constraint hole_size (min 0.2mm)))
    Board constraints set with project_set_design_rules are hard floors that
    rules cannot go below: to allow a smaller value for one part, lower the
    floor and add a rule restoring it elsewhere, e.g.
    (condition "!A.memberOfFootprint('U1')") (constraint hole_size (min 0.3mm)).

    Args:
        file_path: Path to the .kicad_pro, .kicad_pcb or .kicad_sch file.
        rules: Rule definitions; "(version 1)" is prepended if missing.

    Returns the path written.
    """
    logger.info("project_set_custom_rules")
    return {"path": project.set_custom_rules(file_path, rules)}


@mcp.tool()
def pcb_fill_zones(file_path: str) -> dict:
    """Fill all copper zones using KiCad's zone filler and save the board.

    Zone outlines added with pcb_add_zone have no copper until filled. Run
    this before DRC or Gerber export (pcb_export_manufacturing refills
    automatically). Requires the pcbnew Python module (KiCad 8).

    Args:
        file_path: Path to the .kicad_pcb file.

    Returns {filled_zones: count}.
    """
    logger.info("pcb_fill_zones: %s", file_path)
    return cli.fill_zones(file_path)


@mcp.tool()
def pcb_set_copper_layers(
    file_path: str, count: int, power_layers: list[str] | None = None,
) -> dict:
    """Set the copper layer count (2, 4, 6, ...) and mark power-plane layers.

    Uses KiCad's pcbnew API, which also writes the layer table and stackup.
    Layers in power_layers (e.g. ["In1.Cu"]) become type "power": Freerouting
    keeps signal traces off them and lets vias pass through. Add a zone on
    that layer (pcb_add_zone) for the plane itself. Requires pcbnew (KiCad 8).

    Args:
        file_path: Path to the .kicad_pcb file.
        count: Number of copper layers (even, >= 2).
        power_layers: Inner layers to mark as power planes.

    Returns {copper_layers, power_layers}.
    """
    logger.info("pcb_set_copper_layers: %s count=%s power=%s", file_path, count, power_layers)
    return cli.set_copper_layers(file_path, count, power_layers)


@mcp.tool()
def pcb_set_zone_net(file_path: str, zone_uuid: str, net_name: str) -> dict:
    """Assign a net to an existing copper zone.

    Args:
        file_path: Path to the .kicad_pcb file.
        zone_uuid: UUID of the zone to update.
        net_name: Net name to assign (e.g., "GND").

    Returns whether the zone was found and updated.
    """
    logger.info("pcb_set_zone_net: zone %s -> %s", zone_uuid, net_name)
    ok = pcb.set_zone_net(file_path, zone_uuid, net_name)
    return {"updated": ok}


@mcp.tool()
def pcb_flip_footprint(
    file_path: str, reference: str, to_layer: str = "B.Cu"
) -> dict:
    """Flip a footprint to the opposite side of the board.

    Behaves like pressing F in KiCad: mirrors pads, graphics and text,
    adjusts the orientation, and swaps all front/back layers.

    Args:
        file_path: Path to the .kicad_pcb file.
        reference: Reference designator of the footprint to flip.
        to_layer: Target layer (default "B.Cu").

    Returns whether the footprint was found and flipped.
    """
    logger.info("pcb_flip_footprint: %s -> %s", reference, to_layer)
    ok = pcb.flip_footprint(file_path, reference, to_layer)
    return {"flipped": ok}


@mcp.tool()
def pcb_autoroute(
    file_path: str,
    freerouting_jar: str | None = None,
    timeout: int = 300,
    strategy: str = "freerouting",
    max_passes: int = 20,
    ignore_net_classes: list[str] | None = None,
) -> dict:
    """Route a PCB automatically.

    Strategies:
    - "freerouting" (default): Freerouting (requires pcbnew Python module + Java).
      Errors are reported; there is no silent fallback. "auto" is an alias.
    - "simple": rough L-shaped routing with no obstacle avoidance. Expect DRC
      errors; use it only as a starting point.

    The simple router chains each net's pads with L-shaped tracks on a layer
    both pads reach. If a GND zone exists, surface-mount GND pads get a short
    stub and a via next to the pad. Power nets use 0.4mm tracks, signals 0.25mm.

    Args:
        file_path: Path to the .kicad_pcb file.
        freerouting_jar: Path to freerouting.jar (auto-downloaded if not specified).
        timeout: Max seconds for Freerouting (default 300).
        strategy: "freerouting" or "simple".
        max_passes: Freerouting optimisation passes (default 20).
        ignore_net_classes: Net classes Freerouting leaves unrouted, e.g.
            ["GND"] when pours and stitching vias connect ground. Copper
            zones are removed from the exported DSN automatically (Freerouting
            treats them as obstacles); refill zones after routing.

    Returns status JSON with method used and routing statistics.
    """
    logger.info("pcb_autoroute: %s strategy=%s", file_path, strategy)
    result = pcb.autoroute(file_path, freerouting_jar, timeout, strategy,
                           max_passes, ignore_net_classes)
    logger.info("pcb_autoroute: completed via %s", result.get("method", "unknown"))
    return result


@mcp.tool()
def pcb_delete_footprint(
    file_path: str, reference: str | None = None, uuid: str | None = None
) -> dict:
    """Delete a footprint from the PCB by reference or UUID.

    Args:
        file_path: Path to the .kicad_pcb file.
        reference: Reference designator (e.g., "J1", "C2"). Optional if uuid provided.
        uuid: UUID of the footprint. Optional if reference provided.

    Returns whether the footprint was found and deleted.
    """
    result = pcb.delete_footprint(file_path, reference=reference, target_uuid=uuid)
    return result


@mcp.tool()
def pcb_delete_footprints(
    file_path: str,
    references: list[str] | None = None,
    uuids: list[str] | None = None,
) -> dict:
    """Delete multiple footprints in a single operation.

    Args:
        file_path: Path to the .kicad_pcb file.
        references: List of reference designators to delete.
        uuids: List of UUIDs to delete.

    Returns one result per requested reference and UUID, plus deleted_count.
    """
    results = pcb.delete_footprints_batch(file_path, references, uuids)
    deleted_count = sum(1 for r in results if r.get("deleted"))
    return {"results": results, "deleted_count": deleted_count}


@mcp.tool()
def pcb_delete_traces(file_path: str, uuids: list[str]) -> dict:
    """Delete multiple traces (segments) by UUID in a single operation.

    Args:
        file_path: Path to the .kicad_pcb file.
        uuids: List of trace segment UUIDs to delete.

    Returns list of booleans indicating which were found and deleted.
    """
    results = pcb.delete_elements_batch(file_path, uuids, element_type="segment")
    return {"results": results, "deleted_count": sum(results)}


@mcp.tool()
def pcb_delete_vias(file_path: str, uuids: list[str]) -> dict:
    """Delete multiple vias by UUID in a single operation.

    Args:
        file_path: Path to the .kicad_pcb file.
        uuids: List of via UUIDs to delete.

    Returns list of booleans indicating which were found and deleted.
    """
    results = pcb.delete_elements_batch(file_path, uuids, element_type="via")
    return {"results": results, "deleted_count": sum(results)}


@mcp.tool()
def pcb_run_drc(file_path: str) -> dict:
    """Run Design Rules Check (DRC) on a PCB using kicad-cli.

    Returns {violations: [{severity, message, location}], count}.
    Requires KiCad 8 to be installed.
    """
    violations = cli.run_drc(file_path)
    return {"violations": [asdict(v) for v in violations], "count": len(violations)}


@mcp.tool()
def pcb_export_image(
    file_path: str,
    output_path: str,
    layers: list[str] | None = None,
    dpi: int = 300,
) -> dict:
    """Export a PCB image (SVG) for visual review.

    Args:
        file_path: Path to the .kicad_pcb file.
        output_path: Output SVG file path.
        layers: List of layers to include (default: F.Cu, B.Cu, Edge.Cuts).
        dpi: Resolution in DPI (default 300).

    Returns the output file path.
    """
    path = cli.export_pcb_image(file_path, output_path, layers, dpi)
    return {"output_path": path}


@mcp.tool()
def pcb_export_3d(file_path: str, output_path: str, format: str = "step") -> dict:
    """Export 3D model of the PCB.

    Args:
        file_path: Path to the .kicad_pcb file.
        output_path: Output file path.
        format: Export format — "step" or "vrml" (default "step").

    Returns the output file path.
    """
    path = cli.export_3d(file_path, output_path, format)
    return {"output_path": path}


@mcp.tool()
def pcb_export_gerbers(file_path: str, output_dir: str) -> dict:
    """Export Gerber and drill files for manufacturing.

    Args:
        file_path: Path to the .kicad_pcb file.
        output_dir: Output directory for Gerber files.

    Returns a list of generated file paths.
    """
    files = cli.export_gerbers(file_path, output_dir)
    return {"files": files}


@mcp.tool()
def pcb_export_manufacturing(
    file_path: str,
    output_dir: str,
    format: str = "jlcpcb",
    bom_path: str | None = None,
    exclude_refs: list[str] | None = None,
) -> dict:
    """Export all manufacturing files for a fab house in one call.

    Generates Gerbers, drill files, component placement (CPL), and BOM,
    then zips everything into manufacturing.zip.

    For JLCPCB format:
    - CPL with columns: Designator, Mid X, Mid Y, Rotation, Layer, in the same
      coordinate frame as the Gerbers
    - BOM with columns: Comment, Designator, Footprint, LCSC Part Number

    Only files written by this run are zipped; leftovers in output_dir are ignored.

    Args:
        file_path: Path to the .kicad_pcb file.
        output_dir: Output directory for all manufacturing files.
        format: "jlcpcb" (default), "pcbway", or "raw" (Gerbers + drill only).
        bom_path: Optional path to a BOM CSV with LCSC part numbers.
        exclude_refs: Designators to leave out of the CPL and BOM (bare wire
            pads, hand-soldered parts).

    Copper zones are refilled (and the board saved) before plotting. The BOM
    only lists parts present in the CPL; through-hole parts, which the position
    file omits, are reported in bom_dropped. Rows without an LCSC number are
    reported in bom_missing_lcsc.

    Returns JSON with generated files, zip path, bom_dropped, bom_missing_lcsc.
    """
    logger.info("pcb_export_manufacturing: %s format=%s", file_path, format)
    result = cli.export_manufacturing(file_path, output_dir, format, bom_path,
                                      exclude_refs=exclude_refs)
    return result


# ── Utility Tools ────────────────────────────────────────────────────────────


@mcp.tool()
def list_symbols(query: str) -> dict:
    """Search KiCad's symbol libraries for a component.

    Args:
        query: Search query (e.g., "2N7000", "resistor", "STM32").

    Returns a list of matching library symbol IDs.
    """
    results = library.list_library_symbols(query)
    return {"symbols": results}


@mcp.tool()
def list_footprints(query: str) -> dict:
    """Search KiCad's footprint libraries.

    Args:
        query: Search query (e.g., "R_0603", "QFP", "SOT-23").

    Returns a list of matching library footprint IDs.
    """
    results = library.list_library_footprints(query)
    return {"footprints": results}


@mcp.tool()
def get_netlist(schematic_path: str) -> str:
    """Extract netlist from a schematic (component-to-net mapping).

    Uses kicad-cli to export the netlist. Requires KiCad 8.

    Args:
        schematic_path: Path to the .kicad_sch file.

    Returns the netlist in KiCad's S-expression netlist format.
    """
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = str(Path(tmp_dir) / "netlist.net")
        cli.export_netlist(schematic_path, output_path)
        return Path(output_path).read_text(encoding="utf-8")


@mcp.tool()
def sync_schematic_to_pcb(schematic_path: str, pcb_path: str) -> dict:
    """Update PCB from schematic: nets, footprints, and pad net assignments.

    Exports a netlist from the schematic via kicad-cli, then:
    1. Adds missing net declarations to the PCB
    2. Places missing footprints (spaced out for manual arrangement)
    3. Updates pad-to-net assignments on all footprints to match the netlist

    If kicad-cli is unavailable it falls back to reading the schematic, which
    can add footprints but not pad nets; "netlist_source" and "warnings" in
    the result say when that happened.

    Args:
        schematic_path: Path to the .kicad_sch file.
        pcb_path: Path to the .kicad_pcb file.

    Returns a summary: added_nets (only newly declared ones), added_footprints,
    skipped_footprints (with reasons), updated_pads, netlist_source.
    """
    logger.info("sync_schematic_to_pcb: %s -> %s", schematic_path, pcb_path)
    return pcb.sync_from_schematic(schematic_path, pcb_path)


# ── JLCPCB Tools ────────────────────────────────────────────────────────────


@mcp.tool()
def search_jlcpcb_parts(
    query: str,
    category: str | None = None,
    in_stock: bool = True,
    limit: int = 30,
) -> dict:
    """Search JLCPCB parts catalog. No authentication needed.

    Args:
        query: Search term (e.g., "STM32F103", "0603 resistor 10K").
        category: Optional category filter (e.g., "Resistors").
        in_stock: Only show in-stock parts (default True).
        limit: Max results (default 30).

    Returns {parts: [...], count} with LCSC number, manufacturer, package, stock, price.
    """
    logger.info("search_jlcpcb_parts: %s", query)
    parts = jlcpcb.search_parts(query, category, in_stock, limit)
    return {"parts": [asdict(p) for p in parts], "count": len(parts)}


@mcp.tool()
def get_jlcpcb_part(lcsc_number: str) -> dict:
    """Get details for a specific JLCPCB part by LCSC number.

    Args:
        lcsc_number: LCSC part number (e.g., "C21190" or "21190").

    Returns {part: {...}} or {part: null} if not found.
    """
    part = jlcpcb.get_part(lcsc_number)
    return {"part": asdict(part) if part else None}


@mcp.tool()
def list_jlcpcb_categories() -> dict:
    """List available JLCPCB part categories for filtering searches."""
    cats = jlcpcb.list_categories()
    return {"categories": cats}


# ── Server entry point ──────────────────────────────────────────────────────


def main() -> None:
    """Run the MCP server."""
    def _handle_shutdown(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down...", sig_name)
        os._exit(0)

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    logger.info("KiCad MCP server starting")
    mcp.run()


if __name__ == "__main__":
    main()
