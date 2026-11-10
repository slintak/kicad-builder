"""Full build of the demo project: exit code, files, manifest."""

from __future__ import annotations

import subprocess

from conftest import BOARD, manifest, zip_names

EXPECTED = [
    "manifest.json",
    "reports/erc.json",
    "reports/drc.json",
    "reports/panel-check.json",
    f"{BOARD}_schematic.pdf",
    f"bom/{BOARD}_bom_jlcpcb.csv",
    f"bom/{BOARD}_bom_mouser.csv",
    f"bom/{BOARD}_bom_alternates.csv",
    f"bom/{BOARD}_bom_other.csv",
    f"fab/{BOARD}_gerbers.zip",
    f"fab/{BOARD}_pos_jlcpcb.csv",
    f"panel/{BOARD}_panel.kicad_pcb",
    f"panel/{BOARD}_panel_gerbers.zip",
    f"panel/{BOARD}_panel_bom_jlcpcb.csv",
    f"panel/{BOARD}_panel_pos_jlcpcb.csv",
    f"panel/{BOARD}_panel.png",
    f"{BOARD}.step.xz",
    f"render/{BOARD}-top.png",
    f"render/{BOARD}-bottom.png",
    f"render/{BOARD}-preview.png",
    f"{BOARD}-test.zip",
]


def test_build_exit_code(built):
    assert built["result"].returncode == 0, built["result"].stderr[-3000:]


def test_build_files(built):
    out = built["out"]
    missing = [name for name in EXPECTED if not (out / name).is_file()]
    assert not missing
    for name in EXPECTED:
        assert (out / name).stat().st_size > 0, name


def test_no_do_not_order_when_ready(built):
    out = built["out"]
    assert not list(out.rglob("DO-NOT-ORDER.txt"))
    assert "DO-NOT-ORDER.txt" not in zip_names(out / f"fab/{BOARD}_gerbers.zip")


def test_release_zip_contents(built):
    names = set(zip_names(built["out"] / f"{BOARD}-test.zip"))
    for name in EXPECTED[:-1]:
        assert name in names, name
    assert not [n for n in names if n.endswith(".lck") or "/.kicad-fab" in n]


def test_manifest_versions(built):
    data = manifest(built["out"])
    versions = data["versions"]
    kicad = subprocess.run(["kicad-cli", "version"], capture_output=True, text=True).stdout.strip()
    assert versions["kicad"] == kicad
    assert versions["kikit"]
    assert versions["kicad_fab"]
    assert versions["pcbnew"].startswith(kicad)
    assert data["board"] == BOARD
    assert data["version"] == "test"
    assert data["checks"]["passed"] is True
    assert set(data["checks"]["run"]) >= {"erc", "drc", "parity", "bom", "panel"}
    assert data["panel_check"]["passed"] is True
    paths = {f["path"] for f in data["files"]}
    assert f"fab/{BOARD}_gerbers.zip" in paths
    assert all(len(f["sha256"]) == 64 for f in data["files"])


def test_versions_command():
    result = subprocess.run(["kicad-fab", "versions"], capture_output=True, text=True)
    assert result.returncode == 0
    assert '"kicad"' in result.stdout and '"kikit"' in result.stdout


def test_readme(built):
    """README.md in the build directory tells how to order the board."""
    text = (built["out"] / "README.md").read_text(encoding="utf-8")
    for needle in ("## Board", "## Panel", "## Order at JLCPCB", "Panel by Customer", "rows x",
                   "Complete File", "## Parts bought at Mouser", "Stackup in the board file"):
        assert needle in text, needle
    assert any(f["path"] == "README.md" for f in manifest(built["out"])["files"])
