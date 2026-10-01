"""Tests for project settings (net classes, design rules, custom rules)."""

import json
import os
import shutil
import tempfile

import pytest

from kicad_mcp import project

PRO = {
    "board": {"design_settings": {"rules": {"min_clearance": 0.0}}},
    "net_settings": {
        "classes": [{"name": "Default", "clearance": 0.2, "track_width": 0.2,
                     "via_diameter": 0.6, "via_drill": 0.3}],
        "meta": {"version": 3},
        "netclass_patterns": [],
    },
}


@pytest.fixture
def pro_path():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "board.kicad_pro")
    with open(path, "w") as f:
        json.dump(PRO, f)
    yield path
    shutil.rmtree(d)


def _read(path):
    with open(path) as f:
        return json.load(f)


class TestNetClass:
    def test_new_class_copies_default_and_assigns_nets(self, pro_path):
        pcb = pro_path.replace(".kicad_pro", ".kicad_pcb")
        out = project.set_net_class(pcb, "Power", ["/VBAT", "GND"], track_width=1.0)
        assert out["track_width"] == 1.0 and out["clearance"] == 0.2
        ns = _read(pro_path)["net_settings"]
        assert {c["name"] for c in ns["classes"]} == {"Default", "Power"}
        assert ns["netclass_patterns"] == [
            {"netclass": "Power", "pattern": "/VBAT"},
            {"netclass": "Power", "pattern": "GND"},
        ]

    def test_reassigning_moves_net_between_classes(self, pro_path):
        project.set_net_class(pro_path, "Power", ["/VBAT", "+5V"])
        project.set_net_class(pro_path, "HighCurrent", ["/VBAT"], track_width=2.0)
        pats = _read(pro_path)["net_settings"]["netclass_patterns"]
        assert {"netclass": "HighCurrent", "pattern": "/VBAT"} in pats
        assert {"netclass": "Power", "pattern": "/VBAT"} not in pats
        assert {"netclass": "Power", "pattern": "+5V"} in pats

    def test_rejects_drill_not_smaller_than_diameter(self, pro_path):
        with pytest.raises(ValueError):
            project.set_net_class(pro_path, "Bad", via_diameter=0.4, via_drill=0.4)

    def test_list(self, pro_path):
        project.set_net_class(pro_path, "Power", ["/VBAT"])
        listed = project.list_net_classes(pro_path)
        assert [c["name"] for c in listed["classes"]] == ["Default", "Power"]


class TestDesignRules:
    def test_updates_known_keys(self, pro_path):
        out = project.set_design_rules(pro_path, {"min_clearance": 0.127, "min_track_width": 0.127})
        assert out["min_clearance"] == 0.127
        assert _read(pro_path)["board"]["design_settings"]["rules"]["min_track_width"] == 0.127

    def test_rejects_unknown_key(self, pro_path):
        with pytest.raises(ValueError):
            project.set_design_rules(pro_path, {"min_widget": 1})


class TestCustomRules:
    def test_writes_dru_with_version_header(self, pro_path):
        path = project.set_custom_rules(
            pro_path, '(rule "u1" (condition "A.Parent.Reference == \'U1\'") (constraint hole_size (min 0.2mm)))'
        )
        assert path.endswith("board.kicad_dru")
        with open(path) as f:
            assert f.read().startswith("(version 1)\n(rule")

    def test_rejects_unbalanced(self, pro_path):
        with pytest.raises(ValueError):
            project.set_custom_rules(pro_path, '(rule "x" (condition "A.Type == \'Pad\'")')

    def test_keeps_existing_file_mode(self, pro_path):
        os.chmod(pro_path, 0o644)
        project.set_net_class(pro_path, "Power", ["/VBAT"])
        assert os.stat(pro_path).st_mode & 0o777 == 0o644
