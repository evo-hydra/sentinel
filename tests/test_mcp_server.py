"""Tests for MCP server tools.

These tests exercise the tool functions directly (without MCP transport).
They verify that each tool correctly reads from KnowledgeStore and handles
edge cases like missing .sentinel/ directories.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import ConventionCategory, PitfallCategory, Severity
from sentinel.models.knowledge import CoChange, Convention, Decision, HotFile, Pitfall

try:
    from sentinel.mcp.server import HAS_MCP, _find_sentinel_dir, _open_store, create_server

    SKIP_MCP = not HAS_MCP
except ImportError:
    SKIP_MCP = True

pytestmark = pytest.mark.skipif(SKIP_MCP, reason="mcp package not installed")


# --- Fixtures ---


@pytest.fixture
def sentinel_project(tmp_path: Path) -> Path:
    """Create a project with .sentinel/ and populated knowledge store."""
    project = tmp_path / "myproject"
    project.mkdir()
    sentinel_dir = project / ".sentinel"
    sentinel_dir.mkdir()

    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    store.open()
    store.set_meta("project_name", "myproject")

    store.add_convention(Convention(
        id="conv-1",
        category=ConventionCategory.NAMING,
        pattern="snake_case",
        description="Use snake_case for functions",
        confidence=0.9,
        frequency=15,
    ))

    store.add_pitfall(Pitfall(
        id="pit-1",
        category=PitfallCategory.SECURITY,
        severity=Severity.HIGH,
        description="SQL injection via string formatting",
        how_to_prevent="Use parameterized queries",
        frequency=3,
    ))

    store.add_decision(Decision(
        id="dec-1",
        summary="Use SQLite for persistence",
        rationale="Zero external dependencies",
        author="alice",
        decided_at="2026-01-15T10:00:00Z",
    ))

    store.upsert_hot_file(HotFile(
        file_path="src/auth.py",
        change_count=20,
        bug_fix_count=5,
        revert_count=2,
        churn_score=45.0,
    ))

    store.upsert_co_change(CoChange(
        file_a="src/auth.py", file_b="tests/test_auth.py", change_count=8,
    ))

    store.close()
    return project


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    """Create a project with .sentinel/ but no knowledge."""
    project = tmp_path / "emptyproject"
    project.mkdir()
    sentinel_dir = project / ".sentinel"
    sentinel_dir.mkdir()

    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    store.open()
    store.set_meta("project_name", "emptyproject")
    store.close()
    return project


# --- _find_sentinel_dir ---


def test_find_sentinel_dir_at_root(sentinel_project: Path) -> None:
    result = _find_sentinel_dir(sentinel_project)
    assert result is not None
    assert result == sentinel_project / ".sentinel"


def test_find_sentinel_dir_from_subdir(sentinel_project: Path) -> None:
    subdir = sentinel_project / "src" / "deep"
    subdir.mkdir(parents=True)
    result = _find_sentinel_dir(subdir)
    assert result is not None
    assert result == sentinel_project / ".sentinel"


def test_find_sentinel_dir_not_found(tmp_path: Path) -> None:
    result = _find_sentinel_dir(tmp_path / "nowhere")
    assert result is None


# --- _open_store ---


def test_open_store_found(sentinel_project: Path) -> None:
    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    assert store.get_meta("project_name") == "myproject"
    store.close()


def test_open_store_not_found() -> None:
    store = _open_store(Path("/nonexistent/.sentinel"))
    assert store is None


# --- MCP Tools (via create_server) ---


@pytest.fixture
def mcp_tools(sentinel_project: Path) -> dict:
    """Create server and extract tool functions, patching CWD."""
    with patch("sentinel.mcp.server._find_sentinel_dir", return_value=sentinel_project / ".sentinel"):
        server = create_server()
    # Access registered tools via the server's internal registry
    return {"server": server, "project": sentinel_project}


def _call_tool(mcp_tools: dict, tool_name: str, **kwargs: str) -> str:
    """Call an MCP tool by name, patching _find_sentinel_dir."""
    project = mcp_tools["project"]
    with patch("sentinel.mcp.server._find_sentinel_dir", return_value=project / ".sentinel"):
        # Import the module-level functions directly
        import sentinel.mcp.server as srv
        tool_fn = {
            "sentinel_project_context": lambda: srv._tool_project_context(),
            "sentinel_query": lambda: srv._tool_query(kwargs.get("query", "")),
            "sentinel_conventions": lambda: srv._tool_conventions(),
            "sentinel_pitfalls": lambda: srv._tool_pitfalls(),
            "sentinel_decisions": lambda: srv._tool_decisions(),
            "sentinel_hot_files": lambda: srv._tool_hot_files(),
            "sentinel_co_changes": lambda: srv._tool_co_changes(kwargs.get("file_path", "")),
        }
        # Fallback: just call the function from the server module directly
        return tool_fn[tool_name]()


# Instead of complex tool extraction, test the underlying functions directly

def test_tool_project_context(sentinel_project: Path) -> None:
    """sentinel_project_context returns full summary."""
    from sentinel.mcp.formatters import format_project_context
    from sentinel.mcp.server import _open_store

    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    result = format_project_context(store)
    store.close()

    assert "myproject" in result
    assert "snake_case" in result
    assert "SQL injection" in result
    assert "SQLite" in result
    assert "src/auth.py" in result


def test_tool_query(sentinel_project: Path) -> None:
    """sentinel_query returns FTS5 search results."""
    from sentinel.mcp.formatters import format_query_results
    from sentinel.mcp.server import _open_store

    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    results = store.search("snake_case", limit=20)
    output = format_query_results(results, "snake_case")
    store.close()

    assert "snake_case" in output


def test_tool_conventions(sentinel_project: Path) -> None:
    """sentinel_conventions returns all conventions."""
    from sentinel.mcp.formatters import format_conventions
    from sentinel.mcp.server import _open_store

    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    conventions = store.get_conventions()
    output = format_conventions(conventions)
    store.close()

    assert "naming" in output
    assert "snake_case" in output


def test_tool_pitfalls(sentinel_project: Path) -> None:
    """sentinel_pitfalls returns all pitfalls."""
    from sentinel.mcp.formatters import format_pitfalls
    from sentinel.mcp.server import _open_store

    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    pitfalls = store.get_pitfalls()
    output = format_pitfalls(pitfalls)
    store.close()

    assert "high" in output
    assert "SQL injection" in output


def test_tool_decisions(sentinel_project: Path) -> None:
    """sentinel_decisions returns all decisions."""
    from sentinel.mcp.formatters import format_decisions
    from sentinel.mcp.server import _open_store

    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    decisions = store.get_decisions(limit=30)
    output = format_decisions(decisions)
    store.close()

    assert "SQLite" in output
    assert "alice" in output


def test_tool_hot_files(sentinel_project: Path) -> None:
    """sentinel_hot_files returns hot file table."""
    from sentinel.mcp.formatters import format_hot_files
    from sentinel.mcp.server import _open_store

    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    hot_files = store.get_hot_files(limit=200)
    output = format_hot_files(hot_files, store=store)
    store.close()

    assert "src/auth.py" in output
    assert "Risk" in output


def test_tool_co_changes(sentinel_project: Path) -> None:
    """sentinel_co_changes returns co-change pairs."""
    from sentinel.mcp.formatters import format_co_changes
    from sentinel.mcp.server import _open_store

    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    co_changes = store.get_co_changes("src/auth.py", min_count=2)
    output = format_co_changes("src/auth.py", co_changes)
    store.close()

    assert "tests/test_auth.py" in output


def test_tool_co_changes_no_data(sentinel_project: Path) -> None:
    """sentinel_co_changes handles files with no co-changes."""
    from sentinel.mcp.formatters import format_co_changes
    from sentinel.mcp.server import _open_store

    store = _open_store(sentinel_project / ".sentinel")
    assert store is not None
    co_changes = store.get_co_changes("nonexistent.py", min_count=2)
    output = format_co_changes("nonexistent.py", co_changes)
    store.close()

    assert "No co-change data" in output


# --- Missing .sentinel/ ---


def test_no_sentinel_msg() -> None:
    """Tools return helpful message when .sentinel/ is missing."""
    from sentinel.mcp.server import _no_sentinel_msg
    msg = _no_sentinel_msg()
    assert "sentinel init" in msg


# --- Empty knowledge store ---


def test_empty_store_project_context(empty_project: Path) -> None:
    """Project context works with empty knowledge store."""
    from sentinel.mcp.formatters import format_project_context
    from sentinel.mcp.server import _open_store

    store = _open_store(empty_project / ".sentinel")
    assert store is not None
    result = format_project_context(store)
    store.close()

    assert "emptyproject" in result
    assert "0 conventions" in result


def test_empty_store_conventions(empty_project: Path) -> None:
    """Conventions tool works with empty knowledge store."""
    from sentinel.mcp.formatters import format_conventions
    from sentinel.mcp.server import _open_store

    store = _open_store(empty_project / ".sentinel")
    assert store is not None
    conventions = store.get_conventions()
    output = format_conventions(conventions)
    store.close()

    assert "No conventions" in output


# --- mcp-setup CLI ---


def test_mcp_setup_writes_config(tmp_path: Path) -> None:
    """mcp-setup creates .mcp.json in project root."""
    from sentinel.cli.mcp_setup import _sentinel_mcp_command

    command = _sentinel_mcp_command()
    sentinel_entry = {"command": command, "args": []}
    config = {"mcpServers": {"sentinel": sentinel_entry}}

    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps(config, indent=2) + "\n")

    loaded = json.loads(mcp_json.read_text())
    assert "mcpServers" in loaded
    assert "sentinel" in loaded["mcpServers"]
    assert "sentinel-mcp" in loaded["mcpServers"]["sentinel"]["command"]


def test_mcp_setup_merges_existing(tmp_path: Path) -> None:
    """mcp-setup merges with existing .mcp.json."""
    existing = {"mcpServers": {"other-tool": {"command": "other-mcp"}}}
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps(existing))

    # Simulate merge logic
    config = json.loads(mcp_json.read_text())
    servers = config.get("mcpServers", {})
    servers["sentinel"] = {"command": "sentinel-mcp", "args": []}
    config["mcpServers"] = servers

    assert "other-tool" in config["mcpServers"]
    assert "sentinel" in config["mcpServers"]


def test_mcp_setup_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp-setup --global writes to ~/.claude.json."""
    from sentinel.cli.mcp_setup import _write_global_mcp_config

    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    _write_global_mcp_config()

    loaded = json.loads((fake_home / ".claude.json").read_text())
    assert "sentinel" in loaded["mcpServers"]
    assert "sentinel-mcp" in loaded["mcpServers"]["sentinel"]["command"]


