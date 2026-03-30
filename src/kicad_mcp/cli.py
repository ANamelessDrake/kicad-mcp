"""Wrapper for kicad-cli commands (ERC, DRC, exports)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
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


def export_dsn(file_path: str, output_path: str) -> str:
    """Export PCB to Specctra DSN format for autorouting."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found.")
    cmd = [cli, "pcb", "export", "dsn", "--output", output_path, file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"DSN export failed: {result.stderr.strip()}")
    return output_path


def import_ses(pcb_path: str, ses_path: str) -> str:
    """Import Specctra SES (routed session) back into PCB."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found.")
    cmd = [cli, "pcb", "import", "specctra_ses", "--output", pcb_path, ses_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"SES import failed: {result.stderr.strip()}")
    return pcb_path


def run_freerouting(
    dsn_path: str,
    output_ses_path: str,
    freerouting_jar: str | None = None,
    timeout: int = 300,
) -> str:
    """Run the Freerouting autorouter on a DSN file."""
    import os

    jar_path = freerouting_jar or os.environ.get("FREEROUTING_JAR", "freerouting.jar")

    java = shutil.which("java")
    if not java:
        raise RuntimeError("Java not found. Freerouting requires a Java runtime.")

    if not Path(jar_path).exists():
        raise RuntimeError(
            f"Freerouting JAR not found at {jar_path}. "
            "Download from https://github.com/freerouting/freerouting/releases "
            "and set FREEROUTING_JAR env var."
        )

    cmd = [
        java, "-jar", jar_path,
        "-de", dsn_path,
        "-do", output_ses_path,
        "-mp", "20",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Freerouting failed: {result.stderr.strip()}")

    if not Path(output_ses_path).exists():
        raise RuntimeError("Freerouting completed but no SES output was generated.")

    return output_ses_path
