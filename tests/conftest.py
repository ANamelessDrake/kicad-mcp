"""Shared test fixtures and skip markers."""

import shutil

import pytest

from kicad_mcp import library

needs_symbol_libs = pytest.mark.skipif(
    not (library.symbol_dir() / "Device.kicad_sym").exists(),
    reason="KiCad symbol libraries not installed",
)
needs_footprint_libs = pytest.mark.skipif(
    not (library.footprint_dir() / "Resistor_SMD.pretty").exists(),
    reason="KiCad footprint libraries not installed",
)
needs_kicad_cli = pytest.mark.skipif(
    shutil.which("kicad-cli") is None, reason="kicad-cli not installed"
)
