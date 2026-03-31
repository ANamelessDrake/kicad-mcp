"""PCB file (.kicad_pcb) operations.

Provides functions to read, create, and modify KiCad 8 PCB layout files
by manipulating their S-expression representation.
"""

from __future__ import annotations

import math
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
from .types import (
    FootprintInstance,
    NetInfo,
    PadInfo,
    PCBData,
    Point,
    TraceInfo,
    ViaInfo,
    ZoneInfo,
)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _make_empty_pcb() -> SexpList:
    """Create a minimal empty PCB S-expression tree."""
    return parse(
        '(kicad_pcb (version 20240108) (generator "kicad_mcp") '
        f'(generator_version "8.0") (general (thickness 1.6)) '
        f'(paper "A4") '
        f'(layers '
        f'(0 "F.Cu" signal) (31 "B.Cu" signal) '
        f'(32 "B.Adhes" user "B.Adhesive") (33 "F.Adhes" user "F.Adhesive") '
        f'(34 "B.Paste" user) (35 "F.Paste" user) '
        f'(36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen") '
        f'(38 "B.Mask" user "B.Mask") (39 "F.Mask" user "F.Mask") '
        f'(40 "Dwgs.User" user "User.Drawings") '
        f'(41 "Cmts.User" user "User.Comments") '
        f'(44 "Edge.Cuts" user) '
        f'(45 "Margin" user) '
        f'(46 "B.CrtYd" user "B.Courtyard") (47 "F.CrtYd" user "F.Courtyard") '
        f'(48 "B.Fab" user "B.Fab") (49 "F.Fab" user "F.Fab")) '
        f'(setup (pad_to_mask_clearance 0) '
        f'(pcbplotparams (layerselection 0x00010fc_ffffffff) (plot_on_all_layers_selection 0x0000000_00000000))) '
        f'(net 0 ""))'
    )


