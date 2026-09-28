"""Tests for the S-expression parser/writer."""

import glob
import os

import pytest

from kicad_mcp.sexp_parser import (
    QuotedString,
    SexpList,
    SexpParseError,
    _format_atom,
    escape_sexp_string,
    format_sexp,
    parse,
    write_file,
)


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


class TestQuotingRoundTrip:
    """Tests for quote preservation on round-trip parse -> format -> parse."""

    def test_quoted_string_survives_roundtrip(self):
        original = '(symbol (lib_id "Device:R") (at 0 0))'
        root = parse(original)
        formatted = format_sexp(root)
        assert '"Device:R"' in formatted
        reparsed = parse(formatted)
        assert str(reparsed.find("lib_id").children[1]) == "Device:R"

    def test_bare_colon_string_gets_quoted(self):
        """A bare str containing a colon should be quoted by _format_atom."""
        assert _format_atom("Device:R") == '"Device:R"'

    def test_bare_tag_stays_bare(self):
        """Tags like 'symbol' should NOT be quoted."""
        assert _format_atom("symbol") == "symbol"
        assert _format_atom("kicad_sch") == "kicad_sch"
        assert _format_atom("wire") == "wire"

    def test_uuid_stays_quoted(self):
        sexp = '(uuid "a0000001-aaaa-bbbb-cccc-000000000001")'
        formatted = format_sexp(parse(sexp))
        assert '"a0000001-aaaa-bbbb-cccc-000000000001"' in formatted

    def test_generator_version_stays_quoted(self):
        sexp = '(generator_version "8.0")'
        formatted = format_sexp(parse(sexp))
        assert '"8.0"' in formatted

    def test_pin_number_stays_quoted(self):
        sexp = '(pin "1" (uuid "abc"))'
        formatted = format_sexp(parse(sexp))
        assert '"1"' in formatted

    def test_manual_assignment_with_quoted_string(self):
        """Manually assigning QuotedString preserves quotes."""
        root = parse('(symbol (lib_id "OldLib:OldSym"))')
        lib_id_node = root.find("lib_id")
        lib_id_node.children[1] = QuotedString("Device:R")
        formatted = format_sexp(root)
        assert '"Device:R"' in formatted

    def test_manual_assignment_bare_string_colon(self):
        """Even if someone forgets QuotedString, colon triggers quoting."""
        root = parse('(symbol (lib_id "OldLib:OldSym"))')
        lib_id_node = root.find("lib_id")
        lib_id_node.children[1] = "Device:R"  # bare str — should still be quoted
        formatted = format_sexp(root)
        assert '"Device:R"' in formatted

    def test_full_schematic_header_roundtrip(self):
        sexp = (
            '(kicad_sch (version 20231120) (generator "eeschema") '
            '(generator_version "8.0") (uuid "a0000001-aaaa-bbbb-cccc-000000000001") '
            '(paper "A3"))'
        )
        formatted = format_sexp(parse(sexp))
        assert '"eeschema"' in formatted
        assert '"8.0"' in formatted
        assert '"a0000001-aaaa-bbbb-cccc-000000000001"' in formatted
        assert '"A3"' in formatted


class TestEscapeSexpString:
    def test_escapes_quotes(self):
        assert escape_sexp_string('say "hi"') == 'say \\"hi\\"'

    def test_escapes_backslash(self):
        assert escape_sexp_string("path\\to\\file") == "path\\\\to\\\\file"

    def test_clean_string_unchanged(self):
        assert escape_sexp_string("Device:R") == "Device:R"

    def test_empty_string(self):
        assert escape_sexp_string("") == ""


class TestEscapes:
    def test_newline_escape_is_decoded(self):
        root = parse(r'(text "line1\nline2")')
        assert root.children[1] == "line1\nline2"

    def test_newline_survives_repeated_roundtrips(self):
        src = r'(text "line1\nline2 \"quoted\" back\\slash")'
        once = format_sexp(parse(src))
        twice = format_sexp(parse(once))
        assert once == src
        assert twice == src

    def test_escape_sexp_string_escapes_newlines(self):
        assert escape_sexp_string("a\nb") == "a\\nb"

    def test_escaped_value_roundtrips_through_parse(self):
        value = 'tab\there "q" \\ end\n'
        root = parse(f'(v "{escape_sexp_string(value)}")')
        assert root.children[1] == value


