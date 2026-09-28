"""Lossless S-expression parser and writer for KiCad files.

KiCad uses a Lisp-like S-expression format. This parser preserves the
structure so that read -> modify -> write round-trips without corrupting
unmodified sections, and the writer reproduces KiCad 8's own layout so that
a file saved by KiCad comes back byte-for-byte identical when untouched.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Union


# A parsed S-expression node is either a SexpList (parenthesized group)
# or an atom (string/number token).


class QuotedString(str):
    """A string that was originally quoted in the S-expression source.

    Preserves quoting information so the formatter can reproduce
    the original quoting style on round-trip.
    """


class RawFloat(float):
    """A float parsed from a file, remembering its exact source spelling.

    KiCad writes some values with fixed precision (e.g. "12.000000");
    keeping the token text lets unmodified values round-trip unchanged.
    Any newly computed value is a plain float and is formatted normally.
    """

    text: str


Atom = Union[str, int, float]
SexpNode = Union["SexpList", Atom]


@dataclass
class SexpList:
    """A parenthesized S-expression list, e.g. (symbol (lib_id "Device:R") ...)."""

    children: list[SexpNode] = field(default_factory=list)

    @property
    def tag(self) -> str | None:
        """Return the first atom (the 'tag') if it exists."""
        if self.children and isinstance(self.children[0], (str, int, float)):
            return str(self.children[0])
        return None

    def find(self, tag: str) -> SexpList | None:
        """Find the first direct child list with the given tag."""
        for child in self.children:
            if isinstance(child, SexpList) and child.tag == tag:
                return child
        return None

    def find_all(self, tag: str) -> list[SexpList]:
        """Find all direct child lists with the given tag."""
        return [
            child
            for child in self.children
            if isinstance(child, SexpList) and child.tag == tag
        ]

    def find_value(self, tag: str) -> SexpNode | None:
        """Find a child list with the given tag and return its second element (the value)."""
        node = self.find(tag)
        if node and len(node.children) >= 2:
            return node.children[1]
        return None

    def find_deep(self, tag: str) -> SexpList | None:
        """Recursively find the first descendant list with the given tag."""
        for child in self.children:
            if isinstance(child, SexpList):
                if child.tag == tag:
                    return child
                result = child.find_deep(tag)
                if result is not None:
                    return result
        return None

    def remove_child(self, child: SexpNode) -> bool:
        """Remove a child node (by identity). Returns True if found and removed."""
        for i, c in enumerate(self.children):
            if c is child:
                del self.children[i]
                return True
        return False

    def append(self, child: SexpNode) -> None:
        """Append a child node."""
        self.children.append(child)

    def __repr__(self) -> str:
        return f"SexpList({self.children!r})"


class SexpParseError(Exception):
    """Raised when S-expression parsing fails."""


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_UNESCAPES = {"n": "\n", "r": "\r", "t": "\t"}


def escape_sexp_string(s: str) -> str:
    """Escape a string for safe embedding in an S-expression f-string.

    Use this when building S-expression text via f-strings with user-supplied
    values. It escapes backslashes, double quotes and line breaks so that the
    value is safe inside a quoted S-expression token.

    Example::

        sexp_text = f'(name "{escape_sexp_string(user_input)}")'
    """
    return "".join(_ESCAPES.get(c, c) for c in s)


def _unescape(s: str) -> str:
    """Reverse escape_sexp_string in a single pass."""
    if "\\" not in s:
        return s
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append(_UNESCAPES.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _tokenize(text: str) -> list[str]:
    """Tokenize S-expression text into a list of tokens.

    Tokens are: '(', ')', quoted strings, or bare words/numbers.
    """
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\n\r":
            i += 1
        elif c == "(":
            tokens.append("(")
            i += 1
        elif c == ")":
            tokens.append(")")
            i += 1
        elif c == '"':
            # Quoted string: find matching close quote, handling escapes
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2  # skip escaped character
                elif text[j] == '"':
                    break
                else:
                    j += 1
            if j >= n:
                raise SexpParseError(f"Unterminated string starting at offset {i}")
            tokens.append(text[i : j + 1])
            i = j + 1
        else:
            # Bare token (symbol, number, etc.)
            j = i
            while j < n and text[j] not in " \t\n\r()\"":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


# Only plain decimal numbers are numeric. This keeps tokens like "inf", "nan"
# or "1_000" (which Python's int()/float() would accept) as bare symbols.
_INT_RE = re.compile(r"[+-]?\d+")
_FLOAT_RE = re.compile(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")


def _parse_atom(token: str) -> Atom:
    """Convert a token string to an atom (int, float, or str)."""
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        return QuotedString(_unescape(token[1:-1]))
    if _INT_RE.fullmatch(token):
        return int(token)
    if _FLOAT_RE.fullmatch(token):
        value = RawFloat(token)
        value.text = token
        return value
    return token


def parse(text: str) -> SexpList:
    """Parse an S-expression string into a SexpList tree.

    The input should be a complete KiCad file (a single top-level list).
    Returns the root SexpList.
    """
    tokens = _tokenize(text)
    if not tokens:
        raise SexpParseError("Empty input")
    if tokens[0] != "(":
        raise SexpParseError("Expected '(' at start of input")

    # Iterative parse: deep KiCad files would otherwise approach the
    # recursion limit.
    root: SexpList | None = None
    stack: list[SexpList] = []
    for pos, tok in enumerate(tokens):
        if tok == "(":
            node = SexpList()
            if stack:
                stack[-1].children.append(node)
            elif root is not None:
                raise SexpParseError(f"Unexpected data after top-level list at token {pos}")
            else:
                root = node
            stack.append(node)
        elif tok == ")":
            if not stack:
                raise SexpParseError(f"Unbalanced ')' at token {pos}")
            stack.pop()
        else:
            if not stack:
                raise SexpParseError(f"Unexpected atom {tok!r} outside of a list")
            stack[-1].children.append(_parse_atom(tok))
    if stack:
        raise SexpParseError("Unexpected end of input: missing ')'")
    assert root is not None
    return root


def _is_numeric(s: str) -> bool:
    """Check if a string would be parsed as a number."""
    return bool(_FLOAT_RE.fullmatch(s))


def format_float(value: float) -> str:
    """Format a float the way KiCad does: plain decimal, no exponent.

    Values are rounded to 10 decimal places, which is far below KiCad's
    internal resolution (1 nm on boards, 100 nm in schematics), so parsed
    values survive exactly while arithmetic noise such as
    3.8100000000000005 is dropped.
    """
    value = round(value, 10)
    if value == int(value):
        return str(int(value))
    s = repr(value)
    if "e" in s or "E" in s:
        s = f"{value:.10f}".rstrip("0").rstrip(".")
    return s


def _format_atom(atom: Atom) -> str:
    """Format an atom for output."""
    if isinstance(atom, QuotedString):
        # Always re-quote strings that were originally quoted
        return f'"{escape_sexp_string(str(atom))}"'
    elif isinstance(atom, bool):
        return "yes" if atom else "no"
    elif isinstance(atom, str):
        # Quote strings that look numeric: KiCad requires pin numbers etc.
        # to remain quoted strings, not bare integers
        if _is_numeric(atom):
            return f'"{atom}"'
        # Quote if empty or contains special characters.
        # Colons are included because library refs like "Device:R" must be quoted;
        # KiCad tags (kicad_sch, symbol, wire, etc.) never contain colons.
        if not atom or any(c in atom for c in ' "():\n\r\t\\'):
            return f'"{escape_sexp_string(atom)}"'
        return atom
    elif isinstance(atom, RawFloat):
        return atom.text
    elif isinstance(atom, float):
        return format_float(atom)
    return str(atom)


# Layout constants from KiCad 8's KICAD_FORMAT::Prettify.
_XY_COLUMN_LIMIT = 99
_TOKEN_WRAP_LENGTH = 72


def format_sexp(node: SexpNode) -> str:
    """Format a SexpNode tree back into S-expression text.

    Mirrors KiCad 8's pretty-printer: every list starts on its own line,
    indented with tabs; consecutive (xy ...) lists share a line up to
    column 99; long runs of atoms (image data, group members) wrap once
    72 characters have been written since the last paren; a closing paren goes on its own line only when the
    list ended with a nested list or was wrapped.
    """
    if not isinstance(node, SexpList):
        return _format_atom(node)

    out: list[str] = []
    # Mutable formatter state: current column, last non-space char written,
    # and whether the most recently opened list was an (xy ...).
    # "run" counts atom characters written since the last paren or wrap.
    state = {"col": 0, "last": "", "xy": False, "run": 0}

    def write(s: str) -> None:
        out.append(s)
        nl = s.rfind("\n")
        state["col"] = len(s) - nl - 1 if nl >= 0 else state["col"] + len(s)
        stripped = s.rstrip()
        if stripped:
            state["last"] = stripped[-1]

    def emit(lst: SexpList, depth: int) -> None:
        is_xy = lst.tag == "xy"
        if not out:
            write("(")
        elif is_xy and state["xy"] and state["col"] < _XY_COLUMN_LIMIT:
            write(" (")
        else:
            write("\n" + "\t" * depth + "(")
        state["xy"] = is_xy
        state["run"] = 0

        wrapped = False
        for i, child in enumerate(lst.children):
            if isinstance(child, SexpList):
                emit(child, depth + 1)
                continue
            text = _format_atom(child)
            if i == 0:
                write(text)
                state["run"] += len(text)
            elif state["run"] >= _TOKEN_WRAP_LENGTH:
                write("\n" + "\t" * (depth + 1) + text)
                state["run"] = len(text)
                wrapped = True
            else:
                write(" " + text)
                state["run"] += len(text) + 1

        state["run"] = 0
        if state["last"] == ")" or wrapped:
            write("\n" + "\t" * depth + ")")
        else:
            write(")")

    emit(node, 0)
    return "".join(out)


def parse_file(file_path: str) -> SexpList:
    """Parse a KiCad file and return the S-expression tree."""
    with open(file_path, "r", encoding="utf-8") as f:
        return parse(f.read())


def write_file(file_path: str, root: SexpList) -> None:
    """Write an S-expression tree to a KiCad file.

    Writes to a temporary file in the same directory and atomically renames
    it over the target, so an interrupted write never leaves a truncated
    board or schematic behind.
    """
    text = format_sexp(root) + "\n"
    target = os.path.abspath(file_path)
    directory = os.path.dirname(target)
    fd, tmp_path = tempfile.mkstemp(prefix=".kicad_mcp_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if os.path.exists(target):
            os.chmod(tmp_path, os.stat(target).st_mode & 0o7777)
        else:
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp_path, 0o666 & ~umask)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
