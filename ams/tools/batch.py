from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple

from ams.core.pipeline import AssessmentPipeline
from ams.core.config import ScoringMode
from ams.core.assignment_config import resolve_assignment_config
from ams.io.web_storage import extract_review_flags_from_report, safe_extract_zip, find_submission_root

# Submission filename must match: studentID_assignmentID.zip
# studentID: alphanumeric only, e.g. "student1234" (letters and digits, no special characters)
# assignmentID: anything after the first underscore; matched against the batch-level assignment ID at runtime
_STUDENT_ID_RE = re.compile(r'^[a-zA-Z0-9]+$')


def validate_submission_filename(filename: str) -> Tuple[bool, str, str]:
    """Parse and structurally validate a ZIP filename as studentID_assignmentID.zip.

    Checks that the stem contains a '_' separator, that both parts are non-empty,
    and that the studentID is alphanumeric.  Does NOT cross-check the assignmentID
    against a batch-level assignment ID — that check happens at processing time.

    Returns (is_valid, student_id, assignment_id).
    student_id and assignment_id are empty strings when the filename is invalid.
    """
    stem = Path(filename).stem
    if '_' not in stem:
        return False, "", ""
    idx = stem.index('_')
    student_id = stem[:idx]
    assignment_id_part = stem[idx + 1:]
    if not student_id or not assignment_id_part:
        return False, "", ""
    if not _STUDENT_ID_RE.fullmatch(student_id):
        return False, "", ""
    return True, student_id, assignment_id_part


@dataclass(frozen=True)
class BatchItem:
    id: str
    path: Path
    kind: str  # "dir" or "zip"


def discover_batch_items(submissions_dir: Path) -> List[BatchItem]:
    items: List[BatchItem] = []
    for entry in submissions_dir.iterdir():
        name = entry.name
        if name.startswith(".") or name in {"__MACOSX", ".DS_Store"}:
            continue
        if entry.is_dir():
            items.append(BatchItem(id=name, path=entry, kind="dir"))
        elif entry.is_file() and entry.suffix.lower() == ".zip":
            items.append(BatchItem(id=entry.stem, path=entry, kind="zip"))
    items.sort(key=lambda b: b.id)
    return items


def _build_empty_component_scores() -> Dict[str, Optional[float]]:
    return {"html": None, "css": None, "js": None, "php": None, "sql": None, "api": None}


def _remove_legacy_batch_outputs(out_root: Path) -> None:
    for filename in (
        "component_means.csv",
        "failure_reasons_frequency.csv",
        "findings_frequency.csv",
        "score_buckets.csv",
    ):
        candidate = out_root / filename
        if candidate.is_file():
            candidate.unlink()
    for candidate in out_root.glob("batch_reports_*.zip"):
        if candidate.is_file():
            candidate.unlink()


def run_batch(
    submissions_dir: Path,
    out_root: Path,
    profile: str,
    keep_individual_runs: bool = True,
    assignment_id: Optional[str] = None,
    profile_config_path: Optional[str] = None,
    scoring_mode: ScoringMode = ScoringMode.STATIC_PLUS_LLM,
) -> dict:
    out_root.mkdir(parents=True, exist_ok=True)
    runs_root = out_root / "runs"
    if keep_individual_runs:
        runs_root.mkdir(parents=True, exist_ok=True)

    pipeline = AssessmentPipeline(scoring_mode=scoring_mode)
    working_dir = submissions_dir
    temp_ctx: Optional[tempfile.TemporaryDirectory[str]] = None
    try:
        if submissions_dir.is_file() and submissions_dir.suffix.lower() == ".zip":
            temp_ctx = tempfile.TemporaryDirectory(prefix="ams-bundle-")
            extracted = Path(temp_ctx.name)
            safe_extract_zip(submissions_dir, extracted)
            working_dir = find_submission_root(extracted)
        else:
            working_dir = find_submission_root(submissions_dir)

        items = discover_batch_items(working_dir)
        records: List[dict] = []

        for item in items:
            record = _process_one_submission(
                item=item,
                runs_root=runs_root,
                pipeline=pipeline,
                profile=profile,
                keep_individual_runs=keep_individual_runs,
                assignment_id=assignment_id,
                profile_config_path=profile_config_path,
            )
            records.append(record)

        write_outputs(out_root, records, profile=profile, profile_config_path=profile_config_path)
        print("Batch complete.")

        return {"records": records}
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()


