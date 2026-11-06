"""Antigravity (AGY) provider adapter."""

from __future__ import annotations

import json
from pathlib import Path

from .. import model as _model
from .. import process as _process
from . import base as _base


class AgyAdapter:
    """Antigravity JSON print mode with explicit workspace and resume options."""

    aliases = {"sonnet": "claude-sonnet-5-5-high", "flash": "gemini-3.8-flash-high"}
    terminal_fields = frozenset({"status", "response", "conversation_id", "usage", "error", "model"})

    def extract_session_id(self, raw_path: Path) -> str | None:
        return _process._partial_session(raw_path, "agy")

    def validate(self, request: _model.JobRequest) -> None:
        options = request.provider_options
        if set(options) - {"project", "new_project", "conversation"}:
            raise ValueError("unsupported AGY provider options")
        for key in ("project", "conversation"):
            _model._single_line(options.get(key), key)
        if "new_project" in options and type(options["new_project"]) is not bool:
            raise ValueError("new_project must be a boolean")
        conversation = options.get("conversation")
        if conversation and request.session_id and conversation != request.session_id:
            raise ValueError("--conversation and --session must identify the same conversation")
        if options.get("new_project") and (options.get("project") or conversation or request.session_id):
            raise ValueError("--new-project conflicts with --project and --conversation/--session")
        if request.name is not None:
            raise ValueError("AGY does not support --name")
        if request.effort is not None and request.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("unsupported AGY effort")

    def build_command(self, request: _model.JobRequest) -> list[str]:
        self.validate(request)
        model = self.aliases.get(request.model or "sonnet", request.model)
        command = ["agy", "--dangerously-skip-permissions", "--output-format", "json", "--model", model,
                   "--effort", request.effort or "high", "--add-dir", str(request.cwd)]
        for directory in request.add_dirs:
            command.extend(["--add-dir", str(directory)])
        project = request.provider_options.get("project")
        conversation = request.provider_options.get("conversation") or request.session_id
        if project:
            command.extend(["--project", project])
        elif not conversation:
            command.append("--new-project")
        if conversation:
            command.extend(["--conversation", conversation])
        command.extend(["--prompt", request.prompt])
        return command

    def parse(self, request_or_raw, raw_or_code=None, stderr_path=None, exit_code=0) -> _model.ProviderOutcome:
        request, raw_path, stderr_path, exit_code = _base._parse_args(request_or_raw, raw_or_code, stderr_path, exit_code, default_provider="agy")
        file_size = raw_path.stat().st_size if raw_path.exists() else 0
        tail_bound = 2 * _model.MAX_EVENT_BYTES
        if file_size <= tail_bound:
            raw = raw_path.read_text(encoding="utf-8", errors="replace")
        else:
            with raw_path.open("rb") as stream:
                stream.seek(file_size - tail_bound)
                chunk = stream.read(tail_bound)
                first_nl = chunk.find(b"\n")
                if first_nl >= 0:
                    chunk = chunk[first_nl + 1:]
                raw = chunk.decode("utf-8", errors="replace")
        decoder = json.JSONDecoder()
        terminal = None
        offset = 0
        # Decode from line starts so pretty objects work amid diagnostics too.
        # Advance over decoded objects rather than mistaking nested metadata for
        # a later terminal record.
        while offset < len(raw):
            start = offset
            while start < len(raw) and raw[start] in " \t\r\n":
                start += 1
            try:
                candidate, end = decoder.raw_decode(raw, start)
            except (ValueError, RecursionError):
                newline = raw.find("\n", start)
                if newline < 0:
                    break
                offset = newline + 1
                continue
            offset = end
            if isinstance(candidate, dict) and candidate.get("status") in ("SUCCESS", "ERROR"):
                terminal = candidate
        if terminal is None:
            return _model.ProviderOutcome(False, "", _process._partial_session(raw_path, "agy"), {},
                                          _base._no_result_failure("agy", raw_path, stderr_path, exit_code), None, {})
        response = terminal.get("response", "")
        if not isinstance(response, str):
            raise ValueError("AGY terminal response must be text")
        session_id = terminal.get("conversation_id")
        if session_id is not None:
            if not isinstance(session_id, str):
                raise ValueError("AGY conversation_id must be nonempty text without control characters")
            if not session_id.strip():
                session_id = None
            else:
                _model._single_line(session_id, "AGY conversation_id")
        has_nonempty_response = bool(response.strip())
        success = exit_code == 0 and terminal["status"] == "SUCCESS" and has_nonempty_response
        error = None
        if not success:
            if terminal["status"] == "SUCCESS" and not has_nonempty_response:
                error = {"type": "parse_error", "message": "AGY output contains no nonempty response"}
                terminal = {k: v for k, v in terminal.items() if k != "error"}
            else:
                provider_error = terminal.get("error")
                if isinstance(provider_error, dict):
                    error = _base._public_error(provider_error)
                    classified = _base._classify_provider_failure("agy", json.dumps(error), exit_code)
                    if classified["type"] in ("quota_error", "auth_error"):
                        error = {**error, "type": classified["type"], "message": error.get("message") or classified["message"]}
                    else:
                        error = {**error, "type": "provider_error"}
                elif terminal["status"] == "ERROR":
                    message = provider_error if isinstance(provider_error, str) else None
                    text_to_check = message or response or "AGY reported ERROR"
                    classified = _base._classify_provider_failure("agy", text_to_check, exit_code)
                    if classified["type"] in ("quota_error", "auth_error"):
                        error = {"type": classified["type"], "message": _process._public_text(text_to_check) or classified["message"]}
                    else:
                        error = {"type": "provider_error",
                                 "message": _process._public_text(text_to_check)}
                else:
                    error = {"type": "provider_exit", "message": f"AGY exited with status {exit_code}"}
        usage = terminal.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("AGY terminal usage must be an object")
        model = terminal.get("model")
        terminal = _base._public_terminal(terminal, self.terminal_fields, "agy")
        return _model.ProviderOutcome(success, response, session_id, _base._public_usage(usage, "agy"), error, model, terminal)


__all__ = ["AgyAdapter"]