def read_pcb(file_path: str) -> PCBData:
    """Read and parse a .kicad_pcb file into structured data."""
    root = parse_file(file_path)
    data = PCBData()

    # Parse nets
    for net_node in root.find_all("net"):
        if len(net_node.children) >= 3:
            data.nets.append(NetInfo(
                number=int(net_node.children[1]),
                name=str(net_node.children[2]),
            ))

    # Parse footprints
    for fp in root.find_all("footprint"):
        if len(fp.children) < 2:
            continue
        fp_lib = str(fp.children[1])

        layer_node = fp.find("layer")
        layer = str(layer_node.children[1]) if layer_node and len(layer_node.children) >= 2 else "F.Cu"

        at_node = fp.find("at")
        x = float(at_node.children[1]) if at_node and len(at_node.children) >= 2 else 0.0
        y = float(at_node.children[2]) if at_node and len(at_node.children) >= 3 else 0.0
        rot = float(at_node.children[3]) if at_node and len(at_node.children) >= 4 else 0.0

        uuid_node = fp.find("uuid")
        fp_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""

        ref = value = ""
        for prop in fp.find_all("property"):
            if len(prop.children) >= 3:
                pname = str(prop.children[1])
                pval = str(prop.children[2])
                if pname == "Reference":
                    ref = pval
                elif pname == "Value":
                    value = pval

        pads: list[PadInfo] = []
        for pad in fp.find_all("pad"):
            if len(pad.children) >= 2:
                pad_num = str(pad.children[1])
                net_node = pad.find("net")
                net_num = int(net_node.children[1]) if net_node and len(net_node.children) >= 2 else 0
                net_name = str(net_node.children[2]) if net_node and len(net_node.children) >= 3 else ""
                pad_at = pad.find("at")
                px = float(pad_at.children[1]) if pad_at and len(pad_at.children) >= 2 else 0.0
                py = float(pad_at.children[2]) if pad_at and len(pad_at.children) >= 3 else 0.0
                pads.append(PadInfo(number=pad_num, net_number=net_num, net_name=net_name, position=Point(px, py)))

        data.footprints.append(FootprintInstance(
            uuid=fp_uuid, footprint_lib=fp_lib, reference=ref, value=value,
            position=Point(x, y), rotation=rot, layer=layer, pads=pads,
        ))

    # Parse traces (segments)
    for seg in root.find_all("segment"):
        start_node = seg.find("start")
        end_node = seg.find("end")
        width_node = seg.find("width")
        layer_node = seg.find("layer")
        net_node = seg.find("net")
        uuid_node = seg.find("uuid")

        sx = float(start_node.children[1]) if start_node and len(start_node.children) >= 3 else 0.0
        sy = float(start_node.children[2]) if start_node and len(start_node.children) >= 3 else 0.0
        ex = float(end_node.children[1]) if end_node and len(end_node.children) >= 3 else 0.0
        ey = float(end_node.children[2]) if end_node and len(end_node.children) >= 3 else 0.0
        width = float(width_node.children[1]) if width_node and len(width_node.children) >= 2 else 0.25
        layer = str(layer_node.children[1]) if layer_node and len(layer_node.children) >= 2 else "F.Cu"
        net = int(net_node.children[1]) if net_node and len(net_node.children) >= 2 else 0
        seg_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""

        data.traces.append(TraceInfo(
            uuid=seg_uuid, start=Point(sx, sy), end=Point(ex, ey),
            width=width, layer=layer, net=net,
        ))

    # Parse vias
    for via in root.find_all("via"):
        at_node = via.find("at")
        size_node = via.find("size")
        drill_node = via.find("drill")
        net_node = via.find("net")
        uuid_node = via.find("uuid")

        vx = float(at_node.children[1]) if at_node and len(at_node.children) >= 2 else 0.0
        vy = float(at_node.children[2]) if at_node and len(at_node.children) >= 3 else 0.0
        size = float(size_node.children[1]) if size_node and len(size_node.children) >= 2 else 0.8
        drill = float(drill_node.children[1]) if drill_node and len(drill_node.children) >= 2 else 0.4
        net = int(net_node.children[1]) if net_node and len(net_node.children) >= 2 else 0
        via_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""

        data.vias.append(ViaInfo(uuid=via_uuid, position=Point(vx, vy), size=size, drill=drill, net=net))

    # Parse zones
    for zone in root.find_all("zone"):
        net_node = zone.find("net")
        net_name_node = zone.find("net_name")
        layer_node = zone.find("layer")
        uuid_node = zone.find("uuid")
        polygon = zone.find("polygon")

        net_num = int(net_node.children[1]) if net_node and len(net_node.children) >= 2 else 0
        net_name = str(net_name_node.children[1]) if net_name_node and len(net_name_node.children) >= 2 else ""
        layer = str(layer_node.children[1]) if layer_node and len(layer_node.children) >= 2 else "F.Cu"
        zone_uuid = str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""

        outline: list[Point] = []
        if polygon:
            pts_node = polygon.find("pts")
            if pts_node:
                for xy_node in pts_node.find_all("xy"):
                    if len(xy_node.children) >= 3:
                        outline.append(Point(float(xy_node.children[1]), float(xy_node.children[2])))

        data.zones.append(ZoneInfo(uuid=zone_uuid, net_number=net_num, net_name=net_name, layer=layer, outline=outline))

    # Parse board outline (Edge.Cuts)
    for gr in root.find_all("gr_rect"):
        layer_node = gr.find("layer")
        if layer_node and len(layer_node.children) >= 2 and str(layer_node.children[1]) == "Edge.Cuts":
            start_node = gr.find("start")
            end_node = gr.find("end")
            if start_node and end_node:
                sx = float(start_node.children[1])
                sy = float(start_node.children[2])
                ex = float(end_node.children[1])
                ey = float(end_node.children[2])
                data.board_outline = [Point(sx, sy), Point(ex, sy), Point(ex, ey), Point(sx, ey)]

    for gr in root.find_all("gr_line"):
        layer_node = gr.find("layer")
        if layer_node and len(layer_node.children) >= 2 and str(layer_node.children[1]) == "Edge.Cuts":
            start_node = gr.find("start")
            end_node = gr.find("end")
            if start_node and end_node:
                sx = float(start_node.children[1])
                sy = float(start_node.children[2])
                data.board_outline.append(Point(sx, sy))

    return data


