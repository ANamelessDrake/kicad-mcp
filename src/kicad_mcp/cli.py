"""Wrapper for kicad-cli commands (ERC, DRC, exports)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .types import DRCViolation, ERCViolation, Point


_pcbnew_python_cache: str | None = None


def _find_pcbnew_python() -> str | None:
    """Find a Python interpreter that has the pcbnew module. Cached after first call."""
    global _pcbnew_python_cache
    if _pcbnew_python_cache is not None:
        return _pcbnew_python_cache

    import sys
    env = {**__import__("os").environ, "DISPLAY": "", "WAYLAND_DISPLAY": ""}
    for python in [sys.executable, "python3.13", "python3.12", "python3"]:
        py = shutil.which(python) if not python.startswith("/") else python
        if not py:
            continue
        try:
            result = subprocess.run(
                [py, "-c", "import pcbnew; print('ok')"],
                capture_output=True, text=True, timeout=10, env=env,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                _pcbnew_python_cache = py
                return py
        except (subprocess.TimeoutExpired, OSError):
            continue
    return None


def _run_pcbnew(script: str, timeout: int = 60) -> str:
    """Run a Python script that uses pcbnew, finding the right interpreter.

    Returns stdout. Raises RuntimeError if pcbnew is not available.
    Runs headless (DISPLAY unset) to prevent GUI initialization.
    """
    py = _find_pcbnew_python()
    if not py:
        raise RuntimeError(
            "pcbnew Python module not found on any Python interpreter. "
            "Install KiCad 8 (it bundles pcbnew with its Python)."
        )
    env = {**__import__("os").environ, "DISPLAY": "", "WAYLAND_DISPLAY": ""}
    result = subprocess.run(
        [py, "-c", script],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pcbnew script failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _find_kicad_cli() -> str | None:
    """Find the kicad-cli executable."""
    return shutil.which("kicad-cli")


def _parse_kicad_violations(report: dict) -> list[dict]:
    """Extract violations from kicad-cli JSON report.

    kicad-cli nests violations under sheets[].violations[]. Each violation
    has a description, severity, type, and items[] with pos info.
    """
    violations: list[dict] = []
    # Try top-level violations (future-proofing)
    violations.extend(report.get("violations", []))
    # Parse sheet-level violations (actual kicad-cli 8 format)
    for sheet in report.get("sheets", []):
        violations.extend(sheet.get("violations", []))
    # Also check unconnected_items for DRC
    violations.extend(report.get("unconnected_items", []))
    return violations


def run_erc(file_path: str) -> list[ERCViolation]:
    """Run ERC on a schematic using kicad-cli. Returns list of violations."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found. Install KiCad 8 to use ERC.")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        report_path = f.name

    try:
        result = subprocess.run(
            [cli, "sch", "erc", "--output", report_path, "--format", "json",
             "--severity-all", file_path],
            capture_output=True,
            text=True,
            timeout=60,
        )

        violations: list[ERCViolation] = []
        report_file = Path(report_path)
        if report_file.exists() and report_file.stat().st_size > 0:
            try:
                report = json.loads(report_file.read_text())
                for v in _parse_kicad_violations(report):
                    loc = None
                    # Position is on the first item, not the violation itself
                    items = v.get("items", [])
                    if items and "pos" in items[0]:
                        pos = items[0]["pos"]
                        loc = Point(pos.get("x", 0), pos.get("y", 0))
                    violations.append(ERCViolation(
                        severity=v.get("severity", "error"),
                        message=v.get("description", str(v)),
                        location=loc,
                    ))
            except (json.JSONDecodeError, KeyError):
                violations.append(ERCViolation(
                    severity="error",
                    message=f"ERC report parse error: {result.stderr.strip() or result.stdout.strip()}",
                ))
        elif result.returncode != 0:
            violations.append(ERCViolation(
                severity="error",
                message=f"ERC failed: {result.stderr.strip() or result.stdout.strip()}",
            ))

        return violations
    finally:
        Path(report_path).unlink(missing_ok=True)


