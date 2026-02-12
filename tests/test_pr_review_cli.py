"""Tests for pr_review CLI — --update, --exit-code, and upsert logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from sentinel.cli.pr_review import _find_sentinel_comment, _upsert_pr_comment


def _mock_subprocess_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Create a mock subprocess.CompletedProcess."""
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


class TestFindSentinelComment:
    """Tests for _find_sentinel_comment."""

    def test_found(self, tmp_path: Path) -> None:
        """Returns comment ID when marker is found."""
        mock_result = _mock_subprocess_result(stdout="12345\n")
        with patch("sentinel.cli.pr_review.subprocess.run", return_value=mock_result):
            comment_id = _find_sentinel_comment("42", tmp_path)
        assert comment_id == "12345"

    def test_not_found_null(self, tmp_path: Path) -> None:
        """Returns None when jq produces null."""
        mock_result = _mock_subprocess_result(stdout="null\n")
        with patch("sentinel.cli.pr_review.subprocess.run", return_value=mock_result):
            comment_id = _find_sentinel_comment("42", tmp_path)
        assert comment_id is None

    def test_not_found_empty(self, tmp_path: Path) -> None:
        """Returns None when no output."""
        mock_result = _mock_subprocess_result(stdout="")
        with patch("sentinel.cli.pr_review.subprocess.run", return_value=mock_result):
            comment_id = _find_sentinel_comment("42", tmp_path)
        assert comment_id is None

    def test_not_found_error(self, tmp_path: Path) -> None:
        """Returns None when gh api fails."""
        mock_result = _mock_subprocess_result(returncode=1, stderr="Not Found")
        with patch("sentinel.cli.pr_review.subprocess.run", return_value=mock_result):
            comment_id = _find_sentinel_comment("42", tmp_path)
        assert comment_id is None


class TestUpsertPrComment:
    """Tests for _upsert_pr_comment."""

    def test_creates_when_no_existing(self, tmp_path: Path) -> None:
        """Falls through to _post_pr_comment when no existing comment."""
        with (
            patch("sentinel.cli.pr_review._get_pr_number", return_value="42"),
            patch("sentinel.cli.pr_review._find_sentinel_comment", return_value=None),
            patch("sentinel.cli.pr_review._post_pr_comment") as mock_post,
        ):
            _upsert_pr_comment("test comment", tmp_path)
        mock_post.assert_called_once_with("test comment", tmp_path)

    def test_edits_when_existing(self, tmp_path: Path) -> None:
        """Edits via PATCH when existing comment found."""
        mock_patch_result = _mock_subprocess_result()
        with (
            patch("sentinel.cli.pr_review._get_pr_number", return_value="42"),
            patch("sentinel.cli.pr_review._find_sentinel_comment", return_value="12345"),
            patch("sentinel.cli.pr_review.subprocess.run", return_value=mock_patch_result) as mock_run,
            patch("sentinel.cli.pr_review._post_pr_comment") as mock_post,
        ):
            _upsert_pr_comment("updated comment", tmp_path)

        # Should NOT have created a new comment
        mock_post.assert_not_called()

        # Should have called gh api --method PATCH
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "PATCH" in call_args
        # Comment ID should appear in the URL argument
        url_args = [a for a in call_args if "12345" in a]
        assert len(url_args) == 1


class TestExitCode:
    """Tests for --exit-code flag via CLI."""

    def test_exit_code_high_risk(self, tmp_git_repo: Path) -> None:
        """Exit code 1 when risk is HIGH."""
        import os

        from typer.testing import CliRunner

        from sentinel.cli.app import app
        from sentinel.core.pr_analyzer import PRAnalysis
        from sentinel.models.findings import Finding
        from sentinel.models.knowledge import HotFile

        runner = CliRunner()
        runner.invoke(app, ["init", str(tmp_git_repo)])

        # Mock a HIGH risk analysis
        mock_analysis = PRAnalysis(
            changed_files=["a.py"],
            hot_files_touched=[HotFile(file_path="a.py", change_count=20, churn_score=50)],
            findings=[Finding(message=f"Issue {i}") for i in range(5)],
        )

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_git_repo)
            with patch("sentinel.core.pr_analyzer.PRAnalyzer") as mock_cls:
                mock_cls.return_value.analyze_pr.return_value = mock_analysis
                result = runner.invoke(app, ["pr-review", "--base", "HEAD~1", "--exit-code"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 1

    def test_exit_code_low_risk(self, tmp_git_repo: Path) -> None:
        """Exit code 0 when risk is LOW."""
        import os

        from typer.testing import CliRunner

        from sentinel.cli.app import app

        runner = CliRunner()
        runner.invoke(app, ["init", str(tmp_git_repo)])

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_git_repo)
            result = runner.invoke(app, ["pr-review", "--base", "HEAD~1", "--exit-code"])
        finally:
            os.chdir(old_cwd)

        assert result.exit_code == 0, result.stdout
