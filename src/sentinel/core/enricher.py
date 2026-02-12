"""CommitEnricher — LLM-powered knowledge extraction from commit batches."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from sentinel.core.config import SentinelConfig
from sentinel.core.llm_provider import LLMProvider, LLMProviderError
from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import (
    AnalysisResult,
    Convention,
    Decision,
    Pitfall,
)

logger = logging.getLogger(__name__)

_ENRICHER_SYSTEM_PROMPT = """\
You are analyzing a batch of git commits to extract project intelligence.

For each batch, identify:

DECISIONS: Architectural choices, technology selections, design pattern adoptions,
migration decisions. Only flag genuine decisions, not routine work. Include:
- summary: One-sentence description of the decision
- rationale: Why this was decided (infer from commit message + files)
- tags: Categorize (e.g., "architecture", "dependency", "api", "database", "security")

PITFALLS: Bugs, regressions, and mistakes. Assess severity based on ACTUAL IMPACT,
not just keywords. A typo fix is LOW. A security fix is CRITICAL. Include:
- description: What went wrong
- severity: critical/high/medium/low (based on impact, not keywords)
- category: security/performance/bug/quality/testing/compatibility
- how_to_prevent: Actionable prevention advice
- code_pattern: Regex pattern to detect similar issues (if applicable), or null

CONVENTIONS: Coding standards and team practices you can infer. Only include
conventions with clear evidence. Include:
- pattern: Short identifier (e.g., "pytest-fixtures", "factory-pattern")
- description: What the convention is
- category: naming/structure/commit/import/style

Respond as JSON:
{"decisions": [...], "pitfalls": [...], "conventions": [...]}
Return empty arrays for categories with no findings.
Do NOT wrap the JSON in markdown code blocks.
"""

# Valid enum values for validation
_VALID_SEVERITIES = {s.value for s in Severity}
_VALID_PITFALL_CATEGORIES = {c.value for c in PitfallCategory}
_VALID_CONVENTION_CATEGORIES = {c.value for c in ConventionCategory}


class CommitEnricher:
    """LLM-powered knowledge extraction from commit batches."""

    def __init__(self, provider: LLMProvider, config: SentinelConfig) -> None:
        self.provider = provider
        self.batch_size = config.enrich_batch_size

    def enrich(
        self,
        commits: list[dict[str, Any]],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> AnalysisResult:
        """Process commits in batches through LLM for semantic extraction."""
        merged = AnalysisResult(commits_analyzed=len(commits))

        batches = [
            commits[i : i + self.batch_size]
            for i in range(0, len(commits), self.batch_size)
        ]

        for idx, batch in enumerate(batches):
            try:
                batch_result = self._enrich_batch(batch)
                merged.conventions.extend(batch_result.conventions)
                merged.decisions.extend(batch_result.decisions)
                merged.pitfalls.extend(batch_result.pitfalls)
            except LLMProviderError:
                logger.exception("Enrichment failed for batch %d/%d", idx + 1, len(batches))

            if on_progress:
                on_progress(idx + 1, len(batches))

        return merged

    def _enrich_batch(self, batch: list[dict[str, Any]]) -> AnalysisResult:
        """Send a batch of commits to LLM and parse structured response."""
        user_prompt = self._format_commit_batch(batch)
        response = self.provider.analyze(_ENRICHER_SYSTEM_PROMPT, user_prompt)
        return self._parse_response(response, batch)

    def _format_commit_batch(self, batch: list[dict[str, Any]]) -> str:
        """Format commits as structured text for LLM."""
        parts: list[str] = []
        for c in batch:
            files_str = ", ".join(c.get("files", [])[:20])
            body = (c.get("body", "") or "")[:300]
            parts.append(
                f"SHA: {c['sha'][:12]}\n"
                f"Author: {c.get('author', 'unknown')}\n"
                f"Date: {c.get('date', '')}\n"
                f"Subject: {c.get('subject', '')}\n"
                f"Body: {body}\n"
                f"Files: {files_str}\n"
            )
        return "---\n".join(parts)

    def _parse_response(
        self, response: str, batch: list[dict[str, Any]],
    ) -> AnalysisResult:
        """Parse LLM JSON response into knowledge models."""
        data = _extract_json_object(response)
        if data is None:
            logger.warning("Failed to parse enricher response as JSON")
            return AnalysisResult()

        result = AnalysisResult()

        # Parse decisions
        for raw in data.get("decisions", []):
            if not isinstance(raw, dict) or not raw.get("summary"):
                continue
            tags = raw.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            result.decisions.append(Decision(
                summary=str(raw["summary"]),
                rationale=str(raw.get("rationale", "")),
                tags=[str(t) for t in tags if isinstance(t, str)],
                source=KnowledgeSource.INFERRED,
            ))

        # Parse pitfalls
        for raw in data.get("pitfalls", []):
            if not isinstance(raw, dict) or not raw.get("description"):
                continue
            severity_str = str(raw.get("severity", "medium")).lower()
            if severity_str not in _VALID_SEVERITIES:
                severity_str = "medium"
            cat_str = str(raw.get("category", "bug")).lower()
            if cat_str not in _VALID_PITFALL_CATEGORIES:
                cat_str = "bug"
            result.pitfalls.append(Pitfall(
                category=PitfallCategory(cat_str),
                severity=Severity(severity_str),
                description=str(raw["description"]),
                code_pattern=raw.get("code_pattern"),
                how_to_prevent=str(raw.get("how_to_prevent", "")),
                source=KnowledgeSource.INFERRED,
            ))

        # Parse conventions
        for raw in data.get("conventions", []):
            if not isinstance(raw, dict) or not raw.get("pattern"):
                continue
            cat_str = str(raw.get("category", "style")).lower()
            if cat_str not in _VALID_CONVENTION_CATEGORIES:
                cat_str = "style"
            result.conventions.append(Convention(
                category=ConventionCategory(cat_str),
                pattern=str(raw["pattern"]),
                description=str(raw.get("description", "")),
                source=KnowledgeSource.INFERRED,
            ))

        return result


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from LLM response text."""
    text = text.strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting from markdown code blocks
    patterns = [
        r"```json\s*\n(.*?)\n\s*```",
        r"```\s*\n(.*?)\n\s*```",
        r"\{.*\}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(1) if match.lastindex else match.group(0))
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                continue

    return None
