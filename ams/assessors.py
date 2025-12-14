from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .models import Finding, SubmissionContext


class Assessor(ABC):
    """Base class for assessment steps."""

    name: str

    @abstractmethod
    def run(self, context: SubmissionContext) -> List[Finding]:
        """Execute assessment and return findings."""
        raise NotImplementedError
