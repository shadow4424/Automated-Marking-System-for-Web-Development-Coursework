# Automated-Marking-System-for-Web-Development-Coursework
UoM 3rd Year Project

## Examiner Quick Path
1. Install with extras: `pip install -e .[demo]`
2. Run the full demo (builds batch, runs marking, analytics, figures, evaluation): `AMS_RUNS_ROOT=demo_out ams demo --profile fullstack`
3. Start the web UI: `python -m ams.webui` then open http://localhost:5000
4. In the UI: Run History → open the latest batch run → Analytics → download figures/CSV as needed.

## Web UI

Run a simple teacher-facing web app:

```
python -m ams.webui
```

Environment:
- `AMS_RUNS_ROOT` (optional) to override where web runs are stored (default: `ams_web_runs/`).
- `FLASK_ENV=development` for debug if needed.

Features: single submission marking, batch marking with analytics, and run history with downloadable artifacts.

Browser automation (Playwright):
- Install extras: `pip install "playwright>=1.41.0"` then `python -m playwright install` to fetch browsers.
- In environments without Playwright browsers, browser tests will be skipped and marked accordingly in findings.

Examiner quickstart:
- Install: `pip install -e .[demo]`
- One-command demo (builds demo batch, runs marking, analytics, figures, evaluation): `AMS_RUNS_ROOT=demo_out ams demo --profile fullstack`
- Open the web UI: `python -m ams.webui` (optionally `AMS_RUNS_ROOT=demo_out` to view the demo) and browse to http://localhost:5000 to view runs/analytics.
- Run evaluation harness manually: `ams eval --fixtures demo --out ams_eval_out` (produces evaluation_results.csv and evaluation_summary.json)
- Export figures from a batch run: `ams export-figures --run-id <run_id> --runs-root ams_batch_runs --out figures/`
