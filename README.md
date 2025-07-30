# kicad-builder Docker Image

A ready-to-use Docker image for building, documenting, and rendering static
websites and manufacturing files for KiCAD hardware projects. This container
is designed for both CI/CD environments (such as GitHub Actions) and manual
use by hardware developers who want reproducible, automated documentation
and manufacturing outputs.

## Features

- Ubuntu 22.04 base
- KiCAD 9.x (via official PPA)
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

Included tools

* KiCAD 9.x
* KiKit
* PcbDraw (latest, from source)
* Python 3, pip, and Markdown
* jinja2-cli
* xsltproc, rsync, latexmk, cmark, librsvg2-bin, and more
