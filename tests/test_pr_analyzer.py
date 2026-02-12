"""Tests for PR analyzer and formatter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from sentinel.core.config import SentinelConfig
from sentinel.core.knowledge import KnowledgeStore
from sentinel.core.pr_analyzer import PRAnalysis, PRAnalyzer
from sentinel.core.pr_formatter import SENTINEL_COMMENT_MARKER, format_pr_comment
from sentinel.models.knowledge import CoChange, HotFile


def test_analyze_pr_detects_changed_files(knowledge_store: KnowledgeStore, tmp_path: Path) -> None:
    """Mock git diff, verify file list."""
    config = SentinelConfig()
    git_root = tmp_path / "repo"
    git_root.mkdir()

    analyzer = PRAnalyzer(knowledge_store, config, git_root)

    mock_result = type("R", (), {"returncode": 0, "stdout": "src/auth.py\nsrc/utils.py\n", "stderr": ""})()
    with patch("sentinel.core.pr_analyzer.safe_git_command", return_value=mock_result):
        analysis = analyzer.analyze_pr(base="main", head="HEAD")

    assert analysis.changed_files == ["src/auth.py", "src/utils.py"]


def test_hot_files_flagged_in_pr(knowledge_store: KnowledgeStore, tmp_path: Path) -> None:
    """PR touching hot file appears in analysis."""
    knowledge_store.upsert_hot_file(HotFile(
        file_path="src/auth.py", change_count=20, bug_fix_count=5, revert_count=2, churn_score=45,
    ))

    config = SentinelConfig()
    git_root = tmp_path / "repo"
    git_root.mkdir()
    # Create the file so verify_files can read it
    (git_root / "src").mkdir(parents=True)
    (git_root / "src" / "auth.py").write_text("def login(): pass\n")

    analyzer = PRAnalyzer(knowledge_store, config, git_root)

    mock_result = type("R", (), {"returncode": 0, "stdout": "src/auth.py\n", "stderr": ""})()
    with patch("sentinel.core.pr_analyzer.safe_git_command", return_value=mock_result):
        analysis = analyzer.analyze_pr()

    assert len(analysis.hot_files_touched) == 1
    assert analysis.hot_files_touched[0].file_path == "src/auth.py"


def test_missing_co_changes(knowledge_store: KnowledgeStore, tmp_path: Path) -> None:
    """Changed file's partner not in PR triggers reminder."""
    knowledge_store.upsert_co_change(CoChange(
        file_a="src/auth.py", file_b="tests/test_auth.py", change_count=10,
    ))

    config = SentinelConfig()
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / "src").mkdir(parents=True)
    (git_root / "src" / "auth.py").write_text("def login(): pass\n")

    analyzer = PRAnalyzer(knowledge_store, config, git_root)

    # Only auth.py changed, not test_auth.py
    mock_result = type("R", (), {"returncode": 0, "stdout": "src/auth.py\n", "stderr": ""})()
    with patch("sentinel.core.pr_analyzer.safe_git_command", return_value=mock_result):
        analysis = analyzer.analyze_pr()

    assert len(analysis.missing_co_changes) >= 1
    missing_partners = [mc[1] for mc in analysis.missing_co_changes]
    assert "tests/test_auth.py" in missing_partners


def test_format_pr_comment_sections() -> None:
    """Markdown output contains expected headers."""
    analysis = PRAnalysis(
        changed_files=["src/auth.py", "src/utils.py"],
        findings=[],
        hot_files_touched=[HotFile(
            file_path="src/auth.py", change_count=20, bug_fix_count=5,
            revert_count=2, churn_score=45,
        )],
        missing_co_changes=[("src/auth.py", "tests/test_auth.py", 10)],
        relevant_pitfalls=[],
        relevant_decisions=[],
    )

    comment = format_pr_comment(analysis)
    assert "## Sentinel PR Review" in comment
    assert "### Risk Assessment" in comment
    assert "### Co-Change Reminders" in comment
    assert "src/auth.py" in comment


def test_format_pr_comment_empty() -> None:
    """No findings produces clean output."""
    analysis = PRAnalysis(changed_files=["README.md"])

    comment = format_pr_comment(analysis)
    assert "## Sentinel PR Review" in comment
    assert "1 files changed" in comment
    assert "0 findings" in comment
    assert "Risk: LOW" in comment


def test_pr_analysis_to_dict() -> None:
    """PRAnalysis.to_dict() serializes properly."""
    analysis = PRAnalysis(
        changed_files=["a.py"],
        hot_files_touched=[HotFile(file_path="a.py", change_count=5, churn_score=10)],
    )
    d = analysis.to_dict()
    assert d["changed_files"] == ["a.py"]
    assert len(d["hot_files_touched"]) == 1
    # Ensure JSON-serializable
    json.dumps(d)


def test_format_pr_comment_with_findings() -> None:
    """Comment with findings should include severity and suggestions."""
    from sentinel.models.enums import FindingType, Severity
    from sentinel.models.findings import Finding

    analysis = PRAnalysis(
        changed_files=["src/auth.py"],
        findings=[
            Finding(
                file_path="src/auth.py", line=10, severity=Severity.HIGH,
                finding_type=FindingType.PITFALL_MATCH,
                message="SQL injection risk", suggestion="Use parameterized queries",
            ),
        ],
    )

    comment = format_pr_comment(analysis)
    assert "### Findings" in comment
    assert "[HIGH]" in comment
    assert "SQL injection risk" in comment
    assert "Use parameterized queries" in comment


