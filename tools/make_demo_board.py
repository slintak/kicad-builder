#!/usr/bin/env python3
"""Make the demo board that the tests use.

Run it in the kicad-builder image (it needs kicad-cli and the pcbnew module):

    python3 tools/make_demo_board.py tests/projects/demo

The script writes a small 4 layer project with its own symbol and footprint
library. All parts, symbols and footprints are made here. The script does not
use the KiCad libraries, so the demo has no third party content.

Contents (the tests use each item):
  - board 40 x 32 mm, 4 layers, rounded corners, aux origin at the lower left corner
  - R1, R2: 0402, same part (LCSC, Mouser, MPN): one BOM line with two references
  - C1, C3: 0402, same part, C3 is on the bottom side
  - C2: 0402 with Alt fields (two drop-in alternates)
  - D1: 0603 LED with JLCPCB_CORRECTION
  - L1: 0603 ferrite bead without order numbers (goes to the "other" BOM)
  - R3: DNP
  - J1: THT pin header, in assembly.exclude of kicad-fab.toml
  - panel.json: 2 x 2 grid, V-cuts, rails, fiducials, tooling holes

The order numbers (LCSC, Mouser, MPN) are not real parts. They only have the
format of real numbers.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

NAME = "demo"
LIB = "demo"
NS = uuid.UUID("6f1b3c1e-6c55-4c3f-9a4b-2f0d7a1d0b11")


def uid(*parts: str) -> str:
    """Stable UUID, so the generated files do not change between runs."""
    return str(uuid.uuid5(NS, "/".join(parts)))


# ---------------------------------------------------------------- symbols

FONT = "(effects (font (size 1.27 1.27)))"
HIDDEN = "(effects (font (size 1.27 1.27)) (hide yes))"


def _prop(name: str, value: str, x: float = 0, y: float = 0, angle: float = 0, hide: bool = True) -> str:
    effects = HIDDEN if hide else FONT
    return f'(property "{name}" "{value}" (at {x} {y} {angle}) {effects})'


def _pin(number: str, x: float, y: float, angle: int, length: float = 1.27) -> str:
    return (f'(pin passive line (at {x} {y} {angle}) (length {length}) '
            f'(name "~" {FONT}) (number "{number}" {FONT}))')


def _two_pin(name: str, ref: str, description: str, body: str) -> str:
    return f"""(symbol "{name}" (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes))
  (exclude_from_sim no) (in_bom yes) (on_board yes)
  {_prop("Reference", ref, 2.54, 0, 0, False)}
  {_prop("Value", name, 2.54, 2.54, 0, False)}
  {_prop("Footprint", "")}
  {_prop("Datasheet", "")}
  {_prop("Description", description)}
  (symbol "{name}_0_1" {body})
  (symbol "{name}_1_1" {_pin("1", 0, 3.81, 270)} {_pin("2", 0, -3.81, 90)})
  (embedded_fonts no))"""


def symbols() -> dict[str, str]:
    box = "(rectangle (start -1.016 2.54) (end 1.016 -2.54) (stroke (width 0.254) (type default)) (fill (type none)))"
    cap = ("(polyline (pts (xy -1.524 0.508) (xy 1.524 0.508)) (stroke (width 0.3) (type default)) (fill (type none)))"
           "(polyline (pts (xy -1.524 -0.508) (xy 1.524 -0.508)) (stroke (width 0.3) (type default)) (fill (type none)))"
           "(polyline (pts (xy 0 2.54) (xy 0 0.508)) (stroke (width 0) (type default)) (fill (type none)))"
           "(polyline (pts (xy 0 -2.54) (xy 0 -0.508)) (stroke (width 0) (type default)) (fill (type none)))")
    led = ("(polyline (pts (xy -1.27 -1.016) (xy 1.27 -1.016) (xy 0 1.016) (xy -1.27 -1.016))"
           " (stroke (width 0.254) (type default)) (fill (type none)))"
           "(polyline (pts (xy -1.27 1.016) (xy 1.27 1.016)) (stroke (width 0.254) (type default)) (fill (type none)))"
           "(polyline (pts (xy 0 2.54) (xy 0 1.016)) (stroke (width 0) (type default)) (fill (type none)))"
           "(polyline (pts (xy 0 -2.54) (xy 0 -1.016)) (stroke (width 0) (type default)) (fill (type none)))")
    bead = ("(rectangle (start -0.762 2.032) (end 0.762 -2.032) (stroke (width 0.254) (type default))"
            " (fill (type outline)))")
    out = {
        "R": _two_pin("R", "R", "Resistor", box),
        "C": _two_pin("C", "C", "Capacitor", cap),
        # Pin 1 is the cathode (bottom), pin 2 the anode (top).
        "LED": _two_pin("LED", "D", "Light emitting diode", led).replace(
            f'{_pin("1", 0, 3.81, 270)} {_pin("2", 0, -3.81, 90)}',
            f'{_pin("2", 0, 3.81, 270)} {_pin("1", 0, -3.81, 90)}'),
        "FerriteBead": _two_pin("FerriteBead", "L", "Ferrite bead", bead),
    }
    pins = " ".join(_pin(str(i + 1), -5.08, 3.81 - 2.54 * i, 0, 3.81) for i in range(4))
    out["Conn_01x04"] = f"""(symbol "Conn_01x04" (pin_names (offset 1.016) (hide yes))
  (exclude_from_sim no) (in_bom yes) (on_board yes)
  {_prop("Reference", "J", 0, 6.35, 0, False)}
  {_prop("Value", "Conn_01x04", 0, -6.35, 0, False)}
  {_prop("Footprint", "")}
  {_prop("Datasheet", "")}
  {_prop("Description", "Connector, 4 pins")}
  (symbol "Conn_01x04_1_1"
    (rectangle (start -1.27 5.08) (end 1.27 -5.08) (stroke (width 0.254) (type default)) (fill (type background)))
    {pins})
  (embedded_fonts no))"""
    return out


def write_symbol_lib(path: Path, syms: dict[str, str]) -> None:
    text = '(kicad_symbol_lib (version 20251024) (generator "kicad_fab_demo") (generator_version "10.0")\n'
    text += "\n".join(syms.values()) + ")\n"
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- footprints

def _fp_text(kind: str, value: str, x: float, y: float, layer: str, size: float, thick: float, hide: bool = False) -> str:
    h = " (hide yes)" if hide else ""
    return (f'(property "{kind}" "{value}" (at {x} {y} 0) (layer "{layer}"){h} '
            f'(effects (font (size {size} {size}) (thickness {thick}))))')


def _rect(x1: float, y1: float, x2: float, y2: float, layer: str, width: float) -> str:
    return (f'(fp_rect (start {x1} {y1}) (end {x2} {y2}) (stroke (width {width}) (type solid)) (fill no) '
            f'(layer "{layer}"))')


def _line(x1: float, y1: float, x2: float, y2: float, layer: str, width: float) -> str:
    return f'(fp_line (start {x1} {y1}) (end {x2} {y2}) (stroke (width {width}) (type solid)) (layer "{layer}"))'


def chip_footprint(name: str, descr: str, pitch: float, pad: tuple[float, float],
                   body: tuple[float, float], court: tuple[float, float]) -> str:
    """Two pad chip footprint. Pad 1 is on the left."""
    px, py = pad
    bx, by = body
    cx, cy = court
    silk_y = round(by / 2 + 0.11, 3)
    silk_x = round(pitch / 2 - px / 2 - 0.15, 3)
    items = [
        _fp_text("Reference", "REF**", 0, round(-cy / 2 - 0.55, 2), "F.SilkS", 0.8, 0.12),
        _fp_text("Value", name, 0, round(cy / 2 + 0.6, 2), "F.Fab", 0.5, 0.08),
        "(attr smd)",
        _line(-silk_x, -silk_y, silk_x, -silk_y, "F.SilkS", 0.12) if silk_x > 0.05 else "",
        _line(-silk_x, silk_y, silk_x, silk_y, "F.SilkS", 0.12) if silk_x > 0.05 else "",
        _rect(-cx / 2, -cy / 2, cx / 2, cy / 2, "F.CrtYd", 0.05),
        _rect(-bx / 2, -by / 2, bx / 2, by / 2, "F.Fab", 0.1),
    ]
    for number, x in (("1", -pitch / 2), ("2", pitch / 2)):
        items.append(f'(pad "{number}" smd roundrect (at {x} 0) (size {px} {py}) '
                     f'(layers "F.Cu" "F.Mask" "F.Paste") (roundrect_rratio 0.25))')
    return (f'(footprint "{name}" (version 20260206) (generator "kicad_fab_demo") (layer "F.Cu") '
            f'(descr "{descr}") ' + " ".join(i for i in items if i) + ")\n")


def header_footprint() -> str:
    name = "PinHeader_1x04_P2.54mm"
    items = [
        _fp_text("Reference", "REF**", 0, -2.6, "F.SilkS", 0.8, 0.12),
        _fp_text("Value", name, 0, 10.2, "F.Fab", 0.8, 0.12),
        "(attr through_hole)",
        _rect(-1.33, -1.33, 1.33, 8.95, "F.SilkS", 0.12),
        _rect(-1.8, -1.8, 1.8, 9.4, "F.CrtYd", 0.05),
        _rect(-1.27, -1.27, 1.27, 8.89, "F.Fab", 0.1),
    ]
    for i in range(4):
        shape = "rect" if i == 0 else "circle"
        items.append(f'(pad "{i + 1}" thru_hole {shape} (at 0 {round(2.54 * i, 2)}) (size 1.7 1.7) (drill 1.0) '
                     f'(layers "*.Cu" "*.Mask"))')
    return (f'(footprint "{name}" (version 20260206) (generator "kicad_fab_demo") (layer "F.Cu") '
            f'(descr "Pin header, 1x04, 2.54 mm pitch, vertical") ' + " ".join(items) + ")\n")


def write_footprints(lib: Path) -> None:
    lib.mkdir(parents=True, exist_ok=True)
    fps = {
        "R_0402": chip_footprint("R_0402", "Resistor 0402 (1005 metric)", 0.96, (0.54, 0.64), (1.0, 0.5), (1.86, 0.94)),
        "C_0402": chip_footprint("C_0402", "Capacitor 0402 (1005 metric)", 0.96, (0.54, 0.64), (1.0, 0.5), (1.86, 0.94)),
        "L_0603": chip_footprint("L_0603", "Inductor 0603 (1608 metric)", 1.55, (0.8, 0.95), (1.6, 0.8), (2.96, 1.46)),
        "LED_0603": chip_footprint("LED_0603", "LED 0603 (1608 metric), pad 1 cathode", 1.55, (0.8, 0.95),
                                   (1.6, 0.8), (2.96, 1.46)),
        "PinHeader_1x04_P2.54mm": header_footprint(),
    }
    for name, text in fps.items():
        (lib / f"{name}.kicad_mod").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- parts

# ref: (symbol, value, footprint, fields, nets of pin 1, 2, ...)
R_FIELDS = {"LCSC": "C100001", "Mouser": "000-DEMO-R1K", "MPN": "DEMO-R1K-0402",
            "Manufacturer": "Demo Parts", "Description": "Resistor 1 kOhm 1% 0402"}
C100N = {"LCSC": "C100002", "Mouser": "000-DEMO-C100N", "MPN": "DEMO-C100N-0402",
         "Manufacturer": "Demo Parts", "Description": "Capacitor 100 nF 16 V X7R 0402"}
PARTS = {
    "J1": ("Conn_01x04", "Conn_01x04", "PinHeader_1x04_P2.54mm",
           {"Mouser": "000-DEMO-HDR4", "MPN": "DEMO-HDR-1X04", "Manufacturer": "Demo Parts",
            "Description": "Pin header 1x04 2.54 mm"}, ["VCC", "SIG_IN", "GND", "GND"]),
    "L1": ("FerriteBead", "600R", "L_0603", {"Description": "Ferrite bead 600 Ohm 0603"}, ["SIG_IN", "SIG"]),
    "R1": ("R", "1k", "R_0402", R_FIELDS, ["VCC", "LED_A"]),
    "R2": ("R", "1k", "R_0402", R_FIELDS, ["SIG", "GND"]),
    "R3": ("R", "10k", "R_0402",
           {"LCSC": "C100003", "Mouser": "000-DEMO-R10K", "MPN": "DEMO-R10K-0402",
            "Manufacturer": "Demo Parts", "Description": "Resistor 10 kOhm 1% 0402"}, ["SIG", "VCC"]),
    "D1": ("LED", "green", "LED_0603",
           {"LCSC": "C100004", "Mouser": "000-DEMO-LEDG", "MPN": "DEMO-LED-G-0603",
            "Manufacturer": "Demo Parts", "Description": "LED green 0603",
            "JLCPCB_CORRECTION": "0;0;180"}, ["GND", "LED_A"]),
    "C1": ("C", "100n", "C_0402", C100N, ["VCC", "GND"]),
    "C2": ("C", "1u", "C_0402",
           {"LCSC": "C100005", "Mouser": "000-DEMO-C1U", "MPN": "DEMO-C1U-0402",
            "Manufacturer": "Demo Parts", "Description": "Capacitor 1 uF 25 V X5R 0402",
            "Alt Manufacturer": "Other Parts, Third Parts", "Alt MPN": "ALT-C1U-A, ALT-C1U-B",
            "Alt Mouser": "000-ALT-C1U-A, 000-ALT-C1U-B", "Alt LCSC": "C100006, -"},
           ["VCC", "GND"]),
    "C3": ("C", "100n", "C_0402", C100N, ["VCC", "GND"]),
}
DNP = {"R3"}
FIELD_ORDER = ["LCSC", "Mouser", "MPN", "Manufacturer", "Alt Manufacturer", "Alt MPN", "Alt Mouser", "Alt LCSC",
               "JLCPCB_CORRECTION"]

# Schematic positions (mm, multiples of 2.54).
SCH_POS = {"J1": (38.1, 63.5), "L1": (63.5, 50.8), "R1": (88.9, 50.8), "R2": (114.3, 50.8), "R3": (139.7, 50.8),
           "D1": (165.1, 50.8), "C1": (88.9, 88.9), "C2": (114.3, 88.9), "C3": (139.7, 88.9)}


def pin_points(ref: str) -> list[tuple[float, float]]:
    """Schematic coordinates of the pin ends, in pin order."""
    x, y = SCH_POS[ref]
    sym = PARTS[ref][0]
    if sym == "Conn_01x04":
        return [(round(x - 5.08, 2), round(y - 3.81 + 2.54 * i, 2)) for i in range(4)]
    if sym == "LED":
        return [(x, round(y + 3.81, 2)), (x, round(y - 3.81, 2))]
    return [(x, round(y - 3.81, 2)), (x, round(y + 3.81, 2))]


def schematic(syms: dict[str, str], root: str) -> str:
    lib_symbols = "\n".join(text.replace(f'(symbol "{name}" ', f'(symbol "{LIB}:{name}" ', 1)
                            for name, text in syms.items())
    parts = []
    for ref, (sym, value, footprint, fields, nets) in PARTS.items():
        x, y = SCH_POS[ref]
        dnp = "yes" if ref in DNP else "no"
        props = [
            _prop("Reference", ref, x + 2.54, y - 1.27, 0, False),
            _prop("Value", value, x + 2.54, y + 1.27, 0, False),
            _prop("Footprint", f"{LIB}:{footprint}", x, y),
            _prop("Datasheet", "", x, y),
            _prop("Description", fields.get("Description", ""), x, y),
        ]
        for name in FIELD_ORDER:
            if name in fields:
                props.append(_prop(name, fields[name], x, y))
        pins = " ".join(f'(pin "{i + 1}" (uuid "{uid(ref, "pin", str(i))}"))' for i in range(len(nets)))
        parts.append(f"""(symbol (lib_id "{LIB}:{sym}") (at {x} {y} 0) (unit 1) (body_style 1)
  (exclude_from_sim no) (in_bom yes) (on_board yes) (in_pos_files yes) (dnp {dnp})
  (uuid "{uid(ref)}")
  {" ".join(props)}
  {pins}
  (instances (project "{NAME}" (path "/{root}" (reference "{ref}") (unit 1)))))""")
    labels = []
    for ref, (_, _, _, _, nets) in PARTS.items():
        for i, ((x, y), net) in enumerate(zip(pin_points(ref), nets)):
            labels.append(f'(label "{net}" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (justify left bottom)) '
                          f'(uuid "{uid(ref, "label", str(i))}"))')
    return f"""(kicad_sch (version 20260306) (generator "eeschema") (generator_version "10.0")
(uuid "{root}")
(paper "A4")
(title_block (title "kicad-fab demo board") (rev "A") (company "kicad-builder tests"))
(lib_symbols
{lib_symbols})
{chr(10).join(labels)}
{chr(10).join(parts)}
(sheet_instances (path "/" (page "1")))
(embedded_fonts no))
"""


def project_file() -> str:
    default_class = {
        "name": "Default", "clearance": 0.2, "track_width": 0.25, "via_diameter": 0.6, "via_drill": 0.3,
        "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
        "diff_pair_width": 0.2, "bus_width": 12, "wire_width": 6, "line_style": 0, "priority": 2147483647,
        "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)", "tuning_profile": "",
    }
    return json.dumps({
        "board": {"design_settings": {"defaults": {"board_outline_line_width": 0.05}}},
        "meta": {"filename": f"{NAME}.kicad_pro", "version": 3},
        "net_settings": {"classes": [default_class], "meta": {"version": 5}, "net_colors": {},
                         "netclass_assignments": {}, "netclass_patterns": []},
        "text_variables": {"WIP_MARK": ""},
    }, indent=2) + "\n"


# ---------------------------------------------------------------- board

ORIGIN = (100.0, 100.0)
SIZE = (40.0, 32.0)
CORNER = 2.0
# ref: (x, y, rotation, side)
PCB_POS = {
    "J1": (105.0, 108.19, 0, "F"),
    "L1": (110.0, 110.73, 0, "F"),
    "R2": (116.0, 112.2, 270, "F"),
    "R3": (121.0, 110.73, 0, "F"),
    "R1": (113.0, 105.5, 0, "F"),
    "D1": (118.0, 105.5, 180, "F"),
    "C1": (112.0, 122.0, 0, "F"),
    "C2": (118.0, 122.0, 0, "F"),
    "C3": (124.0, 122.0, 0, "B"),
}
VIA_OFFSET = 1.3


def build_board(out: Path, netlist: Path) -> None:
    import pcbnew

    pcbnew.KIID.SeedGenerator(1717)

    def mm(v: float) -> int:
        return pcbnew.FromMM(v)

    def pt(x: float, y: float):
        return pcbnew.VECTOR2I(mm(x), mm(y))

    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
    settings = board.GetDesignSettings()
    settings.SetBoardThickness(mm(1.6))
    settings.SetAuxOrigin(pt(ORIGIN[0], ORIGIN[1] + SIZE[1]))

    root = ET.parse(netlist).getroot()
    nets: dict[str, pcbnew.NETINFO_ITEM] = {}
    pad_net: dict[tuple[str, str], str] = {}
    for net in root.iter("net"):
        name = net.get("name")
        info = pcbnew.NETINFO_ITEM(board, name)
        board.Add(info)
        nets[name] = info
        for node in net.iter("node"):
            pad_net[(node.get("ref"), node.get("pin"))] = name

    def net(short: str) -> pcbnew.NETINFO_ITEM:
        for name, info in nets.items():
            if name.lstrip("/") == short:
                return info
        raise KeyError(short)

    # Outline with rounded corners.
    x0, y0 = ORIGIN
    x1, y1 = x0 + SIZE[0], y0 + SIZE[1]
    r = CORNER

    def edge_line(a, b):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pt(*a))
        s.SetEnd(pt(*b))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.05))
        board.Add(s)

    def edge_arc(center, start):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_ARC)
        s.SetCenter(pt(*center))
        s.SetStart(pt(*start))
        s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T), True)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.05))
        board.Add(s)

    edge_line((x0 + r, y0), (x1 - r, y0))
    edge_line((x1, y0 + r), (x1, y1 - r))
    edge_line((x1 - r, y1), (x0 + r, y1))
    edge_line((x0, y1 - r), (x0, y0 + r))
    edge_arc((x1 - r, y0 + r), (x1 - r, y0))
    edge_arc((x1 - r, y1 - r), (x1, y1 - r))
    edge_arc((x0 + r, y1 - r), (x0 + r, y1))
    edge_arc((x0 + r, y0 + r), (x0, y0 + r))

    # Footprints.
    lib = str(out / f"{LIB}.pretty")
    comps = {c.get("ref"): c for c in root.iter("comp")}
    footprints = {}
    for ref, (sym, value, fpname, fields, _) in PARTS.items():
        fp = pcbnew.FootprintLoad(lib, fpname)
        fp.SetFPID(pcbnew.LIB_ID(LIB, fpname))
        fp.SetReference(ref)
        fp.SetValue(value)
        comp = comps[ref]
        tstamp = comp.find("tstamps").text.strip()
        fp.SetPath(pcbnew.KIID_PATH(f"/{tstamp}"))
        fp.SetSheetname("/")
        fp.SetSheetfile(f"{NAME}.kicad_sch")
        board.Add(fp)
        x, y, rot, side = PCB_POS[ref]
        fp.SetPosition(pt(x, y))
        if side == "B":
            fp.Flip(pt(x, y), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientation(pcbnew.EDA_ANGLE(rot, pcbnew.DEGREES_T))
        for name in ["Description"] + FIELD_ORDER:
            if name in fields:
                fp.SetField(name, fields[name])
                field = fp.GetField(name)
                field.SetVisible(False)
                field.SetLayer(pcbnew.F_Fab if side == "F" else pcbnew.B_Fab)
        if ref in DNP:
            fp.SetDNP(True)
        for pad in fp.Pads():
            pad.SetNet(nets[pad_net[(ref, pad.GetNumber())]])
        footprints[ref] = fp

    def pad_pos(ref: str, number: str) -> tuple[float, float]:
        for pad in footprints[ref].Pads():
            if pad.GetNumber() == number:
                p = pad.GetPosition()
                return (pcbnew.ToMM(p.x), pcbnew.ToMM(p.y))
        raise KeyError((ref, number))

    def track(a, b, netname: str, layer=pcbnew.F_Cu, width: float = 0.25):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pt(*a))
        t.SetEnd(pt(*b))
        t.SetWidth(mm(width))
        t.SetLayer(layer)
        t.SetNet(net(netname))
        board.Add(t)

    def via(p, netname: str):
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pt(*p))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetDrill(mm(0.3))
        try:
            v.SetWidth(mm(0.6))
        except TypeError:
            v.SetWidth(pcbnew.PADSTACK.ALL_LAYERS, mm(0.6))
        v.SetNet(net(netname))
        board.Add(v)

    def pad_to_via(ref: str, number: str, netname: str, layer=pcbnew.F_Cu):
        """Track from the pad away from the part centre, then a via."""
        a = pad_pos(ref, number)
        c = footprints[ref].GetPosition()
        dx, dy = a[0] - pcbnew.ToMM(c.x), a[1] - pcbnew.ToMM(c.y)
        length = (dx * dx + dy * dy) ** 0.5
        b = (round(a[0] + VIA_OFFSET * dx / length, 3), round(a[1] + VIA_OFFSET * dy / length, 3))
        track(a, b, netname, layer)
        via(b, netname)

    # Signal routing on F.Cu.
    track(pad_pos("J1", "2"), pad_pos("L1", "1"), "SIG_IN")
    junction = (pad_pos("R2", "1")[0], pad_pos("L1", "2")[1])
    track(pad_pos("L1", "2"), junction, "SIG")
    track(junction, pad_pos("R3", "1"), "SIG")
    track(junction, pad_pos("R2", "1"), "SIG")
    track(pad_pos("R1", "2"), pad_pos("D1", "2"), "LED_A")
    # Power pads go to the inner planes with vias.
    pad_to_via("R2", "2", "GND")
    pad_to_via("R3", "2", "VCC")
    pad_to_via("R1", "1", "VCC")
    pad_to_via("D1", "1", "GND")
    for ref in ("C1", "C2"):
        pad_to_via(ref, "1", "VCC")
        pad_to_via(ref, "2", "GND")
    # C3 is on the bottom side.
    pad_to_via("C3", "1", "VCC", pcbnew.B_Cu)
    pad_to_via("C3", "2", "GND", pcbnew.B_Cu)

    # Inner planes, 1 mm from the board edge.
    for layer, netname, priority in ((pcbnew.In1_Cu, "GND", 0), (pcbnew.In2_Cu, "VCC", 0)):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(layer)
        zone.SetNet(net(netname))
        zone.SetAssignedPriority(priority)
        outline = zone.Outline()
        outline.NewOutline()
        for px, py in ((x0 + 1, y0 + 1), (x1 - 1, y0 + 1), (x1 - 1, y1 - 1), (x0 + 1, y1 - 1)):
            outline.Append(mm(px), mm(py))
        zone.SetMinThickness(mm(0.2))
        zone.SetLocalClearance(mm(0.3))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        board.Add(zone)

    # Board text on the top silkscreen, inside the board.
    text = pcbnew.PCB_TEXT(board)
    text.SetText("kicad-fab demo rev ${REVISION}")
    text.SetLayer(pcbnew.F_SilkS)
    text.SetPosition(pt(120.0, 128.0))
    text.SetTextSize(pcbnew.VECTOR2I(mm(1.0), mm(1.0)))
    text.SetTextThickness(mm(0.15))
    board.Add(text)

    tb = board.GetTitleBlock()
    tb.SetTitle("kicad-fab demo board")
    tb.SetRevision("A")
    tb.SetCompany("kicad-builder tests")

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(out / f"{NAME}.kicad_pcb"))


# ---------------------------------------------------------------- panel and config

PANEL = {
    "layout": {"type": "grid", "rows": 2, "cols": 2, "hspace": "0mm", "vspace": "0mm",
               "renameref": "{orig}"},
    "tabs": {"type": "full"},
    "cuts": {"type": "vcuts", "layer": "Cmts.User", "clearance": "2mm"},
    "framing": {"type": "railstb", "width": "7mm", "hspace": "0mm", "vspace": "0mm", "cuts": "both"},
    "tooling": {"type": "3hole", "hoffset": "3mm", "voffset": "3.5mm", "size": "1.152mm"},
    "fiducials": {"type": "3fid", "hoffset": "9mm", "voffset": "4mm", "coppersize": "1mm", "opening": "2mm"},
    "text": {"type": "simple", "text": "kicad-fab demo panel", "anchor": "mt", "voffset": "3.5mm",
             "hjustify": "center", "vjustify": "center", "width": "1.2mm", "height": "1.2mm",
             "thickness": "0.2mm", "layer": "F.SilkS"},
    "post": {"millradius": "1mm"},
    # Frame copper: the V-cuts cut the rails into parts; every part must get copper.
    "copperfill": {"type": "hatched", "layers": "F.Cu,B.Cu", "clearance": "1mm", "edgeclearance": "1mm",
                   "width": "1mm", "spacing": "1mm", "orientation": "45deg"},
}

CONFIG = """# kicad-fab configuration of the demo board (used by the tests)
fab_house = "jlcpcb"
boards = 10
spares = { "0402" = 10, "0603" = 5, default = 0 }

