"""MCP server entry point — registers all KiCad tools.

Run with: python -m kicad_mcp.server
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from . import cli, jlcpcb, library, pcb, schematic


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
def schematic_read(file_path: str) -> str:
    """Read and parse a .kicad_sch file.

    Returns structured JSON with components (ref, value, footprint, position, pins),
    wires, and labels.
    """
    logger.info("schematic_read: %s", file_path)
    data = schematic.read_schematic(file_path)
    return json.dumps(asdict(data), indent=2)


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
) -> str:
    """Place a component symbol in the schematic.

    Args:
        file_path: Path to the .kicad_sch file (created if it doesn't exist).
        lib_id: KiCad library symbol ID (e.g., "Device:R", "Device:C").
        reference: Reference designator (e.g., "R1", "C1").
        value: Component value (e.g., "10K", "100nF").
        footprint: Footprint library ID (e.g., "Resistor_SMD:R_0603_1608Metric").
        x: X position in mm.
        y: Y position in mm.
        rotation: Rotation in degrees (default 0).

    Returns the UUID of the placed symbol.
    """
    logger.info("schematic_place_symbol: %s %s at (%s,%s)", lib_id, reference, x, y)
    uuid = schematic.place_symbol(file_path, lib_id, reference, value, footprint, x, y, rotation)
    return json.dumps({"uuid": uuid})


@mcp.tool()
def schematic_add_wire(file_path: str, points: list[list[float]]) -> str:
    """Add a wire between a series of (x, y) points.

    Args:
        file_path: Path to the .kicad_sch file.
        points: List of [x, y] coordinate pairs in mm.

    Returns the UUID of the wire.
    """
    pts = [(p[0], p[1]) for p in points]
    uuid = schematic.add_wire(file_path, pts)
    return json.dumps({"uuid": uuid})


@mcp.tool()
def schematic_add_label(
    file_path: str, name: str, x: float, y: float, rotation: float = 0
) -> str:
    """Add a net label at a position in the schematic.

    Args:
        file_path: Path to the .kicad_sch file.
        name: Net name (e.g., "3V3", "SDA").
        x: X position in mm.
        y: Y position in mm.
        rotation: Rotation in degrees (default 0).

    Returns the UUID of the label.
    """
    uuid = schematic.add_label(file_path, name, x, y, rotation)
    return json.dumps({"uuid": uuid})


@mcp.tool()
def schematic_add_power_symbol(
    file_path: str, name: str, x: float, y: float, rotation: float = 0
) -> str:
    """Add a power port symbol (GND, 3V3, 5V, etc.) at a position.

    Args:
        file_path: Path to the .kicad_sch file.
        name: Power net name (e.g., "GND", "3V3", "5V").
        x: X position in mm.
        y: Y position in mm.
        rotation: Rotation in degrees (default 0).

    Returns the UUID of the power symbol.
    """
    uuid = schematic.add_power_symbol(file_path, name, x, y, rotation)
    return json.dumps({"uuid": uuid})


@mcp.tool()
def schematic_add_global_label(
    file_path: str, name: str, x: float, y: float, rotation: float = 0
) -> str:
    """Add a global label for inter-sheet connectivity.

    Args:
        file_path: Path to the .kicad_sch file.
        name: Global label name.
        x: X position in mm.
        y: Y position in mm.
        rotation: Rotation in degrees (default 0).

    Returns the UUID of the global label.
    """
    uuid = schematic.add_global_label(file_path, name, x, y, rotation)
    return json.dumps({"uuid": uuid})


@mcp.tool()
def schematic_delete(file_path: str, uuid: str) -> str:
    """Delete any schematic element by UUID.

    Args:
        file_path: Path to the .kicad_sch file.
        uuid: UUID of the element to delete.

    Returns whether the element was found and deleted.
    """
    found = schematic.delete_by_uuid(file_path, uuid)
    return json.dumps({"deleted": found})


@mcp.tool()
def schematic_add_labels(file_path: str, labels: list[dict]) -> str:
    """Add multiple net labels in a single operation (one file read/write cycle).

    Args:
        file_path: Path to the .kicad_sch file.
        labels: List of label dicts, each with keys: name (str), x (float), y (float),
                and optionally rotation (float, default 0).

    Returns JSON with list of UUIDs.
    """
    logger.info("schematic_add_labels: %d labels", len(labels))
    uuids = schematic.add_labels_batch(file_path, labels)
    return json.dumps({"uuids": uuids, "count": len(uuids)})


@mcp.tool()
def schematic_delete_many(file_path: str, uuids: list[str]) -> str:
    """Delete multiple schematic elements by UUID in a single operation.

    Args:
        file_path: Path to the .kicad_sch file.
        uuids: List of UUIDs to delete.

    Returns JSON with list of booleans indicating which were found and deleted.
    """
    logger.info("schematic_delete_many: %d uuids", len(uuids))
    results = schematic.delete_many(file_path, uuids)
    return json.dumps({"results": results, "deleted_count": sum(results)})


@mcp.tool()
def schematic_add_power_symbols(file_path: str, symbols: list[dict]) -> str:
    """Add multiple power symbols in a single operation (one file read/write cycle).

    Args:
        file_path: Path to the .kicad_sch file.
        symbols: List of dicts, each with keys: name (str, e.g. "GND", "3V3"),
                 x (float), y (float), and optionally rotation (float, default 0).

    Returns JSON with list of UUIDs.
    """
    logger.info("schematic_add_power_symbols: %d symbols", len(symbols))
    uuids = schematic.add_power_symbols_batch(file_path, symbols)
    return json.dumps({"uuids": uuids, "count": len(uuids)})


@mcp.tool()
def schematic_get_pin_positions(file_path: str, reference: str) -> str:
    """Get the actual pin endpoint positions for a component in schematic coordinates.

    Reads the component's position and rotation, looks up pin offsets from the
    lib_symbols definition, and applies the rotation transform. Use this to find
    the exact coordinates where labels and wires should connect to a component.

    Handles extended symbols that inherit pins from a parent definition.

    Args:
        file_path: Path to the .kicad_sch file.
        reference: Reference designator (e.g., "R1", "U1", "J1").

    Returns JSON list of {pin_number, pin_name, x, y} for each pin.
    """
    logger.info("schematic_get_pin_positions: %s in %s", reference, file_path)
    pins = schematic.get_pin_positions(file_path, reference)
    return json.dumps({"pins": pins, "count": len(pins)})


@mcp.tool()
def schematic_add_no_connect(file_path: str, x: float, y: float) -> str:
    """Add a no-connect (X) flag at a pin position.

    Args:
        file_path: Path to the .kicad_sch file.
        x: X position in mm (snapped to grid).
        y: Y position in mm (snapped to grid).

    Returns the UUID of the no-connect flag.
    """
    uuid = schematic.add_no_connect(file_path, x, y)
    return json.dumps({"uuid": uuid})


@mcp.tool()
def schematic_add_no_connects(file_path: str, positions: list[dict]) -> str:
    """Add multiple no-connect flags in a single operation.

    Args:
        file_path: Path to the .kicad_sch file.
        positions: List of dicts with keys: x (float), y (float).

    Returns JSON with list of UUIDs.
    """
    logger.info("schematic_add_no_connects: %d positions", len(positions))
    uuids = schematic.add_no_connects_batch(file_path, positions)
    return json.dumps({"uuids": uuids, "count": len(uuids)})


@mcp.tool()
def schematic_modify_lib_symbol_pin(
    file_path: str, lib_id: str, pin_number: str, pin_type: str
) -> str:
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

    Returns whether the pin was found and modified.
    """
    logger.info("schematic_modify_lib_symbol_pin: %s pin %s -> %s", lib_id, pin_number, pin_type)
    ok = schematic.modify_lib_symbol_pin(file_path, lib_id, pin_number, pin_type)
    return json.dumps({"modified": ok})


@mcp.tool()
def schematic_annotate(file_path: str) -> str:
    """Assign reference designators to unannotated symbols.

    Finds all symbols with '?' in their reference (e.g., R?, C?, U?), and assigns
    sequential numbers per prefix, avoiding numbers already in use.

    Args:
        file_path: Path to the .kicad_sch file.

    Returns JSON with changes made, e.g. {"changes": {"R": ["R1", "R2"], "C": ["C1"]}}.
    """
    logger.info("schematic_annotate: %s", file_path)
    result = schematic.annotate(file_path)
    return json.dumps(result)


@mcp.tool()
def schematic_run_erc(file_path: str) -> str:
    """Run Electrical Rules Check (ERC) on a schematic using kicad-cli.

    Returns a list of violations with severity, message, and location.
    Requires KiCad 8 to be installed.
    """
    violations = cli.run_erc(file_path)
    return json.dumps([asdict(v) for v in violations], indent=2)


# ── PCB Tools ────────────────────────────────────────────────────────────────


@mcp.tool()
def pcb_read(file_path: str) -> str:
    """Read and parse a .kicad_pcb file.

    Returns structured JSON with footprints (ref, position, rotation, layer, pad nets),
    traces, vias, zones, board outline, and net list.
    """
    logger.info("pcb_read: %s", file_path)
    data = pcb.read_pcb(file_path)
    return json.dumps(asdict(data), indent=2)


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
) -> str:
    """Place a footprint on the PCB.

    Args:
        file_path: Path to the .kicad_pcb file (created if it doesn't exist).
        footprint_lib: Footprint library ID (e.g., "Resistor_SMD:R_0603_1608Metric").
        reference: Reference designator (e.g., "R1").
        value: Component value (e.g., "10K").
        x: X position in mm.
        y: Y position in mm.
        rotation: Rotation in degrees (default 0).
        layer: Placement layer (default "F.Cu").

    Returns the UUID of the placed footprint.
    """
    logger.info("pcb_place_footprint: %s %s at (%s,%s)", footprint_lib, reference, x, y)
    uuid = pcb.place_footprint(file_path, footprint_lib, reference, value, x, y, rotation, layer)
    return json.dumps({"uuid": uuid})


