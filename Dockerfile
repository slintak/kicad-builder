FROM ubuntu:22.04

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends software-properties-common gnupg wget git curl sudo python3-pip make \
    cmark rsync xsltproc librsvg2-bin libnng1 libjpeg-dev zlib1g-dev \
    texlive-latex-base texlive-fonts-recommended texlive-latex-recommended texlive-latex-extra \
    texlive-extra-utils texlive-science texlive-lang-european latexmk fonts-freefont-ttf \
    build-essential pandoc && \
    rm -rf /var/lib/apt/lists/*

# Install KiCAD 10.0
RUN add-apt-repository --yes ppa:kicad/kicad-10.0-releases && \
    apt-get update && \
    apt-get install -y --no-install-recommends kicad kicad-footprints kicad-packages3d kicad-symbols && \
    rm -rf /var/lib/apt/lists/*

# Install KiKit and PcbDraw
RUN pip3 install --no-cache-dir kikit mistune==2.0.5 && \
    pip3 install --no-cache-dir jinja2-cli markdown && \
    pip3 install --no-cache-dir git+https://github.com/slintak/PcbDraw@master

# Add custom fonts
COPY ./fonts/* /usr/share/fonts/truetype/
RUN fc-cache -fv

# Default workdir
WORKDIR /workspace