def test_format_pr_comment_with_context() -> None:
    """Comment with decisions and pitfalls in relevant context."""
    from sentinel.models.enums import PitfallCategory, Severity
    from sentinel.models.knowledge import Decision, Pitfall

    analysis = PRAnalysis(
        changed_files=["src/api.py"],
        relevant_decisions=[Decision(summary="Use FastAPI for async support")],
        relevant_pitfalls=[Pitfall(
            category=PitfallCategory.SECURITY, severity=Severity.HIGH,
            description="SQL injection risk",
        )],
    )

    comment = format_pr_comment(analysis)
    assert "### Relevant Context" in comment
    assert "Use FastAPI" in comment
    assert "SQL injection risk" in comment


def test_format_pr_comment_high_risk() -> None:
    """Many findings should produce HIGH risk."""
    from sentinel.models.findings import Finding

    analysis = PRAnalysis(
        changed_files=["a.py", "b.py"],
        findings=[Finding(message=f"Issue {i}") for i in range(5)],
    )

    comment = format_pr_comment(analysis)
    assert "Risk: HIGH" in comment


def test_analyze_pr_empty_diff(knowledge_store: KnowledgeStore, tmp_path: Path) -> None:
    """Empty diff should return empty analysis."""
    config = SentinelConfig()
    git_root = tmp_path / "repo"
    git_root.mkdir()

    analyzer = PRAnalyzer(knowledge_store, config, git_root)

    mock_result = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    with patch("sentinel.core.pr_analyzer.safe_git_command", return_value=mock_result):
        analysis = analyzer.analyze_pr()

    assert analysis.changed_files == []
    assert analysis.findings == []


def test_analyze_pr_git_diff_failure(knowledge_store: KnowledgeStore, tmp_path: Path) -> None:
    """Git diff failure should return empty file list."""
    config = SentinelConfig()
    git_root = tmp_path / "repo"
    git_root.mkdir()

    analyzer = PRAnalyzer(knowledge_store, config, git_root)

    mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "fatal: bad revision"})()
    with patch("sentinel.core.pr_analyzer.safe_git_command", return_value=mock_result):
        analysis = analyzer.analyze_pr()

    assert analysis.changed_files == []


def test_pr_review_cli_json(tmp_git_repo: Path) -> None:
    """CLI pr-review --json should produce structured output."""
    import os

    from typer.testing import CliRunner

    from sentinel.cli.app import app

    runner = CliRunner()
    runner.invoke(app, ["init", str(tmp_git_repo)])

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_git_repo)
        result = runner.invoke(app, ["pr-review", "--base", "HEAD~1", "--json"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert "changed_files" in data


def test_pr_review_cli_human_output(tmp_git_repo: Path) -> None:
    """CLI pr-review without --json should produce markdown."""
    import os

    from typer.testing import CliRunner

    from sentinel.cli.app import app

    runner = CliRunner()
    runner.invoke(app, ["init", str(tmp_git_repo)])

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_git_repo)
        result = runner.invoke(app, ["pr-review", "--base", "HEAD~1"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.stdout
    assert "Sentinel PR Review" in result.stdout


def test_pr_review_cli_not_git_repo(tmp_path: Path) -> None:
    """CLI pr-review outside git repo should error."""
    import os

    from typer.testing import CliRunner

    from sentinel.cli.app import app

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(app, ["pr-review"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 1


def test_pr_review_cli_no_sentinel(tmp_git_repo: Path) -> None:
    """CLI pr-review without sentinel init should error."""
    import os

    from typer.testing import CliRunner

    from sentinel.cli.app import app

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_git_repo)
        result = runner.invoke(app, ["pr-review"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 1


# --- risk_level property tests ---


def test_pr_analysis_risk_level_high_hot_files() -> None:
    """Hot files touched → HIGH risk."""
    analysis = PRAnalysis(
        changed_files=["a.py"],
        hot_files_touched=[HotFile(file_path="a.py", change_count=20, churn_score=50)],
    )
    assert analysis.risk_level == "HIGH"


def test_pr_analysis_risk_level_high_many_findings() -> None:
    """More than 3 findings → HIGH risk."""
    from sentinel.models.findings import Finding

    analysis = PRAnalysis(
        changed_files=["a.py"],
        findings=[Finding(message=f"Issue {i}") for i in range(4)],
    )
    assert analysis.risk_level == "HIGH"


def test_pr_analysis_risk_level_medium() -> None:
    """1-3 findings, no hot files → MEDIUM risk."""
    from sentinel.models.findings import Finding

    analysis = PRAnalysis(
        changed_files=["a.py"],
        findings=[Finding(message="Issue 1")],
    )
    assert analysis.risk_level == "MEDIUM"


def test_pr_analysis_risk_level_low() -> None:
    """No findings → LOW risk."""
    analysis = PRAnalysis(changed_files=["a.py"])
    assert analysis.risk_level == "LOW"


def test_pr_analysis_risk_in_to_dict() -> None:
    """risk_level appears in to_dict() output."""
    analysis = PRAnalysis(changed_files=["a.py"])
    d = analysis.to_dict()
    assert "risk_level" in d
    assert d["risk_level"] == "LOW"


# --- comment marker tests ---


def test_format_pr_comment_has_marker() -> None:
    """Comment starts with the HTML marker."""
    analysis = PRAnalysis(changed_files=["a.py"])
    comment = format_pr_comment(analysis)
    assert comment.startswith(SENTINEL_COMMENT_MARKER)


def test_format_pr_comment_marker_is_html_comment() -> None:
    """Marker is a valid HTML comment (invisible in rendered markdown)."""
    assert SENTINEL_COMMENT_MARKER.startswith("<!--")
    assert SENTINEL_COMMENT_MARKER.endswith("-->")
