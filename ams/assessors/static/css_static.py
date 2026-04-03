from __future__ import annotations

import re
from pathlib import Path
from typing import List

from ams.assessors.static.base_static import BaseStaticAssessor
from ams.core.finding_ids import CSS as CID
from ams.core.models import Finding, FindingCategory, Severity


class CSSStaticAssessor(BaseStaticAssessor):
    """Deterministic CSS static checks."""

    _component = "css"
    _finding_ids_class = CID
    _extensions = [".css"]

    _SPECIFICITY_WARN_THRESHOLD = 120

    def _analyse_loaded_files(
        self, loaded_files: list[tuple[Path, str]],
    ) -> List[Finding]:
        findings: List[Finding] = []
        for path, content in loaded_files:
            structure = self._structure_stats(path, content)
            findings.append(self._structure_finding(structure))
            findings.append(self._evidence_finding(structure))

            quality = self._specificity_quality(content)
            if quality["overly_specific_selectors"]:
                findings.append(
                    Finding(
                        id=CID.QUALITY_OVERLY_SPECIFIC,
                        category="css",
                        message=f"Found {len(quality['overly_specific_selectors'])} overly specific selector(s). High specificity makes CSS harder to maintain and override.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "overly_specific_count": len(quality["overly_specific_selectors"]),
                            "max_specificity": quality["max_specificity_score"],
                            "threshold": self._SPECIFICITY_WARN_THRESHOLD,
                            "examples": quality["overly_specific_selectors"][:5],
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

        return findings

    def _structure_stats(self, path: Path, content: str) -> dict[str, object]:
        open_braces = content.count("{")
        close_braces = content.count("}")
        balanced = open_braces == close_braces and open_braces > 0
        lowered = content.lower()
        return {
            "path": str(path),
            "open_braces": open_braces,
            "close_braces": close_braces,
            "balanced": balanced,
            "has_selector_hint": self._has_selector_hint(content),
            "has_at_rule": "@media" in lowered or "@keyframes" in lowered,
            "media_queries": lowered.count("@media"),
            "keyframes": lowered.count("@keyframes"),
            "important": lowered.count("!important"),
        }

    @staticmethod
    def _has_selector_hint(content: str) -> bool:
        for line in content.splitlines():
            if "{" in line:
                before = line.split("{", 1)[0].strip()
                if before and not before.startswith("@"):
                    return True
        return False

    def _structure_finding(self, structure: dict[str, object]) -> Finding:
        open_braces = int(structure["open_braces"])
        close_braces = int(structure["close_braces"])
        balanced = bool(structure["balanced"])
        if balanced:
            finding_id = CID.BRACES_BALANCED
            message = "CSS braces appear balanced."
            severity = Severity.INFO
        elif open_braces == 0 and close_braces == 0:
            finding_id = CID.NO_RULES
            message = "No CSS rules detected."
            severity = Severity.WARN
        else:
            finding_id = CID.BRACES_UNBALANCED
            message = "CSS braces appear unbalanced."
            severity = Severity.WARN

        return Finding(
            id=finding_id,
            category="css",
            message=message,
            severity=severity,
            evidence={
                "path": structure["path"],
                "open_braces": open_braces,
                "close_braces": close_braces,
                "balanced": balanced,
                "has_selector_hint": structure["has_selector_hint"],
                "has_at_rule": structure["has_at_rule"],
            },
            source=self.name,
        )

    def _evidence_finding(self, structure: dict[str, object]) -> Finding:
        return Finding(
            id=CID.EVIDENCE,
            category="css",
            message="CSS evidence collected.",
            severity=Severity.INFO,
            evidence={
                "path": structure["path"],
                "selectors_approx": structure["open_braces"],
                "media_queries": structure["media_queries"],
                "keyframes": structure["keyframes"],
                "important": structure["important"],
            },
            source=self.name,
        )

    def _specificity_quality(self, content: str) -> dict[str, object]:
        overly_specific_selectors: list[dict[str, object]] = []
        max_specificity_score = 0
        for line in content.splitlines():
            if "{" not in line:
                continue
            selector_part = line.split("{", 1)[0].strip()
            if not selector_part or selector_part.startswith("@"):
                continue
            specificity = self._selector_specificity(selector_part)
            max_specificity_score = max(max_specificity_score, specificity)
            if specificity > self._SPECIFICITY_WARN_THRESHOLD:
                overly_specific_selectors.append(
                    {"selector": selector_part[:50], "specificity": specificity}
                )
        return {
            "overly_specific_selectors": overly_specific_selectors,
            "max_specificity_score": max_specificity_score,
        }

    @staticmethod
    def _selector_specificity(selector_part: str) -> int:
        id_count = selector_part.count("#")
        class_count = selector_part.count(".")
        element_count = len(re.findall(r"\b[a-z]+\b", selector_part.lower()))
        return id_count * 100 + class_count * 10 + element_count


__all__ = ["CSSStaticAssessor"]
