"""Shared HTML parsing utilities used by both static and required assessors."""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Dict


class TagCountingParser(HTMLParser):
    """HTML parser that counts tags and tracks specific attributes for rule checking.

    This parser is shared across the required-elements and static HTML
    assessors so that HTML is parsed once and interrogated many times.
    """

    def __init__(self) -> None:
        super().__init__()
        self.counts: Dict[str, int] = {}

        # Semantic structure detection
        self.semantic_tags = {"header", "nav", "main", "section", "article", "aside", "footer"}
        self.has_semantic = False

        # Heading hierarchy detection (h1-h6)
        self.heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
        self.has_heading = False

        # List detection (ul, ol, dl)
        self.list_tags = {"ul", "ol", "dl"}
        self.has_list = False

        # Image alt attribute tracking
        self.img_count = 0
        self.img_with_alt = 0

        # Meta tag tracking
        self.has_meta_charset = False
        self.has_meta_viewport = False

        # HTML lang attribute tracking
        self.has_html_lang = False

        # Label tracking
        self.label_count = 0

        # DOCTYPE tracking
        self.has_doctype = False

        # Structure tracking (used by static assessor)
        self.has_html_tag = False
        self.has_head = False
        self.has_body = False

        # Code-quality tracking (used by static assessor)
        self.form_count = 0
        self.input_count = 0
        self.link_count = 0

        # Stylesheet and script linkage
        self.link_stylesheet_count = 0  # <link rel="stylesheet">
        self.script_count = 0           # <script> tags (inline or src)

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        lowered = tag.lower()
        self.counts[lowered] = self.counts.get(lowered, 0) + 1
        attrs_dict = dict(attrs)

        # Structure elements
        if lowered == "html":
            self.has_html_tag = True
            if "lang" in attrs_dict and attrs_dict["lang"]:
                self.has_html_lang = True
        elif lowered == "head":
            self.has_head = True
        elif lowered == "body":
            self.has_body = True

        # Semantic elements
        if lowered in self.semantic_tags:
            self.has_semantic = True

        # Heading elements
        if lowered in self.heading_tags:
            self.has_heading = True

        # List elements
        if lowered in self.list_tags:
            self.has_list = True

        # Image tags
        if lowered == "img":
            self.img_count += 1
            if "alt" in attrs_dict and attrs_dict["alt"]:
                self.img_with_alt += 1

        # Meta tags
        if lowered == "meta":
            if "charset" in attrs_dict:
                self.has_meta_charset = True
            if attrs_dict.get("http-equiv", "").lower() == "content-type":
                content = attrs_dict.get("content", "")
                if "charset" in content.lower():
                    self.has_meta_charset = True
            if attrs_dict.get("name", "").lower() == "viewport":
                self.has_meta_viewport = True

        # Form-related elements
        if lowered == "form":
            self.form_count += 1
        elif lowered == "input":
            self.input_count += 1
        elif lowered == "a":
            self.link_count += 1

        # Labels
        if lowered == "label":
            self.label_count += 1

        # Stylesheet linkage: <link rel="stylesheet" ...>
        if lowered == "link":
            rel = attrs_dict.get("rel", "") or ""
            if "stylesheet" in rel.lower():
                self.link_stylesheet_count += 1

        # Script tags: <script> or <script src="...">
        if lowered == "script":
            self.script_count += 1

    def handle_startendtag(self, tag: str, attrs) -> None:  # type: ignore[override]
        self.handle_starttag(tag, attrs)

    def handle_decl(self, decl: str) -> None:
        """Handle declarations like <!DOCTYPE html>."""
        if decl.lower().startswith("doctype"):
            self.has_doctype = True


__all__ = ["TagCountingParser"]
