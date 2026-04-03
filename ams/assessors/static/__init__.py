from __future__ import annotations

from .html_static import HTMLStaticAssessor
from .css_static import CSSStaticAssessor
from .js_static import JSStaticAssessor
from .php_static import PHPStaticAssessor
from .sql_static import SQLStaticAssessor
from .api_static import APIStaticAssessor

__all__ = [
    "HTMLStaticAssessor",
    "CSSStaticAssessor",
    "JSStaticAssessor",
    "PHPStaticAssessor",
    "SQLStaticAssessor",
    "APIStaticAssessor",
]
