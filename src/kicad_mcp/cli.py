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


def run_erc(file_path: str) -> list[ERCViolation]:
    """Run ERC on a schematic using kicad-cli. Returns list of violations."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found. Install KiCad 8 to use ERC.")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        report_path = f.name

    try:
        result = subprocess.run(
            [cli, "sch", "erc", "--output", report_path, "--format", "json", file_path],
            capture_output=True,
            text=True,
            timeout=60,
        )

        violations: list[ERCViolation] = []
        report_file = Path(report_path)
        if report_file.exists():
            try:
                report = json.loads(report_file.read_text())
                for v in report.get("violations", []):
                    loc = None
                    if "pos" in v:
                        loc = Point(v["pos"].get("x", 0), v["pos"].get("y", 0))
                    violations.append(ERCViolation(
                        severity=v.get("severity", "error"),
                        message=v.get("description", str(v)),
                        location=loc,
                    ))
            except (json.JSONDecodeError, KeyError):
                if result.returncode != 0:
                    violations.append(ERCViolation(
                        severity="error",
                        message=f"ERC failed: {result.stderr.strip() or result.stdout.strip()}",
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
            [cli, "pcb", "drc", "--output", report_path, "--format", "json", file_path],
            capture_output=True,
            text=True,
            timeout=60,
        )

        violations: list[DRCViolation] = []
        report_file = Path(report_path)
        if report_file.exists():
            try:
                report = json.loads(report_file.read_text())
                for v in report.get("violations", []):
                    loc = None
                    if "pos" in v:
                        loc = Point(v["pos"].get("x", 0), v["pos"].get("y", 0))
                    violations.append(DRCViolation(
                        severity=v.get("severity", "error"),
                        message=v.get("description", str(v)),
                        location=loc,
                    ))
            except (json.JSONDecodeError, KeyError):
                if result.returncode != 0:
                    violations.append(DRCViolation(
                        severity="error",
                        message=f"DRC failed: {result.stderr.strip() or result.stdout.strip()}",
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


def export_netlist(schematic_path: str, output_path: str) -> str:
    """Export netlist from schematic using kicad-cli."""
    cli = _find_kicad_cli()
    if not cli:
        raise RuntimeError("kicad-cli not found.")

    cmd = [cli, "sch", "export", "netlist", "--output", output_path, schematic_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Netlist export failed: {result.stderr.strip()}")
    return output_path
