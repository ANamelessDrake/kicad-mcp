"""PCB file (.kicad_pcb) operations.

Provides functions to read, create, and modify KiCad 8 PCB layout files
by manipulating their S-expression representation.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger("kicad_mcp")


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


def _load_or_create(file_path: str) -> SexpList:
    if Path(file_path).exists():
        return parse_file(file_path)
    return _make_empty_pcb()


# ── Angle and coordinate helpers ────────────────────────────────────────────


def _norm180(angle: float) -> float:
    """Normalize to (-180, 180], KiCad's range for footprint and text angles."""
    a = round(angle % 360, 6)
    return a - 360 if a > 180 else a


def _norm360(angle: float) -> float:
    """Normalize to [0, 360), KiCad's range for pad angles."""
    a = round(angle % 360, 6)
    return 0.0 if a == 360 else a


def _at_angle(at_node: SexpList) -> float:
    return float(at_node.children[3]) if len(at_node.children) >= 4 else 0.0


def _set_at(at_node: SexpList, x: float, y: float, angle: float, keep_zero: bool) -> None:
    """Rewrite an (at x y [angle]) node; KiCad omits a zero angle on footprints and pads."""
    at_node.children = ["at", x, y]
    if angle or keep_zero:
        at_node.children.append(angle)


def _rotate_local(x: float, y: float, angle: float) -> tuple[float, float]:
    """Rotate a footprint-local offset by a KiCad angle (counterclockwise on screen, Y down)."""
    rad = math.radians(angle)
    c, s = math.cos(rad), math.sin(rad)
    return x * c + y * s, -x * s + y * c


def _pad_absolute_position(fp_x: float, fp_y: float, fp_rot: float, px: float, py: float) -> tuple[float, float]:
    dx, dy = _rotate_local(px, py, fp_rot)
    return round(fp_x + dx, 6), round(fp_y + dy, 6)


def _is_text(node: SexpList) -> bool:
    return node.tag in ("property", "fp_text")


def _get_fp_reference(fp_node: SexpList) -> str:
    """Get the reference designator from a footprint node."""
    for prop in fp_node.find_all("property"):
        if len(prop.children) >= 3 and str(prop.children[1]) == "Reference":
            return str(prop.children[2])
    for text in fp_node.find_all("fp_text"):  # KiCad 7 and older
        if len(text.children) >= 3 and str(text.children[1]) == "reference":
            return str(text.children[2])
    return ""


def _find_footprint(root: SexpList, reference: str) -> SexpList | None:
    for fp in root.find_all("footprint"):
        if _get_fp_reference(fp) == reference:
            return fp
    return None


def _fp_uuid(fp: SexpList) -> str:
    uuid_node = fp.find("uuid")
    return str(uuid_node.children[1]) if uuid_node and len(uuid_node.children) >= 2 else ""


