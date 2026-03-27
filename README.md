# Automated Marking System (AMS) for Web Development Coursework

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

AMS is an assessment system for web development coursework. It evaluates multi-file submissions across HTML, CSS, JavaScript, PHP, SQL, and API-backed work using deterministic checks, sandboxed execution, browser evidence, and optional LLM-assisted scoring support.

The current system exposes two primary workflows:

- `ams mark` for a single submission
- `ams batch` for bulk marking and reporting

It also includes a Flask web interface for submission handling, assignment management, analytics, exports, and run history.

## What The System Does

- Scores coursework against named profiles such as `frontend_interactive`, `frontend`, and `fullstack`
- Runs static, behavioural, browser, and consistency-style checks through the assessment pipeline
- Stores run artefacts and reports for later inspection in the web UI
- Supports teacher/admin workflows for marking, batch uploads, analytics, exports, and moderation
- Supports student-facing dashboards and assignment analytics views
- Can optionally use LLM-backed scoring helpers when the relevant dependencies and configuration are present

## Installation

### Prerequisites

- Python 3.10+
- Docker, if you want Docker sandbox mode
- Playwright browser binaries, if you want browser-based checks
- LLM credentials or a local provider, if you want LLM-backed features

### Install The Package

Base install:

```bash
pip install -e .
```

Development install:

```bash
pip install -e .[dev]
```

Development install with LLM extras:

```bash
pip install -e .[dev,llm]
```

LLM-only extras:

```bash
pip install -e .[llm]
```

### Install Playwright Browsers

```bash
python -m playwright install
```

### Build The Sandbox Image

```bash
./docker/build.sh
```

If Docker is unavailable, you can switch to subprocess sandbox mode:

```bash
export AMS_SANDBOX_MODE="subprocess"
```

PowerShell:

```powershell
$env:AMS_SANDBOX_MODE = "subprocess"
```

## CLI Usage

Show top-level help:

```bash
ams -h
```

### Mark A Single Submission

```bash
ams mark path/to/submission_dir --profile frontend_interactive
```

Write the final report to a specific location:

```bash
ams mark path/to/submission_dir \
  --profile frontend_interactive \
  --workspace ams_runs/manual-run \
  --out reports/report.json
```

Using a custom profile file:

```bash
ams mark path/to/submission_dir \
  --profile custom_profile \
  --profile-config path/to/profile.json
```

### Run Batch Marking

```bash
ams batch path/to/submissions --profile fullstack
```

With an explicit output directory:

```bash
ams batch path/to/submissions \
  --profile fullstack \
  --out ams_batch_runs/coursework_01
```

With a custom profile file:

```bash
ams batch path/to/submissions \
  --profile custom_profile \
  --profile-config path/to/profile.json
```

### Current CLI Commands

The live CLI currently supports:

- `ams mark`
- `ams batch`

Older demo/evaluation commands are no longer part of the system.

## Web UI

Run the Flask application with the app factory:

```bash
flask --app ams.webui:create_app run --debug
```

Then open:

```text
http://127.0.0.1:5000
```

Useful web-related environment variables:

```bash
export AMS_RUNS_ROOT="ams_web_runs"
export AMS_GITHUB_CLIENT_ID="your-github-client-id"
export AMS_GITHUB_CLIENT_SECRET="your-github-client-secret"
export AMS_GITHUB_OAUTH_CALLBACK="http://localhost:5000/api/github/callback"
```

PowerShell:

```powershell
$env:AMS_RUNS_ROOT = "ams_web_runs"
$env:AMS_GITHUB_CLIENT_ID = "your-github-client-id"
$env:AMS_GITHUB_CLIENT_SECRET = "your-github-client-secret"
$env:AMS_GITHUB_OAUTH_CALLBACK = "http://localhost:5000/api/github/callback"
```

## Output Locations

By default, AMS writes artefacts to:

- `ams_runs/` for CLI single-submission runs
- `ams_batch_runs/` for CLI batch runs
- `ams_web_runs/` or `AMS_RUNS_ROOT` for the web UI

## Testing

Run the full suite:

```bash
python -m pytest tests/ -q
```

Run focused suites:

```bash
python -m pytest tests/webui/ -q
python -m pytest tests/sandbox/ -q
python -m pytest tests/output/ -q
```

## Troubleshooting

Rebuild the sandbox image:

```bash
./docker/build.sh --no-cache
```

Clear web run history:

```powershell
Remove-Item -Recurse -Force ams_web_runs\*
```

Clear batch runs:

```powershell
Remove-Item -Recurse -Force ams_batch_runs\*
```

If Docker mode is enabled but Docker is unavailable, AMS will stop at startup. Use subprocess mode if you need a local fallback:

```powershell
$env:AMS_SANDBOX_MODE = "subprocess"
```

## Notes

- The web app entrypoint is the `create_app()` factory in `ams.webui`
- The CLI entrypoint is `ams`
- The package metadata and console script are defined in `pyproject.toml`
