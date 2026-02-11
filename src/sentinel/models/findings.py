"""Finding and report models for hunt results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sentinel.models.enums import FindingType, Severity


@dataclass
class Finding:
    """A single issue found during verification."""
    file_path: str = ""
    line: int | None = None
    severity: Severity = Severity.MEDIUM
    finding_type: FindingType = FindingType.CONVENTION_VIOLATION
    message: str = ""
    suggestion: str = ""
    knowledge_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "line": self.line,
            "severity": self.severity.value,
            "type": self.finding_type.value,
            "message": self.message,
            "suggestion": self.suggestion,
            "knowledge_id": self.knowledge_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Finding:
        """Reconstruct a Finding from its to_dict() output."""
        try:
            severity = Severity(data["severity"]) if "severity" in data else Severity.MEDIUM
        except ValueError:
            severity = Severity.MEDIUM
        try:
            finding_type = FindingType(data["type"]) if "type" in data else FindingType.CONVENTION_VIOLATION
        except ValueError:
            finding_type = FindingType.CONVENTION_VIOLATION
        return cls(
            file_path=data.get("file_path", ""),
            line=data.get("line"),
            severity=severity,
            finding_type=finding_type,
            message=data.get("message", ""),
            suggestion=data.get("suggestion", ""),
            knowledge_id=data.get("knowledge_id"),
        )


@dataclass
class HuntReport:
    """Aggregated report from a hunt (verification) run."""
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())

    @property
    def total(self) -> int:
        return len(self.findings)

    def count_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
        return counts

    @property
    def has_critical(self) -> bool:
        return any(f.severity == Severity.CRITICAL for f in self.findings)

    @property
    def has_high(self) -> bool:
        return any(f.severity == Severity.HIGH for f in self.findings)

    @property
    def max_severity(self) -> Severity | None:
        if not self.findings:
            return None
        order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
        for sev in order:
            if any(f.severity == sev for f in self.findings):
                return sev
        return None

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "files_scanned": self.files_scanned,
            "total_findings": self.total,
            "by_severity": self.count_by_severity(),
            "timestamp": self.timestamp,
        }