@mcp.tool()
def pcb_move_footprint(
    file_path: str, reference: str, x: float, y: float, rotation: float | None = None
) -> str:
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
    return json.dumps({"moved": found})


@mcp.tool()
def pcb_add_trace(
    file_path: str,
    net_name: str,
    layer: str,
    width: float,
    points: list[list[float]],
) -> str:
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
    return json.dumps({"uuid": uuid})


@mcp.tool()
def pcb_add_via(
    file_path: str,
    net_name: str,
    x: float,
    y: float,
    size: float = 0.8,
    drill: float = 0.4,
) -> str:
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
    return json.dumps({"uuid": uuid})


@mcp.tool()
def pcb_add_zone(
    file_path: str,
    net_name: str,
    layer: str,
    outline_points: list[list[float]],
    fill_type: str = "solid",
) -> str:
    """Add a copper zone/pour.

    Args:
        file_path: Path to the .kicad_pcb file.
        net_name: Net name for the zone (e.g., "GND").
        layer: Copper layer (e.g., "B.Cu").
        outline_points: List of [x, y] coordinate pairs defining the zone boundary.
        fill_type: Fill type (default "solid").

    Returns the UUID of the zone.
    """
    pts = [(p[0], p[1]) for p in outline_points]
    uuid = pcb.add_zone(file_path, net_name, layer, pts, fill_type)
    return json.dumps({"uuid": uuid})