def _ensure_net(root: SexpList, net_name: str) -> int:
    """Ensure a net declaration exists. Returns the net number."""
    max_net = 0
    for net_node in root.find_all("net"):
        if len(net_node.children) >= 3:
            num = int(net_node.children[1])
            if str(net_node.children[2]) == net_name:
                return num
            max_net = max(max_net, num)

    new_num = max_net + 1
    net_sexp = parse(f'(net {new_num} "{esc(net_name)}")')

    # Insert after last existing net declaration
    last_net_idx = -1
    for i, child in enumerate(root.children):
        if isinstance(child, SexpList) and child.tag == "net":
            last_net_idx = i
    if last_net_idx >= 0:
        root.children.insert(last_net_idx + 1, net_sexp)
    else:
        root.children.append(net_sexp)

    return new_num


def _load_footprint_from_disk(footprint_lib: str) -> SexpList | None:
    """Try to load a footprint definition from KiCad's installed library files."""
    import os
    footprint_dir = os.environ.get("KICAD_FOOTPRINT_DIR", "/usr/share/kicad/footprints")

    parts = footprint_lib.split(":")
    if len(parts) != 2:
        return None

    lib_name, fp_name = parts
    fp_file = Path(footprint_dir) / f"{lib_name}.pretty" / f"{fp_name}.kicad_mod"

    if not fp_file.exists():
        return None

    try:
        fp_root = parse_file(str(fp_file))
        if fp_root.tag in ("footprint", "module"):
            fp_root.children[1] = QuotedString(footprint_lib)
        return fp_root
    except Exception:
        return None


def place_footprint(
    file_path: str,
    footprint_lib: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    rotation: float = 0,
    layer: str = "F.Cu",
) -> str:
    """Place a footprint on the PCB. Returns the UUID."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_pcb()

    fp_uuid = _new_uuid()

    # Try to load the actual footprint from disk
    fp_node = _load_footprint_from_disk(footprint_lib)
    if fp_node:
        # Update the loaded footprint with our placement info
        at_node = fp_node.find("at")
        if at_node:
            at_node.children = ["at", x, y]
            if rotation != 0:
                at_node.children.append(rotation)
        else:
            at_sexp = parse(f"(at {x} {y}{f' {rotation}' if rotation else ''})")
            fp_node.children.insert(2, at_sexp)

        layer_node = fp_node.find("layer")
        if layer_node:
            layer_node.children = ["layer", QuotedString(layer)]
        else:
            fp_node.children.insert(2, parse(f'(layer "{esc(layer)}")'))

        uuid_node = fp_node.find("uuid")
        if uuid_node:
            uuid_node.children = ["uuid", QuotedString(fp_uuid)]
        else:
            fp_node.children.append(parse(f'(uuid "{fp_uuid}")'))

        for prop in fp_node.find_all("property"):
            if len(prop.children) >= 3:
                if str(prop.children[1]) == "Reference":
                    prop.children[2] = QuotedString(reference)
                elif str(prop.children[1]) == "Value":
                    prop.children[2] = QuotedString(value)

        root.children.append(fp_node)
    else:
        # Fallback: create a minimal footprint stub
        silk_layer = layer.replace("Cu", "SilkS")
        fab_layer = layer.replace("Cu", "Fab")
        fp_sexp = (
            f'(footprint "{esc(footprint_lib)}" (layer "{esc(layer)}") (at {x} {y}'
            f'{f" {rotation}" if rotation else ""}) '
            f'(uuid "{fp_uuid}") '
            f'(property "Reference" "{esc(reference)}" (at 0 -2) '
            f'(layer "{esc(silk_layer)}") '
            f'(effects (font (size 1 1) (thickness 0.15)))) '
            f'(property "Value" "{esc(value)}" (at 0 2) '
            f'(layer "{esc(fab_layer)}") '
            f'(effects (font (size 1 1) (thickness 0.15)))))'
        )
        fp_node = parse(fp_sexp)
        root.children.append(fp_node)

    write_file(file_path, root)
    return fp_uuid


def move_footprint(
    file_path: str, reference: str, x: float, y: float, rotation: float | None = None
) -> bool:
    """Move an existing footprint to new position. Returns True if found."""
    root = parse_file(file_path)

    for fp in root.find_all("footprint"):
        for prop in fp.find_all("property"):
            if len(prop.children) >= 3 and str(prop.children[1]) == "Reference" and str(prop.children[2]) == reference:
                at_node = fp.find("at")
                if at_node:
                    at_node.children = ["at", x, y]
                    if rotation is not None:
                        at_node.children.append(rotation)
                write_file(file_path, root)
                return True
    return False


def add_trace(
    file_path: str,
    net_name: str,
    layer: str,
    width: float,
    points: list[tuple[float, float]],
) -> str:
    """Add copper trace segments. Returns UUID of the last segment."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_pcb()

    net_num = _ensure_net(root, net_name)

    last_uuid = ""
    for i in range(len(points) - 1):
        seg_uuid = _new_uuid()
        last_uuid = seg_uuid
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        seg_sexp = (
            f'(segment (start {x1} {y1}) (end {x2} {y2}) (width {width}) '
            f'(layer "{esc(layer)}") (net {net_num}) (uuid "{seg_uuid}"))'
        )
        root.children.append(parse(seg_sexp))

    write_file(file_path, root)
    return last_uuid


