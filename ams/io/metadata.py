from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


@dataclass
class SubmissionMetadata:
    """Metadata for a submission including identity and upload information."""
    
    student_id: str
    assignment_id: str
    timestamp: datetime
    original_filename: str
    uploader_metadata: Dict[str, str] = field(default_factory=dict)
    file_hash: Optional[str] = None
    version: int = 1
    
    def to_dict(self) -> Dict[str, object]:
        """Convert metadata to dictionary for JSON serialization."""
        return {
            "student_id": self.student_id,
            "assignment_id": self.assignment_id,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "original_filename": self.original_filename,
            "uploader_metadata": self.uploader_metadata,
            "file_hash": self.file_hash,
            "version": self.version,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SubmissionMetadata":
        """Create metadata from dictionary."""
        timestamp_str = data.get("timestamp", "")
        if isinstance(timestamp_str, str):
            # Parse ISO format timestamp
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except Exception:
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)
        
        return cls(
            student_id=str(data.get("student_id", "")),
            assignment_id=str(data.get("assignment_id", "")),
            timestamp=timestamp,
            original_filename=str(data.get("original_filename", "")),
            uploader_metadata=dict(data.get("uploader_metadata", {}) or {}),
            file_hash=data.get("file_hash"),
            version=int(data.get("version", 1)),
        )


class MetadataValidator:
    """Validates and sanitizes metadata fields."""
    
    # Common student ID patterns (alphanumeric, may include dashes/underscores)
    STUDENT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{3,20}$')
    
    # Assignment ID patterns (alphanumeric, may include dashes/underscores)
    ASSIGNMENT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{2,30}$')
    
    @staticmethod
    def validate_student_id(student_id: str) -> tuple[bool, Optional[str]]:
        """Validate student ID format."""
        if not student_id or not student_id.strip():
            return False, "Student ID is required"
        
        student_id = student_id.strip()
        if not MetadataValidator.STUDENT_ID_PATTERN.match(student_id):
            return False, "Student ID must be 3-20 alphanumeric characters (may include - or _)"
        
        return True, None
    
    @staticmethod
    def validate_assignment_id(assignment_id: str) -> tuple[bool, Optional[str]]:
        """Validate assignment ID format."""
        if not assignment_id or not assignment_id.strip():
            return False, "Assignment ID is required"
        
        assignment_id = assignment_id.strip()
        if not MetadataValidator.ASSIGNMENT_ID_PATTERN.match(assignment_id):
            return False, "Assignment ID must be 2-30 alphanumeric characters (may include - or _)"
        
        return True, None
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Sanitize filename to prevent path traversal and unsafe characters."""
        if not filename:
            return "submission.zip"
        
        # Remove path components
        filename = Path(filename).name
        
        # Remove dangerous characters
        filename = re.sub(r'[<>:"|?*\x00-\x1f]', '', filename)
        
        # Limit length
        if len(filename) > 255:
            name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
            filename = name[:250] + ('.' + ext if ext else '')
        
        return filename or "submission.zip"
    
    @staticmethod
    def sanitize_identifier(identifier: str) -> str:
        """Sanitize student/assignment ID to prevent injection."""
        if not identifier:
            return ""
        
        identifier = identifier.replace("..", "").replace("/", "").replace("\\", "")
        
        # Remove dangerous characters
        identifier = re.sub(r'[<>:"|?*\x00-\x1f]', '', identifier)
        
        # Limit length
        identifier = identifier[:50]
        
        return identifier.strip()
    
    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """Compute SHA-256 hash of file for tamper detection."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()


__all__ = [
    "SubmissionMetadata",
    "MetadataValidator",
]

