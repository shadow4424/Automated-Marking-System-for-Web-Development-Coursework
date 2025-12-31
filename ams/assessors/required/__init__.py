from __future__ import annotations

from .html import HTMLRequiredElementsAssessor
from .css import CSSRequiredRulesAssessor
from .js import JSRequiredFeaturesAssessor
from .php import PHPRequiredFeaturesAssessor
from .sql import SQLRequiredFeaturesAssessor

__all__ = [
    "HTMLRequiredElementsAssessor",
    "CSSRequiredRulesAssessor",
    "JSRequiredFeaturesAssessor",
    "PHPRequiredFeaturesAssessor",
    "SQLRequiredFeaturesAssessor",
]
