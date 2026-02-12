"""Enumerations for Sentinel models."""

from enum import Enum


class Severity(str, Enum):
    """Severity levels for findings and pitfalls."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class KnowledgeType(str, Enum):
    """Types of knowledge entries."""
    CONVENTION = "convention"
    DECISION = "decision"
    PITFALL = "pitfall"
    PATTERN = "pattern"


class PitfallCategory(str, Enum):
    """Categories for pitfalls (learned from reverts/bug fixes)."""
    SECURITY = "security"
    PERFORMANCE = "performance"
    BUG = "bug"
    QUALITY = "quality"
    TESTING = "testing"
    COMPATIBILITY = "compatibility"


class ConventionCategory(str, Enum):
    """Categories for conventions."""
    NAMING = "naming"
    STRUCTURE = "structure"
    COMMIT = "commit"
    IMPORT = "import"
    STYLE = "style"


class FindingType(str, Enum):
    """Types of findings from verification."""
    CONVENTION_VIOLATION = "convention_violation"
    PITFALL_MATCH = "pitfall_match"
    HOT_FILE_WARNING = "hot_file_warning"
    CO_CHANGE_REMINDER = "co_change_reminder"
    NAMING_VIOLATION = "naming_violation"
    LLM_FINDING = "llm_finding"


class KnowledgeSource(str, Enum):
    """How knowledge was acquired."""
    GIT_HISTORY = "git_history"
    MANUAL = "manual"
    INFERRED = "inferred"
    CROSS_PROJECT = "cross_project"


class FeedbackOutcome(str, Enum):
    """Outcomes for feedback on knowledge entries."""
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
