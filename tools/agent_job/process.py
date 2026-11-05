"""Process management, job execution, and durable worker lifecycle."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from functools import lru_cache
import json
import fcntl
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Mapping

from .model import (
    JOB_ID_PATTERN,
    MAX_EVENT_BYTES,
    PERMISSION_POLICY,
    RUNNER_PATH,
    SCHEMA_VERSION,
    SUMMARY_FIELDS,
    TERMINAL_STATES,
    JobRequest,
    ProviderAdapter,
    ProviderOutcome,
    _directory,
    _single_line,
    truncate_response,
    validate_job_id,
)
from .store import (
    Job,
    JobStore,
    _append_stream,
    _json_text,
    _read_json,
    _refuse_symlink,
    atomic_write,
)


def _output_objects(raw_path: Path, max_bytes: int = 2 * MAX_EVENT_BYTES):
    """Yield complete JSON objects amid diagnostics; incomplete tails are harmless."""
    _refuse_symlink(raw_path)
    if not raw_path.exists():
        return
    file_size = raw_path.stat().st_size
    if file_size <= max_bytes:
        with raw_path.open("rb") as stream:
            raw = stream.read(max_bytes).decode("utf-8", errors="replace")
    else:
        with raw_path.open("rb") as stream:
            stream.seek(file_size - max_bytes)
            chunk = stream.read(max_bytes)
            first_nl = chunk.find(b"\n")
            if first_nl >= 0:
                chunk = chunk[first_nl + 1:]
            raw = chunk.decode("utf-8", errors="replace")
    decoder, offset = json.JSONDecoder(), 0
    while offset < len(raw):
        start = offset
        while start < len(raw) and raw[start] in " \t\r\n":
            start += 1
        try:
            value, end = decoder.raw_decode(raw, start)
        except (ValueError, RecursionError):
            newline = raw.find("\n", start)
            if newline < 0:
                break
            offset = newline + 1
            continue
        offset = end
        if isinstance(value, dict):
            yield value


def _event_session_id(provider: str, event: dict) -> str | None:
    """Recognize only provider session events and valid single-line IDs."""
    kind = event.get("type")
    if provider == "agy" and "conversation_id" in event:
        value = event["conversation_id"]
    elif provider == "copilot" and isinstance(kind, str) and (kind == "result" or kind.startswith("session.")):
        value = event.get("sessionId")
        if value is None and isinstance(event.get("data"), dict):
            value = event["data"].get("sessionId")
    elif provider == "codex" and kind == "thread.started":
        value = event.get("thread_id")
    elif provider == "claude" and (kind == "result" or (kind == "system" and event.get("subtype") == "init")):
        value = event.get("session_id")
    else:
        return None
    try:
        _single_line(value, "session ID")
    except ValueError:
        return None
    return value


def _partial_session(raw_path: Path, provider: str) -> str | None:
    if not raw_path.exists():
        return None
    _refuse_symlink(raw_path)
    file_size = raw_path.stat().st_size
    if file_size == 0:
        return None
    bound = 2 * MAX_EVENT_BYTES
    scanner = _IncrementalSessionScanner(raw_path, provider)
    session_id = scanner.scan(max_bytes=bound)
    if session_id is not None or file_size <= bound:
        return session_id
    tail_offset = max(0, file_size - bound)
    tail_scanner = _IncrementalSessionScanner(raw_path, provider, offset=tail_offset)
    tail_scanner.discarding = True
    return tail_scanner.scan(max_bytes=bound)


class _IncrementalSessionScanner:
    """Frame complete JSON objects from appended bytes without reparsing tails."""

    chunk_size = 65536
    max_line_size = 1048576

    def __init__(self, raw_path: Path, provider: str, offset: int = 0):
        self.raw_path = raw_path
        self.provider = provider
        self.offset = offset
        self.pending = bytearray()
        self.line_start = 0
        self.depth = 0
        self.in_string = False
        self.escaped = False
        self.at_line_start = True
        self.discarding = False
        self.session_id = None

    def _reset_frame(self) -> None:
        self.pending.clear()
        self.line_start = 0
        self.depth = 0
        self.in_string = False
        self.escaped = False

    def _session_from_json(self, raw: bytes, *, standalone_line: bool = False) -> str | None:
        # AGY uses ordinary objects, so a pretty-printed nested object can
        # contain a different conversation_id. Only the whole frame is safe.
        if standalone_line and self.provider == "agy":
            return None
        try:
            event = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            return None
        return _event_session_id(self.provider, event) if isinstance(event, dict) else None

    def _feed(self, byte: int) -> str | None:
        if self.discarding:
            if byte == 10:
                self.discarding, self.at_line_start = False, True
            return None
        if self.depth == 0:
            if byte == 10:
                self.at_line_start = True
            elif self.at_line_start and byte in (9, 13, 32):
                pass
            elif self.at_line_start and byte == 123:
                self.pending.append(byte)
                self.depth, self.at_line_start = 1, False
            else:
                self.at_line_start = False
            return None

        self.pending.append(byte)
        if len(self.pending) > self.max_line_size:
            self._reset_frame()
            self.discarding = True
            return None
        if byte == 10:
            self.at_line_start = True
            # A complete JSONL event can follow an unfinished outer object.
            # The line is a slice of the frame, so no second growing buffer.
            session_id = self._session_from_json(self.pending[self.line_start:-1], standalone_line=True)
            self.line_start = len(self.pending)
            if session_id is not None:
                return session_id
        elif self.at_line_start and byte not in (9, 13, 32):
            self.at_line_start = False
        if self.in_string:
            if self.escaped:
                self.escaped = False
            elif byte == 92:
                self.escaped = True
            elif byte == 34:
                self.in_string = False
        elif byte == 34:
            self.in_string = True
        elif byte in (91, 123):
            self.depth += 1
        elif byte in (93, 125):
            self.depth -= 1
            if self.depth == 0:
                session_id = self._session_from_json(self.pending)
                self._reset_frame()
                self.at_line_start = True
                return session_id
        return None

    def scan(self, max_bytes: int | None = None) -> str | None:
        if self.session_id is not None:
            return self.session_id
        if not self.raw_path.exists():
            return None
        _refuse_symlink(self.raw_path)
        if self.raw_path.stat().st_size < self.offset:
            self.offset, self.discarding, self.at_line_start = 0, False, True
            self._reset_frame()
        bytes_read = 0
        with self.raw_path.open("rb") as stream:
            stream.seek(self.offset)
            while True:
                to_read = self.chunk_size
                if max_bytes is not None:
                    remaining = max_bytes - bytes_read
                    if remaining <= 0:
                        break
                    to_read = min(to_read, remaining)
                chunk = stream.read(to_read)
                if not chunk:
                    break
                self.offset += len(chunk)
                bytes_read += len(chunk)
                for byte in chunk:
                    self.session_id = self._feed(byte)
                    if self.session_id is not None:
                        return self.session_id
        return None


_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}"),
    re.compile(r"(?i)(\b(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret"
               r"|password|passwd|credentials?)\b\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|\S+)"),
    re.compile(r"\b(?:sk|pk|ghp|gho|ghu|ghs|ghr|github_pat|xox[abprs])[-_][A-Za-z0-9_-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    re.compile(r"(?i)\bcredential[-_]sentinel[A-Za-z0-9_-]*"),
)

_PATH_PATTERN = re.compile(r"/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")


def _public_text(value: object, limit: int = 300) -> str:
    """One bounded line with credential-shaped values removed; never raw provider text."""
    text = " ".join(str(value).split())
    text = _CREDENTIAL_PATTERNS[0].sub("[redacted]", text)
    text = _CREDENTIAL_PATTERNS[1].sub(r"\1[redacted]", text)
    for pattern in _CREDENTIAL_PATTERNS[2:]:
        text = pattern.sub("[redacted]", text)
    text = _PATH_PATTERN.sub("[path]", text)
    return text[:limit]


@contextmanager
def _job_lock(job: Job):
    """Serialize state writers; kernel releases this lock after a worker crash."""
    fd = os.open(job.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _remove_launch_environment(job: Job) -> None:
    path = job.launch_env
    try:
        path.unlink()
    except FileNotFoundError:
        return
    fd = os.open(job.directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _restore_launch_environment(job: Job) -> None:
    path = job.launch_env
    if not path.exists():
        return
    try:
        environment = _read_json(path)
        if any(not key or "=" in key or "\0" in key or not isinstance(value, str) or "\0" in value
               for key, value in environment.items()):
            raise ValueError("invalid launch environment")
        os.environ.update(environment)
    finally:
        _remove_launch_environment(job)


def _group_has_members(pgid: int) -> bool:
    """Keep supervisor alive while non-zombie provider descendants remain."""
    try:
        output = subprocess.run(["ps", "-axo", "pid=,pgid=,stat="],
                                capture_output=True, text=True, check=True, start_new_session=True).stdout
        for line in output.splitlines():
            pid, group, state = line.split()
            if int(group) == pgid and int(pid) != os.getpid() and not state.startswith("Z"):
                return True
        return False
    except (OSError, ValueError, subprocess.SubprocessError):
        # An unavailable process table cannot establish that descendants exited.
        return True


@lru_cache(maxsize=1)
def _boot_id() -> str:
    if sys.platform.startswith("linux"):
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if sys.platform == "darwin":
        return subprocess.run(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                              capture_output=True, text=True, check=True).stdout.strip()
    raise ValueError("precise process identity requires Linux or macOS")


def _process_birth(pid: int) -> str | None:
    """Kernel birth identity survives exec and includes the machine boot ID."""
    if sys.platform.startswith("linux"):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return None
        return _boot_id() + ":" + fields[19]
    if sys.platform == "darwin":
        import ctypes

        class RUsageV0(ctypes.Structure):
            # Public libproc.h/sys/resource.h rusage_info_v0 ABI.
            _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
                (name, ctypes.c_uint64) for name in
                ("user", "system", "idle", "interrupts", "pageins", "wired",
                 "resident", "footprint", "start", "exit")
            ]

        usage = RUsageV0()
        library = ctypes.CDLL("/usr/lib/libproc.dylib")
        library.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        library.proc_pid_rusage.restype = ctypes.c_int
        if library.proc_pid_rusage(pid, 0, ctypes.byref(usage)) != 0:
            return None
        return _boot_id() + ":" + str(usage.start)
    raise ValueError("precise process identity requires Linux or macOS")


def _validate_outcome(outcome: ProviderOutcome) -> None:
    if (not isinstance(outcome, ProviderOutcome) or type(outcome.success) is not bool
            or not isinstance(outcome.response, str)
            or (outcome.session_id is not None and not isinstance(outcome.session_id, str))
            or (outcome.model is not None and not isinstance(outcome.model, str))
            or not isinstance(outcome.usage, Mapping)
            or not isinstance(outcome.provider_result, Mapping)
            or (outcome.error is not None and not isinstance(outcome.error, Mapping))):
        raise ValueError("provider returned an invalid outcome")
    _json_text({"usage": dict(outcome.usage), "provider_result": dict(outcome.provider_result),
                "error": None if outcome.error is None else dict(outcome.error)})


def _recover_metadata(job: Job) -> dict:
    try:
        data = _read_json(job.metadata)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    try:
        request = _read_json(job.directory / "request.json")
    except (OSError, ValueError):
        request = {}
    stat_val = None
    try:
        stat_val = job.directory.stat()
    except OSError:
        pass
    created_at = stat_val.st_mtime if stat_val is not None else time.time()
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job.job_id,
        "provider": request.get("provider", "unknown"),
        "status": "ERROR",
        "cwd": request.get("cwd", str(job.directory.parent)),
        "model": request.get("model"),
        "effort": request.get("effort"),
        "session_id": request.get("session_id"),
        "created_at": created_at,
        "source_job_id": request.get("source_job_id"),
        "transcript_dir": str(job.directory),
        "permission_policy": PERMISSION_POLICY,
    }


LAUNCHCTL_PROBE_TIMEOUT = 1.0


class JobManager:
    """Durable job lifecycle; result.json is the terminal commit marker."""

    def __init__(self, store: JobStore | None = None, *, adapters: Mapping[str, ProviderAdapter] | None = None):
        self.store = JobStore() if store is None else store
        if adapters is None:
            from .providers import provider_adapters
            adapters = provider_adapters()
        self.adapters = adapters
        self.launch_grace = 1.0
        self._children: dict[str, subprocess.Popen] = {}

    def start(self, request: JobRequest) -> dict:
        request.__post_init__()
        adapter = self.adapters.get(request.provider)
        if adapter is None:
            raise ValueError(f"provider execution is not available: {request.provider}")
        adapter.validate(request)
        launcher = self._launcher()
        job = self.store.create(request)
        token = secrets.token_hex(32)
        atomic_write(job.directory / "worker.json", _json_text({"token": token, "pid": None, "pgid": None, "runner": str(RUNNER_PATH), "launcher": launcher}))
        for name in ("stdout.jsonl", "stderr.log", "manager.stdout.log", "manager.stderr.log"):
            atomic_write(job.directory / name, "")
        if launcher == "launchctl":
            atomic_write(job.directory / "launch-environment.json", _json_text(dict(os.environ)))
        try:
            self._launch(job, token)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.finalize(job.job_id, status="ERROR", exit_code=1,
                          error={"type": "launch_error", "message": _public_text(error, 200)})
        return self._summary(job)

    def followup(self, job_id: str, prompt: str) -> dict:
        self.status(job_id)
        job = self.store.get(job_id)
        result = self._result(job)
        if result is None:
            raise ValueError("followup requires a completed source job")
        session_id = result.get("session_id")
        _single_line(session_id, "session ID")
        if session_id is None:
            raise ValueError("source job has no resumable session ID")
        request = _read_json(job.directory / "request.json")
        options = dict(request.get("provider_options", {}))
        if request["provider"] == "agy":
            options.pop("new_project", None)
            options.pop("conversation", None)
        request.update(session_id=session_id, source_job_id=job_id, provider_options=options)
        return self.start(JobRequest(prompt=prompt, **request))

    @staticmethod
    def _launcher() -> str:
        launcher = os.environ.get("AGENT_JOB_LAUNCHER", "auto")
        if launcher == "auto":
            return "launchctl" if sys.platform == "darwin" else "detached"
        if launcher not in {"launchctl", "detached"}:
            raise ValueError("AGENT_JOB_LAUNCHER must be auto, launchctl, or detached")
        return launcher

    @staticmethod
    def _is_launchctl_active(job: Job, timeout: float = LAUNCHCTL_PROBE_TIMEOUT) -> bool | None:
        label = f"agent-job.{job.job_id}"
        launchctl_bin = os.environ.get("AGENT_JOB_LAUNCHCTL", "launchctl")
        try:
            res = subprocess.run([launchctl_bin, "list", label],
                                 capture_output=True, text=True, check=False,
                                 timeout=timeout)
            return True if res.returncode == 0 else False
        except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
            return None

    def _launch(self, job: Job, token: str) -> None:
        command = [sys.executable, str(RUNNER_PATH), "--_worker", str(self.store.root), job.job_id, token]
        stdout = job.directory / "manager.stdout.log"
        stderr = job.directory / "manager.stderr.log"
        with _append_stream(stdout) as out, _append_stream(stderr) as err:
            if self._launcher() == "launchctl":
                # launchd cannot enforce O_NOFOLLOW for output paths. The
                # token-validated worker opens and redirects its own logs.
                subprocess.run([os.environ.get("AGENT_JOB_LAUNCHCTL", "launchctl"), "submit", "-l", f"agent-job.{job.job_id}", "--", *command],
                                check=True, capture_output=True, text=True)
            else:
                child = subprocess.Popen(
                    command, cwd=job.directory, stdin=subprocess.DEVNULL,
                    stdout=out, stderr=err, start_new_session=True, close_fds=True,
                )
                self._children[job.job_id] = child
                threading.Thread(target=child.wait, daemon=True).start()

    def _reap(self, job_id: str, timeout: float = 0) -> None:
        child = self._children.get(job_id)
        if child is None:
            return
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return
        self._children.pop(job_id, None)

    @staticmethod
    def _process_argv(pid: int) -> list[str]:
        try:
            output = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="],
                                    capture_output=True, text=True, check=True).stdout.strip()
            # ps does not preserve quoting for paths containing spaces. The
            # internal worker has a fixed marker and two space-free final args.
            marker = f" {RUNNER_PATH} --_worker "
            executable, arguments = output.split(marker, 1)
            root, job_id, token = arguments.rsplit(" ", 2)
            return [executable, str(RUNNER_PATH), "--_worker", root, job_id, token]
        except (OSError, ValueError, subprocess.SubprocessError):
            return []

    def result(self, job: Job) -> dict | None:
        return self._result(job)

    def _owns_worker(self, job: Job, identity: dict) -> bool:
        pid, token = identity.get("pid"), identity.get("token")
        if type(pid) is not int or pid <= 0 or not isinstance(token, str):
            return False
        birth = identity.get("birth")
        if not isinstance(birth, str) or not birth.strip():
            return False
        current_birth = _process_birth(pid)
        if current_birth is None or current_birth != birth:
            return False
        command = self._process_argv(pid)
        expected = [str(RUNNER_PATH), "--_worker", str(self.store.root), job.job_id, token]
        executable = identity.get("executable")
        return (len(command) == 6 and command[1:] == expected and isinstance(executable, str)
                and Path(command[0]).resolve() == Path(executable).resolve())

    @staticmethod
    def _group_snapshot(pgid: int) -> dict[int, tuple[str, str]]:
        """Snapshot live group members by PID, birth time, and executable."""
        output = subprocess.run(["ps", "-axo", "pid=,pgid=,stat=,lstart=,comm="],
                                capture_output=True, text=True, check=True,
                                start_new_session=True).stdout
        members = {}
        for line in output.splitlines():
            fields = line.split(None, 8)
            if len(fields) != 9 or int(fields[1]) != pgid or fields[2].startswith("Z"):
                continue
            birth = _process_birth(int(fields[0]))
            if birth is not None:
                members[int(fields[0])] = (birth, fields[8])
        return members

    @staticmethod
    def _process_has_job_token(pid: int, token: str) -> bool:
        # Read environment fields separately from argv: text from ps eww merges
        # the two and would let an argument impersonate an inherited token.
        environment = Path(f"/proc/{pid}/environ")
        field = f"AGENT_JOB_WORKER_TOKEN={token}"
        if sys.platform.startswith("linux"):
            try:
                return field.encode() in environment.read_bytes().split(b"\0")
            except FileNotFoundError:
                return False
        if sys.platform != "darwin":
            return False
        import ctypes

        # Public sys/sysctl.h: CTL_KERN=1, KERN_PROCARGS2=49. Payload is argc,
        # executable path, padding, argc NUL-terminated arguments, environment.
        mib = (ctypes.c_int * 3)(1, 49, pid)
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        library.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
                                  ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                  ctypes.c_void_p, ctypes.c_size_t]
        library.sysctl.restype = ctypes.c_int
        size = ctypes.c_size_t()
        if library.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value < 4:
            return False
        buffer = ctypes.create_string_buffer(size.value)
        if library.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            return False
        data = buffer.raw[:size.value]
        argc = int.from_bytes(data[:4], sys.byteorder, signed=True)
        end = data.find(b"\0", 4)
        if argc <= 0 or argc > len(data) or end < 0:
            return False
        offset = end + 1
        while offset < len(data) and data[offset] == 0:
            offset += 1
        for _ in range(argc):
            end = data.find(b"\0", offset)
            if end < 0:
                return False
            offset = end + 1
        return field.encode() in data[offset:].split(b"\0")

    def _cleanup_lost_group(self, identity: dict) -> None:
        """Signal orphaned groups only with a live, job-bound ownership proof."""
        pid, pgid, token = identity.get("pid"), identity.get("provider_pgid"), identity.get("token")
        executable, command = identity.get("executable"), identity.get("provider_command")
        provider = identity.get("provider_process", {})
        if (type(pid) is not int or pid <= 0 or type(pgid) is not int or pgid <= 0
                or not isinstance(provider, dict) or provider.get("pid") != pgid
                or pgid == os.getpgrp() or not isinstance(token, str)
                or re.fullmatch(r"[0-9a-f]{64}", token) is None
                or identity.get("runner") != str(RUNNER_PATH)
                or not isinstance(executable, str) or not Path(executable).is_absolute()
                or not Path(executable).is_file() or not isinstance(command, list) or not command
                or any(not isinstance(value, str) or "\0" in value for value in command)):
            return
        provider_birth = provider.get("birth")
        if not isinstance(provider_birth, str) or not provider_birth.strip():
            return
        verified = self._group_snapshot(pgid)
        if not any(self._process_has_job_token(member, token) for member in verified):
            # A reused PGID with no inherited job context must never be signaled.
            return
        if pgid in verified and verified[pgid][0] != provider_birth:
            return

        def current_group():
            current = self._group_snapshot(pgid)
            owned = any(verified.get(member) == context or self._process_has_job_token(member, token)
                        for member, context in current.items())
            if owned:
                # Existing member continuity proves the group was not recycled.
                verified.update(current)
            return current, owned

        current, owned = current_group()
        if not current or not owned:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            current, owned = current_group()
            if not current or not owned:
                return
            time.sleep(0.1)
        current, owned = current_group()
        if not current or not owned:
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            current, owned = current_group()
            if not current or not owned:
                return
            time.sleep(0.05)
        raise ValueError("owned provider group remains alive after recovery escalation")

    def _result(self, job: Job) -> dict | None:
        path = job.result
        if not path.exists():
            return None
        try:
            value = _read_json(path)
            if (isinstance(value, dict) and value.get("status") in TERMINAL_STATES
                    and isinstance(value.get("exit_code"), int)):
                return value
            return None
        except (OSError, ValueError):
            return None

    def _recover_session(self, job: Job, *, scanner: _IncrementalSessionScanner | None = None,
                         _locked: bool = False) -> None:
        """Publish recognized partial sessions without validating terminal output."""
        try:
            metadata = _read_json(job.metadata)
        except (OSError, ValueError):
            return
        if metadata.get("session_id"):
            return
        adapter = self.adapters.get(metadata["provider"])
        extract = getattr(adapter, "extract_session_id", None)
        if extract is None:
            return
        try:
            session_id = scanner.scan() if scanner is not None else extract(job.directory / "stdout.jsonl")
            _single_line(session_id, "session ID")
        except (OSError, ValueError):
            # A writer may leave an incomplete object or UTF-8 sequence.
            return
        if session_id is None:
            return
        with nullcontext() if _locked else _job_lock(job):
            if self._result(job) is not None:
                return
            try:
                metadata = _read_json(job.metadata)
            except (OSError, ValueError):
                return
            if not metadata.get("session_id"):
                metadata["session_id"] = session_id
                atomic_write(job.metadata, _json_text(metadata))

    def _summary(self, job: Job) -> dict:
        terminal = self._result(job)
        try:
            source = terminal or _read_json(job.metadata)
        except (OSError, ValueError):
            source = _recover_metadata(job)
        summary = {key: source.get(key) for key in SUMMARY_FIELDS}
        try:
            state = source["status"] if terminal else job.state.read_text(encoding="utf-8").strip()
        except OSError:
            state = source.get("status", "ERROR")
        # result.json is the sole terminal commit marker; state is written first.
        summary["status"] = state if terminal or state not in TERMINAL_STATES else source["status"]
        if source.get("exit_code") is not None:
            summary["exit_code"] = source["exit_code"]
        return summary

    def status(self, job_id: str, probe_timeout: float = LAUNCHCTL_PROBE_TIMEOUT,
               deadline: float | None = None) -> dict:
        job = self.store.get(job_id)
        self._reap(job_id)
        if self._result(job) is None:
            if deadline is not None and time.monotonic() >= deadline:
                return self._summary(job)
            try:
                metadata = _read_json(job.metadata)
                corrupt_metadata = False
            except (OSError, ValueError):
                metadata = _recover_metadata(job)
                corrupt_metadata = True
            result_file = job.result
            truncated_result = result_file.exists() and self._result(job) is None
            try:
                identity = _read_json(job.worker)
            except (OSError, ValueError):
                identity = {}
            age = time.time() - metadata.get("created_at", 0)
            launcher = identity.get("launcher") or ("launchctl" if (job.directory / "launch-environment.json").exists() else "detached")
            is_launchctl_candidate = (
                launcher == "launchctl"
                and identity.get("pid") is None
                and not corrupt_metadata
                and not truncated_result
            )
            launchctl_active: bool | None = False
            if is_launchctl_candidate:
                if deadline is not None and time.monotonic() >= deadline:
                    return self._summary(job)
                p1_timeout = probe_timeout
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return self._summary(job)
                    p1_timeout = min(probe_timeout, max(0.001, remaining))
                try:
                    launchctl_active = self._is_launchctl_active(job, timeout=p1_timeout)
                except (OSError, subprocess.SubprocessError):
                    launchctl_active = None
            is_launchctl_active = is_launchctl_candidate and launchctl_active is not False
            effective_grace = 5.0 if is_launchctl_active and self.launch_grace == 1.0 else self.launch_grace
            worker_owned = not corrupt_metadata and not truncated_result and (self._owns_worker(job, identity) or (is_launchctl_active and age < effective_grace))
            if not worker_owned and (corrupt_metadata or truncated_result or age >= effective_grace):
                if deadline is not None and time.monotonic() >= deadline:
                    return self._summary(job)
                with _job_lock(job):
                    if deadline is not None and time.monotonic() >= deadline:
                        return self._summary(job)
                    if self._result(job) is None:
                        try:
                            identity = _read_json(job.worker)
                        except (OSError, ValueError):
                            identity = {}
                        rechecked_candidate = (
                            launcher == "launchctl"
                            and identity.get("pid") is None
                            and not corrupt_metadata
                            and not truncated_result
                        )
                        rechecked_active_status: bool | None = False
                        if rechecked_candidate:
                            if deadline is not None and time.monotonic() >= deadline:
                                return self._summary(job)
                            p2_timeout = probe_timeout
                            if deadline is not None:
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    return self._summary(job)
                                p2_timeout = min(probe_timeout, max(0.001, remaining))
                            try:
                                rechecked_active_status = self._is_launchctl_active(job, timeout=p2_timeout)
                            except (OSError, subprocess.SubprocessError):
                                rechecked_active_status = None
                        rechecked_active = rechecked_candidate and rechecked_active_status is not False
                        rechecked_grace = 5.0 if rechecked_active and self.launch_grace == 1.0 else self.launch_grace
                        if corrupt_metadata or truncated_result or not (self._owns_worker(job, identity) or (rechecked_active and age < rechecked_grace)):
                            if deadline is not None and time.monotonic() >= deadline:
                                return self._summary(job)
                            self._cleanup_lost_group(identity)
                            stopped = (job.directory / "stop_requested").exists()
                            if stopped:
                                status = "STOPPED"
                                exit_code = 130
                                err = None
                            else:
                                status = "ERROR"
                                exit_code = 1
                                if corrupt_metadata:
                                    err = {"type": "metadata_corrupt", "message": "metadata.json is corrupt or unreadable"}
                                elif truncated_result:
                                    err = {"type": "result_corrupt", "message": "result.json is corrupt or truncated"}
                                else:
                                    err = {"type": "worker_lost", "message": "worker is missing or no longer owned"}
                            self.finalize(job_id, status=status, exit_code=exit_code, error=err, _locked=True)
        return self._summary(job)

    def wait(self, job_id: str, timeout: float | None = None) -> dict:
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
                raise ValueError("timeout must be a finite nonnegative number")
        validate_job_id(job_id)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return {**self._summary(self.store.get(job_id)), "exit_code": 124}
            summary = self.status(job_id, deadline=deadline)
            result = self._result(self.store.get(job_id))
            if result is not None:
                self._reap(job_id, timeout=1)
                return result
            if deadline is not None and time.monotonic() >= deadline:
                return {**summary, "exit_code": 124}
            delay = 0.1 if deadline is None else min(0.1, max(0, deadline - time.monotonic()))
            time.sleep(delay)

    def read(self, job_id: str) -> str:
        self.status(job_id)
        result = self._result(self.store.get(job_id))
        if result is None:
            raise ValueError("job has not completed")
        return result["response"]

    def stop(self, job_id: str) -> dict:
        job = self.store.get(job_id)
        with _job_lock(job):
            result = self._result(job)
            if result is not None:
                _remove_launch_environment(job)
                self._reap(job_id, timeout=1)
                return result
            atomic_write(job.directory / "stop_requested", "STOPPED\n")
            atomic_write(job.state, "STOPPING\n")
            try:
                identity = _read_json(job.worker)
            except (OSError, ValueError):
                identity = {}
            self._cleanup_lost_group(identity)
            result = self.finalize(job_id, status="STOPPED", exit_code=130, _locked=True)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                current_identity = _read_json(job.worker)
            except (OSError, ValueError):
                current_identity = {}
            if not self._owns_worker(job, current_identity):
                break
            time.sleep(0.05)
        try:
            current_identity = _read_json(job.worker)
        except (OSError, ValueError):
            current_identity = {}
        if (self._owns_worker(job, current_identity)
                and current_identity.get("pid") == identity.get("pid")
                and current_identity.get("birth") == identity.get("birth")
                and current_identity.get("pgid") == identity.get("pgid")):
            try:
                os.kill(current_identity["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._reap(job_id, timeout=1)
        return result

    def list_jobs(self, cwd: Path | None = None) -> list[dict]:
        jobs = []
        for summary in self.store.list_summaries():
            if cwd is None or summary["cwd"] == str(Path(cwd).resolve()):
                jobs.append(self.status(summary["job_id"]))
        return jobs

    def finalize(self, job_id: str, *, status: str, exit_code: int, outcome: ProviderOutcome | None = None,
                 error: Mapping[str, object] | None = None, _locked: bool = False) -> dict:
        job = self.store.get(job_id)
        if status not in TERMINAL_STATES or type(exit_code) is not int:
            raise ValueError("invalid terminal status or exit code")
        if outcome is not None:
            _validate_outcome(outcome)
        with nullcontext() if _locked else _job_lock(job):
            result = self._result(job)
            if result is not None:
                _remove_launch_environment(job)
                return result
            lock = job.directory / ".finalize"
            _refuse_symlink(lock)
            # A live finalizer holds the directory flock. Any remaining empty
            # claim belongs to a process that crashed before result publication.
            if lock.exists():
                lock.rmdir()
            lock.mkdir(mode=0o700)
            lock.chmod(0o700)
            try:
                _remove_launch_environment(job)
                self._recover_session(job, _locked=True)
                try:
                    metadata = _read_json(job.metadata)
                except (OSError, ValueError):
                    metadata = _recover_metadata(job)
                if (job.directory / "stop_requested").exists():
                    status, exit_code, error = "STOPPED", 130, None
                elif status == "SUCCESS" and (exit_code != 0 or outcome is None or not outcome.success):
                    status, exit_code = "ERROR", exit_code or 1
                elif status != "SUCCESS":
                    exit_code = exit_code or 1
                result = {
                    **{key: metadata.get(key) for key in SUMMARY_FIELDS},
                    "status": status, "exit_code": exit_code,
                    "response": truncate_response(outcome.response if outcome else ""),
                    "session_id": outcome.session_id if outcome and outcome.session_id else metadata.get("session_id"),
                    "model": outcome.model if outcome and outcome.model else metadata.get("model"),
                    "usage": dict(outcome.usage) if outcome else {},
                    "error": dict(error) if error else (dict(outcome.error) if outcome and outcome.error else None),
                    "started_at": metadata.get("started_at"), "finished_at": time.time(),
                    "provider_result": {
                        k: (truncate_response(v) if k in ("response", "result") and isinstance(v, str) else v)
                        for k, v in (outcome.provider_result.items() if outcome else ())
                    },
                }
                atomic_write(job.directory / "response.txt", result["response"])
                atomic_write(job.directory / "exit_code", f"{exit_code}\n")
                atomic_write(job.state, status + "\n")
                atomic_write(job.result, _json_text(result))
                try:
                    _read_json(job.metadata)
                except (OSError, ValueError):
                    metadata_to_save = {key: result.get(key) for key in SUMMARY_FIELDS}
                    metadata_to_save["permission_policy"] = PERMISSION_POLICY
                    atomic_write(job.metadata, _json_text(metadata_to_save))
                return result
            finally:
                lock.rmdir()


def _worker_finalize(manager: JobManager, job: Job, *, detached: bool, status: str, exit_code: int,
                     outcome: ProviderOutcome | None = None, error: Mapping[str, object] | None = None) -> int:
    """Serialize the last stop check with publication, after draining children."""
    with _job_lock(job):
        try:
            identity = _read_json(job.worker)
        except (OSError, ValueError):
            identity = {}
        manager._cleanup_lost_group(identity)
        return manager.finalize(job.job_id, status=status, exit_code=exit_code,
                                outcome=outcome, error=error, _locked=True)["exit_code"]


def sentinel_main(root: Path, job_id: str, token: str) -> int:
    """Keep job ownership provable until its separate provider group drains."""
    manager = JobManager(JobStore(root))
    job = manager.store.get(job_id)
    identity = _read_json(job.directory / "worker.json")
    if (not secrets.compare_digest(token, identity.get("token", ""))
            or identity.get("provider_pgid") != os.getpgrp()):
        return 2
    terminating = [False]
    signal.signal(signal.SIGTERM, lambda signum, frame: terminating.__setitem__(0, True))
    while not (job.directory / "result.json").exists():
        orphaned = not manager._owns_worker(job, identity)
        if (terminating[0] or orphaned or (job.directory / "stop_requested").exists()) and not _group_has_members(os.getpgrp()):
            return 0
        time.sleep(0.05)
    return 0


def provider_main(root: Path, job_id: str, token: str) -> int:
    """Exec provider only after its own PID birth identity is durably recorded."""
    job = JobStore(root).get(job_id)
    identity = _read_json(job.directory / "worker.json")
    if not secrets.compare_digest(token, identity.get("token", "")):
        return 2
    gate = job.directory / "provider-start"
    deadline = time.monotonic() + 5.0
    while not gate.exists():
        if (job.directory / "stop_requested").exists() or (job.directory / "result.json").exists():
            return 130
        if time.monotonic() >= deadline:
            return 2
        time.sleep(0.01)
    with _job_lock(job):
        if (job.directory / "stop_requested").exists() or (job.directory / "result.json").exists():
            return 130
        _refuse_symlink(gate)
        identity = _read_json(job.directory / "worker.json")
        record = identity.get("provider_process", {})
        if (record.get("pid") != os.getpid() or record.get("birth") != _process_birth(os.getpid())
                or not secrets.compare_digest(gate.read_text().strip(), token)
                or not secrets.compare_digest(identity.get("token", ""), token)):
            return 2
        request = _read_json(job.directory / "request.json")
        os.chdir(_directory(Path(request["cwd"]), "cwd"))
        command = identity["provider_command"]
        sentinel = subprocess.Popen([sys.executable, str(RUNNER_PATH), "--_sentinel", str(root), job_id, token],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    close_fds=True)
        identity["sentinel_pid"] = sentinel.pid
        atomic_write(job.directory / "worker.json", _json_text(identity))
        atomic_write(job.directory / "provider-ready", token + "\n")
        os.execvpe(command[0], command, os.environ)


def worker_main(root: Path, job_id: str, token: str, *, adapters: Mapping[str, ProviderAdapter] | None = None) -> int:
    """Internal worker entry point; injected adapters support in-process tests."""
    manager = JobManager(JobStore(root), adapters=adapters)
    job = manager.store.get(job_id)
    identity = _read_json(job.directory / "worker.json")
    if not isinstance(token, str) or not secrets.compare_digest(token, identity.get("token", "")):
        return 2
    result = manager._result(job)
    if result is not None:
        return result["exit_code"]
    if (job.directory / "stop_requested").exists():
        return manager.finalize(job_id, status="STOPPED", exit_code=130)["exit_code"]
    detached = adapters is None
    previous_handler = None
    if detached:
        if os.getpgrp() != os.getpid():
            os.setsid()
        previous_handler = signal.signal(signal.SIGTERM, lambda signum, frame: None)
    try:
        if detached:
            with _append_stream(job.directory / "manager.stdout.log") as stdout, _append_stream(job.directory / "manager.stderr.log") as stderr:
                os.dup2(stdout.fileno(), 1)
                os.dup2(stderr.fileno(), 2)
        with _job_lock(job):
            result = manager._result(job)
            if result is not None:
                return result["exit_code"]
            _restore_launch_environment(job)
            if adapters is None:
                from .providers import provider_adapters
                manager.adapters = provider_adapters()
            command = manager._process_argv(os.getpid())
            birth = _process_birth(os.getpid())
            identity.update(pid=os.getpid(), pgid=os.getpgrp(),
                            birth=birth,
                            executable=command[0] if command else str(Path(sys.executable).resolve()))
            atomic_write(job.directory / "worker.json", _json_text(identity))
        metadata = _read_json(job.directory / "metadata.json")
        request_data = _read_json(job.directory / "request.json")
        request = JobRequest(prompt=(job.directory / "prompt.txt").read_bytes().decode("utf-8"), **request_data)
        adapter = manager.adapters[request.provider]
        adapter.validate(request)
        command = adapter.build_command(request)
        if not isinstance(command, list) or not command or any(not isinstance(value, str) or "\0" in value for value in command):
            raise ValueError("provider command must be a nonempty argument vector")
        with _append_stream(job.directory / "stdout.jsonl") as stdout, _append_stream(job.directory / "stderr.log") as stderr:
            with _job_lock(job):
                result = manager._result(job)
                if result is not None:
                    return result["exit_code"]
                stopping = (job.directory / "stop_requested").exists()
                if not stopping:
                    identity["provider_command"] = command
                    atomic_write(job.directory / "worker.json", _json_text(identity))
                    metadata.update(status="RUNNING", started_at=time.time())
                    gated = [sys.executable, str(RUNNER_PATH), "--_provider", str(manager.store.root), job_id, token]
                    process = subprocess.Popen(gated, cwd=request.cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                               env={**os.environ, "AGENT_JOB_WORKER_TOKEN": token}, close_fds=True,
                                               start_new_session=True)
                    spawn_birth = None
                    try:
                        spawn_birth = _process_birth(process.pid)
                        if spawn_birth is None or not isinstance(spawn_birth, str) or not spawn_birth.strip():
                            raise ValueError("provider exited before its identity was recorded")
                        identity["provider_process"] = {"pid": process.pid, "birth": spawn_birth,
                                                        "token": token, "executable": identity["executable"]}
                        identity["provider_pgid"] = process.pid
                        atomic_write(job.directory / "worker.json", _json_text(identity))
                        atomic_write(job.directory / "provider-start", token + "\n")
                    except BaseException:
                        if spawn_birth and isinstance(spawn_birth, str) and spawn_birth.strip():
                            try:
                                current_birth = _process_birth(process.pid)
                                current_pgid = os.getpgid(process.pid)
                                if current_birth == spawn_birth and current_pgid == process.pid:
                                    process.terminate()
                                    process.wait(timeout=1)
                            except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                                pass
                        raise
            if stopping:
                return _worker_finalize(manager, job, detached=detached, status="STOPPED", exit_code=130)
            deadline = time.monotonic() + 5.0
            while not (job.directory / "provider-ready").exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    raise ValueError("provider launch gate timed out")
                time.sleep(0.01)
            with _job_lock(job):
                if manager._result(job) is None and not (job.directory / "stop_requested").exists():
                    atomic_write(job.directory / "metadata.json", _json_text(metadata))
                    atomic_write(job.directory / "state", "RUNNING\n")
            scanner = _IncrementalSessionScanner(job.directory / "stdout.jsonl", request.provider)
            while True:
                manager._recover_session(job, scanner=scanner)
                try:
                    exit_code = process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
        if (job.directory / "stop_requested").exists():
            return _worker_finalize(manager, job, detached=detached, status="STOPPED", exit_code=130)
        try:
            outcome = adapter.parse(request, job.directory / "stdout.jsonl", job.directory / "stderr.log", exit_code)
            _validate_outcome(outcome)
        except Exception as error:
            return _worker_finalize(manager, job, detached=detached, status="ERROR", exit_code=exit_code or 1,
                                    error={"type": "parse_error", "message": _public_text(error, 200)})
        return _worker_finalize(manager, job, detached=detached, status="SUCCESS" if outcome.success and exit_code == 0 else "ERROR",
                                exit_code=exit_code, outcome=outcome)
    except Exception as error:
        return _worker_finalize(manager, job, detached=detached, status="ERROR", exit_code=1,
                                error={"type": "worker_error", "message": _public_text(error, 200)})
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)
