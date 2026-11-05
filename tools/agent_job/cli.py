"""Command-line interface and dispatch for agent jobs."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from typing import Mapping

from .model import (
    DOCTOR_OUTPUT_LIMIT,
    DOCTOR_TIMEOUT_SECONDS,
    EFFORT_LEVELS,
    HELP_TEXT,
    PERMISSION_POLICY,
    PROVIDERS,
    RUNNER_PATH,
    SCHEMA_VERSION,
    SUMMARY_FIELDS,
    JobRequest,
    ProviderAdapter,
    ProviderOutcome,
    _directory,
    _single_line,
    validate_job_id,
)
from .store import (
    Job,
    JobStore,
    _read_json,
    _refuse_symlink,
)
from .process import (
    JobManager,
    _IncrementalSessionScanner,
    _partial_session,
    _process_birth,
    _public_text,
    provider_main,
    sentinel_main,
    worker_main,
)
from .providers import (
    ProviderAdapter,
    _FixtureAdapter,
    AgyAdapter,
    CopilotAdapter,
    CodexAdapter,
    ClaudeAdapter,
    provider_adapters,
    _classify_provider_failure,
    _diagnostic_text,
    _jsonl_events,
    _metric_number,
    _no_result_failure,
    _public_error,
    _public_metrics,
    _public_terminal,
    _public_usage,
)


def _bounded_probe(command: list[str]) -> tuple[bytes, int | None, str | None]:
    """Capture at most 4 KiB within a deadline; never expose provider diagnostics."""
    process = None
    spawn_birth = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        spawn_birth = _process_birth(process.pid)
        deadline = time.monotonic() + DOCTOR_TIMEOUT_SECONDS
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return b"", None, "timed out"
                chunk = os.read(process.stdout.fileno(), DOCTOR_OUTPUT_LIMIT + 1 - len(output))
                if not chunk:
                    selector.unregister(process.stdout)
                    break
                output.extend(chunk)
                if len(output) > DOCTOR_OUTPUT_LIMIT:
                    return b"", None, "output limit exceeded"
        try:
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return b"", None, "timed out"
        return bytes(output), code, None
    except OSError:
        return b"", None, "could not execute"
    finally:
        if process is not None:
            # Before reaping an unfinished probe, its session/group is still owned.
            if process.returncode is None:
                if spawn_birth and isinstance(spawn_birth, str) and spawn_birth.strip():
                    try:
                        current_birth = _process_birth(process.pid)
                        current_pgid = os.getpgid(process.pid)
                        if current_birth == spawn_birth and current_pgid == process.pid:
                            os.killpg(process.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
            try:
                process.wait(timeout=0.1)
            except (subprocess.TimeoutExpired, OSError):
                pass
            if process.stdout:
                process.stdout.close()


def doctor_provider(provider: str) -> dict[str, object]:
    """Local readiness is deliberately separate from live provider acceptance."""
    executable = shutil.which(provider)
    result = {"provider": provider, "status": "MISSING", "executable": executable,
              "version": None, "auth": "NOT_CHECKED", "service_access": "UNVERIFIED",
              "reason": "executable not found on PATH"}
    if executable is None:
        return result
    output, code, failure = _bounded_probe([executable, "--version"])
    result["status"] = "UNAVAILABLE"
    if failure or code != 0:
        result["reason"] = "version probe " + (failure or "failed")
        return result
    patterns = {"agy": r"([0-9]+\.[0-9]+\.[0-9]+)",
                "copilot": r"GitHub Copilot CLI ([0-9]+\.[0-9]+\.[0-9]+)\.?",
                "codex": r"codex-cli ([0-9]+\.[0-9]+\.[0-9]+)",
                "claude": r"([0-9]+\.[0-9]+\.[0-9]+) \(Claude Code\)"}
    version = re.search(r"(?m)^" + patterns[provider] + r"\r?$", output.decode("utf-8", errors="replace"))
    if version is None:
        result["reason"] = "version output not recognized"
        return result
    result["version"] = version.group(1)
    if provider in {"claude", "codex"}:
        auth_command = ["auth", "status", "--json"] if provider == "claude" else ["login", "status"]
        output, code, failure = _bounded_probe([executable, *auth_command])
        signed_in = False
        if failure is None and code == 0:
            if provider == "claude":
                try:
                    auth = json.loads(output)
                    signed_in = isinstance(auth, dict) and auth.get("loggedIn") is True
                except (ValueError, RecursionError):
                    pass
            else:
                # Exit zero is the CLI's login-status contract; never echo its text.
                signed_in = True
        result["auth"] = "SIGNED_IN" if signed_in else "UNAVAILABLE"
        if not signed_in:
            result["reason"] = "local authentication probe " + (failure or "unavailable")
            return result
    result["status"] = "READY"
    result["reason"] = "local CLI ready; service access unverified"
    return result


def _doctor_main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-job doctor", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Report local provider readiness as JSON: READY, MISSING, or UNAVAILABLE.",
        epilog="""READY checks executable/version and local auth status for Codex/Claude only.
