"""kicad-cli calls and the ERC and DRC report checks."""

from __future__ import annotations

import json
import lzma
import os
import shutil
import subprocess
from pathlib import Path

from .findings import ERROR, Findings
from .log import info, warn

# Text that KiCad prints on stderr for each start. It is not a problem.
NOISE = ("PROPERTY_ENUM", "m_choices.GetCount()", "Debug: Adding duplicate image handler")


class ToolError(Exception):
    pass


def _clean(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if line.strip() and not any(n in line for n in NOISE))


def run(args: list[str], ok_codes: tuple[int, ...] = (0,), cwd: Path | None = None,
        retries: int = 0) -> subprocess.CompletedProcess:
    """retries: run again this many times if the tool crashes (stopped by a signal)."""
    info("run: " + " ".join(str(a) for a in args))
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, cwd=cwd)
    while result.returncode < 0 and retries > 0:
        warn(f"{args[0]} crashed (signal {-result.returncode}), run it again")
        retries -= 1
        result = subprocess.run([str(a) for a in args], capture_output=True, text=True, cwd=cwd)
    if result.returncode not in ok_codes:
        output = _clean(result.stdout + "\n" + result.stderr)
        raise ToolError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed (exit code {result.returncode})"
                        + (f":\n{output}" if output else ""))
    return result


TEMPLATE_DIR = Path("/usr/share/kicad/template")


def ensure_lib_tables() -> list[Path]:
    """Give KiCad the global symbol and footprint library tables of the installation.

    Without a user configuration (a fresh HOME in a container), KiCad has empty global
    tables, so ERC and DRC report the stock libraries (power, Device, Resistor_SMD, ...)
    as missing. Copy the installed templates when the user has no tables.
    """
    version = ".".join(cli_version().split(".")[:2])
    if not version or not TEMPLATE_DIR.is_dir():
        return []
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kicad" / version
    written = []
    for name in ("sym-lib-table", "fp-lib-table"):
        src, dst = TEMPLATE_DIR / name, config / name
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            written.append(dst)
    if written:
        info("KiCad global library tables from " + str(TEMPLATE_DIR))
    return written


def cli_version() -> str:
    try:
        return run(["kicad-cli", "version"]).stdout.strip()
    except (ToolError, OSError):
        return ""


def erc(sch: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["kicad-cli", "sch", "erc", "--format", "json", "--severity-all", "--units", "mm", "-o", out, sch])
    return out


def drc(pcb: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity", "--units", "mm",
         "-o", out, pcb])
    return out


def _items(violation: dict) -> str:
    descriptions = [i.get("description", "") for i in violation.get("items", [])]
    descriptions = [d for d in descriptions if d]
    return f" [{'; '.join(descriptions)}]" if descriptions else ""


def check_erc(report: Path, strict: bool) -> Findings:
    findings = Findings()
    data = json.loads(report.read_text(encoding="utf-8"))
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            text = f"{v.get('description')}{_items(v)} (sheet {sheet.get('path', '/')})"
            if v.get("severity") == ERROR or strict:
                findings.error("erc", v.get("type", "?"), text)
            else:
                findings.warning("erc", v.get("type", "?"), text)
    return findings


def check_drc(report: Path, strict: bool, wip: bool) -> Findings:
    """Layout findings are warnings in a work in progress. Schematic parity is always strict."""
    findings = Findings()
    data = json.loads(report.read_text(encoding="utf-8"))
    for group in ("violations", "unconnected_items", "schematic_parity"):
        for v in data.get(group, []):
            text = f"{v.get('description')}{_items(v)}"
            source = "parity" if group == "schematic_parity" else "drc"
            if group == "schematic_parity":
                findings.error(source, v.get("type", "?"), text)
            elif wip:
                findings.warning(source, v.get("type", "?"), text + " (allowed: work in progress)")
            elif v.get("severity") == ERROR or strict:
                findings.error(source, v.get("type", "?"), text)
            else:
                findings.warning(source, v.get("type", "?"), text)
    return findings


def schematic_pdf(sch: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["kicad-cli", "sch", "export", "pdf", "-o", out, sch])
    return out


def bom_xml(sch: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["kicad-cli", "sch", "export", "python-bom", "-o", out, sch])
    return out


def step(pcb: Path, out_xz: Path) -> Path:
    """STEP model, compressed with xz. Exit code 2 (missing 3D models) is accepted."""
    out_xz.parent.mkdir(parents=True, exist_ok=True)
    step_file = out_xz.with_suffix("")
    step_file.unlink(missing_ok=True)
    run(["kicad-cli", "pcb", "export", "step", "--subst-models", "-f", "-o", step_file, pcb], ok_codes=(0, 2))
    if not step_file.is_file() or step_file.stat().st_size == 0:
        raise ToolError("kicad-cli did not write the STEP file")
    with step_file.open("rb") as src, lzma.open(out_xz, "wb", preset=6) as dst:
        shutil.copyfileobj(src, dst)
    step_file.unlink()
    return out_xz


def render(pcb: Path, out: Path, args: list[str], side: str = "top", rotate: str | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["kicad-cli", "pcb", "render", *args, "--side", side]
    if rotate:
        cmd += ["--rotate", rotate]
    run(cmd + ["-o", out, pcb])
    return out
