"""ERC and DRC items that the designer excluded in KiCad are not findings."""

from __future__ import annotations

import json

from kicad_fab.kicad import check_drc, check_erc


def test_drc_exclusions(tmp_path):
    report = tmp_path / "drc.json"
    report.write_text(json.dumps({"violations": [
        {"type": "clearance", "severity": "error", "description": "excluded one", "excluded": True, "items": []},
        {"type": "clearance", "severity": "error", "description": "real one", "items": []}],
        "unconnected_items": [], "schematic_parity": []}), encoding="utf-8")
    findings = check_drc(report, strict=False, wip=False)
    assert [f.message for f in findings.errors] == ["real one"]


def test_erc_exclusions(tmp_path):
    report = tmp_path / "erc.json"
    report.write_text(json.dumps({"sheets": [{"path": "/", "violations": [
        {"type": "pin_to_pin", "severity": "error", "description": "excluded one", "excluded": True, "items": []},
        {"type": "pin_to_pin", "severity": "warning", "description": "real one", "items": []}]}]}), encoding="utf-8")
    findings = check_erc(report, strict=False)
    assert not findings.errors
    assert [f.message for f in findings.warnings] == ["real one (sheet /)"]
