# Automated Marking System (AMS) for Web Development Coursework

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

AMS is a pipeline-based assessment system for undergraduate web development coursework.
It supports heterogeneous, multi-file submissions (HTML, CSS, JavaScript, PHP, SQL, API code) and combines deterministic checks with controlled AI-assisted feedback.

The goal is fair, scalable, and reproducible marking with actionable feedback for students.

## Key Features

- Secure sandboxed execution using Docker.
- Static and behavioral assessment across multiple languages.
- Browser-based interaction checks with Playwright.
- Optional LLM-assisted arbitration and feedback generation.
- Assignment-level analytics for moderation.
- CLI and Web UI workflows.

## Pipeline

1. Acquisition and normalization of student submissions.
2. Sandbox setup.
3. Static assessors.
4. Behavioral assessors.
5. Playwright assessors.
6. Optional LLM arbitration.
7. Structured reporting (including 1.0 / 0.5 / 0.0 scoring outputs).

## Installation and Setup

### Prerequisites

- Python 3.10+
- Docker (required for secure sandboxing)
- LM Studio or API credentials for LLM-backed workflows

### Developer Setup

1. Clone the repository:

```bash
git clone <repository_url>
cd Automated-Marking-System-for-Web-Development-Coursework
```

2. Install package and dependencies:

```bash
pip install -e .[demo,dev]
```
This installs core dependencies and demo/dev extras (including pytest, playwright, matplotlib, and openai).

LLM provider only:

```bash
pip install -e .[llm]
```

3. Install Playwright browsers:

```bash
python -m playwright install
```

### Build Sandbox Image

From repository root:

```bash
./docker/build.sh
```

If the Docker image is missing, AMS will warn and provide guidance. You can also bypass Docker with `AMS_SANDBOX_MODE=subprocess` when appropriate.

## Usage

### Demo Run

```bash
AMS_RUNS_ROOT=demo_out ams demo --profile fullstack
```

### Web UI

```bash
FLASK_ENV=development python -m ams.webui
```

Open `http://localhost:5000` in your browser.

### CLI Examples

Mark a single submission:

```bash
ams mark path/to/submission_dir -w path/to/workspace -o report.json --profile frontend
```

Run batch assessment:

```bash
ams batch path/to/input_folder --profile fullstack -o ams_batch_runs/run_name
```

## Evaluation Modes

Accuracy evaluation - compare pipeline scores against ground-truth labels:

```bash
ams eval --accuracy evaluation_dataset/ \
  --profile frontend_interactive \
  --profile-config evaluation_dataset/eval_profile.json \
  --out results/accuracy/
```

Consistency evaluation - run one submission N times and measure determinism:

```bash
ams eval --consistency evaluation_dataset/correct/correct_001 \
  --runs 5 \
  --profile frontend_interactive \
  --profile-config evaluation_dataset/eval_profile.json \
  --out results/consistency/
```

Robustness evaluation - test pipeline behaviour on malformed/adversarial inputs:

```bash
ams eval --robustness evaluation_dataset/ \
  --profile frontend_interactive \
  --profile-config evaluation_dataset/eval_profile.json \
  --out results/robustness/
```

LLM marking evaluation - compare STATIC_ONLY vs STATIC_PLUS_LLM on attempt submissions:

```bash
ams eval --llm-marking evaluation_dataset/ \
  --profile frontend_interactive \
  --profile-config evaluation_dataset/eval_profile.json \
  --llm-profile-config evaluation_dataset/eval_profile_llm.json \
  --out results/llm_marking/
```

### Shared Flags

| Flag | Default | Description |
| --- | --- | --- |
| --profile | frontend_interactive | AMS profile to use for marking |
| --profile-config PATH | none | Custom profile JSON (disables browser/behavioral checks) |
| --llm-profile-config PATH | none | Custom profile JSON for the LLM run in --llm-marking mode |
| --runs N | 5 | Number of repeated runs for --consistency |
| --out / -o PATH | ams_eval_runs/<timestamp> | Output directory for results |

### Profile Config Files

| File | Purpose |
| --- | --- |
| evaluation_dataset/eval_profile.json | Static-only, no browser/behavioral checks; use for accuracy/consistency/robustness |
| evaluation_dataset/eval_profile_llm.json | LLM + partial credit enabled, no browser/behavioral checks; use with --llm-marking |

## Testing

Run all tests:

```bash
pytest
```

Skip slow sandbox or LLM tests:

```bash
pytest -m "not slow"
```

## Troubleshooting Commands

Hard reset sandbox containers:

```powershell
docker stop $(docker ps -a -q --filter ancestor=ams-sandbox)
docker rm -f $(docker ps -a -q --filter ancestor=ams-sandbox)
# Also clear retained threat containers
docker rm -f $(docker ps -a -q --filter name="ams-threat-")
```

Clear web run history:

```powershell
Remove-Item -Recurse -Force ams_web_runs\*
```

Clear LLM cache database:

```powershell
Remove-Item -Force ams\cache.db
```

Force rebuild sandbox image:

```bash
./docker/build.sh --no-cache
```

GitHub integration environment variables:

```bash
export AMS_GITHUB_CLIENT_ID="your-github-client-id"
export AMS_GITHUB_CLIENT_SECRET="your-github-client-secret"
export AMS_GITHUB_OAUTH_CALLBACK="http://localhost:5000/api/github/callback"
```
