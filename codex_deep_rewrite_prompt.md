# AMS Deep Algorithmic Rewrite — Codex Prompt

You are rewriting four Python files in an Automated Marking System codebase on branch `ground_up`.
Each file has large methods that can be made significantly shorter through algorithmic improvements
without changing any external behaviour.

**Working directory:** `e:\0. 3rd Year Project\0.Code\Automated-Marking-System-for-Web-Development-Coursework`
**Branch:** `ground_up` (already checked out)
**Verification gate:** Run `pytest tests/ -x` after each file. All 515 tests must pass.

---

## GLOBAL RULES

1. Do NOT change any function signatures, return types, or public method names.
2. Do NOT change any dict key names in returned data structures.
3. Do NOT alter `report.json` schema, `ScoreEvidenceBundle.to_dict()` output, or `Finding` fields.
4. Make four atomic commits — one per file, each with a passing test run.
5. Read each full file before editing it.

---

## FILE 1 — `ams/core/requirements.py` (981 lines → target ~450)

### Task 1A — Replace `_evaluate_definition()` dispatch chain with a registry

**Current code (L157–196):** a sequence of `if evaluator == "..."` checks dispatching to 12 different methods.

**Replace with:**

```python
_EVALUATOR_MAP: ClassVar[dict[str, str]] = {
    "required_rule":          "_evaluate_static_rule",
    "behavioral_rule":        "_evaluate_behavioral_rule",
    "browser_page_load":      "_evaluate_browser_page_load",
    "browser_interaction":    "_evaluate_browser_interaction",
    "layout_responsive":      "_evaluate_layout_requirement",
    "quality_penalty":        "_evaluate_quality_penalty",
    "api_usage":              "_evaluate_api_usage",
    "cross_file_result":      "_evaluate_cross_file_result",
    "browser_console_clean":  "_evaluate_browser_console_clean",
    "browser_network_assets": "_evaluate_browser_network_assets",
    "browser_dom_structure":  "_evaluate_browser_dom_structure",
    "browser_accessibility":  "_evaluate_browser_accessibility",
}

def _evaluate_definition(
    self,
    *,
    definition: RequirementDefinition,
    context: SubmissionContext,
    findings: Sequence[Finding],
    profile: ProfileSpec,
) -> RequirementEvaluationResult:
    handler_name = self._EVALUATOR_MAP.get(definition.evaluator)
    if handler_name is None:
        return self._skip_without_browser_evidence(definition)
    handler = getattr(self, handler_name)
    # Static rule and behavioral rule need extra args
    if definition.evaluator == "required_rule":
        return handler(definition, context, profile)
    if definition.evaluator == "behavioral_rule":
        return handler(definition, context)
    if definition.evaluator in ("layout_responsive", "quality_penalty"):
        return handler(definition, context, findings)
    return handler(definition, context)
```

**Verify** the evaluator string values match what `ProfileSpec` / `RequirementDefinition.evaluator` actually produces — grep the profiles package before editing.

---

### Task 1B — Replace `_evaluate_browser_console_clean`, `_evaluate_browser_network_assets`, `_evaluate_browser_dom_structure`, `_evaluate_browser_accessibility` with a data table

These 4 methods (total ~120 lines) all follow this identical algorithm:
1. Guard: `if not context.browser_evidence: return self._skip_without_browser_evidence(definition)`
2. Extract one field from `browser = context.browser_evidence[0]`
3. Apply threshold/boolean check
4. Call `build_requirement_result(definition, score=..., status=..., evidence=...)`

**Add this dataclass and table before the class:**

```python
@dataclass(frozen=True)
class _BrowserCheckSpec:
    evidence_attr: str       # attribute name on BrowserRunResult
    check_fn: str            # "empty_list", "lte", "gte", "bool_false"
    threshold: float | None  # for lte/gte comparisons
    pass_evidence_key: str   # key name in evidence dict
```

**Add a single dispatcher method:**

