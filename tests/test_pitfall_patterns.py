"""Tests for pitfall generalization (clustering episode-level pitfalls into patterns)."""

from __future__ import annotations

import pytest

from sentinel.core.analyzer import GitAnalyzer
from sentinel.core.knowledge import KnowledgeStore
from sentinel.models.enums import KnowledgeSource, PitfallCategory, Severity
from sentinel.models.knowledge import AnalysisResult, Pitfall, PitfallPattern


# --- Unit tests for _generalize_pitfalls ---

class TestGeneralizePitfalls:
    """Test the clustering/generalisation logic in GitAnalyzer."""

    @pytest.fixture
    def analyzer(self, tmp_path):
        """Analyzer pointing at a dummy repo (only using _generalize_pitfalls)."""
        # _generalize_pitfalls doesn't touch git — repo path doesn't matter
        return GitAnalyzer(tmp_path)

    def _pit(self, desc: str, prevent: str = "", files: list[str] | None = None,
             category: PitfallCategory = PitfallCategory.BUG,
             severity: Severity = Severity.MEDIUM) -> Pitfall:
        return Pitfall(
            description=desc,
            how_to_prevent=prevent,
            file_paths=files or [],
            category=category,
            severity=severity,
            evidence=["abc123"],
        )

    def test_no_pitfalls_returns_empty(self, analyzer):
        assert analyzer._generalize_pitfalls([]) == []

    def test_single_pitfall_returns_empty(self, analyzer):
        """A single pitfall can't form a cluster."""
        assert analyzer._generalize_pitfalls([self._pit("fix: null check")]) == []

    def test_two_state_pitfalls_cluster(self, analyzer):
        """Two pitfalls mentioning state/race form a 'state' cluster."""
        pitfalls = [
            self._pit("fix: race condition in handler", files=["src/handler.py"]),
            self._pit("fix: concurrent state mutation", files=["src/service.py"]),
        ]
        patterns = analyzer._generalize_pitfalls(pitfalls)
        assert len(patterns) >= 1
        state_patterns = [p for p in patterns if p.cluster_name == "state"]
        assert len(state_patterns) == 1
        assert state_patterns[0].episode_count == 2
        assert "state" in state_patterns[0].pattern.lower()

    def test_validation_cluster(self, analyzer):
        """Pitfalls with validation keywords cluster together."""
        pitfalls = [
            self._pit("fix: missing required field validation"),
            self._pit("fix: invalid input not checked"),
        ]
        patterns = analyzer._generalize_pitfalls(pitfalls)
        validation_patterns = [p for p in patterns if p.cluster_name == "validation"]
        assert len(validation_patterns) == 1
        assert validation_patterns[0].episode_count == 2

    def test_severity_uses_highest(self, analyzer):
        """Pattern severity is the highest from its episodes."""
        pitfalls = [
            self._pit("fix: null pointer crash", severity=Severity.HIGH),
            self._pit("fix: optional value not checked", severity=Severity.MEDIUM),
        ]
        patterns = analyzer._generalize_pitfalls(pitfalls)
        null_patterns = [p for p in patterns if p.cluster_name == "null_handling"]
        assert len(null_patterns) == 1
        assert null_patterns[0].severity == Severity.HIGH

    def test_file_paths_collected(self, analyzer):
        """Pattern collects file paths from all episodes."""
        pitfalls = [
            self._pit("fix: race condition", files=["src/a.py"]),
            self._pit("fix: concurrent access", files=["src/b.py", "src/c.py"]),
        ]
        patterns = analyzer._generalize_pitfalls(pitfalls)
        state_patterns = [p for p in patterns if p.cluster_name == "state"]
        assert len(state_patterns) == 1
        assert set(state_patterns[0].file_paths) == {"src/a.py", "src/b.py", "src/c.py"}

    def test_no_cluster_for_unrelated(self, analyzer):
        """Unrelated pitfalls don't cluster."""
        pitfalls = [
            self._pit("feat: add new logging format"),
            self._pit("chore: update dependencies"),
        ]
        patterns = analyzer._generalize_pitfalls(pitfalls)
        assert patterns == []

    def test_pattern_has_prevention_advice(self, analyzer):
        """Generated patterns include prevention advice."""
        pitfalls = [
            self._pit("fix: null deref in parser", prevent="check for None"),
            self._pit("fix: NoneType error in handler"),
        ]
        patterns = analyzer._generalize_pitfalls(pitfalls)
        null_patterns = [p for p in patterns if p.cluster_name == "null_handling"]
        assert len(null_patterns) == 1
        assert "Prevention:" in null_patterns[0].how_to_prevent

    def test_pitfall_in_multiple_clusters(self, analyzer):
        """A pitfall matching multiple keyword clusters appears in each."""
        pitfalls = [
            self._pit("fix: null check missing in validate function"),  # null_handling + validation
            self._pit("fix: NoneType attribute error"),                 # null_handling
            self._pit("fix: missing required field check"),              # validation
        ]
        patterns = analyzer._generalize_pitfalls(pitfalls)
        cluster_names = {p.cluster_name for p in patterns}
        assert "null_handling" in cluster_names
        assert "validation" in cluster_names


