"""Tests for core/knowledge.py — KnowledgeStore."""

from __future__ import annotations

from pathlib import Path

from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import (
    ConventionCategory,
    KnowledgeSource,
    KnowledgeType,
    PitfallCategory,
    Severity,
)
from sentinel.models.knowledge import (
    AnalysisResult,
    CoChange,
    CodePattern,
    Convention,
    Decision,
    HotFile,
    Pitfall,
)


def test_open_close(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "test.db")
    store.open()
    assert store._conn is not None
    store.close()
    assert store._conn is None


def test_context_manager(tmp_path: Path) -> None:
    with KnowledgeStore(tmp_path / "test.db") as store:
        assert store._conn is not None
    assert store._conn is None


def test_set_get_meta(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.set_meta("project_name", "test-project")
    assert knowledge_store.get_meta("project_name") == "test-project"
    assert knowledge_store.get_meta("missing", "default") == "default"


def test_add_get_convention(knowledge_store: KnowledgeStore) -> None:
    conv = Convention(
        category=ConventionCategory.NAMING,
        pattern="snake_case",
        description="Files use snake_case",
        evidence=["auth.py", "main.py"],
        confidence=0.9,
        frequency=10,
        source=KnowledgeSource.GIT_HISTORY,
    )
    knowledge_store.add_convention(conv)

    result = knowledge_store.get_conventions()
    assert len(result) == 1
    assert result[0].pattern == "snake_case"
    assert result[0].confidence == 0.9
    assert result[0].evidence == ["auth.py", "main.py"]


def test_get_conventions_by_category(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.add_convention(Convention(
        category=ConventionCategory.NAMING, pattern="snake_case", description="test"
    ))
    knowledge_store.add_convention(Convention(
        category=ConventionCategory.COMMIT, pattern="conventional", description="test"
    ))

    naming = knowledge_store.get_conventions(ConventionCategory.NAMING)
    assert len(naming) == 1
    commit = knowledge_store.get_conventions(ConventionCategory.COMMIT)
    assert len(commit) == 1


def test_add_get_decision(knowledge_store: KnowledgeStore) -> None:
    dec = Decision(
        summary="Chose FastAPI because of async support",
        rationale="Better performance for our use case",
        commit_sha="abc123",
        author="dev",
        file_paths=["src/api.py"],
        tags=["framework"],
    )
    knowledge_store.add_decision(dec)

    result = knowledge_store.get_decisions()
    assert len(result) == 1
    assert result[0].summary == "Chose FastAPI because of async support"
    assert result[0].file_paths == ["src/api.py"]
    assert result[0].tags == ["framework"]


def test_add_get_pitfall(knowledge_store: KnowledgeStore) -> None:
    pit = Pitfall(
        category=PitfallCategory.SECURITY,
        severity=Severity.HIGH,
        description="SQL injection in user input",
        code_pattern=r"execute\(.*\+.*\)",
        how_to_prevent="Use parameterized queries",
        evidence=["commit abc123"],
    )
    knowledge_store.add_pitfall(pit)

    result = knowledge_store.get_pitfalls()
    assert len(result) == 1
    assert result[0].severity == Severity.HIGH
    assert result[0].code_pattern == r"execute\(.*\+.*\)"


def test_get_pitfalls_by_category(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.add_pitfall(Pitfall(
        category=PitfallCategory.SECURITY, description="sql injection"
    ))
    knowledge_store.add_pitfall(Pitfall(
        category=PitfallCategory.BUG, description="off by one"
    ))

    sec = knowledge_store.get_pitfalls(PitfallCategory.SECURITY)
    assert len(sec) == 1


def test_add_get_pattern(knowledge_store: KnowledgeStore) -> None:
    pat = CodePattern(
        name="singleton",
        description="Singleton pattern usage",
        ast_pattern="class.*Meta.*singleton",
        file_glob="*.py",
        frequency=5,
        examples=["class Config(metaclass=Singleton)"],
    )
    knowledge_store.add_pattern(pat)

    result = knowledge_store.get_patterns()
    assert len(result) == 1
    assert result[0].name == "singleton"
    assert result[0].examples == ["class Config(metaclass=Singleton)"]


def test_upsert_hot_file(knowledge_store: KnowledgeStore) -> None:
    hf = HotFile(file_path="src/auth.py", change_count=10, bug_fix_count=3, revert_count=1, churn_score=24)
    knowledge_store.upsert_hot_file(hf)

    result = knowledge_store.get_hot_files()
    assert len(result) == 1
    assert result[0].file_path == "src/auth.py"
    assert result[0].churn_score == 24

    # Upsert should accumulate
    hf2 = HotFile(file_path="src/auth.py", change_count=2, bug_fix_count=1, revert_count=0, churn_score=5)
    knowledge_store.upsert_hot_file(hf2)

    result = knowledge_store.get_hot_files()
    assert len(result) == 1
    assert result[0].change_count == 12


def test_get_hot_file(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.upsert_hot_file(
        HotFile(file_path="a.py", change_count=5, bug_fix_count=0, revert_count=0, churn_score=5)
    )
    assert knowledge_store.get_hot_file("a.py") is not None
    assert knowledge_store.get_hot_file("missing.py") is None


def test_upsert_co_change(knowledge_store: KnowledgeStore) -> None:
    cc = CoChange(file_a="auth.py", file_b="test_auth.py", change_count=5)
    knowledge_store.upsert_co_change(cc)

    result = knowledge_store.get_co_changes("auth.py", min_count=3)
    assert len(result) == 1
    assert result[0].change_count == 5

    # Upsert should accumulate
    cc2 = CoChange(file_a="test_auth.py", file_b="auth.py", change_count=2)
    knowledge_store.upsert_co_change(cc2)

    result = knowledge_store.get_co_changes("auth.py", min_count=3)
    assert len(result) == 1
    assert result[0].change_count == 7


def test_co_changes_min_count(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.upsert_co_change(CoChange(file_a="a.py", file_b="b.py", change_count=2))
    result = knowledge_store.get_co_changes("a.py", min_count=3)
    assert len(result) == 0

    result = knowledge_store.get_co_changes("a.py", min_count=1)
    assert len(result) == 1


def test_record_swarm(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.record_swarm("abc123", 100)
    assert knowledge_store.last_swarm_sha() == "abc123"
    assert knowledge_store.get_meta("last_swarm_at") != ""


def test_fts_search(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.add_convention(Convention(
        category=ConventionCategory.NAMING, pattern="snake_case",
        description="Python files use snake_case naming"
    ))
    knowledge_store.add_pitfall(Pitfall(
        category=PitfallCategory.SECURITY, description="SQL injection vulnerability",
        how_to_prevent="Use parameterized queries"
    ))

    results = knowledge_store.search("snake_case")
    assert len(results) >= 1
    assert results[0]["type"] == "convention"

    results = knowledge_store.search("SQL injection")
    assert len(results) >= 1
    assert results[0]["type"] == "pitfall"


def test_fts_search_with_type_filter(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.add_convention(Convention(
        category=ConventionCategory.NAMING, pattern="test", description="test pattern"
    ))
    knowledge_store.add_pitfall(Pitfall(description="test pitfall"))

    results = knowledge_store.search("test", ktype=KnowledgeType.CONVENTION)
    assert all(r["type"] == "convention" for r in results)


def test_stats(knowledge_store: KnowledgeStore) -> None:
    knowledge_store.add_convention(Convention(
        category=ConventionCategory.NAMING, pattern="test", description="test"
    ))
    knowledge_store.add_decision(Decision(summary="test"))
    knowledge_store.add_pitfall(Pitfall(description="test"))
    knowledge_store.upsert_hot_file(HotFile(file_path="a.py", churn_score=1))

    s = knowledge_store.stats()
    assert s["conventions"] == 1
    assert s["decisions"] == 1
    assert s["pitfalls"] == 1
    assert s["hot_files"] == 1


def test_store_results_batch(knowledge_store: KnowledgeStore) -> None:
    """store_results() should insert all items in a single transaction."""
    results = AnalysisResult(
        conventions=[
            Convention(category=ConventionCategory.NAMING, pattern="snake", description="test"),
            Convention(category=ConventionCategory.COMMIT, pattern="conventional", description="test"),
        ],
        decisions=[Decision(summary="Use SQLite")],
        pitfalls=[Pitfall(description="Off-by-one error")],
        hot_files=[HotFile(file_path="x.py", change_count=3, churn_score=3)],
        co_changes=[CoChange(file_a="a.py", file_b="b.py", change_count=5)],
        commits_analyzed=10,
    )
    knowledge_store.store_results(results)

    assert len(knowledge_store.get_conventions()) == 2
    assert len(knowledge_store.get_decisions()) == 1
    assert len(knowledge_store.get_pitfalls()) == 1
    assert len(knowledge_store.get_hot_files()) == 1
    assert len(knowledge_store.get_co_changes("a.py", min_count=1)) == 1


def test_decision_source_column(knowledge_store: KnowledgeStore) -> None:
    """Decisions should store and retrieve the source field."""
    knowledge_store.add_decision(Decision(
        summary="Use React", source=KnowledgeSource.INFERRED,
    ))
    knowledge_store.add_decision(Decision(
        summary="Use FastAPI", source=KnowledgeSource.GIT_HISTORY,
    ))

    decisions = knowledge_store.get_decisions()
    sources = {d.summary: d.source for d in decisions}
    assert sources["Use React"] == KnowledgeSource.INFERRED
    assert sources["Use FastAPI"] == KnowledgeSource.GIT_HISTORY


def test_pitfall_source_column(knowledge_store: KnowledgeStore) -> None:
    """Pitfalls should store and retrieve the source field."""
    knowledge_store.add_pitfall(Pitfall(
        description="Bug found by LLM", source=KnowledgeSource.INFERRED,
    ))
    knowledge_store.add_pitfall(Pitfall(
        description="Bug from revert", source=KnowledgeSource.GIT_HISTORY,
    ))

    pitfalls = knowledge_store.get_pitfalls()
    sources = {p.description: p.source for p in pitfalls}
    assert sources["Bug found by LLM"] == KnowledgeSource.INFERRED
    assert sources["Bug from revert"] == KnowledgeSource.GIT_HISTORY
