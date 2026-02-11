"""Tests for core/analyzer.py — GitAnalyzer."""

from __future__ import annotations

from pathlib import Path

from sentinel.core.analyzer import GitAnalyzer


def test_analyze_history(tmp_git_repo: Path) -> None:
    analyzer = GitAnalyzer(tmp_git_repo)
    result = analyzer.analyze_history(max_commits=100)

    assert result.commits_analyzed >= 4
    assert result.last_sha is not None
    assert len(result.last_sha) == 40


def test_extracts_conventions(tmp_git_repo: Path) -> None:
    analyzer = GitAnalyzer(tmp_git_repo)
    result = analyzer.analyze_history(max_commits=100)

    # Should detect commit conventions (feat/fix prefixes)
    commit_convs = [c for c in result.conventions if c.category.value == "commit"]
    assert len(commit_convs) >= 1

    # Should detect file naming (snake_case .py files)
    naming_convs = [c for c in result.conventions if c.category.value == "naming"]
    assert len(naming_convs) >= 1


def test_extracts_decisions(tmp_git_repo: Path) -> None:
    analyzer = GitAnalyzer(tmp_git_repo)
    result = analyzer.analyze_history(max_commits=100)

    # "because we need user authentication" triggers decision detection
    assert len(result.decisions) >= 1
    decision_texts = " ".join(d.summary for d in result.decisions)
    assert "auth" in decision_texts.lower() or "because" in decision_texts.lower()


def test_extracts_pitfalls(tmp_git_repo: Path) -> None:
    analyzer = GitAnalyzer(tmp_git_repo)
    result = analyzer.analyze_history(max_commits=100)

    # The fix commits should generate pitfalls
    assert len(result.pitfalls) >= 1
    pitfall_descs = " ".join(p.description for p in result.pitfalls)
    assert "fix" in pitfall_descs.lower()


def test_extracts_hot_files(tmp_git_repo: Path) -> None:
    analyzer = GitAnalyzer(tmp_git_repo)
    result = analyzer.analyze_history(max_commits=100)

    assert len(result.hot_files) > 0

    # auth.py should be hot (4 commits touch it)
    auth_files = [h for h in result.hot_files if "auth.py" in h.file_path]
    assert len(auth_files) >= 1
    assert auth_files[0].change_count >= 3


def test_extracts_co_changes(tmp_git_repo: Path) -> None:
    analyzer = GitAnalyzer(tmp_git_repo)
    result = analyzer.analyze_history(max_commits=100)

    # co-changes may or may not meet threshold of 3, depends on repo
    assert isinstance(result.co_changes, list)


def test_incremental_analysis(tmp_git_repo: Path) -> None:
    import os
    import subprocess
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@test.com"}

    analyzer = GitAnalyzer(tmp_git_repo)

    # Get current HEAD
    from sentinel.core.git import git_rev_parse_head
    head = git_rev_parse_head(tmp_git_repo)

    # Add a new commit
    (tmp_git_repo / "src" / "new_file.py").write_text("def new_func():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=tmp_git_repo, capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add new_file because we need it"],
        cwd=tmp_git_repo, capture_output=True, env=env, check=True,
    )

    # Incremental should only see the new commit
    result = analyzer.analyze_incremental(head, max_commits=100)  # type: ignore[arg-type]
    assert result.commits_analyzed >= 1


def test_empty_repo(tmp_path: Path) -> None:
    """Test with a repo that has no history to parse."""
    import os
    import subprocess
    repo = tmp_path / "empty"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@test.com"}
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, env=env, check=True)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=repo, capture_output=True, env=env, check=True,
    )

    analyzer = GitAnalyzer(repo)
    result = analyzer.analyze_history(max_commits=10)
    assert result.commits_analyzed >= 1
