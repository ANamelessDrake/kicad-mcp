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
    logger.debug("schematic_read: %s", file_path)
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
    logger.debug("schematic_place_symbol: %s %s at (%s,%s)", lib_id, reference, x, y)
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
    logger.debug("pcb_read: %s", file_path)
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
    logger.debug("pcb_place_footprint: %s %s at (%s,%s)", footprint_lib, reference, x, y)
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
    logger.debug("pcb_place_footprint_array: %s x%d %s", footprint_lib, count, pattern)
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
    """Update PCB netlist from schematic.

    Reads the schematic to extract component and net information, then
    ensures the PCB file has matching net declarations and footprints.
    This is a simplified version of KiCad's "Update PCB from Schematic".

    Args:
        schematic_path: Path to the .kicad_sch file.
        pcb_path: Path to the .kicad_pcb file.

    Returns a summary of changes made.
    """
    from pathlib import Path

    from .sexp_parser import parse_file, write_file

    sch_data = schematic.read_schematic(schematic_path)

    if not Path(pcb_path).exists():
        root = pcb._make_empty_pcb()
    else:
        root = parse_file(pcb_path)

    added_nets: list[str] = []
    added_fps: list[str] = []

    net_names: set[str] = set()
    for sym in sch_data.symbols:
        if sym.value and sym.reference and not sym.reference.startswith("#"):
            net_names.add(sym.reference)

    for name in sorted(net_names):
        pcb._ensure_net(root, name)
        added_nets.append(name)

    pcb_data = pcb.read_pcb(pcb_path) if Path(pcb_path).exists() else pcb.PCBData()
    existing_refs = {fp.reference for fp in pcb_data.footprints}

    for sym in sch_data.symbols:
        if sym.reference not in existing_refs and sym.footprint and not sym.reference.startswith("#"):
            pcb.place_footprint(pcb_path, sym.footprint, sym.reference, sym.value, 50, 50)
            added_fps.append(sym.reference)

    write_file(pcb_path, root)

    return json.dumps({
        "added_nets": added_nets,
        "added_footprints": added_fps,
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
    logger.debug("search_jlcpcb_parts: %s", query)
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
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    logger.info("KiCad MCP server starting (29 tools registered)")
    mcp.run()


if __name__ == "__main__":
    main()
