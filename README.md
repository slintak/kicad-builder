# kicad-builder Docker Image

A ready-to-use Docker image for building, documenting, and rendering static
websites and manufacturing files for KiCAD hardware projects. This container
is designed for both CI/CD environments (such as GitHub Actions) and manual
use by hardware developers who want reproducible, automated documentation
and manufacturing outputs.

## Features

- Ubuntu 26.04 LTS base
- KiCad 10.0.8 (from the official KiCad PPA)
- `kicad-fab`: one command makes all fabrication and release files of a KiCad
  project for JLCPCB (see [Fabrication pipeline](#fabrication-pipeline-kicad-fab))
- KiKit for Gerber, panel and PCB fabrication outputs
- PcbDraw for high-quality PCB renders
- LaTeX tools for PDF docs
- Tools for:
    - Board image rendering
    - BOM generation (including JLCPCB CSV support)
    - Markdown to HTML site generation (with Jinja2, markdown-cli)
    - Static web build (rsync, xsltproc, etc.)
- Designed for GitHub Actions, but also convenient for manual use

## Usage: In GitHub Actions

Add this to your `.github/workflows/ci.yml` file:

```yaml
name: CI
on:
  push:
    branches: [ main ]
jobs:
  build-web:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/slintak/kicad-builder:latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
      - name: Build your site or artifacts
        run: make web  # or whatever your build command is
```

For the fabrication files, use the reusable workflows (see [CI for board repositories](#ci-for-board-repositories)).

## Usage: Manually

Build the image (optional, only if customizing):

```
make build
```

Run an interactive shell in the container:

```
make run
```

This opens a shell in /workspace (the default working directory).

(Recommended) Mount your project directory:

```
docker run --rm -it -v $PWD:/workspace ghcr.io/slintak/kicad-builder:latest /bin/bash
```

With no command, the image runs `kicad-fab build /workspace`:

```
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD":/workspace ghcr.io/slintak/kicad-builder:latest
```

## Fabrication pipeline (kicad-fab)

`kicad-fab` takes a KiCad project directory and makes the checks and the files to
order and to release the board. The same command runs on your computer and in
GitHub Actions. A project needs only its KiCad files, an optional `panel.json`
(KiKit preset) and an optional `kicad-fab.toml`.

```
kicad-fab build [DIR]       # all checks and all outputs, then the release ZIP (DIR: default .)
kicad-fab check [DIR]       # ERC, DRC, schematic parity, BOM field checks, panel checks
kicad-fab schematic|bom|pos|gerbers|panel|step|render|zip [DIR]
kicad-fab versions          # tool versions as JSON
```

Options: `--out DIR` (default `DIR/build`), `--wip`, `--release-version TEXT`
(default: `git describe`), and for `build`: `--skip schematic,panel,step,render`.

The exit code is not 0 if a check finds an error. `build` makes all outputs also
when a check fails, then it puts `DO-NOT-ORDER.txt` into the fab directories and ZIP
files and gives exit code 1.

Work in progress: `--wip`, `wip = true` in `kicad-fab.toml`, or a text in the KiCad
text variable `WIP_MARK` (used by the M17 board library). Then layout DRC errors are
warnings, and every fab directory and ZIP file gets `DO-NOT-ORDER.txt`. ERC and
schematic parity stay strict.

### Outputs

```
build/
  manifest.json                    versions (image, KiCad, KiKit), git describe, date, files, check results
  reports/erc.json drc.json panel-check.json
  <board>_schematic.pdf
  bom/<board>_bom_jlcpcb.csv       Comment,Designator,Footprint,LCSC Part #   (one board)
  bom/<board>_bom_mouser.csv       Mouser Part Number,Manufacturer Part Number,Quantity,Customer Part Number,Description,Manufacturer
  bom/<board>_bom_alternates.csv   References,Quantity,Primary MPN,Alt Manufacturer,Alt MPN,Alt Mouser Part Number,Alt LCSC
  bom/<board>_bom_other.csv        parts with no LCSC and no Mouser number
  fab/<board>_gerbers.zip          Gerbers and drill files of one board
  fab/<board>_pos_jlcpcb.csv       Designator,Mid X,Mid Y,Layer,Rotation (Top/Bottom, mm)
  panel/<board>_panel.kicad_pcb    KiKit panel (only with panel.json)
  panel/<board>_panel_gerbers.zip
  panel/<board>_panel_bom_jlcpcb.csv
  panel/<board>_panel_pos_jlcpcb.csv
  panel/<board>_panel.png
  <board>.step.xz
  render/<board>-top.png -bottom.png -preview.png
  <board>-<version>.zip            all files above, for the release
```

The Mouser quantity is the quantity on one board x `boards` + spares. "Customer
Part Number" has the references. The Mouser BOM tool accepts 99 lines; kicad-fab
gives a warning above this.

DNP parts and parts with "exclude from BOM", "exclude from board" or "exclude from
position files" are not in the assembly files (JLCPCB BOM and CPL). Parts in
`assembly.exclude` are not in the assembly files, but they stay in the Mouser order.

### Configuration: kicad-fab.toml

All keys are optional. These are the defaults:

```toml
fab_house = "jlcpcb"
boards = 10                      # quantity for the Mouser order file
spares = { "0402" = 10, "0603" = 5, default = 0 }   # extra parts per line, by footprint name
wip = false
strict = false                   # true: KiCad ERC and DRC warnings are errors too
version = ""                     # release version; empty: git describe
# project = "name"               # only if the directory has more than one .kicad_pro

[fields]                         # first field with a value wins
lcsc = ["LCSC", "LCSC Part #", "JLCPCB Part #"]
mouser = ["Mouser", "Mouser Part Number"]
mpn = ["MPN", "Manufacturer Part Number"]
manufacturer = ["Manufacturer"]
description = ["Description"]
source = ["Source"]
alt_prefix = "Alt "              # Alt LCSC, Alt Mouser, Alt MPN, Alt Manufacturer
correction = "JLCPCB_CORRECTION" # rotation and offset correction, KiKit format "x;y;rotation"

[assembly]
exclude = []                     # references the assembler must not place, for example ["J1", "J2"]
sides = "auto"                   # auto, both, top or bottom

[panel]
file = "panel.json"              # KiKit preset; no file: no panel
vcut_layer = "User.Comments"     # layer of the V-cuts in KiCad (Cmts.User)
designators = "unique"           # "unique": C1_1..C1_n in the panel CPL and BOM; "repeat": C1 n times
ignore = []                      # panel check rules that only give a warning, for example ["copper_edge"]

[render]
args = ["--quality", "high", "--background", "transparent", "-w", "1440", "-h", "1080"]
```

Alt fields have one value per alternate, separated by commas. Use `-` for no value,
for example `Alt LCSC = "C100006, -"`.

### JLCPCB rules

kicad-fab follows these JLCPCB rules (from the JLCPCB KiCad guide, the Gerber
preparation page, the ordering guide and the V-cut and panel standards, 2026-11):

Gerbers and drill files:

* Protel file extensions as KiCad writes them (`gtl`, `g1`, `g2`, ..., `gbl`, `gts`,
  `gbs`, `gto`, `gbo`, `gtp`, `gbp`), one outline file (`gm1`, Edge.Cuts), no other
  mechanical or user layer files, netlist attributes, format 4.6, mm.
* Excellon drill files, mm, decimal format, PTH and NPTH in separate files.
* Gerbers, drill files and positions use one origin: the lower left corner of the board (or panel) outline. All coordinates are positive.

V-cuts:

* JLCPCB reads V-cuts only from the outline layer. A V-cut line in a separate
  `CmtUser` Gerber is ignored, and the panel arrives without V-cuts.
* In KiCad the V-cuts stay on their own layer (`vcut_layer`), so that the DRC of the
  panel passes. For the panel Gerbers, kicad-fab moves the V-cut lines and their
  "V-CUT" labels into the outline Gerber. It does not plot the V-cut layer.
* The panel check: lines are straight, horizontal or vertical, and cross the whole
  panel (no jump V-cut), max. 25 per axis, parallel lines >= 2 mm apart, copper
  >= 0.4 mm from the centre line (warning below 1.0 mm), board thickness >= 0.6 mm.

Panel for SMT assembly ("Panel by Customer"):

* Panel 70 x 70 mm to 250 x 250 mm, rails >= 5 mm.
* 3 or 4 fiducials on each assembled side: 1 mm copper, 2 mm mask opening, centre
  >= 3.85 mm from the panel edge.
* NPTH tooling holes: 3 holes of 1.152 mm (KiKit `:jlcTooling`) or 2 mm (warning otherwise).
* Mouse bites: holes 0.5 to 0.8 mm, 0.2 to 0.3 mm between the holes; routed gap >= 2 mm.
* Copper >= 0.2 mm from a routed edge.

`kicad-fab check` writes the results to `reports/panel-check.json` and fails on the
hard rules. The warnings do not fail.

Assembly files and panel designators:

* KiKit makes the panel with `renameref = "{orig}"`. kicad-fab sets this and gives
  a warning if `panel.json` has another value. Thus the silkscreen of every board
  copy shows the original references (C1, not C1_0).
* JLCPCB accepts each designator only once. With `designators = "unique"`, the panel
  CPL and panel BOM rename the parts only in the CSV files: C1 on copy n is `C1_n`
  (copies in rows from the top left). The PCB is not changed.
* The BOM comes from the schematic of the single board. The panel positions come
  from the panel PCB. kicad-fab finds the copies from the footprint positions, not
  from the references.
* Positions use the KiKit logic: relative to the aux origin, Y up, bottom side
  rotation mirrored, `JLCPCB_CORRECTION` applied.

### CI for board repositories

The repository has two reusable workflows.

`board.yml` makes all files and uploads `build/` as an artifact:

```yaml
# .github/workflows/fab.yml in a board repository
name: Fabrication files
on:
  push:
  pull_request:
jobs:
  fab:
    uses: slintak/kicad-builder/.github/workflows/board.yml@main
    with:
      image: ghcr.io/slintak/kicad-builder:10.0.8   # optional
      directory: .                                  # optional, directory of the .kicad_pro
```

`release-board.yml` runs on a tag on the default branch. It makes all files and a
GitHub release with the renders in the release text, the commits since the previous
tag, and the release ZIP (Gerbers of the board and the panel, BOMs, CPLs, STEP,
renders, schematic PDF, manifest):

```yaml
# .github/workflows/release.yml in a board repository
name: Release
on:
  push:
    tags: ["v*"]
jobs:
  release:
    uses: slintak/kicad-builder/.github/workflows/release-board.yml@main
    permissions:
      contents: write
```

A release of a board with `DO-NOT-ORDER.txt` (work in progress or check errors) is
marked as a pre-release.

### Tests

The tests use a small demo board in `tests/projects/demo` (4 layers, 2 x 2 panel
with V-cuts). The script `tools/make_demo_board.py` makes it (`make demo`). The
tests run in the image:

```
make build TAGS=dev
make test TAG=dev
```

## Versions

The `latest` tag and the `10.0.8` tag contain these versions:

| Tool | Version |
| --- | --- |
| Ubuntu | 26.04 LTS |
| KiCad | 10.0.8 (PPA `kicad/kicad-10.0-releases`) |
| Python | 3.14 (Ubuntu package) |
| kicad-fab | 1.0.2 (this repository) |
| KiKit | 1.8.1 and later fixes from master, commit `2aa564ff6019560e18553b2c7d16c875fa417d63` |
| PcbDraw | fork `slintak/PcbDraw`, commit `3dd07c63179172746e9f28facd8800814c2185a1` |
| mistune | 2.0.5 |
| jinja2-cli | 1.0.1 |
| Markdown | 3.10.3 |
| PyYAML | 6.0.3 |
| pytest | 9.0.2 |
| Pillow | from Ubuntu (python3-pil) |

The KiKit commit has fixes for KiCad 10 that are not in a KiKit release yet:

* A workaround for the KiCad SWIG iterator change.
* KiKit keeps KiCad 10 variants in generated panels.
* Tab fillets on curved and finely segmented board outlines, and faster panels.

Image tags: `latest` (from the main branch), `<KiCad version>` (for example `10.0.8`)
and `<KiCad version>-<kicad-fab version>` (for example `10.0.8-1.0.2`, from the tag
`v1.0.2`). Use a version tag in CI if you want the same tools in every build.
Each week, a CI job tries the newest KiCad 10 package and KiKit release and opens
an issue if the tests fail.

## Python packages

Ubuntu 24.04 and newer mark the system Python as "externally managed" (PEP 668).
The image installs KiKit and PcbDraw into the system Python. They need the `pcbnew`
module from the KiCad package. The image sets `PIP_BREAK_SYSTEM_PACKAGES=1`.
Thus `pip install` works in your CI job without extra flags.

## Included tools

* KiCad 10.0.8, with the symbol, footprint and 3D model libraries
* kicad-fab
* KiKit
* PcbDraw
* Python 3, pip, pytest, Markdown and jinja2-cli
* LaTeX (pdflatex, latexmk, pdfjam) and pandoc
* xsltproc, rsync, cmark, librsvg2-bin, xz-utils and more
