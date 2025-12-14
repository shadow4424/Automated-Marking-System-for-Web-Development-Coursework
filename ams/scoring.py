from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping
from datetime import datetime, timezone

from .models import Finding


class ScoringEngine:
    """Placeholder scoring engine."""

    def score(self, findings: Iterable[Finding]) -> Mapping[str, object]:
        findings_list = list(findings)
        return {
            "summary": "scoring not implemented",
            "total_findings": len(findings_list),
            "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        }
