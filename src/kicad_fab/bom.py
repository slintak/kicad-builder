"""BOM files from the schematic (KiCad python-bom XML export).

Column formats follow the BOM tool of the M17 board library, so board
projects can change to kicad-fab without changes to their order process.
"""

from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .config import Project
from .findings import Findings

JLC_BOM_HEADER = ["Comment", "Designator", "Footprint", "LCSC Part #"]
MOUSER_HEADER = ["Mouser Part Number", "Manufacturer Part Number", "Quantity", "Customer Part Number",
                 "Description", "Manufacturer", "Alt Mouser Part Number", "Alt MPN"]
# Several alternates of one part in one cell.
ALT_JOIN = "; "
ALTERNATES_HEADER = ["References", "Quantity", "Primary MPN", "Alt Manufacturer", "Alt MPN",
                     "Alt Mouser Part Number", "Alt LCSC"]
OTHER_HEADER = ["References", "Quantity", "Order Quantity", "Value", "Footprint", "Manufacturer", "MPN",
                "Description", "Source"]
MOUSER_MAX_LINES = 99
# JLCPCB accepts at most 200 designators in one BOM line. KiKit uses 100.
JLC_CHUNK = 100
ALT_KEYS = ("manufacturer", "mpn", "mouser", "lcsc")
ALT_NAMES = {"manufacturer": "Manufacturer", "mpn": "MPN", "mouser": "Mouser", "lcsc": "LCSC"}
ALT_SEPARATOR = ","
ALT_EMPTY = "-"
LCSC_CODE = re.compile(r"^C\d+$")
CORRECTION = re.compile(r"^\s*-?\d+(\.\d+)?\s*;\s*-?\d+(\.\d+)?\s*;\s*-?\d+(\.\d+)?\s*$")


def ref_key(ref: str) -> tuple[str, int, str]:
    """Natural order: C2 before C10."""
    match = re.match(r"^(.*?)(\d+)(\D*)$", ref)
    if not match:
        return (ref, 0, "")
    return (match.group(1), int(match.group(2)), match.group(3))


@dataclass
class Part:
    ref: str
    value: str
    footprint: str            # library:name
    fields: dict[str, str]
    dnp: bool = False
    in_bom: bool = True
    on_board: bool = True
    in_pos: bool = True
    lcsc: str = ""
    mouser: str = ""
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    source: str = ""
    correction: str = ""
    alternates: list[dict[str, str]] = field(default_factory=list)

    @property
    def footprint_name(self) -> str:
        return self.footprint.split(":", 1)[-1]

    @property
    def group_key(self) -> tuple[str, str, str, str]:
        return (self.value, self.footprint_name, self.lcsc, self.mpn)


def _first(fields: dict[str, str], names: list[str]) -> str:
    for name in names:
        value = fields.get(name, "").strip()
        if value:
            return value
    return ""


def split_alternates(fields: dict[str, str], prefix: str) -> list[dict[str, str]]:
    """Alt fields hold one value per alternate, comma separated, '-' for no value."""
    columns = {key: [v.strip() for v in fields.get(prefix + ALT_NAMES[key], "").split(ALT_SEPARATOR)]
               for key in ALT_KEYS}
    count = len([v for v in columns["mpn"] if v])
    out = []
    for i in range(count):
        item = {}
        for key, values in columns.items():
            value = values[i] if i < len(values) else ""
            item[key] = "" if value == ALT_EMPTY else value
        out.append(item)
    return out


def read_parts(xml_path: Path, project: Project) -> list[Part]:
    root = ET.parse(xml_path).getroot()
    parts: dict[str, Part] = {}
    for comp in root.iter("comp"):
        ref = comp.get("ref", "")
        if not ref or ref.startswith("#") or ref in parts:
            continue
        props = {p.get("name"): p.get("value", "") for p in comp.findall("property")}
        fields = {f.get("name"): (f.text or "").strip() for f in comp.iter("field")}
        description = (comp.findtext("description") or "").strip()
        part = Part(
            ref=ref,
            value=(comp.findtext("value") or "").strip(),
            footprint=(comp.findtext("footprint") or "").strip(),
            fields=fields,
            dnp="dnp" in props,
            in_bom="exclude_from_bom" not in props,
            on_board="exclude_from_board" not in props,
            in_pos="exclude_from_pos_files" not in props,
        )
        part.lcsc = _first(fields, project.fields("lcsc"))
        part.mouser = _first(fields, project.fields("mouser"))
        part.mpn = _first(fields, project.fields("mpn"))
        part.manufacturer = _first(fields, project.fields("manufacturer"))
        part.description = _first(fields, project.fields("description")) or description
        part.source = _first(fields, project.fields("source"))
        part.correction = fields.get(project.correction_field, "").strip()
        part.alternates = split_alternates(fields, project.alt_prefix)
        parts[ref] = part
    return sorted(parts.values(), key=lambda p: ref_key(p.ref))


def order_parts(parts: list[Part]) -> list[Part]:
    """Parts to buy: fitted BOM parts."""
    return [p for p in parts if p.in_bom and not p.dnp]


def assembly_parts(parts: list[Part], project: Project, sides: dict[str, str] | None = None) -> list[Part]:
    """Parts the assembler places. sides: reference -> 'Top' or 'Bottom' (from the PCB).
    sides = "none" in the config: bare PCB order, the assembler places nothing."""
    if project.sides == "none":
        return []
    out = []
    for p in order_parts(parts):
        if not p.on_board or not p.in_pos or p.ref in project.assembly_exclude:
            continue
        if sides is not None:
            side = sides.get(p.ref)
            if side is None:
                continue
            if project.sides == "top" and side != "Top":
                continue
            if project.sides == "bottom" and side != "Bottom":
                continue
        out.append(p)
    return out