# ── Reading ─────────────────────────────────────────────────────────────────


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

        ref = value = ""
        for prop in fp.find_all("property"):
            if len(prop.children) >= 3:
                pname = str(prop.children[1])
                pval = str(prop.children[2])
                if pname == "Reference":
                    ref = pval
                elif pname == "Value":
                    value = pval
        if not ref:
            ref = _get_fp_reference(fp)

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
                ax, ay = _pad_absolute_position(x, y, rot, px, py)
                pads.append(PadInfo(
                    number=pad_num, net_number=net_num, net_name=net_name,
                    position=Point(px, py), absolute_position=Point(ax, ay),
                ))

        data.footprints.append(FootprintInstance(
            uuid=_fp_uuid(fp), footprint_lib=fp_lib, reference=ref, value=value,
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

    data.board_outline = [Point(x, y) for x, y in _board_outline(root)]
    return data


def _xy(node: SexpList | None) -> tuple[float, float] | None:
    if node is None or len(node.children) < 3:
        return None
    return float(node.children[1]), float(node.children[2])


def _on_edge_cuts(node: SexpList) -> bool:
    layer_node = node.find("layer")
    return bool(layer_node and len(layer_node.children) >= 2 and str(layer_node.children[1]) == "Edge.Cuts")


def _polygon_area(points: list[tuple[float, float]]) -> float:
    return abs(sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )) / 2


def _board_outline(root: SexpList) -> list[tuple[float, float]]:
    """Return the board outline as an ordered list of corner points.

    Lines and arcs on Edge.Cuts are chained end to end into closed loops;
    rectangles and polygons are loops already. Arcs contribute their
    midpoint so the shape stays recognizable. When there are several loops
    (cutouts), the one enclosing the largest area is the outline.
    """
    loops: list[list[tuple[float, float]]] = []
    segments: list[list[tuple[float, float]]] = []  # each: [start, (mid,) end]

    for child in root.children:
        if not isinstance(child, SexpList) or not _on_edge_cuts(child):
            continue
        if child.tag == "gr_rect":
            s, e = _xy(child.find("start")), _xy(child.find("end"))
            if s and e:
                loops.append([s, (e[0], s[1]), e, (s[0], e[1])])
        elif child.tag == "gr_poly":
            pts = child.find("pts")
            if pts:
                loop = [p for p in (_xy(xy) for xy in pts.find_all("xy")) if p]
                if len(loop) >= 3:
                    loops.append(loop)
        elif child.tag in ("gr_line", "gr_arc"):
            s, e = _xy(child.find("start")), _xy(child.find("end"))
            if s and e:
                mid = _xy(child.find("mid")) if child.tag == "gr_arc" else None
                segments.append([s, mid, e] if mid else [s, e])

    def close(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return abs(a[0] - b[0]) < 1e-4 and abs(a[1] - b[1]) < 1e-4

    remaining = segments[:]
    while remaining:
        seg = remaining.pop(0)
        chain = list(seg)
        extended = True
        while extended and not close(chain[0], chain[-1]):
            extended = False
            for i, other in enumerate(remaining):
                if close(other[0], chain[-1]):
                    chain.extend(other[1:])
                elif close(other[-1], chain[-1]):
                    chain.extend(list(reversed(other))[1:])
                else:
                    continue
                remaining.pop(i)
                extended = True
                break
        if len(chain) > 2 and close(chain[0], chain[-1]):
            chain.pop()
        loops.append(chain)

    if not loops:
        return []
    return max(loops, key=lambda lp: (_polygon_area(lp) if len(lp) >= 3 else 0, len(lp)))


# ── Nets ────────────────────────────────────────────────────────────────────


def _ensure_net(root: SexpList, net_name: str) -> int:
    """Ensure a net declaration exists. Returns the net number."""
    return _ensure_net_tracked(root, net_name)[0]


def _ensure_net_tracked(root: SexpList, net_name: str) -> tuple[int, bool]:
    """Ensure a net declaration exists. Returns (net number, whether it was added)."""
    max_net = 0
    for net_node in root.find_all("net"):
        if len(net_node.children) >= 3:
            num = int(net_node.children[1])
            if str(net_node.children[2]) == net_name:
                return num, False
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

    return new_num, True


# ── Footprint geometry: rotate and flip ─────────────────────────────────────


def _set_footprint_rotation(fp: SexpList, rotation: float) -> None:
    """Set a footprint's orientation the way KiCad stores it.

    Pad and text positions are footprint-local and don't change, but their
    angles are stored absolute, so each is shifted by the rotation delta.
    """
    at_node = fp.find("at")
    if at_node is None:
        at_node = parse("(at 0 0)")
        fp.children.insert(2, at_node)
    old = _at_angle(at_node)
    new = _norm180(rotation)
    delta = new - old
    _set_at(at_node, at_node.children[1], at_node.children[2], new, keep_zero=False)
    if delta == 0:
        return

    for child in fp.children:
        if not isinstance(child, SexpList):
            continue
        child_at = child.find("at")
        if child_at is None or len(child_at.children) < 3:
            continue
        if child.tag == "pad":
            angle = _norm360(_at_angle(child_at) + delta)
            _set_at(child_at, child_at.children[1], child_at.children[2], angle, keep_zero=False)
        elif _is_text(child):
            angle = _norm180(_at_angle(child_at) + delta)
            _set_at(child_at, child_at.children[1], child_at.children[2], angle, keep_zero=True)


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

_CHAMFER_FLIP = {
    "top_left": "bottom_left", "bottom_left": "top_left",
    "top_right": "bottom_right", "bottom_right": "top_right",
}

# Nodes whose first two values are a footprint-local point.
_POINT_TAGS = frozenset({"at", "start", "end", "mid", "center", "xy"})


def _flip_layer(layer_str: str) -> str:
    """Flip a layer name from front to back or vice versa."""
    return _LAYER_FLIP.get(layer_str, layer_str)


def _flip_layers_recursive(node: SexpList) -> None:
    """Recursively flip all layer references in a node tree."""
    for child in node.children:
        if isinstance(child, SexpList):
            if child.tag == "layer" and len(child.children) >= 2:
                old = str(child.children[1])
                flipped = _flip_layer(old)
                if flipped != old:
                    child.children[1] = QuotedString(flipped)
            elif child.tag == "layers":
                # Pad layers list: (layers "F.Cu" "F.Paste" "F.Mask")
                for j in range(1, len(child.children)):
                    if isinstance(child.children[j], str):
                        old = str(child.children[j])
                        flipped = _flip_layer(old)
                        if flipped != old:
                            child.children[j] = QuotedString(flipped)
            else:
                _flip_layers_recursive(child)


def _mirror_y_recursive(node: SexpList, skip: SexpList | None = None) -> None:
    """Negate the Y of every local point below node (not the 3D model or `skip`)."""
    for child in node.children:
        if not isinstance(child, SexpList) or child.tag == "model" or child is skip:
            continue
        if child.tag in _POINT_TAGS and len(child.children) >= 3:
            child.children[2] = -float(child.children[2])
        elif child.tag == "offset" and len(child.children) >= 3:  # drill offset
            child.children[2] = -float(child.children[2])
        elif child.tag == "rect_delta" and len(child.children) >= 3:
            child.children[2] = -float(child.children[2])
        elif child.tag == "chamfer":
            child.children[1:] = [
                _CHAMFER_FLIP.get(str(c), c) for c in child.children[1:]
            ]
        _mirror_y_recursive(child)


def _toggle_text_mirror(text: SexpList) -> None:
    effects = text.find("effects")
    if effects is None:
        effects = parse("(effects (font (size 1 1) (thickness 0.15)))")
        text.append(effects)
    justify = effects.find("justify")
    if justify is None:
        effects.append(parse("(justify mirror)"))
        return
    if "mirror" in [str(c) for c in justify.children[1:]]:
        justify.children = [c for c in justify.children if str(c) != "mirror"]
        if len(justify.children) == 1:
            effects.remove_child(justify)
    else:
        justify.append("mirror")


def _flip_footprint_node(fp: SexpList) -> None:
    """Flip a footprint to the other side of the board, left/right like KiCad.

    Matches pcbnew's FOOTPRINT::Flip(pos, flipLeftRight=True), verified
    against KiCad 8 output: local Y coordinates are mirrored, the
    orientation becomes 180 - angle, pad angles become 180 - angle,
    text is mirrored and kept upright, and every layer swaps sides.
    """
    at_node = fp.find("at")
    if at_node is None:
        at_node = parse("(at 0 0)")
        fp.children.insert(2, at_node)
    old = _at_angle(at_node)
    _set_at(at_node, at_node.children[1], at_node.children[2], _norm180(180 - old), keep_zero=False)

    for child in fp.children:
        if not isinstance(child, SexpList):
            continue
        child_at = child.find("at")
        if child.tag == "pad" and child_at is not None and len(child_at.children) >= 3:
            angle = _norm360(180 - _at_angle(child_at))
            _set_at(child_at, child_at.children[1], child_at.children[2], angle, keep_zero=False)
        elif _is_text(child) and child_at is not None and len(child_at.children) >= 3:
            angle = _norm360(-_at_angle(child_at))
            if angle >= 180:  # keep mirrored text readable
                angle -= 180
            _set_at(child_at, child_at.children[1], child_at.children[2], angle, keep_zero=True)
        if _is_text(child):
            _toggle_text_mirror(child)

    _mirror_y_recursive(fp, skip=at_node)  # the footprint's own position stays put
    _flip_layers_recursive(fp)


def flip_footprint(file_path: str, reference: str, to_layer: str = "B.Cu") -> bool:
    """Flip a footprint to the specified side. Returns True if found.

    Mirrors the footprint geometry (pads, graphics, text) and swaps all
    front/back layers, the same as pressing F in KiCad. Does nothing if the
    footprint is already on to_layer.
    """
    if to_layer not in ("F.Cu", "B.Cu"):
        raise ValueError("to_layer must be 'F.Cu' or 'B.Cu'.")
    root = parse_file(file_path)

    fp = _find_footprint(root, reference)
    if fp is None:
        return False
    layer_node = fp.find("layer")
    if not layer_node or len(layer_node.children) < 2:
        return False
    if str(layer_node.children[1]) == to_layer:
        return True

    _flip_footprint_node(fp)
    write_file(file_path, root)
    return True


# ── Footprint placement ─────────────────────────────────────────────────────


_LIBRARY_ONLY_TAGS = frozenset({"version", "generator", "generator_version"})


def _load_footprint_from_disk(footprint_lib: str) -> SexpList | None:
    """Load a footprint definition from KiCad's installed library files."""
    if ":" not in footprint_lib:
        return None
    lib_name, fp_name = footprint_lib.split(":", 1)
    fp_root = library.load_footprint(lib_name, fp_name)
    if fp_root is None or fp_root.tag not in ("footprint", "module"):
        return None
    fp_root.children[0] = "footprint"
    fp_root.children[1] = QuotedString(footprint_lib)
    # File-level header tokens don't belong inside a board
    fp_root.children = [
        c for c in fp_root.children
        if not (isinstance(c, SexpList) and c.tag in _LIBRARY_ONLY_TAGS)
    ]
    return fp_root


def _place_footprint_in_root(
    root: SexpList,
    footprint_lib: str,
    reference: str,
    value: str,
    x: float,
    y: float,
    rotation: float = 0,
    layer: str = "F.Cu",
    fp_node: SexpList | None = None,
) -> str:
    """Place a footprint into a parsed board tree. Returns the UUID."""
    if layer not in ("F.Cu", "B.Cu"):
        raise ValueError("layer must be 'F.Cu' or 'B.Cu'.")
    if _find_footprint(root, reference) is not None:
        raise ValueError(f"A footprint with reference {reference!r} already exists.")

    if fp_node is None:
        fp_node = _load_footprint_from_disk(footprint_lib)
    if fp_node is None:
        raise ValueError(
            f"Footprint {footprint_lib!r} not found in the KiCad footprint libraries "
            f"at {library.footprint_dir()}. Use list_footprints to search."
        )

    fp_uuid = _new_uuid()

    layer_node = fp_node.find("layer")
    if layer_node:
        layer_node.children = ["layer", QuotedString("F.Cu")]
    else:
        fp_node.children.insert(2, parse('(layer "F.Cu")'))

    uuid_node = fp_node.find("uuid")
    if uuid_node:
        uuid_node.children = ["uuid", QuotedString(fp_uuid)]
    else:
        idx = fp_node.children.index(fp_node.find("layer")) + 1
        fp_node.children.insert(idx, parse(f'(uuid "{fp_uuid}")'))

    at_node = fp_node.find("at")
    if at_node is None:
        idx = fp_node.children.index(fp_node.find("uuid")) + 1
        fp_node.children.insert(idx, parse(f"(at {x} {y})"))
    else:
        _set_at(at_node, x, y, _at_angle(at_node), keep_zero=False)

    for prop in fp_node.find_all("property"):
        if len(prop.children) >= 3:
            if str(prop.children[1]) == "Reference":
                prop.children[2] = QuotedString(reference)
            elif str(prop.children[1]) == "Value":
                prop.children[2] = QuotedString(value)

    # Library footprints are drawn on the front; flip first, then orient.
    if layer == "B.Cu":
        _flip_footprint_node(fp_node)
    _set_footprint_rotation(fp_node, rotation)

    root.children.append(fp_node)
    return fp_uuid


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
    """Place a footprint on the PCB. Returns the UUID.

    Raises ValueError if the footprint isn't in the libraries or the
    reference is already used.
    """
    root = _load_or_create(file_path)
    fp_uuid = _place_footprint_in_root(root, footprint_lib, reference, value, x, y, rotation, layer)
    write_file(file_path, root)
    return fp_uuid


def move_footprint(
    file_path: str, reference: str, x: float, y: float, rotation: float | None = None
) -> bool:
    """Move an existing footprint to new position. Returns True if found.

    Keeps the current rotation unless a new one is given.
    """
    root = parse_file(file_path)

    fp = _find_footprint(root, reference)
    if fp is None:
        return False
    at_node = fp.find("at")
    if at_node is None:
        at_node = parse(f"(at {x} {y})")
        fp.children.insert(2, at_node)
    _set_at(at_node, x, y, _at_angle(at_node), keep_zero=False)
    if rotation is not None:
        _set_footprint_rotation(fp, rotation)
    write_file(file_path, root)
    return True


def add_trace(
    file_path: str,
    net_name: str,
    layer: str,
    width: float,
    points: list[tuple[float, float]],
) -> str:
    """Add copper trace segments. Returns UUID of the last segment."""
    if len(points) < 2:
        raise ValueError("A trace requires at least 2 points.")
    root = _load_or_create(file_path)
    last_uuid = _add_trace_to_root(root, net_name, layer, width, points)
    write_file(file_path, root)
    return last_uuid


def _add_trace_to_root(
    root: SexpList, net_name: str, layer: str, width: float, points: list[tuple[float, float]]
) -> str:
    net_num = _ensure_net(root, net_name)
    last_uuid = ""
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        if (x1, y1) == (x2, y2):
            continue
        seg_uuid = _new_uuid()
        last_uuid = seg_uuid
        seg_sexp = (
            f'(segment (start {x1} {y1}) (end {x2} {y2}) (width {width}) '
            f'(layer "{esc(layer)}") (net {net_num}) (uuid "{seg_uuid}"))'
        )
        root.children.append(parse(seg_sexp))
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
    root = _load_or_create(file_path)
    via_uuid = _add_via_to_root(root, net_name, x, y, size, drill)
    write_file(file_path, root)
    return via_uuid


def _add_via_to_root(
    root: SexpList, net_name: str, x: float, y: float, size: float, drill: float
) -> str:
    net_num = _ensure_net(root, net_name)
    via_uuid = _new_uuid()
    via_sexp = (
        f'(via (at {x} {y}) (size {size}) (drill {drill}) '
        f'(layers "F.Cu" "B.Cu") (net {net_num}) (uuid "{via_uuid}"))'
    )
    root.children.append(parse(via_sexp))
    return via_uuid


ZONE_FILL_TYPES = ("solid", "hatch")


def add_zone(
    file_path: str,
    net_name: str,
    layer: str,
    outline_points: list[tuple[float, float]],
    fill_type: str = "solid",
) -> str:
    """Add a copper zone/pour. Returns the UUID.

    fill_type is "solid" or "hatch" (a 1 mm grid of 1.5 mm openings).
    KiCad computes the actual fill when the board is refilled (B key).
    """
    if fill_type not in ZONE_FILL_TYPES:
        raise ValueError(f"fill_type must be one of {ZONE_FILL_TYPES}, not {fill_type!r}.")
    if len(outline_points) < 3:
        raise ValueError("A zone outline needs at least 3 points.")

    root = _load_or_create(file_path)

    net_num = _ensure_net(root, net_name)
    zone_uuid = _new_uuid()

    hatch = (
        " (mode hatch) (hatch_thickness 1) (hatch_gap 1.5) (hatch_orientation 0)"
        " (hatch_border_algorithm hatch_thickness) (hatch_min_hole_area 0.3)"
        if fill_type == "hatch" else ""
    )
    pts_str = " ".join(f"(xy {x} {y})" for x, y in outline_points)
    zone_sexp = (
        f'(zone (net {net_num}) (net_name "{esc(net_name)}") (layer "{esc(layer)}") '
        f'(uuid "{zone_uuid}") (hatch edge 0.5) '
        f'(connect_pads (clearance 0.5)) (min_thickness 0.25) (filled_areas_thickness no) '
        f'(fill yes{hatch} (thermal_gap 0.5) (thermal_bridge_width 0.5)) '
        f'(polygon (pts {pts_str})))'
    )
    root.children.append(parse(zone_sexp))

    write_file(file_path, root)
    return zone_uuid


def set_board_outline(file_path: str, outline_points: list[tuple[float, float]]) -> bool:
    """Set the board outline on Edge.Cuts layer. Returns True on success."""
    if len(outline_points) < 3:
        raise ValueError("A board outline needs at least 3 points.")
    root = _load_or_create(file_path)

    # Remove existing Edge.Cuts geometry
    for child in list(root.children):
        if isinstance(child, SexpList) and child.tag in (
            "gr_line", "gr_rect", "gr_arc", "gr_poly", "gr_circle", "gr_curve",
        ) and _on_edge_cuts(child):
            root.remove_child(child)

    # Add new outline as line segments
    for i in range(len(outline_points)):
        x1, y1 = outline_points[i]
        x2, y2 = outline_points[(i + 1) % len(outline_points)]
        line_sexp = (
            f'(gr_line (start {x1} {y1}) (end {x2} {y2}) '
            f'(stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "{_new_uuid()}"))'
        )
        root.children.append(parse(line_sexp))

    write_file(file_path, root)
    return True


def assign_net_to_pad(file_path: str, footprint_ref: str, pad_number: str, net_name: str) -> bool:
    """Assign a net to a pad on a footprint. Returns True if found.

    Every pad with the given number is updated (footprints repeat numbers
    for split thermal pads and the like).
    """
    root = parse_file(file_path)

    fp = _find_footprint(root, footprint_ref)
    if fp is None:
        return False
    pads = [p for p in fp.find_all("pad") if len(p.children) >= 2 and str(p.children[1]) == pad_number]
    if not pads:
        return False

    net_num = _ensure_net(root, net_name)
    for pad in pads:
        _set_pad_net(pad, net_num, net_name)
    write_file(file_path, root)
    return True


def _set_pad_net(pad: SexpList, net_num: int, net_name: str) -> None:
    net_node = pad.find("net")
    if net_node:
        net_node.children = ["net", net_num, QuotedString(net_name)]
    else:
        pad.children.append(parse(f'(net {net_num} "{esc(net_name)}")'))


def _next_reference(root: SexpList, prefix: str) -> str:
    used = set()
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    for fp in root.find_all("footprint"):
        m = pattern.match(_get_fp_reference(fp))
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"{prefix}{n}"


def _library_mounting_hole(drill_size: float, pad_size: float | None) -> SexpList | None:
    """Find a plated MountingHole library footprint with this drill (and pad) size."""
    lib_dir = library.footprint_dir() / "MountingHole.pretty"
    if not lib_dir.exists():
        return None
    # Shortest name first: prefer the plain "..._M3_Pad" over "..._M3_DIN965_Pad"
    matches = lib_dir.glob(f"MountingHole_{drill_size:g}mm_M*_Pad.kicad_mod")
    for path in sorted(matches, key=lambda p: (len(p.stem), p.stem)):
        fp = _load_footprint_from_disk(f"MountingHole:{path.stem}")
        if fp is None:
            continue
        pads = fp.find_all("pad")
        if len(pads) != 1:
            continue
        size, drill = pads[0].find("size"), pads[0].find("drill")
        if not size or not drill:
            continue
        if abs(float(drill.children[1]) - drill_size) > 1e-6:
            continue
        if pad_size is not None and abs(float(size.children[1]) - pad_size) > 1e-6:
            continue
        return fp
    return None


def add_mounting_hole(
    file_path: str, x: float, y: float, drill_size: float = 3.2, pad_size: float | None = None
) -> str:
    """Add a plated mounting hole footprint with the next free H reference. Returns the UUID.

    Uses the matching KiCad MountingHole library footprint when one exists
    (e.g. MountingHole_3.2mm_M3_Pad for a 3.2 mm drill); otherwise builds
    one with the requested pad size (default: twice the drill).
    """
    root = _load_or_create(file_path)
    ref = _next_reference(root, "H")

    fp_node = _library_mounting_hole(drill_size, pad_size)
    if fp_node is not None:
        footprint_lib = str(fp_node.children[1])
    else:
        pad = pad_size if pad_size is not None else round(drill_size * 2, 4)
        footprint_lib = f"MountingHole_{drill_size:g}mm_Pad_D{pad:g}mm"
        courtyard = round(pad / 2 + 0.25, 4)
        fp_node = parse(
            f'(footprint "{footprint_lib}" (layer "F.Cu") '
            f'(property "Reference" "{ref}" (at 0 {-(pad / 2 + 1)} 0) (layer "F.SilkS") '
            f'(effects (font (size 1 1) (thickness 0.15)))) '
            f'(property "Value" "MountingHole" (at 0 {pad / 2 + 1} 0) (layer "F.Fab") '
            f'(effects (font (size 1 1) (thickness 0.15)))) '
            f'(attr exclude_from_pos_files exclude_from_bom) '
            f'(fp_circle (center 0 0) (end {courtyard} 0) (stroke (width 0.05) (type solid)) '
            f'(fill none) (layer "F.CrtYd") (uuid "{_new_uuid()}")) '
            f'(pad "1" thru_hole circle (at 0 0) (size {pad} {pad}) (drill {drill_size}) '
            f'(layers "*.Cu" "*.Mask") (remove_unused_layers no) (uuid "{_new_uuid()}")))'
        )

    fp_uuid = _place_footprint_in_root(
        root, footprint_lib, ref, "MountingHole", x, y, fp_node=fp_node,
    )
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

    Returns list of UUIDs for all placed footprints. The file is read and
    written once.
    """
    if pattern not in ("grid", "circular"):
        raise ValueError(f"Unknown pattern: {pattern!r}. Use 'grid' or 'circular'.")

    if count < 1:
        raise ValueError("count must be >= 1")

    root = _load_or_create(file_path)
    uuids: list[str] = []

    for i in range(count):
        if pattern == "grid":
            cols = columns if columns else count  # default: single row
            x = start_x + (i % cols) * spacing_x
            y = start_y + (i // cols) * spacing_y
            fp_rotation = rotation
        else:
            angle_rad = 2 * math.pi * i / count
            x = round(start_x + radius * math.cos(angle_rad), 6)
            y = round(start_y + radius * math.sin(angle_rad), 6)
            fp_rotation = rotation + math.degrees(angle_rad)
        ref = f"{reference_prefix}{start_index + i}"
        uuids.append(_place_footprint_in_root(
            root, footprint_lib, ref, value, x, y, fp_rotation, layer,
        ))

    write_file(file_path, root)
    return uuids


# ── Autorouting ─────────────────────────────────────────────────────────────


def autoroute(
    file_path: str,
    freerouting_jar: str | None = None,
    timeout: int = 300,
    strategy: str = "freerouting",
) -> dict:
    """Route a PCB.

    Strategies:
    - "freerouting" (default): Freerouting (requires pcbnew + Java). "auto"
      is accepted as an alias. Failures are raised, never silently replaced
      by the simple router.
    - "simple": naive L-shaped routing with no obstacle avoidance; a
      starting point that needs DRC and manual cleanup.

    Returns dict with status and details.
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"PCB file not found: {file_path}")

    if strategy in ("freerouting", "auto"):
        try:
            return _autoroute_freerouting(file_path, freerouting_jar, timeout)
        except RuntimeError as e:
            raise RuntimeError(
                f"Freerouting failed: {e} The board was not modified. "
                "Fix the issue above, or use strategy='simple' for rough L-shaped routing."
            ) from e
    if strategy == "simple":
        return _autoroute_simple(file_path)
    raise ValueError(f"Unknown strategy {strategy!r}; use 'freerouting' or 'simple'.")


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
_VIA_CLEARANCE = 0.2


def _pad_copper_layers(pad: SexpList) -> set[str]:
    layers_node = pad.find("layers")
    names = {str(c) for c in layers_node.children[1:]} if layers_node else set()
    result = set()
    if "*.Cu" in names or "F&B.Cu" in names:
        result |= {"F.Cu", "B.Cu"}
    result |= names & {"F.Cu", "B.Cu"}
    return result


def _collect_routable_pads(root: SexpList) -> dict[str, list[dict]]:
    """Map net name -> pads with absolute position, copper layers and size."""
    net_pads: dict[str, list[dict]] = {}
    for fp in root.find_all("footprint"):
        fp_at = fp.find("at")
        fx = float(fp_at.children[1]) if fp_at and len(fp_at.children) >= 3 else 0.0
        fy = float(fp_at.children[2]) if fp_at and len(fp_at.children) >= 3 else 0.0
        frot = _at_angle(fp_at) if fp_at else 0.0
        ref = _get_fp_reference(fp)
        for pad in fp.find_all("pad"):
            net_node = pad.find("net")
            if not net_node or len(net_node.children) < 3 or not str(net_node.children[2]):
                continue
            pad_at = pad.find("at")
            if not pad_at or len(pad_at.children) < 3:
                continue
            px, py = float(pad_at.children[1]), float(pad_at.children[2])
            ax, ay = _pad_absolute_position(fx, fy, frot, px, py)
            size = pad.find("size")
            w = float(size.children[1]) if size and len(size.children) >= 2 else 0.0
            h = float(size.children[2]) if size and len(size.children) >= 3 else w
            net_pads.setdefault(str(net_node.children[2]), []).append({
                "ref": ref,
                "pad": str(pad.children[1]),
                "x": ax,
                "y": ay,
                "fx": fx,
                "fy": fy,
                "layers": _pad_copper_layers(pad),
                "thru_hole": len(pad.children) >= 3 and str(pad.children[2]) == "thru_hole",
                "radius": max(w, h) / 2,
            })
    return net_pads


def _autoroute_simple(file_path: str) -> dict:
    """Naive L-shaped router. Reads and writes the board once.

    - Pads of a net are chained in order with L-shaped tracks on a copper
      layer both pads reach; pairs with no common layer are reported as
      unrouted.
    - If a GND zone exists, surface-mount GND pads on the other side get a
      short stub and a via just outside the pad instead of tracks;
      through-hole GND pads already reach the zone.
    - Existing identical segments and vias are not duplicated, so running
      it twice is harmless.

    It does not avoid other tracks, pads or keepouts: expect DRC errors.
    """
    root = parse_file(file_path)
    net_pads = _collect_routable_pads(root)

    existing_segments: set[tuple] = set()
    for seg in root.find_all("segment"):
        s, e = _xy(seg.find("start")), _xy(seg.find("end"))
        layer = seg.find("layer")
        if s and e and layer:
            lname = str(layer.children[1])
            existing_segments.add((round(s[0], 4), round(s[1], 4), round(e[0], 4), round(e[1], 4), lname))
            existing_segments.add((round(e[0], 4), round(e[1], 4), round(s[0], 4), round(s[1], 4), lname))
    existing_vias = [p for p in (_xy(v.find("at")) for v in root.find_all("via")) if p]

    gnd_zone_layers = {
        str(z.find("layer").children[1])
        for z in root.find_all("zone")
        if z.find("net_name") and str(z.find("net_name").children[1]).upper() == "GND"
        and z.find("layer")
    }

    traces_added = 0
    vias_added = 0
    nets_routed = 0
    unrouted: list[dict] = []

    def add_path(net: str, layer: str, width: float, pts: list[tuple[float, float]]) -> int:
        added = 0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            key = (round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4), layer)
            if (x1, y1) == (x2, y2) or key in existing_segments:
                continue
            _add_trace_to_root(root, net, layer, width, [(x1, y1), (x2, y2)])
            existing_segments.add(key)
            existing_segments.add((key[2], key[3], key[0], key[1], layer))
            added += 1
        return added

    for net_name, pads in net_pads.items():
        is_gnd = net_name.upper() == "GND"
        width = _POWER_TRACE_WIDTH if net_name in _POWER_NETS or is_gnd else _SIGNAL_TRACE_WIDTH
        routed_any = False

        if is_gnd and gnd_zone_layers:
            for p in pads:
                if p["thru_hole"] or p["layers"] & gnd_zone_layers:
                    continue  # already touches the plane
                # Step outward from the footprint centre, clear of the pad
                dx, dy = p["x"] - p["fx"], p["y"] - p["fy"]
                length = math.hypot(dx, dy) or 1.0
                ux, uy = (dx / length, dy / length) if (dx or dy) else (1.0, 0.0)
                dist = p["radius"] + _VIA_SIZE / 2 + _VIA_CLEARANCE
                vx, vy = round(p["x"] + ux * dist, 4), round(p["y"] + uy * dist, 4)
                if any(math.hypot(vx - ex, vy - ey) < 0.01 for ex, ey in existing_vias):
                    continue
                layer = next(iter(p["layers"]), "F.Cu")
                traces_added += add_path(net_name, layer, width, [(p["x"], p["y"]), (vx, vy)])
                _add_via_to_root(root, net_name, vx, vy, _VIA_SIZE, _VIA_DRILL)
                existing_vias.append((vx, vy))
                vias_added += 1
                routed_any = True
        else:
            for a, b in zip(pads, pads[1:]):
                common = a["layers"] & b["layers"]
                if not common:
                    unrouted.append({
                        "net": net_name,
                        "from": f"{a['ref']}.{a['pad']}",
                        "to": f"{b['ref']}.{b['pad']}",
                        "reason": "no common copper layer",
                    })
                    continue
                layer = "F.Cu" if "F.Cu" in common else "B.Cu"
                x1, y1, x2, y2 = a["x"], a["y"], b["x"], b["y"]
                pts = [(x1, y1), (x2, y2)] if x1 == x2 or y1 == y2 else [(x1, y1), (x2, y1), (x2, y2)]
                n = add_path(net_name, layer, width, pts)
                traces_added += n
                routed_any = routed_any or n > 0

        if routed_any:
            nets_routed += 1

    if traces_added or vias_added:
        write_file(file_path, root)

    return {
        "status": "success",
        "method": "simple",
        "traces_added": traces_added,
        "vias_added": vias_added,
        "nets_routed": nets_routed,
        "unrouted": unrouted,
        "warning": (
            "The simple router does not avoid obstacles; run DRC and fix "
            "crossings and clearance violations."
        ),
        "file": file_path,
    }


