"""Messages to stderr. Standard library only."""

from __future__ import annotations

import sys

PREFIX = "kicad-fab"


def info(message: str) -> None:
    print(f"{PREFIX}: {message}", file=sys.stderr, flush=True)


def warn(message: str) -> None:
    print(f"{PREFIX}: warning: {message}", file=sys.stderr, flush=True)


def error(message: str) -> None:
    print(f"{PREFIX}: error: {message}", file=sys.stderr, flush=True)