```python
def _run_browser_check(
    self,
    definition: RequirementDefinition,
    context: SubmissionContext,
    spec: _BrowserCheckSpec,
) -> RequirementEvaluationResult:
    if not context.browser_evidence:
        return self._skip_without_browser_evidence(definition)
    browser = context.browser_evidence[0]
    value = getattr(browser, spec.evidence_attr, None)
    # Delegate check logic inline — avoids another level of dispatch
    if spec.check_fn == "empty_list":
        items = value or []
        if len(items) == 0:
            score, status = 1.0, "PASS"
        elif len(items) <= 2:
            score, status = 0.5, "PARTIAL"
        else:
            score, status = 0.0, "FAIL"
        evidence = {spec.pass_evidence_key: len(items), "sample": items[:5]}
    elif spec.check_fn == "lte":
        ms = value or 0
        if ms <= spec.threshold:
            score, status = 1.0, "PASS"
        elif ms <= spec.threshold * 1.5:
            score, status = 0.5, "PARTIAL"
        else:
            score, status = 0.0, "FAIL"
        evidence = {spec.pass_evidence_key: ms}
    else:
        score, status = (1.0, "PASS") if value else (0.0, "FAIL")
        evidence = {spec.pass_evidence_key: value}
    return build_requirement_result(definition, score=score, status=status, evidence=evidence)
```

**Replace each of the 4 methods** with a one-liner that calls `self._run_browser_check(definition, context, <spec>)`. Keep `_evaluate_browser_page_load` and `_evaluate_browser_interaction` as-is for now (they have more complex interaction logic).

---

### Task 1C — Simplify `_finding_from_requirement()` (L836–889, 54 lines)

The current method builds a `mapping` dict per component, then has 4 if/elif branches setting `(finding_id, severity, finding_category)`. It can be expressed as two lookups:

```python
def _finding_from_requirement(
    self,
    result: RequirementEvaluationResult,
    *,
    profile_name: str,
) -> Finding:
    # ID lookup table — keep exact same ID constants
    _IDS = {
        "html": (HID.REQ_PASS, HID.REQ_FAIL, HID.REQ_SKIPPED, HID.REQ_MISSING_FILES),
        "css":  (CID.REQ_PASS, CID.REQ_FAIL, CID.REQ_SKIPPED, CID.REQ_MISSING_FILES),
        "js":   (JID.REQ_PASS, JID.REQ_FAIL, JID.REQ_SKIPPED, JID.REQ_MISSING_FILES),
        "php":  (PID.REQ_PASS, PID.REQ_FAIL, PID.REQ_SKIPPED, PID.REQ_MISSING_FILES),
        "sql":  (SLID.REQ_PASS, SLID.REQ_FAIL, SLID.REQ_SKIPPED, SLID.REQ_MISSING_FILES),
        "api":  (AID.REQ_PASS, AID.REQ_FAIL, AID.REQ_SKIPPED, AID.REQ_MISSING_FILES),
    }
    passed_id, failed_id, skipped_id, missing_id = _IDS[result.component]

    # Status → (finding_id, severity, category)
    _STATUS = {
        "PASS":    (passed_id,  Severity.INFO,    FindingCategory.STRUCTURE),
        "SKIPPED": (skipped_id, Severity.SKIPPED, FindingCategory.OTHER),
    }
    if result.status in _STATUS:
        finding_id, severity, finding_category = _STATUS[result.status]
    elif result.skipped_reason == "no_relevant_files":
        finding_id, severity, finding_category = missing_id, Severity.FAIL, FindingCategory.MISSING
    else:
        finding_id, severity, finding_category = failed_id, Severity.WARN, FindingCategory.MISSING

    snippets = result.evidence.get("snippets")
    primary_snippet = str(snippets[0]) if isinstance(snippets, list) and snippets else ""
    evidence = {**result.evidence, "rule_id": result.evidence.get("rule_id", result.requirement_id)}
    evidence.setdefault("weight", getattr(result, "weight", 1.0))

    return Finding(
        id=finding_id,
        category=result.component,
        message=_build_requirement_message(result),
        severity=severity,
        source="requirement_engine",
        evidence=evidence,
        finding_category=finding_category,
        profile=profile_name,
        required=True,
        snippet=primary_snippet,
    )
```

