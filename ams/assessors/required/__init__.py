from __future__ import annotations

from .html_required import HTMLRequiredElementsAssessor
from .css_required import CSSRequiredRulesAssessor
from .js_required import JSRequiredFeaturesAssessor
from .php_required import PHPRequiredFeaturesAssessor
from .sql_required import SQLRequiredFeaturesAssessor
from .api_required import APIRequiredFeaturesAssessor

__all__ = [
    "HTMLRequiredElementsAssessor",
    "CSSRequiredRulesAssessor",
    "JSRequiredFeaturesAssessor",
    "PHPRequiredFeaturesAssessor",
    "SQLRequiredFeaturesAssessor",
    "APIRequiredFeaturesAssessor",
]
