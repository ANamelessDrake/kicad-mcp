"""Lossless S-expression parser and writer for KiCad files.

KiCad uses a Lisp-like S-expression format. This parser preserves the
structure exactly so that read -> modify -> write round-trips without
corrupting unmodified sections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


# A parsed S-expression node is either a SexpList (parenthesized group)
# or an atom (string/number token).


class QuotedString(str):
    """A string that was originally quoted in the S-expression source.

    Preserves quoting information so the formatter can reproduce
    the original quoting style on round-trip.
    """


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


def escape_sexp_string(s: str) -> str:
    """Escape a string for safe embedding in an S-expression f-string.

    Use this when building S-expression text via f-strings with user-supplied
    values. It escapes backslashes and double quotes so that the value is safe
    inside a quoted S-expression token.

    Example::

        sexp_text = f'(name "{escape_sexp_string(user_input)}")'
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


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
        # Quoted string — unescape and mark as originally quoted
        inner = token[1:-1]
        inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return QuotedString(inner)

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


def _is_numeric(s: str) -> bool:
    """Check if a string would be parsed as a number."""
    try:
        int(s)
        return True
    except ValueError:
        pass
    try:
        float(s)
        return True
    except ValueError:
        return False


def _format_atom(atom: Atom) -> str:
    """Format an atom for output."""
    if isinstance(atom, QuotedString):
        # Always re-quote strings that were originally quoted
        escaped = str(atom).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(atom, str):
        # Quote strings that look numeric — KiCad requires pin numbers etc.
        # to remain quoted strings, not bare integers
        if _is_numeric(atom):
            return f'"{atom}"'
        # Quote if empty or contains special characters.
        # Colons are included because library refs like "Device:R" must be quoted;
        # KiCad tags (kicad_sch, symbol, wire, etc.) never contain colons.
        if not atom or any(c in atom for c in ' "():\n\\'):
            escaped = atom.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return atom
    elif isinstance(atom, float):
        # KiCad uses specific float formatting
        if atom == int(atom):
            return f"{int(atom)}"
        return f"{atom:g}"
    elif isinstance(atom, int):
        return str(atom)
    return str(atom)


def _format_compact(node: SexpNode) -> str:
    """Format a node entirely on one line (no line breaks)."""
    if not isinstance(node, SexpList):
        return _format_atom(node)
    parts = [_format_compact(child) for child in node.children]
    return "(" + " ".join(parts) + ")"


def format_sexp(node: SexpNode, indent: int = 0, max_depth: int = 2) -> str:
    """Format a SexpNode tree back into S-expression text.

    Only expands nodes across multiple lines at the top levels (up to max_depth).
    Deeper nodes are kept on a single line to avoid formatting issues with KiCad's
    parser, which is sensitive to how atoms and sublists are split across lines.

    Args:
        node: The node to format.
        indent: Current indentation level.
        max_depth: Maximum depth at which to expand across multiple lines.
    """
    if not isinstance(node, SexpList):
        return _format_atom(node)

    # Beyond max_depth, always compact
    if indent >= max_depth:
        return _format_compact(node)

    # Try compact first — if short enough, use it
    compact = _format_compact(node)
    if len(compact) < 120:
        return compact

    # Expand: tag + leading atoms on first line, sublists on indented lines
    prefix_parts: list[str] = []
    sublist_entries: list[SexpNode] = []
    for child in node.children:
        if isinstance(child, SexpList):
            sublist_entries.append(child)
        else:
            if sublist_entries:
                # Atom after a sublist — treat as sublist entry too
                sublist_entries.append(child)
            else:
                prefix_parts.append(_format_atom(child))

    if not sublist_entries:
        return compact

    indent_str = "  " * (indent + 1)
    lines = ["(" + " ".join(prefix_parts)]
    for entry in sublist_entries:
        lines.append(indent_str + format_sexp(entry, indent + 1, max_depth))
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
