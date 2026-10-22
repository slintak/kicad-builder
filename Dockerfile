FROM ubuntu:26.04

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

# Install KiCad 10.0
RUN add-apt-repository --yes ppa:kicad/kicad-10.0-releases && \
    apt-get update && \
    apt-get install -y --no-install-recommends kicad kicad-footprints kicad-packages3d kicad-symbols && \
    rm -rf /var/lib/apt/lists/*

# Install KiKit and PcbDraw from exact commits.
# KiKit master after v1.8.1 has fixes for KiCad 10 (SWIG iterators, variants in panels).
ARG KIKIT_REF=958abd031e3d254d9777f6c192badbdb1c1c3757
ARG PCBDRAW_REF=3dd07c63179172746e9f28facd8800814c2185a1
# PcbDraw asks for Pillow~=9.0. Pillow 9 does not build on Python 3.14.
# So we use Pillow from Ubuntu (python3-pil) and install PcbDraw without its
# dependency list. The other PcbDraw dependencies are listed here.
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-pil && \
    rm -rf /var/lib/apt/lists/* && \
    pip3 install --no-cache-dir \
        "kikit @ git+https://github.com/yaqwsx/KiKit@${KIKIT_REF}" \
        mistune==2.0.5 jinja2-cli==1.0.1 Markdown==3.10.3 PyYAML==6.0.3 \
        svgpathtools==1.4.1 pcbnewTransition==0.5.2 PyVirtualDisplay==3.0 lxml==6.1.3 && \
    pip3 install --no-cache-dir --no-deps \
        "pcbdraw @ git+https://github.com/slintak/PcbDraw@${PCBDRAW_REF}" && \
    rm -rf /root/.cache

# Add custom fonts
COPY ./fonts/* /usr/share/fonts/truetype/
RUN fc-cache -fv

# Default workdir
WORKDIR /workspace
