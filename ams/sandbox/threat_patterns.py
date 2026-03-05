"""Configurable threat pattern definitions for pre-execution scanning.

Each pattern is a compiled regex paired with metadata (category, severity,
human-readable description).  Patterns are separated from scanner logic so
they can be maintained, extended, and unit-tested independently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List


class ThreatSeverity(str, Enum):
    """Severity of a detected threat pattern."""
    HIGH = "HIGH"      # Halt execution
    MEDIUM = "MEDIUM"  # Flag but proceed
    LOW = "LOW"        # Informational


class ThreatCategory(str, Enum):
    """Category grouping for threat patterns."""
    SHELL_EXECUTION = "shell_execution"
    PROCESS_CONTROL = "process_control"
    FILESYSTEM_ESCAPE = "filesystem_escape"
    NETWORK_ACCESS = "network_access"
    CODE_INJECTION = "code_injection"
    OBFUSCATION = "obfuscation"
    DANGEROUS_JS = "dangerous_js"
    BINARY_INJECTION = "binary_injection"
    SYMLINK_ATTACK = "symlink_attack"


@dataclass(frozen=True)
class ThreatPattern:
    """A single threat detection pattern."""
    name: str
    category: ThreatCategory
    severity: ThreatSeverity
    regex: re.Pattern[str]
    description: str
    file_extensions: tuple[str, ...] = ()  # Empty = all files


def _compile(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# ── PHP Threat Patterns ─────────────────────────────────────────────────

_PHP_SHELL = [
    ThreatPattern(
        name="php_system",
        category=ThreatCategory.SHELL_EXECUTION,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\b(?:system|exec|passthru|shell_exec|popen|proc_open)\s*\('),
        description="PHP shell execution function call",
        file_extensions=(".php",),
    ),
    ThreatPattern(
        name="php_backtick_exec",
        category=ThreatCategory.SHELL_EXECUTION,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'`[^`]+`'),
        description="PHP backtick operator (shell execution)",
        file_extensions=(".php",),
    ),
]

_PHP_PROCESS = [
    ThreatPattern(
        name="php_pcntl",
        category=ThreatCategory.PROCESS_CONTROL,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\b(?:pcntl_fork|pcntl_exec|posix_kill|posix_setuid|posix_setsid)\s*\('),
        description="PHP process control function",
        file_extensions=(".php",),
    ),
]

_PHP_INJECTION = [
    ThreatPattern(
        name="php_eval_variable",
        category=ThreatCategory.CODE_INJECTION,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\beval\s*\(\s*\$'),
        description="PHP eval() with variable input",
        file_extensions=(".php",),
    ),
    ThreatPattern(
        name="php_preg_replace_e",
        category=ThreatCategory.CODE_INJECTION,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\bpreg_replace\s*\(\s*[\'"][^"\']*e[^\'"]*[\'"]'),
        description="PHP preg_replace with e modifier (code execution)",
        file_extensions=(".php",),
    ),
]

_PHP_OBFUSCATION = [
    ThreatPattern(
        name="php_base64_eval",
        category=ThreatCategory.OBFUSCATION,
        severity=ThreatSeverity.MEDIUM,
        regex=_compile(r'\b(?:eval|assert)\s*\(\s*(?:base64_decode|gzinflate|gzuncompress|str_rot13)\s*\('),
        description="Obfuscated PHP code execution (eval + decode)",
        file_extensions=(".php",),
    ),
]

_PHP_NETWORK = [
    ThreatPattern(
        name="php_network_access",
        category=ThreatCategory.NETWORK_ACCESS,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\b(?:fsockopen|pfsockopen|curl_exec|curl_multi_exec)\s*\('),
        description="PHP network socket/curl function",
        file_extensions=(".php",),
    ),
    ThreatPattern(
        name="php_remote_include",
        category=ThreatCategory.NETWORK_ACCESS,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\b(?:file_get_contents|include|require|include_once|require_once)\s*\(\s*[\'"]https?://'),
        description="PHP remote file inclusion/access",
        file_extensions=(".php",),
    ),
]

# ── JavaScript / Node.js Threat Patterns ─────────────────────────────────

_JS_DANGEROUS = [
    ThreatPattern(
        name="js_child_process",
        category=ThreatCategory.DANGEROUS_JS,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'''(?:require|import)\s*\(?\s*['"]child_process['"]\s*\)?'''),
        description="Node.js child_process module (shell execution)",
        file_extensions=(".js", ".mjs", ".cjs"),
    ),
    ThreatPattern(
        name="js_fs_module",
        category=ThreatCategory.DANGEROUS_JS,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'''(?:require|import)\s*\(?\s*['"]fs['"]\s*\)?'''),
        description="Node.js fs module (filesystem access)",
        file_extensions=(".js", ".mjs", ".cjs"),
    ),
    ThreatPattern(
        name="js_process_env",
        category=ThreatCategory.DANGEROUS_JS,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\bprocess\.env\b'),
        description="Node.js process.env access",
        file_extensions=(".js", ".mjs", ".cjs"),
    ),
    ThreatPattern(
        name="js_eval",
        category=ThreatCategory.CODE_INJECTION,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\beval\s*\('),
        description="JavaScript eval() call",
        file_extensions=(".js", ".mjs", ".cjs"),
    ),
]

# ── Filesystem Escape Patterns (all file types) ─────────────────────────

_FILESYSTEM_ESCAPE = [
    ThreatPattern(
        name="path_traversal",
        category=ThreatCategory.FILESYSTEM_ESCAPE,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'(?:\.\./){2,}|(?:\.\.[/\\]){2,}'),
        description="Path traversal attempt (multiple directory-up sequences)",
    ),
    ThreatPattern(
        name="sensitive_path_access",
        category=ThreatCategory.FILESYSTEM_ESCAPE,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'(?:/etc/passwd|/etc/shadow|/proc/self|/proc/\d+|/sys/|/dev/)'),
        description="Access attempt to sensitive system paths",
    ),
]

# ── Binary Injection ────────────────────────────────────────────────────

_BINARY_PATTERNS = [
    ThreatPattern(
        name="shebang_interpreter",
        category=ThreatCategory.BINARY_INJECTION,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'^#!\s*/(?:usr/)?(?:local/)?(?:bin|sbin)/'),
        description="Script with shebang targeting system interpreter",
        file_extensions=(".sh", ".py", ".pl", ".rb"),
    ),
]

# ── Shell Script Patterns ───────────────────────────────────────────────

_SHELL_PATTERNS = [
    ThreatPattern(
        name="shell_dangerous_commands",
        category=ThreatCategory.SHELL_EXECUTION,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\b(?:rm\s+-rf|chmod\s+777|mkfifo|nc\s+-[le]|ncat|socat)\b'),
        description="Dangerous shell command",
        file_extensions=(".sh", ".bash"),
    ),
]

# ── SQL Patterns ────────────────────────────────────────────────────────

_SQL_PATTERNS = [
    ThreatPattern(
        name="sql_file_ops",
        category=ThreatCategory.FILESYSTEM_ESCAPE,
        severity=ThreatSeverity.HIGH,
        regex=_compile(r'\b(?:LOAD_FILE|INTO\s+(?:OUTFILE|DUMPFILE))\b'),
        description="SQL file operation attempt",
        file_extensions=(".sql",),
    ),
]


# ── Public API ──────────────────────────────────────────────────────────

def get_all_patterns() -> List[ThreatPattern]:
    """Return the complete list of threat patterns."""
    return [
        *_PHP_SHELL,
        *_PHP_PROCESS,
        *_PHP_INJECTION,
        *_PHP_OBFUSCATION,
        *_PHP_NETWORK,
        *_JS_DANGEROUS,
        *_FILESYSTEM_ESCAPE,
        *_BINARY_PATTERNS,
        *_SHELL_PATTERNS,
        *_SQL_PATTERNS,
    ]


def get_patterns_for_extension(ext: str) -> List[ThreatPattern]:
    """Return patterns applicable to a given file extension.

    Patterns with empty *file_extensions* match all files.
    """
    ext = ext.lower()
    return [
        p for p in get_all_patterns()
        if not p.file_extensions or ext in p.file_extensions
    ]


# Files that should be scanned for text-based threats
SCANNABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".php", ".js", ".mjs", ".cjs", ".py", ".sh", ".bash",
    ".sql", ".html", ".htm", ".css", ".json", ".xml",
    ".pl", ".rb", ".inc",
})

# Binary file signatures (magic bytes) for detecting compiled executables
BINARY_SIGNATURES: dict[str, bytes] = {
    "ELF": b"\x7fELF",         # Linux executables
    "PE": b"MZ",               # Windows executables
    "Mach-O_32": b"\xfe\xed\xfa\xce",   # macOS 32-bit
    "Mach-O_64": b"\xfe\xed\xfa\xcf",   # macOS 64-bit
}


__all__ = [
    "ThreatCategory",
    "ThreatPattern",
    "ThreatSeverity",
    "BINARY_SIGNATURES",
    "SCANNABLE_EXTENSIONS",
    "get_all_patterns",
    "get_patterns_for_extension",
]