class TestNumbers:
    def test_parsed_float_spelling_is_preserved(self):
        src = "(x 12.000000 0.0000 1.5)"
        assert format_sexp(parse(src)) == src

    def test_float_precision_not_truncated(self):
        assert _format_atom(123.456789) == "123.456789"
        assert _format_atom(105.123456) == "105.123456"

    def test_no_exponent_notation(self):
        assert _format_atom(1234567.5) == "1234567.5"
        assert _format_atom(0.00001) == "0.00001"

    def test_arithmetic_noise_is_dropped(self):
        assert _format_atom(3 * 1.27) == "3.81"
        assert _format_atom(-0.0) == "0"

    def test_non_decimal_tokens_stay_symbols(self):
        root = parse("(a inf nan 1_000)")
        assert root.children[1:] == ["inf", "nan", "1_000"]
        assert format_sexp(root) == "(a inf nan 1_000)"


class TestKiCadLayout:
    def test_matches_kicad_indentation(self):
        root = parse(
            '(kicad_sch (version 20231120) (wire (pts (xy 1 2) (xy 3 4)) '
            '(stroke (width 0) (type default))) (pin_names (offset 0.762) hide))'
        )
        assert format_sexp(root) == (
            "(kicad_sch\n"
            "\t(version 20231120)\n"
            "\t(wire\n"
            "\t\t(pts\n"
            "\t\t\t(xy 1 2) (xy 3 4)\n"
            "\t\t)\n"
            "\t\t(stroke\n"
            "\t\t\t(width 0)\n"
            "\t\t\t(type default)\n"
            "\t\t)\n"
            "\t)\n"
            "\t(pin_names\n"
            "\t\t(offset 0.762) hide)\n"
            ")"
        )

    def test_long_atom_runs_wrap(self):
        uuids = " ".join(f'"{i:08d}-0000-0000-0000-000000000000"' for i in range(4))
        out = format_sexp(parse(f"(g (members {uuids}))"))
        assert out.count("\n") > 2
        assert parse(out).find("members").children[1:] == parse(f"(m {uuids})").children[1:]

    @pytest.mark.skipif(
        not os.path.isdir("/usr/share/kicad/demos"), reason="KiCad demos not installed"
    )
    def test_kicad_saved_files_roundtrip_byte_identical(self):
        paths = sorted(glob.glob("/usr/share/kicad/demos/**/*.kicad_sch", recursive=True))
        checked = 0
        for path in paths:
            with open(path, encoding="utf-8", newline="") as f:
                src = f.read()
            if "(generator_version \"8.0\")" not in src[:400] or "\n\t(" not in src[:400]:
                continue  # only files saved by KiCad 8 itself
            if "(image" in src or "(group" in src:
                continue  # rare layouts KiCad itself formats inconsistently
            assert format_sexp(parse(src)) == src.rstrip("\n"), path
            checked += 1
        if not checked:
            pytest.skip("no KiCad 8 schematics among the demos")


class TestParserStructure:
    def test_remove_child_uses_identity(self):
        root = parse("(r (a 1) (a 1))")
        second = root.children[2]
        assert root.remove_child(second)
        assert len(root.children) == 2
        assert root.children[1] is not second

    def test_unbalanced_input_raises(self):
        with pytest.raises(SexpParseError):
            parse("(a (b)")
        with pytest.raises(SexpParseError):
            parse('(a "unterminated)')


class TestWriteFile:
    def test_atomic_write_preserves_mode_and_leaves_no_temp(self, tmp_path):
        path = tmp_path / "board.kicad_pcb"
        path.write_text("(old)\n")
        os.chmod(path, 0o640)
        write_file(str(path), parse("(new (x 1))"))
        assert path.read_text() == "(new\n\t(x 1)\n)\n"
        assert (os.stat(path).st_mode & 0o777) == 0o640
        assert os.listdir(tmp_path) == ["board.kicad_pcb"]