def groups(parts: list[Part], key) -> list[list[Part]]:
    """Group parts; groups in the order of their first reference."""
    out: dict = {}
    for p in sorted(parts, key=lambda p: ref_key(p.ref)):
        out.setdefault(key(p), []).append(p)
    return list(out.values())


def spares_for(part: Part, spares: dict) -> int:
    name = part.footprint_name.lower()
    for key, count in spares.items():
        if key == "default":
            continue
        if str(key).lower() in name:
            return int(count)
    return int(spares.get("default", 0))


def _write(path: Path, header: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def jlcpcb_rows(parts: list[Part], designators=None) -> list[list[str]]:
    """designators: function reference -> list of designators (for a panel). Default: the reference."""
    rows = []
    for items in groups(parts, lambda p: p.group_key):
        names = []
        for p in items:
            names.extend(designators(p.ref) if designators else [p.ref])
        if not names:
            continue
        first = items[0]
        for i in range(0, len(names), JLC_CHUNK):
            rows.append([first.value, ",".join(names[i:i + JLC_CHUNK]), first.footprint_name, first.lcsc])
    return rows


def write_jlcpcb(parts: list[Part], path: Path, designators=None) -> Path:
    return _write(path, JLC_BOM_HEADER, jlcpcb_rows(parts, designators))


def mouser_rows(parts: list[Part], boards: int, spares: dict) -> list[list]:
    rows = []
    for items in groups([p for p in parts if p.mouser], lambda p: p.mouser.upper()):
        first = items[0]
        quantity = len(items) * boards + spares_for(first, spares)
        alts = [a for a in first.alternates if a.get("mpn")]
        rows.append([first.mouser, first.mpn, quantity, ",".join(p.ref for p in items), first.description,
                     first.manufacturer, ALT_JOIN.join(a["mouser"] for a in alts if a.get("mouser")),
                     ALT_JOIN.join(a["mpn"] for a in alts)])
    return rows


def alternates_rows(parts: list[Part]) -> list[list]:
    rows = []
    for items in groups(parts, lambda p: p.mpn or p.group_key):
        first = items[0]
        refs = ",".join(p.ref for p in items)
        for alt in first.alternates:
            rows.append([refs, len(items), first.mpn, alt["manufacturer"], alt["mpn"], alt["mouser"], alt["lcsc"]])
    return rows


def other_rows(parts: list[Part], boards: int, spares: dict) -> list[list]:
    rows = []
    for items in groups([p for p in parts if not p.lcsc and not p.mouser], lambda p: p.group_key):
        first = items[0]
        rows.append([",".join(p.ref for p in items), len(items), len(items) * boards + spares_for(first, spares),
                     first.value, first.footprint_name, first.manufacturer, first.mpn, first.description,
                     first.source])
    return rows


def write_order_files(parts: list[Part], project: Project, bom_dir: Path) -> dict[str, Path]:
    order = order_parts(parts)
    name = project.name
    mouser = mouser_rows(order, project.boards, project.spares)
    return {
        "mouser": _write(bom_dir / f"{name}_bom_mouser.csv", MOUSER_HEADER, mouser),
        "alternates": _write(bom_dir / f"{name}_bom_alternates.csv", ALTERNATES_HEADER, alternates_rows(order)),
        "other": _write(bom_dir / f"{name}_bom_other.csv", OTHER_HEADER,
                        other_rows(order, project.boards, project.spares)),
    }


def check_parts(parts: list[Part], project: Project) -> Findings:
    findings = Findings()
    refs = {p.ref for p in parts}
    for ref in sorted(project.assembly_exclude - refs, key=ref_key):
        findings.warning("bom", "exclude_unknown", f"assembly.exclude: {ref} is not in the schematic")
    order = order_parts(parts)
    assembly = {p.ref for p in assembly_parts(parts, project)}
    for p in order:
        if p.correction and not CORRECTION.match(p.correction):
            findings.error("bom", "correction_format",
                           f"{p.ref}: {project.correction_field} '{p.correction}' is not 'x;y;rotation'")
        if p.lcsc and not LCSC_CODE.match(p.lcsc):
            findings.warning("bom", "lcsc_format", f"{p.ref}: LCSC number '{p.lcsc}' is not like 'C1234'")
        alt_columns = {key: [v.strip() for v in p.fields.get(project.alt_prefix + ALT_NAMES[key], "").split(",")
                             if v.strip()] for key in ALT_KEYS}
        count = len(alt_columns["mpn"])
        for key, values in alt_columns.items():
            if key != "mpn" and len(values) > count:
                findings.error("bom", "alt_fields",
                               f"{p.ref}: '{project.alt_prefix}{ALT_NAMES[key]}' has {len(values)} values, "
                               f"'{project.alt_prefix}MPN' has {count} (use '{ALT_EMPTY}' for no value)")
        if p.ref in assembly and not p.lcsc:
            findings.warning("bom", "no_lcsc", f"{p.ref} ({p.value}): no LCSC number, the JLCPCB BOM line is empty")
    other = [p.ref for p in order if not p.lcsc and not p.mouser]
    if other:
        findings.warning("bom", "other_parts",
                         f"no LCSC and no Mouser number: {', '.join(other)} (see the 'other' BOM)")
    lines = len(groups([p for p in order if p.mouser], lambda p: p.mouser.upper()))
    if lines > MOUSER_MAX_LINES:
        findings.warning("bom", "mouser_lines",
                         f"the Mouser BOM has {lines} lines; the Mouser BOM tool accepts {MOUSER_MAX_LINES}")
    return findings
