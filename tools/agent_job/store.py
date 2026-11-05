"""Filesystem storage layout and persistence for agent jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
from typing import Mapping

from .model import (
    PERMISSION_POLICY,
    SCHEMA_VERSION,
    SUMMARY_FIELDS,
    JobRequest,
    validate_job_id,
)


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"symlinks are not allowed for job storage: {path}")


def _private_mkdir(path: Path) -> None:
    """Make each missing parent traversable even under a restrictive umask."""
    if not path.exists():
        _private_mkdir(path.parent)
        try:
            path.mkdir(mode=0o700)
            path.chmod(0o700)
        except FileExistsError:
            pass
    _refuse_symlink(path)
    if not path.is_dir():
        raise ValueError(f"job storage must be a directory: {path}")


def atomic_write(path: Path, content: str | bytes) -> None:
    """Publish a private file after syncing its content, then sync its directory."""
    path = Path(path)
    _refuse_symlink(path.parent)
    _refuse_symlink(path)
    data = content.encode("utf-8") if isinstance(content, str) else content
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _append_stream(path: Path):
    """Open a private regular stream without following a replaced symlink."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"job stream must be a regular file: {path.name}")
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "ab")
    except BaseException:
        os.close(fd)
        raise


def _json_text(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


@dataclass(frozen=True)
class Job:
    job_id: str
    directory: Path

    @property
    def launch_env(self) -> Path:
        return self.directory / "launch-environment.json"

    @property
    def result(self) -> Path:
        return self.directory / "result.json"

    @property
    def metadata(self) -> Path:
        return self.directory / "metadata.json"

    @property
    def worker(self) -> Path:
        return self.directory / "worker.json"

    @property
    def state(self) -> Path:
        return self.directory / "state"


class JobStore:
    """Private job storage; construction and validation never create files."""

    def __init__(self, root: Path | None = None, *, cwd: Path | None = None):
        workspace = Path.cwd() if cwd is None else Path(cwd)
        if root is None:
            root = os.environ.get("AGENT_JOB_DIR") or workspace / ".artifacts" / "agent-jobs"
        # Keep the final path component intact so a symlink root is detectable.
        self.root = Path(os.path.abspath(Path(root).expanduser()))

    def _check_root(self) -> None:
        _refuse_symlink(self.root)
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("job root must be a directory")

    def create(self, request: JobRequest) -> Job:
        # Revalidate mutable option mappings before any filesystem operation.
        request.__post_init__()
        self._check_root()
        _private_mkdir(self.root)
        self._check_root()
        self.root.chmod(0o700)
        while True:
            job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(6)
            directory = self.root / job_id
            try:
                directory.mkdir(mode=0o700)
                directory.chmod(0o700)
                break
            except FileExistsError:
                continue
        job = Job(job_id, directory)
        request_data = {
            "provider": request.provider,
            "cwd": str(request.cwd),
            "model": request.model,
            "effort": request.effort,
            "session_id": request.session_id,
            "name": request.name,
            "add_dirs": [str(path) for path in request.add_dirs],
            "provider_options": dict(request.provider_options),
            "source_job_id": request.source_job_id,
        }
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "provider": request.provider,
            "status": "STARTING",
            "cwd": str(request.cwd),
            "model": request.model,
            "effort": request.effort,
            "session_id": request.session_id,
            "created_at": time.time(),
            "source_job_id": request.source_job_id,
            "transcript_dir": str(directory),
            "permission_policy": PERMISSION_POLICY,
        }
        atomic_write(directory / "prompt.txt", request.prompt)
        atomic_write(directory / "request.json", _json_text(request_data))
        atomic_write(directory / "metadata.json", _json_text(metadata))
        atomic_write(directory / "state", "STARTING\n")
        return job

    def get(self, job_id: str) -> Job:
        validate_job_id(job_id)
        self._check_root()
        directory = self.root / job_id
        _refuse_symlink(directory)
        if not directory.is_dir():
            raise ValueError(f"job not found: {job_id}")
        return Job(job_id, directory)

    def list_summaries(self) -> list[dict[str, object]]:
        self._check_root()
        if not self.root.exists():
            return []
        summaries = []
        for directory in sorted(self.root.iterdir(), reverse=True):
            try:
                job = self.get(directory.name)
                _refuse_symlink(job.directory / "metadata.json")
                _refuse_symlink(job.directory / "state")
                metadata = json.loads((job.directory / "metadata.json").read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    continue
                summary = {key: metadata.get(key) for key in SUMMARY_FIELDS}
                summary["status"] = (job.directory / "state").read_text(encoding="utf-8").strip()
                summaries.append(summary)
            except (OSError, ValueError):
                # Ignore incomplete/corrupt entries; lifecycle reconciliation is separate.
                continue
        return summaries


def _read_json(path: Path) -> dict:
    _refuse_symlink(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value
