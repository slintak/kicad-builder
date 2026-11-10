"""KiKit panel: make the panel, then check it against the JLCPCB rules for panels."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import board as pcb
from .config import Project
from .findings import Findings
from .kicad import run
from .log import warn

ORIG_REF = "{orig}"

# JLCPCB rules (2026-11): panel for SMT assembly ("Panel by Customer"), V-cut and mouse bites.
PANEL_MIN = 70.0
PANEL_MAX = 250.0
RAIL_MIN = 5.0
FIDUCIAL_EDGE_MIN = 3.85
FIDUCIAL_COPPER = 1.0
FIDUCIAL_OPENING = 2.0
TOOLING_SIZES = (1.152, 2.0)
VCUT_MAX_PER_AXIS = 25
VCUT_SPACING_MIN = 2.0
VCUT_COPPER_MIN = 0.4
VCUT_COPPER_WARN = 1.0
THICKNESS_MIN = 0.6
MOUSEBITE_DRILL = (0.5, 0.8)
MOUSEBITE_GAP = (0.2, 0.3)
ROUTED_GAP_MIN = 2.0
COPPER_EDGE_MIN = 0.2
TOL = 1e-3

UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "mil": 0.0254, "in": 25.4, "inch": 25.4}


def length(value) -> float:
    """KiKit length ('5mm', '0.2in', 3) in mm."""
    if isinstance(value, (int, float)):
        return float(value)
    match = re.match(r"^\s*(-?[\d.]+)\s*([a-z]*)\s*$", str(value))
    if not match:
        raise ValueError(f"not a length: {value!r}")
    return float(match.group(1)) * UNITS.get(match.group(2) or "mm", 1.0)


def panel_paths(project: Project) -> dict[str, Path]:
    d = project.out / "panel"
    return {
        "dir": d,
        "pcb": d / f"{project.name}_panel.kicad_pcb",
        "override": d / ".kicad-fab-override.json",
        "preset": d / ".kicad-fab-preset.json",
    }


def panelize(project: Project) -> dict[str, Path]:
    """Run KiKit with the panel preset of the project. The builder forces renameref = '{orig}'.

    With '{orig}' the silkscreen of every board copy shows the original references. The
    unique designators for the assembly files are made later, only in the CSV files.
    """
    paths = panel_paths(project)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    user = json.loads(project.panel_file.read_text(encoding="utf-8"))
    override: dict = {"layout": {"renameref": ORIG_REF}}
    renameref = (user.get("layout") or {}).get("renameref")
    if renameref not in (None, ORIG_REF):
        warn(f"{project.panel_file.name}: layout.renameref '{renameref}' is replaced by '{ORIG_REF}' "
             f"(the silkscreen keeps the original references)")
    cuts = user.get("cuts") or {}
    if cuts.get("type") == "vcuts":
        layer = pcb.legacy_layer_name(project.vcut_layer)
        user_layer = cuts.get("layer")
        if user_layer and pcb.legacy_layer_name(user_layer) != layer:
            warn(f"{project.panel_file.name}: cuts.layer '{user_layer}' is replaced by '{layer}' "
                 f"(panel.vcut_layer in kicad-fab.toml)")
        override["cuts"] = {"layer": layer}
    paths["override"].write_text(json.dumps(override, indent=2) + "\n", encoding="utf-8")
    paths["pcb"].unlink(missing_ok=True)
    run(["kikit", "panelize", "-p", project.panel_file, "-p", paths["override"], "--dump", paths["preset"],
         project.pcb, paths["pcb"]], cwd=project.dir, retries=2)
    if not paths["pcb"].is_file():
        raise RuntimeError("KiKit did not write the panel")
    for lock in paths["dir"].glob("~*.lck"):
        lock.unlink(missing_ok=True)
    keep_frame_fill(paths["pcb"], project.pcb)
    return paths


def keep_frame_fill(panel_pcb: Path, source_pcb: Path) -> int:
    """Fill the frame copper (KiKit `copperfill`) on all frame parts.

    V-cuts and routed gaps cut a rail into several parts. A KiKit frame zone has no
    net, and KiCad 10 removes all islands of such a zone but one, so only one part of
    each rail got copper. Set the island removal of the frame zones to "never" and
    fill again. Frame zones are the zones without a net that are larger than the board.
    Returns the number of changed zones.
    """
    p = pcb.pcbnew()
    source = pcb.load(source_pcb)
    sbox = pcb.outline_box(source)
    sw, sh = sbox.GetWidth(), sbox.GetHeight()
    board = p.LoadBoard(str(panel_pcb))
    changed = []
    for zone in board.Zones():
        if zone.GetIsRuleArea() or zone.GetNetCode() != 0:
            continue
        box = zone.GetBoundingBox()
        if box.GetWidth() <= sw + p.FromMM(1) and box.GetHeight() <= sh + p.FromMM(1):
            continue
        if zone.GetIslandRemovalMode() != p.ISLAND_REMOVAL_MODE_NEVER:
            zone.SetIslandRemovalMode(p.ISLAND_REMOVAL_MODE_NEVER)
            changed.append(zone)
    if changed:
        p.ZONE_FILLER(board).Fill(board.Zones())
        p.SaveBoard(str(panel_pcb), board)
    return len(changed)


# ---------------------------------------------------------------- geometry


def _poly_set_to_shapely(poly_set):
    from shapely.geometry import Polygon  # noqa: PLC0415

    out = []
    for i in range(poly_set.OutlineCount()):
        outline = poly_set.COutline(i)
        pts = [(pcb.mm(outline.CPoint(j).x), pcb.mm(outline.CPoint(j).y)) for j in range(outline.PointCount())]
        if len(pts) >= 3:
            poly = Polygon(pts)
            out.append(poly if poly.is_valid else poly.buffer(0))
    return out


def copper_geometry(board) -> list:
    """Shapely polygons of all copper (outline only, in mm)."""
    p = pcb.pcbnew()
    layers = [layer for layer in board.GetEnabledLayers().CuStack()]
    max_error = p.FromMM(0.005)
    geoms = []

    def add(item, layer):
        buf = p.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(buf, layer, 0, max_error, p.ERROR_INSIDE)
        geoms.extend(_poly_set_to_shapely(buf))

    for track in board.GetTracks():
        for layer in layers:
            if track.IsOnLayer(layer):
                add(track, layer)
                break
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetAttribute() == p.PAD_ATTRIB_NPTH:
                continue
            for layer in layers:
                if pad.IsOnLayer(layer):
                    add(pad, layer)
        for item in fp.GraphicalItems():
            if item.IsOnCopperLayer():
                add(item, item.GetLayer())
    for item in board.GetDrawings():
        if item.IsOnCopperLayer():
            add(item, item.GetLayer())
    for zone in board.Zones():
        if zone.GetIsRuleArea():
            continue
        for layer in layers:
            if zone.IsOnLayer(layer) and zone.HasFilledPolysForLayer(layer):
                geoms.extend(_poly_set_to_shapely(zone.GetFilledPolysList(layer)))
    return [g for g in geoms if not g.is_empty]


def board_outline(board):
    """Shapely geometry of the board outline (Edge.Cuts)."""
    from shapely.ops import unary_union  # noqa: PLC0415

    p = pcb.pcbnew()
    poly_set = p.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(poly_set, False)
    polys = []
    for i in range(poly_set.OutlineCount()):
        from shapely.geometry import Polygon  # noqa: PLC0415

        outline = poly_set.COutline(i)
        shell = [(pcb.mm(outline.CPoint(j).x), pcb.mm(outline.CPoint(j).y)) for j in range(outline.PointCount())]
        holes = []
        for h in range(poly_set.HoleCount(i)):
            hole = poly_set.CHole(i, h)
            holes.append([(pcb.mm(hole.CPoint(j).x), pcb.mm(hole.CPoint(j).y)) for j in range(hole.PointCount())])
        polys.append(Polygon(shell, holes))
    return unary_union(polys)


def _min_distance(geoms: list, target) -> float:
    if not geoms:
        return float("inf")
    from shapely import STRtree  # noqa: PLC0415

    tree = STRtree(geoms)
    _, distance = tree.query_nearest(target, return_distance=True, all_matches=False)
    return float(min(distance)) if len(distance) else float("inf")


# ---------------------------------------------------------------- checks


class Rules:
    """Collects rule results for panel-check.json."""

    def __init__(self, ignore: set[str]):
        self.rules: list[dict] = []
        self.ignore = ignore
        self.findings = Findings()

    def add(self, rule: str, status: str, message: str, value=None, limit=None) -> None:
        if status == "error" and rule in self.ignore:
            status = "warning"
            message += " (ignored by panel.ignore)"
        entry = {"rule": rule, "status": status, "message": message}
        if value is not None:
            entry["value"] = round(value, 4) if isinstance(value, float) else value
        if limit is not None:
            entry["limit"] = limit
        self.rules.append(entry)
        if status == "error":
            self.findings.error("panel", rule, message)
        elif status == "warning":
            self.findings.warning("panel", rule, message)

    def ok(self, rule: str, message: str, value=None, limit=None) -> None:
        self.add(rule, "ok", message, value, limit)


def _side_margins(panel_box, copies) -> dict[str, float]:
    x0 = min(c.bbox[0] for c in copies)
    y0 = min(c.bbox[1] for c in copies)
    x1 = max(c.bbox[2] for c in copies)
    y1 = max(c.bbox[3] for c in copies)
    return {"left": x0 - panel_box[0], "top": y0 - panel_box[1], "right": panel_box[2] - x1,
            "bottom": panel_box[3] - y1}


def check_panel(project: Project, source, panel, preset: dict, copies: list, assembly_sides: set[str]) -> dict:
    p = pcb.pcbnew()
    rules = Rules(project.panel_ignore)
    box = pcb.board_bbox(panel)
    width, height = box[2] - box[0], box[3] - box[1]
    measurements: dict = {"panel_size_mm": [round(width, 3), round(height, 3)], "copies": len(copies)}

    # Panel size for SMT assembly.
    if PANEL_MIN - TOL <= width <= PANEL_MAX + TOL and PANEL_MIN - TOL <= height <= PANEL_MAX + TOL:
        rules.ok("panel_size", f"panel {width:.2f} x {height:.2f} mm", [width, height], [PANEL_MIN, PANEL_MAX])
    else:
        rules.add("panel_size", "error",
                  f"panel {width:.2f} x {height:.2f} mm; JLCPCB assembly needs {PANEL_MIN:g} to {PANEL_MAX:g} mm "
                  f"on each side", [width, height], [PANEL_MIN, PANEL_MAX])

    # Board thickness.
    thickness = pcb.mm(panel.GetDesignSettings().GetBoardThickness())
    measurements["thickness_mm"] = thickness
    cut_type = (preset.get("cuts") or {}).get("type", "none")
    if cut_type == "vcuts" and thickness < THICKNESS_MIN - TOL:
        rules.add("thickness", "error", f"board thickness {thickness:.2f} mm; V-cut needs >= {THICKNESS_MIN} mm",
                  thickness, THICKNESS_MIN)
    else:
        rules.ok("thickness", f"board thickness {thickness:.2f} mm", thickness, THICKNESS_MIN)

    # Rails.
    framing = preset.get("framing") or {}
    ftype = framing.get("type", "none")
    if copies:
        margins = _side_margins(box, copies)
        measurements["edge_margins_mm"] = {k: round(v, 3) for k, v in margins.items()}
    else:
        margins = {}
    rail_sides = {"railstb": ("top", "bottom"), "railslr": ("left", "right"),
                  "frame": ("top", "bottom", "left", "right"), "tightframe": ("top", "bottom", "left", "right")}
    if ftype not in rail_sides:
        rules.add("rails", "error", f"no rails (framing type '{ftype}'); JLCPCB assembly needs rails >= "
                  f"{RAIL_MIN:g} mm", None, RAIL_MIN)
    else:
        hspace = length(framing.get("hspace", 0))
        vspace = length(framing.get("vspace", 0))
        widths = {}
        for side in rail_sides[ftype]:
            space = vspace if side in ("top", "bottom") else hspace
            widths[side] = margins.get(side, length(framing.get("width", 0)) + space) - space
        narrow = {k: v for k, v in widths.items() if v < RAIL_MIN - TOL}
        measurements["rail_width_mm"] = {k: round(v, 3) for k, v in widths.items()}
        if narrow:
            rules.add("rails", "error", "rail too narrow: " + ", ".join(f"{k} {v:.2f} mm" for k, v in narrow.items())
                      + f"; JLCPCB needs >= {RAIL_MIN:g} mm", min(narrow.values()), RAIL_MIN)
        else:
            rules.ok("rails", "rails " + ", ".join(f"{k} {v:.2f} mm" for k, v in widths.items()),
                     min(widths.values()), RAIL_MIN)
        for name, gap in (("hspace", hspace), ("vspace", vspace)):
            if TOL < gap < ROUTED_GAP_MIN - TOL:
                rules.add("routed_gap", "error", f"framing {name} {gap:.2f} mm: a routed gap must be >= "
                          f"{ROUTED_GAP_MIN:g} mm (or 0)", gap, ROUTED_GAP_MIN)

    # Spaces between boards.
    layout = preset.get("layout") or {}
    for name in ("hspace", "vspace"):
        gap = length(layout.get(name, 0))
        if TOL < gap < ROUTED_GAP_MIN - TOL:
            rules.add("routed_gap", "error", f"layout {name} {gap:.2f} mm: a routed gap must be >= "
                      f"{ROUTED_GAP_MIN:g} mm (or 0)", gap, ROUTED_GAP_MIN)
        else:
            rules.ok("routed_gap", f"layout {name} {gap:.2f} mm", gap, ROUTED_GAP_MIN)

    # Fiducials and tooling holes: footprints that are not in the source board.
    source_refs = {fp.GetReference() for fp in source.GetFootprints()}
    _, mapping = pcb.find_copies(source, panel)
    copy_kiids = set(mapping)
    fiducials = {"Top": [], "Bottom": []}
    tooling = []
    for fp in panel.GetFootprints():
        if fp.m_Uuid.AsString() in copy_kiids or fp.GetReference() in source_refs:
            continue
        pads = list(fp.Pads())
        if not pads:
            continue
        pos = fp.GetPosition()
        x, y = pcb.mm(pos.x), pcb.mm(pos.y)
        edge = min(x - box[0], box[2] - x, y - box[1], box[3] - y)
        if all(pad.GetAttribute() == p.PAD_ATTRIB_NPTH for pad in pads):
            tooling.append({"x": x, "y": y, "edge": edge, "drill": pcb.mm(pads[0].GetDrillSizeX())})
        elif "fiducial" in str(fp.GetFPID().GetUniStringLibItemName()).lower() or (
                len(pads) == 1 and pads[0].GetAttribute() == p.PAD_ATTRIB_SMD):
            pad = pads[0]
            mask_layer = p.B_Mask if fp.GetLayer() == p.B_Cu else p.F_Mask
            copper = pcb.mm(pad.GetSizeX())
            try:
                opening = copper + 2 * pcb.mm(pad.GetSolderMaskExpansion(mask_layer))
            except TypeError:
                opening = length((preset.get("fiducials") or {}).get("opening", copper))
            fiducials[pcb.footprint_side(fp)].append({"x": x, "y": y, "edge": edge, "copper": copper,
                                                      "opening": opening})
    measurements["fiducials"] = {k: len(v) for k, v in fiducials.items()}
    measurements["tooling_holes"] = len(tooling)
    for side in ("Top", "Bottom"):
        fids = fiducials[side]
        needed = side in assembly_sides
        if not fids and not needed:
            continue
        if not 3 <= len(fids) <= 4:
            rules.add("fiducials", "error", f"{side}: {len(fids)} fiducials; JLCPCB needs 3 or 4 on each "
                      f"assembled side", len(fids), [3, 4])
        else:
            rules.ok("fiducials", f"{side}: {len(fids)} fiducials", len(fids), [3, 4])
        for f in fids:
            if f["edge"] < FIDUCIAL_EDGE_MIN - TOL:
                rules.add("fiducial_edge", "error", f"{side} fiducial at ({f['x']:.2f}, {f['y']:.2f}): centre "
                          f"{f['edge']:.2f} mm from the panel edge; JLCPCB needs >= {FIDUCIAL_EDGE_MIN} mm",
                          f["edge"], FIDUCIAL_EDGE_MIN)
            if abs(f["copper"] - FIDUCIAL_COPPER) > 0.01 or f["opening"] < FIDUCIAL_OPENING - 0.01:
                rules.add("fiducial_size", "error", f"{side} fiducial: copper {f['copper']:.2f} mm, mask opening "
                          f"{f['opening']:.2f} mm; JLCPCB needs {FIDUCIAL_COPPER:g} mm copper and "
                          f"{FIDUCIAL_OPENING:g} mm opening", f["copper"], FIDUCIAL_COPPER)
        if fids:
            nearest = min(f["edge"] for f in fids)
            rules.ok("fiducial_edge", f"{side}: fiducial centres >= {nearest:.2f} mm from the edge", nearest,
                     FIDUCIAL_EDGE_MIN)
    if not tooling:
        rules.add("tooling", "warning", "no tooling holes; JLCPCB uses 3 NPTH tooling holes on the rails", 0, 3)
    else:
        sizes = sorted({round(t["drill"], 3) for t in tooling})
        bad = [s for s in sizes if not any(abs(s - ok) < 0.05 for ok in TOOLING_SIZES)]
        if len(tooling) != 3:
            rules.add("tooling", "warning", f"{len(tooling)} tooling holes; JLCPCB uses 3", len(tooling), 3)
        if bad:
            rules.add("tooling", "warning", f"tooling hole size {', '.join(f'{s:g}' for s in bad)} mm; JLCPCB "
                      f"uses 1.152 mm (KiKit :jlcTooling) or 2 mm", bad[0], list(TOOLING_SIZES))
        if len(tooling) == 3 and not bad:
            rules.ok("tooling", f"3 tooling holes, {', '.join(f'{s:g}' for s in sizes)} mm", 3, 3)

    # Mouse bites.
    cuts = preset.get("cuts") or {}
    if cut_type == "mousebites":
        drill = length(cuts.get("drill", 0.5))
        gap = length(cuts.get("spacing", 0.8)) - drill
        if not MOUSEBITE_DRILL[0] - TOL <= drill <= MOUSEBITE_DRILL[1] + TOL:
            rules.add("mousebites", "error", f"mouse bite holes {drill:.2f} mm; JLCPCB needs "
                      f"{MOUSEBITE_DRILL[0]}-{MOUSEBITE_DRILL[1]} mm", drill, list(MOUSEBITE_DRILL))
        elif not MOUSEBITE_GAP[0] - TOL <= gap <= MOUSEBITE_GAP[1] + TOL:
            rules.add("mousebites", "warning", f"mouse bite space between holes {gap:.2f} mm; JLCPCB "
                      f"recommends {MOUSEBITE_GAP[0]}-{MOUSEBITE_GAP[1]} mm", gap, list(MOUSEBITE_GAP))
        else:
            rules.ok("mousebites", f"mouse bites {drill:.2f} mm, space {gap:.2f} mm", drill)

    copper = copper_geometry(panel)
    measurements["copper_items"] = len(copper)

    # V-cuts.
    vcut_lines = []
    if cut_type == "vcuts":
        layer = pcb.layer_id(panel, project.vcut_layer)
        lines, texts = pcb.vcut_items(panel, layer, copies)
        vcut_lines = [pcb.segment_mm(item) for item in lines]
        measurements["vcut_texts"] = len(texts)
    if vcut_lines:
        _check_vcuts(rules, vcut_lines, box, copper, measurements)

    # Copper to the routed (milled) edge. The panel outline has no lines at the V-cuts,
    # so its boundary is the milled edge only.
    boundary = board_outline(panel).boundary
    distance = _min_distance(copper, boundary) if not boundary.is_empty else float("inf")
    measurements["copper_to_edge_mm"] = None if distance == float("inf") else round(distance, 4)
    if distance < COPPER_EDGE_MIN - TOL:
        rules.add("copper_edge", "error", f"copper {distance:.3f} mm from a routed edge; JLCPCB needs >= "
                  f"{COPPER_EDGE_MIN} mm", distance, COPPER_EDGE_MIN)
    else:
        rules.ok("copper_edge", f"copper >= {distance:.3f} mm from routed edges", distance, COPPER_EDGE_MIN)

    return {
        "passed": not rules.findings.errors,
        "errors": len(rules.findings.errors),
        "warnings": len(rules.findings.warnings),
        "rules": rules.rules,
        "measurements": measurements,
        "findings": rules.findings,
    }


def _check_vcuts(rules: Rules, lines: list, box, copper: list, measurements: dict) -> None:
    from shapely.geometry import LineString  # noqa: PLC0415

    horizontal, vertical = [], []
    for x0, y0, x1, y1 in lines:
        if abs(y0 - y1) < TOL:
            horizontal.append((y0, min(x0, x1), max(x0, x1)))
        elif abs(x0 - x1) < TOL:
            vertical.append((x0, min(y0, y1), max(y0, y1)))
        else:
            rules.add("vcut_straight", "error", f"V-cut from ({x0:.2f}, {y0:.2f}) to ({x1:.2f}, {y1:.2f}) is not "
                      f"horizontal or vertical")
    measurements["vcuts"] = {"horizontal": sorted(round(h[0] - box[1], 3) for h in horizontal),
                             "vertical": sorted(round(v[0] - box[0], 3) for v in vertical)}
    for name, cuts, lo, hi in (("horizontal", horizontal, box[0], box[2]), ("vertical", vertical, box[1], box[3])):
        for pos, a, b in cuts:
            if a > lo + TOL or b < hi - TOL:
                rules.add("vcut_full", "error", f"{name} V-cut at {pos:.2f} mm does not cross the whole panel "
                          f"(jump V-cut)")
        if len(cuts) > VCUT_MAX_PER_AXIS:
            rules.add("vcut_count", "error", f"{len(cuts)} {name} V-cuts; JLCPCB accepts {VCUT_MAX_PER_AXIS}",
                      len(cuts), VCUT_MAX_PER_AXIS)
        positions = sorted({round(c[0], 4) for c in cuts})
        for a, b in zip(positions, positions[1:]):
            if b - a < VCUT_SPACING_MIN - TOL:
                rules.add("vcut_spacing", "error", f"{name} V-cuts at {a:.2f} and {b:.2f} mm are {b - a:.2f} mm "
                          f"apart; JLCPCB needs >= {VCUT_SPACING_MIN:g} mm", b - a, VCUT_SPACING_MIN)
    rules.ok("vcut_count", f"{len(horizontal)} horizontal, {len(vertical)} vertical V-cuts",
             max(len(horizontal), len(vertical)), VCUT_MAX_PER_AXIS)

    nearest = float("inf")
    for pos, _, _ in horizontal:
        d = _min_distance(copper, LineString([(box[0], pos), (box[2], pos)]))
        nearest = min(nearest, d)
        _copper_rule(rules, "horizontal", pos - box[1], d)
    for pos, _, _ in vertical:
        d = _min_distance(copper, LineString([(pos, box[1]), (pos, box[3])]))
        nearest = min(nearest, d)
        _copper_rule(rules, "vertical", pos - box[0], d)
    measurements["vcut_copper_mm"] = None if nearest == float("inf") else round(nearest, 4)
    if nearest >= VCUT_COPPER_WARN - TOL:
        rules.ok("vcut_copper", f"copper >= {nearest:.3f} mm from the V-cut lines", nearest, VCUT_COPPER_MIN)


def _copper_rule(rules: Rules, name: str, pos: float, distance: float) -> None:
    if distance < VCUT_COPPER_MIN - TOL:
        rules.add("vcut_copper", "error", f"copper {distance:.3f} mm from the {name} V-cut at {pos:.2f} mm; "
                  f"JLCPCB needs >= {VCUT_COPPER_MIN} mm", distance, VCUT_COPPER_MIN)
    elif distance < VCUT_COPPER_WARN - TOL:
        rules.add("vcut_copper", "warning", f"copper {distance:.3f} mm from the {name} V-cut at {pos:.2f} mm; "
                  f"JLCPCB recommends >= {VCUT_COPPER_WARN} mm", distance, VCUT_COPPER_WARN)