def add_via(
    file_path: str,
    net_name: str,
    x: float,
    y: float,
    size: float = 0.8,
    drill: float = 0.4,
) -> str:
    """Add a via at position. Returns the UUID."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_pcb()

    net_num = _ensure_net(root, net_name)
    via_uuid = _new_uuid()

    via_sexp = (
        f'(via (at {x} {y}) (size {size}) (drill {drill}) '
        f'(layers "F.Cu" "B.Cu") (net {net_num}) (uuid "{via_uuid}"))'
    )
    root.children.append(parse(via_sexp))

    write_file(file_path, root)
    return via_uuid


def add_zone(
    file_path: str,
    net_name: str,
    layer: str,
    outline_points: list[tuple[float, float]],
    fill_type: str = "solid",
) -> str:
    """Add a copper zone/pour. Returns the UUID."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_pcb()

    net_num = _ensure_net(root, net_name)
    zone_uuid = _new_uuid()

    pts_str = " ".join(f"(xy {x} {y})" for x, y in outline_points)
    zone_sexp = (
        f'(zone (net {net_num}) (net_name "{esc(net_name)}") (layer "{esc(layer)}") '
        f'(uuid "{zone_uuid}") '
        f'(fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5)) '
        f'(polygon (pts {pts_str})))'
    )
    root.children.append(parse(zone_sexp))

    write_file(file_path, root)
    return zone_uuid