@mcp.tool()
def pcb_set_board_outline(file_path: str, outline_points: list[list[float]]) -> str:
    """Set the board outline on the Edge.Cuts layer.

    Args:
        file_path: Path to the .kicad_pcb file.
        outline_points: List of [x, y] coordinate pairs defining the board boundary.

    Returns whether the outline was set successfully.
    """
    pts = [(p[0], p[1]) for p in outline_points]
    ok = pcb.set_board_outline(file_path, pts)
    return json.dumps({"success": ok})


@mcp.tool()
def pcb_assign_net_to_pad(
    file_path: str, footprint_ref: str, pad_number: str, net_name: str
) -> str:
    """Assign a net to a specific pad on a footprint.

    Args:
        file_path: Path to the .kicad_pcb file.
        footprint_ref: Reference designator of the footprint.
        pad_number: Pad number (e.g., "1", "2").
        net_name: Net name to assign.

    Returns whether the pad was found and updated.
    """
    ok = pcb.assign_net_to_pad(file_path, footprint_ref, pad_number, net_name)
    return json.dumps({"assigned": ok})


@mcp.tool()
def pcb_add_mounting_hole(
    file_path: str, x: float, y: float, drill_size: float = 3.2, pad_size: float = 6.0
) -> str:
    """Add a mounting hole footprint.

    Args:
        file_path: Path to the .kicad_pcb file.
        x: X position in mm.
        y: Y position in mm.
        drill_size: Drill diameter in mm (default 3.2).
        pad_size: Pad diameter in mm (default 6.0).

    Returns the UUID of the mounting hole.
    """
    uuid = pcb.add_mounting_hole(file_path, x, y, drill_size, pad_size)
    return json.dumps({"uuid": uuid})


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
) -> str:
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
    return json.dumps({"uuids": uuids, "count": len(uuids)})


