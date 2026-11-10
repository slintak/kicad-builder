"""Checks: DRC errors, work in progress, panel rules on changed copies of the demo."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import BOARD, copy_demo, manifest, run_fab, run_python, zip_names

ADD_TRACK = """
import sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
x0, y0, x1, y1, net = float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), sys.argv[6]
track = pcbnew.PCB_TRACK(board)
track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0)))
track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
track.SetWidth(pcbnew.FromMM(0.25))
track.SetLayer(pcbnew.F_Cu)
track.SetNet(board.FindNet(net))
board.Add(track)
board.Save(sys.argv[1])
"""


def add_track(pcb: Path, x0: float, y0: float, x1: float, y1: float, net: str = "/GND") -> None:
    run_python(ADD_TRACK, str(pcb), str(x0), str(y0), str(x1), str(y1), net)


def short_circuit(project: Path) -> None:
    """A GND track over the pads of R1 and D1 (nets VCC and LED_A): DRC errors."""
    add_track(project / f"{BOARD}.kicad_pcb", 112.0, 105.5, 119.5, 105.5)


def test_check_passes(tmp_path):
    project = copy_demo(tmp_path / "demo")
    result = run_fab("check", str(project))
    assert result.returncode == 0, result.stderr[-3000:]
    data = manifest(project / "build")
    assert data["checks"]["passed"]
    assert (project / "build/reports/panel-check.json").is_file()


def test_check_fails_on_drc_error(tmp_path):
    project = copy_demo(tmp_path / "demo")
    short_circuit(project)
    result = run_fab("check", str(project))
    assert result.returncode != 0
    findings = manifest(project / "build")["checks"]["findings"]
    assert any(f["source"] == "drc" and f["severity"] == "error" for f in findings)


def test_build_fails_and_marks_outputs(tmp_path):
    project = copy_demo(tmp_path / "demo")
    short_circuit(project)
    result = run_fab("build", str(project), "--skip", "step,render")
    assert result.returncode != 0
    out = project / "build"
    assert (out / "fab/DO-NOT-ORDER.txt").is_file()
    assert "DO-NOT-ORDER.txt" in zip_names(out / f"fab/{BOARD}_gerbers.zip")


def test_wip_option(tmp_path):
    project = copy_demo(tmp_path / "demo")
    short_circuit(project)
    result = run_fab("build", str(project), "--wip", "--skip", "step,render")
    assert result.returncode == 0, result.stderr[-3000:]
    out = project / "build"
    for d in ("bom", "fab", "panel"):
        assert (out / d / "DO-NOT-ORDER.txt").is_file(), d
    for archive in out.rglob("*.zip"):
        assert "DO-NOT-ORDER.txt" in zip_names(archive), archive.name
    data = manifest(out)
    assert data["wip"] and data["do_not_order"]
    assert any(f["source"] == "drc" and f["severity"] == "warning" for f in data["checks"]["findings"])


def test_wip_text_variable(tmp_path):
    project = copy_demo(tmp_path / "demo")
    pro = project / f"{BOARD}.kicad_pro"
    data = json.loads(pro.read_text(encoding="utf-8"))
    data["text_variables"]["WIP_MARK"] = "WORK IN PROGRESS - DO NOT ORDER"
    pro.write_text(json.dumps(data, indent=2), encoding="utf-8")
    short_circuit(project)
    result = run_fab("check", str(project))
    assert result.returncode == 0, result.stderr[-3000:]
    assert manifest(project / "build")["wip"]


def test_wip_keeps_parity_strict(tmp_path):
    project = copy_demo(tmp_path / "demo")
    pcb = project / f"{BOARD}.kicad_pcb"
    # A value change on the board only: schematic parity error.
    text = pcb.read_text(encoding="utf-8")
    assert '(property "Value" "10k"' in text
    pcb.write_text(text.replace('(property "Value" "10k"', '(property "Value" "22k"', 1), encoding="utf-8")
    result = run_fab("check", str(project), "--wip")
    assert result.returncode != 0
    findings = manifest(project / "build")["checks"]["findings"]
    assert any(f["source"] == "parity" and f["severity"] == "error" for f in findings)


def _panel_rules(project: Path) -> dict[str, list[dict]]:
    report = json.loads((project / "build/reports/panel-check.json").read_text(encoding="utf-8"))
    rules: dict[str, list[dict]] = {}
    for rule in report["rules"]:
        rules.setdefault(rule["rule"], []).append(rule)
    return rules


def test_panel_check_narrow_rail(tmp_path):
    project = copy_demo(tmp_path / "demo")
    panel = project / "panel.json"
    preset = json.loads(panel.read_text(encoding="utf-8"))
    preset["framing"]["width"] = "3mm"
    # Keep the fiducials and tooling holes in the rail.
    preset["fiducials"]["voffset"] = "1.5mm"
    preset["tooling"]["voffset"] = "1.5mm"
    panel.write_text(json.dumps(preset), encoding="utf-8")
    result = run_fab("panel", str(project))
    assert result.returncode != 0
    rules = _panel_rules(project)
    assert any(r["status"] == "error" for r in rules["rails"])
    assert any(r["status"] == "error" for r in rules["fiducial_edge"])


def test_panel_check_vcut_copper(tmp_path):
    project = copy_demo(tmp_path / "demo")
    # A copper track 0.25 mm from the top edge: the V-cut at the top board edge is too near.
    add_track(project / f"{BOARD}.kicad_pcb", 106.0, 100.25, 134.0, 100.25)
    result = run_fab("panel", str(project))
    assert result.returncode != 0
    rules = _panel_rules(project)
    errors = [r for r in rules["vcut_copper"] if r["status"] == "error"]
    assert errors and all(r["value"] < 0.4 for r in errors)


def test_panel_check_ignore(tmp_path):
    project = copy_demo(tmp_path / "demo")
    add_track(project / f"{BOARD}.kicad_pcb", 106.0, 100.25, 134.0, 100.25)
    config = project / "kicad-fab.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[panel]\nignore = ["vcut_copper"]\n',
                      encoding="utf-8")
    result = run_fab("panel", str(project))
    assert result.returncode == 0, result.stderr[-3000:]


def test_entrypoint_runs_command():
    result = subprocess.run(["/usr/local/bin/kicad-builder-entrypoint", "kicad-fab", "--version"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.startswith("kicad-fab ")


FRAME_FILL = """
import sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
frame = [z for z in board.Zones()
         if not z.GetIsRuleArea() and z.GetNetCode() == 0 and pcbnew.ToMM(z.GetBoundingBox().GetWidth()) > 60]
assert frame, "no frame copper fill"
for zone in frame:
    filled = zone.GetFilledPolysList(zone.GetFirstLayer())
    assert filled.OutlineCount() >= 2, "only one part of the rail has copper"
    # The fill covers the rail from the left to the right end (less the edge clearance).
    assert pcbnew.ToMM(filled.BBox().GetWidth()) > pcbnew.ToMM(zone.GetBoundingBox().GetWidth()) - 3
"""


def test_frame_copper_fill_on_all_rail_parts(built):
    """V-cuts cut the rails into parts. Every part of a rail gets the frame copper."""
    run_python(FRAME_FILL, str(built["out"] / f"panel/{BOARD}_panel.kicad_pcb"))
