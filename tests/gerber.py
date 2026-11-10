"""A small Gerber (RS-274X) reader for the tests: units, format, apertures and draws."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

COORD = re.compile(r"(?:X(?P<x>[+-]?\d+))?(?:Y(?P<y>[+-]?\d+))?(?:I(?P<i>[+-]?\d+))?(?:J(?P<j>[+-]?\d+))?"
                   r"D0?(?P<d>[123])\*$")


@dataclass
class Draw:
    x0: float
    y0: float
    x1: float
    y1: float
    aperture: str
    arc: bool = False

    @property
    def horizontal(self) -> bool:
        return not self.arc and abs(self.y0 - self.y1) < 1e-6 and abs(self.x0 - self.x1) > 1e-6

    @property
    def vertical(self) -> bool:
        return not self.arc and abs(self.x0 - self.x1) < 1e-6 and abs(self.y0 - self.y1) > 1e-6

    @property
    def length(self) -> float:
        return ((self.x1 - self.x0) ** 2 + (self.y1 - self.y0) ** 2) ** 0.5


@dataclass
class Gerber:
    units: str = ""
    format: tuple[int, int] = (0, 0)
    file_function: str = ""
    apertures: dict[str, str] = field(default_factory=dict)
    draws: list[Draw] = field(default_factory=list)
    flashes: list[tuple[float, float, str]] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)

    def bbox(self, draws: list[Draw] | None = None) -> tuple[float, float, float, float]:
        draws = self.draws if draws is None else draws
        xs = [v for d in draws for v in (d.x0, d.x1)]
        ys = [v for d in draws for v in (d.y0, d.y1)]
        return (min(xs), min(ys), max(xs), max(ys))


def parse(text: str) -> Gerber:
    g = Gerber()
    scale = 1e6
    x = y = 0.0
    aperture = ""
    mode_arc = False
    for raw in re.split(r"(?<=\*)\s*|\n", text):
        line = raw.strip().strip("%")
        if not line:
            continue
        if line.startswith("G04"):
            g.comments.append(line)
            if "TF.FileFunction," in line:
                g.file_function = line.split("TF.FileFunction,", 1)[1].rstrip("*")
            continue
        if line.startswith("TF.FileFunction,"):
            g.file_function = line.split(",", 1)[1].rstrip("*")
            continue
        if line.startswith("FSLA"):
            m = re.match(r"FSLAX(\d)(\d)Y(\d)(\d)", line)
            g.format = (int(m.group(1)), int(m.group(2)))
            scale = 10 ** g.format[1]
            continue
        if line.startswith("MO"):
            g.units = line[2:4]
            continue
        if line.startswith("AD"):
            m = re.match(r"ADD(\d+)(.*)\*", line)
            if m:
                g.apertures[f"D{m.group(1)}"] = m.group(2)
            continue
        if line.startswith("G01"):
            mode_arc = False
            line = line[3:]
        elif line.startswith(("G02", "G03")):
            mode_arc = True
            line = line[3:]
        if not line or line.startswith(("G75", "G74", "LP", "TA", "TD", "TO", "M02", "G36", "G37")):
            continue
        m = re.match(r"^D(\d+)\*$", line)
        if m and int(m.group(1)) >= 10:
            aperture = f"D{m.group(1)}"
            continue
        m = COORD.match(line)
        if not m:
            continue
        nx = int(m.group("x")) / scale if m.group("x") is not None else x
        ny = int(m.group("y")) / scale if m.group("y") is not None else y
        d = m.group("d")
        if d == "1":
            g.draws.append(Draw(x, y, nx, ny, aperture, mode_arc))
        elif d == "3":
            g.flashes.append((nx, ny, aperture))
        x, y = nx, ny
    return g