# --- Storage tests ---

class TestPitfallPatternStorage:
    """Test storing and querying pitfall patterns in KnowledgeStore."""

    @pytest.fixture
    def store(self, tmp_path):
        db = tmp_path / "test.db"
        with KnowledgeStore(db) as s:
            yield s

    def _pattern(self, cluster: str = "state", pattern: str = "State issues — 2 episodes",
                 episode_count: int = 2) -> PitfallPattern:
        return PitfallPattern(
            cluster_name=cluster,
            pattern=pattern,
            how_to_prevent="Avoid mutable singletons",
            category=PitfallCategory.BUG,
            severity=Severity.HIGH,
            episode_ids=["ep1", "ep2"],
            episode_count=episode_count,
            file_paths=["src/handler.py"],
        )

    def test_store_and_retrieve(self, store):
        """Round-trip: store a pattern and retrieve it."""
        pp = self._pattern()
        store._add_pitfall_pattern_no_commit(pp)
        store.conn.commit()

        results = store.get_pitfall_patterns()
        assert len(results) == 1
        assert results[0].cluster_name == "state"
        assert results[0].episode_count == 2

    def test_dedup_on_reinsert(self, store):
        """Reinserting same (cluster_name, pattern) updates rather than duplicates."""
        pp1 = self._pattern(episode_count=2)
        pp2 = self._pattern(episode_count=3)
        store._add_pitfall_pattern_no_commit(pp1)
        store._add_pitfall_pattern_no_commit(pp2)
        store.conn.commit()

        results = store.get_pitfall_patterns()
        assert len(results) == 1
        assert results[0].episode_count == 3

    def test_search_by_keyword(self, store):
        """search_pitfall_patterns finds patterns by keyword."""
        pp = self._pattern(
            pattern="State Issues in src/handler — 3 episodes",
            cluster="state",
        )
        pp2 = PitfallPattern(
            cluster_name="validation",
            pattern="Validation Issues in src/api — 2 episodes",
            how_to_prevent="Validate inputs at entry points",
            episode_count=2,
        )
        store._add_pitfall_pattern_no_commit(pp)
        store._add_pitfall_pattern_no_commit(pp2)
        store.conn.commit()

        results = store.search_pitfall_patterns("state mutable singleton")
        # Should find the state pattern
        assert any(p.cluster_name == "state" for p in results)

    def test_store_results_includes_patterns(self, store):
        """store_results() persists pitfall_patterns from AnalysisResult."""
        pp = self._pattern()
        result = AnalysisResult(pitfall_patterns=[pp])
        store.store_results(result)

        stored = store.get_pitfall_patterns()
        assert len(stored) == 1
        assert stored[0].cluster_name == "state"
