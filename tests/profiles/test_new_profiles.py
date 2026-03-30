"""Tests for newly added profiles: frontend_css_lab and frontend_calculator."""

from __future__ import annotations

import pytest

from ams.core.profiles import get_profile_spec, list_profile_names


def test_frontend_css_lab_profile_exists():
    spec = get_profile_spec("frontend_css_lab")
    assert spec is not None
    assert spec.name == "frontend_css_lab"


def test_frontend_css_lab_alias_works():
    spec = get_profile_spec("css_lab")
    assert spec is not None
    assert spec.name == "frontend_css_lab"


def test_frontend_css_lab_has_css_lab_rules():
    spec = get_profile_spec("frontend_css_lab")
    css_rule_ids = {r.id for r in spec.required_css}
    # Must include all base CSS rules plus the lab-specific ones
    assert "css.has_rule_block" in css_rule_ids
    assert "css.body_card_layout" in css_rule_ids
    assert "css.h1_styled" in css_rule_ids
    assert "css.link_hover_style" in css_rule_ids
    assert "css.h2_section_style" in css_rule_ids


def test_frontend_css_lab_has_extended_browser():
    spec = get_profile_spec("frontend_css_lab")
    assert "extended_browser" in spec.enabled_browser_checks


def test_frontend_calculator_profile_exists():
    spec = get_profile_spec("frontend_calculator")
    assert spec is not None
    assert spec.name == "frontend_calculator"


def test_frontend_calculator_has_calculator_js_rules():
    spec = get_profile_spec("frontend_calculator")
    js_rule_ids = {r.id for r in spec.required_js}
    # Must include base JS rules + calculator-specific ones
    assert "js.has_event_listener" in js_rule_ids
    assert "js.creates_display_dom" in js_rule_ids
    assert "js.creates_digit_buttons" in js_rule_ids
    assert "js.creates_operator_buttons" in js_rule_ids
    assert "js.has_updateDisplay" in js_rule_ids
    assert "js.has_prevalue_preop_state" in js_rule_ids
    assert "js.has_doCalc" in js_rule_ids
    assert "js.uses_createElement" in js_rule_ids
    assert "js.avoids_document_write" in js_rule_ids


def test_frontend_calculator_has_extended_browser():
    spec = get_profile_spec("frontend_calculator")
    assert "extended_browser" in spec.enabled_browser_checks


def test_new_profiles_in_profile_list():
    names = list_profile_names(include_aliases=True)
    assert "frontend_css_lab" in names
    assert "frontend_calculator" in names
    assert "css_lab" in names  # Alias


def test_frontend_css_lab_relevant_artefacts():
    spec = get_profile_spec("frontend_css_lab")
    assert "html" in spec.relevant_artefacts
    assert "css" in spec.relevant_artefacts
    assert "js" in spec.relevant_artefacts


def test_frontend_calculator_relevant_artefacts():
    spec = get_profile_spec("frontend_calculator")
    assert "html" in spec.relevant_artefacts
    assert "css" in spec.relevant_artefacts
    assert "js" in spec.relevant_artefacts


def test_frontend_css_lab_end_to_end_pass(build_submission, run_pipeline):
    """A CSS file with lab-quality styling should pass all lab CSS rules."""
    css = (
        "* { box-sizing: border-box; }\n"
        "body { max-width: 900px; margin: 0 auto; padding: 20px; "
        "box-shadow: 0 2px 8px rgba(0,0,0,0.2); border-radius: 10px; "
        "font-family: sans-serif; color: #333; }\n"
        "h1 { font-size: 2em; color: #0066cc; }\n"
        "h2 { font-size: 1.4em; color: #444; }\n"
        "a { text-decoration: none; }\n"
        "a:hover { text-decoration: underline; color: #0066cc; }\n"
        "ul { list-style: disc; padding-left: 1.5em; }\n"
        "table { width: 100%; border-collapse: collapse; }\n"
        "img { border-radius: 50%; box-shadow: 0 1px 4px #000; }\n"
    )
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend_css_lab")
    passes = [f for f in data["findings"] if f["id"] == "CSS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "css.body_card_layout" in rule_ids
    assert "css.h1_styled" in rule_ids
    assert "css.link_hover_style" in rule_ids