def set_board_outline(file_path: str, outline_points: list[tuple[float, float]]) -> bool:
    """Set the board outline on Edge.Cuts layer. Returns True on success."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_pcb()

    # Remove existing Edge.Cuts geometry
    to_remove = []
    for child in root.children:
        if isinstance(child, SexpList) and child.tag in ("gr_line", "gr_rect", "gr_arc", "gr_poly"):
            layer_node = child.find("layer")
            if layer_node and len(layer_node.children) >= 2 and str(layer_node.children[1]) == "Edge.Cuts":
                to_remove.append(child)
    for child in to_remove:
        root.children.remove(child)

    # Add new outline as line segments
    for i in range(len(outline_points)):
        x1, y1 = outline_points[i]
        x2, y2 = outline_points[(i + 1) % len(outline_points)]
        line_sexp = (
            f'(gr_line (start {x1} {y1}) (end {x2} {y2}) '
            f'(layer "Edge.Cuts") (width 0.05) (uuid "{_new_uuid()}"))'
        )
        root.children.append(parse(line_sexp))

    write_file(file_path, root)
    return True


def assign_net_to_pad(file_path: str, footprint_ref: str, pad_number: str, net_name: str) -> bool:
    """Assign a net to a specific pad on a footprint. Returns True if found."""
    root = parse_file(file_path)
    net_num = _ensure_net(root, net_name)

    for fp in root.find_all("footprint"):
        for prop in fp.find_all("property"):
            if len(prop.children) >= 3 and str(prop.children[1]) == "Reference" and str(prop.children[2]) == footprint_ref:
                for pad in fp.find_all("pad"):
                    if len(pad.children) >= 2 and str(pad.children[1]) == pad_number:
                        net_node = pad.find("net")
                        if net_node:
                            net_node.children = ["net", net_num, QuotedString(net_name)]
                        else:
                            pad.children.append(parse(f'(net {net_num} "{esc(net_name)}")'))
                        write_file(file_path, root)
                        return True
    return False


def add_mounting_hole(
    file_path: str, x: float, y: float, drill_size: float = 3.2, pad_size: float = 6.0
) -> str:
    """Add a mounting hole footprint. Returns the UUID."""
    path = Path(file_path)
    if path.exists():
        root = parse_file(file_path)
    else:
        root = _make_empty_pcb()

    fp_uuid = _new_uuid()
    fp_sexp = (
        f'(footprint "MountingHole:MountingHole_{drill_size}mm" '
        f'(layer "F.Cu") (at {x} {y}) '
        f'(uuid "{fp_uuid}") '
        f'(property "Reference" "H1" (at 0 -{pad_size / 2 + 1}) '
        f'(layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15)))) '
        f'(property "Value" "MountingHole" (at 0 {pad_size / 2 + 1}) '
        f'(layer "F.Fab") (effects (font (size 1 1) (thickness 0.15)))) '
        f'(pad "" thru_hole circle (at 0 0) (size {pad_size} {pad_size}) (drill {drill_size}) '
        f'(layers "*.Cu" "*.Mask")))'
    )
    root.children.append(parse(fp_sexp))

    write_file(file_path, root)
    return fp_uuid


def place_footprint_array(
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
) -> list[str]:
    """Place an array of identical footprints in a grid or circular pattern.

    Returns list of UUIDs for all placed footprints.

    Note: Each placement re-reads/writes the file. For very large arrays
    this could be slow — acceptable for typical component counts.
    """
    if pattern not in ("grid", "circular"):
        raise ValueError(f"Unknown pattern: {pattern!r}. Use 'grid' or 'circular'.")

    if count < 1:
        raise ValueError("count must be >= 1")

    uuids: list[str] = []

    if pattern == "grid":
        cols = columns if columns else count  # default: single row
        for i in range(count):
            col = i % cols
            row = i // cols
            x = start_x + col * spacing_x
            y = start_y + row * spacing_y
            ref = f"{reference_prefix}{start_index + i}"
            uid = place_footprint(file_path, footprint_lib, ref, value, x, y, rotation, layer)
            uuids.append(uid)

    elif pattern == "circular":
        for i in range(count):
            angle_rad = 2 * math.pi * i / count
            x = start_x + radius * math.cos(angle_rad)
            y = start_y + radius * math.sin(angle_rad)
            fp_rotation = rotation + math.degrees(angle_rad)
            ref = f"{reference_prefix}{start_index + i}"
            uid = place_footprint(file_path, footprint_lib, ref, value, x, y, fp_rotation, layer)
            uuids.append(uid)

    return uuids


def autoroute(
    file_path: str,
    freerouting_jar: str | None = None,
    timeout: int = 300,
    strategy: str = "auto",
) -> dict:
    """Route a PCB.

    Strategies:
    - "freerouting": Use Freerouting (requires pcbnew + Java)
    - "simple": L-shaped routing using add_trace/add_via
    - "auto": Try Freerouting, fall back to simple

    Returns dict with status and details.
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"PCB file not found: {file_path}")

    if strategy in ("freerouting", "auto"):
        try:
            return _autoroute_freerouting(file_path, freerouting_jar, timeout)
        except RuntimeError as e:
            if strategy == "freerouting":
                raise
            # Fall through to simple router

    return _autoroute_simple(file_path)


