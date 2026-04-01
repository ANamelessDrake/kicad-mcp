"""Wrapper for kicad-cli commands (ERC, DRC, exports)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .types import DRCViolation, ERCViolation, Point


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

    Path(output_dir).mkdir(parents=True, exist_ok=True)

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

    return sorted(str(p) for p in Path(output_dir).iterdir() if p.is_file())


def export_position_file(
    file_path: str, output_path: str, fmt: str = "csv", units: str = "mm",
    smd_only: bool = True,
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
) -> dict:
    """Export all manufacturing files for a fab house.

    Args:
        file_path: PCB file path.
        output_dir: Output directory.
        fmt: "jlcpcb", "pcbway", or "raw" (Gerbers + drill only).
        bom_path: Optional BOM CSV with LCSC part numbers.

    Returns dict with file list and zip path.
    """
    import zipfile

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Export Gerbers + drill
    gerber_files = export_gerbers(file_path, output_dir)

    # 2. Export position file
    pos_path = str(out / "positions.csv")
    export_position_file(file_path, pos_path, fmt="csv", units="mm")

    generated_files = list(gerber_files) + [pos_path]

    if fmt == "raw":
        return {"files": generated_files, "zip_path": None}

    # 3. Generate fab-specific files
    if fmt in ("jlcpcb", "pcbway"):
        # Generate CPL (Component Placement List) from position file
        cpl_path = str(out / f"cpl-{fmt}.csv")
        _generate_cpl(pos_path, cpl_path, fmt)
        generated_files.append(cpl_path)

        # Generate BOM
        bom_out_path = str(out / f"bom-{fmt}.csv")
        _generate_bom(bom_path, bom_out_path, fmt)
        generated_files.append(bom_out_path)

    # 4. Zip everything
    zip_path = str(out / "manufacturing.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in generated_files:
            zf.write(fpath, Path(fpath).name)
    generated_files.append(zip_path)

    return {"files": generated_files, "zip_path": zip_path}


def _generate_cpl(
    pos_csv_path: str, output_path: str, fmt: str
) -> None:
    """Generate a fab-specific CPL (Component Placement List) file.

    KiCad's position CSV has columns:
    Ref, Val, Package, PosX, PosY, Rot, Side

    JLCPCB wants: Designator, Mid X, Mid Y, Rotation, Layer
    PCBWay wants: Designator, Mid X, Mid Y, Rotation, Layer
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

            # JLCPCB/PCBWay: negate Y to match Gerber coordinates
            try:
                y_val = -float(pos_y.strip())
            except (ValueError, AttributeError):
                y_val = 0.0

            try:
                x_val = float(pos_x.strip())
            except (ValueError, AttributeError):
                x_val = 0.0

            layer = "Top" if "top" in side.lower() or "front" in side.lower() else "Bottom"

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


def _generate_bom(
    bom_input_path: str | None, output_path: str, fmt: str
) -> None:
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

    if bom_input_path and Path(bom_input_path).exists():
        with open(bom_input_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                comment = row.get("Value", row.get("Comment", row.get("value", "")))
                designator = row.get("Reference", row.get("Designator", row.get("ref", "")))
                footprint = row.get("Footprint", row.get("Package", row.get("footprint", "")))
                lcsc = row.get("LCSC", row.get("LCSC Part Number", row.get("lcsc", "")))
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

    Note: kicad-cli does not support DSN export. This requires the pcbnew
    Python module. Raises RuntimeError if pcbnew is not available.
    """
    try:
        import pcbnew
    except ImportError:
        raise RuntimeError(
            "DSN export requires the pcbnew Python module (part of KiCad). "
            "It is not available in this Python environment. "
            "Install KiCad or use the simple autorouter fallback."
        )
    board = pcbnew.LoadBoard(file_path)
    pcbnew.ExportSpecctraDSN(board, output_path)
    return output_path


def import_ses(pcb_path: str, ses_path: str) -> str:
    """Import Specctra SES (routed session) back into PCB.

    Note: Requires the pcbnew Python module.
    """
    try:
        import pcbnew
    except ImportError:
        raise RuntimeError("SES import requires the pcbnew Python module.")
    board = pcbnew.LoadBoard(pcb_path)
    pcbnew.ImportSpecctraSES(board, ses_path)
    board.Save(pcb_path)
    return pcb_path


def run_freerouting(
    dsn_path: str,
    output_ses_path: str,
    freerouting_jar: str | None = None,
    timeout: int = 300,
) -> str:
    """Run the Freerouting autorouter on a DSN file."""
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
        "-mp", "20",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Freerouting failed: {result.stderr.strip() or result.stdout.strip()}")

    if not Path(output_ses_path).exists():
        raise RuntimeError("Freerouting completed but no SES output was generated.")

    return output_ses_path
