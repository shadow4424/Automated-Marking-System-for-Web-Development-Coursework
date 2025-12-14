from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Mapping, MutableMapping


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


@dataclass
class SubmissionContext:
    submission_path: Path
    workspace_path: Path
    discovered_files: MutableMapping[str, List[Path]] = field(default_factory=dict)
    metadata: MutableMapping[str, object] = field(default_factory=dict)


@dataclass
class Finding:
    id: str
    category: str
    message: str
    severity: Severity
    evidence: Mapping[str, object]
    source: str
