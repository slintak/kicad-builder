# kicad-builder Docker Image

A ready-to-use Docker image for building, documenting, and rendering static
websites and manufacturing files for KiCAD hardware projects. This container
is designed for both CI/CD environments (such as GitHub Actions) and manual
use by hardware developers who want reproducible, automated documentation
and manufacturing outputs.

## Features

- Ubuntu 26.04 LTS base
- KiCad 10.0.7 (from the official KiCad PPA)
- KiKit for Gerber and PCB fabrication outputs
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

## Versions

The `latest` tag and the `10.0.7` tag contain these versions:

| Tool | Version |
| --- | --- |
| Ubuntu | 26.04 LTS |
| KiCad | 10.0.7 (PPA `kicad/kicad-10.0-releases`) |
| Python | 3.14 (Ubuntu package) |
| KiKit | 1.8.1 and later fixes from master, commit `958abd031e3d254d9777f6c192badbdb1c1c3757` |
| PcbDraw | fork `slintak/PcbDraw`, commit `3dd07c63179172746e9f28facd8800814c2185a1` |
| mistune | 2.0.5 |
| jinja2-cli | 1.0.1 |
| Markdown | 3.10.3 |
| PyYAML | 6.0.3 |
| Pillow | from Ubuntu (python3-pil) |

The KiKit commit has two fixes for KiCad 10 that are not in a KiKit release yet:

* A workaround for the KiCad SWIG iterator change.
* KiKit keeps KiCad 10 variants in generated panels.

Each image also has a version tag, for example `ghcr.io/slintak/kicad-builder:10.0.7`.
Use a version tag in CI if you want the same tools in every build.

## Python packages

Ubuntu 24.04 and newer mark the system Python as "externally managed" (PEP 668).
The image installs KiKit and PcbDraw into the system Python. They need the `pcbnew`
module from the KiCad package. The image sets `PIP_BREAK_SYSTEM_PACKAGES=1`.
Thus `pip install` works in your CI job without extra flags.

## Included tools

* KiCad 10.0.7, with the symbol, footprint and 3D model libraries
* KiKit
* PcbDraw
* Python 3, pip, Markdown and jinja2-cli
* LaTeX (pdflatex, latexmk, pdfjam) and pandoc
* xsltproc, rsync, cmark, librsvg2-bin, xz-utils and more
