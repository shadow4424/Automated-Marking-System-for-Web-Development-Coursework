"""Tests for the threat pattern registry."""
from __future__ import annotations

import re

import pytest

from ams.sandbox.threat_patterns import (
    BINARY_SIGNATURES,
    SCANNABLE_EXTENSIONS,
    ThreatCategory,
    ThreatPattern,
    ThreatSeverity,
    get_all_patterns,
    get_patterns_for_extension,
)


class TestThreatPatternRegistry:
    """Unit tests for the threat pattern definitions."""

    def test_get_all_patterns_returns_nonempty(self):
        patterns = get_all_patterns()
        assert len(patterns) > 10, "Expected at least 10 threat patterns"

    def test_all_patterns_are_threat_pattern_instances(self):
        for p in get_all_patterns():
            assert isinstance(p, ThreatPattern)

    def test_all_patterns_have_compiled_regex(self):
        for p in get_all_patterns():
            assert isinstance(p.regex, re.Pattern), f"{p.name} regex is not compiled"

    def test_all_patterns_have_valid_category(self):
        for p in get_all_patterns():
            assert isinstance(p.category, ThreatCategory), f"{p.name} has invalid category"

    def test_all_patterns_have_valid_severity(self):
        for p in get_all_patterns():
            assert isinstance(p.severity, ThreatSeverity), f"{p.name} has invalid severity"

    def test_all_patterns_have_name_and_description(self):
        for p in get_all_patterns():
            assert p.name, f"Pattern missing name"
            assert p.description, f"Pattern {p.name} missing description"

    def test_pattern_names_are_unique(self):
        names = [p.name for p in get_all_patterns()]
        assert len(names) == len(set(names)), "Duplicate pattern names found"


class TestGetPatternsForExtension:
    """Tests for extension-based pattern filtering."""

    def test_php_extension_returns_php_patterns(self):
        patterns = get_patterns_for_extension(".php")
        names = {p.name for p in patterns}
        assert "php_system" in names
        assert "php_eval_variable" in names

    def test_js_extension_returns_js_patterns(self):
        patterns = get_patterns_for_extension(".js")
        names = {p.name for p in patterns}
        assert "js_child_process" in names
        assert "js_eval" in names

    def test_sql_extension_returns_sql_patterns(self):
        patterns = get_patterns_for_extension(".sql")
        names = {p.name for p in patterns}
        assert "sql_file_ops" in names

    def test_universal_patterns_included_for_all_extensions(self):
        """Patterns with empty file_extensions should match all scannable files."""
        for ext in [".php", ".js", ".html", ".py"]:
            patterns = get_patterns_for_extension(ext)
            names = {p.name for p in patterns}
            assert "path_traversal" in names, f"Universal pattern missing for {ext}"

    def test_unknown_extension_returns_only_universal(self):
        patterns = get_patterns_for_extension(".xyz")
        for p in patterns:
            assert not p.file_extensions, (
                f"Pattern {p.name} has specific extensions but matched .xyz"
            )

    def test_case_insensitive_extension(self):
        lower = get_patterns_for_extension(".php")
        upper = get_patterns_for_extension(".PHP")
        assert len(lower) == len(upper)


class TestPatternMatching:
    """Tests that individual patterns correctly match known malicious strings."""

    @pytest.mark.parametrize("code,pattern_name", [
        ("system('ls');", "php_system"),
        ("exec('whoami');", "php_system"),
        ("passthru('cat /etc/passwd');", "php_system"),
        ("shell_exec('id');", "php_system"),
    ])
    def test_php_shell_patterns(self, code, pattern_name):
        patterns = {p.name: p for p in get_all_patterns()}
        assert patterns[pattern_name].regex.search(code), f"Failed to match: {code}"

    @pytest.mark.parametrize("code,pattern_name", [
        ("eval($user_input);", "php_eval_variable"),
        ("eval(base64_decode('encoded'));", "php_base64_eval"),
    ])
    def test_php_injection_patterns(self, code, pattern_name):
        patterns = {p.name: p for p in get_all_patterns()}
        assert patterns[pattern_name].regex.search(code), f"Failed to match: {code}"

    @pytest.mark.parametrize("code,pattern_name", [
        ("require('child_process');", "js_child_process"),
        ("import('fs');", "js_fs_module"),
        ("eval(someVar);", "js_eval"),
    ])
    def test_js_dangerous_patterns(self, code, pattern_name):
        patterns = {p.name: p for p in get_all_patterns()}
        assert patterns[pattern_name].regex.search(code), f"Failed to match: {code}"

    def test_path_traversal_pattern(self):
        patterns = {p.name: p for p in get_all_patterns()}
        p = patterns["path_traversal"]
        assert p.regex.search("../../../../etc/passwd")
        assert p.regex.search("..\\..\\..\\windows\\system32")

    def test_sensitive_path_pattern(self):
        patterns = {p.name: p for p in get_all_patterns()}
        p = patterns["sensitive_path_access"]
        assert p.regex.search("file_get_contents('/etc/passwd')")
        assert p.regex.search("/proc/self/environ")

    def test_benign_code_not_matched(self):
        """Legitimate student code should not trigger shell patterns."""
        patterns = {p.name: p for p in get_all_patterns()}
        benign_php = "echo 'Hello World';"
        assert not patterns["php_system"].regex.search(benign_php)

    def test_benign_js_not_matched(self):
        benign_js = "document.getElementById('form').addEventListener('submit', handler);"
        patterns = {p.name: p for p in get_all_patterns()}
        assert not patterns["js_child_process"].regex.search(benign_js)
        assert not patterns["js_fs_module"].regex.search(benign_js)


class TestConstants:
    """Tests for module constants."""

    def test_scannable_extensions_is_frozenset(self):
        assert isinstance(SCANNABLE_EXTENSIONS, frozenset)

    def test_scannable_extensions_contain_common_types(self):
        for ext in [".php", ".js", ".html", ".css", ".sql", ".py"]:
            assert ext in SCANNABLE_EXTENSIONS

    def test_binary_signatures_contain_elf_and_pe(self):
        assert "ELF" in BINARY_SIGNATURES
        assert "PE" in BINARY_SIGNATURES
        assert BINARY_SIGNATURES["ELF"] == b"\x7fELF"
        assert BINARY_SIGNATURES["PE"] == b"MZ"
