"""KiCad library lookup: locate, index, and load symbol and footprint libraries."""

from __future__ import annotations

import copy
import os
import re
from functools import lru_cache
from pathlib import Path

from .sexp_parser import QuotedString, SexpList, _unescape, parse_file

_SYMBOL_ENV_VARS = ("KICAD_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR")
_FOOTPRINT_ENV_VARS = ("KICAD_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR")
_DEFAULT_SYMBOL_DIRS = ("/usr/share/kicad/symbols", "/usr/local/share/kicad/symbols")
_DEFAULT_FOOTPRINT_DIRS = ("/usr/share/kicad/footprints", "/usr/local/share/kicad/footprints")

_MAX_RESULTS = 100


def _resolve_dir(env_vars: tuple[str, ...], defaults: tuple[str, ...]) -> Path:
    for var in env_vars:
        value = os.environ.get(var)
        if value:
            return Path(value)
    for d in defaults:
        if Path(d).exists():
            return Path(d)
    return Path(defaults[0])


def symbol_dir() -> Path:
    """Directory holding KiCad's .kicad_sym symbol libraries."""
    return _resolve_dir(_SYMBOL_ENV_VARS, _DEFAULT_SYMBOL_DIRS)


def footprint_dir() -> Path:
    """Directory holding KiCad's .pretty footprint libraries."""
    return _resolve_dir(_FOOTPRINT_ENV_VARS, _DEFAULT_FOOTPRINT_DIRS)


# ── Search ──────────────────────────────────────────────────────────────────

_SYMBOL_NAME_RE = re.compile(r'\(symbol\s+"((?:[^"\\]|\\.)*)"')
_UNIT_SUFFIX_RE = re.compile(r"^(.*)_\d+_\d+$")


