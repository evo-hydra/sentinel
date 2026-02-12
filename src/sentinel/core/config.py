"""Sentinel configuration using Pydantic."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from sentinel.models.enums import Severity


class SentinelConfig(BaseModel):
    """Configuration for Sentinel behavior."""

    # Minimum severity to report
    min_severity: Severity = Severity.LOW

    # Fail CI if findings at or above this severity
    fail_on: Severity | None = None

    # Maximum commits to analyze
    max_commits: int = 500

    # Co-change threshold (minimum co-occurrences to report)
    co_change_threshold: int = 3

    # Hot file churn score threshold
    hot_file_threshold: float = 10.0

    # PR review base branch
    pr_base_branch: str = "main"

    # Paths to exclude from scanning
    exclude_patterns: list[str] = Field(default_factory=lambda: [
        ".git", ".sentinel", "__pycache__", "node_modules", ".venv",
        "*.pyc", "*.egg-info", "dist", "build",
    ])

    # File extensions to scan
    scan_extensions: list[str] = Field(default_factory=lambda: [
        ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
        ".java", ".rb", ".php", ".c", ".cpp", ".h",
    ])

    # LLM settings (used by hunt --llm)
    llm_provider: str = "ollama"  # ollama, anthropic, openai, gemini, grok
    llm_model: str = "deepseek-coder:6.7b-instruct-q4_K_M"
    llm_max_tokens: int = 4096
    llm_timeout: float = 120.0
    llm_max_file_size: int = 50_000  # skip files larger than 50KB
    llm_ollama_url: str = "http://localhost:11434"
    llm_workers: int = 2

    # Enrichment settings (LLM-powered knowledge extraction)
    enrich_provider: str = "anthropic"
    enrich_model: str = "claude-haiku-4-5-20251001"
    enrich_batch_size: int = 25

    # Embedding settings (semantic search)
    embed_provider: str = "ollama"
    embed_model: str = "nomic-embed-text"
    embed_batch_size: int = 50

    @classmethod
    def load(cls, sentinel_dir: Path) -> SentinelConfig:
        """Load config from .sentinel/config.yaml if it exists."""
        config_file = sentinel_dir / "config.yaml"
        if config_file.exists():
            import yaml
            with open(config_file) as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()

    def should_scan(self, file_path: Path) -> bool:
        """Check if a file should be scanned."""
        name = str(file_path)
        for pattern in self.exclude_patterns:
            if pattern in name:
                return False
        return file_path.suffix in self.scan_extensions
