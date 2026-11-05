"""OpenAI Codex provider adapter."""

from __future__ import annotations

from pathlib import Path

from .. import model as _model
from .. import process as _process
from . import base as _base


class CodexAdapter:
    """Codex exec JSONL with explicit workspace and recorded-thread resume."""

    terminal_fields = frozenset({"type", "usage", "error", "message"})

    def extract_session_id(self, raw_path: Path) -> str | None:
        return _process._partial_session(raw_path, "codex")

    def validate(self, request: _model.JobRequest) -> None:
        if request.provider_options:
            raise ValueError("unsupported Codex provider options")
        if request.name is not None:
            raise ValueError("Codex does not support --name")

    def build_command(self, request: _model.JobRequest) -> list[str]:
        self.validate(request)
        if request.session_id is None:
            command = ["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
                       "--skip-git-repo-check", "-C", str(request.cwd)]
        else:
            # Resume lacks -C/--add-dir. The provider already runs in request.cwd,
            # retains session context, and bypasses the filesystem sandbox.
            command = ["codex", "exec", "resume", "--dangerously-bypass-approvals-and-sandbox",
                       "--json", "--all", "--skip-git-repo-check"]
        if request.model is not None:
            command.extend(["--model", request.model])
        if request.session_id is None:
            for directory in request.add_dirs:
                command.extend(["--add-dir", str(directory)])
        if request.effort is not None:
            command.extend(["-c", f'model_reasoning_effort="{request.effort}"'])
        command.append("--")
        if request.session_id is not None:
            command.append(request.session_id)
        command.append(request.prompt)
        return command

    def parse(self, request_or_raw, raw_or_code=None, stderr_path=None, exit_code=0) -> _model.ProviderOutcome:
        request, raw_path, stderr_path, exit_code = _base._parse_args(request_or_raw, raw_or_code, stderr_path, exit_code, default_provider="codex")
        session_id, response, terminal, usage = None, "", {}, {}
        completed, failed, failure_message = False, False, None
        for event in _base._jsonl_events(raw_path):
            kind = event.get("type")
            item_error = False
            if kind == "thread.started":
                session_id = event.get("thread_id")
                if session_id is None:
                    raise ValueError("Codex thread ID is required")
                _model._single_line(session_id, "Codex thread ID")
            elif kind == "turn.started":
                completed, response, terminal, usage = False, "", {}, {}
            elif kind == "item.completed":
                item = event.get("item")
                if not isinstance(item, dict):
                    raise ValueError("Codex item.completed item must be an object")
                if item.get("type") == "agent_message":
                    response = item.get("text")
                    if not isinstance(response, str):
                        raise ValueError("Codex agent_message text must be text")
                elif item.get("type") == "error":
                    item_error = True
            elif kind == "turn.completed":
                terminal, completed = event, True
                usage = event.get("usage", {})
                if not isinstance(usage, dict):
                    raise ValueError("Codex turn.completed usage must be an object")
            if (kind == "error" or (isinstance(kind, str) and kind.endswith((".failed", ".incomplete", ".error")))
                    or item_error or (kind == "turn.completed" and event.get("error") is not None)):
                failed = True
                payload = item if item_error else event
                detail = payload.get("error") or payload.get("message")
                message = (_base._public_error(detail).get("message") if isinstance(detail, dict)
                           else detail if isinstance(detail, str) else None)
                failure_message = message or failure_message or f"Codex reported {kind}"
                if isinstance(kind, str) and kind.startswith("turn."):
                    terminal = event
        success = exit_code == 0 and completed and bool(response.strip()) and not failed
        error = None
        if failed:
            classified = _base._classify_provider_failure("codex", failure_message or "", exit_code)
            if classified["type"] in ("quota_error", "auth_error"):
                error = {"type": classified["type"], "message": _process._public_text(failure_message) or classified["message"]}
            else:
                error = {"type": "provider_error", "message": _process._public_text(failure_message)}
        elif not completed:
            # No terminal turn: diagnostics may reveal quota/auth; a bare exit stays provider_exit.
            error = _base._no_result_failure("codex", raw_path, stderr_path, exit_code)
            if exit_code != 0 and error["type"] == "parse_error":
                error = {"type": "provider_exit", "message": f"Codex exited with status {exit_code}"}
        elif exit_code != 0:
            error = {"type": "provider_exit", "message": f"Codex exited with status {exit_code}"}
        elif not response.strip():
            error = {"type": "provider_error", "message": "Codex output contains no nonempty completed agent_message"}
        terminal = _base._public_terminal(terminal, self.terminal_fields, "codex")
        return _model.ProviderOutcome(success, response, session_id, _base._public_usage(usage, "codex"), error, None, terminal)


__all__ = ["CodexAdapter"]
