"""Domain models for static analysis findings."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StaticAnalysisFinding:
    """Represents a finding from a local static analysis tool (Bandit or Semgrep)."""
    tool: str
    file: str
    line: int
    rule_id: str
    message: str
    severity: str

    def to_hint_string(self) -> str:
        """Formats the finding as a concise hint for the LLM prompt."""
        clean_msg = self.message.strip().replace("\n", " ")
        return f"[{self.tool}] {self.file}:{self.line} ({self.rule_id}): {clean_msg}"

