"""Command line: kicad-fab <command> [DIR]."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import __version__
from . import kicad
from .config import ConfigError, find_project
from .kicad import ToolError
from .log import error, info

COMMANDS = {
    "build": "all checks and all outputs, then the release ZIP",
    "check": "ERC, DRC, schematic parity, BOM field checks and panel checks",
    "schematic": "schematic PDF",
    "bom": "BOM files (JLCPCB, Mouser, alternates, other)",
    "pos": "position file (CPL) of the board",
    "gerbers": "Gerber and drill files of the board",
    "panel": "KiKit panel, panel checks, panel Gerbers, BOM, CPL and render",
    "step": "STEP model (xz)",
    "render": "renders of the board",
    "zip": "manifest and release ZIP of the files in the output directory",
}
SKIPPABLE = ("schematic", "step", "render", "panel", "panel-render", "zip")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kicad-fab",
        description="Make the fabrication and release files of a KiCad project (JLCPCB rules).")
    parser.add_argument("--version", action="version", version=f"kicad-fab {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, text in COMMANDS.items():
        p = sub.add_parser(name, help=text, description=text)
        p.add_argument("dir", nargs="?", default=".", type=Path, help="project directory (default: current)")
        p.add_argument("--out", type=Path, help="output directory (default: DIR/build)")
        p.add_argument("--wip", action="store_true",
                       help="work in progress: layout DRC errors are warnings, outputs get DO-NOT-ORDER.txt")
        p.add_argument("--release-version", help="version in the release ZIP name (default: git describe)")
        if name == "build":
            p.add_argument("--skip", default="", help=f"comma separated steps to skip: {', '.join(SKIPPABLE)}")
    sub.add_parser("versions", help="print the tool versions as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command is None:
        _parser().print_help()
        return 2
    if args.command == "versions":
        from .pipeline import versions  # noqa: PLC0415

        print(json.dumps(versions(), indent=2))
        return 0
    kicad.ensure_lib_tables()
    try:
        project = find_project(args.dir, args.out, args.wip, args.release_version)
    except ConfigError as e:
        error(str(e))
        return 2

    from .pipeline import Build  # noqa: PLC0415

    build = Build(project)
    info(f"project {project.name} in {project.dir}, output {project.out}")
    if project.wip:
        info("work in progress: " + "; ".join(project.wip_reasons))
    skip = set()
    if args.command == "build":
        skip = {s.strip() for s in args.skip.split(",") if s.strip()}
        unknown = skip - set(SKIPPABLE)
        if unknown:
            error(f"--skip: unknown step(s) {', '.join(sorted(unknown))}")
            return 2
        if "panel-render" in skip:
            project.cfg["_skip_panel_render"] = True

    plan = {
        "build": ["check_reports", "check_bom", "schematic", "bom_files", "pos", "gerbers", "panel", "step",
                  "render"],
        "check": ["check_reports", "check_bom", "panel_check"],
        "schematic": ["schematic"],
        "bom": ["check_bom", "bom_files"],
        "pos": ["pos"],
        "gerbers": ["gerbers"],
        "panel": ["panel"],
        "step": ["step"],
        "render": ["render"],
        "zip": [],
    }[args.command]
    plan = [s for s in plan if s not in skip]

    project.out.mkdir(parents=True, exist_ok=True)
    if args.command == "zip":
        build.load_previous()
    build.clear_marks()
    try:
        for step in plan:
            if step == "panel_check":
                build.panel(outputs=False)
            else:
                getattr(build, step)()
            build.steps.append(step)
    except Exception as e:  # noqa: BLE001 - report every failure, write the manifest, exit 1
        if not isinstance(e, (ToolError, ConfigError)):
            traceback.print_exc()
        error(str(e))
        build.findings.error("build", "step_failed", f"{plan[len(build.steps)]}: {e}")
        build.mark()
        build.manifest()
        return 1

    if args.command in ("build", "zip"):
        if args.command == "build":
            build.readme()
        build.mark()
        build.manifest()
        if "zip" not in skip:
            build.release_zip()
    else:
        build.mark()
        if args.command == "check":
            build.manifest()

    errors, warnings = len(build.findings.errors), len(build.findings.warnings)
    if errors:
        error(f"{errors} error(s), {warnings} warning(s)")
        return 1
    info(f"ok ({warnings} warning(s))" + (" - work in progress, DO NOT ORDER" if project.wip else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