**Verify** the exact alias names (`HID`, `CID`, `JID`, `PID`, `SLID`, `AID`) by checking the imports at the top of the existing file — do not guess.

---

### Task 1D — Simplify `evaluate()` (L90–157, 67 lines)

Extract the per-definition try/except wrapper and simplify the main loop:

```python
def _safe_evaluate_definition(self, definition, context, findings_list, profile):
    try:
        return self._evaluate_definition(
            definition=definition, context=context,
            findings=findings_list, profile=profile,
        )
    except Exception as exc:
        logger.exception("Failed to evaluate requirement %s: %s", definition.requirement_id, exc)
        return build_skipped_requirement_result(
            definition, skipped_reason="evaluation_error"
        )

def evaluate(self, context, findings):
    resolved = context.resolved_config
    if resolved is None:
        raise ValueError("SubmissionContext.resolved_config must be populated before evaluating requirements")
    profile = resolved.profile
    profile_name = str(context.metadata.get("profile") or profile.name)
    findings_list = list(findings)

    results = [
        self._safe_evaluate_definition(defn, context, findings_list, profile)
        for defn in resolved.requirement_definitions
    ]
    generated_findings = [
        self._finding_from_requirement(r, profile_name=profile_name)
        for r, defn in zip(results, resolved.requirement_definitions)
        if defn.stage == "static" and defn.rule is not None
    ]
    # Keep any existing post-loop component summary logic below here unchanged
    ...
```

**Keep** any existing component-summary loop at the bottom of `evaluate()` — do not delete it.

---

**Commit after Task 1A–1D passes tests:**
```
refactor(core): algorithmic rewrite of requirements.py — registry dispatch, browser check table
```

---

## FILE 2 — `ams/core/scoring.py` (566 lines → target ~300)

### Task 2A — Move `_enrich_with_llm_hybrid()` out of scoring.py

This 137-line method (L182–318) is only called when `SCORING_MODE == STATIC_PLUS_LLM`. It belongs in the LLM module.

1. Create `ams/llm/scoring_integration.py` with the full body of `_enrich_with_llm_hybrid` moved there, renamed to `enrich_with_llm_hybrid(engine, static_results, findings)` where `engine` is the `ScoringEngine` instance (pass `self`).

2. In `scoring.py`, replace the method body:
```python
def _enrich_with_llm_hybrid(self, static_results, findings):
    from ams.llm.scoring_integration import enrich_with_llm_hybrid
    return enrich_with_llm_hybrid(self, static_results, findings)
```

3. Move `_apply_llm_hybrid_to_requirement_results()` (L338–383, 46 lines) into `scoring_integration.py` as well — it is only called from `_enrich_with_llm_hybrid`.

**Net removal from scoring.py:** 137 + 46 = 183 lines → 3 lines.

---

### Task 2B — Collapse `_score_from_requirements()` + `_run_static_evaluation()` + `_enrich_with_llm_hybrid()` call chain

The current chain is:
```
score_with_evidence() → _score_from_requirements() → _run_static_evaluation() → _enrich_with_llm_hybrid()
```

`_score_from_requirements()` (L319–337, 19 lines) is a thin pass-through that:
1. Calls `_run_static_evaluation()` → gets `static_results`
2. Calls `_enrich_with_llm_hybrid(static_results, findings)` → returns result

After Task 2A, `_enrich_with_llm_hybrid` is a 3-line wrapper. Inline `_score_from_requirements` into `score_with_evidence`:

```python
# In score_with_evidence(), replace the branch:
#   return self._score_from_requirements(...)
# with:
static_results = self._run_static_evaluation(
    findings_list, context=context, resolved_config=resolved_config,
    behavioural_evidence=behavioural_evidence_list,
    browser_evidence=browser_evidence_list,
)
return self._enrich_with_llm_hybrid(static_results, findings_list)
```

