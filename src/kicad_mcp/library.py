"""KiCad library lookup — search symbol and footprint libraries."""

from __future__ import annotations

import os
from pathlib import Path

from .sexp_parser import SexpList, parse_file


def list_library_symbols(query: str) -> list[str]:
    """Search KiCad's symbol libraries for matching components.

    Returns a list of library IDs like "Device:R", "Device:C", etc.
    """
    symbol_dir = Path(os.environ.get("KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols"))
    if not symbol_dir.exists():
        return []

    query_lower = query.lower()
    results: list[str] = []

    for lib_file in sorted(symbol_dir.glob("*.kicad_sym")):
        lib_name = lib_file.stem
        try:
            root = parse_file(str(lib_file))
            for child in root.children:
                if isinstance(child, SexpList) and child.tag == "symbol":
                    if len(child.children) >= 2:
                        sym_name = str(child.children[1])
                        # Skip sub-symbols (contain _0_, _1_, etc.)
                        if "_0_" in sym_name or "_1_" in sym_name:
                            continue
                        full_id = f"{lib_name}:{sym_name}"
                        if query_lower in full_id.lower():
                            results.append(full_id)
        except Exception:
            continue

    return results[:100]  # Limit results


def list_library_footprints(query: str) -> list[str]:
    """Search KiCad's footprint libraries for matching footprints.

    Returns a list of library IDs like "Resistor_SMD:R_0603_1608Metric".
    """
    footprint_dir = Path(os.environ.get("KICAD_FOOTPRINT_DIR", "/usr/share/kicad/footprints"))
    if not footprint_dir.exists():
        return []

    query_lower = query.lower()
    results: list[str] = []

    for lib_dir in sorted(footprint_dir.glob("*.pretty")):
        lib_name = lib_dir.stem
        for fp_file in sorted(lib_dir.glob("*.kicad_mod")):
            fp_name = fp_file.stem
            full_id = f"{lib_name}:{fp_name}"
            if query_lower in full_id.lower():
                results.append(full_id)

    return results[:100]
