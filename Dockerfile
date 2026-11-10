FROM ubuntu:26.04

# Versions. Change them here; the CI and the Makefile read them from this file.
# KICAD_VERSION=latest installs the newest package of the KiCad 10 PPA (weekly CI job).
ARG KICAD_VERSION=10.0.8
# KiKit master after v1.8.1: fixes for KiCad 10 (SWIG iterators, variants in panels)
# and for tab fillets on curved board outlines (2026-11-08).
ARG KIKIT_REF=2aa564ff6019560e18553b2c7d16c875fa417d63
ARG PCBDRAW_REF=3dd07c63179172746e9f28facd8800814c2185a1
ARG BUILDER_VERSION=1.0.0

LABEL org.opencontainers.image.title="kicad-builder" \
      org.opencontainers.image.description="KiCad, KiKit and kicad-fab: fabrication and release files for KiCad projects" \
      org.opencontainers.image.source="https://github.com/slintak/kicad-builder" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${KICAD_VERSION}-${BUILDER_VERSION}" \
      io.github.slintak.kicad-builder.kicad="${KICAD_VERSION}" \
      io.github.slintak.kicad-builder.kikit="${KIKIT_REF}" \
      io.github.slintak.kicad-builder.pcbdraw="${PCBDRAW_REF}" \
      io.github.slintak.kicad-builder.kicad-fab="${BUILDER_VERSION}"

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Ubuntu 24.04 and newer mark the system Python as "externally managed" (PEP 668).
# We install the Python tools into the system Python. Then KiKit and PcbDraw can
# import the `pcbnew` module from the KiCad package. This setting also lets CI jobs
# run `pip install` without extra flags, as on the old Ubuntu 22.04 image.
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends software-properties-common gnupg wget git curl sudo python3-pip make \
    cmark rsync xsltproc librsvg2-bin libnng1 libjpeg-dev zlib1g-dev xz-utils fontconfig \
    texlive-latex-base texlive-fonts-recommended texlive-latex-recommended texlive-latex-extra \
    texlive-extra-utils texlive-science texlive-lang-european latexmk fonts-freefont-ttf \
    build-essential pandoc && \
    rm -rf /var/lib/apt/lists/*

# Install KiCad 10 from the KiCad PPA, with an exact version.
RUN add-apt-repository --yes ppa:kicad/kicad-10.0-releases && \
    apt-get update && \
    if [ "${KICAD_VERSION}" = "latest" ]; then pin=""; else pin="=${KICAD_VERSION}*"; fi && \
    apt-get install -y --no-install-recommends "kicad${pin}" "kicad-footprints${pin}" \
        "kicad-packages3d${pin}" "kicad-symbols${pin}" && \
    rm -rf /var/lib/apt/lists/* && \
    installed="$(kicad-cli version)" && echo "KiCad ${installed}" && \
    if [ "${KICAD_VERSION}" != "latest" ] && [ "${installed}" != "${KICAD_VERSION}" ]; then \
        echo "error: KiCad ${installed} installed, ${KICAD_VERSION} expected" >&2; exit 1; fi

# Install KiKit and PcbDraw from exact commits.
# PcbDraw asks for Pillow~=9.0. Pillow 9 does not build on Python 3.14.
# So we use Pillow from Ubuntu (python3-pil) and install PcbDraw without its
# dependency list. The other PcbDraw dependencies are listed here.
# pytest runs the kicad-fab tests in the image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-pil && \
    rm -rf /var/lib/apt/lists/* && \
    pip3 install --no-cache-dir \
        "kikit @ git+https://github.com/yaqwsx/KiKit@${KIKIT_REF}" \
        mistune==2.0.5 jinja2-cli==1.0.1 Markdown==3.10.3 PyYAML==6.0.3 \
        svgpathtools==1.4.1 pcbnewTransition==0.5.2 PyVirtualDisplay==3.0 lxml==6.1.3 \
        pytest==9.0.2 && \
    pip3 install --no-cache-dir --no-deps \
        "pcbdraw @ git+https://github.com/slintak/PcbDraw@${PCBDRAW_REF}" && \
    rm -rf /root/.cache

# Add custom fonts
COPY ./fonts/* /usr/share/fonts/truetype/
RUN fc-cache -fv

# kicad-fab: the fabrication pipeline (see README.md).
COPY pyproject.toml README.md LICENSE /opt/kicad-fab/
COPY src /opt/kicad-fab/src
RUN pip3 install --no-cache-dir --no-deps /opt/kicad-fab && \
    rm -rf /root/.cache /opt/kicad-fab/build && \
    kicad-fab --version
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/kicad-builder-entrypoint

ENV KICAD_FAB_IMAGE="${KICAD_VERSION}-${BUILDER_VERSION}"

# Default workdir
WORKDIR /workspace

# No arguments: "kicad-fab build /workspace". Arguments: run them as a command.
ENTRYPOINT ["/usr/local/bin/kicad-builder-entrypoint"]
