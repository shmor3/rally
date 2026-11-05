"""Anthropic Claude provider adapter."""

from __future__ import annotations

import math
from pathlib import Path

from .. import model as _model
from .. import process as _process
from . import base as _base


class ClaudeAdapter:
    """Claude print-mode JSONL; semantic API failures override process success."""

    result_subtypes = frozenset({"success", "error_during_execution", "error_max_turns",
                                "error_max_budget_usd", "error_max_structured_output_retries"})
    terminal_fields = frozenset({
        "type", "subtype", "session_id", "result", "is_error", "terminal_reason", "model",
        "usage", "modelUsage", "total_cost_usd", "duration_ms", "duration_api_ms", "num_turns",
        "api_error_status", "api_error_code", "api_error_origin", "is_api_error_message",
        "stop_reason", "permission_denials", "fast_mode_state", "fast_mode_disabled_reason",
        "subagent_stats", "safety_stops", "uuid", "queued_turn_count", "result_index",
        "model_usage_since_last_result", "error", "errors",
    })

    def extract_session_id(self, raw_path: Path) -> str | None:
        return _process._partial_session(raw_path, "claude")

    def validate(self, request: _model.JobRequest) -> None:
        if request.provider_options:
            raise ValueError("unsupported Claude provider options")
        if request.effort is not None and request.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("unsupported Claude effort")

    def build_command(self, request: _model.JobRequest) -> list[str]:
        self.validate(request)
        command = ["claude", "--print", "--output-format", "stream-json", "--verbose",
                   "--dangerously-skip-permissions", "--permission-prompts", "none", "--no-chrome"]
        for flag in ("model", "effort", "name"):
            value = getattr(request, flag)
            if value is not None:
                command.extend(["--" + flag, value])
        for directory in request.add_dirs:
            command.extend(["--add-dir", str(directory)])
        if request.session_id is not None:
            command.extend(["--resume", request.session_id])
        command.extend(["--", request.prompt])
        return command

    @staticmethod
    def _validate_event(event: dict) -> None:
        """Validate consumed fields only on recognized protocol events."""
        for key in ("session_id", "model", "api_error_code", "api_error_origin", "terminal_reason"):
            if key in event:
                _model._single_line(event[key], "Claude " + key)
                if event[key] is None:
                    raise ValueError(f"Claude {key} must be nonempty text")
        for key in ("is_error", "is_api_error_message"):
            if key in event and type(event[key]) is not bool:
                raise ValueError(f"Claude {key} must be a boolean")
        for key in ("api_error_status", "status"):
            if key in event and event[key] is not None and (type(event[key]) is not int or not 100 <= event[key] <= 599):
                raise ValueError(f"Claude {key} must be an HTTP status integer")
        if "result" in event and not isinstance(event["result"], str):
            raise ValueError("Claude result must be text")
        error_event = event.get("type") in ("error", "api_error") or (event.get("type") == "system" and event.get("subtype") == "error")
        if error_event and "message" in event and not isinstance(event["message"], str):
            raise ValueError("Claude error message must be text")
        for key in ("usage", "modelUsage"):
            if key in event and not isinstance(event[key], dict):
                raise ValueError(f"Claude {key} must be an object")
        if "total_cost_usd" in event:
            cost = event["total_cost_usd"]
            if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                raise ValueError("Claude total_cost_usd must be a finite nonnegative number")
        if "error" in event and event["error"] is not None and not isinstance(event["error"], (str, dict)):
            raise ValueError("Claude error must be text or an object")
        if isinstance(event.get("error"), dict):
            detail = event["error"]
            if "message" in detail and not isinstance(detail["message"], str):
                raise ValueError("Claude error.message must be text")
            for key in ("type", "code"):
                if key in detail:
                    _model._single_line(detail[key], "Claude error." + key)
                    if detail[key] is None:
                        raise ValueError(f"Claude error.{key} must be nonempty text")
            if "status" in detail and detail["status"] is not None and (type(detail["status"]) is not int or not 100 <= detail["status"] <= 599):
                raise ValueError("Claude error.status must be an HTTP status integer")
        if "errors" in event and (not isinstance(event["errors"], list)
                                  or any(not isinstance(item, str) for item in event["errors"])):
            raise ValueError("Claude errors must be a list of text")

    @staticmethod
    def _has_error(event: dict) -> bool:
        return ("error" in event or "errors" in event or event.get("is_error") is True
                or event.get("is_api_error_message") is True or "api_error_code" in event
                or event.get("api_error_status") is not None or event.get("terminal_reason") == "api_error")

    @staticmethod
    def _error_detail(event: dict, assistant_text: str, *, assistant_context: bool) -> dict:
        payload = event.get("error")
        detail = payload if isinstance(payload, dict) else {}
        message = (detail.get("message") or "; ".join(event.get("errors", []))
                   or (event.get("message") if isinstance(event.get("message"), str) else None)
                   or (payload if isinstance(payload, str) and not assistant_context else None)
                   or event.get("result") or assistant_text
                   # Assistant error strings can be aliases; readable API text is preferable.
                   or (payload if isinstance(payload, str) else None)
                   or "Claude reported a provider error")
        # Retain concise, single-line public details, never arbitrary error payloads.
        error = {"type": "provider_error", "message": _process._public_text(message)}
        code = event.get("api_error_code") or detail.get("code") or detail.get("type")
        status = event.get("api_error_status") or detail.get("status") or event.get("status")
        if isinstance(code, str) and code.strip():
            error["code"] = _process._public_text(code, 100)
        if type(status) is int:
            error["status"] = status
        code_str = str(code).lower() if code else ""
        msg_str = str(message).lower() if message else ""
        if status == 429 or "rate_limit" in code_str or "quota" in msg_str or "usage limit" in msg_str:
            error["type"] = "quota_error"
        elif status in (401, 403) or any(k in code_str or k in msg_str for k in ("unauthorized", "oauth", "authentication", "api_key", "forbidden")):
            error["type"] = "auth_error"
        return error

    def parse(self, request_or_raw, raw_or_code=None, stderr_path=None, exit_code=0) -> _model.ProviderOutcome:
        request, raw_path, stderr_path, exit_code = _base._parse_args(request_or_raw, raw_or_code, stderr_path, exit_code, default_provider="claude")
        terminal, init_session, model, provider_error = None, None, None, None
        for event in _base._jsonl_events(raw_path):
            kind = event.get("type")
            is_init = kind == "system" and event.get("subtype") == "init"
            error_event = kind in ("error", "api_error") or (kind == "system" and event.get("subtype") == "error")
            if kind not in ("assistant", "result") and not is_init and not error_event:
                continue
            self._validate_event(event)
            assistant_text = ""
            nested_error = None
            if is_init:
                init_session = event.get("session_id")
                if init_session is None:
                    raise ValueError("Claude init session_id is required")
                model = event.get("model") or model
            elif kind == "assistant":
                message = event.get("message")
                if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                    raise ValueError("Claude assistant message/content must be an object/list")
                self._validate_event(message)
                for block in message["content"]:
                    if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                        raise ValueError("Claude assistant content blocks must be typed objects")
                    if block["type"] == "text":
                        if not isinstance(block.get("text"), str):
                            raise ValueError("Claude assistant text must be text")
                        assistant_text += block["text"]
                if message.get("model") and message["model"] != "<synthetic>":
                    model = message["model"]
                if self._has_error(message):
                    nested_error = self._error_detail(message, assistant_text, assistant_context=True)
            elif kind == "result":
                subtype = event.get("subtype")
                if not isinstance(subtype, str) or subtype not in self.result_subtypes:
                    raise ValueError("Claude result subtype is not recognized")
                if type(event.get("is_error")) is not bool:
                    raise ValueError("Claude result is_error must be a boolean")
                terminal = _base._public_terminal(event, self.terminal_fields, "claude")
            failed = (error_event or self._has_error(event) or nested_error is not None
                      or (kind == "result" and event["subtype"] != "success"))
            if failed:
                detail = (self._error_detail(event, assistant_text, assistant_context=kind == "assistant")
                          if error_event or self._has_error(event) else nested_error)
                if detail is None:
                    detail = self._error_detail(event, assistant_text, assistant_context=kind == "assistant")
                # Empty trailing errors cannot erase the provider's useful API message.
                if provider_error and detail["message"] == "Claude reported a provider error":
                    detail["message"] = provider_error["message"]
                provider_error = {**(provider_error or {}), **detail}
        if terminal is None and provider_error is None:
            # Diagnostics may classify quota/auth before falling back to exit status or parse error.
            error = _base._no_result_failure("claude", raw_path, stderr_path, exit_code)
            if exit_code != 0 and error["type"] == "parse_error":
                error = {"type": "provider_exit", "message": f"Claude exited with status {exit_code}"}
            session_id = _process._partial_session(raw_path, "claude") or init_session
            return _model.ProviderOutcome(False, "", session_id, {}, error, model, {})
        response = terminal.get("result", "") if terminal else ""
        session_id = terminal.get("session_id", init_session) if terminal else init_session
        usage = terminal.get("usage", {}) if terminal else {}
        if terminal and terminal.get("model"):
            model = terminal["model"]
        success = exit_code == 0 and terminal is not None and bool(response.strip()) and provider_error is None
        error = provider_error
        if error is None and exit_code != 0:
            error = {"type": "provider_exit", "message": f"Claude exited with status {exit_code}"}
        elif error is None and terminal is None:
            error = {"type": "provider_error", "message": "Claude output contains no recognized terminal result"}
        elif error is None and not response.strip():
            error = {"type": "provider_error", "message": "Claude result contains no nonempty response"}
        return _model.ProviderOutcome(success, response, session_id, usage, error, model, terminal or {})


__all__ = ["ClaudeAdapter"]
