"""Data models and constants for portable agent jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Mapping, Protocol


SCHEMA_VERSION = 1
EFFORT_LEVELS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
PERMISSION_POLICY = "Providers run autonomously with permission/sandbox bypass flags."
JOB_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
TERMINAL_STATES = frozenset({"SUCCESS", "ERROR", "STOPPED"})
RUNNER_MODULE = "rally_core"
RUNNER_PATH = (Path(__file__).resolve().parents[1] / "rally").resolve()
SUMMARY_FIELDS = ("schema_version", "job_id", "provider", "status", "cwd", "model", "effort", "session_id", "created_at", "source_job_id", "transcript_dir")
PROVIDERS = ("agy", "copilot", "codex", "claude")
DOCTOR_TIMEOUT_SECONDS = 3.0
DOCTOR_OUTPUT_LIMIT = 4096
MAX_PUBLIC_RESPONSE_BYTES = 1_048_576
MAX_EVENT_BYTES = 1_048_576
TRUNCATION_MARKER = "\n[truncated]"


def truncate_response(text: str, limit: int = MAX_PUBLIC_RESPONSE_BYTES) -> str:
    """Deterministically truncate response to at most limit bytes of valid UTF-8."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker = TRUNCATION_MARKER.encode("utf-8")
    max_payload = max(0, limit - len(marker))
    clipped = encoded[:max_payload]
    while clipped:
        try:
            return clipped.decode("utf-8") + TRUNCATION_MARKER
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return TRUNCATION_MARKER
HELP_TEXT = """Lifecycle:
  rally PROVIDER [run|start] (--prompt TEXT | --prompt-file FILE) [options]
  rally followup JOB_ID (--prompt TEXT | --prompt-file FILE)
  rally status JOB_ID
  rally wait JOB_ID [--timeout SECONDS]
  rally read JOB_ID
  rally stop JOB_ID
  rally list [--cwd DIR]
  rally doctor [PROVIDER ...]

Permission policy:
  All providers run autonomously with permission/sandbox bypass flags. These
  flags permit broader machine access than --cwd/--add-dir; trust every prompt
  as code-execution authority.

Managed jobs:
  run blocks and prints terminal JSON. For parallel work, start once, retain
  job_id, continue other work, then wait once. wait prints no polling output.
  followup starts a NEW job only from a completed source with a resumable session
  ID; it returns a descriptor. Wait on its new job_id. No active message injection.
  read prints final assistant text. stop drains owned processes. status/wait
  recover dead workers into durable ERROR results. A wait timeout returns 124
  without stopping the job. Only terminal SUCCESS exits zero.

Storage and result schema:
  Transcripts default to <cwd>/.artifacts/agent-jobs/JOB_ID. Use the same caller
  cwd or AGENT_JOB_DIR for followup/status/wait/read/stop. list --cwd filters jobs.
  Private files include prompt.txt, request.json, metadata.json, stdout.jsonl,
  stderr.log, manager logs, response.txt, state, worker.json, and result.json.
  list/status omit prompts and raw output. Normalized terminal schema_version=1:
  job_id, provider, status, exit_code, model, effort, session_id, response, usage,
  error, cwd, transcript_dir, created_at, started_at, finished_at, source_job_id,
  provider_result. Provider semantic failures override zero process exits.

Providers and examples:
  rally agy run --model flash --effort high --prompt 'Review changes.'
  rally copilot start --model auto --max-credits 30 --prompt-file task.txt
  rally codex start --effort low --cwd /absolute/project --prompt 'Review.'
  rally claude run --model haiku --effort low --prompt 'Review changes.'
  rally wait JOB_ID
  rally followup JOB_ID --prompt 'Check another case.'
  AGY defaults: sonnet alias and high effort; flash alias also supported.
  AGY selectors: --project ID and --conversation ID may coexist; --new-project
  conflicts with either. --session resumes a provider conversation/thread.
  Copilot defaults: auto model, omitted effort. auto rejects explicit effort.
  --max-credits maps to Copilot's soft --max-ai-credits cap (minimum 30).
  --name supports Copilot/Claude; --add-dir is repeatable and must be absolute.
  Codex/Claude omitted model/effort use provider defaults.

Environment and portability:
  Python 3.9+, standard library only; no .venv, external dependencies, or global install.
  Use /absolute/path/to/rally from any repository with --cwd DIR.
  AGENT_JOB_DIR overrides transcript root (relative to caller cwd).
  AGENT_JOB_LAUNCHER=auto|launchctl|detached; auto uses launchctl on macOS and
  detached sessions elsewhere. AGENT_JOB_LAUNCHCTL overrides launchctl executable.
  Provider executables and credentials use normal PATH/provider configuration.
  Compatibility shim ./agent-job preserves legacy entrypoint compatibility.

Doctor:
  READY means local CLI/version and available local auth checks only. Doctor sends
  no prompt or API request; service access, quota, billing, and OAuth organization
  policy remain unverified even after local login succeeds. Live job results are
  authoritative. Output contains no raw auth/version diagnostics or credentials.
"""


