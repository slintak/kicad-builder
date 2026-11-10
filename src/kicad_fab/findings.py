"""Check results. Each check gives findings with a severity."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .log import error, warn

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    source: str     # erc, drc, parity, bom, panel, config
    severity: str   # error or warning
    rule: str
    message: str

    def text(self) -> str:
        return f"{self.source} {self.rule}: {self.message}"


@dataclass
class Findings:
    items: list[Finding] = field(default_factory=list)

    def add(self, source: str, severity: str, rule: str, message: str) -> Finding:
        item = Finding(source, severity, rule, message)
        self.items.append(item)
        (error if severity == ERROR else warn)(item.text())
        return item

    def error(self, source: str, rule: str, message: str) -> Finding:
        return self.add(source, ERROR, rule, message)

    def warning(self, source: str, rule: str, message: str) -> Finding:
        return self.add(source, WARNING, rule, message)

    def extend(self, other: "Findings") -> None:
        self.items.extend(other.items)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.items if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.items if f.severity == WARNING]

    def summary(self) -> dict:
        return {
            "passed": not self.errors,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [asdict(f) for f in self.items],
        }
