# Automated Marking System for Web Development Coursework

Deterministic, rule-based assessment pipeline for heterogeneous web coursework
submissions. The system emphasises transparency: every awarded mark is backed by
recorded reasons and reproducible checks.

## Pipeline overview
1. **Normalization** – safely unpack the submission archive and record the file inventory.
2. **Static analysis** – lightweight structural checks across HTML, CSS, JS, PHP, and SQL assets.
3. **Deterministic behavioural rules** – verify expected coursework patterns such as entry points, form handling, and data access.
4. **Browser-inspired checks** – heuristic analysis of interactive elements (optionally switchable to Playwright when available).
5. **Score aggregation** – consolidate stage scores into a discrete mark (1/0.5/0) with explicit reasons.
6. **Reporting** – emit human-readable text and JSON summaries for auditability.

## Running the pipeline

```bash
python main.py path/to/submission.zip --workspace submissions --reports reports
```

Reports are written to the chosen output directory and also printed to stdout.

### Quick smoke test

Use the bundled sample coursework to see the end-to-end pipeline in action:

```bash
# 1) Create a workspace and reports directory (optional; default names used below)
mkdir -p submissions reports

# 2) Package the sample coursework into a zip
python -m zipfile -c submissions/sample_submission.zip examples/minimal_submission/*

# 3) Run the AMS pipeline against the sample
python main.py submissions/sample_submission.zip --workspace submissions --reports reports
```

The run will produce a textual summary and a JSON report under `reports/`, showing
the normalisation, static analysis, deterministic checks, and browser-inspired
results for the sample submission. This verifies that dependencies are satisfied
and that the pipeline executes end-to-end.

## Running tests

```bash
python -m unittest discover -s tests
```
