"""README.md in the build directory: what the files are and how to order the board.

The values come from the board (outline, stackup, copper, holes), the panel (KiKit
preset and panel check), the BOM and the [order] table of kicad-fab.toml. A value that
the design does not give is written as "not set", so the person who orders sees it.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from . import board as pcb

NOT_SET = "not set (choose it in the order form)"
OZ_MM = 0.035  # 1 oz copper


def _num(text: str) -> float:
    return float(re.sub(r"[^0-9.\-]", "", str(text)) or 0)


def stackup(pcb_path: Path) -> dict:
    """Stackup of the board, read from the .kicad_pcb file (the Python API does not give it)."""
    text = pcb_path.read_text(encoding="utf-8")
    start = text.find("(stackup")
    out = {"layers": [], "finish": "", "impedance": False}
    if start < 0:
        return out
    depth, end = 0, start
    for end in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[end], 0)
        if depth == 0:
            break
    block = text[start:end + 1]
    for m in re.finditer(r'\(layer "([^"]+)"(.*?)\n\t\t\t\)', block, re.S):
        body = m.group(2)
        item = {"name": m.group(1)}
        for key in ("type", "material", "color"):
            k = re.search(rf'\({key} "([^"]*)"\)', body)
            if k:
                item[key] = k.group(1)
        for key in ("thickness", "epsilon_r", "loss_tangent"):
            k = re.search(rf"\({key} ([0-9.]+)", body)
            if k:
                item[key] = float(k.group(1))
        out["layers"].append(item)
    finish = re.search(r'\(copper_finish "([^"]*)"\)', block)
    out["finish"] = finish.group(1) if finish and finish.group(1) != "None" else ""
    out["impedance"] = bool(re.search(r"\(dielectric_constraints yes\)", block))
    return out


def board_facts(board) -> dict:
    p = pcb.pcbnew()
    box = pcb.outline_box(board)
    tracks = [t for t in board.GetTracks() if t.Type() != p.PCB_VIA_T]
    vias = [t for t in board.GetTracks() if t.Type() == p.PCB_VIA_T]
    pth = [pad for fp in board.GetFootprints() for pad in fp.Pads()
           if pad.GetAttribute() == p.PAD_ATTRIB_PTH and pad.GetDrillSize().x > 0]
    npth = [pad for fp in board.GetFootprints() for pad in fp.Pads()
            if pad.GetAttribute() == p.PAD_ATTRIB_NPTH and pad.GetDrillSize().x > 0]
    mm = p.ToMM
    return {
        "size": (mm(box.GetWidth()), mm(box.GetHeight())),
        "layers": board.GetCopperLayerCount(),
        "thickness": mm(board.GetDesignSettings().GetBoardThickness()),
        "min_track": min((mm(t.GetWidth()) for t in tracks), default=0),
        "min_via_drill": min((mm(v.GetDrillValue()) for v in vias), default=0),
        "min_via_diameter": min((mm(v.GetWidth(p.F_Cu)) for v in vias), default=0),
        "vias": len(vias),
        "min_pth": min((mm(min(pad.GetDrillSize().x, pad.GetDrillSize().y)) for pad in pth), default=0),
        "pth": len(pth),
        "npth": len(npth),
    }


def _oz(thickness: float | None) -> str:
    if not thickness:
        return "?"
    # KiCad writes 0.5 oz as 0.0152 or 0.0175 mm: give the nearest standard copper weight.
    oz = min((0.5, 1, 1.5, 2, 2.5, 3), key=lambda w: abs(w - thickness / OZ_MM))
    return f"{oz:g} oz"


def _stackup_table(st: dict) -> list[str]:
    rows = ["| Layer | Type | Thickness | Material | Er |", "|---|---|---|---|---|"]
    for item in st["layers"]:
        if "thickness" not in item:
            continue
        rows.append(f"| {item['name']} | {item.get('type', '')} | {item['thickness']:.4f} mm | "
                    f"{item.get('material', '')} | {item.get('epsilon_r', '')} |")
    return rows


def write_readme(build, path: Path) -> Path:
    """build: the pipeline Build object (after all steps)."""
    project = build.project
    order = project.cfg.get("order", {})
    board = build.board()
    facts = board_facts(board)
    st = stackup(project.pcb)
    copper = [i for i in st["layers"] if i.get("type") == "copper"]
    outer = _oz(copper[0].get("thickness")) if copper else "?"
    inner = _oz(copper[1].get("thickness")) if len(copper) > 2 else "-"
    stack_total = sum(i.get("thickness", 0) for i in st["layers"]
                      if i.get("type") in ("copper", "core", "prepreg"))
    thickness = order.get("thickness") or f"{stack_total:.2f} mm (copper and dielectric layers of the stackup)"
    finish = order.get("finish") or st["finish"] or NOT_SET
    mask = order.get("mask_color") or next((i.get("color") for i in st["layers"]
                                            if i["name"] == "F.Mask" and i.get("color")), "") or NOT_SET
    silk = order.get("silk_color") or NOT_SET
    impedance = "yes" if st["impedance"] else "no"
    stack_name = order.get("stackup") or NOT_SET

    assembly = build.assembly()
    lines_jlc = len({p.group_key for p in assembly})
    sides = sorted({pcb.footprint_sides(board).get(p.ref, "top") for p in assembly})
    excluded = sorted(project.assembly_exclude, key=lambda r: (re.sub(r"\d", "", r), _num(r) if re.search(r"\d", r) else 0))
    panel = getattr(build, "panel_result", None)
    panels = 0
    name, version = build.name, project.version()
    wip = build.do_not_order_text()
    reasons = list(project.wip_reasons) + (
        [f"{len(build.findings.errors)} check error(s), see manifest.json"] if build.findings.errors else [])

    out: list[str] = [f"# {name} {version}: manufacturing files", ""]
    if wip:
        out += ["> **DO NOT ORDER.** " + "; ".join(reasons), ""]
    out += [f"Made by kicad-fab {build.versions.get('kicad_fab', '')} with KiCad {build.versions.get('kicad', '')} "
            f"and KiKit {build.versions.get('kikit', '')}. See `manifest.json` for all versions and checks.", ""]

    out += ["## Board", "",
            "| Item | Value |", "|---|---|",
            f"| Size | {facts['size'][0]:.2f} x {facts['size'][1]:.2f} mm |",
            f"| Copper layers | {facts['layers']} |",
            f"| Stackup | {stack_name} |",
            f"| Thickness | {thickness} |",
            f"| Outer / inner copper | {outer} / {inner} |",
            f"| Impedance control | {impedance} (dielectric constraints in the board stackup) |",
            f"| Surface finish | {finish} |",
            f"| Solder mask / silkscreen | {mask} / {silk} |",
            f"| Minimum track | {facts['min_track']:.3f} mm |",
            f"| Minimum via | hole {facts['min_via_drill']:.2f} mm, diameter {facts['min_via_diameter']:.2f} mm "
            f"({facts['vias']} vias) |",
            f"| Plated holes (pads) | {facts['pth']}, minimum {facts['min_pth']:.2f} mm |",
            f"| Non-plated holes (pads) | {facts['npth']} |", ""]
    out += ["Stackup in the board file:", ""] + _stackup_table(st) + [""]

    if panel:
        m = panel["measurements"]
        preset = build.panel_preset or {}
        layout = preset.get("layout", {})
        cuts = preset.get("cuts", {})
        rows_, cols_ = int(layout.get("rows", 0) or 0), int(layout.get("cols", 0) or 0)
        copies = int(m.get("copies") or (rows_ * cols_))
        panels = math.ceil(project.boards / copies) if copies else 0
        rails = m.get("rail_width_mm", {})
        out += ["## Panel", "",
                "| Item | Value |", "|---|---|",
                f"| Panel size | {m['panel_size_mm'][0]:.2f} x {m['panel_size_mm'][1]:.2f} mm |",
                f"| Boards | {rows_} rows x {cols_} columns = {copies} boards in one panel |",
                f"| Separation | {cuts.get('type', '?')}"
                + (f", {len(m.get('vcuts', {}).get('horizontal', []))} horizontal and "
                   f"{len(m.get('vcuts', {}).get('vertical', []))} vertical V-cut lines" if cuts.get("type") == "vcuts" else "")
                + " |",
                f"| Rails | " + ", ".join(f"{k} {v:.2f} mm" for k, v in rails.items()) + " |",
                f"| Fiducials | " + ", ".join(f"{k} {v}" for k, v in m.get("fiducials", {}).items())
                + f", centre {preset.get('fiducials', {}).get('voffset', '?')} from the panel edge |",
                f"| Tooling holes | {m.get('tooling_holes', '?')} x {preset.get('tooling', {}).get('size', '?')} NPTH |",
                f"| Panel check | {'passed' if panel['passed'] else 'FAILED'}: {panel['errors']} errors, "
                f"{panel['warnings']} warnings (`reports/panel-check.json`) |",
                f"| Order quantity | {project.boards} boards = {panels} panels |", ""]
        if cuts.get("type") == "vcuts":
            out += ["The V-cut lines are in the outline Gerber (`*-Edge_Cuts.gm1`), with a `V-CUT` text at each line, "
                    "as JLCPCB requires. JLCPCB reads V-cuts, slots and milling only from the outline layer. "
                    "The online Gerber viewer of JLCPCB shows the V-cut lines as outline lines; this is normal.", ""]

    out += ["## Order at JLCPCB", "", "### PCB", "",
            "| Order form | Value |", "|---|---|",
            "| Gerber file | `panel/" + f"{name}_panel_gerbers.zip`" + " |" if panel else
            f"| Gerber file | `fab/{name}_gerbers.zip` |",
            "| Base material | FR-4 |",
            f"| Layers | {facts['layers']} |"]
    if panel:
        m = panel["measurements"]
        out += [f"| Dimensions | {m['panel_size_mm'][0]:.2f} x {m['panel_size_mm'][1]:.2f} mm |",
                f"| PCB qty | {panels} (panels) |",
                "| Delivery format | Panel by Customer |",
                f"| Different designs | 1 |"]
    else:
        out += [f"| Dimensions | {facts['size'][0]:.2f} x {facts['size'][1]:.2f} mm |",
                f"| PCB qty | {project.boards} |", "| Delivery format | Single PCB |"]
    out += [f"| PCB thickness | {thickness} |",
            f"| Impedance control | {'yes, stackup ' + stack_name if st['impedance'] else 'no'} |",
            f"| Outer / inner copper weight | {outer} / {inner} |",
            f"| Via covering | {order.get('via_covering') or NOT_SET} |",
            f"| Min via hole / diameter | {facts['min_via_drill']:.2f} mm / {facts['min_via_diameter']:.2f} mm |",
            f"| Surface finish | {finish} |",
            f"| PCB color / silkscreen | {mask} / {silk} |",
            "| Edge rails, fiducials, tooling holes | already in the panel: do not add |" if panel else
            "| Edge rails | no |", ""]
    if panel and (preset := build.panel_preset or {}).get("cuts", {}).get("type") == "vcuts":
        out += ["Remark for the order: "
                "\"Panel by customer. V-cut lines are in the outline layer (GM1) and are marked with V-CUT text. "
                "All other lines in the outline layer are routed edges.\"", ""]

    if project.sides == "none":
        out += ["### Assembly", "",
                "No assembly (`assembly.sides = \"none\"`): order the bare PCB. All parts are soldered by hand.", ""]
    else:
        out += _assembly_section(project, name, panel, sides, lines_jlc, excluded)

    out += ["## Parts bought at Mouser", "",
            f"`bom/{name}_bom_mouser.csv` has the Mouser numbers for {project.boards} boards (with spares), the "
            "references in `Customer Part Number` and the drop-in alternates in the `Alt` columns. "
            "Upload it in the Mouser BOM tool and map the columns on the first upload. "
            f"`bom/{name}_bom_alternates.csv` lists each alternate; `bom/{name}_bom_other.csv` lists parts "
            "with neither a Mouser nor an LCSC number.", ""]

    def have(rel: str) -> bool:
        return build.path(rel).exists()

    out += ["## Files", "",
            "| Path | Content |", "|---|---|"]
    if have(f"{name}_schematic.pdf"):
        out += [f"| `{name}_schematic.pdf` | schematic |"]
    out += [f"| `fab/{name}_gerbers.zip` | Gerbers and drill files of one board |",
            f"| `fab/{name}_pos_jlcpcb.csv` | positions of one board |",
            "| `bom/` | BOM files: JLCPCB, Mouser, alternates, other |"]
    if panel:
        out += [f"| `panel/{name}_panel_gerbers.zip` | Gerbers and drill files of the panel |",
                f"| `panel/{name}_panel_bom_jlcpcb.csv`, `..._pos_jlcpcb.csv` | assembly files of the panel |",
                f"| `panel/{name}_panel.kicad_pcb` | panel board file |"]
        if have(f"panel/{name}_panel.png"):
            out += [f"| `panel/{name}_panel.png` | panel render |"]
    if have(f"{name}.step.xz"):
        out += [f"| `{name}.step.xz` | 3D model |"]
    if have("render"):
        out += ["| `render/` | board renders |"]
    out += ["| `reports/` | ERC, DRC and panel check |",
            "| `manifest.json` | versions, checks and SHA-256 of all files |", ""]
    out += ["All coordinates in the Gerber, drill and CPL files start at the lower left corner of the "
            "board (or panel) outline.", ""]
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def _assembly_section(project, name: str, panel, sides: list, lines_jlc: int, excluded: list) -> list[str]:
    out = ["### Assembly", "",
           "| Order form | Value |", "|---|---|",
           "| PCBA type | Standard (Economic PCBA does not accept V-cut panels) |" if panel else
            "| PCBA type | Economic or Standard |",
            f"| Assembly side | {', '.join(sides) or 'top'} |",
            f"| PCBA qty | {project.boards} boards |",
            "| Tooling holes | added by customer |" if panel else "| Tooling holes | added by JLCPCB |",
            f"| BOM | `{'panel/' + name + '_panel_bom_jlcpcb.csv' if panel else 'fab/../bom/' + name + '_bom_jlcpcb.csv'}` "
            f"({lines_jlc} lines) |",
            f"| CPL | `{'panel/' + name + '_panel_pos_jlcpcb.csv' if panel else 'fab/' + name + '_pos_jlcpcb.csv'}` |"]
    if panel:
        out += ["| Panel data | \"Complete File, just proceed with my own files\": the BOM and CPL describe the "
                "whole panel, with unique designators (C1_1 ... C1_n). The silkscreen shows C1 on every board. |"
                if project.designators == "unique" else
                "| Panel data | the CPL repeats each designator once per board |"]
    out += [f"| Not assembled (hand soldered) | {', '.join(excluded) or 'none'} |", "",
            "Check the part placement and the rotation in the JLCPCB preview before you confirm the order.", ""]

    return out
