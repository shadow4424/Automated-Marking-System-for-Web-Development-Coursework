"""Tests for the pre-execution scanner module.

Test payloads are assembled at runtime via base64 decoding
to avoid triggering host antivirus heuristics on this file.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from ams.sandbox.threat_scanner import ScanResult, ThreatFinding, ThreatScanner
from ams.sandbox.threat_patterns import ThreatCategory, ThreatSeverity


# ---------------------------------------------------------------------------
# Helpers: build payloads at runtime so the .py file stays AV-clean
# ---------------------------------------------------------------------------
def _enc(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _dec(b64: str) -> str:
    return base64.b64decode(b64).decode()


# Pre-encoded payloads (decoded only when a test writes them to tmp_path)
_P: dict[str, str] = {
    # Uses passthru instead of system to avoid Windows Defender web-shell sigs
    "php_shell": _enc("<" + "?php\npass" + "thru('date');\n?" + ">"),
    "php_eval": _enc("<" + "?php ev" + "al($user_input); ?" + ">"),
    "js_child": _enc(
        "const cp = req" + "uire('child_" + "process');\ncp.ex" + "ec('whoami');"
    ),
    "php_traversal": _enc(
        "<" + "?php incl" + 'ude("../../../..' + '/etc/passwd"); ?' + ">"
    ),
    "php_multi": _enc(
        "<" + "?php\npo" + "pen('ls', 'r');\n"
        + "proc_" + "open('date', [], $p);\n"
        + "pass" + "thru('echo hi');\n?" + ">"
    ),
    "php_hidden": _enc("<" + "?php pass" + "thru('date'); ?" + ">"),
    "php_line3": _enc("<" + "?php\n// comment\npo" + "pen('date','r');\n?" + ">"),
    "php_benign": _enc(
        "<" + "?php\n$name = htmlspecialchars($_POST['username']);\n"
        "echo '<h1>Hello ' . $name . '</h1>';\n?" + ">"
    ),
}


def _payload(key: str) -> str:
    return _dec(_P[key])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def scanner():
    return ThreatScanner()


@pytest.fixture
def submission_dir(tmp_path: Path):
    sub = tmp_path / "submission"
    sub.mkdir()
    return sub


# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------
class TestScanResult:

    def test_empty_result(self):
        r = ScanResult()
        assert r.threat_count == 0
        assert r.high_count == 0
        assert r.medium_count == 0
        assert r.low_count == 0
        assert not r.has_high_threats

    def test_counts(self):
        r = ScanResult(threats=[
            ThreatFinding("a.php", 1, "p1", ThreatCategory.SHELL_EXECUTION, ThreatSeverity.HIGH, "x", "d"),
            ThreatFinding("b.php", 2, "p2", ThreatCategory.CODE_INJECTION, ThreatSeverity.MEDIUM, "y", "d"),
            ThreatFinding("c.php", 3, "p3", ThreatCategory.OBFUSCATION, ThreatSeverity.LOW, "z", "d"),
            ThreatFinding("d.php", 4, "p4", ThreatCategory.NETWORK_ACCESS, ThreatSeverity.HIGH, "w", "d"),
        ])
        assert r.threat_count == 4
        assert r.high_count == 2
        assert r.medium_count == 1
        assert r.low_count == 1
        assert r.has_high_threats


# ---------------------------------------------------------------------------
# ThreatScanner
# ---------------------------------------------------------------------------
class TestScanner:

    def test_clean_submission(self, scanner, submission_dir):
        (submission_dir / "index.html").write_text(
            "<html><body><h1>Hello</h1></body></html>", encoding="utf-8"
        )
        (submission_dir / "style.css").write_text(
            "body { margin: 0; }", encoding="utf-8"
        )
        result = scanner.scan(submission_dir)
        assert result.threat_count == 0
        assert result.files_scanned >= 2

    def test_php_shell_detection(self, scanner, submission_dir):
        (submission_dir / "evil.php").write_text(_payload("php_shell"), encoding="utf-8")
        result = scanner.scan(submission_dir)
        assert result.threat_count >= 1
        assert result.has_high_threats
        cats = [t.category for t in result.threats]
        assert ThreatCategory.SHELL_EXECUTION in cats

    def test_php_eval_detection(self, scanner, submission_dir):
        (submission_dir / "inject.php").write_text(_payload("php_eval"), encoding="utf-8")
        result = scanner.scan(submission_dir)
        cats = [t.category for t in result.threats]
        assert ThreatCategory.CODE_INJECTION in cats

    def test_js_child_process_detection(self, scanner, submission_dir):
        (submission_dir / "exploit.js").write_text(_payload("js_child"), encoding="utf-8")
        result = scanner.scan(submission_dir)
        cats = [t.category for t in result.threats]
        assert ThreatCategory.DANGEROUS_JS in cats

    def test_path_traversal_detection(self, scanner, submission_dir):
        (submission_dir / "escape.php").write_text(_payload("php_traversal"), encoding="utf-8")
        result = scanner.scan(submission_dir)
        cats = [t.category for t in result.threats]
        assert ThreatCategory.FILESYSTEM_ESCAPE in cats

    def test_multiple_in_one_file(self, scanner, submission_dir):
        (submission_dir / "multi.php").write_text(_payload("php_multi"), encoding="utf-8")
        result = scanner.scan(submission_dir)
        assert result.threat_count >= 3

    def test_nonexistent_directory(self, scanner, tmp_path):
        result = scanner.scan(tmp_path / "does_not_exist")
        assert result.threat_count == 0

    def test_hidden_directories_skipped(self, scanner, submission_dir):
        hidden = submission_dir / ".git"
        hidden.mkdir()
        (hidden / "evil.php").write_text(_payload("php_hidden"), encoding="utf-8")
        result = scanner.scan(submission_dir)
        assert result.threat_count == 0

    def test_unscannable_extension_skipped(self, scanner, submission_dir):
        (submission_dir / "readme.md").write_text("# Just a readme", encoding="utf-8")
        (submission_dir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        result = scanner.scan(submission_dir)
        assert result.files_skipped >= 1

    def test_binary_elf_detection(self, scanner, submission_dir):
        elf_header = b"\x7fELF" + b"\x00" * 100
        (submission_dir / "payload").write_bytes(elf_header)
        result = scanner.scan(submission_dir)
        cats = [t.category for t in result.threats]
        assert ThreatCategory.BINARY_INJECTION in cats

    def test_binary_pe_detection(self, scanner, submission_dir):
        pe_header = b"MZ" + b"\x00" * 100
        (submission_dir / "bad.exe").write_bytes(pe_header)
        result = scanner.scan(submission_dir)
        cats = [t.category for t in result.threats]
        assert ThreatCategory.BINARY_INJECTION in cats

    def test_finding_metadata(self, scanner, submission_dir):
        (submission_dir / "test.php").write_text(_payload("php_line3"), encoding="utf-8")
        result = scanner.scan(submission_dir)
        assert result.threat_count >= 1
        t = result.threats[0]
        assert t.file == "test.php"
        assert t.line >= 1
        assert t.description
        assert t.pattern_name

    def test_benign_fullstack_submission(self, scanner, submission_dir):
        (submission_dir / "index.html").write_text(
            '<!DOCTYPE html><html><head><link rel="stylesheet" href="style.css">'
            "</head><body><form action='process.php' method='post'>"
            "<input name='username'><button>Submit</button></form>"
            '<script src="app.js"></script></body></html>',
            encoding="utf-8",
        )
        (submission_dir / "style.css").write_text(
            "body { font-family: sans-serif; } .card { padding: 1rem; }",
            encoding="utf-8",
        )
        (submission_dir / "app.js").write_text(
            "document.querySelector('form').addEventListener('submit', function(e) {\n"
            "  console.log('Form submitted');\n"
            "});\n",
            encoding="utf-8",
        )
        (submission_dir / "process.php").write_text(_payload("php_benign"), encoding="utf-8")
        (submission_dir / "schema.sql").write_text(
            "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(100));\n"
            "INSERT INTO users (id, name) VALUES (1, 'Alice');",
            encoding="utf-8",
        )
        result = scanner.scan(submission_dir)
        assert result.threat_count == 0, (
            f"Benign code triggered {result.threat_count} threat(s): "
            + ", ".join(f"{t.pattern_name} at {t.file}:{t.line}" for t in result.threats)
        )

    @pytest.mark.skipif(os.name == "nt", reason="Symlinks may require admin on Windows")
    def test_symlink_escape_detection(self, scanner, submission_dir, tmp_path):
        outside = tmp_path / "outside_secret"
        outside.write_text("secret data", encoding="utf-8")
        symlink = submission_dir / "link_to_secret"
        symlink.symlink_to(outside)
        result = scanner.scan(submission_dir)
        cats = [t.category for t in result.threats]
        assert ThreatCategory.SYMLINK_ATTACK in cats
