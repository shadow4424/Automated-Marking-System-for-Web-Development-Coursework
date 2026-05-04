"""Docker-isolated Playwright execution."""
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
    """Execute Playwright in an isolated Docker container."""

    def __init__(
        self,
        config: SandboxConfig | None = None,
        timeout_ms: int | None = None,
        output_cap: int = 10_000,
        container_retain: bool = False,
        run_id: str | None = None,
    ) -> None:
        self.config = config or get_sandbox_config()
        self.timeout_ms = timeout_ms or self.config.browser_timeout_ms
        self.output_cap = output_cap
        self.container_retain = container_retain
        self.run_id = run_id

    def run(
        self,
        entry_path: Path,
        workdir: Path,
        interaction: bool = True,
    ) -> BrowserRunResult:
        """Run browser automation inside a Docker container. Retries once on timeout to handle transient Docker/Chromium delays."""
        result = self._run_once(entry_path, workdir, interaction)
        if result.status == "timeout":
            logger.info(
                "Docker Playwright timed out — retrying once (entry=%s)",
                entry_path.name,
            )
            result = self._run_once(entry_path, workdir, interaction)
            if result.status == "timeout":
                logger.warning(
                    "Docker Playwright timed out on retry (entry=%s)",
                    entry_path.name,
                )
        return result

    def _run_once(
        self,
        entry_path: Path,
        workdir: Path,
        interaction: bool = True,
    ) -> BrowserRunResult:
        """Single attempt at running browser automation."""
        start = time.time()

        # Build the Python script that will run inside the container
        script_content = self._generate_script(entry_path, workdir, interaction)

        # Write onto a tmpdir that we mount into the container
        script_path = workdir / "_ams_pw_script.py"
        script_path.write_text(script_content, encoding="utf-8")

        cmd = self._build_docker_run_command(workdir, script_path)
        logger.debug("Docker Playwright command: %s", " ".join(cmd))

        try:
            result = self._start_docker_container(cmd)
            return self._collect_playwright_output(result, workdir, start)

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            # Try to salvage any screenshots the container wrote before timeout
            salvaged: list[str] = []
            output_dir = workdir / "artifacts" / "browser"
            if output_dir.is_dir():
                for png in sorted(output_dir.glob("*.png")):
                    if png.stat().st_size > 500:
                        salvaged.append(str(png))
                        logger.info(
                            "Salvaged screenshot after timeout: %s (%d bytes)",
                            png, png.stat().st_size,
                        )
                        break
            return BrowserRunResult(
                status="timeout",
                duration_ms=duration_ms,
                screenshot_paths=salvaged or None,
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


    def _build_docker_run_command(self, workdir: Path, script_path: Path) -> list[str]:
        """Build the Docker command for a single Playwright run."""
        return self._build_docker_cmd(workdir, script_path)

    def _start_docker_container(self, command: list[str]):
        """Execute the Docker command and return the completed subprocess result."""
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_ms / 1000 + 5,
        )

    def _collect_playwright_output(
        self,
        process,
        workdir: Path,
        started_at: float,
    ) -> BrowserRunResult:
        """Parse Docker stdout/stderr into a BrowserRunResult."""
        if process.returncode == 0 and process.stdout.strip():
            try:
                data = json.loads(process.stdout.strip().split("\n")[-1])
                duration_ms = int((time.time() - started_at) * 1000)

                host_screenshots: list[str] = []
                container_shot = data.get("screenshot_path", "")
                if container_shot:
                    shot_name = container_shot.rsplit("/", 1)[-1]
                    host_shot = workdir / "artifacts" / "browser" / shot_name
                    if host_shot.exists() and host_shot.stat().st_size > 500:
                        host_screenshots.append(str(host_shot))
                        logger.info(
                            "Docker Playwright screenshot saved: %s (%d bytes)",
                            host_shot, host_shot.stat().st_size,
                        )
                    else:
                        logger.warning(
                            "Screenshot path reported by container (%s) but host file missing or too small: %s (exists=%s)",
                            container_shot, host_shot, host_shot.exists(),
                        )

                if not host_screenshots:
                    output_dir = workdir / "artifacts" / "browser"
                    if output_dir.is_dir():
                        for png in sorted(output_dir.glob("*.png")):
                            if png.stat().st_size > 500:
                                host_screenshots.append(str(png))
                                logger.info(
                                    "Docker Playwright screenshot found via scan: %s (%d bytes)",
                                    png, png.stat().st_size,
                                )
                                break

                return BrowserRunResult(
                    status=data.get("status", "error"),
                    url=data.get("url", ""),
                    duration_ms=duration_ms,
                    dom_before=data.get("dom_before", "")[:self.output_cap],
                    dom_after=data.get("dom_after", "")[:self.output_cap],
                    console_errors=data.get("console_errors", [])[:20],
                    network_errors=data.get("network_errors", [])[:20],
                    actions=data.get("actions", []),
                    screenshot_paths=host_screenshots,
                    notes=data.get("notes", ""),
                )
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to parse Playwright container output: %s", exc)

        duration_ms = int((time.time() - started_at) * 1000)
        return BrowserRunResult(
            status="error",
            duration_ms=duration_ms,
            notes=f"Container exited {process.returncode}: {process.stderr[:500]}",
        )

    def _build_docker_cmd(self, workdir: Path, script_path: Path) -> list[str]:
        cfg = self.config

        # Ensure a writable output directory exists on the host so the
        # Container can persist screenshots back to the Windows filesystem.
        output_dir = workdir / "artifacts" / "browser"
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "docker", "run",
        ]

        # Container retention: skip --rm and assign a name when threats detected
        if self.container_retain and self.run_id:
            cmd.extend(["--name", f"ams-threat-pw-{self.run_id}"])
        else:
            cmd.append("--rm")

        cmd.extend([
            "--cpus", str(cfg.cpu_limit),
            "--memory", cfg.memory_limit,
            "--pids-limit", str(max(cfg.pids_limit, 256)),  # Chromium needs many threads
            "--network", cfg.network_mode,
            "--user", cfg.user,
            "--shm-size", "128m",
            "-v", f"{workdir.resolve()}:/workspace:rw",
            "-v", f"{output_dir.resolve()}:/output:rw",
            "-v", f"{script_path.resolve()}:/run_pw.py:ro",
            "--tmpfs", f"/tmp:rw,exec,size={cfg.tmpfs_size}",
            "-e", "HOME=/tmp",
            "-e", "PLAYWRIGHT_BROWSERS_PATH=/home/amsuser/.cache/ms-playwright",
        ])

        # Security hardening — NOTE: we intentionally skip --cap-drop ALL
        # For Playwright containers because Chromium's internal sandbox
        # Requires default Linux capabilities (even with --no-sandbox flag).
        # Docker network=none + user namespacing provides sufficient isolation.
        if cfg.no_new_privileges:
            cmd.extend(["--security-opt", "no-new-privileges"])
        if cfg.seccomp_profile:
            cmd.extend(["--security-opt", f"seccomp={cfg.seccomp_profile}"])

        cmd.extend([
            cfg.playwright_image,
            "python", "/run_pw.py",
        ])
        return cmd

    def _generate_script(
        self,
        entry_path: Path,
        workdir: Path,
        interaction: bool,
    ) -> str:
        """Generate the Python script executed inside the container."""
        # Resolve the relative path from workdir to entry file
        try:
            rel = entry_path.resolve().relative_to(workdir.resolve())
        except ValueError:
            rel = Path(entry_path.name)

        # Build an absolute container path, forcing forward slashes
        container_path = f"/workspace/{rel.as_posix()}".replace("\\", "/")

        timeout = self.timeout_ms
        cap = self.output_cap
        interact_flag = "True" if interaction else "False"
        safe_stem = entry_path.stem.replace(".", "_")

        return textwrap.dedent(f"""\
            import json, sys, time
            from pathlib import Path as _Path
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            result = {{
                "status": "unknown", "url": "", "duration_ms": 0,
                "dom_before": "", "dom_after": "",
                "console_errors": [], "network_errors": [],
                "actions": [], "notes": ""
            }}
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        executable_path="/usr/bin/chromium",
                        headless=True,
                        args=[
                            "--disable-dev-shm-usage",
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-gpu",
                        ],
                    )
                    page = browser.new_page()

                    console_errors, network_errors, actions = [], [], []

                    def _on_console(msg):
                        if msg.type == "error":
                            console_errors.append(msg.text[:{cap}])
                    def _on_req_fail(req):
                        network_errors.append(f"{{req.url}}: {{req.failure}}"[:{cap}])

                    page.on("console", _on_console)
                    page.on("requestfailed", _on_req_fail)

                    url = f"file://{container_path}".replace("\\\\", "/")
                    actions.append({{"type": "goto", "target": "{rel.name}"}})
                    start = time.time()
                    page.goto(url, wait_until="domcontentloaded", timeout={timeout})
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

                    # Capture a full-page screenshot to the writable /output mount
                    _shot_path = f"/output/{safe_stem}_{{int(time.time() * 1000)}}.png"
                    try:
                        try:
                            page.screenshot(path=_shot_path, full_page=True)
                        except Exception:
                            page.screenshot(path=_shot_path, full_page=False)
                        import os
                        os.chmod(_shot_path, 0o644)  # ensure readable by host
                    except Exception as _se:
                        _shot_path = ""
                        result["notes"] = f"Screenshot failed: {{_se}}"

                    browser.close()

                    result.update({{
                        "status": "pass", "url": url,
                        "duration_ms": duration_ms,
                        "dom_before": dom_before, "dom_after": dom_after,
                        "console_errors": console_errors[:20],
                        "network_errors": network_errors[:20],
                        "actions": actions,
                        "screenshot_path": _shot_path,
                    }})
            except PWTimeout:
                result["status"] = "timeout"
                result["notes"] = "Page load timeout inside container"
                # Still try to capture whatever rendered before the timeout
                try:
                    _shot_path = f"/output/{safe_stem}_timeout_{{int(time.time() * 1000)}}.png"
                    try:
                        page.screenshot(path=_shot_path, full_page=True, timeout=3000)
                    except Exception:
                        page.screenshot(path=_shot_path, full_page=False, timeout=3000)
                    import os
                    os.chmod(_shot_path, 0o644)
                    result["screenshot_path"] = _shot_path
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
            except Exception as e:
                result["status"] = "error"
                result["notes"] = str(e)

            print(json.dumps(result))
        """)


__all__ = ["DockerPlaywrightRunner"]
