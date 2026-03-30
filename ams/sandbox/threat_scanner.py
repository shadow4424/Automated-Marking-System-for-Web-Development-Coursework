"""Pre-execution threat scanner for student submissions."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from ams.sandbox.threat_patterns import (
    BINARY_SIGNATURES,
    SCANNABLE_EXTENSIONS,
    ThreatCategory,
    ThreatPattern,
    ThreatSeverity,
    get_patterns_for_extension,
)

logger = logging.getLogger(__name__)

# Maximum file size to scan (skip huge files to avoid DOS on scanner itself)
_MAX_SCAN_BYTES = 2 * 1024 * 1024  # 2 MB


@dataclass
class ThreatFinding:
    """A single threat detected in a submission file."""
    file: str              # Relative path within the submission
    line: int              # 1-based line number (0 for binary checks)
    pattern_name: str      # Name of the matched ThreatPattern
    category: ThreatCategory
    severity: ThreatSeverity
    snippet: str           # Matched line / context (truncated)
    description: str       # Human-readable description


@dataclass
class ScanResult:
    """Aggregate result from scanning an entire submission."""
    threats: List[ThreatFinding] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped: int = 0

    @property
    def has_high_threats(self) -> bool:
        return any(t.severity == ThreatSeverity.HIGH for t in self.threats)

    @property
    def threat_count(self) -> int:
        return len(self.threats)

    @property
    def high_count(self) -> int:
        return sum(1 for t in self.threats if t.severity == ThreatSeverity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for t in self.threats if t.severity == ThreatSeverity.MEDIUM)

    @property
    def low_count(self) -> int:
        return sum(1 for t in self.threats if t.severity == ThreatSeverity.LOW)


class ThreatScanner:
    """Scans submission files against the threat pattern registry."""

    def scan(self, submission_path: Path) -> ScanResult:
        """Scan all files under *submission_path* for threat patterns. Returns a:class:`ScanResult` containing all detected threats."""
        result = ScanResult()

        if not submission_path.is_dir():
            logger.warning("Threat scanner: path is not a directory: %s", submission_path)
            return result

        for root, dirs, files in os.walk(submission_path):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for fname in files:
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()

                # Binary detection (check magic bytes regardless of extension)
                binary_threats = self._check_binary(fpath, submission_path)
                if binary_threats:
                    result.threats.extend(binary_threats)
                    result.files_scanned += 1
                    continue

                # Symlink detection
                if fpath.is_symlink():
                    target = os.readlink(fpath)
                    try:
                        resolved = fpath.resolve()
                        if not str(resolved).startswith(str(submission_path.resolve())):
                            result.threats.append(ThreatFinding(
                                file=str(fpath.relative_to(submission_path)),
                                line=0,
                                pattern_name="symlink_escape",
                                category=ThreatCategory.SYMLINK_ATTACK,
                                severity=ThreatSeverity.HIGH,
                                snippet=f"Symlink → {target}",
                                description="Symlink pointing outside submission directory",
                            ))
                    except (OSError, ValueError):
                        result.threats.append(ThreatFinding(
                            file=str(fpath.relative_to(submission_path)),
                            line=0,
                            pattern_name="symlink_unresolvable",
                            category=ThreatCategory.SYMLINK_ATTACK,
                            severity=ThreatSeverity.HIGH,
                            snippet=f"Symlink → {target}",
                            description="Symlink that cannot be resolved (potential escape)",
                        ))
                    result.files_scanned += 1
                    continue

                # Only scan text-based files for pattern matching
                if ext not in SCANNABLE_EXTENSIONS:
                    result.files_skipped += 1
                    continue

                self._scan_file(fpath, submission_path, ext, result)
                result.files_scanned += 1

        if result.threats:
            logger.warning(
                "Threat scanner found %d threat(s) [%d HIGH, %d MEDIUM, %d LOW] "
                "across %d files",
                result.threat_count,
                result.high_count,
                result.medium_count,
                result.low_count,
                result.files_scanned,
            )
        else:
            logger.info(
                "Threat scanner: clean — scanned %d files, skipped %d",
                result.files_scanned,
                result.files_skipped,
            )

        return result

    def _scan_file(
        self,
        fpath: Path,
        submission_root: Path,
        ext: str,
        result: ScanResult,
    ) -> None:
        """Scan a single text file against applicable patterns."""
        patterns = get_patterns_for_extension(ext)
        if not patterns:
            return

        try:
            size = fpath.stat().st_size
            if size > _MAX_SCAN_BYTES:
                result.files_skipped += 1
                logger.debug("Skipping oversized file: %s (%d bytes)", fpath.name, size)
                return

            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read file for threat scanning: %s (%s)", fpath, exc)
            return

        rel_path = str(fpath.relative_to(submission_root))

        for line_no, line in enumerate(content.splitlines(), start=1):
            for pattern in patterns:
                if pattern.regex.search(line):
                    result.threats.append(ThreatFinding(
                        file=rel_path,
                        line=line_no,
                        pattern_name=pattern.name,
                        category=pattern.category,
                        severity=pattern.severity,
                        snippet=line.strip()[:200],
                        description=pattern.description,
                    ))

    def _check_binary(self, fpath: Path, submission_root: Path) -> List[ThreatFinding]:
        """Check a file for binary executable signatures (magic bytes)."""
        threats: List[ThreatFinding] = []
        try:
            with open(fpath, "rb") as f:
                header = f.read(4)
        except OSError:
            return threats

        for sig_name, sig_bytes in BINARY_SIGNATURES.items():
            if header[:len(sig_bytes)] == sig_bytes:
                threats.append(ThreatFinding(
                    file=str(fpath.relative_to(submission_root)),
                    line=0,
                    pattern_name=f"binary_{sig_name.lower()}",
                    category=ThreatCategory.BINARY_INJECTION,
                    severity=ThreatSeverity.HIGH,
                    snippet=f"Binary header: {sig_name}",
                    description=f"Compiled executable detected ({sig_name} binary)",
                ))
                break  # One signature match is enough

        return threats


__all__ = ["ScanResult", "ThreatFinding", "ThreatScanner"]
