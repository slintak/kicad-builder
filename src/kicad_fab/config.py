"""Project discovery and the optional kicad-fab.toml configuration."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .log import warn

CONFIG_NAME = "kicad-fab.toml"
WIP_VAR = "WIP_MARK"

DEFAULTS: dict = {
    "fab_house": "jlcpcb",
    "boards": 10,
    "spares": {"0402": 10, "0603": 5, "default": 0},
    "wip": False,
    # true: KiCad ERC and DRC warnings are errors too.
    "strict": False,
    "version": "",
    "fields": {
        "lcsc": ["LCSC", "LCSC Part #", "JLCPCB Part #"],
        "mouser": ["Mouser", "Mouser Part Number"],
        "mpn": ["MPN", "Manufacturer Part Number"],
        "manufacturer": ["Manufacturer"],
        "description": ["Description"],
        "source": ["Source"],
        "alt_prefix": "Alt ",
        "correction": "JLCPCB_CORRECTION",
    },
    "assembly": {
        "exclude": [],
        "sides": "auto",
    },
    "panel": {
        "file": "panel.json",
        "vcut_layer": "User.Comments",
        "designators": "unique",
        # Names of panel-check rules that only give a warning for this project.
        "ignore": [],
    },
    # Order options that the board files do not give (for README.md). Empty: "not set".
    "order": {
        "stackup": "",
        "thickness": "",
        "finish": "",
        "mask_color": "",
        "silk_color": "",
        "via_covering": "",
    },
    "render": {
        "args": ["--quality", "high", "--background", "transparent", "-w", "1440", "-h", "1080"],
    },
}

FAB_HOUSES = ("jlcpcb",)
SIDES = ("auto", "both", "top", "bottom", "none")
DESIGNATORS = ("unique", "repeat")


class ConfigError(Exception):
    pass


def _merge(base: dict, extra: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        name = f"{path}{key}"
        if key not in base:
            warn(f"{CONFIG_NAME}: unknown key '{name}' (ignored)")
            continue
        if isinstance(base[key], dict) and key != "spares":
            if not isinstance(value, dict):
                raise ConfigError(f"{CONFIG_NAME}: '{name}' must be a table")
            out[key] = _merge(base[key], value, f"{name}.")
        else:
            out[key] = value
    return out


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


@dataclass
class Project:
    """A KiCad project directory and its configuration."""

    dir: Path
    name: str
    pro: Path
    sch: Path
    pcb: Path
    out: Path
    cfg: dict = field(default_factory=dict)
    wip_reasons: list[str] = field(default_factory=list)

    # Shortcuts to the configuration.
    def fields(self, key: str) -> list[str]:
        return _as_list(self.cfg["fields"][key])

    @property
    def alt_prefix(self) -> str:
        return str(self.cfg["fields"]["alt_prefix"])

    @property
    def correction_field(self) -> str:
        return str(self.cfg["fields"]["correction"])

    @property
    def boards(self) -> int:
        return int(self.cfg["boards"])

    @property
    def spares(self) -> dict:
        return dict(self.cfg["spares"])

    @property
    def assembly_exclude(self) -> set[str]:
        return set(_as_list(self.cfg["assembly"]["exclude"]))

    @property
    def sides(self) -> str:
        return str(self.cfg["assembly"]["sides"])

    @property
    def panel_file(self) -> Path | None:
        name = self.cfg["panel"]["file"]
        if not name:
            return None
        path = self.dir / name
        return path if path.is_file() else None

    @property
    def vcut_layer(self) -> str:
        return str(self.cfg["panel"]["vcut_layer"])

    @property
    def designators(self) -> str:
        return str(self.cfg["panel"]["designators"])

    @property
    def panel_ignore(self) -> set[str]:
        return set(_as_list(self.cfg["panel"]["ignore"]))

    @property
    def wip(self) -> bool:
        return bool(self.wip_reasons)

    @property
    def strict(self) -> bool:
        return bool(self.cfg["strict"])

    def text_variables(self) -> dict[str, str]:
        try:
            data = json.loads(self.pro.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(k): str(v) for k, v in (data.get("text_variables") or {}).items()}

    def version(self) -> str:
        """Release version: config 'version', else git describe, else the board revision."""
        if self.cfg.get("version"):
            return str(self.cfg["version"])
        described = git_describe(self.dir)
        if described:
            return described
        rev = board_revision(self.pcb)
        return f"rev{rev}" if rev else "dev"


def git_describe(path: Path) -> str:
    """git describe of the project directory. Empty if it is not in a git work tree."""
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "describe", "--tags", "--always", "--dirty"],
            cwd=path, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def board_revision(pcb: Path) -> str:
    try:
        text = pcb.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'\(title_block.*?\(rev\s+"([^"]*)"', text, re.S)
    return match.group(1) if match else ""


def find_project(directory: Path, out: Path | None = None, wip: bool = False,
                 version: str | None = None) -> Project:
    directory = directory.resolve()
    if not directory.is_dir():
        raise ConfigError(f"'{directory}' is not a directory")
    cfg_path = directory / CONFIG_NAME
    raw: dict = {}
    if cfg_path.is_file():
        try:
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{CONFIG_NAME}: {e}") from None
    project_name = raw.pop("project", None)
    cfg = _merge(DEFAULTS, raw)

    pros = sorted(directory.glob("*.kicad_pro"))
    if project_name:
        pro = directory / (project_name if project_name.endswith(".kicad_pro") else f"{project_name}.kicad_pro")
        if not pro.is_file():
            raise ConfigError(f"{CONFIG_NAME}: project '{project_name}' not found in {directory}")
    elif len(pros) == 1:
        pro = pros[0]
    elif not pros:
        raise ConfigError(f"no .kicad_pro file in {directory}")
    else:
        raise ConfigError(f"more than one .kicad_pro file in {directory}: set 'project' in {CONFIG_NAME}")
    name = pro.stem

    if cfg["fab_house"] not in FAB_HOUSES:
        raise ConfigError(f"{CONFIG_NAME}: fab_house '{cfg['fab_house']}' is not supported "
                          f"(supported: {', '.join(FAB_HOUSES)})")
    if cfg["assembly"]["sides"] not in SIDES:
        raise ConfigError(f"{CONFIG_NAME}: assembly.sides must be one of {', '.join(SIDES)}")
    if cfg["panel"]["designators"] not in DESIGNATORS:
        raise ConfigError(f"{CONFIG_NAME}: panel.designators must be one of {', '.join(DESIGNATORS)}")
    if version:
        cfg["version"] = version

    project = Project(dir=directory, name=name, pro=pro, sch=directory / f"{name}.kicad_sch",
                      pcb=directory / f"{name}.kicad_pcb", out=(out or directory / "build").resolve(), cfg=cfg)
    for path in (project.sch, project.pcb):
        if not path.is_file():
            raise ConfigError(f"'{path.name}' not found next to '{pro.name}'")
    if wip:
        project.wip_reasons.append("--wip option")
    if cfg["wip"]:
        project.wip_reasons.append(f"wip = true in {CONFIG_NAME}")
    mark = project.text_variables().get(WIP_VAR, "").strip()
    if mark:
        project.wip_reasons.append(f"text variable {WIP_VAR} = '{mark}'")
    return project
