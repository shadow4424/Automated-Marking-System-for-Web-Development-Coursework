def test_js_required_missing_when_no_js(build_submission, run_pipeline):
    """JS is required for frontend, so missing files should be MISSING_FILES, not SKIPPED."""
    submission = build_submission({"index.html": "<html></html>"})
    data = run_pipeline(submission, profile="frontend")
    ids = [f["id"] for f in data["findings"]]
    # JS is required for frontend, so should be MISSING_FILES
    assert "JS.REQ.MISSING_FILES" in ids or "JS.MISSING_FILES" in ids


def test_js_required_pass(build_submission, run_pipeline):
    js = "document.body.addEventListener('click', ()=>{});"
    submission = build_submission({"app.js": js})
    data = run_pipeline(submission, profile="frontend")
    passes = [f for f in data["findings"] if f["id"] == "JS.REQ.PASS"]
    assert passes
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "js.has_event_listener" in rule_ids


def test_js_required_fail(build_submission, run_pipeline):
    js = "console.log('x');"
    submission = build_submission({"app.js": js})
    data = run_pipeline(submission, profile="frontend")
    fails = [f for f in data["findings"] if f["id"] == "JS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "js.has_event_listener" for f in fails)


def test_js_avoids_document_write_pass(build_submission, run_pipeline):
    """No document.write → js.avoids_document_write passes in calculator profile."""
    js = (
        "const display = document.getElementById('theDisplay');\n"
        "function updateDisplay(val) { display.value += val; }\n"
        "const btn = document.createElement('button');\n"
        "btn.addEventListener('click', () => updateDisplay('1'));\n"
    )
    submission = build_submission({"calc.js": js})
    data = run_pipeline(submission, profile="frontend_calculator")
    passes = [f for f in data["findings"] if f["id"] == "JS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "js.avoids_document_write" in rule_ids


def test_js_avoids_document_write_fail(build_submission, run_pipeline):
    """Document.write present → js.avoids_document_write fails in calculator profile."""
    js = (
        "document.write('<button>1</button>');\n"
        "const display = document.getElementById('theDisplay');\n"
    )
    submission = build_submission({"calc.js": js})
    data = run_pipeline(submission, profile="frontend_calculator")
    fails = [f for f in data["findings"] if f["id"] == "JS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "js.avoids_document_write" for f in fails)


def test_js_uses_createElement_pass(build_submission, run_pipeline):
    """Document.createElement calls → js.uses_createElement passes in calculator profile."""
    js = (
        "const display = document.getElementById('theDisplay');\n"
        "['1','2','+','-'].forEach(val => {\n"
        "  const b = document.createElement('button');\n"
        "  b.textContent = val;\n"
        "  b.addEventListener('click', () => { display.value += val; });\n"
        "});\n"
    )
    submission = build_submission({"calc.js": js})
    data = run_pipeline(submission, profile="frontend_calculator")
    passes = [f for f in data["findings"] if f["id"] == "JS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "js.uses_createElement" in rule_ids


def test_js_creates_display_dom_pass(build_submission, run_pipeline):
    """TheDisplay reference → js.creates_display_dom passes in calculator profile."""
    js = (
        "const theDisplay = document.getElementById('theDisplay');\n"
        "function updateDisplay(val) { theDisplay.value += val; }\n"
        "let preValue = '', preOp = '';\n"
        "function doCalc() {\n"
        "  if (preOp === '+') return parseFloat(preValue) + parseFloat(theDisplay.value);\n"
        "  if (preOp === '-') return parseFloat(preValue) - parseFloat(theDisplay.value);\n"
        "}\n"
        "const btn = document.createElement('button');\n"
        "btn.addEventListener('click', () => updateDisplay('1'));\n"
    )
    submission = build_submission({"calc.js": js})
    data = run_pipeline(submission, profile="frontend_calculator")
    passes = [f for f in data["findings"] if f["id"] == "JS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "js.creates_display_dom" in rule_ids


def test_js_has_doCalc_pass(build_submission, run_pipeline):
    """DoCalc function with arithmetic → js.has_doCalc passes in calculator profile."""
    js = (
        "let preValue = '', preOp = '';\n"
        "const theDisplay = document.getElementById('theDisplay');\n"
        "function doCalc() {\n"
        "  if (preOp === '+') theDisplay.value = parseFloat(preValue) + parseFloat(theDisplay.value);\n"
        "  if (preOp === '-') theDisplay.value = parseFloat(preValue) - parseFloat(theDisplay.value);\n"
        "  if (preOp === '*') theDisplay.value = parseFloat(preValue) * parseFloat(theDisplay.value);\n"
        "  if (preOp === '/') theDisplay.value = parseFloat(preValue) / parseFloat(theDisplay.value);\n"
        "  theDisplay.value = '';\n"
        "}\n"
        "const btn = document.createElement('button');\n"
        "btn.addEventListener('click', () => {});\n"
    )
    submission = build_submission({"calc.js": js})
    data = run_pipeline(submission, profile="frontend_calculator")
    passes = [f for f in data["findings"] if f["id"] == "JS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "js.has_doCalc" in rule_ids
