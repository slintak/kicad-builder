"""BOM and CPL files of the board and of the panel."""

from __future__ import annotations

from collections import Counter

from conftest import ASSEMBLED, BOARD, COPIES, PITCH, copy_demo, footprint_refs, read_csv, run_fab

JLC_BOM = ["Comment", "Designator", "Footprint", "LCSC Part #"]
CPL = ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]
MOUSER = ["Mouser Part Number", "Manufacturer Part Number", "Quantity", "Customer Part Number", "Description",
          "Manufacturer", "Alt Mouser Part Number", "Alt MPN"]
ALTERNATES = ["References", "Quantity", "Primary MPN", "Alt Manufacturer", "Alt MPN", "Alt Mouser Part Number",
              "Alt LCSC"]


def _designators(rows) -> list[str]:
    return [d for r in rows for d in r["Designator"].split(",")]


def test_headers(built):
    out = built["out"]
    assert read_csv(out / f"bom/{BOARD}_bom_jlcpcb.csv")[0] == JLC_BOM
    assert read_csv(out / f"panel/{BOARD}_panel_bom_jlcpcb.csv")[0] == JLC_BOM
    assert read_csv(out / f"fab/{BOARD}_pos_jlcpcb.csv")[0] == CPL
    assert read_csv(out / f"panel/{BOARD}_panel_pos_jlcpcb.csv")[0] == CPL
    assert read_csv(out / f"bom/{BOARD}_bom_mouser.csv")[0] == MOUSER
    assert read_csv(out / f"bom/{BOARD}_bom_alternates.csv")[0] == ALTERNATES


def test_board_cpl(built):
    _, rows = read_csv(built["out"] / f"fab/{BOARD}_pos_jlcpcb.csv")
    refs = [r["Designator"] for r in rows]
    assert sorted(refs) == sorted(ASSEMBLED)
    assert len(refs) == len(set(refs))
    by_ref = {r["Designator"]: r for r in rows}
    assert by_ref["C3"]["Layer"] == "Bottom"
    assert {r["Layer"] for d, r in by_ref.items() if d != "C3"} == {"Top"}
    # Aux origin at the lower left board corner: all positions are on the board.
    for r in rows:
        assert 0 < float(r["Mid X"]) < PITCH[0] and 0 < float(r["Mid Y"]) < PITCH[1]


def test_board_bom(built):
    _, rows = read_csv(built["out"] / f"bom/{BOARD}_bom_jlcpcb.csv")
    designators = _designators(rows)
    assert sorted(designators) == sorted(ASSEMBLED)
    by_ref = {d: r for r in rows for d in r["Designator"].split(",")}
    assert by_ref["R1"]["Designator"] == "R1,R2"
    assert by_ref["R1"]["LCSC Part #"] == "C100001"
    assert by_ref["C1"]["Footprint"] == "C_0402"
    assert by_ref["L1"]["LCSC Part #"] == ""


def test_correction(built, tmp_path):
    """D1 has JLCPCB_CORRECTION '0;0;180'. Without the field the rotation is 180 degrees less."""
    _, rows = read_csv(built["out"] / f"fab/{BOARD}_pos_jlcpcb.csv")
    with_corr = float({r["Designator"]: r for r in rows}["D1"]["Rotation"])
    project = copy_demo(tmp_path / "demo")
    sch = project / f"{BOARD}.kicad_sch"
    sch.write_text(sch.read_text(encoding="utf-8").replace('"JLCPCB_CORRECTION" "0;0;180"',
                                                           '"JLCPCB_CORRECTION" ""'), encoding="utf-8")
    assert run_fab("pos", str(project)).returncode == 0
    _, rows = read_csv(project / f"build/fab/{BOARD}_pos_jlcpcb.csv")
    without = float({r["Designator"]: r for r in rows}["D1"]["Rotation"])
    assert (with_corr - without) % 360 == 180


def test_panel_refs_on_silkscreen(built):
    refs = Counter(footprint_refs(built["out"] / f"panel/{BOARD}_panel.kicad_pcb"))
    source = Counter(footprint_refs(built["project"] / f"{BOARD}.kicad_pcb"))
    for ref in source:
        assert refs[ref] == COPIES, ref
    assert not [r for r in refs if "_" in r and r.split("_")[0] in source]


def _copies(rows) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["Designator"].rsplit("_", 1)[0], []).append(r)
    return out


def test_panel_cpl_unique(built):
    _, rows = read_csv(built["out"] / f"panel/{BOARD}_panel_pos_jlcpcb.csv")
    # Origin at the lower left panel corner: all positions are positive.
    assert all(float(r["Mid X"]) > 0 and float(r["Mid Y"]) > 0 for r in rows)
    names = [r["Designator"] for r in rows]
    assert len(names) == len(ASSEMBLED) * COPIES
    assert len(set(names)) == len(names)
    assert {n.rsplit("_", 1)[0] for n in names} == ASSEMBLED
    assert {n.rsplit("_", 1)[1] for n in names} == {str(i + 1) for i in range(COPIES)}
    _, board_rows = read_csv(built["out"] / f"fab/{BOARD}_pos_jlcpcb.csv")
    board = {r["Designator"]: r for r in board_rows}
    for ref, copies in _copies(rows).items():
        by_n = {int(r["Designator"].rsplit("_", 1)[1]): r for r in copies}
        x1, y1 = float(by_n[1]["Mid X"]), float(by_n[1]["Mid Y"])
        # Copies 1, 2 in the top row, 3, 4 below (Y up in the CPL).
        expected = {1: (0, 0), 2: (PITCH[0], 0), 3: (0, -PITCH[1]), 4: (PITCH[0], -PITCH[1])}
        for n, (dx, dy) in expected.items():
            assert abs(float(by_n[n]["Mid X"]) - x1 - dx) < 0.01, (ref, n)
            assert abs(float(by_n[n]["Mid Y"]) - y1 - dy) < 0.01, (ref, n)
            assert by_n[n]["Layer"] == board[ref]["Layer"]
            assert by_n[n]["Rotation"] == board[ref]["Rotation"]


