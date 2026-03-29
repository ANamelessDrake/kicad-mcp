"""Tests for the S-expression parser/writer."""

import pytest

from kicad_mcp.sexp_parser import SexpList, format_sexp, parse


class TestParse:
    def test_simple_list(self):
        result = parse('(hello "world")')
        assert isinstance(result, SexpList)
        assert result.tag == "hello"
        assert result.children[1] == "world"

    def test_nested_lists(self):
        result = parse("(a (b 1) (c 2))")
        assert result.tag == "a"
        b = result.find("b")
        assert b is not None
        assert b.children[1] == 1
        c = result.find("c")
        assert c is not None
        assert c.children[1] == 2

    def test_numbers(self):
        result = parse("(pos 100 50.5 -3)")
        assert result.children[1] == 100
        assert result.children[2] == 50.5
        assert result.children[3] == -3

    def test_quoted_string_with_spaces(self):
        result = parse('(name "hello world")')
        assert result.children[1] == "hello world"

    def test_quoted_string_with_escaped_quotes(self):
        result = parse(r'(name "say \"hi\"")')
        assert result.children[1] == 'say "hi"'

    def test_uuid(self):
        result = parse('(uuid "a1b2c3d4-e5f6-7890-abcd-ef1234567890")')
        assert result.children[1] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_deeply_nested(self):
        result = parse("(a (b (c (d 42))))")
        d = result.find_deep("d")
        assert d is not None
        assert d.children[1] == 42

    def test_find_all(self):
        result = parse("(root (item 1) (item 2) (item 3) (other 4))")
        items = result.find_all("item")
        assert len(items) == 3

    def test_find_value(self):
        result = parse('(root (version 20231120) (generator "kicad"))')
        assert result.find_value("version") == 20231120
        assert result.find_value("generator") == "kicad"

    def test_empty_input_raises(self):
        with pytest.raises(Exception):
            parse("")


class TestFormat:
    def test_simple_roundtrip(self):
        original = '(hello "world" 42)'
        result = parse(original)
        formatted = format_sexp(result)
        reparsed = parse(formatted)
        assert reparsed.tag == "hello"
        assert reparsed.children[1] == "world"
        assert reparsed.children[2] == 42

    def test_nested_roundtrip(self):
        original = '(kicad_sch (version 20231120) (generator "test"))'
        result = parse(original)
        formatted = format_sexp(result)
        reparsed = parse(formatted)
        assert reparsed.find_value("version") == 20231120

    def test_preserves_structure(self):
        sexp = "(root (a 1) (b (c 2) (d 3)))"
        result = parse(sexp)
        formatted = format_sexp(result)
        reparsed = parse(formatted)
        assert reparsed.find("a") is not None
        b = reparsed.find("b")
        assert b is not None
        assert b.find("c") is not None
        assert b.find("d") is not None


class TestSexpListMethods:
    def test_append(self):
        node = SexpList(["root"])
        node.append(SexpList(["child", 1]))
        assert len(node.children) == 2
        assert node.find("child") is not None

    def test_remove_child(self):
        child = SexpList(["child", 1])
        node = SexpList(["root", child])
        assert node.remove_child(child)
        assert len(node.children) == 1

    def test_remove_nonexistent(self):
        node = SexpList(["root"])
        other = SexpList(["other"])
        assert not node.remove_child(other)