# ── Zones, deletion ─────────────────────────────────────────────────────────


def set_zone_net(file_path: str, zone_uuid: str, net_name: str) -> bool:
    """Assign a net to an existing copper zone by UUID. Returns True if found."""
    root = parse_file(file_path)

    for zone in root.find_all("zone"):
        uuid_node = zone.find("uuid")
        if uuid_node and len(uuid_node.children) >= 2 and str(uuid_node.children[1]) == zone_uuid:
            net_num = _ensure_net(root, net_name)
            net_node = zone.find("net")
            if net_node and len(net_node.children) >= 2:
                net_node.children[1] = net_num
            else:
                zone.children.insert(1, parse(f"(net {net_num})"))
            net_name_node = zone.find("net_name")
            if net_name_node and len(net_name_node.children) >= 2:
                net_name_node.children[1] = QuotedString(net_name)
            else:
                zone.children.append(parse(f'(net_name "{esc(net_name)}")'))
            write_file(file_path, root)
            return True
    return False


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


def delete_footprint(
    file_path: str, reference: str | None = None, target_uuid: str | None = None
) -> dict:
    """Delete a footprint by reference or UUID. Returns {deleted, reference}."""
    if not reference and not target_uuid:
        raise ValueError("Provide at least one of reference or uuid.")

    root = parse_file(file_path)

    for fp in root.find_all("footprint"):
        fp_ref = _get_fp_reference(fp)
        if (reference and fp_ref == reference) or (target_uuid and _fp_uuid(fp) == target_uuid):
            root.remove_child(fp)
            write_file(file_path, root)
            return {"deleted": True, "reference": fp_ref}

    return {"deleted": False}


