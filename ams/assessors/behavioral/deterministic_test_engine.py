from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence

from ams.assessors import Assessor
from ams.core.finding_ids import BEHAVIOUR as BID
from ams.core.models import BehaviouralEvidence, Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


@dataclass
class RunResult:
    """Captured result of one subprocess-style behavioural test run."""
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


class CommandRunner:
    """Abstraction over subprocess for dependency injection in tests."""

    def run(self, args: Sequence[str], timeout: float, cwd: Path | None = None) -> RunResult:  # pragma: no cover - interface
        """Execute a command and return its captured run result."""
        raise NotImplementedError


class SubprocessRunner(CommandRunner):
    """Direct host-process runner (dev/test only)..."""

    def run(self, args: Sequence[str], timeout: float, cwd: Path | None = None) -> RunResult:
        """Execute a command on the host and capture its outputs."""
        start = time.time()
        try:
            completed = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
            )
            duration_ms = int((time.time() - start) * 1000)
            return RunResult(
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                duration_ms=duration_ms,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.time() - start) * 1000)
            return RunResult(
                exit_code=None,
                stdout=(exc.stdout or ""),
                stderr=(exc.stderr or ""),
                duration_ms=duration_ms,
                timed_out=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            duration_ms = int((time.time() - start) * 1000)
            return RunResult(exit_code=None, stdout="", stderr=str(exc), duration_ms=duration_ms, timed_out=False)


class FormDetector(HTMLParser):
    """Extract form actions and field names from HTML."""

    # Store runner settings and timeout limits.
    def __init__(self) -> None:
        super().__init__()
        self.actions: List[str] = []
        self.field_names: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        """Collect form actions and field names while parsing HTML."""
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "form":
            action = attrs_dict.get("action")
            if action:
                self.actions.append(action.strip())
        if tag.lower() in {"input", "textarea", "select"}:
            name = attrs_dict.get("name")
            if name:
                self.field_names.append(name.strip())


class DeterministicTestEngine(Assessor):
    """Runs deterministic backend/database behavioural checks."""

    name = "deterministic_test_engine"

    # Store runner settings and timeout limits.
    def __init__(
        self,
        runner: CommandRunner | None = None,
        per_test_timeout: float = 4.0,
        overall_timeout: float = 12.0,
        output_cap: int = 10_000,
    ) -> None:
        if runner is not None:
            self.runner = runner
        else:
            from ams.sandbox.factory import get_command_runner
            self.runner = get_command_runner()
        self.per_test_timeout = per_test_timeout
        self.overall_timeout = overall_timeout
        self.output_cap = output_cap
        self._fatal_error_tokens = ("fatal error", "parse error")

    def _is_php_available(self) -> bool:
        """Check whether the PHP binary is reachable by the active runner."""
        try:
            from ams.sandbox.docker_runner import DockerCommandRunner
            if isinstance(self.runner, DockerCommandRunner):
                return True
        except ImportError:  # pragma: no cover
            pass
        return bool(shutil.which("php"))

    def run(self, context: SubmissionContext) -> List[Finding]:
        """Run the enabled deterministic behavioural checks for a submission."""
        profile = context.metadata.get("profile", "unknown")
        profile_spec = getattr(getattr(context, "resolved_config", None), "profile", None)
        if profile_spec is None:
            try:
                profile_spec = get_profile_spec(profile)
            except ValueError:
                profile_spec = None

        findings: List[Finding] = []
        start = time.time()

        enabled = set(getattr(profile_spec, "enabled_behavioural_checks", []) or [])
        if not profile_spec or not enabled:
            findings.append(
                self._finding(
                    code=BID.SKIPPED_PROFILE,
                    message="Behavioural tests skipped for this profile.",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    evidence={"profile": profile},
                    required=False,
                )
            )
            return findings

        try:
            if "form_submit" in enabled or "api_exec" in enabled:
                findings.extend(self._php_smoke(context, profile, start))
            if "form_submit" in enabled:
                findings.extend(self._php_form_injection(context, profile, start))
            if "db_persist" in enabled:
                findings.extend(self._sql_sqlite_exec(context, profile, start))
            if "api_exec" in enabled:
                findings.extend(self._api_exec(context, profile, start))
        except Exception as exc:  # pragma: no cover - defensive guard
            findings.append(
                self._finding(
                    code=BID.UNEXPECTED_ERROR,
                    message="Behavioural engine encountered an unexpected error.",
                    severity=Severity.WARN,
                    profile=profile,
                    evidence={"error": str(exc)},
                )
            )
        return findings

    # PHP smoke.
    def _php_smoke(self, context: SubmissionContext, profile: str, started_at: float) -> List[Finding]:
        target = self._select_php_entrypoint(context)
        php_available = self._is_php_available()
        component_required = False

        if not target:
            evidence = BehaviouralEvidence(
                test_id="PHP.SMOKE",
                component="php",
                status="skipped",
                exit_code=None,
                stdout="",
                stderr="No PHP entrypoint discovered",
                duration_ms=0,
                inputs={"reason": "no_php_files"},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_SMOKE_SKIPPED,
                    message="PHP smoke test skipped (no PHP files found).",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"reason": "no_php_files"},
                )
            ]

        if not php_available:
            evidence = BehaviouralEvidence(
                test_id="PHP.SMOKE",
                component="php",
                status="skipped",
                exit_code=None,
                stdout="",
                stderr="php binary not available",
                duration_ms=0,
                inputs={"target": str(target)},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_SMOKE_SKIPPED,
                    message="PHP smoke test skipped; php binary not available.",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(target), "php_available": False},
                )
            ]

        if self._timed_out(started_at):
            evidence = BehaviouralEvidence(
                test_id="PHP.SMOKE",
                component="php",
                status="timeout",
                exit_code=None,
                stdout="",
                stderr="Overall behavioural stage timeout reached",
                duration_ms=int((time.time() - started_at) * 1000),
                inputs={"target": str(target)},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_SMOKE_TIMEOUT,
                    message="PHP smoke test timed out (stage timeout).",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(target)},
                )
            ]

        result = self.runner.run(
            ["php", "-d", "display_errors=1", "-f", str(target)],
            timeout=self.per_test_timeout,
            cwd=target.parent,
        )
        fatal_seen = self._contains_fatal(result.stderr) or self._contains_fatal(result.stdout)
        evidence = BehaviouralEvidence(
            test_id="PHP.SMOKE",
            component="php",
            status="timeout" if result.timed_out else ("pass" if (result.exit_code == 0 and not fatal_seen) else "fail"),
            exit_code=result.exit_code,
            stdout=self._cap(result.stdout),
            stderr=self._cap(result.stderr),
            duration_ms=result.duration_ms,
            inputs={"target": str(target), "mode": "execute"},
            outputs={"timed_out": result.timed_out},
        )

        if result.timed_out:
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_SMOKE_TIMEOUT,
                    message="PHP smoke test timed out.",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(target), "duration_ms": result.duration_ms},
                )
            ]
        if result.exit_code == 0:
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_SMOKE_PASS,
                    message="PHP entrypoint executed without fatal errors.",
                    severity=Severity.INFO,
                    profile=profile,
                    required=component_required,
                    finding_evidence={
                        "target": str(target),
                        "exit_code": result.exit_code,
                        "duration_ms": result.duration_ms,
                    },
                )
            ]
        return [
            self._record_finding(
                context,
                evidence,
                code=BID.PHP_SMOKE_FAIL,
                message="PHP entrypoint execution failed.",
                severity=Severity.FAIL,
                profile=profile,
                required=component_required,
                finding_evidence={
                    "target": str(target),
                    "exit_code": result.exit_code,
                    "stderr_first_line": self._first_line(result.stderr),
                },
            )
        ]

    # PHP form injection.
    def _php_form_injection(self, context: SubmissionContext, profile: str, started_at: float) -> List[Finding]:
        target = self._select_php_entrypoint(context)
        php_available = self._is_php_available()
        component_required = False

        if not target or not php_available:
            evidence = BehaviouralEvidence(
                test_id="PHP.FORM_INJECTION",
                component="php",
                status="skipped",
                exit_code=None,
                stdout="",
                stderr="php unavailable" if not php_available else "No PHP entrypoint discovered",
                duration_ms=0,
                inputs={"target": str(target) if target else None},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_FORM_RUN_SKIPPED,
                    message="PHP form injection test skipped.",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"php_available": php_available, "target": str(target) if target else None},
                )
            ]

        if self._timed_out(started_at):
            evidence = BehaviouralEvidence(
                test_id="PHP.FORM_INJECTION",
                component="php",
                status="timeout",
                exit_code=None,
                stdout="",
                stderr="Overall behavioural stage timeout reached",
                duration_ms=int((time.time() - started_at) * 1000),
                inputs={"target": str(target)},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_FORM_RUN_TIMEOUT,
                    message="PHP form injection test timed out (stage timeout).",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(target)},
                )
            ]

        inputs = self._discover_form_inputs(context) or {"name": "test", "email": "a@b.com"}
        wrapper_content = self._php_wrapper(inputs, target)
        with tempfile.TemporaryDirectory(prefix="ams-php-wrapper-", dir=str(target.parent)) as tmpdir:
            wrapper_path = Path(tmpdir) / "wrapper.php"
            wrapper_path.write_text(wrapper_content, encoding="utf-8")
            result = self.runner.run(
                ["php", "-d", "display_errors=1", "-f", str(wrapper_path)],
                timeout=self.per_test_timeout,
                cwd=target.parent,
            )

        fatal_seen = self._contains_fatal(result.stderr) or self._contains_fatal(result.stdout)
        passed = (
            result.exit_code == 0
            and not fatal_seen
            and (self._has_content(result.stdout) or not self._has_content(result.stderr))
        )
        status = "timeout" if result.timed_out else ("pass" if passed else "fail")
        evidence = BehaviouralEvidence(
            test_id="PHP.FORM_INJECTION",
            component="php",
            status=status,
            exit_code=result.exit_code,
            stdout=self._cap(result.stdout),
            stderr=self._cap(result.stderr),
            duration_ms=result.duration_ms,
            inputs={"target": str(target), "form_inputs": inputs},
            outputs={"timed_out": result.timed_out},
        )

        if result.timed_out:
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_FORM_RUN_TIMEOUT,
                    message="PHP form injection test timed out.",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(target), "duration_ms": result.duration_ms},
                )
            ]
        if passed:
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.PHP_FORM_RUN_PASS,
                    message="PHP form injection executed with request variables injected.",
                    severity=Severity.INFO,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(target), "exit_code": result.exit_code},
                )
            ]
        return [
            self._record_finding(
                context,
                evidence,
                code=BID.PHP_FORM_RUN_FAIL,
                message="PHP form injection execution failed.",
                severity=Severity.WARN,
                profile=profile,
                required=component_required,
                finding_evidence={
                    "target": str(target),
                    "exit_code": result.exit_code,
                    "stderr_first_line": self._first_line(result.stderr),
                },
            )
        ]

    # SQL SQLite execution.
    def _sql_sqlite_exec(self, context: SubmissionContext, profile: str, started_at: float) -> List[Finding]:
        sql_files = sorted(context.files_for("sql", relevant_only=True))
        component_required = False
        if not sql_files:
            evidence = BehaviouralEvidence(
                test_id="SQL.SQLITE_EXEC",
                component="sql",
                status="skipped",
                exit_code=None,
                stdout="",
                stderr="No SQL files discovered",
                duration_ms=0,
                inputs={"discovered_sql_files": 0},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.SQL_EXEC_SKIPPED,
                    message="SQLite execution skipped (no SQL files).",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"discovered_sql_files": 0},
                )
            ]

        if self._timed_out(started_at):
            evidence = BehaviouralEvidence(
                test_id="SQL.SQLITE_EXEC",
                component="sql",
                status="timeout",
                exit_code=None,
                stdout="",
                stderr="Overall behavioural stage timeout reached",
                duration_ms=int((time.time() - started_at) * 1000),
                inputs={"files": [str(p) for p in sql_files]},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.SQL_EXEC_TIMEOUT,
                    message="SQLite execution timed out (stage timeout).",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"files": [str(p) for p in sql_files]},
                )
            ]

        start = time.time()
        try:
            exec_result = self._execute_sql_files(sql_files)
            duration_ms = int((time.time() - start) * 1000)
            evidence = BehaviouralEvidence(
                test_id="SQL.SQLITE_EXEC",
                component="sql",
                status="pass" if exec_result["executed_ok"] else "fail",
                exit_code=0 if exec_result["executed_ok"] else 1,
                stdout="",
                stderr=self._cap("\n".join(exec_result.get("errors", []))),
                duration_ms=duration_ms,
                inputs={"files": [str(p) for p in sql_files]},
                outputs={
                    "schema_ok": exec_result["schema_ok"],
                    "insert_ok": exec_result["insert_ok"],
                    "select_ok": exec_result["select_ok"],
                    "select_row_count": exec_result.get("row_count"),
                    "executed_count": exec_result.get("executed_count", 0),
                },
                artifacts={"database": exec_result.get("database_path")},
            )
            self._record_evidence(context, evidence)
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            evidence = BehaviouralEvidence(
                test_id="SQL.SQLITE_EXEC",
                component="sql",
                status="error",
                exit_code=1,
                stdout="",
                stderr=self._cap(str(exc)),
                duration_ms=duration_ms,
                inputs={"files": [str(p) for p in sql_files]},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.SQL_EXEC_FAIL,
                    message="SQLite execution failed.",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"error": str(exc)},
                )
            ]

        if evidence.status == "fail":
            if evidence.stderr:
                message = f"SQLite execution failed: {self._first_line(evidence.stderr)}"
            else:
                message = "SQLite execution failed."
            return [
                self._finding(
                    code=BID.SQL_EXEC_FAIL,
                    message=message,
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    evidence=evidence.outputs,
                )
            ]
        return [
            self._finding(
                code=BID.SQL_EXEC_PASS,
                message="SQLite execution completed.",
                severity=Severity.INFO,
                profile=profile,
                required=component_required,
                evidence=evidence.outputs,
            )
        ]

    # API execution.
    def _api_exec(
        self, context: SubmissionContext, profile: str, started_at: float
    ) -> List[Finding]:
        """Execute API endpoint tests against PHP files that serve JSON."""
        component_required = False

        # Discovery: find files that look like API endpoints.
        api_endpoint = self._discover_api_endpoint(context)

        if not api_endpoint:
            evidence = BehaviouralEvidence(
                test_id="API.EXEC",
                component="api",
                status="skipped",
                exit_code=None,
                stdout="",
                stderr="No API endpoint discovered",
                duration_ms=0,
                inputs={"reason": "no_api_endpoint"},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.API_EXEC_SKIPPED,
                    message="API execution test skipped (no API endpoint discovered).",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"reason": "no_api_endpoint"},
                )
            ]

        if not self._is_php_available():
            evidence = BehaviouralEvidence(
                test_id="API.EXEC",
                component="api",
                status="skipped",
                exit_code=None,
                stdout="",
                stderr="php binary not available",
                duration_ms=0,
                inputs={"target": str(api_endpoint)},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.API_EXEC_SKIPPED,
                    message="API execution test skipped; php binary not available.",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(api_endpoint), "php_available": False},
                )
            ]

        if self._timed_out(started_at):
            evidence = BehaviouralEvidence(
                test_id="API.EXEC",
                component="api",
                status="timeout",
                exit_code=None,
                stdout="",
                stderr="Overall behavioural stage timeout reached",
                duration_ms=int((time.time() - started_at) * 1000),
                inputs={"target": str(api_endpoint)},
                outputs={},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.API_EXEC_TIMEOUT,
                    message="API execution test timed out (stage timeout).",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(api_endpoint)},
                )
            ]

        # Execute API test via PHP wrapper.
        findings: List[Finding] = []
        test_start = time.time()

        # Build a wrapper that simulates a GET request and captures JSON output
        wrapper_content = self._api_wrapper(api_endpoint)
        with tempfile.TemporaryDirectory(
            prefix="ams-api-exec-", dir=str(api_endpoint.parent)
        ) as tmpdir:
            wrapper_path = Path(tmpdir) / "api_test_wrapper.php"
            wrapper_path.write_text(wrapper_content, encoding="utf-8")

            result = self.runner.run(
                ["php", "-d", "display_errors=1", "-f", str(wrapper_path)],
                timeout=self.per_test_timeout,
                cwd=api_endpoint.parent,
            )

        duration_ms = int((time.time() - test_start) * 1000)

        if result.timed_out:
            evidence = BehaviouralEvidence(
                test_id="API.EXEC",
                component="api",
                status="timeout",
                exit_code=result.exit_code,
                stdout=self._cap(result.stdout),
                stderr=self._cap(result.stderr),
                duration_ms=duration_ms,
                inputs={"target": str(api_endpoint)},
                outputs={"timed_out": True},
            )
            return [
                self._record_finding(
                    context,
                    evidence,
                    code=BID.API_EXEC_TIMEOUT,
                    message="API execution test timed out.",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    finding_evidence={"target": str(api_endpoint), "duration_ms": duration_ms},
                )
            ]

        # Validate output.
        output = result.stdout.strip()
        fatal_seen = self._contains_fatal(result.stderr) or self._contains_fatal(
            result.stdout
        )

        json_valid = False
        parsed_json = None
        if output:
            try:
                parsed_json = json.loads(output)
                json_valid = True
            except (json.JSONDecodeError, ValueError):
                json_valid = False

        api_passed = (
            result.exit_code == 0 and not fatal_seen and json_valid
        )
        status = "pass" if api_passed else "fail"

        evidence = BehaviouralEvidence(
            test_id="API.EXEC",
            component="api",
            status=status,
            exit_code=result.exit_code,
            stdout=self._cap(result.stdout),
            stderr=self._cap(result.stderr),
            duration_ms=duration_ms,
            inputs={"target": str(api_endpoint)},
            outputs={
                "json_valid": json_valid,
                "response_type": type(parsed_json).__name__ if parsed_json is not None else None,
            },
        )
        self._record_evidence(context, evidence)

        # JSON validity sub-finding
        if json_valid:
            findings.append(
                self._finding(
                    code=BID.API_JSON_VALID,
                    message="API endpoint returned valid JSON.",
                    severity=Severity.INFO,
                    profile=profile,
                    required=component_required,
                    evidence={
                        "target": str(api_endpoint),
                        "response_type": type(parsed_json).__name__,
                    },
                )
            )
        elif output:
            findings.append(
                self._finding(
                    code=BID.API_JSON_INVALID,
                    message="API endpoint output is not valid JSON.",
                    severity=Severity.WARN,
                    profile=profile,
                    required=component_required,
                    evidence={
                        "target": str(api_endpoint),
                        "output_preview": output[:500],
                    },
                )
            )

        # Overall pass/fail
        if api_passed:
            findings.append(
                self._finding(
                    code=BID.API_EXEC_PASS,
                    message="API endpoint executed and returned valid JSON response.",
                    severity=Severity.INFO,
                    profile=profile,
                    required=component_required,
                    evidence={
                        "target": str(api_endpoint),
                        "exit_code": result.exit_code,
                        "duration_ms": duration_ms,
                    },
                )
            )
        else:
            findings.append(
                self._finding(
                    code=BID.API_EXEC_FAIL,
                    message="API endpoint execution failed or returned invalid response.",
                    severity=Severity.WARN,
                    profile=profile,
                    required=component_required,
                    evidence={
                        "target": str(api_endpoint),
                        "exit_code": result.exit_code,
                        "json_valid": json_valid,
                        "fatal_error": fatal_seen,
                        "stderr_first_line": self._first_line(result.stderr),
                    },
                )
            )

        return findings

    # Run SQL statements in an in-memory SQLite database.
    def _execute_sql_files(self, sql_files: Iterable[Path]) -> dict:
        statements: List[tuple[Path, str]] = []
        for path in sql_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise RuntimeError(f"Failed to read SQL file {path}: {exc}") from exc
            for raw in content.split(";"):
                stmt = raw.strip()
                if stmt:
                    statements.append((path, stmt))

        if not statements:
            return {
                "executed_ok": False,
                "executed_count": 0,
                "schema_ok": False,
                "insert_ok": False,
                "select_ok": False,
                "row_count": None,
                "errors": ["No executable statements found"],
            }

        schema_ok = False
        insert_ok = False
        select_ok = False
        row_count: int | None = None
        executed_count = 0
        errors: List[str] = []
        with sqlite3.connect(":memory:") as conn:
            for path, stmt in statements:
                stmt_lower = stmt.lower()
                try:
                    cursor = conn.execute(stmt)
                    executed_count += 1
                    if stmt_lower.startswith("create table"):
                        schema_ok = True
                    if stmt_lower.startswith("insert"):
                        insert_ok = True
                    if stmt_lower.startswith("select"):
                        rows = cursor.fetchall()
                        row_count = len(rows)
                        select_ok = True
                except Exception as exc:  # pragma: no cover - handled in tests via assertions
                    errors.append(f"{path.name}: {exc}")
        executed_ok = executed_count > 0 and (schema_ok or insert_ok or select_ok)
        return {
            "executed_ok": executed_ok,
            "executed_count": executed_count,
            "schema_ok": schema_ok,
            "insert_ok": insert_ok,
            "select_ok": select_ok,
            "row_count": row_count,
            "errors": errors[:20],  # Guard runaway error lists
        }

    def _php_wrapper(self, inputs: Mapping[str, str], target: Path) -> str:
        """Generate a minimal PHP wrapper that injects form inputs."""
        php_array = ", ".join(f"'{k}' => '{v}'" for k, v in inputs.items())
        target_posix = target.resolve().as_posix()
        return "<?php\n$_POST = array(" + php_array + ");\n$_GET = $_POST;\ninclude '" + target_posix + "';\n"

    # Build sample form inputs from the HTML files.
    def _discover_form_inputs(self, context: SubmissionContext) -> Mapping[str, str]:
        html_files = sorted(context.files_for("html", relevant_only=True))
        inputs: dict[str, str] = {}
        for path in html_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parser = FormDetector()
            try:
                parser.feed(content)
            except Exception:
                continue
            for name in parser.field_names:
                inputs[name] = f"sample_{name}"
        return inputs

    # Choose the PHP entrypoint that best matches the submission.
    def _select_php_entrypoint(self, context: SubmissionContext) -> Path | None:
        php_files = sorted(context.files_for("php", relevant_only=True))
        if not php_files:
            return None

        actions = self._extract_form_actions(context)
        resolved_root = Path(context.metadata.get("resolved_root", context.workspace_path))

        for action in actions:
            action_name = Path(action).name.lower()
            for php_path in php_files:
                rel = php_path.relative_to(resolved_root) if php_path.is_absolute() else php_path
                if php_path.name.lower() == action_name or rel.as_posix().lower().endswith(action_name):
                    return php_path

        preferred = ["index.php", "submit.php", "form.php", "process.php", "handler.php"]
        for name in preferred:
            for php_path in php_files:
                if php_path.name.lower() == name:
                    return php_path

        return php_files[0]

    # Collect form actions from the HTML files.
    def _extract_form_actions(self, context: SubmissionContext) -> List[str]:
        actions: List[str] = []
        html_files = sorted(context.files_for("html", relevant_only=True))
        for path in html_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parser = FormDetector()
            try:
                parser.feed(content)
                actions.extend(parser.actions)
            except Exception:
                continue
        return actions

    def _discover_api_endpoint(self, context: SubmissionContext) -> Path | None:
        """Find PHP files that appear to be API endpoints (return JSON)."""
        import re as _re

        php_files = sorted(context.files_for("php", relevant_only=True))
        if not php_files:
            return None

        # First pass: look for files with explicit JSON content-type + json_encode
        for path in php_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lowered = content.lower()
            has_json_header = bool(_re.search(
                r"""header\s*\(\s*['"]Content-Type\s*:\s*application/json""",
                content,
                _re.IGNORECASE,
            ))
            has_json_encode = "json_encode(" in lowered
            if has_json_header and has_json_encode:
                return path

        # Second pass: look for files with json_encode + method routing
        for path in php_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lowered = content.lower()
            has_json_encode = "json_encode(" in lowered
            has_method_routing = bool(_re.search(
                r"""\$_SERVER\s*\[\s*['"]REQUEST_METHOD""",
                content,
            ))
            if has_json_encode and has_method_routing:
                return path

        # Third pass: preferred API-style filenames
        api_names = ["api.php", "endpoint.php", "rest.php", "data.php", "service.php"]
        for name in api_names:
            for path in php_files:
                if path.name.lower() == name:
                    return path

        return None

    def _api_wrapper(self, target: Path) -> str:
        """Generate a PHP wrapper that simulates a GET request to an API endpoint."""
        # Use relative path from the wrapper's temp dir up to the target's parent
        target_name = target.name
        return (
            "<?php\n"
            "$_SERVER['REQUEST_METHOD'] = 'GET';\n"
            "$_SERVER['CONTENT_TYPE'] = 'application/json';\n"
            "$_GET = [];\n"
            "$_POST = [];\n"
            "ob_start();\n"
            f"include __DIR__ . '/../{target_name}';\n"
            "$output = ob_get_clean();\n"
            "echo $output;\n"
        )

    # Record behavioural evidence on the submission context.
    def _record_evidence(self, context: SubmissionContext, evidence: BehaviouralEvidence) -> None:
        # Ensure standard stage label and capped outputs
        evidence.stage = "behavioural"
        evidence.stdout = self._cap(evidence.stdout)
        evidence.stderr = self._cap(evidence.stderr)
        context.behavioural_evidence.append(evidence)

    # Record evidence first, then return the paired finding.
    def _record_finding(
        self,
        context: SubmissionContext,
        evidence: BehaviouralEvidence,
        *,
        code: str,
        message: str,
        severity: Severity,
        profile: str,
        required: bool | None = None,
        finding_evidence: Mapping[str, object] | None = None,
    ) -> Finding:
        self._record_evidence(context, evidence)
        return self._finding(
            code=code,
            message=message,
            severity=severity,
            profile=profile,
            evidence=finding_evidence,
            required=required,
        )

    # Build a behavioural finding with standard metadata.
    def _finding(
        self,
        code: str,
        message: str,
        severity: Severity,
        profile: str,
        evidence: Mapping[str, object] | None = None,
        required: bool | None = None,
    ) -> Finding:
        evidence_data = dict(evidence or {})
        if profile is not None and "profile" not in evidence_data:
            evidence_data["profile"] = profile
        if required is not None and "required" not in evidence_data:
            evidence_data["required"] = required
        return Finding(
            id=code,
            category="behavioral",
            message=message,
            severity=severity,
            evidence=evidence_data,
            source=self.name,
            finding_category=FindingCategory.BEHAVIORAL,
            profile=profile,
            required=required,
        )

    # Check whether the overall behavioural timeout has been reached.
    def _timed_out(self, started_at: float) -> bool:
        return (time.time() - started_at) > self.overall_timeout

    # Cap long output so stored evidence stays manageable.
    def _cap(self, text: str) -> str:
        if len(text) <= self.output_cap:
            return text
        return text[: self.output_cap] + "...[truncated]"

    # Check whether the text contains any visible content.
    def _has_content(self, text: str) -> bool:
        return bool(text and text.strip())

    # Return the first line of text for compact error messages.
    def _first_line(self, text: str) -> str:
        return (text.splitlines() or [""])[0]

    # Check whether the output contains a fatal PHP error token.
    def _contains_fatal(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(token in lowered for token in self._fatal_error_tokens)


__all__ = ["DeterministicTestEngine", "CommandRunner", "SubprocessRunner", "RunResult"]
