"""Automated Marking System package."""

from .models import SubmissionContext, Finding, Severity
from .pipeline import AssessmentPipeline
from .scoring import ScoringEngine
from .reporting import ReportWriter
from .assessors import Assessor

__all__ = [
    "SubmissionContext",
    "Finding",
    "Severity",
    "AssessmentPipeline",
    "ScoringEngine",
    "ReportWriter",
    "Assessor",
]
