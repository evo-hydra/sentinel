"""Cross-project knowledge sharing — export and import anonymized patterns."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sentinel import __version__
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import KnowledgeSource

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    """Summary of a cross-project import operation."""
    conventions_imported: int = 0
    pitfalls_imported: int = 0
    duplicates_skipped: int = 0


def export_patterns(store: KnowledgeStore) -> dict:
    """Export anonymized knowledge for cross-project sharing.

    Strips: commit SHAs, authors, file paths, evidence lists.
    Keeps: pattern/description, category, severity, confidence, frequency.
    """
    project_name = store.get_meta("project_name", "unknown")
    source_hash = hashlib.sha256(project_name.encode()).hexdigest()[:16]

    conventions = []
    for c in store.get_conventions():
        conventions.append({
            "category": c.category.value,
            "pattern": c.pattern,
            "description": c.description,
            "confidence": c.confidence,
            "frequency": c.frequency,
        })

    pitfalls = []
    for p in store.get_pitfalls():
        pitfalls.append({
            "category": p.category.value,
            "severity": p.severity.value,
            "description": p.description,
            "how_to_prevent": p.how_to_prevent,
            "frequency": p.frequency,
        })

    return {
        "conventions": conventions,
        "pitfalls": pitfalls,
        "metadata": {
            "export_date": datetime.now(tz=timezone.utc).isoformat(),
            "sentinel_version": __version__,
            "source_hash": source_hash,
        },
    }


def import_patterns(store: KnowledgeStore, data: dict) -> ImportResult:
    """Import cross-project patterns into the local knowledge base.

    Deduplicates by exact match on normalized description text.
    Caps imported confidence at 0.3.
    """
    from sentinel.models.enums import ConventionCategory, PitfallCategory, Severity
    from sentinel.models.knowledge import Convention, Pitfall

    result = ImportResult()

    # Build set of existing descriptions for dedup
    existing_conv_descs = {
        c.description.lower().strip() for c in store.get_conventions()
    }
    existing_pit_descs = {
        p.description.lower().strip() for p in store.get_pitfalls()
    }

    for c_data in data.get("conventions", []):
        desc = c_data.get("description", "").lower().strip()
        if desc in existing_conv_descs:
            result.duplicates_skipped += 1
            continue

        try:
            category = ConventionCategory(c_data.get("category", "naming"))
        except ValueError:
            category = ConventionCategory.NAMING

        conv = Convention(
            category=category,
            pattern=c_data.get("pattern", ""),
            description=c_data.get("description", ""),
            confidence=min(c_data.get("confidence", 0.3), 0.3),
            frequency=c_data.get("frequency", 1),
            source=KnowledgeSource.CROSS_PROJECT,
        )
        store.add_convention(conv)
        existing_conv_descs.add(desc)
        result.conventions_imported += 1

    for p_data in data.get("pitfalls", []):
        desc = p_data.get("description", "").lower().strip()
        if desc in existing_pit_descs:
            result.duplicates_skipped += 1
            continue

        try:
            pit_category = PitfallCategory(p_data.get("category", "bug"))
        except ValueError:
            pit_category = PitfallCategory.BUG
        try:
            severity = Severity(p_data.get("severity", "medium"))
        except ValueError:
            severity = Severity.MEDIUM

        pit = Pitfall(
            category=pit_category,
            severity=severity,
            description=p_data.get("description", ""),
            how_to_prevent=p_data.get("how_to_prevent", ""),
            frequency=p_data.get("frequency", 1),
            source=KnowledgeSource.CROSS_PROJECT,
        )
        store.add_pitfall(pit)
        existing_pit_descs.add(desc)
        result.pitfalls_imported += 1

    return result
