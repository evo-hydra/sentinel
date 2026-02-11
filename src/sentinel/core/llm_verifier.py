"""LLMVerifier — orchestrates LLM-powered code review with project context."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sentinel.core.config import SentinelConfig
from sentinel.core.context import ContextEngine
from sentinel.core.llm_provider import LLMProvider, LLMProviderError
from sentinel.models.enums import FindingType, Severity
from sentinel.models.findings import Finding

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are Sentinel, a code reviewer with persistent memory of this project.

PROJECT KNOWLEDGE:
{context}

RULES:
- Report ONLY issues that are project-specific or genuinely concerning
- Prioritize: security > correctness > convention violations
- Reference specific conventions/pitfalls from the knowledge when relevant
- Do NOT report: style nits, missing docstrings, generic best practices
- If the file is a "hot file" — apply extra scrutiny

Respond as JSON array:
[{{"line": <int|null>, "severity": "critical|high|medium|low", "message": "...", "suggestion": "..."}}]

Return [] if no issues found.
"""

_SEVERITY_MAP = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
    "info": Severity.INFO,
}


class LLMVerifier:
    """Orchestrates LLM code review using ContextEngine for knowledge and LLMProvider for inference."""

    def __init__(
        self,
        context_engine: ContextEngine,
        provider: LLMProvider,
        config: SentinelConfig | None = None,
    ) -> None:
        self._context = context_engine
        self._provider = provider
        self._config = config or SentinelConfig()

    def build_prompts(self, file_path: Path, content: str, rel_path: str) -> tuple[str, str] | None:
        """Build (system_prompt, user_prompt) for a file. Touches DB — NOT thread-safe.

        Returns None if the file should be skipped (e.g. too large).
        """
        if len(content) > self._config.llm_max_file_size:
            logger.info("Skipping %s — exceeds max file size (%d bytes)", rel_path, len(content))
            return None

        file_ctx = self._context.file_context(rel_path)
        project_ctx = self._context.project_context()
        formatted_context = self._context.format_for_llm(file_ctx, project_ctx)

        system_prompt = _SYSTEM_PROMPT.format(context=formatted_context)
        user_prompt = f"Review this file ({rel_path}):\n\n```\n{content}\n```"
        return system_prompt, user_prompt

    def analyze_prompts(self, system_prompt: str, user_prompt: str, rel_path: str) -> list[Finding]:
        """Send pre-built prompts to the LLM and parse findings. Thread-safe (network I/O only)."""
        try:
            response = self._provider.analyze(system_prompt, user_prompt)
        except LLMProviderError as exc:
            logger.warning("LLM error for %s: %s", rel_path, exc)
            return []
        return self._parse_response(response, rel_path)

    def verify_file(self, file_path: Path, content: str, rel_path: str) -> list[Finding]:
        """Run LLM verification on a single file. Returns findings or empty list on error."""
        prompts = self.build_prompts(file_path, content, rel_path)
        if prompts is None:
            return []

        system_prompt, user_prompt = prompts
        return self.analyze_prompts(system_prompt, user_prompt, rel_path)

    def verify_files(self, file_paths: list[Path], git_root: Path) -> list[Finding]:
        """Run LLM verification on multiple files."""
        all_findings: list[Finding] = []

        for fp in file_paths:
            if not fp.exists():
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            rel_path = str(fp.relative_to(git_root)) if fp.is_absolute() else str(fp)
            findings = self.verify_file(fp, content, rel_path)
            all_findings.extend(findings)

        return all_findings

    def _parse_response(self, response: str, rel_path: str) -> list[Finding]:
        """Parse LLM response into findings. Robust against format variations."""
        items = self._extract_json(response)
        if items is None:
            if response.strip() and response.strip() != "[]":
                logger.warning("Could not parse LLM response for %s as JSON", rel_path)
            return []

        findings: list[Finding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            severity = _SEVERITY_MAP.get(
                str(item.get("severity", "medium")).lower(), Severity.MEDIUM
            )
            raw_line = item.get("line")
            line = int(raw_line) if isinstance(raw_line, (int, float)) and raw_line > 0 else None
            message = str(item.get("message", "LLM finding"))
            suggestion = str(item.get("suggestion", ""))
            findings.append(Finding(
                file_path=rel_path,
                line=line,
                severity=severity,
                finding_type=FindingType.LLM_FINDING,
                message=message,
                suggestion=suggestion,
            ))

        return findings

    @staticmethod
    def _extract_json(text: str) -> list[dict] | None:  # type: ignore[type-arg]
        """Extract JSON array from text. Handles raw JSON, markdown code blocks, etc."""
        # Try direct parse
        text = text.strip()
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

        # Try extracting from markdown code blocks
        patterns = [
            r"```json\s*\n(.*?)\n\s*```",
            r"```\s*\n(.*?)\n\s*```",
            r"\[.*\]",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    candidate = match.group(1) if match.lastindex else match.group(0)
                    result = json.loads(candidate)
                    if isinstance(result, list):
                        return result
                except (json.JSONDecodeError, ValueError, IndexError):
                    continue

        return None