def run_drc(file_path: str) -> list[DRCViolation]:
    """Run DRC on a PCB using kicad-cli. Returns list of violations."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found. Install KiCad 8 to use DRC.")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        report_path = f.name

    try:
        result = subprocess.run(
            [cli, "pcb", "drc", "--output", report_path, "--format", "json",
             "--severity-all", "--schematic-parity", file_path],
            capture_output=True,
            text=True,
            timeout=60,
        )

        violations: list[DRCViolation] = []
        report_file = Path(report_path)
        if report_file.exists() and report_file.stat().st_size > 0:
            try:
                report = json.loads(report_file.read_text())
                for v in _parse_kicad_violations(report):
                    loc = None
                    items = v.get("items", [])
                    if items and "pos" in items[0]:
                        pos = items[0]["pos"]
                        loc = Point(pos.get("x", 0), pos.get("y", 0))
                    violations.append(DRCViolation(
                        severity=v.get("severity", "error"),
                        message=v.get("description", str(v)),
                        location=loc,
                    ))
            except (json.JSONDecodeError, KeyError):
                violations.append(DRCViolation(
                    severity="error",
                    message=f"DRC report parse error: {result.stderr.strip() or result.stdout.strip()}",
                ))
        elif result.returncode != 0:
            violations.append(DRCViolation(
                severity="error",
                message=f"DRC failed: {result.stderr.strip() or result.stdout.strip()}",
            ))

        return violations
    finally:
        Path(report_path).unlink(missing_ok=True)


def export_pcb_image(
    file_path: str,
    output_path: str,
    layers: list[str] | None = None,
    dpi: int = 300,
) -> str:
    """Export a PCB image (SVG) using kicad-cli."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found.")

    if layers is None:
        layers = ["F.Cu", "B.Cu", "Edge.Cuts"]

    cmd = [cli, "pcb", "export", "svg", "--layers", ",".join(layers), "--output", output_path, file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Export failed: {result.stderr.strip()}")
    return output_path


def export_3d(file_path: str, output_path: str, fmt: str = "step") -> str:
    """Export 3D model using kicad-cli."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found.")

    cmd = [cli, "pcb", "export", fmt, "--output", output_path, file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"3D export failed: {result.stderr.strip()}")
    return output_path


def export_gerbers(file_path: str, output_dir: str) -> list[str]:
    """Export Gerber + drill files using kicad-cli."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found.")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    before = _dir_snapshot(out)

    # Export gerbers
    cmd = [cli, "pcb", "export", "gerbers", "--output", output_dir + "/", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Gerber export failed: {result.stderr.strip()}")

    # Export drill files
    cmd = [cli, "pcb", "export", "drill", "--output", output_dir + "/", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Drill export failed: {result.stderr.strip()}")

    # Only report files this run wrote, not leftovers already in the folder
    after = _dir_snapshot(out)
    return sorted(str(out / name) for name, stamp in after.items() if before.get(name) != stamp)


def _dir_snapshot(directory: Path) -> dict[str, int]:
    """Map file name -> modification time (ns) for files directly in directory."""
    return {p.name: p.stat().st_mtime_ns for p in directory.iterdir() if p.is_file()}


def export_position_file(
    file_path: str, output_path: str, fmt: str = "csv", units: str = "mm",
    smd_only: bool = True, exclude_dnp: bool = True,
) -> str:
    """Export component position (pick-and-place) file."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found.")

    cmd = [
        cli, "pcb", "export", "pos",
        "--format", fmt, "--units", units, "--output", output_path,
    ]
    if smd_only:
        cmd.append("--smd-only")
    if exclude_dnp:
        cmd.append("--exclude-dnp")
    cmd.append(file_path)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Position file export failed: {result.stderr.strip()}")
    return output_path


def export_manufacturing(
    file_path: str,
    output_dir: str,
    fmt: str = "jlcpcb",
    bom_path: str | None = None,
    refill_zones: bool = True,
    exclude_refs: list[str] | None = None,
) -> dict:
    """Export all manufacturing files for a fab house.

    Args:
        file_path: PCB file path.
        output_dir: Output directory.
        fmt: "jlcpcb", "pcbway", or "raw" (Gerbers + drill only).
        bom_path: Optional BOM CSV with LCSC part numbers.
        refill_zones: Refill copper zones (saving the board) before plotting,
            so pours in the Gerbers match the current layout.
        exclude_refs: Designators to leave out of the CPL and BOM (e.g. bare
            wire pads or parts you will hand-solder).

    The BOM only lists designators that are in the CPL (the position file
    leaves out through-hole parts), so the fab is never asked to place a
    part it has no coordinates for. Dropped designators are reported.

    Returns dict with file list, zip path, and bom_dropped / bom_missing_lcsc.
    """
    import zipfile

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if fmt not in ("jlcpcb", "pcbway", "raw"):
        raise ValueError(f"Unknown format {fmt!r}; use 'jlcpcb', 'pcbway' or 'raw'.")

    # 1. Export Gerbers + drill (from freshly filled zones)
    if refill_zones:
        fill_zones(file_path)
    gerber_files = export_gerbers(file_path, output_dir)

    # 2. Export position file
    pos_path = str(out / "positions.csv")
    export_position_file(file_path, pos_path, fmt="csv", units="mm")

    generated_files = [f for f in gerber_files if Path(f).name != "manufacturing.zip"]
    generated_files.append(pos_path)

    if fmt == "raw":
        return {"files": generated_files, "zip_path": None}

    # 3. Generate fab-specific files
    if fmt in ("jlcpcb", "pcbway"):
        # Generate CPL (Component Placement List) from position file
        cpl_path = str(out / f"cpl-{fmt}.csv")
        placed = _generate_cpl(pos_path, cpl_path, fmt, set(exclude_refs or ()))
        generated_files.append(cpl_path)

        # Generate BOM
        bom_out_path = str(out / f"bom-{fmt}.csv")
        bom_report = _generate_bom(bom_path, bom_out_path, fmt, placed)
        generated_files.append(bom_out_path)

    # 4. Zip this run's files (each once, never a previous zip)
    zip_path = out / "manufacturing.zip"
    to_zip = [f for f in dict.fromkeys(generated_files) if Path(f).resolve() != zip_path.resolve()]
    tmp_zip = out / ".manufacturing.zip.tmp"
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in to_zip:
            zf.write(fpath, Path(fpath).name)
    tmp_zip.replace(zip_path)

    result = {"files": to_zip + [str(zip_path)], "zip_path": str(zip_path)}
    if fmt in ("jlcpcb", "pcbway"):
        result.update(bom_report)
    return result


def _generate_cpl(
    pos_csv_path: str, output_path: str, fmt: str, exclude: set[str] | None = None,
) -> set[str]:
    """Generate a fab-specific CPL (Component Placement List) file.

    KiCad's position CSV has columns: Ref, Val, Package, PosX, PosY, Rot, Side
    Output columns: Designator, Mid X, Mid Y, Rotation, Layer

    Coordinates are passed through unchanged: kicad-cli already writes the
    position file in the Gerber frame (Y up, so boards drawn below the
    origin get negative Y), which is what the fab matches against.
    - Rotation: raw KiCad value, no correction (JLCPCB preview handles alignment)
    - Units: mm suffix on coordinates
    """
    import csv
    import io

    rows: list[dict] = []
    with open(pos_csv_path, "r") as f:
        # KiCad CSV uses ',' delimiter, may have comment header lines starting with #
        lines = [l for l in f if not l.startswith("#")]
        reader = csv.DictReader(io.StringIO("".join(lines)))
        for row in reader:
            # Normalize column names (KiCad may use different casing/spacing)
            ref = row.get("Ref", row.get("ref", row.get("Designator", "")))
            pos_x = row.get("PosX", row.get("posx", row.get("Mid X", "0")))
            pos_y = row.get("PosY", row.get("posy", row.get("Mid Y", "0")))
            rot = row.get("Rot", row.get("rot", row.get("Rotation", "0")))
            side = row.get("Side", row.get("side", row.get("Layer", "top")))

            try:
                y_val = float(pos_y.strip())
            except (ValueError, AttributeError):
                y_val = 0.0

            try:
                x_val = float(pos_x.strip())
            except (ValueError, AttributeError):
                x_val = 0.0

            layer = "Top" if "top" in side.lower() or "front" in side.lower() else "Bottom"

            if exclude and ref.strip() in exclude:
                continue
            rows.append({
                "Designator": ref.strip(),
                "Mid X": f"{x_val}mm",
                "Mid Y": f"{y_val}mm",
                "Rotation": rot.strip(),
                "Layer": layer,
            })

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Designator", "Mid X", "Mid Y", "Rotation", "Layer"])
        writer.writeheader()
        writer.writerows(rows)
    return {r["Designator"] for r in rows}


def _generate_bom(
    bom_input_path: str | None, output_path: str, fmt: str, placed: set[str] | None = None,
) -> dict:
    """Generate a fab-specific BOM file.

    If bom_input_path is provided, reads it and reformats.
    Otherwise creates a stub BOM that the user can fill in with LCSC numbers.
    """
    import csv

    if fmt == "jlcpcb":
        fieldnames = ["Comment", "Designator", "Footprint", "LCSC Part Number"]
    else:
        fieldnames = ["Comment", "Designator", "Footprint", "Manufacturer Part"]

    rows: list[dict] = []
    dropped: list[str] = []
    missing: list[str] = []

    if bom_input_path and Path(bom_input_path).exists():
        with open(bom_input_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                comment = row.get("Value", row.get("Comment", row.get("value", "")))
                designator = row.get("Reference", row.get("Designator", row.get("ref", "")))
                footprint = row.get("Footprint", row.get("Package", row.get("footprint", "")))
                lcsc = row.get("LCSC", row.get("LCSC Part Number", row.get("lcsc", "")))
                if placed is not None:
                    refs = [r.strip() for r in designator.split(",") if r.strip()]
                    kept = [r for r in refs if r in placed]
                    dropped.extend(r for r in refs if r not in placed)
                    if not kept:
                        continue
                    designator = ",".join(kept)
                if not lcsc.strip():
                    missing.append(designator)
                rows.append({
                    fieldnames[0]: comment,
                    fieldnames[1]: designator,
                    fieldnames[2]: footprint,
                    fieldnames[3]: lcsc,
                })

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return {"bom_dropped": sorted(set(dropped)), "bom_missing_lcsc": missing}


def export_netlist(
    schematic_path: str, output_path: str, fmt: str = "kicadsexpr"
) -> str:
    """Export netlist from schematic using kicad-cli.

    Args:
        fmt: Netlist format — "kicadsexpr" (default) or "kicadxml".
    """
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found.")

    cmd = [cli, "sch", "export", "netlist", "--format", fmt, "--output", output_path, schematic_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Netlist export failed: {result.stderr.strip()}")
    return output_path


def _get_freerouting_jar() -> str:
    """Find or download the Freerouting JAR. Returns path to the JAR."""
    import os
    import urllib.request

    # Check env var first
    jar_path = os.environ.get("FREEROUTING_JAR")
    if jar_path and Path(jar_path).exists():
        return jar_path

    # Check cache
    cache_dir = Path.home() / ".cache" / "kicad-mcp"
    cached_jar = cache_dir / "freerouting.jar"
    if cached_jar.exists():
        return str(cached_jar)

    # Download from GitHub releases
    release_url = "https://api.github.com/repos/freerouting/freerouting/releases/latest"
    try:
        req = urllib.request.Request(release_url, headers={"User-Agent": "kicad-mcp/0.1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            release = json.loads(resp.read().decode())

        jar_url = None
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if name.endswith(".jar") and "freerouting" in name.lower():
                jar_url = asset["browser_download_url"]
                break

        if not jar_url:
            raise RuntimeError(
                "Could not find Freerouting JAR in latest release. "
                "Download manually from https://github.com/freerouting/freerouting/releases "
                "and set FREEROUTING_JAR env var."
            )

        cache_dir.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(jar_url, headers={"User-Agent": "kicad-mcp/0.1"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            cached_jar.write_bytes(resp.read())

        return str(cached_jar)
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(
            f"Failed to download Freerouting: {e}. "
            "Download manually from https://github.com/freerouting/freerouting/releases "
            "and set FREEROUTING_JAR env var."
        ) from e


def export_dsn(file_path: str, output_path: str) -> str:
    """Export PCB to Specctra DSN format for autorouting.

    Uses pcbnew via subprocess (auto-detects the Python that has pcbnew).
    """
    _run_pcbnew(
        f"import pcbnew; "
        f"board = pcbnew.LoadBoard({file_path!r}); "
        f"pcbnew.ExportSpecctraDSN(board, {output_path!r}); "
        f"print('ok')"
    )
    return output_path


def fill_zones(file_path: str) -> dict:
    """Fill all copper zones with pcbnew's zone filler and save the board.

    Zone fills are stored in the board file; Gerber export and DRC use the
    stored fill, so boards edited outside the GUI must be refilled first.
    """
    out = _run_pcbnew(
        f"import pcbnew; "
        f"board = pcbnew.LoadBoard({file_path!r}); "
        f"zones = board.Zones(); "
        f"pcbnew.ZONE_FILLER(board).Fill(zones); "
        f"board.Save({file_path!r}); "
        f"print('zones', len(zones))",
        timeout=300,
    )
    count = next((int(line.split()[1]) for line in out.splitlines() if line.startswith("zones ")), 0)
    return {"filled_zones": count}


def set_copper_layers(file_path: str, count: int, power_layers: list[str] | None = None) -> dict:
    """Set the board's copper layer count and mark inner layers as power planes.

    Uses pcbnew so KiCad writes the layer table and stackup itself. Layers in
    power_layers (e.g. ["In1.Cu"]) get type "power": the Specctra export marks
    them as planes, so Freerouting keeps signals off them but lets vias pass.
    """
    if count < 2 or count % 2:
        raise ValueError("count must be an even number >= 2")
    power_layers = power_layers or []
    out = _run_pcbnew(
        "import pcbnew\n"
        f"board = pcbnew.LoadBoard({file_path!r})\n"
        f"board.SetCopperLayerCount({count})\n"
        f"for name in {power_layers!r}:\n"
        "    lid = board.GetLayerID(name)\n"
        "    if lid < 0 or not board.IsLayerEnabled(lid):\n"
        "        raise SystemExit('unknown or disabled layer ' + name)\n"
        "    board.SetLayerType(lid, pcbnew.LT_POWER)\n"
        f"board.Save({file_path!r})\n"
        "print('layers', board.GetCopperLayerCount())\n",
    )
    layers = next((int(line.split()[1]) for line in out.splitlines() if line.startswith("layers ")), 0)
    return {"copper_layers": layers, "power_layers": power_layers}


def import_ses(pcb_path: str, ses_path: str) -> str:
    """Import Specctra SES (routed session) back into PCB.

    Uses pcbnew via subprocess (auto-detects the Python that has pcbnew).
    """
    _run_pcbnew(
        f"import pcbnew; "
        f"board = pcbnew.LoadBoard({pcb_path!r}); "
        f"pcbnew.ImportSpecctraSES(board, {ses_path!r}); "
        f"board.Save({pcb_path!r}); "
        f"print('ok')"
    )
    return pcb_path


def run_freerouting(
    dsn_path: str,
    output_ses_path: str,
    freerouting_jar: str | None = None,
    timeout: int = 300,
    max_passes: int = 20,
    ignore_net_classes: list[str] | None = None,
) -> str:
    """Run the Freerouting autorouter on a DSN file.

    ignore_net_classes: net classes to leave unrouted, e.g. a GND class that
    copper pours and stitching vias will connect.
    """
    java = shutil.which("java")
    if not java:
        raise RuntimeError(
            "Java not found. Freerouting requires a Java runtime. "
            "Install Java (e.g., 'sudo dnf install java-latest-openjdk' or "
            "'sudo apt install default-jre')."
        )

    jar_path = freerouting_jar or _get_freerouting_jar()

    cmd = [
        java, "-jar", jar_path,
        "-de", dsn_path,
        "-do", output_ses_path,
        "-mp", str(max_passes),
        # 2.x ignores -mp and opens a window unless these are given
        f"--router.max_passes={max_passes}",
        "--gui.enabled=false",
    ]
    if ignore_net_classes:
        cmd += ["-inc", ",".join(ignore_net_classes)]

    env = {**os.environ, "DISPLAY": "", "WAYLAND_DISPLAY": ""}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip()
        if "UnsupportedClassVersionError" in output:
            raise RuntimeError(
                f"{jar_path} needs a newer Java than the one installed. Install a newer "
                "JDK or use an older Freerouting release (2.1.0 runs on Java 21) via "
                "freerouting_jar or the FREEROUTING_JAR env var."
            )
        raise RuntimeError(f"Freerouting failed: {output}")

    if not Path(output_ses_path).exists():
        raise RuntimeError("Freerouting completed but no SES output was generated.")

    return output_ses_path
