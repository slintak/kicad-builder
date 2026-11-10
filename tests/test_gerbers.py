"""Gerber and drill files: JLCPCB file set, units, one outline file, V-cuts in the outline."""

from __future__ import annotations

import re

import pytest

import gerber
from conftest import BOARD, read_zip_text, zip_names

# Protel extensions of KiCad for a 4 layer board.
BOARD_EXTENSIONS = {"gtl", "g1", "g2", "gbl", "gts", "gbs", "gto", "gbo", "gtp", "gbp", "gm1"}
OUTLINE_EXTENSIONS = {"gm1", "gko"}


def _gerbers(names):
    return [n for n in names if not n.endswith(".drl") and n != "DO-NOT-ORDER.txt"]


@pytest.mark.parametrize("zip_name", [f"fab/{BOARD}_gerbers.zip", f"panel/{BOARD}_panel_gerbers.zip"])
def test_file_set(built, zip_name):
    names = zip_names(built["out"] / zip_name)
    extensions = {n.rsplit(".", 1)[-1].lower() for n in _gerbers(names)}
    assert extensions == BOARD_EXTENSIONS
    assert len(_gerbers(names)) == len(BOARD_EXTENSIONS)
    outlines = [n for n in names if n.rsplit(".", 1)[-1].lower() in OUTLINE_EXTENSIONS]
    assert len(outlines) == 1
    assert not [n for n in names if re.search(r"cmt|comment|user|gbrjob|drl_map|\.gbr$", n, re.I)]
    drills = sorted(n for n in names if n.endswith(".drl"))
    assert [d.rsplit("-", 1)[-1] for d in drills] == ["NPTH.drl", "PTH.drl"]


@pytest.mark.parametrize("zip_name", [f"fab/{BOARD}_gerbers.zip", f"panel/{BOARD}_panel_gerbers.zip"])
def test_units_and_format(built, zip_name):
    path = built["out"] / zip_name
    for name in zip_names(path):
        text = read_zip_text(path, name)
        if name.endswith(".drl"):
            assert "METRIC" in text
            assert "FORMAT={-:-/ absolute / metric / decimal}" in text
        else:
            g = gerber.parse(text)
            assert g.units == "MM", name
            assert g.format == (4, 6), name
    copper = read_zip_text(path, next(n for n in zip_names(path) if n.endswith(".gtl")))
    # Netlist attributes.
    assert "TO.N," in copper or "TO.C," in copper


def _outline(path):
    name = next(n for n in zip_names(path) if n.rsplit(".", 1)[-1].lower() in OUTLINE_EXTENSIONS)
    return gerber.parse(read_zip_text(path, name))


def _panel_vcuts(pcb_text: str) -> list[tuple[float, float, float, float]]:
    """V-cut lines in the panel PCB: gr_line on Cmts.User as KiKit writes them."""
    lines = []
    for m in re.finditer(r"\(gr_line\s+\(start ([-\d.]+) ([-\d.]+)\)\s+\(end ([-\d.]+) ([-\d.]+)\)(.*?)\n\t\)",
                         pcb_text, re.S):
        if '(layer "Cmts.User")' in m.group(5):
            lines.append(tuple(float(m.group(i)) for i in range(1, 5)))
    return lines


