from __future__ import annotations

from .html import HTMLStaticAssessor
from .css import CSSStaticAssessor
from .js import JSStaticAssessor
from .php import PHPStaticAssessor
from .sql import SQLStaticAssessor
from .api import APIStaticAssessor

__all__ = [
    "HTMLStaticAssessor",
    "CSSStaticAssessor",
    "JSStaticAssessor",
    "PHPStaticAssessor",
    "SQLStaticAssessor",
    "APIStaticAssessor",
]