@mcp.tool()
def pcb_autoroute(
    file_path: str,
    freerouting_jar: str | None = None,
    timeout: int = 300,
) -> str:
    """Autoroute a PCB using Freerouting.

    Exports the PCB to Specctra DSN format, runs the Freerouting autorouter,
    and imports the routed result back. Requires Java and Freerouting JAR.

    Args:
        file_path: Path to the .kicad_pcb file.
        freerouting_jar: Path to freerouting.jar (default: FREEROUTING_JAR env var).
        timeout: Max seconds to wait for routing (default 300).

    Returns status JSON.
    """
    logger.info("pcb_autoroute: starting on %s", file_path)
    result = pcb.autoroute(file_path, freerouting_jar, timeout)
    logger.info("pcb_autoroute: completed")
    return json.dumps(result)


@mcp.tool()
def pcb_run_drc(file_path: str) -> str:
    """Run Design Rules Check (DRC) on a PCB using kicad-cli.

    Returns a list of violations with severity, message, and location.
    Requires KiCad 8 to be installed.
    """
    violations = cli.run_drc(file_path)
    return json.dumps([asdict(v) for v in violations], indent=2)


@mcp.tool()
def pcb_export_image(
    file_path: str,
    output_path: str,
    layers: list[str] | None = None,
    dpi: int = 300,
) -> str:
    """Export a PCB image (SVG) for visual review.

    Args:
        file_path: Path to the .kicad_pcb file.
        output_path: Output SVG file path.
        layers: List of layers to include (default: F.Cu, B.Cu, Edge.Cuts).
        dpi: Resolution in DPI (default 300).

    Returns the output file path.
    """
    path = cli.export_pcb_image(file_path, output_path, layers, dpi)
    return json.dumps({"output_path": path})


@mcp.tool()
def pcb_export_3d(file_path: str, output_path: str, format: str = "step") -> str:
    """Export 3D model of the PCB.

    Args:
        file_path: Path to the .kicad_pcb file.
        output_path: Output file path.
        format: Export format — "step" or "vrml" (default "step").

    Returns the output file path.
    """
    path = cli.export_3d(file_path, output_path, format)
    return json.dumps({"output_path": path})


@mcp.tool()
def pcb_export_gerbers(file_path: str, output_dir: str) -> str:
    """Export Gerber and drill files for manufacturing.

    Args:
        file_path: Path to the .kicad_pcb file.
        output_dir: Output directory for Gerber files.

    Returns a list of generated file paths.
    """
    files = cli.export_gerbers(file_path, output_dir)
    return json.dumps({"files": files})


# ── Utility Tools ────────────────────────────────────────────────────────────