def test_vcuts_in_outline(built):
    out = built["out"]
    pcb_text = (out / f"panel/{BOARD}_panel.kicad_pcb").read_text(encoding="utf-8")
    vcuts = _panel_vcuts(pcb_text)
    # 2 x 2 grid with rails top and bottom: 3 horizontal and 1 vertical V-cut.
    assert len([v for v in vcuts if abs(v[1] - v[3]) < 1e-6]) == 3
    assert len([v for v in vcuts if abs(v[0] - v[2]) < 1e-6]) == 1
    g = _outline(out / f"panel/{BOARD}_panel_gerbers.zip")
    # Gerber origin is the lower left panel corner and Gerber Y is up: KiCad Y is down.
    ox, oy = _origin(out / f"panel/{BOARD}_panel.kicad_pcb")
    for d in g.draws:
        d.x0, d.x1, d.y0, d.y1 = d.x0 + ox, d.x1 + ox, d.y0 - oy, d.y1 - oy
    draws = {(round(d.x0, 3), round(-d.y0, 3), round(d.x1, 3), round(-d.y1, 3)) for d in g.draws}
    vcut_apertures = set()
    for x0, y0, x1, y1 in vcuts:
        key = (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3))
        rev = (key[2], key[3], key[0], key[1])
        assert key in draws or rev in draws, f"V-cut {key} is not in the outline Gerber"
        for d in g.draws:
            if {(round(d.x0, 3), round(-d.y0, 3)), (round(d.x1, 3), round(-d.y1, 3))} == {key[:2], key[2:]}:
                vcut_apertures.add(d.aperture)

    lines = [d for d in g.draws if d.aperture in vcut_apertures and (d.horizontal or d.vertical) and d.length > 30]
    assert len(lines) == len(vcuts)
    # The panel outline is drawn with the thinnest aperture (KiKit edge width), the V-cut
    # lines and the V-CUT texts with wider ones.
    others = [d for d in g.draws if d not in lines]
    thinnest = min(_diameter(g, d.aperture) for d in others)
    profile = [d for d in others if _diameter(g, d.aperture) == thinnest]
    texts = [d for d in others if _diameter(g, d.aperture) > thinnest]
    px0, py0, px1, py1 = g.bbox(profile)
    assert round(px1 - px0, 1) == 80.0 and round(py1 - py0, 1) == 78.0
    for d in lines:
        if d.horizontal:
            assert min(d.x0, d.x1) <= px0 + 1e-3 and max(d.x0, d.x1) >= px1 - 1e-3, "V-cut does not cross the panel"
            assert py0 < d.y0 < py1
        else:
            assert min(d.y0, d.y1) <= py0 + 1e-3 and max(d.y0, d.y1) >= py1 - 1e-3, "V-cut does not cross the panel"
            assert px0 < d.x0 < px1

    # "V-CUT" labels: KiCad plots text as strokes. Each V-cut has a label after its end,
    # outside the panel outline.
    for d in lines:
        if d.horizontal:
            near = [s for s in texts if min(s.x0, s.x1) > px1 and abs(s.y0 - d.y0) < 12]
        else:
            near = [s for s in texts if min(s.y0, s.y1) > py1 and abs(s.x0 - d.x0) < 12]
        assert len(near) >= 10, "no V-CUT text next to a V-cut line"


def _diameter(g, aperture: str) -> float:
    definition = g.apertures[aperture]
    return float(definition.split(",", 1)[1].split("X")[0]) if definition.startswith("C,") else 0.0


def test_vcut_text_in_panel_pcb(built):
    pcb_text = (built["out"] / f"panel/{BOARD}_panel.kicad_pcb").read_text(encoding="utf-8")
    texts = re.findall(r'\(gr_text "(V-CUT[^"]*)"(.*?)\(layer "([^"]+)"', pcb_text, re.S)
    assert texts
    assert all(layer == "Cmts.User" for _, _, layer in texts)


def test_board_outline_has_no_vcuts(built):
    g = _outline(built["out"] / f"fab/{BOARD}_gerbers.zip")
    x0, y0, x1, y1 = g.bbox()
    assert abs(x1 - x0 - 40.0) < 0.01 and abs(y1 - y0 - 32.0) < 0.01
    assert len({d.aperture for d in g.draws}) == 1


def _origin(pcb):
    import pcbnew
    from kicad_fab.board import outline_box
    box = outline_box(pcbnew.LoadBoard(str(pcb)))
    return pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetBottom())


@pytest.mark.parametrize("zip_name", [f"fab/{BOARD}_gerbers.zip", f"panel/{BOARD}_panel_gerbers.zip"])
def test_origin_lower_left(built, zip_name):
    """Gerbers and positions use the lower left outline corner as origin."""
    g = _outline(built["out"] / zip_name)
    # Only the outline itself (thinnest aperture), not V-cut lines and texts.
    thinnest = min(_diameter(g, d.aperture) for d in g.draws)
    edge = [d for d in g.draws if _diameter(g, d.aperture) == thinnest]
    x0, y0, _, _ = g.bbox(edge)
    assert abs(x0) < 0.05 and abs(y0) < 0.05, (x0, y0)
