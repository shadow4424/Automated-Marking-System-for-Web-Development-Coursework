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

## Running tests

```bash
python -m unittest discover -s tests
```
