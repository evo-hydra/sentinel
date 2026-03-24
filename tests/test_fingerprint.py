"""Tests for error fingerprinting."""

from __future__ import annotations

from sentinel.core.fingerprint import fingerprint, normalize_error


class TestNormalizeError:
    def test_strips_paths(self):
        msg = "ImportError at /home/user/project/src/auth.py"
        result = normalize_error(msg)
        assert "/home/user" not in result
        assert "auth.py" not in result

    def test_strips_line_numbers(self):
        msg = "TypeError at line 42 in module"
        result = normalize_error(msg)
        assert "line 42" not in result
        assert "42" not in result

    def test_strips_timestamps(self):
        msg = "Error at 2026-02-28T14:30:00Z: connection refused"
        result = normalize_error(msg)
        assert "2026-02-28" not in result
        assert "connection refused" in result

    def test_strips_memory_addresses(self):
        msg = "Object at 0xdeadbeef is not callable"
        result = normalize_error(msg)
        assert "0xdeadbeef" not in result
        assert "is not callable" in result

    def test_strips_pids(self):
        msg = "Process failed pid: 12345 with exit code 1"
        result = normalize_error(msg)
        assert "pid: 12345" not in result

    def test_collapses_whitespace(self):
        msg = "Error   in    module"
        result = normalize_error(msg)
        assert result == "Error in module"

    def test_strips_windows_paths(self):
        msg = r"ImportError at C:\Users\alice\project\src\auth.py"
        result = normalize_error(msg)
        assert "Users" not in result
        assert "auth.py" not in result

    def test_preserves_port_numbers(self):
        msg = "ConnectionError: refused on localhost:5432"
        result = normalize_error(msg)
        assert "5432" in result

    def test_preserves_version_strings(self):
        msg = "Requires python:3.12 or higher"
        result = normalize_error(msg)
        assert "3.12" in result


class TestFingerprint:
    def test_same_error_different_paths_same_fingerprint(self):
        msg1 = "ImportError: No module named 'foo' at /home/alice/project/main.py"
        msg2 = "ImportError: No module named 'foo' at /home/bob/project/app.py"
        assert fingerprint(msg1) == fingerprint(msg2)

    def test_different_errors_different_fingerprints(self):
        msg1 = "TypeError: expected str, got int"
        msg2 = "ValueError: invalid literal for int"
        assert fingerprint(msg1) != fingerprint(msg2)

    def test_deterministic(self):
        msg = "ConnectionError: refused on port 5432"
        fp1 = fingerprint(msg)
        fp2 = fingerprint(msg)
        assert fp1 == fp2

    def test_returns_hex_sha256(self):
        fp = fingerprint("test error")
        assert len(fp) == 64  # SHA256 hex length
        assert all(c in "0123456789abcdef" for c in fp)

    def test_same_error_different_line_numbers(self):
        msg1 = "Error in module.py:42 something failed"
        msg2 = "Error in module.py:99 something failed"
        assert fingerprint(msg1) == fingerprint(msg2)

    def test_same_error_unix_vs_windows_paths(self):
        msg1 = "ImportError: No module named 'foo' at /home/alice/project/main.py"
        msg2 = r"ImportError: No module named 'foo' at C:\Users\alice\project\main.py"
        assert fingerprint(msg1) == fingerprint(msg2)