def validate_job_id(job_id: str) -> str:
    """Accept only opaque IDs generated by this runner, never paths."""
    if not isinstance(job_id, str) or JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise ValueError("invalid job ID")
    return job_id


def _single_line(value: str | None, label: str) -> None:
    if value is not None and (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} must be nonempty text without control characters")


def _directory(path: Path, label: str) -> Path:
    path = Path(path).expanduser()
    if not path.is_dir():
        raise ValueError(f"{label} must be an existing directory: {path}")
    return path.resolve()


@dataclass(frozen=True)
class JobRequest:
    provider: str
    prompt: str
    cwd: Path
    model: str | None = None
    effort: str | None = None
    session_id: str | None = None
    name: str | None = None
    add_dirs: tuple[Path, ...] = ()
    provider_options: Mapping[str, object] = field(default_factory=dict)
    source_job_id: str | None = None

    def __post_init__(self):
        _single_line(self.provider, "provider")
        fixture = self.provider == "_fixture" and os.environ.get("AGENT_JOB_TEST_FIXTURE_EXECUTABLE")
        if not isinstance(self.provider, str) or (not fixture and re.fullmatch(r"[a-z][a-z0-9_-]*", self.provider) is None):
            raise ValueError("invalid provider name")
        if not isinstance(self.prompt, str):
            raise ValueError("prompt must be text")
        for label in ("model", "session_id", "name"):
            _single_line(getattr(self, label), label)
        if self.effort is not None and self.effort not in EFFORT_LEVELS:
            raise ValueError(f"invalid effort: {self.effort}")
        if self.source_job_id is not None:
            validate_job_id(self.source_job_id)
        object.__setattr__(self, "cwd", _directory(self.cwd, "cwd"))
        extra_directories = []
        for path in self.add_dirs:
            path = Path(path).expanduser()
            if not path.is_absolute():
                raise ValueError("extra directories must be absolute")
            extra_directories.append(_directory(path, "extra directory"))
        object.__setattr__(self, "add_dirs", tuple(extra_directories))
        if not isinstance(self.provider_options, Mapping):
            raise ValueError("provider options must be a JSON object")
        try:
            json.dumps(dict(self.provider_options), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("provider options must be JSON serializable") from error


@dataclass(frozen=True)
class ProviderOutcome:
    success: bool
    response: str
    session_id: str | None
    usage: Mapping[str, object]
    error: Mapping[str, object] | None
    model: str | None
    provider_result: Mapping[str, object]

    def __post_init__(self):
        if isinstance(self.response, str):
            object.__setattr__(self, "response", truncate_response(self.response))
        if isinstance(self.provider_result, Mapping):
            updated = dict(self.provider_result)
            modified = False
            for key in ("response", "result"):
                if isinstance(updated.get(key), str):
                    truncated = truncate_response(updated[key])
                    if truncated != updated[key]:
                        updated[key] = truncated
                        modified = True
            if modified:
                object.__setattr__(self, "provider_result", updated)


class ProviderAdapter(Protocol):
    """Providers validate before storage, build argv, then normalize raw output."""

    def validate(self, request: JobRequest) -> None:
        ...

    def build_command(self, request: JobRequest) -> list[str]:
        ...

    def parse(self, request: JobRequest, raw_path: Path, stderr_path: Path, exit_code: int) -> ProviderOutcome:
        ...

    def extract_session_id(self, raw_path: Path) -> str | None:
        """Recover a session from incomplete output without terminal validation."""
        ...