Delete `_score_from_requirements`. Net: -19 lines.

---

### Task 2C — Simplify `score_with_evidence()` (L60–153, 94 lines)

The method has two branches:
- Branch A (L73–80): `context is not None and resolved_config is not None` → calls `_score_from_requirements`
- Branch B (L81–153): legacy fallback using `by_category` finding dict

After Task 2B, Branch A inlines to ~6 lines. Branch B is the legacy path — keep it unchanged but extract its component-scoring loop into `_score_components_legacy(by_category, relevant_components, profile) -> tuple`:

```python
def score_with_evidence(self, findings, profile=None, context=None, resolved_config=None,
                        behavioural_evidence=None, browser_evidence=None):
    findings_list = list(findings)
    beh = list(behavioural_evidence or [])
    brw = list(browser_evidence or [])
    if context is not None and resolved_config is not None:
        static_results = self._run_static_evaluation(
            findings_list, context=context, resolved_config=resolved_config,
            behavioural_evidence=beh, browser_evidence=brw,
        )
        return self._enrich_with_llm_hybrid(static_results, findings_list)
    # Legacy path
    return self._score_components_legacy(findings_list, profile, beh, brw)

def _score_components_legacy(self, findings_list, profile, beh, brw):
    by_category = {c: [] for c in self.COMPONENTS}
    for f in findings_list:
        if f.category in by_category:
            by_category[f.category].append(f)
    relevant = self._determine_relevant_components(profile)
    # ... rest of Branch B logic here
```

Target: `score_with_evidence` 94 lines → ~15 lines. `_score_components_legacy` holds the extracted ~79 lines.

---

**Commit after Tasks 2A–2C pass tests:**
```
refactor(core): move LLM hybrid scoring to llm/scoring_integration.py, slim ScoringEngine
```

---

## FILE 3 — `ams/core/pipeline.py` (560 lines → target ~300)

### Task 3A — Extract per-assessor try/except from `_run_analysis()` (L126–205, 80 lines)

The assessors loop currently has ad-hoc error handling. Extract a safe runner:

```python
def _run_assessor_safe(self, assessor: Assessor, context: SubmissionContext) -> list[Finding]:
    try:
        return list(assessor.run(context))
    except Exception as exc:
        logger.exception("Assessor %s failed: %s", type(assessor).__name__, exc)
        return []
```

Then in `_run_analysis`, the assessor loop becomes:
```python
for assessor in assessors:
    findings.extend(self._run_assessor_safe(assessor, context))
```

This removes the inlined try/except and logging from `_run_analysis`. Net: -~20 lines in `_run_analysis`, +12 in new method.

---

### Task 3B — Simplify `_check_config_warnings()` (L491–523, 33 lines of loop)

Read the current method body. It iterates over `components` and checks whether each required component has no required rules, appending a warning `Finding` if so. Convert to a list comprehension:

```python
def _check_config_warnings(
    self, profile_spec: ProfileSpec, context: SubmissionContext
) -> list[Finding]:
    warnings = []
    for component in ("html", "css", "js", "php", "sql"):
        is_required = component in (profile_spec.required_components or [])
        has_required_rules = any(
            r.component == component
            for r in (profile_spec.required_rules or [])
        )
        if is_required and not has_required_rules:
            warnings.append(Finding(
                id=f"{component.upper()}.NO_REQUIRED_RULES",
                category=component,
                message=f"Component '{component}' is required but has no required rules configured.",
                severity=Severity.WARN,
                source="config_checker",
                evidence={"component": component},
                finding_category=FindingCategory.config,
            ))
    return warnings
```

**Read the actual method first** — adapt the above template to match whatever logic is actually there. Do not simplify if the actual method has substantive conditional logic beyond the above.

---

### Task 3C — Simplify `_find_screenshot()` (L425–483, 59 lines)

The current method has three passes:
1. Check `context.browser_evidence` for Playwright screenshots
2. Preferred filename search in `search_dirs`
3. Glob fallback