def delete_footprints_batch(
    file_path: str,
    references: list[str] | None = None,
    uuids: list[str] | None = None,
) -> list[dict]:
    """Delete multiple footprints in a single file read/write cycle.

    Returns one result per requested reference and per requested UUID, in
    request order: {"deleted": bool, "reference": ...} or {"deleted": bool,
    "uuid": ..., "reference": ...}.
    """
    references = references or []
    uuids = uuids or []
    if not references and not uuids:
        return []

    root = parse_file(file_path)
    by_ref: dict[str, SexpList] = {}
    by_uuid: dict[str, SexpList] = {}
    for fp in root.find_all("footprint"):
        by_ref.setdefault(_get_fp_reference(fp), fp)
        by_uuid.setdefault(_fp_uuid(fp), fp)

    results: list[dict] = []
    to_remove: list[SexpList] = []

    def mark(fp: SexpList | None) -> bool:
        if fp is None:
            return False
        if not any(fp is r for r in to_remove):
            to_remove.append(fp)
        return True

    for ref in references:
        results.append({"deleted": mark(by_ref.get(ref)), "reference": ref})
    for u in uuids:
        fp = by_uuid.get(u)
        entry = {"deleted": mark(fp), "uuid": u}
        if fp is not None:
            entry["reference"] = _get_fp_reference(fp)
        results.append(entry)

    for fp in to_remove:
        root.remove_child(fp)
    if to_remove:
        write_file(file_path, root)

    return results


