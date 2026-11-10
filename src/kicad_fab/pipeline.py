"""The build steps. Each step writes its files below the output directory."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import zipfile
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from . import __version__, bom, kicad
from . import board as pcb
from . import panel as panelmod
from .config import Project, git_describe
from .findings import Finding, Findings
from .log import info, warn

DO_NOT_ORDER = "DO-NOT-ORDER.txt"
FAB_DIRS = ("bom", "fab", "panel")


class Build:
    """State of one kicad-fab run."""

    def __init__(self, project: Project):
        self.project = project
        self.out = project.out
        self.findings = Findings()
        self.checks_run: list[str] = []
        self._parts: list[bom.Part] | None = None
        self._board = None
        self.panel_result: dict | None = None
        self.panel_preset: dict | None = None
        self.steps: list[str] = []
        self._versions: dict | None = None

    # ------------------------------------------------------------ paths

    def path(self, *parts: str) -> Path:
        return self.out.joinpath(*parts)

    @property
    def name(self) -> str:
        return self.project.name

    # ------------------------------------------------------------ cached data

    def parts(self) -> list[bom.Part]:
        if self._parts is None:
            xml = kicad.bom_xml(self.project.sch, self.path("bom", f"{self.name}_bom.xml"))
            self._parts = bom.read_parts(xml, self.project)
        return self._parts

    def board(self):
        if self._board is None:
            self._board = pcb.load(self.project.pcb)
        return self._board

    def assembly(self) -> list[bom.Part]:
        return bom.assembly_parts(self.parts(), self.project, pcb.footprint_sides(self.board()))

    # ------------------------------------------------------------ checks

    def check_reports(self) -> None:
        info("ERC and DRC")
        erc = kicad.erc(self.project.sch, self.path("reports", "erc.json"))
        drc = kicad.drc(self.project.pcb, self.path("reports", "drc.json"))
        self.findings.extend(kicad.check_erc(erc, self.project.strict))
        self.findings.extend(kicad.check_drc(drc, self.project.strict, self.project.wip))
        self.checks_run += ["erc", "drc", "parity"]

    def check_bom(self) -> None:
        info("BOM field checks")
        self.findings.extend(bom.check_parts(self.parts(), self.project))
        self.checks_run.append("bom")

    # ------------------------------------------------------------ outputs

    def schematic(self) -> None:
        info("schematic PDF")
        kicad.schematic_pdf(self.project.sch, self.path(f"{self.name}_schematic.pdf"))

    def bom_files(self) -> None:
        info("BOM files")
        bom_dir = self.path("bom")
        bom.write_order_files(self.parts(), self.project, bom_dir)
        bom.write_jlcpcb(self.assembly(), bom_dir / f"{self.name}_bom_jlcpcb.csv")

    def pos(self) -> None:
        info("position file (CPL)")
        rows = pcb.board_cpl(self.board(), self.assembly())
        pcb.write_cpl(rows, self.path("fab", f"{self.name}_pos_jlcpcb.csv"))

    def gerbers(self) -> None:
        info("Gerber and drill files of the board")
        pcb.plot_fab_files(self.project.pcb, self.path("fab", f"{self.name}_gerbers.zip"))

    def panel(self, outputs: bool = True) -> None:
        """Make the panel and check it. outputs: also write the panel Gerbers, BOM, CPL and render."""
        if self.project.panel_file is None:
            info(f"no {self.project.cfg['panel']['file']}: no panel")
            return
        info("panel (KiKit)")
        paths = panelmod.panelize(self.project)
        source = self.board()
        panel = pcb.load(paths["pcb"])
        preset = json.loads(paths["preset"].read_text(encoding="utf-8"))
        self.panel_preset = preset
        assembly = self.assembly()
        rows, names, copies = pcb.panel_cpl(source, panel, assembly, self.project.designators)
        expected = len(copies) * len(pcb.board_cpl(source, assembly))
        if len(rows) != expected:
            warn(f"panel CPL has {len(rows)} lines, expected {expected} ({len(copies)} copies)")
        self._check_panel_refs(panel, source)

        info("panel checks")
        sides = {pcb.footprint_sides(source).get(p.ref) for p in assembly}
        result = panelmod.check_panel(self.project, source, panel, preset, copies, sides)
        findings = result.pop("findings")
        self.findings.extend(findings)
        self.checks_run.append("panel")
        result["copies"] = [{"index": c.index, "angle": round(c.angle, 3), "bbox_mm": [round(v, 3) for v in c.bbox]}
                            for c in copies]
        result["designators"] = self.project.designators
        self.panel_result = result
        report = self.path("reports", "panel-check.json")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if not outputs:
            return

        pdir = paths["dir"]
        pcb.write_cpl(rows, pdir / f"{self.name}_panel_pos_jlcpcb.csv")
        bom.write_jlcpcb(assembly, pdir / f"{self.name}_panel_bom_jlcpcb.csv", lambda ref: names.get(ref, []))
        info("Gerber and drill files of the panel")
        plotted = pcb.plot_fab_files(paths["pcb"], pdir / f"{self.name}_panel_gerbers.zip",
                                     vcut_layer=self.project.vcut_layer, copies=copies)
        info(f"panel outline Gerber: {plotted['vcut_lines']} V-cut lines, {plotted['vcut_texts']} V-CUT texts")
        if self.project.cfg.get("_skip_render"):
            return
        info("panel render")
        kicad.render(paths["pcb"], pdir / f"{self.name}_panel.png", self.project.cfg["render"]["args"], "top")

    def _check_panel_refs(self, panel, source) -> None:
        """The silkscreen of every copy must show the original references."""
        refs = {fp.GetReference() for fp in source.GetFootprints()}
        bad = sorted({fp.GetReference() for fp in panel.GetFootprints()
                      if fp.GetReference() not in refs and "_" in fp.GetReference()
                      and fp.GetReference().rsplit("_", 1)[0] in refs})
        if bad:
            self.findings.error("panel", "renamed_refs", f"panel references are renamed: {', '.join(bad[:10])}")

    def step(self) -> None:
        info("STEP model")
        kicad.step(self.project.pcb, self.path(f"{self.name}.step.xz"))

    def render(self) -> None:
        info("renders")
        args = self.project.cfg["render"]["args"]
        rdir = self.path("render")
        kicad.render(self.project.pcb, rdir / f"{self.name}-top.png", args, "top")
        kicad.render(self.project.pcb, rdir / f"{self.name}-bottom.png", args, "bottom")
        kicad.render(self.project.pcb, rdir / f"{self.name}-preview.png", args, "top", rotate="315,0,315")

    # ------------------------------------------------------------ marking, manifest, release ZIP

    @property
    def versions(self) -> dict:
        if self._versions is None:
            self._versions = versions()
        return self._versions

    def readme(self) -> Path | None:
        """README.md in the build directory: the files and how to order the board."""
        if "bom_files" not in self.steps:
            return None
        from . import ordering
        return ordering.write_readme(self, self.path("README.md"))

    def clear_marks(self) -> None:
        for d in FAB_DIRS:
            (self.path(d) / DO_NOT_ORDER).unlink(missing_ok=True)

    def do_not_order_text(self) -> str | None:
        reasons = list(self.project.wip_reasons)
        if self.findings.errors:
            reasons.append(f"{len(self.findings.errors)} check error(s), see manifest.json")
        if not reasons:
            return None
        return ("DO NOT ORDER\n\n"
                "These files are not ready for manufacture.\n"
                "Reasons:\n" + "".join(f"  - {r}\n" for r in reasons))

    def mark(self) -> None:
        """Put DO-NOT-ORDER.txt into each fab directory and each ZIP file."""
        text = self.do_not_order_text()
        if text is None:
            return
        for d in FAB_DIRS:
            path = self.path(d)
            if path.is_dir():
                (path / DO_NOT_ORDER).write_text(text, encoding="utf-8")
        for archive in self.out.rglob("*.zip"):
            _add_to_zip(archive, DO_NOT_ORDER, text)

    def load_previous(self) -> None:
        """For 'zip': keep the check results of the last run (from manifest.json)."""
        path = self.path("manifest.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        checks = data.get("checks") or {}
        for f in checks.get("findings", []):
            self.findings.items.append(Finding(f["source"], f["severity"], f["rule"], f["message"]))
        self.checks_run = list(checks.get("run", []))
        self.steps = list(data.get("steps", []))
        self.panel_result = data.get("panel_check")

    def manifest(self) -> Path:
        files = []
        for path in sorted(self.out.rglob("*")):
            if not path.is_file() or path.name == "manifest.json" or _internal(path):
                continue
            if path.suffix == ".zip" and path.parent == self.out:
                continue
            files.append({"path": path.relative_to(self.out).as_posix(), "size": path.stat().st_size,
                          "sha256": _sha256(path)})
        data = {
            "board": self.name,
            "version": self.project.version(),
            "git_describe": git_describe(self.project.dir),
            "date": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "versions": versions(),
            "fab_house": self.project.cfg["fab_house"],
            "wip": self.project.wip,
            "wip_reasons": self.project.wip_reasons,
            "do_not_order": self.do_not_order_text() is not None,
            "steps": self.steps,
            "checks": {"run": sorted(set(self.checks_run)), **self.findings.summary()},
            "panel_check": None if self.panel_result is None else {
                k: self.panel_result.get(k) for k in ("passed", "errors", "warnings")},
            "files": files,
        }
        path = self.path("manifest.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path

    def release_zip(self) -> Path:
        name = f"{self.name}-{_safe(self.project.version())}.zip"
        path = self.path(name)
        for old in self.out.glob(f"{self.name}-*.zip"):
            old.unlink()
        files = [p for p in sorted(self.out.rglob("*")) if p.is_file() and p != path
                 and not _internal(p) and not (p.suffix == ".zip" and p.parent == self.out)]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for f in files:
                archive.write(f, f.relative_to(self.out).as_posix())
        text = self.do_not_order_text()
        if text is not None:
            _add_to_zip(path, DO_NOT_ORDER, text)
        info(f"release ZIP: {path}")
        return path


def _internal(path: Path) -> bool:
    """Work files that are not outputs: preset dumps, lock files."""
    return path.name.startswith((".kicad-fab", "~")) or path.suffix == ".lck"


def _add_to_zip(path: Path, name: str, text: str) -> None:
    with zipfile.ZipFile(path, "a") as archive:
        if name not in archive.namelist():
            archive.writestr(name, text)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "._-+" else "_" for c in text) or "dev"


def _pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


def versions() -> dict:
    out = {
        "kicad_fab": __version__,
        "image": os.environ.get("KICAD_FAB_IMAGE", ""),
        "kicad": kicad.cli_version(),
        "kikit": _pkg_version("kikit"),
        "pcbdraw": _pkg_version("pcbdraw"),
        "shapely": _pkg_version("shapely"),
        "python": platform.python_version(),
    }
    try:
        out["pcbnew"] = pcb.pcbnew().GetBuildVersion()
    except ImportError:
        out["pcbnew"] = ""
    return out
