"""PCB work with the pcbnew module: positions, panel copies, Gerbers and drill files."""

from __future__ import annotations

import contextlib
import csv
import math
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .bom import Part, ref_key
from .log import warn

CPL_HEADER = ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]
SIDE_NAMES = {"T": "Top", "B": "Bottom"}
# KiCad 9 and later show new names for the user layers. Files and KiKit use the old names.
LAYER_ALIASES = {
    "User.Comments": "Cmts.User",
    "User.Drawings": "Dwgs.User",
    "User.Eco1": "Eco1.User",
    "User.Eco2": "Eco2.User",
}
VCUT_TEXT = "V-CUT"
_pcbnew = None


@contextlib.contextmanager
def _quiet():
    """pcbnew prints wx debug text on stderr (file descriptor 2). Hide it."""
    sys.stderr.flush()
    saved = os.dup(2)
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), 2)
            yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def pcbnew():
    """Import pcbnew when it is necessary (the import is slow)."""
    global _pcbnew
    if _pcbnew is None:
        with _quiet():
            import pcbnew as module  # noqa: PLC0415
        _pcbnew = module
    return _pcbnew


def kikit_fab():
    from kikit.fab import common  # noqa: PLC0415

    return common


def mm(value: int) -> float:
    return value / 1e6


def load(path: Path):
    """Load a board for the outputs. The aux origin is set (in memory only) to the lower
    left corner of the board outline, so Gerbers, drill files and positions share one
    origin and all positions are positive."""
    module = pcbnew()
    with _quiet():
        board = module.LoadBoard(str(path))
    box = outline_box(board)
    board.GetDesignSettings().SetAuxOrigin(module.VECTOR2I(box.GetLeft(), box.GetBottom()))
    return board


def outline_box(board):
    """Bounding box of the board outline geometry (without the line width)."""
    module = pcbnew()
    outline = module.SHAPE_POLY_SET()
    if board.GetBoardPolygonOutlines(outline, False) and outline.OutlineCount():
        return outline.BBox()
    return board.GetBoardEdgesBoundingBox()


def legacy_layer_name(name: str) -> str:
    return LAYER_ALIASES.get(name, name)


def layer_id(board, name: str) -> int:
    for candidate in (name, legacy_layer_name(name)):
        layer = board.GetLayerID(candidate)
        if layer >= 0:
            return layer
    std = {v: k for k, v in LAYER_ALIASES.items()}.get(name)
    if std:
        layer = board.GetLayerID(std)
        if layer >= 0:
            return layer
    raise ValueError(f"unknown layer '{name}'")


def footprint_side(fp) -> str:
    return "Bottom" if fp.GetLayer() == pcbnew().B_Cu else "Top"


def footprint_sides(board) -> dict[str, str]:
    return {fp.GetReference(): footprint_side(fp) for fp in board.GetFootprints()}


# ---------------------------------------------------------------- positions


def _compensation(part: Part | None) -> tuple[float, float, float]:
    if part is None or not part.correction:
        return (0.0, 0.0, 0.0)
    return tuple(kikit_fab().parseCompensation(part.correction))


def position(fp, board, part: Part | None) -> tuple[float, float, str, float]:
    """Mid X, Mid Y (mm, relative to the aux origin, Y up), side, rotation. KiKit logic for JLCPCB."""
    common = kikit_fab()
    offset = board.GetDesignSettings().GetAuxOrigin()
    comp = _compensation(part)
    x = common.defaultFootprintX(fp, offset, comp)
    y = common.defaultFootprintY(fp, offset, comp)
    rot = common.footprintOrientation(fp, comp, common.FootprintOrientationHandling.MirrorBottom)
    return x, y, SIDE_NAMES[common.layerToSide(fp.GetLayer())], rot


def _excluded_from_pos(fp) -> bool:
    return bool(fp.GetAttributes() & pcbnew().FP_EXCLUDE_FROM_POS_FILES)


def _cpl_row(name: str, x: float, y: float, side: str, rot: float) -> list[str]:
    return [name, f"{x:.3f}", f"{y:.3f}", side, f"{rot % 360:.2f}"]


