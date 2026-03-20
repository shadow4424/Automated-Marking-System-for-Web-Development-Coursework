# Automated Marking System (AMS) for Web Development Coursework

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

The AMS (Automated Marking System) is a powerful, pipeline-based assessment tool designed specifically for undergraduate web development coursework. It addresses the unique challenges of marking heterogeneous, multi-file submissions containing HTML, CSS, JavaScript, PHP, SQL, and API components.

Unlike traditional automated markers that look for exact outputs, AMS combines **deterministic testing** (static analysis and behavioural tests) with **controlled, AI-assisted feedback**. This hybrid approach ensures fair, scalable, and reproducible marking while providing rich, explainable feedback that helps students understand their mistakes. 

## Core Features

- **Secure Sandboxing**: Student code is executed safely in isolated Docker containers, protecting the host environment from malicious or runaway scripts.
- **Browser Automation & Vision**: Uses Playwright to simulate user interactions and LLM Vision capabilities to test visual layout, responsiveness, and dynamic DOM updates.
- **Multi-Language Static Analysis**: Robust AST-based static analysis to detect necessary structures (e.g., HTML semantics, CSS rules, JavaScript control flow, PHP database queries) without enforcing rigid one-size-fits-all solutions.
- **AI-Assisted Feedback**: Leverages advanced LLMs (local or API-driven) to summarise deterministic findings into clear, pedagogical feedback—crucially, without letting the AI dictate the final mark.
- **Assignment Analytics**: Open a single assignment analytics view that refreshes from all current submissions for that assignment and highlights cohort patterns for moderation.
- **Web UI & CLI**: Instructors can use the intuitive `Flask`-based Web UI for quick, visual reviews, or rely on the powerful CLI for scriptable batch operations.

## Architecture Pipeline

Submissions flow through a rigorous, rule-based pipeline:
1. **Acquisition & Normalisation**: Extracts and normalises student ZIP structures.
2. **Setup**: Configures a secure Docker sandbox environment.
3. **Static Assessors**: Analyses raw code (HTML/CSS parsing, simple AST checks).
4. **Behavioural Assessors**: Executes deterministic tests against the sandbox (API endpoints, DB checks).
5. **Playwright Assessors**: Spins up a headless browser for UI and interactivity checks.
6. **LLM Arbitration**: Optional step where the LLM resolves conflicting signals (e.g., code looks right but UI is broken) and generates final feedback.
7. **Reporting**: Outputs structured findings and final scores (1.0, 0.5, 0.0) into actionable reports.

## Installation & Setup

### Prerequisites
- Python 3.10 or higher
- Docker (required for secure sandboxing)
- (Optional but recommended) Locally running LLM via LM Studio, or valid API keys (e.g., OpenAI) configured.

### Developer Setup

1. **Clone the repository:**
   ```bash
   git clone <repository_url>
   cd Automated-Marking-System-for-Web-Development-Coursework
   ```
2. **Install the package and dependencies:**
   To install the CLI and all necessary extras (including the Web UI and demo tooling):
   ```bash
   pip install -e .[demo,dev]
   ```
3. **Install Playwright Browsers:**
   Required for UI testing.
   ```bash
   playwright install
   ```

### Running the Docker Sandbox
For the sandboxed behavioural tests to operate securely, you must have Docker running and the local sandbox image built.
To build the required sandbox environment, run the provided script from the repository root:
```bash
./docker/build.sh
```
If you start the `ams` CLI without the Docker image available, it will display a warning and instructions to either build it or bypass using `AMS_SANDBOX_MODE=subprocess`.

## Usage Guide

### 1. The Examiner Quick Path (Demo)
Run a full end-to-end demonstration. This command processes a sample submission, calculates scores, and produces a full report.
```bash
AMS_RUNS_ROOT=demo_out ams demo --profile fullstack
```

### 2. Using the Web UI
Start the simple teacher-facing web application to upload and review individual submissions interactively:
```bash
FLASK_ENV=development python -m ams.webui
```
Then, open your browser to `http://localhost:5000`.
- *Note: In the UI, open an assignment and use the `Analytics` button to view fresh assignment-wide analytics.*

### 3. Command Line Interface (CLI)
The CLI allows integration with scripts and CI/CD systems.

**Mark a single submission:**
```bash
ams mark path/to/submission_dir -w path/to/workspace -o report.json --profile frontend
```

**Run Batch Assessment:**
Process a directory filled with student zip files or folders. Overrides global target paths.
```bash
ams batch path/to/input_folder --profile fullstack -o ams_batch_runs/run_name
```

## Contributing and Development

The test suite ensures the stability of parsing, sandboxing, and assessment logic.
Run all tests using pytest:
```bash
pytest
```
To run tests while skipping slow sandbox or LLM logic:
```bash
pytest -m "not slow"
```

## Debug Commands

If you need to reset the system to default settings or troubleshoot issues, here are the most important debug commands:

**1. Hard Reset All Docker Containers:**
Useful if the system is hanging on Docker operations or failing to list containers.
```powershell
docker stop $(docker ps -a -q --filter ancestor=ams-sandbox)
docker rm -f $(docker ps -a -q --filter ancestor=ams-sandbox)
# Also clear retained threat containers
docker rm -f $(docker ps -a -q --filter name="ams-threat-")
```

**2. Clear Run History (Hard Delete):**
This deletes all run data and resets the dashboard to empty.
```powershell
Remove-Item -Recurse -Force ams_web_runs\*
```

**3. Clear LLM Caches:**
Forces the LLM to regenerate all responses instead of using cached ones.
```powershell
Remove-Item -Recurse -Force cache\*
```

**4. Force Rebuild Docker Sandbox:**
Useful if package dependencies have changed.
```bash
./docker/build.sh --no-cache
```
