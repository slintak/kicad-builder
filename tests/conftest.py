"""Test fixtures. The tests run in the kicad-builder image (kicad-cli, pcbnew, KiKit)."""

from __future__ import annotations

import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

DEMO = Path(__file__).parent / "projects" / "demo"
BOARD = "demo"
# All parts the assembler places (J1 is in assembly.exclude, R3 is DNP).
ASSEMBLED = {"C1", "C2", "C3", "D1", "L1", "R1", "R2"}
COPIES = 4
PITCH = (40.0, 32.0)      # board size = grid pitch of the panel (no space between boards)


def fab_command() -> list[str]:
    """KICAD_FAB_CMD can select another command, for example 'python3 -m kicad_fab'."""
    return shlex.split(os.environ.get("KICAD_FAB_CMD", "kicad-fab"))


def run_fab(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run([*fab_command(), *args], cwd=cwd, capture_output=True, text=True)
    sys.stderr.write(result.stderr[-6000:])
    return result


def run_python(code: str, *args: str) -> None:
    """Run code in a new Python process (pcbnew changes stay out of the test process)."""
    subprocess.run([sys.executable, "-c", code, *args], check=True, capture_output=True, text=True)


def copy_demo(target: Path) -> Path:
    shutil.copytree(DEMO, target, ignore=shutil.ignore_patterns("build", "*.lck", "~*"))
    return target


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def read_zip_text(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name).decode("utf-8")


def zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def footprint_refs(pcb: Path) -> list[str]:
    """References of all footprints in a .kicad_pcb file (text, without pcbnew)."""
    text = pcb.read_text(encoding="utf-8")
    refs = []
    for block in re.split(r"\n\t\(footprint ", text)[1:]:
        m = re.search(r'\(property "Reference" "([^"]*)"', block)
        if m:
            refs.append(m.group(1))
    return refs


def manifest(out: Path) -> dict:
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def built(tmp_path_factory) -> dict:
    """One full build of a copy of the demo project."""
    project = copy_demo(tmp_path_factory.mktemp("build") / "demo")
    result = run_fab("build", str(project), "--release-version", "test")
    return {"project": project, "out": project / "build", "result": result}
