from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class RequiredPage:
    """Represents a required page in the coursework."""
    name: str
    path: str  # e.g., "index.html", "login.html"
    description: str
    required_features: List[str]  # IDs of required features


@dataclass
class RequiredFeature:
    """Represents a required feature in the coursework."""
    id: str
    name: str
    description: str
    feature_type: str  # "form", "navigation", "validation", "dynamic_update", "authentication"
    component: str  # "html", "js", "css", "php", "sql"
    weight: float = 1.0
    test_config: Dict[str, any] = None  # Test-specific configuration


@dataclass
class RequiredFlow:
    """Represents a required user flow."""
    id: str
    name: str
    description: str
    steps: List[Dict[str, str]]  # List of {action: "...", expected: "..."}
    component: str
    weight: float = 1.0


@dataclass
class CourseworkSpecification:
    """Complete coursework specification."""
    assignment_name: str
    assignment_id: str
    required_pages: List[RequiredPage]
    required_features: List[RequiredFeature]
    required_flows: List[RequiredFlow]
    performance_thresholds: Dict[str, float] = None  # e.g., {"page_load_ms": 3000, "interaction_ms": 500}


def create_default_coursework_spec() -> CourseworkSpecification:
    """Create a default coursework specification for testing."""
    return CourseworkSpecification(
        assignment_name="Web Development Assignment",
        assignment_id="webdev_001",
        required_pages=[
            RequiredPage(
                name="Home Page",
                path="index.html",
                description="Main landing page",
                required_features=["form_contact", "navigation_main"],
            ),
        ],
        required_features=[
            RequiredFeature(
                id="form_contact",
                name="Contact Form",
                description="Contact form with validation",
                feature_type="form",
                component="html",
                weight=2.0,
                test_config={
                    "selector": "form",
                    "fields": ["name", "email", "message"],
                    "validation": True,
                },
            ),
            RequiredFeature(
                id="navigation_main",
                name="Main Navigation",
                description="Navigation links between pages",
                feature_type="navigation",
                component="html",
                weight=1.5,
                test_config={
                    "selector": "nav a, .navigation a",
                    "min_links": 2,
                },
            ),
            RequiredFeature(
                id="js_interactivity",
                name="JavaScript Interactivity",
                description="Dynamic content updates via JavaScript",
                feature_type="dynamic_update",
                component="js",
                weight=2.5,
                test_config={
                    "trigger_selector": "button",
                    "expected_selector": ".dynamic-content",
                },
            ),
        ],
        required_flows=[
            RequiredFlow(
                id="form_submission_flow",
                name="Form Submission Flow",
                description="User submits form and sees confirmation",
                steps=[
                    {"action": "fill_form", "expected": "form_filled"},
                    {"action": "submit_form", "expected": "form_submitted"},
                    {"action": "verify_success", "expected": "success_message"},
                ],
                component="html",
                weight=2.0,
            ),
        ],
        performance_thresholds={
            "page_load_ms": 3000,
            "interaction_ms": 500,
            "dom_update_ms": 1000,
        },
    )


def load_coursework_spec_from_dict(data: Dict) -> CourseworkSpecification:
    """Load coursework specification from dictionary."""
    pages = [
        RequiredPage(
            name=p["name"],
            path=p["path"],
            description=p.get("description", ""),
            required_features=p.get("required_features", []),
        )
        for p in data.get("required_pages", [])
    ]
    
    features = [
        RequiredFeature(
            id=f["id"],
            name=f["name"],
            description=f.get("description", ""),
            feature_type=f.get("feature_type", "unknown"),
            component=f.get("component", "html"),
            weight=f.get("weight", 1.0),
            test_config=f.get("test_config", {}),
        )
        for f in data.get("required_features", [])
    ]
    
    flows = [
        RequiredFlow(
            id=fl["id"],
            name=fl["name"],
            description=fl.get("description", ""),
            steps=fl.get("steps", []),
            component=fl.get("component", "html"),
            weight=fl.get("weight", 1.0),
        )
        for fl in data.get("required_flows", [])
    ]
    
    return CourseworkSpecification(
        assignment_name=data.get("assignment_name", "Unknown Assignment"),
        assignment_id=data.get("assignment_id", "unknown"),
        required_pages=pages,
        required_features=features,
        required_flows=flows,
        performance_thresholds=data.get("performance_thresholds", {}),
    )


def load_coursework_spec_from_json(json_path: str) -> CourseworkSpecification:
    """Load coursework specification from JSON file."""
    import json
    from pathlib import Path
    
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Coursework spec file not found: {json_path}")
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return load_coursework_spec_from_dict(data)


__all__ = [
    "CourseworkSpecification",
    "RequiredPage",
    "RequiredFeature",
    "RequiredFlow",
    "create_default_coursework_spec",
    "load_coursework_spec_from_dict",
    "load_coursework_spec_from_json",
]