Keep all three passes but collapse the glob fallback (currently a double for-loop) into a generator:

```python
# Replace the glob fallback block:
for ext in image_extensions:
    for directory in search_dirs:
        match = next(iter(sorted(directory.glob(f"*{ext}"))), None)
        if match:
            return match
return None
```

Also collapse the preferred-stem search similarly. Do not change Pass 1 (Playwright logic). Net: -~20 lines.

---

### Task 3D — Slim `_run_threat_scan()` (L330–406, 77 lines)

The method has a large `_CATEGORY_TO_ID` dict and a per-threat loop. The per-threat loop builds a `Finding` for each threat. Extract the Finding builder:

```python
@staticmethod
def _threat_to_finding(threat, category_to_id: dict) -> Finding:
    finding_id = category_to_id.get(threat.category, SANDBOX.THREAT.UNKNOWN)
    return Finding(
        id=finding_id,
        category="security",
        message=threat.description,
        severity=Severity.THREAT,
        source="threat_scanner",
        evidence={"pattern": threat.pattern_name, "file": str(threat.file_path),
                  "line": threat.line_number, "severity": threat.severity.name},
        finding_category=FindingCategory.security,
    )
```

Then the per-threat loop becomes:
```python
findings = [self._threat_to_finding(t, _CATEGORY_TO_ID) for t in scan_result.threats]
```

**Read the actual loop** and adapt. Net: -~25 lines.

---

**Commit after Tasks 3A–3D pass tests:**
```
refactor(core): extract assessor runner, slim pipeline _run_analysis/_find_screenshot/_run_threat_scan
```

---

## FILE 4 — `ams/core/attempts.py` (530 lines → target ~280)

### Task 4A — Replace `update_attempt()` dynamic SET builder (L442–530, 89 lines)

Current code builds a SET clause by iterating `fields` dict, filtering against an `allowed` set, and handling bool conversion for two fields. Replace with a focused helper:

```python
def _build_update_sql(
    table: str, id_col: str, allowed: frozenset[str], bool_fields: frozenset[str], **fields
) -> tuple[str, list]:
    """Return (sql, params) for a filtered UPDATE, converting bool fields to int."""
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return "", []
    set_parts = [f"{k} = ?" for k in filtered]
    params = [1 if (k in bool_fields and v) else (0 if k in bool_fields else v)
              for k, v in filtered.items()]
    return f"UPDATE {table} SET {', '.join(set_parts)} WHERE {id_col} = ?", params
```

Then `update_attempt()` becomes:
```python
def update_attempt(attempt_id: str, **fields) -> dict[str, Any] | None:
    _ALLOWED = frozenset({
        "ingestion_status", "pipeline_status", "validity_status",
        "overall_score", "confidence", "manual_review_required",
        "error_message", "report_path", "run_dir", "run_id",
        "is_active", "batch_run_id", "updated_at",
    })
    _BOOL_FIELDS = frozenset({"manual_review_required", "is_active"})
    sql, params = _build_update_sql(
        "submission_attempts", "id", _ALLOWED, _BOOL_FIELDS, **fields
    )
    if not sql:
        return get_attempt(attempt_id)
    with get_db() as conn:
        conn.execute(sql + " ?", [*params, attempt_id])
    return get_attempt(attempt_id)
```

**Read the actual `update_attempt` body** to get the exact `allowed` set and bool fields — do not guess. Net: 89 → ~20 lines.

---

### Task 4B — Merge `create_attempt()` and `_insert_attempt_record()`

`create_attempt()` (L388–441) calls `_insert_attempt_record(conn, metadata)` which does the actual INSERT. The split adds no value. Merge the INSERT SQL inline into `create_attempt()`:

```python
def create_attempt(
    *,
    assignment_id: str,
    student_id: str,
    source_type: str,
    **kwargs,
) -> dict[str, Any]:
    metadata = _build_attempt_metadata(
        assignment_id=assignment_id, student_id=student_id,
        source_type=source_type, **kwargs,
    )
    with get_db() as conn:
        existing_id = _find_existing_attempt_id(conn, metadata)
        if existing_id:
            metadata["attempt_id"] = existing_id
        else:
            metadata["attempt_number"] = _next_attempt_number(conn, metadata)
            conn.execute(
                "INSERT INTO submission_attempts (...) VALUES (...)",
                _attempt_persistence_values(metadata),
            )
    return get_attempt(metadata["attempt_id"])
```

**Read the actual INSERT SQL** in `_insert_attempt_record` and copy it verbatim. Delete `_insert_attempt_record` after inlining. Net: 54 + 44 = 98 → ~30 lines.

---

### Task 4C — Simplify `_build_attempt_metadata()` (L189–240, 52 lines)

The current function takes ~18 explicit keyword arguments and builds a dict. This is correct and explicit but verbose. Simplify by using `locals()` capture or a minimal explicit dict:

```python
def _build_attempt_metadata(
    *,
    assignment_id: str,
    student_id: str,
    source_type: str,
    source_actor_user_id: str = "",
    original_filename: str = "",
    source_ref: str = "",
    submitted_at: str | None = None,
    created_at: str | None = None,
    ingestion_status: str = "pending",
    pipeline_status: str = "pending",
    validity_status: str = "pending",
    run_id: str | None = None,
    run_dir: str | None = None,
    report_path: str = "",
    batch_run_id: str = "",
    batch_submission_id: str = "",
) -> dict[str, Any]:
    assignment_value, student_value = _validate_attempt_identity(assignment_id, student_id)
    attempt_id = str(run_id or generate_attempt_id("attempt"))
    now = utc_now_iso()
    created = str(created_at or now)
    return {
        "attempt_id": attempt_id,
        "assignment_id": assignment_value,
        "student_id": student_value,
        "source_type": source_type,
        "source_actor_user_id": source_actor_user_id,
        "original_filename": original_filename,
        "source_ref": source_ref,
        "submitted_at": str(submitted_at or created),
        "created_at": created,
        "ingestion_status": ingestion_status,
        "pipeline_status": pipeline_status,
        "validity_status": validity_status,
        "run_id": attempt_id,
        "run_dir": str(run_dir or ""),
        "report_path": report_path,
        "batch_run_id": batch_run_id,
        "batch_submission_id": str(batch_submission_id or ""),
        "overall_score": None,
        "confidence": None,
        "manual_review_required": False,
        "error_message": "",
        "is_active": True,
    }
```

**Read the actual current function** before editing — verify the dict keys match exactly. Net: 52 → ~30 lines.

---

**Commit after Tasks 4A–4C pass tests:**
```
refactor(core): slim attempts.py — generic update helper, merged create_attempt, simplified metadata builder
```

---

## FINAL VERIFICATION

After all four commits:

```bash
# Full test suite
pytest tests/ -v --tb=short

# LOC count — target < 1,400 across the 4 files
wc -l ams/core/requirements.py ams/core/scoring.py ams/core/pipeline.py ams/core/attempts.py

# Import sanity
python -c "
from ams.core.requirements import RequirementEvaluationEngine
from ams.core.scoring import ScoringEngine
from ams.core.pipeline import AssessmentPipeline
from ams.core.attempts import create_attempt, update_attempt
from ams.llm.scoring_integration import enrich_with_llm_hybrid
print('All imports OK')
"

# Verify no deleted functions are still referenced
grep -r "_insert_attempt_record\|_score_from_requirements\|_apply_llm_hybrid" ams/ --include="*.py"
# Should return nothing (or only the new scoring_integration.py)
```

---

## IMPORTANT: Before each edit

1. **Read the full file** with the Read tool first.
2. **Check actual line numbers** — the line numbers in this prompt were accurate at time of writing but may have shifted slightly from prior edits.
3. **Verify constant names** (HID, CID, JID, etc.) by reading the existing imports at the top of `requirements.py`.
4. **Do not change** anything outside the methods listed above.