[assembly]
# The pin header is soldered by hand. It stays in the Mouser order.
exclude = ["J1"]
"""


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/projects/demo").resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = uid("root-sheet")
    syms = symbols()
    write_symbol_lib(out / f"{LIB}.kicad_sym", syms)
    write_footprints(out / f"{LIB}.pretty")
    (out / "sym-lib-table").write_text(
        f'(sym_lib_table (version 7) (lib (name "{LIB}") (type "KiCad") (uri "${{KIPRJMOD}}/{LIB}.kicad_sym") '
        f'(options "") (descr "kicad-fab demo symbols")))\n', encoding="utf-8")
    (out / "fp-lib-table").write_text(
        f'(fp_lib_table (version 7) (lib (name "{LIB}") (type "KiCad") (uri "${{KIPRJMOD}}/{LIB}.pretty") '
        f'(options "") (descr "kicad-fab demo footprints")))\n', encoding="utf-8")
    (out / f"{NAME}.kicad_sch").write_text(schematic(syms, root), encoding="utf-8")
    (out / f"{NAME}.kicad_pro").write_text(project_file(), encoding="utf-8")
    netlist = out / "netlist.xml"
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", str(netlist),
                    str(out / f"{NAME}.kicad_sch")], check=True)
    try:
        build_board(out, netlist)
    finally:
        netlist.unlink(missing_ok=True)
        # Local view settings of KiCad: not part of the project.
        (out / f"{NAME}.kicad_prl").unlink(missing_ok=True)
    (out / "panel.json").write_text(json.dumps(PANEL, indent=2) + "\n", encoding="utf-8")
    (out / "kicad-fab.toml").write_text(CONFIG, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