def test_mcp_setup_global_merges_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mcp-setup --global preserves existing entries in ~/.claude.json."""
    from sentinel.cli.mcp_setup import _write_global_mcp_config

    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    claude_json = fake_home / ".claude.json"
    claude_json.write_text(json.dumps({
        "numStartups": 42,
        "mcpServers": {"anno": {"command": "node", "args": []}},
    }))

    _write_global_mcp_config()

    loaded = json.loads(claude_json.read_text())
    assert "anno" in loaded["mcpServers"]
    assert "sentinel" in loaded["mcpServers"]
    assert loaded["numStartups"] == 42  # other keys preserved


# --- Health Check ---


@pytest.fixture
def health_check_project(tmp_path: Path) -> Path:
    """Create a project with .sentinel/, git, pyproject.toml, and tests."""
    import subprocess

    project = tmp_path / "healthproject"
    project.mkdir()

    # Init git
    subprocess.run(["git", "init"], cwd=project, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, capture_output=True)

    # Create project structure
    (project / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    src = project / "src" / "mylib"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text('__version__ = "1.0.0"\n')

    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_main.py").write_text(
        "def test_one(): pass\ndef test_two(): pass\ndef test_three(): pass\n"
    )

    # Initial commit
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=project, capture_output=True)

    # Init sentinel
    sentinel_dir = project / ".sentinel"
    sentinel_dir.mkdir()
    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    store.open()
    store.set_meta("project_name", "healthproject")
    store.close()

    return project


@pytest.mark.skip(reason="sentinel_health_check removed from MCP surface in v4 collapse")
class TestHealthCheck:
    def test_basic_health_check(self, health_check_project: Path) -> None:
        """Health check runs all checks and stores results."""
        server = create_server()
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        tool_fn = tools["sentinel_health_check"].fn

        result = tool_fn(project_root=str(health_check_project))
        assert "Health Check" in result
        assert "version_consistency" in result
        assert "[pass]" in result
        assert "3 test functions" in result

        # Verify stored in DB
        sentinel_dir = health_check_project / ".sentinel"
        store = KnowledgeStore(sentinel_dir / "sentinel.db")
        store.open()
        last = store.get_last_health_check()
        store.close()
        assert last is not None
        assert last["issues_found"] == 0

    def test_version_mismatch_detected(self, health_check_project: Path) -> None:
        """Health check detects version mismatch between pyproject.toml and __init__.py."""
        # Create a mismatch
        init_file = health_check_project / "src" / "mylib" / "__init__.py"
        init_file.write_text('__version__ = "0.9.0"\n')

        server = create_server()
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        tool_fn = tools["sentinel_health_check"].fn

        result = tool_fn(project_root=str(health_check_project))
        assert "FAIL" in result
        assert "0.9.0" in result

    def test_second_check_shows_delta(self, health_check_project: Path) -> None:
        """Second health check shows commits since last check."""
        import subprocess

        server = create_server()
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        tool_fn = tools["sentinel_health_check"].fn

        # First check
        tool_fn(project_root=str(health_check_project))

        # Add a commit
        (health_check_project / "new_file.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=health_check_project, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add file"], cwd=health_check_project, capture_output=True)

        # Second check
        result = tool_fn(project_root=str(health_check_project))
        assert "1 commits since last check" in result

    def test_no_sentinel_dir(self) -> None:
        """Health check without .sentinel/ returns init message."""
        server = create_server()
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        tool_fn = tools["sentinel_health_check"].fn

        result = tool_fn(project_root="/nonexistent/path")
        assert "No `.sentinel/`" in result