def _autoroute_freerouting(
    file_path: str, freerouting_jar: str | None, timeout: int
) -> dict:
    """Route using Freerouting (requires pcbnew Python module + Java)."""
    import tempfile
    from . import cli as kicad_cli

    with tempfile.TemporaryDirectory() as tmp_dir:
        dsn_path = str(Path(tmp_dir) / "board.dsn")
        ses_path = str(Path(tmp_dir) / "board.ses")

        kicad_cli.export_dsn(file_path, dsn_path)
        kicad_cli.run_freerouting(dsn_path, ses_path, freerouting_jar, timeout)
        kicad_cli.import_ses(file_path, ses_path)

    return {"status": "success", "method": "freerouting", "file": file_path}


_POWER_NETS = {"GND", "+3V3", "+3.3V", "+5V", "+12V", "VCC", "VDD", "VBUS"}
_POWER_TRACE_WIDTH = 0.4
_SIGNAL_TRACE_WIDTH = 0.25
_VIA_SIZE = 0.8
_VIA_DRILL = 0.4


def _autoroute_simple(file_path: str) -> dict:
    """Simple L-shaped autorouter using add_trace and add_via.

    Strategy:
    - GND pads: drop a via to B.Cu ground plane
    - Power pads: route on F.Cu with wider traces
    - Signal pads: route on F.Cu with L-shaped paths (horizontal then vertical)
    - Connects pairs of pads on the same net
    """
    data = read_pcb(file_path)

    # Build net-to-pads map: net_name -> [(fp_ref, pad_num, abs_x, abs_y)]
    net_pads: dict[str, list[tuple[str, str, float, float]]] = {}
    for fp in data.footprints:
        for pad in fp.pads:
            if not pad.net_name or pad.net_name == "":
                continue
            # Pad position is relative to footprint; compute absolute
            abs_x = fp.position.x + pad.position.x
            abs_y = fp.position.y + pad.position.y
            net_pads.setdefault(pad.net_name, []).append(
                (fp.reference, pad.number, abs_x, abs_y)
            )

    # Get existing trace endpoints to avoid duplicates
    existing_segments: set[tuple[float, float, float, float]] = set()
    for t in data.traces:
        existing_segments.add((t.start.x, t.start.y, t.end.x, t.end.y))
        existing_segments.add((t.end.x, t.end.y, t.start.x, t.start.y))

    traces_added = 0
    vias_added = 0
    nets_routed = 0

    for net_name, pads in net_pads.items():
        is_gnd = net_name.upper() == "GND"
        is_power = net_name in _POWER_NETS

        # GND pads always get vias even if there's only one
        if not is_gnd and len(pads) < 2:
            continue
        width = _POWER_TRACE_WIDTH if is_power else _SIGNAL_TRACE_WIDTH

        if is_gnd:
            # GND strategy: drop a via at each pad to reach B.Cu ground plane
            for _ref, _pin, px, py in pads:
                seg_key = (px, py, px, py)
                if seg_key not in existing_segments:
                    add_via(file_path, net_name, px, py, _VIA_SIZE, _VIA_DRILL)
                    vias_added += 1
                    existing_segments.add(seg_key)
            nets_routed += 1
        else:
            # Connect pads sequentially with L-shaped routes on F.Cu
            routed_any = False
            for i in range(len(pads) - 1):
                _, _, x1, y1 = pads[i]
                _, _, x2, y2 = pads[i + 1]

                seg_key = (x1, y1, x2, y2)
                if seg_key in existing_segments:
                    continue

                # L-shaped: horizontal then vertical
                points = [(x1, y1), (x2, y1), (x2, y2)]
                # Skip if start == end
                if x1 == x2 and y1 == y2:
                    continue
                # Simplify to straight line if aligned
                if x1 == x2 or y1 == y2:
                    points = [(x1, y1), (x2, y2)]

                add_trace(file_path, net_name, "F.Cu", width, points)
                traces_added += len(points) - 1
                existing_segments.add(seg_key)
                routed_any = True

            if routed_any:
                nets_routed += 1

    return {
        "status": "success",
        "method": "simple",
        "traces_added": traces_added,
        "vias_added": vias_added,
        "nets_routed": nets_routed,
        "file": file_path,
    }