@lru_cache(maxsize=1024)
def _symbol_names_in_file(path: str, _mtime_ns: int) -> tuple[str, ...]:
    """Top-level symbol names in a .kicad_sym file.

    Scans the text instead of parsing it: parsing every library on each
    search takes tens of seconds. Unit sub-symbols ("NAME_1_1") are dropped
    when their parent NAME is in the same file.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    names = [_unescape(m.group(1)) for m in _SYMBOL_NAME_RE.finditer(text)]
    name_set = set(names)
    result: list[str] = []
    for name in names:
        m = _UNIT_SUFFIX_RE.match(name)
        if m and m.group(1) in name_set:
            continue
        result.append(name)
    return tuple(result)


def list_library_symbols(query: str) -> list[str]:
    """Search KiCad's symbol libraries for matching components.

    Returns a list of library IDs like "Device:R", "Device:C", etc.
    """
    sdir = symbol_dir()
    if not sdir.exists():
        return []

    query_lower = query.lower()
    results: list[str] = []

    for lib_file in sorted(sdir.glob("*.kicad_sym")):
        lib_name = lib_file.stem
        try:
            names = _symbol_names_in_file(str(lib_file), lib_file.stat().st_mtime_ns)
        except OSError:
            continue
        for sym_name in names:
            full_id = f"{lib_name}:{sym_name}"
            if query_lower in full_id.lower():
                results.append(full_id)
                if len(results) >= _MAX_RESULTS:
                    return results

    return results


def list_library_footprints(query: str) -> list[str]:
    """Search KiCad's footprint libraries for matching footprints.

    Returns a list of library IDs like "Resistor_SMD:R_0603_1608Metric".
    """
    fdir = footprint_dir()
    if not fdir.exists():
        return []

    query_lower = query.lower()
    results: list[str] = []

    for lib_dir in sorted(fdir.glob("*.pretty")):
        lib_name = lib_dir.stem
        for fp_file in sorted(lib_dir.glob("*.kicad_mod")):
            full_id = f"{lib_name}:{fp_file.stem}"
            if query_lower in full_id.lower():
                results.append(full_id)
                if len(results) >= _MAX_RESULTS:
                    return results

    return results


# ── Loading ─────────────────────────────────────────────────────────────────


@lru_cache(maxsize=32)
def _parse_cached(path: str, _mtime_ns: int) -> SexpList:
    return parse_file(path)


def _load_cached(path: Path) -> SexpList | None:
    """Parse a library file, cached by path and modification time.

    The returned tree is shared with the cache: callers must not mutate it.
    """
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    return _parse_cached(str(path), mtime)


def load_symbol(lib_name: str, symbol_name: str) -> SexpList | None:
    """Return a self-contained copy of a library symbol, or None if not found.

    Symbols defined with (extends "Parent") get the parent's properties they
    don't override and the parent's unit sub-symbols (renamed), matching how
    KiCad flattens derived symbols into a schematic's lib_symbols.
    """
    lib_root = _load_cached(symbol_dir() / f"{lib_name}.kicad_sym")
    if lib_root is None:
        return None

    by_name = {
        str(child.children[1]): child
        for child in lib_root.children
        if isinstance(child, SexpList) and child.tag == "symbol" and len(child.children) >= 2
    }
    source = by_name.get(symbol_name)
    if source is None:
        return None
    target = copy.deepcopy(source)

    extends_node = target.find("extends")
    if extends_node is not None and len(extends_node.children) >= 2:
        parent = by_name.get(str(extends_node.children[1]))
        target.remove_child(extends_node)
        if parent is not None:
            parent_name = str(parent.children[1])
            own_props = {
                str(p.children[1]) for p in target.find_all("property") if len(p.children) >= 2
            }
            insert_at = max(
                (i + 1 for i, c in enumerate(target.children)
                 if isinstance(c, SexpList) and c.tag == "property"),
                default=len(target.children),
            )
            for child in parent.children[2:]:
                if not isinstance(child, SexpList):
                    continue
                if child.tag == "property":
                    if len(child.children) >= 2 and str(child.children[1]) not in own_props:
                        target.children.insert(insert_at, copy.deepcopy(child))
                        insert_at += 1
                elif child.tag == "symbol" and len(child.children) >= 2:
                    sub = copy.deepcopy(child)
                    sub_name = str(sub.children[1])
                    if sub_name.startswith(parent_name + "_"):
                        sub_name = symbol_name + sub_name[len(parent_name):]
                    sub.children[1] = QuotedString(sub_name)
                    target.children.append(sub)
                elif target.find(child.tag or "") is None:
                    # Flags like (pin_names ...), (in_bom ...) the child didn't set
                    target.children.insert(insert_at, copy.deepcopy(child))
                    insert_at += 1
    return target


_ENV_REF_RE = re.compile(r"\$\{([^}]+)\}")


def project_footprint_lib_dir(lib_name: str, project_dir: Path) -> Path | None:
    """Resolve a library nickname through the project's fp-lib-table.

    ${KIPRJMOD} expands to the project directory; other ${VAR} references
    expand from the environment. Returns None if the table has no such entry.
    """
    table = _load_cached(project_dir / "fp-lib-table")
    if table is None:
        return None
    for lib in table.find_all("lib"):
        name, uri = lib.find("name"), lib.find("uri")
        if not name or not uri or str(name.children[1]) != lib_name:
            continue
        env = {"KIPRJMOD": str(project_dir)}

        def expand(m: re.Match) -> str:
            return env.get(m.group(1)) or os.environ.get(m.group(1)) or m.group(0)

        path = Path(_ENV_REF_RE.sub(expand, str(uri.children[1])))
        return path if path.is_absolute() else project_dir / path
    return None


def load_footprint(lib_name: str, fp_name: str, project_dir: Path | None = None) -> SexpList | None:
    """Return a copy of a .kicad_mod footprint, or None if not found.

    With project_dir, the project's fp-lib-table is searched before the
    global footprint directory, as KiCad does.
    """
    lib_dirs = []
    if project_dir is not None:
        project_lib = project_footprint_lib_dir(lib_name, project_dir)
        if project_lib is not None:
            lib_dirs.append(project_lib)
    lib_dirs.append(footprint_dir() / f"{lib_name}.pretty")
    for lib_dir in lib_dirs:
        root = _load_cached(lib_dir / f"{fp_name}.kicad_mod")
        if root is not None:
            return copy.deepcopy(root)
    return None
