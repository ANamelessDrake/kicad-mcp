"""Tests for library search and loading."""

import pytest

from kicad_mcp import library
from tests.conftest import needs_symbol_libs


@pytest.fixture
def fake_symbol_dir(tmp_path, monkeypatch):
    (tmp_path / "Mine.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20231120)\n'
        '\t(symbol "Amp" (symbol "Amp_0_1") (symbol "Amp_1_1"))\n'
        '\t(symbol "Odd_1_2" (symbol "Odd_1_2_1_1"))\n'
        '\t(symbol "Say \\"hi\\"")\n'
        ')\n'
    )
    monkeypatch.setenv("KICAD_SYMBOL_DIR", str(tmp_path))
    return tmp_path


def test_index_skips_unit_subsymbols_only(fake_symbol_dir):
    assert library.list_library_symbols("") == ["Mine:Amp", "Mine:Odd_1_2", 'Mine:Say "hi"']


def test_query_is_case_insensitive(fake_symbol_dir):
    assert library.list_library_symbols("amp") == ["Mine:Amp"]


def test_versioned_env_var_is_honored(tmp_path, monkeypatch):
    monkeypatch.delenv("KICAD_SYMBOL_DIR", raising=False)
    monkeypatch.delenv("KICAD9_SYMBOL_DIR", raising=False)
    monkeypatch.setenv("KICAD8_SYMBOL_DIR", str(tmp_path))
    assert library.symbol_dir() == tmp_path


@needs_symbol_libs
def test_derived_symbol_is_flattened():
    sym = library.load_symbol("Amplifier_Operational", "LM358")
    assert sym is not None
    assert sym.find("extends") is None
    names = {str(s.children[1]) for s in sym.find_all("symbol")}
    assert {"LM358_1_1", "LM358_2_1", "LM358_3_1"} <= names
    value = next(p for p in sym.find_all("property") if str(p.children[1]) == "Value")
    assert str(value.children[2]) == "LM358"


@needs_symbol_libs
def test_loaded_symbol_is_a_copy():
    a = library.load_symbol("Device", "R")
    a.children.clear()
    b = library.load_symbol("Device", "R")
    assert b.children