def write_cpl(rows: list[list[str]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: ref_key(r[0]))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(CPL_HEADER)
        writer.writerows(rows)
    return path


def board_cpl(board, parts: list[Part]) -> list[list[str]]:
    by_ref = {p.ref: p for p in parts}
    rows = []
    seen = set()
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref not in by_ref or _excluded_from_pos(fp):
            continue
        if ref in seen:
            warn(f"footprint {ref} is on the board more than once")
            continue
        seen.add(ref)
        rows.append(_cpl_row(ref, *position(fp, board, by_ref[ref])))
    for ref in sorted(set(by_ref) - seen, key=ref_key):
        warn(f"{ref} is in the assembly BOM but has no footprint on the board")
    return rows


# ---------------------------------------------------------------- panel copies


@dataclass
class Copy:
    index: int                      # 1, 2, ... in rows from the top left
    angle: float                    # degrees, KiCad orientation change
    tx: float                       # translation, mm
    ty: float
    bbox: tuple[float, float, float, float]   # board outline in the panel, mm

    def transform(self, x: float, y: float) -> tuple[float, float]:
        return _rotate(x, y, self.angle, self.tx, self.ty)


def _rotate(x: float, y: float, angle: float, tx: float = 0.0, ty: float = 0.0) -> tuple[float, float]:
    """KiCad RotatePoint (Y down, positive angle is counter-clockwise on the screen), then translate."""
    a = math.radians(angle)
    s, c = math.sin(a), math.cos(a)
    return (x * c + y * s + tx, y * c - x * s + ty)


def board_bbox(board) -> tuple[float, float, float, float]:
    box = board.GetBoardEdgesBoundingBox()
    return (mm(box.GetLeft()), mm(box.GetTop()), mm(box.GetRight()), mm(box.GetBottom()))


def find_copies(source, panel) -> tuple[list[Copy], dict[str, tuple[str, int]]]:
    """Find the board copies in a panel without the references (all copies have the same references).

    Each panel footprint with a reference of the source board gives a rigid transform
    (rotation, translation) from its source footprint. Footprints with the same transform
    are one copy. Copies are numbered in rows from the top left, as KiKit places a grid.
    Returns the copies and a map: panel footprint KIID -> (source reference, copy index).
    """
    src = {}
    for fp in source.GetFootprints():
        pos = fp.GetPosition()
        src[fp.GetReference()] = (mm(pos.x), mm(pos.y), fp.GetOrientation().AsDegrees(), fp.GetLayer(),
                                  str(fp.GetFPID().GetUniStringLibItemName()))
    clusters: list[dict] = []
    members: list[tuple[str, str, int]] = []   # (kiid, ref, cluster)
    for fp in panel.GetFootprints():
        ref = fp.GetReference()
        if ref not in src:
            continue
        sx, sy, sangle, slayer, sname = src[ref]
        if fp.GetLayer() != slayer or str(fp.GetFPID().GetUniStringLibItemName()) != sname:
            continue
        pos = fp.GetPosition()
        angle = (fp.GetOrientation().AsDegrees() - sangle) % 360
        rx, ry = _rotate(sx, sy, angle)
        tx, ty = mm(pos.x) - rx, mm(pos.y) - ry
        for i, c in enumerate(clusters):
            da = abs((c["angle"] - angle + 180) % 360 - 180)
            if da < 0.05 and abs(c["tx"] - tx) < 0.02 and abs(c["ty"] - ty) < 0.02:
                break
        else:
            clusters.append({"angle": angle, "tx": tx, "ty": ty})
            i = len(clusters) - 1
        members.append((fp.m_Uuid.AsString(), ref, i))

    x0, y0, x1, y1 = board_bbox(source)
    copies = []
    for i, c in enumerate(clusters):
        corners = [_rotate(x, y, c["angle"], c["tx"], c["ty"]) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        copies.append((i, Copy(0, c["angle"], c["tx"], c["ty"], (min(xs), min(ys), max(xs), max(ys)))))

    # Rows from the top, then left to right. Copies in one row have centres within 1 mm.
    def centre(item):
        b = item[1].bbox
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    ordered = sorted(copies, key=lambda item: centre(item)[1])
    rows: list[list] = []
    for item in ordered:
        if rows and abs(centre(rows[-1][0])[1] - centre(item)[1]) < 1.0:
            rows[-1].append(item)
        else:
            rows.append([item])
    number = {}
    result = []
    n = 0
    for row in rows:
        for cluster, copy in sorted(row, key=lambda item: centre(item)[0]):
            n += 1
            copy.index = n
            number[cluster] = n
            result.append(copy)
    mapping = {kiid: (ref, number[cluster]) for kiid, ref, cluster in members}
    return result, mapping


def panel_cpl(source, panel, parts: list[Part], designators: str) -> tuple[list[list[str]], dict[str, list[str]],
                                                                         list[Copy]]:
    """CPL rows of the panel and, per reference, the panel designators in copy order."""
    copies, mapping = find_copies(source, panel)
    by_ref = {p.ref: p for p in parts}
    rows = []
    names: dict[str, list[tuple[int, str]]] = {}
    for fp in panel.GetFootprints():
        key = mapping.get(fp.m_Uuid.AsString())
        if key is None:
            continue
        ref, n = key
        if ref not in by_ref or _excluded_from_pos(fp):
            continue
        name = f"{ref}_{n}" if designators == "unique" else ref
        rows.append(_cpl_row(name, *position(fp, panel, by_ref[ref])))
        names.setdefault(ref, []).append((n, name))
    ordered = {ref: [name for _, name in sorted(items)] for ref, items in names.items()}
    if designators == "unique":
        rows.sort(key=lambda r: (ref_key(r[0].rsplit("_", 1)[0]), int(r[0].rsplit("_", 1)[1])))
    return rows, ordered, copies


# ---------------------------------------------------------------- V-cuts


def vcut_items(panel, layer: int, copies: list[Copy] | None = None):
    """Lines and texts on the V-cut layer that are not part of a board copy (KiKit adds them).

    Returns (lines, texts). A line belongs to the V-cuts when it is not inside the bounding
    box of one board copy. Without copies, all lines on the layer that are as long as the
    panel are V-cuts.
    """
    p = pcbnew()
    px0, py0, px1, py1 = board_bbox(panel)
    lines, texts = [], []

    def inside_copy(x0, y0, x1, y1) -> bool:
        for c in copies or []:
            b = c.bbox
            if x0 >= b[0] - 0.5 and x1 <= b[2] + 0.5 and y0 >= b[1] - 0.5 and y1 <= b[3] + 0.5:
                return True
        return False

    for item in panel.GetDrawings():
        if item.GetLayer() != layer:
            continue
        box = item.GetBoundingBox()
        x0, y0, x1, y1 = mm(box.GetLeft()), mm(box.GetTop()), mm(box.GetRight()), mm(box.GetBottom())
        if isinstance(item, p.PCB_TEXT):
            if VCUT_TEXT in item.GetText().upper() and not inside_copy(x0, y0, x1, y1):
                texts.append(item)
            continue
        if not isinstance(item, p.PCB_SHAPE) or item.GetShape() != p.SHAPE_T_SEGMENT:
            continue
        if copies:
            if inside_copy(x0, y0, x1, y1):
                continue
        else:
            spans_x = x0 <= px0 + 0.01 and x1 >= px1 - 0.01
            spans_y = y0 <= py0 + 0.01 and y1 >= py1 - 0.01
            if not (spans_x or spans_y):
                continue
        lines.append(item)
    return lines, texts


def segment_mm(item) -> tuple[float, float, float, float]:
    s, e = item.GetStart(), item.GetEnd()
    return (mm(s.x), mm(s.y), mm(e.x), mm(e.y))


# ---------------------------------------------------------------- Gerbers and drill files


def _plot_layers(board) -> list[tuple[int, str]]:
    p = pcbnew()
    layers = [(p.F_Cu, "Top copper")]
    for i in range(1, board.GetCopperLayerCount() - 1):
        layers.append((board.GetLayerID(f"In{i}.Cu"), f"Inner copper {i}"))
    layers += [
        (p.B_Cu, "Bottom copper"),
        (p.F_Mask, "Top solder mask"),
        (p.B_Mask, "Bottom solder mask"),
        (p.F_SilkS, "Top silkscreen"),
        (p.B_SilkS, "Bottom silkscreen"),
        (p.F_Paste, "Top paste"),
        (p.B_Paste, "Bottom paste"),
        (p.Edge_Cuts, "Board outline"),
    ]
    return layers


def plot_fab_files(pcb: Path, zip_path: Path, vcut_layer: str | None = None,
                   copies: list[Copy] | None = None) -> dict:
    """Gerbers and Excellon drill files for JLCPCB in one ZIP file.

    - Protel file extensions, format 4.6 in mm, netlist attributes, one outline file.
    - Drill files: Excellon, mm, decimal format, PTH and NPTH in separate files.
    - Gerbers, drill files and positions use the same origin: the lower left board corner.
    - vcut_layer: the V-cut lines and labels on this layer go into the outline Gerber.
      JLCPCB reads V-cuts only from the outline layer. The layer itself is not plotted.
    """
    p = pcbnew()
    board = load(pcb)
    info = {"vcut_lines": 0, "vcut_texts": 0}
    if vcut_layer:
        layer = layer_id(board, vcut_layer)
        if layer != p.Edge_Cuts:
            lines, texts = vcut_items(board, layer, copies)
            for item in lines + texts:
                item.SetLayer(p.Edge_Cuts)
            info["vcut_lines"], info["vcut_texts"] = len(lines), len(texts)
            if lines and not texts:
                _add_vcut_text(board, lines)
                info["vcut_texts"] = 1

    tmp = Path(tempfile.mkdtemp(prefix="kicad-fab-gerber-"))
    try:
        pctl = p.PLOT_CONTROLLER(board)
        popt = pctl.GetPlotOptions()
        popt.SetOutputDirectory(str(tmp))
        popt.SetPlotFrameRef(False)
        popt.SetAutoScale(False)
        popt.SetScale(1)
        popt.SetMirror(False)
        popt.SetUseAuxOrigin(True)
        popt.SetUseGerberAttributes(True)
        popt.SetUseGerberX2format(False)
        popt.SetIncludeGerberNetlistInfo(True)
        popt.SetCreateGerberJobFile(False)
        popt.SetUseGerberProtelExtensions(True)
        popt.SetGerberPrecision(6)
        popt.SetDisableGerberMacros(False)
        popt.SetSubtractMaskFromSilk(True)
        popt.SetPlotReference(True)
        popt.SetPlotValue(True)
        popt.SetSketchPadsOnFabLayers(False)
        popt.SetDrillMarksType(p.DRILL_MARKS_NO_DRILL_SHAPE)
        for layer, description in _plot_layers(board):
            popt.SetSkipPlotNPTH_Pads(p.IsCopperLayer(layer))
            pctl.SetLayer(layer)
            pctl.OpenPlotfile(board.GetLayerName(layer).replace(".", "_"), p.PLOT_FORMAT_GERBER, description)
            if not pctl.PlotLayer():
                raise RuntimeError(f"KiCad could not plot layer {board.GetLayerName(layer)}")
        pctl.ClosePlot()

        drill = p.EXCELLON_WRITER(board)
        drill.SetMapFileFormat(p.PLOT_FORMAT_GERBER)
        drill.SetOptions(False, False, board.GetDesignSettings().GetAuxOrigin(), False)
        drill.SetRouteModeForOvalHoles(False)
        drill.SetFormat(True, p.GENDRILL_WRITER_BASE.DECIMAL_FORMAT)
        drill.CreateDrillandMapFilesSet(str(tmp), True, False)

        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.unlink(missing_ok=True)
        names = sorted(os.listdir(tmp))
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.write(tmp / name, name)
        info["files"] = names
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return info


def _add_vcut_text(board, lines) -> None:
    p = pcbnew()
    x0, y0, x1, y1 = segment_mm(lines[0])
    text = p.PCB_TEXT(board)
    text.SetText(VCUT_TEXT)
    text.SetLayer(p.Edge_Cuts)
    text.SetTextSize(p.VECTOR2I(p.FromMM(2), p.FromMM(2)))
    text.SetTextThickness(p.FromMM(0.3))
    if abs(y0 - y1) < 1e-3:   # horizontal line: text after the right end
        text.SetPosition(p.VECTOR2I(p.FromMM(max(x0, x1) + 6), p.FromMM(y0)))
    else:
        text.SetPosition(p.VECTOR2I(p.FromMM(x0), p.FromMM(min(y0, y1) - 6)))
        text.SetTextAngle(p.EDA_ANGLE(90, p.DEGREES_T))
    board.Add(text)