@mcp.tool()
def list_symbols(query: str) -> str:
    """Search KiCad's symbol libraries for a component.

    Args:
        query: Search query (e.g., "2N7000", "resistor", "STM32").

    Returns a list of matching library symbol IDs.
    """
    results = library.list_library_symbols(query)
    return json.dumps({"symbols": results})


@mcp.tool()
def list_footprints(query: str) -> str:
    """Search KiCad's footprint libraries.

    Args:
        query: Search query (e.g., "R_0603", "QFP", "SOT-23").

    Returns a list of matching library footprint IDs.
    """
    results = library.list_library_footprints(query)
    return json.dumps({"footprints": results})


@mcp.tool()
def get_netlist(schematic_path: str) -> str:
    """Extract netlist from a schematic (component-to-net mapping).

    Uses kicad-cli to export the netlist. Requires KiCad 8.

    Args:
        schematic_path: Path to the .kicad_sch file.

    Returns the netlist XML content.
    """
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        output_path = f.name

    try:
        cli.export_netlist(schematic_path, output_path)
        content = Path(output_path).read_text()
        return content
    finally:
        Path(output_path).unlink(missing_ok=True)


@mcp.tool()
def sync_schematic_to_pcb(schematic_path: str, pcb_path: str) -> str:
    """Update PCB from schematic: nets, footprints, and pad net assignments.

    Exports a netlist from the schematic via kicad-cli, then:
    1. Adds missing net declarations to the PCB
    2. Places missing footprints (spaced out for manual arrangement)
    3. Updates pad-to-net assignments on all existing footprints to match the netlist

    Args:
        schematic_path: Path to the .kicad_sch file.
        pcb_path: Path to the .kicad_pcb file.

    Returns a summary of changes made.
    """
    import tempfile
    from pathlib import Path

    from .sexp_parser import parse_file, write_file

    logger.info("sync_schematic_to_pcb: %s -> %s", schematic_path, pcb_path)

    # Export netlist (S-expression format) from schematic
    components: list[dict] = []  # {ref, value, footprint}
    # pin_nets maps (ref, pin_number) -> net_name
    pin_nets: dict[tuple[str, str], str] = {}
    net_names: set[str] = set()

    try:
        with tempfile.NamedTemporaryFile(suffix=".net", delete=False) as f:
            netlist_path = f.name
        cli.export_netlist(schematic_path, netlist_path)
        netlist = parse_file(netlist_path)

        # Extract components
        comps_node = netlist.find("components")
        if comps_node:
            for comp in comps_node.find_all("comp"):
                ref = str(comp.find_value("ref") or "")
                if not ref or ref.startswith("#"):
                    continue
                value = str(comp.find_value("value") or "")
                fp_node = comp.find("footprint")
                fp = str(fp_node.children[1]) if fp_node and len(fp_node.children) >= 2 else ""
                components.append({"ref": ref, "value": value, "footprint": fp})

        # Extract nets with pin assignments
        nets_node = netlist.find("nets")
        if nets_node:
            for net in nets_node.find_all("net"):
                name = str(net.find_value("name") or "")
                if not name:
                    continue
                net_names.add(name)
                for node in net.find_all("node"):
                    ref = str(node.find_value("ref") or "")
                    pin = str(node.find_value("pin") or "")
                    if ref and pin:
                        pin_nets[(ref, pin)] = name

        Path(netlist_path).unlink(missing_ok=True)
    except Exception as e:
        # Fallback: read schematic directly if kicad-cli is not available
        logger.warning("kicad-cli netlist export failed (%s), falling back to schematic parse", e)
        sch_data = schematic.read_schematic(schematic_path)
        for sym in sch_data.symbols:
            if sym.reference and not sym.reference.startswith("#") and sym.footprint:
                components.append({
                    "ref": sym.reference,
                    "value": sym.value,
                    "footprint": sym.footprint,
                })

    # Update PCB
    if not Path(pcb_path).exists():
        root = pcb._make_empty_pcb()
        write_file(pcb_path, root)

    root = parse_file(pcb_path)

    # 1. Add missing net declarations
    added_nets: list[str] = []
    for name in sorted(net_names):
        pcb._ensure_net(root, name)
        added_nets.append(name)

    write_file(pcb_path, root)

    # 2. Place missing footprints
    pcb_data = pcb.read_pcb(pcb_path)
    existing_refs = {fp.reference for fp in pcb_data.footprints}

    added_fps: list[str] = []
    x_offset = 50.0
    for comp in components:
        if comp["ref"] not in existing_refs and comp["footprint"]:
            pcb.place_footprint(
                pcb_path, comp["footprint"], comp["ref"], comp["value"],
                x_offset, 50, 0, "F.Cu",
            )
            added_fps.append(comp["ref"])
            x_offset += 10.0

    # 3. Update pad net assignments on all footprints
    updated_pads = 0
    if pin_nets:
        # Re-read the PCB after footprint additions
        root = parse_file(pcb_path)

        for fp_node in root.find_all("footprint"):
            # Get the footprint's reference
            fp_ref = ""
            for prop in fp_node.find_all("property"):
                if len(prop.children) >= 3 and str(prop.children[1]) == "Reference":
                    fp_ref = str(prop.children[2])
                    break

            if not fp_ref:
                continue

            for pad_node in fp_node.find_all("pad"):
                if len(pad_node.children) < 2:
                    continue
                pad_num = str(pad_node.children[1])
                target_net = pin_nets.get((fp_ref, pad_num))
                if target_net is None:
                    continue

                # Ensure the net exists and get its number
                net_num = pcb._ensure_net(root, target_net)

                # Update or add the net assignment on this pad
                from .sexp_parser import QuotedString, parse as _parse
                net_node = pad_node.find("net")
                if net_node:
                    old_name = str(net_node.children[2]) if len(net_node.children) >= 3 else ""
                    if old_name != target_net:
                        net_node.children = ["net", net_num, QuotedString(target_net)]
                        updated_pads += 1
                else:
                    pad_node.children.append(_parse(f'(net {net_num} "{target_net}")'))
                    updated_pads += 1

        write_file(pcb_path, root)

    return json.dumps({
        "added_nets": added_nets,
        "added_footprints": added_fps,
        "updated_pads": updated_pads,
        "total_components": len(components),
        "total_pin_net_mappings": len(pin_nets),
    })


