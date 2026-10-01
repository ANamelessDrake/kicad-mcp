"""Project-level settings: net classes, board design rules, custom DRC rules.

These live next to the board: net classes and constraints in the .kicad_pro
JSON, custom rules in the .kicad_dru text file.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Board constraint keys KiCad 8 stores under board.design_settings.rules (mm)
DESIGN_RULE_KEYS = (
    "min_clearance",
    "min_connection",
    "min_copper_edge_clearance",
    "min_hole_clearance",
    "min_hole_to_hole",
    "min_microvia_diameter",
    "min_microvia_drill",
    "min_resolved_spokes",
    "min_silk_clearance",
    "min_text_height",
    "min_text_thickness",
    "min_through_hole_diameter",
    "min_track_width",
    "min_via_annular_width",
    "min_via_diameter",
    "solder_mask_to_copper_clearance",
)

_NET_CLASS_NUMBERS = ("clearance", "track_width", "via_diameter", "via_drill",
                      "diff_pair_width", "diff_pair_gap")


def project_path(path: str) -> Path:
    """The .kicad_pro that belongs with a .kicad_pro, .kicad_pcb or .kicad_sch path."""
    p = Path(path)
    return p if p.suffix == ".kicad_pro" else p.with_suffix(".kicad_pro")


def _load(path: str) -> tuple[Path, dict]:
    pro = project_path(path)
    if not pro.exists():
        raise FileNotFoundError(f"Project file not found: {pro}")
    return pro, json.loads(pro.read_text(encoding="utf-8"))


def _write_text(target: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        # mkstemp creates 0600; keep the target's mode, or use the umask default
        if target.exists():
            os.chmod(tmp, target.stat().st_mode & 0o7777)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _save(pro: Path, data: dict) -> None:
    # KiCad writes 2-space indented JSON with a trailing newline
    _write_text(pro, json.dumps(data, indent=2) + "\n")


def list_net_classes(path: str) -> dict:
    """Net classes and the net-name patterns assigned to each."""
    _, data = _load(path)
    ns = data.get("net_settings", {})
    patterns = ns.get("netclass_patterns") or []
    return {
        "classes": [
            {k: c.get(k) for k in ("name", *_NET_CLASS_NUMBERS)} for c in ns.get("classes", [])
        ],
        "patterns": patterns,
    }


def set_net_class(
    path: str,
    name: str,
    nets: list[str] | None = None,
    clearance: float | None = None,
    track_width: float | None = None,
    via_diameter: float | None = None,
    via_drill: float | None = None,
) -> dict:
    """Create or update a net class and assign nets to it.

    A new class starts as a copy of Default. Given nets are assigned by exact
    name pattern and removed from any other class's patterns. Passing nets
    replaces the class's previous pattern list; omitting it keeps it.
    """
    pro, data = _load(path)
    ns = data.setdefault("net_settings", {})
    classes = ns.setdefault("classes", [])
    cls = next((c for c in classes if c.get("name") == name), None)
    if cls is None:
        default = next((c for c in classes if c.get("name") == "Default"), {})
        cls = dict(default)
        cls["name"] = name
        classes.append(cls)
    for key, value in (("clearance", clearance), ("track_width", track_width),
                       ("via_diameter", via_diameter), ("via_drill", via_drill)):
        if value is not None:
            if value <= 0:
                raise ValueError(f"{key} must be positive")
            cls[key] = value
    if (cls.get("via_drill") or 0) >= (cls.get("via_diameter") or 0) > 0:
        raise ValueError("via_drill must be smaller than via_diameter")

    if nets is not None:
        if name == "Default":
            raise ValueError("Nets not matched by a pattern already use Default; assign them to another class.")
        wanted = set(nets)
        patterns = [
            p for p in (ns.get("netclass_patterns") or [])
            if p.get("netclass") != name and p.get("pattern") not in wanted
        ]
        patterns += [{"netclass": name, "pattern": n} for n in nets]
        ns["netclass_patterns"] = patterns

    _save(pro, data)
    return {
        "name": name,
        **{k: cls.get(k) for k in _NET_CLASS_NUMBERS},
        "nets": [p["pattern"] for p in ns.get("netclass_patterns") or [] if p.get("netclass") == name],
    }


def set_design_rules(path: str, rules: dict[str, float]) -> dict:
    """Update board-wide constraints (Board Setup > Constraints), in mm."""
    unknown = sorted(set(rules) - set(DESIGN_RULE_KEYS))
    if unknown:
        raise ValueError(f"Unknown rule(s) {unknown}; valid keys: {', '.join(DESIGN_RULE_KEYS)}")
    pro, data = _load(path)
    current = data.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rules", {})
    current.update(rules)
    _save(pro, data)
    return dict(current)


def set_custom_rules(path: str, rules: str) -> str:
    """Write the project's custom DRC rules file (<project>.kicad_dru).

    rules is the body in KiCad's custom rule syntax, e.g.
    (rule "name" (condition "A.memberOfFootprint('U1')") (constraint hole_size (min 0.2mm))).
    Board constraints (project_set_design_rules) are hard floors: a rule can
    only relax a limit down to them, so lower the floor and add a rule that
    restores it for everything else.
    A "(version 1)" header is added if missing. Replaces the whole file.
    """
    text = rules.strip()
    depth = 0
    in_str = False
    for ch in text:
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            depth += {"(": 1, ")": -1}.get(ch, 0)
            if depth < 0:
                raise ValueError("Unbalanced parentheses in rules")
    if depth != 0 or in_str:
        raise ValueError("Unbalanced parentheses or quotes in rules")
    if not text.startswith("(version"):
        text = "(version 1)\n" + text
    target = project_path(path).with_suffix(".kicad_dru")
    _write_text(target, text + "\n")
    return str(target)