def delete_elements_batch(
    file_path: str, uuids: list[str], element_type: str | None = None
) -> list[bool]:
    """Delete multiple PCB elements by UUID in a single read/write cycle.

    If element_type is specified ("segment", "via", "zone", etc.), only
    matches top-level elements of that type. Otherwise matches any element.
    """
    if not uuids:
        return []

    root = parse_file(file_path)
    target_set = set(uuids)
    found: dict[str, bool] = {u: False for u in uuids}

    for child in list(root.children):
        if not isinstance(child, SexpList):
            continue
        if element_type and child.tag != element_type:
            continue
        uuid_node = child.find("uuid")
        if uuid_node and len(uuid_node.children) >= 2:
            child_uuid = str(uuid_node.children[1])
            if child_uuid in target_set:
                root.remove_child(child)
                found[child_uuid] = True

    if any(found.values()):
        write_file(file_path, root)

    return [found[u] for u in uuids]


# ── Schematic sync ──────────────────────────────────────────────────────────


def sync_from_schematic(schematic_path: str, pcb_path: str) -> dict:
    """Update a PCB from its schematic: nets, missing footprints, pad nets.

    Exports a netlist with kicad-cli. If that fails, falls back to reading
    the schematic directly, which can add footprints but can't compute pad
    nets; the result says so in "netlist_source" and "warnings".
    Reads and writes the board once.
    """
    import tempfile
    import xml.etree.ElementTree as ET

    from . import cli, schematic

    components: list[dict] = []  # {ref, value, footprint}
    pin_nets: dict[tuple[str, str], str] = {}  # (ref, pin) -> net_name
    net_names: set[str] = set()
    warnings: list[str] = []
    netlist_source = "kicad-cli"

    with tempfile.TemporaryDirectory() as tmp_dir:
        netlist_path = str(Path(tmp_dir) / "netlist.xml")
        try:
            # XML format includes connected power nets (+3V3, +5V, etc.)
            cli.export_netlist(schematic_path, netlist_path, fmt="kicadxml")
            root_xml = ET.parse(netlist_path).getroot()
        except (RuntimeError, OSError, ET.ParseError) as e:
            root_xml = None
            netlist_source = "schematic"
            warnings.append(
                f"kicad-cli netlist export failed ({e}); pad nets were not updated."
            )
            logger.warning("sync: netlist export failed, falling back to schematic parse: %s", e)

    sch_data = schematic.read_schematic(schematic_path)

    if root_xml is not None:
        for comp in root_xml.iter("comp"):
            ref = comp.get("ref", "")
            if not ref or ref.startswith("#"):
                continue
            value_el = comp.find("value")
            fp_el = comp.find("footprint")
            components.append({
                "ref": ref,
                "value": value_el.text if value_el is not None and value_el.text else "",
                "footprint": fp_el.text if fp_el is not None and fp_el.text else "",
            })
        for net_el in root_xml.iter("net"):
            name = net_el.get("name", "")
            if not name:
                continue
            net_names.add(name)
            for node in net_el.findall("node"):
                ref = node.get("ref", "")
                pin = node.get("pin", "")
                if ref and pin:
                    pin_nets[(ref, pin)] = name
    else:
        seen: set[str] = set()
        for sym in sch_data.symbols:
            ref = sym.reference
            if ref and not ref.startswith("#") and "?" not in ref and ref not in seen:
                seen.add(ref)
                components.append({"ref": ref, "value": sym.value, "footprint": sym.footprint})

    # The netlist omits unconnected power nets (e.g. GND before any wires),
    # but they should still be declared in the PCB.
    for sym in sch_data.symbols:
        if sym.lib_id.startswith("power:") and sym.value:
            net_names.add(sym.value)

    root = _load_or_create(pcb_path)

    added_nets: list[str] = []
    for name in sorted(net_names):
        if _ensure_net_tracked(root, name)[1]:
            added_nets.append(name)

    existing_refs = {_get_fp_reference(fp) for fp in root.find_all("footprint")}
    added_fps: list[str] = []
    skipped: list[dict] = []
    x_offset = 50.0
    for comp in components:
        if comp["ref"] in existing_refs:
            continue
        if not comp["footprint"]:
            skipped.append({"ref": comp["ref"], "reason": "no footprint assigned"})
            continue
        try:
            _place_footprint_in_root(
                root, comp["footprint"], comp["ref"], comp["value"], x_offset, 50, 0, "F.Cu",
            )
        except ValueError as e:
            skipped.append({"ref": comp["ref"], "reason": str(e)})
            continue
        existing_refs.add(comp["ref"])
        added_fps.append(comp["ref"])
        x_offset += 10.0

    updated_pads = 0
    for fp_node in root.find_all("footprint"):
        fp_ref = _get_fp_reference(fp_node)
        if not fp_ref:
            continue
        for pad_node in fp_node.find_all("pad"):
            if len(pad_node.children) < 2:
                continue
            target_net = pin_nets.get((fp_ref, str(pad_node.children[1])))
            if target_net is None:
                continue
            _set_pad_net(pad_node, _ensure_net(root, target_net), target_net)
            updated_pads += 1

    write_file(pcb_path, root)

    result = {
        "added_nets": added_nets,
        "added_footprints": added_fps,
        "skipped_footprints": skipped,
        "updated_pads": updated_pads,
        "total_components": len(components),
        "total_pin_net_mappings": len(pin_nets),
        "netlist_source": netlist_source,
    }
    if warnings:
        result["warnings"] = warnings
    return result
