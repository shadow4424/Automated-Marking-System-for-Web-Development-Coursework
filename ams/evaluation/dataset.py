"""Dataset loading for the AMS evaluation framework.

Loads and validates the manifest.json that describes labelled submissions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ManifestEntry:
    """One labelled submission entry from manifest.json."""
    id: str
    path: str                              # relative to dataset root
    category: str                          # correct / partial / incorrect / frontend_only / robustness/*
    profile: str                           # AMS profile to use for marking
    expected_overall: Optional[float]      # None for robustness entries (no expected score)
    expected_components: dict              # e.g. {"html": 1.0, "css": 1.0, "js": 0.0}
    notes: str = ""

    def abs_path(self, dataset_root: Path) -> Path:
        return dataset_root / self.path

    def is_robustness(self) -> bool:
        return self.category.startswith("robustness")


def load_manifest(dataset_path: Path) -> list[ManifestEntry]:
    """Load all entries from manifest.json inside dataset_path.

    Raises FileNotFoundError if manifest.json is missing.
    Raises ValueError if any entry path does not exist on disk.
    """
    manifest_path = dataset_path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found at {manifest_path}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: list[ManifestEntry] = []

    for raw in data.get("submissions", []):
        entry = ManifestEntry(
            id=str(raw["id"]),
            path=str(raw["path"]),
            category=str(raw.get("category", "")),
            profile=str(raw.get("profile", "frontend")),
            expected_overall=raw.get("expected_overall"),  # may be None
            expected_components=dict(raw.get("expected_components") or {}),
            notes=str(raw.get("notes", "")),
        )
        entries.append(entry)

    return entries


def get_accuracy_entries(dataset_path: Path) -> list[ManifestEntry]:
    """Return entries suitable for accuracy evaluation (non-robustness, has expected_overall)."""
    return [
        e for e in load_manifest(dataset_path)
        if not e.is_robustness() and e.expected_overall is not None
    ]


def get_robustness_entries(dataset_path: Path) -> list[ManifestEntry]:
    """Return only robustness entries."""
    return [e for e in load_manifest(dataset_path) if e.is_robustness()]


def get_llm_attempt_entries(dataset_path: Path) -> list[ManifestEntry]:
    """Return only LLM attempt entries (category starts with 'llm_attempt')."""
    return [
        e for e in load_manifest(dataset_path)
        if e.category.startswith("llm_attempt")
    ]