AGY/Copilot auth is NOT_CHECKED. Probes have a 3-second/4096-byte limit each.
Doctor sends no prompt or API request. Service access, quota, billing, and OAuth
organization policy remain UNVERIFIED; a local login can coexist with a live
service rejection. Live job results are authoritative. No credentials, identities,
environment values, or raw probe output are printed. No job files are created.
Exit 0 means the report was produced; inspect each provider's status.
""")
    parser.add_argument("providers", nargs="*", metavar="PROVIDER", help="agy, copilot, codex, claude (default: all)")
    try:
        args = parser.parse_args(arguments)
        if any(provider not in PROVIDERS for provider in args.providers):
            parser.error("invalid provider; choose agy, copilot, codex, or claude")
        providers = list(dict.fromkeys(args.providers or PROVIDERS))
        print(json.dumps({"schema_version": SCHEMA_VERSION,
                          "providers": [doctor_provider(provider) for provider in providers]}))
        return 0
    except SystemExit as error:
        return int(error.code)


def _management_main(arguments: list[str], *, legacy_output: bool = False) -> int:
    parser = argparse.ArgumentParser(prog="agent-job", description="Manage durable agent jobs. " + PERMISSION_POLICY,
                                     epilog=HELP_TEXT, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "wait", "read", "stop"):
        command = commands.add_parser(name)
        command.add_argument("job_id")
        if name == "wait":
            command.add_argument("--timeout", type=float)
    listing = commands.add_parser("list")
    listing.add_argument("--cwd", type=Path, default=Path.cwd())
    followup = commands.add_parser("followup")
    followup.add_argument("job_id")
    prompt = followup.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    try:
        args = parser.parse_args(arguments)
        if args.command == "list":
            cwd = _directory(args.cwd, "cwd")
            jobs = JobManager(JobStore(cwd=cwd)).list_jobs(cwd=None if legacy_output else cwd)
            print(json.dumps({"jobs": jobs} if legacy_output else jobs, ensure_ascii=False))
            return 0
        manager = JobManager()
        if args.command == "followup":
            text = args.prompt if args.prompt is not None else args.prompt_file.read_bytes().decode("utf-8")
            result = manager.followup(args.job_id, text)
        elif args.command == "read":
            sys.stdout.write(manager.read(args.job_id))
            result = manager._result(manager.store.get(args.job_id))
        elif args.command == "wait":
            timeout = None if legacy_output and args.timeout == 0 else args.timeout
            result = manager.wait(args.job_id, timeout=timeout)
        else:
            result = getattr(manager, args.command)(args.job_id)
        if args.command != "read":
            output = {**result, "agy": result["provider_result"]} if legacy_output and "provider_result" in result else result
            print(json.dumps(output, ensure_ascii=False))
        if legacy_output and args.command in {"status", "read", "stop"}:
            return 0
        code = result.get("exit_code", 0)
        return code if 0 <= code <= 255 else 1
    except SystemExit as error:
        return int(error.code)
    except (OSError, ValueError) as error:
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Internal compatibility mode is intentionally separate from provider options.
    legacy_output = bool(arguments and arguments[0] == "--_legacy-output")
    if legacy_output:
        arguments.pop(0)
    if arguments and arguments[0] == "doctor":
        return _doctor_main(arguments[1:])
    if arguments and arguments[0] in {"status", "wait", "read", "stop", "list", "followup"}:
        return _management_main(arguments, legacy_output=legacy_output)
    if arguments and arguments[0] in {"--_worker", "--_provider", "--_sentinel"}:
        if len(arguments) != 4:
            return 2
        try:
            entry = {"--_worker": worker_main, "--_provider": provider_main, "--_sentinel": sentinel_main}[arguments[0]]
            return entry(Path(arguments[1]), arguments[2], arguments[3])
        except (OSError, ValueError):
            return 2
    parser = argparse.ArgumentParser(prog="agent-job", description="Portable agent job runner. " + PERMISSION_POLICY,
                                     epilog=HELP_TEXT, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("provider", choices=PROVIDERS)
    parser.add_argument("action", nargs="?", choices=("run", "start"), default="run")
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=sorted(EFFORT_LEVELS))
    parser.add_argument("--session", dest="session_id")
    parser.add_argument("--name")
    parser.add_argument("--max-credits")
    parser.add_argument("--add-dir", action="append", type=Path, default=[])
    parser.add_argument("--project")
    parser.add_argument("--new-project", action="store_true")
    parser.add_argument("--conversation")
    try:
        args = parser.parse_args(arguments)
        text = args.prompt if args.prompt is not None else args.prompt_file.read_bytes().decode("utf-8")
        if legacy_output and not text:
            raise ValueError("a nonempty --prompt or --prompt-file is required")
        options = {}
        if args.project is not None:
            options["project"] = args.project
        if args.new_project:
            options["new_project"] = True
        if args.conversation is not None:
            options["conversation"] = args.conversation
        if args.provider != "agy" and options:
            raise ValueError("project/conversation options require AGY")
        if args.max_credits is not None:
            if args.provider != "copilot":
                raise ValueError("--max-credits requires Copilot")
            options["max_credits"] = args.max_credits
        model, effort = args.model, args.effort
        if args.provider == "agy":
            model = AgyAdapter.aliases.get(model or "sonnet", model)
            effort = effort or "high"
        elif args.provider == "copilot":
            model = model or "auto"
        request = JobRequest(provider=args.provider, prompt=text, cwd=args.cwd, model=model,
                             effort=effort, session_id=args.session_id, name=args.name,
                             add_dirs=tuple(args.add_dir), provider_options=options)
        manager = JobManager(JobStore(cwd=request.cwd))
        descriptor = manager.start(request)
        if args.action == "start":
            print(json.dumps(descriptor, ensure_ascii=False))
            code = descriptor.get("exit_code", 0)
            return code if 0 <= code <= 255 else 1
        result = manager.wait(descriptor["job_id"])
        if legacy_output:
            directory = manager.store.get(descriptor["job_id"]).directory
            sys.stderr.buffer.write((directory / "stderr.log").read_bytes())
            sys.stdout.buffer.write((directory / "stdout.jsonl").read_bytes())
        else:
            print(json.dumps(result, ensure_ascii=False))
        code = result["exit_code"]
        return code if 0 <= code <= 255 else 1
    except SystemExit as error:
        return int(error.code)
    except (OSError, ValueError) as error:
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return 2