def test_panel_bom_matches_cpl(built):
    _, bom_rows = read_csv(built["out"] / f"panel/{BOARD}_panel_bom_jlcpcb.csv")
    _, cpl_rows = read_csv(built["out"] / f"panel/{BOARD}_panel_pos_jlcpcb.csv")
    designators = _designators(bom_rows)
    assert len(designators) == len(set(designators))
    assert set(designators) == {r["Designator"] for r in cpl_rows}
    _, board_rows = read_csv(built["out"] / f"bom/{BOARD}_bom_jlcpcb.csv")
    assert len(bom_rows) == len(board_rows)
    for board_row, panel_row in zip(board_rows, bom_rows):
        assert panel_row["LCSC Part #"] == board_row["LCSC Part #"]
        refs = board_row["Designator"].split(",")
        assert panel_row["Designator"].split(",") == [f"{r}_{n + 1}" for r in refs for n in range(COPIES)]


def test_excluded_parts(built):
    out = built["out"]
    for path in (f"fab/{BOARD}_pos_jlcpcb.csv", f"panel/{BOARD}_panel_pos_jlcpcb.csv"):
        names = {r["Designator"].rsplit("_", 1)[0] for r in read_csv(out / path)[1]}
        assert "J1" not in names and "R3" not in names
    for path in (f"bom/{BOARD}_bom_jlcpcb.csv", f"panel/{BOARD}_panel_bom_jlcpcb.csv"):
        names = {d.rsplit("_", 1)[0] for d in _designators(read_csv(out / path)[1])}
        assert "J1" not in names and "R3" not in names


def test_mouser_bom(built):
    _, rows = read_csv(built["out"] / f"bom/{BOARD}_bom_mouser.csv")
    by_pn = {r["Mouser Part Number"]: r for r in rows}
    # boards = 10, spares: 0402 = 10, 0603 = 5, default = 0
    assert by_pn["000-DEMO-R1K"]["Quantity"] == "30"           # R1, R2
    assert by_pn["000-DEMO-R1K"]["Customer Part Number"] == "R1,R2"
    assert by_pn["000-DEMO-C100N"]["Quantity"] == "30"          # C1, C3
    assert by_pn["000-DEMO-C1U"]["Quantity"] == "20"          # C2
    assert by_pn["000-DEMO-LEDG"]["Quantity"] == "15"            # D1, 0603
    # J1 is excluded from assembly but it is in the Mouser order.
    assert by_pn["000-DEMO-HDR4"]["Quantity"] == "10"
    assert by_pn["000-DEMO-HDR4"]["Customer Part Number"] == "J1"
    # R3 is DNP.
    assert "000-DEMO-R10K" not in by_pn
    assert by_pn["000-DEMO-R1K"]["Manufacturer Part Number"] == "DEMO-R1K-0402"
    assert by_pn["000-DEMO-R1K"]["Manufacturer"] == "Demo Parts"
    # Alternates are in the Mouser order file too (several in one cell).
    assert by_pn["000-DEMO-C1U"]["Alt Mouser Part Number"] == "000-ALT-C1U-A; 000-ALT-C1U-B"
    assert by_pn["000-DEMO-C1U"]["Alt MPN"] == "ALT-C1U-A; ALT-C1U-B"
    assert by_pn["000-DEMO-R1K"]["Alt MPN"] == ""


def test_alternates_and_other(built):
    _, rows = read_csv(built["out"] / f"bom/{BOARD}_bom_alternates.csv")
    assert [(r["References"], r["Alt MPN"], r["Alt Mouser Part Number"], r["Alt LCSC"]) for r in rows] == [
        ("C2", "ALT-C1U-A", "000-ALT-C1U-A", "C100006"),
        ("C2", "ALT-C1U-B", "000-ALT-C1U-B", ""),
    ]
    assert rows[0]["Primary MPN"] == "DEMO-C1U-0402"
    _, other = read_csv(built["out"] / f"bom/{BOARD}_bom_other.csv")
    assert [r["References"] for r in other] == ["L1"]


def test_repeat_designators(tmp_path):
    project = copy_demo(tmp_path / "demo")
    config = project / "kicad-fab.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[panel]\ndesignators = "repeat"\n', encoding="utf-8")
    result = run_fab("panel", str(project))
    assert result.returncode == 0, result.stderr[-2000:]
    _, rows = read_csv(project / f"build/panel/{BOARD}_panel_pos_jlcpcb.csv")
    counts = Counter(r["Designator"] for r in rows)
    assert counts == {ref: COPIES for ref in ASSEMBLED}
    _, bom_rows = read_csv(project / f"build/panel/{BOARD}_panel_bom_jlcpcb.csv")
    by_first = {r["Designator"].split(",")[0]: r["Designator"] for r in bom_rows}
    assert by_first["R1"] == ",".join(["R1"] * COPIES + ["R2"] * COPIES)


def test_sides_top_only(tmp_path):
    project = copy_demo(tmp_path / "demo")
    config = project / "kicad-fab.toml"
    text = config.read_text(encoding="utf-8").replace('exclude = ["J1"]', 'exclude = ["J1"]\nsides = "top"')
    config.write_text(text, encoding="utf-8")
    assert run_fab("pos", str(project)).returncode == 0
    names = {r["Designator"] for r in read_csv(project / f"build/fab/{BOARD}_pos_jlcpcb.csv")[1]}
    assert names == ASSEMBLED - {"C3"}
