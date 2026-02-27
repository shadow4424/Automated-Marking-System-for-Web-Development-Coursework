from __future__ import annotations

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
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


class CommandRunner:
    """Abstraction over subprocess for dependency injection in tests."""

    def run(self, args: Sequence[str], timeout: float, cwd: Path | None = None) -> RunResult:  # pragma: no cover - interface
        raise NotImplementedError


class SubprocessRunner(CommandRunner):
    """Direct host-process runner (dev/test only).

    .. deprecated:: 2.0
        Production execution uses :class:`~ams.sandbox.docker_runner.DockerCommandRunner`.
        This class is retained only for local development and unit tests that
        inject it explicitly via the *runner* parameter of
        :class:`DeterministicTestEngine`.
    """

    def run(self, args: Sequence[str], timeout: float, cwd: Path | None = None) -> RunResult:
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

    def __init__(self) -> None:
        super().__init__()
        self.actions: List[str] = []
        self.field_names: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
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
        """Check whether the PHP binary is reachable by the active runner.

        When a Docker-based runner is in use, PHP is guaranteed to be
        available inside the sandbox image — we skip the host-side
        ``shutil.which`` check that would produce a false-negative.
        """
        try:
            from ams.sandbox.docker_runner import DockerCommandRunner
            if isinstance(self.runner, DockerCommandRunner):
                return True
        except ImportError:  # pragma: no cover
            pass
        return bool(shutil.which("php"))

    def run(self, context: SubmissionContext) -> List[Finding]:
        profile = context.metadata.get("profile", "unknown")
        try:
            profile_spec = get_profile_spec(profile)
        except ValueError:
            profile_spec = None

        findings: List[Finding] = []
        start = time.time()

        if not profile_spec or profile_spec.name != "fullstack":
            findings.append(
                self._finding(
                    code=BID.SKIPPED_PROFILE,
                    message="Behavioural tests skipped for non-fullstack profile.",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    evidence={"profile": profile},
                    required=False,
                )
            )
            return findings

        try:
            findings.extend(self._php_smoke(context, profile, start))
            findings.extend(self._php_form_injection(context, profile, start))
            findings.extend(self._sql_sqlite_exec(context, profile, start))
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

    # ------------------------------------------------------------------ PHP smoke
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
            self._record_evidence(context, evidence)
            return [
                self._finding(
                    code=BID.PHP_SMOKE_SKIPPED,
                    message="PHP smoke test skipped (no PHP files found).",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    evidence={"reason": "no_php_files"},
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
            self._record_evidence(context, evidence)
            return [
                self._finding(
                    code=BID.PHP_SMOKE_SKIPPED,
                    message="PHP smoke test skipped; php binary not available.",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    evidence={"target": str(target), "php_available": False},
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
            self._record_evidence(context, evidence)
            return [
                self._finding(
                    code=BID.PHP_SMOKE_TIMEOUT,
                    message="PHP smoke test timed out (stage timeout).",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    evidence={"target": str(target)},
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
        self._record_evidence(context, evidence)

        if result.timed_out:
            return [
                self._finding(
                    code=BID.PHP_SMOKE_TIMEOUT,
                    message="PHP smoke test timed out.",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    evidence={"target": str(target), "duration_ms": result.duration_ms},
                )
            ]
        if result.exit_code == 0:
            return [
                self._finding(
                    code=BID.PHP_SMOKE_PASS,
                    message="PHP entrypoint executed without fatal errors.",
                    severity=Severity.INFO,
                    profile=profile,
                    required=component_required,
                    evidence={
                        "target": str(target),
                        "exit_code": result.exit_code,
                        "duration_ms": result.duration_ms,
                    },
                )
            ]
        return [
            self._finding(
                code=BID.PHP_SMOKE_FAIL,
                message="PHP entrypoint execution failed.",
                severity=Severity.FAIL,
                profile=profile,
                required=component_required,
                evidence={
                    "target": str(target),
                    "exit_code": result.exit_code,
                    "stderr_first_line": self._first_line(result.stderr),
                },
            )
        ]

    # ------------------------------------------------------------------ PHP form injection
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
            self._record_evidence(context, evidence)
            return [
                self._finding(
                    code=BID.PHP_FORM_RUN_SKIPPED,
                    message="PHP form injection test skipped.",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    evidence={"php_available": php_available, "target": str(target) if target else None},
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
            self._record_evidence(context, evidence)
            return [
                self._finding(
                    code=BID.PHP_FORM_RUN_TIMEOUT,
                    message="PHP form injection test timed out (stage timeout).",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    evidence={"target": str(target)},
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
        self._record_evidence(context, evidence)

        if result.timed_out:
            return [
                self._finding(
                    code=BID.PHP_FORM_RUN_TIMEOUT,
                    message="PHP form injection test timed out.",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    evidence={"target": str(target), "duration_ms": result.duration_ms},
                )
            ]
        if passed:
            return [
                self._finding(
                    code=BID.PHP_FORM_RUN_PASS,
                    message="PHP form injection executed with request variables injected.",
                    severity=Severity.INFO,
                    profile=profile,
                    required=component_required,
                    evidence={"target": str(target), "exit_code": result.exit_code},
                )
            ]
        return [
            self._finding(
                code=BID.PHP_FORM_RUN_FAIL,
                message="PHP form injection execution failed.",
                severity=Severity.WARN,
                profile=profile,
                required=component_required,
                evidence={
                    "target": str(target),
                    "exit_code": result.exit_code,
                    "stderr_first_line": self._first_line(result.stderr),
                },
            )
        ]

    # ------------------------------------------------------------------ SQL SQLite execution
    def _sql_sqlite_exec(self, context: SubmissionContext, profile: str, started_at: float) -> List[Finding]:
        sql_files = sorted(context.discovered_files.get("sql", []))
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
            self._record_evidence(context, evidence)
            return [
                self._finding(
                    code=BID.SQL_EXEC_SKIPPED,
                    message="SQLite execution skipped (no SQL files).",
                    severity=Severity.SKIPPED,
                    profile=profile,
                    required=component_required,
                    evidence={"discovered_sql_files": 0},
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
            self._record_evidence(context, evidence)
            return [
                self._finding(
                    code=BID.SQL_EXEC_TIMEOUT,
                    message="SQLite execution timed out (stage timeout).",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    evidence={"files": [str(p) for p in sql_files]},
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
            self._record_evidence(context, evidence)
            return [
                self._finding(
                    code=BID.SQL_EXEC_FAIL,
                    message="SQLite execution failed.",
                    severity=Severity.FAIL,
                    profile=profile,
                    required=component_required,
                    evidence={"error": str(exc)},
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

    # ------------------------------------------------------------------ helpers
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
            "errors": errors[:20],  # guard runaway error lists
        }

    def _php_wrapper(self, inputs: Mapping[str, str], target: Path) -> str:
        """Generate a minimal PHP wrapper that injects form inputs."""
        php_array = ", ".join(f"'{k}' => '{v}'" for k, v in inputs.items())
        target_posix = target.resolve().as_posix()
        return "<?php\n$_POST = array(" + php_array + ");\n$_GET = $_POST;\ninclude '" + target_posix + "';\n"

    def _discover_form_inputs(self, context: SubmissionContext) -> Mapping[str, str]:
        html_files = sorted(context.discovered_files.get("html", []))
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

    def _select_php_entrypoint(self, context: SubmissionContext) -> Path | None:
        php_files = sorted(context.discovered_files.get("php", []))
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

    def _extract_form_actions(self, context: SubmissionContext) -> List[str]:
        actions: List[str] = []
        html_files = sorted(context.discovered_files.get("html", []))
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

    def _record_evidence(self, context: SubmissionContext, evidence: BehaviouralEvidence) -> None:
        # Ensure standard stage label and capped outputs
        evidence.stage = "behavioural"
        evidence.stdout = self._cap(evidence.stdout)
        evidence.stderr = self._cap(evidence.stderr)
        context.behavioural_evidence.append(evidence)

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

    def _timed_out(self, started_at: float) -> bool:
        return (time.time() - started_at) > self.overall_timeout

    def _cap(self, text: str) -> str:
        if len(text) <= self.output_cap:
            return text
        return text[: self.output_cap] + "...[truncated]"

    def _has_content(self, text: str) -> bool:
        return bool(text and text.strip())

    def _first_line(self, text: str) -> str:
        return (text.splitlines() or [""])[0]

    def _contains_fatal(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(token in lowered for token in self._fatal_error_tokens)


__all__ = ["DeterministicTestEngine", "CommandRunner", "SubprocessRunner", "RunResult"]