# ── JLCPCB Tools ────────────────────────────────────────────────────────────


@mcp.tool()
def search_jlcpcb_parts(
    query: str,
    category: str | None = None,
    in_stock: bool = True,
    limit: int = 30,
) -> str:
    """Search JLCPCB parts catalog. No authentication needed.

    Args:
        query: Search term (e.g., "STM32F103", "0603 resistor 10K").
        category: Optional category filter (e.g., "Resistors").
        in_stock: Only show in-stock parts (default True).
        limit: Max results (default 30).

    Returns JSON array of parts with LCSC number, manufacturer, package, stock, price.
    """
    logger.info("search_jlcpcb_parts: %s", query)
    parts = jlcpcb.search_parts(query, category, in_stock, limit)
    return json.dumps([asdict(p) for p in parts], indent=2)


@mcp.tool()
def get_jlcpcb_part(lcsc_number: str) -> str:
    """Get details for a specific JLCPCB part by LCSC number.

    Args:
        lcsc_number: LCSC part number (e.g., "C21190" or "21190").

    Returns JSON part details or null if not found.
    """
    part = jlcpcb.get_part(lcsc_number)
    return json.dumps(asdict(part) if part else None, indent=2)


@mcp.tool()
def list_jlcpcb_categories() -> str:
    """List available JLCPCB part categories for filtering searches."""
    cats = jlcpcb.list_categories()
    return json.dumps(cats, indent=2)


# ── Server entry point ──────────────────────────────────────────────────────


def main() -> None:
    """Run the MCP server."""
    def _handle_shutdown(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down...", sig_name)
        os._exit(0)

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    logger.info("KiCad MCP server starting (37 tools registered)")
    mcp.run()


if __name__ == "__main__":
    main()