def set_zone_net(file_path: str, zone_uuid: str, net_name: str) -> bool:
    """Assign a net to an existing copper zone by UUID. Returns True if found."""
    root = parse_file(file_path)
    net_num = _ensure_net(root, net_name)

    for zone in root.find_all("zone"):
        uuid_node = zone.find("uuid")
        if uuid_node and len(uuid_node.children) >= 2 and str(uuid_node.children[1]) == zone_uuid:
            # Update (net N)
            net_node = zone.find("net")
            if net_node and len(net_node.children) >= 2:
                net_node.children[1] = net_num
            # Update (net_name "name")
            net_name_node = zone.find("net_name")
            if net_name_node and len(net_name_node.children) >= 2:
                net_name_node.children[1] = QuotedString(net_name)
            else:
                zone.children.append(parse(f'(net_name "{esc(net_name)}")'))
            write_file(file_path, root)
            return True
    return False


# Layer flip mapping for footprints
_LAYER_FLIP: dict[str, str] = {
    "F.Cu": "B.Cu", "B.Cu": "F.Cu",
    "F.Mask": "B.Mask", "B.Mask": "F.Mask",
    "F.Paste": "B.Paste", "B.Paste": "F.Paste",
    "F.SilkS": "B.SilkS", "B.SilkS": "F.SilkS",
    "F.Silkscreen": "B.Silkscreen", "B.Silkscreen": "F.Silkscreen",
    "F.Fab": "B.Fab", "B.Fab": "F.Fab",
    "F.CrtYd": "B.CrtYd", "B.CrtYd": "F.CrtYd",
    "F.Courtyard": "B.Courtyard", "B.Courtyard": "F.Courtyard",
    "F.Adhes": "B.Adhes", "B.Adhes": "F.Adhes",
    "F.Adhesive": "B.Adhesive", "B.Adhesive": "F.Adhesive",
}


def _flip_layer(layer_str: str) -> str:
    """Flip a layer name from front to back or vice versa."""
    return _LAYER_FLIP.get(layer_str, layer_str)


def flip_footprint(file_path: str, reference: str, to_layer: str = "B.Cu") -> bool:
    """Flip a footprint to the specified layer. Returns True if found.

    Swaps all layer references (F.Cu<->B.Cu, F.Mask<->B.Mask, etc.)
    on the footprint and all its pads, text, and graphics.
    """
    root = parse_file(file_path)

    for fp in root.find_all("footprint"):
        for prop in fp.find_all("property"):
            if len(prop.children) >= 3 and str(prop.children[1]) == "Reference" and str(prop.children[2]) == reference:
                # Determine flip direction
                current_layer_node = fp.find("layer")
                if not current_layer_node or len(current_layer_node.children) < 2:
                    return False
                current_layer = str(current_layer_node.children[1])

                # If already on target layer, nothing to do
                if current_layer == to_layer:
                    write_file(file_path, root)
                    return True

                # Flip all layer references in the entire footprint tree
                _flip_layers_recursive(fp)

                write_file(file_path, root)
                return True
    return False


def _flip_layers_recursive(node: SexpList) -> None:
    """Recursively flip all layer references in a node tree."""
    for i, child in enumerate(node.children):
        if isinstance(child, SexpList):
            if child.tag == "layer" and len(child.children) >= 2:
                old = str(child.children[1])
                flipped = _flip_layer(old)
                if flipped != old:
                    child.children[1] = QuotedString(flipped)
            elif child.tag == "layers":
                # Pad layers list: (layers "F.Cu" "F.Paste" "F.Mask")
                for j in range(1, len(child.children)):
                    if isinstance(child.children[j], (str, QuotedString)):
                        old = str(child.children[j])
                        flipped = _flip_layer(old)
                        if flipped != old:
                            child.children[j] = QuotedString(flipped)
            else:
                _flip_layers_recursive(child)


def delete_by_uuid(file_path: str, target_uuid: str) -> bool:
    """Delete any PCB element by UUID. Returns True if found and deleted."""
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
