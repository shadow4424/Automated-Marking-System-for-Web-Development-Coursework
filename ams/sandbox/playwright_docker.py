"""Docker-isolated Playwright execution.

Runs Playwright browser automation inside a Docker container so that
student JavaScript code cannot access the host filesystem or network.
"""
from __future__ import annotations

import json
import logging
import subprocess
import textwrap
import time
from pathlib import Path
from typing import List, Mapping

from ams.assessors.playwright_assessor import BrowserRunner, BrowserRunResult
from ams.sandbox.config import SandboxConfig, get_sandbox_config

logger = logging.getLogger(__name__)


class DockerPlaywrightRunner(BrowserRunner):
    """Execute Playwright in an isolated Docker container.

    The student's workspace is mounted read-only at ``/workspace`` and
    accessed via ``file://`` URLs.  Network access is disabled.
    """

    def __init__(
        self,
        config: SandboxConfig | None = None,
        timeout_ms: int | None = None,
        output_cap: int = 10_000,
    ) -> None:
        self.config = config or get_sandbox_config()
        self.timeout_ms = timeout_ms or self.config.browser_timeout_ms
        self.output_cap = output_cap

    def run(
        self,
        entry_path: Path,
        workdir: Path,
        interaction: bool = True,
    ) -> BrowserRunResult:
        """Run browser automation inside a Docker container."""
        start = time.time()

        # Build the Python script that will run inside the container
        script_content = self._generate_script(entry_path, workdir, interaction)

        # Write onto a tmpdir that we mount into the container
        script_path = workdir / "_ams_pw_script.py"
        script_path.write_text(script_content, encoding="utf-8")

        cmd = self._build_docker_cmd(workdir, script_path)
        logger.debug("Docker Playwright command: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_ms / 1000 + 5,  # script timeout + grace
            )

            # The script prints a single JSON line on stdout
            if result.returncode == 0 and result.stdout.strip():
                try:
                    data = json.loads(result.stdout.strip().split("\n")[-1])
                    duration_ms = int((time.time() - start) * 1000)
                    return BrowserRunResult(
                        status=data.get("status", "error"),
                        url=data.get("url", ""),
                        duration_ms=duration_ms,
                        dom_before=data.get("dom_before", "")[:self.output_cap],
                        dom_after=data.get("dom_after", "")[:self.output_cap],
                        console_errors=data.get("console_errors", [])[:20],
                        network_errors=data.get("network_errors", [])[:20],
                        actions=data.get("actions", []),
                        screenshot_paths=[],  # screenshots stay inside container
                        notes=data.get("notes", ""),
                    )
                except (json.JSONDecodeError, KeyError) as exc:
                    logger.warning("Failed to parse Playwright container output: %s", exc)

            duration_ms = int((time.time() - start) * 1000)
            return BrowserRunResult(
                status="error",
                duration_ms=duration_ms,
                notes=f"Container exited {result.returncode}: {result.stderr[:500]}",
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return BrowserRunResult(
                status="timeout",
                duration_ms=duration_ms,
                notes="Docker Playwright container timed out",
            )
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            return BrowserRunResult(
                status="error",
                duration_ms=duration_ms,
                notes=f"Docker Playwright execution failed: {exc}",
            )
        finally:
            # Clean up the temporary script
            script_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------

    def _build_docker_cmd(self, workdir: Path, script_path: Path) -> list[str]:
        cfg = self.config
        return [
            "docker", "run", "--rm",
            "--cpus", str(cfg.cpu_limit),
            "--memory", cfg.memory_limit,
            "--pids-limit", str(cfg.pids_limit),
            "--network", cfg.network_mode,
            "-v", f"{workdir.resolve()}:/workspace:ro",
            "-v", f"{script_path.resolve()}:/run_pw.py:ro",
            "--user", cfg.user,
            cfg.playwright_image,
            "python", "/run_pw.py",
        ]

    def _generate_script(
        self,
        entry_path: Path,
        workdir: Path,
        interaction: bool,
    ) -> str:
        """Generate the Python script executed inside the container."""
        # Resolve the relative path from workdir to entry file
        try:
            rel = entry_path.relative_to(workdir)
        except ValueError:
            rel = Path(entry_path.name)

        timeout = self.timeout_ms
        cap = self.output_cap
        interact_flag = "True" if interaction else "False"

        return textwrap.dedent(f"""\
            import json, sys, time
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            result = {{
                "status": "unknown", "url": "", "duration_ms": 0,
                "dom_before": "", "dom_after": "",
                "console_errors": [], "network_errors": [],
                "actions": [], "notes": ""
            }}
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                    page = browser.new_page()

                    console_errors, network_errors, actions = [], [], []

                    def _on_console(msg):
                        if msg.type == "error":
                            console_errors.append(msg.text[:{cap}])
                    def _on_req_fail(req):
                        network_errors.append(f"{{req.url}}: {{req.failure}}"[:{cap}])

                    page.on("console", _on_console)
                    page.on("requestfailed", _on_req_fail)

                    url = "file:///workspace/{rel.as_posix()}"
                    actions.append({{"type": "goto", "target": "{rel.name}"}})
                    start = time.time()
                    page.goto(url, wait_until="load", timeout={timeout})
                    dom_before = page.content()[:{cap}]

                    interact = {interact_flag}
                    dom_after = dom_before
                    if interact:
                        form = page.query_selector("form")
                        if form:
                            text_input = form.query_selector(
                                "input[type=text], input:not([type])")
                            if text_input:
                                text_input.fill("test")
                            submit = form.query_selector(
                                "button[type=submit], input[type=submit]")
                            if submit:
                                submit.click()
                            actions.append({{"type": "form_submit", "selector": "form"}})
                            page.wait_for_timeout(400)
                        else:
                            btn = page.query_selector("button")
                            if btn:
                                btn.click()
                                actions.append({{"type": "click", "selector": "button"}})
                                page.wait_for_timeout(400)
                            else:
                                actions.append({{"type": "interaction_skipped",
                                                "reason": "no form/button found"}})
                        dom_after = page.content()[:{cap}]

                    duration_ms = int((time.time() - start) * 1000)
                    browser.close()

                    result.update({{
                        "status": "pass", "url": url,
                        "duration_ms": duration_ms,
                        "dom_before": dom_before, "dom_after": dom_after,
                        "console_errors": console_errors[:20],
                        "network_errors": network_errors[:20],
                        "actions": actions,
                    }})
            except PWTimeout:
                result["status"] = "timeout"
                result["notes"] = "Page load timeout inside container"
            except Exception as e:
                result["status"] = "error"
                result["notes"] = str(e)

            print(json.dumps(result))
        """)


__all__ = ["DockerPlaywrightRunner"]
