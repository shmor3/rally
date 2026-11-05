"""GitHub Copilot provider adapter."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re

from .. import model as _model
from .. import process as _process
from . import base as _base


class CopilotAdapter:
    """Copilot prompt-mode JSONL, retaining project instructions and full bypass."""

    terminal_fields = frozenset({"type", "timestamp", "sessionId", "exitCode", "usage", "error"})

    def extract_session_id(self, raw_path: Path) -> str | None:
        return _process._partial_session(raw_path, "copilot")

    def validate(self, request: _model.JobRequest) -> None:
        if set(request.provider_options) - {"max_credits"}:
            raise ValueError("unsupported Copilot provider options")
        if (request.model or "auto") == "auto" and request.effort is not None:
            raise ValueError("Copilot model auto does not support an explicit effort")
        if "max_credits" in request.provider_options:
            credits = request.provider_options["max_credits"]
            if type(credits) not in (str, int, float):
                raise ValueError("max credits must be a finite number of at least 30")
            if isinstance(credits, str):
                _model._single_line(credits, "max credits")
                if re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", credits.strip()) is None:
                    raise ValueError("max credits must be a finite number of at least 30")
            try:
                value = float(credits)
            except (ValueError, OverflowError) as error:
                raise ValueError("max credits must be a finite number of at least 30") from error
            if not math.isfinite(value) or value < 30:
                raise ValueError("max credits must be a finite number of at least 30")

    def build_command(self, request: _model.JobRequest) -> list[str]:
        self.validate(request)
        command = ["copilot", "--prompt", request.prompt, "--output-format", "json", "--stream", "off",
                   "--allow-all", "--no-ask-user", "--no-auto-update", "--no-remote-export",
                   "-C", str(request.cwd), "--model", request.model or "auto"]
        if request.effort is not None:
            command.extend(["--effort", request.effort])
        if request.name is not None:
            command.extend(["--name", request.name])
        if request.session_id is not None:
            command.append("--resume=" + request.session_id)
        for directory in request.add_dirs:
            command.extend(["--add-dir", str(directory)])
        if "max_credits" in request.provider_options:
            command.extend(["--max-ai-credits", str(request.provider_options["max_credits"])])
        return command

    def parse(self, request_or_raw, raw_or_code=None, stderr_path=None, exit_code=0) -> _model.ProviderOutcome:
        request, raw_path, stderr_path, exit_code = _base._parse_args(request_or_raw, raw_or_code, stderr_path, exit_code, default_provider="copilot")
        terminal, response, message_model, resolved_model, provider_error = None, "", None, None, None
        session_error = False
        for event in _base._jsonl_events(raw_path):
            kind = event.get("type")
            if kind == "result":
                terminal = event
            elif kind in ("assistant.message", "session.auto_mode_resolved", "session.error"):
                data = event.get("data")
                if not isinstance(data, dict):
                    raise ValueError(f"Copilot {kind} data must be an object")
                if kind == "assistant.message" and data.get("phase") == "final_answer":
                    response = data.get("content")
                    if not isinstance(response, str):
                        raise ValueError("Copilot final_answer content must be text")
                    message_model = data.get("model") or message_model
                elif kind == "session.auto_mode_resolved":
                    resolved_model = data.get("chosenModel") or resolved_model
                elif kind == "session.error":
                    session_error = True
                    provider_error = data
        if terminal is None:
            return _model.ProviderOutcome(False, "", _process._partial_session(raw_path, "copilot"), {},
                                          _base._no_result_failure("copilot", raw_path, stderr_path, exit_code), None, {})
        result_exit = terminal.get("exitCode")
        if type(result_exit) is not int:
            raise ValueError("Copilot result.exitCode must be an integer")
        session_id = terminal.get("sessionId")
        _model._single_line(session_id, "Copilot session ID")
        model = resolved_model or message_model
        _model._single_line(model, "Copilot model")
        usage = terminal.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("Copilot result usage must be an object")
        provider_error = terminal.get("error") or provider_error
        success = (exit_code == 0 and result_exit == 0 and bool(response.strip())
                   and not provider_error and not session_error)
        error = None
        if not success:
            classified = None
            if provider_error or session_error:
                err_text = json.dumps(provider_error) if isinstance(provider_error, dict) else str(provider_error or "")
                classified = _base._classify_provider_failure("copilot", err_text, exit_code)
            if classified and classified["type"] in ("quota_error", "auth_error"):
                message = (_base._public_error(provider_error).get("message") if isinstance(provider_error, dict)
                           else provider_error if isinstance(provider_error, str) else None)
                error = {"type": classified["type"], "message": _process._public_text(message) if message else classified["message"]}
                if isinstance(provider_error, dict):
                    err_dict = _base._public_error(provider_error)
                    for k in ("code", "status", "errorType"):
                        if k in err_dict:
                            error[k] = err_dict[k]
            elif exit_code != 0:
                error = {"type": "provider_exit", "message": f"Copilot exited with status {exit_code}"}
            else:
                message = (_base._public_error(provider_error).get("message") if isinstance(provider_error, dict)
                           else provider_error if isinstance(provider_error, str) else None)
                if not message and session_error:
                    message = "Copilot reported session.error"
                message = message or (f"Copilot result exited with status {result_exit}" if result_exit else
                                      "Copilot output contains no nonempty final_answer response")
                error = {"type": "provider_error", "message": _process._public_text(message)}
        terminal = _base._public_terminal(terminal, self.terminal_fields, "copilot")
        return _model.ProviderOutcome(success, response, session_id, _base._public_usage(usage, "copilot"), error, model, terminal)


__all__ = ["CopilotAdapter"]