def write_outputs(
    out_root: Path,
    records: List[dict],
    profile: str = "frontend",
    profile_config_path: Optional[str] = None,
) -> None:
    _remove_legacy_batch_outputs(out_root)
    batch_summary = {"records": records}
    (out_root / "batch_summary.json").write_text(json.dumps(batch_summary, indent=2), encoding="utf-8")

    csv_path = out_root / "batch_summary.csv"
    try:
        relevant_components = set(
            resolve_assignment_config(
                profile,
                metadata={"profile_config_path": profile_config_path} if profile_config_path else None,
            ).required_components
        )
    except Exception:
        relevant_components = {"html", "css", "js", "php", "sql", "api"}

    fieldnames = ["id", "student_id", "assignment_id", "kind", "overall", "html", "css", "js", "php", "sql", "api", "status", "error"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in sorted(records, key=lambda r: r["id"]):
            comps = record.get("components") or {}
            writer.writerow(
                {
                    "id": record.get("id"),
                    "student_id": record.get("student_id", ""),
                    "assignment_id": record.get("assignment_id", ""),
                    "kind": record.get("kind"),
                    "overall": record.get("overall"),
                    "html": comps.get("html"),
                    "css": comps.get("css"),
                    "js": comps.get("js"),
                    "php": comps.get("php") if "php" in relevant_components else "",
                    "sql": comps.get("sql") if "sql" in relevant_components else "",
                    "api": comps.get("api") if "api" in relevant_components else "",
                    "status": record.get("status", ""),
                    "error": record.get("error", "") or record.get("validation_error", ""),
                }
            )


def _process_one_submission(
    item: BatchItem,
    runs_root: Path,
    pipeline: AssessmentPipeline,
    profile: str,
    keep_individual_runs: bool,
    assignment_id: Optional[str] = None,
    profile_config_path: Optional[str] = None,
) -> dict:
    # ── Filename validation ────────────────────────────────────────────────────
    # Step 1: must contain at least one '_' separator
    stem = Path(item.path.name).stem
    record: dict = {
        "id": item.id,
        "path": str(item.path),
        "kind": item.kind,
        "overall": None,
        "components": _build_empty_component_scores(),
        "report_path": None,
        "original_filename": item.path.name,
        "student_id": item.id,
        "assignment_id": assignment_id or "",
    }

    if '_' not in stem:
        record["overall"] = 0.0
        record["status"] = "invalid_filename"
        record["invalid"] = True
        record["validation_error"] = (
            f"'{item.path.name}' does not match the required format studentID_assignmentID.zip"
        )
        return record

    sep = stem.index('_')
    raw_student_id = stem[:sep]
    raw_assignment_id = stem[sep + 1:]

    # Step 2: studentID must be alphanumeric only
    if not raw_student_id or not _STUDENT_ID_RE.fullmatch(raw_student_id):
        record["student_id"] = raw_student_id or item.id
        record["overall"] = 0.0
        record["status"] = "invalid_student_id"
        record["invalid"] = True
        record["validation_error"] = (
            f"Student ID '{raw_student_id}' in '{item.path.name}' is invalid: "
            f"must contain only letters and digits (e.g. student1234)"
        )
        return record

    # Step 3: assignmentID must be non-empty and match the batch assignment
    if not raw_assignment_id:
        record["student_id"] = raw_student_id
        record["overall"] = 0.0
        record["status"] = "invalid_assignment_id"
        record["invalid"] = True
        record["validation_error"] = (
            f"Assignment ID is missing in '{item.path.name}'"
        )
        return record

    if assignment_id and raw_assignment_id != assignment_id:
        record["student_id"] = raw_student_id
        record["assignment_id"] = raw_assignment_id
        record["overall"] = 0.0
        record["status"] = "invalid_assignment_id"
        record["invalid"] = True
        record["validation_error"] = (
            f"Assignment ID '{raw_assignment_id}' in '{item.path.name}' "
            f"does not match the expected assignment '{assignment_id}'"
        )
        return record

    # Both parts are valid — use parsed values for the rest of processing
    parsed_student_id = raw_student_id
    parsed_assignment_id = raw_assignment_id
    record["student_id"] = parsed_student_id
    record["assignment_id"] = parsed_assignment_id
    # ──────────────────────────────────────────────────────────────────────────

    temp_ctx: Optional[tempfile.TemporaryDirectory[str]] = None
    try:
        if keep_individual_runs:
            run_dir = (runs_root / item.id).resolve()
            run_dir.mkdir(parents=True, exist_ok=True)
            workspace_path = run_dir
        else:
            temp_ctx = tempfile.TemporaryDirectory(prefix="ams-batch-")
            workspace_path = Path(temp_ctx.name)

        submission_root = item.path.resolve()
        
        if item.kind == "zip":
            extracted = workspace_path / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(item.path.resolve(), extracted)
            submission_root = find_submission_root(extracted).resolve()
        elif item.kind == "dir":
            # isolate by copying to workspace - use 'source_files' to avoid conflict
            # with SubmissionProcessor which clears 'submission' folder
            source_path = item.path.resolve()
            target = workspace_path / "source_files"
            if target.exists():
                shutil.rmtree(target)
            # Copy directory contents to target
            shutil.copytree(source_path, target, dirs_exist_ok=True)
            submission_root = find_submission_root(target).resolve()
        if keep_individual_runs:
            record["path"] = str(submission_root)

        # Create metadata for this submission using the validated filename components
        from ams.io.metadata import MetadataValidator, SubmissionMetadata
        from datetime import datetime, timezone

        submission_metadata = SubmissionMetadata(
            student_id=MetadataValidator.sanitize_identifier(parsed_student_id),
            assignment_id=MetadataValidator.sanitize_identifier(parsed_assignment_id),
            timestamp=datetime.now(timezone.utc),
            original_filename=MetadataValidator.sanitize_filename(item.path.name),
        )
        
        pipeline_metadata = submission_metadata.to_dict()
        if profile_config_path:
            pipeline_metadata["profile_config_path"] = str(profile_config_path)

        report_path = pipeline.run(
            submission_path=submission_root,
            workspace_path=workspace_path,
            profile=profile,
            metadata=pipeline_metadata,
        )
        record["report_path"] = str(report_path)
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        canonical_submission = workspace_path / "submission"
        if keep_individual_runs and canonical_submission.exists():
            record["path"] = str(canonical_submission)

        # Extract metadata from report if available
        report_metadata = report_data.get("metadata", {}).get("submission_metadata")
        if report_metadata:
            record["student_id"] = report_metadata.get("student_id", parsed_student_id)
            record["assignment_id"] = report_metadata.get("assignment_id", parsed_assignment_id)
            record["original_filename"] = report_metadata.get("original_filename", item.path.name)
            record["upload_timestamp"] = report_metadata.get("timestamp")
        
        scores = report_data.get("scores", {})
        record["overall"] = scores.get("overall")
        comps = scores.get("by_component", {}) or {}
        record["components"] = {
            k: comps.get(k, {}).get("score")
            for k in _build_empty_component_scores().keys()
        }
        review_flags = extract_review_flags_from_report(report_data)
        llm_error_flagged = bool(review_flags.get("llm_error_flagged"))
        record["status"] = "llm_error" if llm_error_flagged else "ok"
        record["pipeline_status"] = "failed" if llm_error_flagged else "completed"
        record["validity_status"] = "invalid" if llm_error_flagged else "valid"
        if review_flags.get("threat_count"):
            record["threat_count"] = int(review_flags.get("threat_count") or 0)
        if review_flags.get("threat_flagged"):
            record["threat_flagged"] = True
        if llm_error_flagged:
            record["llm_error_flagged"] = True
            record["llm_error_message"] = review_flags.get("llm_error_message")
            record["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
    except Exception as exc:  # pragma: no cover - defensive
        record["error"] = str(exc)
        record["status"] = "error"
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()
    return record


__all__ = [
    "BatchItem",
    "discover_batch_items",
    "validate_submission_filename",
    "run_batch",
    "write_outputs",
]
