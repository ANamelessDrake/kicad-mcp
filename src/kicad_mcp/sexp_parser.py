"""Lossless S-expression parser and writer for KiCad files.

KiCad uses a Lisp-like S-expression format. This parser preserves the
structure exactly so that read -> modify -> write round-trips without
corrupting unmodified sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union


# A parsed S-expression node is either a SexpList (parenthesized group)
# or an atom (string/number token).
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

    def find_value(self, tag: str) -> Atom | None:
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
        """Remove a child node. Returns True if found and removed."""
        try:
            self.children.remove(child)
            return True
        except ValueError:
            return False

    def append(self, child: SexpNode) -> None:
        """Append a child node."""
        self.children.append(child)

    def __repr__(self) -> str:
        return f"SexpList({self.children!r})"


class SexpParseError(Exception):
    """Raised when S-expression parsing fails."""


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
            # Quoted string — find matching close quote, handling escaped quotes
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2  # skip escaped character
                elif text[j] == '"':
                    break
                else:
                    j += 1
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


def _parse_atom(token: str) -> Atom:
    """Convert a token string to an atom (int, float, or str)."""
    if token.startswith('"') and token.endswith('"'):
        # Quoted string — unescape
        inner = token[1:-1]
        inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner

    # Try integer
    try:
        return int(token)
    except ValueError:
        pass

    # Try float
    try:
        return float(token)
    except ValueError:
        pass

    # Bare symbol
    return token


def parse(text: str) -> SexpList:
    """Parse an S-expression string into a SexpList tree.

    The input should be a complete KiCad file (a single top-level list).
    Returns the root SexpList.
    """
    tokens = _tokenize(text)
    if not tokens:
        raise SexpParseError("Empty input")

    pos = 0

    def _parse_list() -> SexpList:
        nonlocal pos
        if pos >= len(tokens) or tokens[pos] != "(":
            raise SexpParseError(f"Expected '(' at position {pos}")
        pos += 1  # consume '('
        node = SexpList()
        while pos < len(tokens) and tokens[pos] != ")":
            if tokens[pos] == "(":
                node.children.append(_parse_list())
            else:
                node.children.append(_parse_atom(tokens[pos]))
                pos += 1
        if pos >= len(tokens):
            raise SexpParseError("Unexpected end of input — missing ')'")
        pos += 1  # consume ')'
        return node

    root = _parse_list()
    return root


def _quote_string(s: str) -> str:
    """Quote a string for S-expression output if needed."""
    if not s or " " in s or '"' in s or "(" in s or ")" in s or "\n" in s:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    # Check if it looks like a number — if so, don't quote
    return s


def _format_atom(atom: Atom) -> str:
    """Format an atom for output."""
    if isinstance(atom, str):
        # Always quote strings that were originally quoted or contain special chars
        # For KiCad compatibility, quote strings that aren't simple identifiers
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.+\-:*]*$", atom):
            return atom
        return _quote_string(atom)
    elif isinstance(atom, float):
        # KiCad uses specific float formatting
        if atom == int(atom):
            return f"{int(atom)}"
        return f"{atom:g}"
    elif isinstance(atom, int):
        return str(atom)
    return str(atom)


def format_sexp(node: SexpNode, indent: int = 0, compact: bool = False) -> str:
    """Format a SexpNode tree back into S-expression text.

    Args:
        node: The node to format.
        indent: Current indentation level.
        compact: If True, output on a single line (used for simple nodes).
    """
    if not isinstance(node, SexpList):
        return _format_atom(node)

    tag = node.tag
    children_strs: list[str] = []
    for child in node.children:
        if isinstance(child, SexpList):
            children_strs.append(format_sexp(child, indent + 1))
        else:
            children_strs.append(_format_atom(child))

    # Decide formatting strategy
    has_sublists = any(isinstance(c, SexpList) for c in node.children)

    # Simple nodes (no sub-lists, short) go on one line
    if not has_sublists:
        one_line = "(" + " ".join(children_strs) + ")"
        if len(one_line) < 120 or compact:
            return one_line

    # For nodes with sublists, put the tag + atoms on the first line,
    # and sublists on subsequent indented lines
    prefix_parts: list[str] = []
    sublist_parts: list[str] = []
    for i, child in enumerate(node.children):
        if isinstance(child, SexpList):
            sublist_parts.append(format_sexp(child, indent + 1))
        else:
            prefix_parts.append(_format_atom(child))

    if not sublist_parts:
        return "(" + " ".join(prefix_parts) + ")"

    indent_str = "  " * (indent + 1)
    lines = ["(" + " ".join(prefix_parts)]
    for part in sublist_parts:
        lines.append(indent_str + part)
    lines.append("  " * indent + ")")
    return "\n".join(lines)


def parse_file(file_path: str) -> SexpList:
    """Parse a KiCad file and return the S-expression tree."""
    with open(file_path, "r", encoding="utf-8") as f:
        return parse(f.read())


def write_file(file_path: str, root: SexpList) -> None:
    """Write an S-expression tree to a KiCad file."""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(format_sexp(root))
        f.write("\n")
