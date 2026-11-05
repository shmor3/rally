"""Shared protocol, parsing, and sanitization helpers for provider adapters."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
from typing import Mapping

from .. import model as _model
from .. import store as _store
from .. import process as _process

ProviderAdapter = _model.ProviderAdapter
JobRequest = _model.JobRequest
ProviderOutcome = _model.ProviderOutcome


def _public_error(value: Mapping) -> dict:
    """Only scalar public error details may leave the raw transcript."""
    return {key: (_process._public_text(item, 100 if key in {"type", "code"} else 300) if isinstance(item, str) else item)
            for key, item in value.items()
            if (key in {"type", "code", "message"} and isinstance(item, str))
            or (key == "status" and type(item) is int and 100 <= item <= 599)}


def _classify_provider_failure(provider: str, text: str, exit_code: int) -> dict:
    """Stable public failure from diagnostics; the diagnostic text itself is never published."""
    lowered = text.lower()
    if any(k in lowered for k in ("quota", "usage limit", "rate limit", "credits", "resource exhausted", "too many requests")):
        return {"type": "quota_error", "message": f"{provider} quota unavailable"}
    if any(k in lowered for k in ("oauth", "not logged in", "unauthorized", "authentication", "api key", "unauthenticated", "login required")):
        return {"type": "auth_error", "message": f"{provider} authentication unavailable"}
    if exit_code:
        return {"type": "provider_exit", "message": f"{provider} exited with status {exit_code}"}
    return {"type": "parse_error", "message": f"{provider} output contains no result event"}


def _bounded_tail_text(path: Path | None, limit: int = 16384) -> str:
    """Read a bounded tail of bytes from path; return sanitized UTF-8 text."""
    if path is None:
        return ""
    try:
        if not path.exists():
            return ""
        _store._refuse_symlink(path)
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - limit))
            return stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _diagnostic_text(raw_path: Path | None, limit: int = 16384) -> str:
    """Bounded tail of non-JSON stdout lines; JSON events and model text are not diagnostics."""
    tail = _bounded_tail_text(raw_path, limit)
    lines = []
    for line in tail.splitlines():
        line = line.strip()
        if line and line[0] not in '{}["]':
            lines.append(line)
    return "\n".join(lines)


def _stderr_diagnostics(stderr_path: Path | None, limit: int = 16384) -> str:
    """Bounded tail of stderr lines; used only for classification, never published."""
    tail = _bounded_tail_text(stderr_path, limit)
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    return "\n".join(lines)


def _no_result_failure(provider: str, raw_path: Path, stderr_path: Path | None = None, exit_code: int = 0) -> dict:
    """Classify a missing result; exit status alone never explains why output was absent."""
    stdout_diag = _diagnostic_text(raw_path)
    stderr_diag = _stderr_diagnostics(stderr_path)
    combined = f"{stdout_diag}\n{stderr_diag}".strip()
    return _classify_provider_failure(provider, combined, exit_code)


def _parse_args(request_or_raw, raw_or_code, stderr_path=None, exit_code=0, default_provider="agy"):
    """Accept both standard parse(request, raw_path, stderr_path, exit_code) and legacy 2-arg forms."""
    if isinstance(request_or_raw, _model.JobRequest):
        return request_or_raw, raw_or_code, stderr_path, exit_code
    raw_path = request_or_raw
    code = raw_or_code
    req = _model.JobRequest(provider=default_provider, prompt="", cwd=raw_path.parent)
    return req, raw_path, None, code


def _jsonl_events(raw_path: Path, max_line_bytes: int = _model.MAX_EVENT_BYTES):
    """Yield complete JSON objects line by line; diagnostics, oversized lines, and damaged tails are skipped."""
    _store._refuse_symlink(raw_path)
    with raw_path.open("rb") as stream:
        while True:
            raw_line = stream.readline(max_line_bytes + 1)
            if not raw_line:
                break
            if len(raw_line) > max_line_bytes and not raw_line.endswith(b"\n"):
                while True:
                    discard = stream.readline(max_line_bytes)
                    if not discard or discard.endswith(b"\n"):
                        break
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(event, dict):
                yield event


def _metric_number(value: object) -> bool:
    """Public counts/costs are finite, nonnegative, and at most 1024 bits."""
    if type(value) is int:
        return value >= 0 and value.bit_length() <= 1024
    return type(value) is float and math.isfinite(value) and value >= 0


def _public_metrics(value: Mapping, fields: set, nested: Mapping[str, set] | None = None,
                    depth: int = 0) -> dict:
    """Keep only known numeric metrics; bound map breadth and nesting."""
    if not isinstance(value, dict) or depth > 3 or len(value) > 64:
        return {}
    nested = nested or {}
    result = {}
    for key in fields:
        if key not in value:
            continue
        item = value[key]
        if key in nested and isinstance(item, dict) and depth < 3:
            child = _public_metrics(item, nested[key], depth=depth + 1)
            if child or not item:
                result[key] = child
        elif key not in nested and _metric_number(item):
            result[key] = item
    return result


def _public_usage(value: Mapping, provider: str) -> dict:
    fields = {
        "agy": {"tokens", "input_tokens", "output_tokens", "total_tokens", "prompt_tokens",
                "completion_tokens", "cached_input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens", "cost", "total_cost_usd"},
        "copilot": {"premiumRequests", "totalApiDurationMs", "sessionDurationMs", "codeChanges"},
        "codex": {"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                  "reasoning_output_tokens"},
        "claude": {"input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens",
                    "output_tokens_details", "server_tool_use", "cache_creation", "iterations"},
    }[provider]
    nested_fields = {
        "codeChanges": {"linesAdded", "linesRemoved", "filesModified"},
        "output_tokens_details": {"thinking_tokens"},
        "server_tool_use": {"web_search_requests", "web_fetch_requests"},
        "cache_creation": {"ephemeral_1h_input_tokens", "ephemeral_5m_input_tokens"},
    }
    return _public_metrics(value, fields, nested_fields)


def _public_terminal(value: dict, fields: frozenset, provider: str) -> dict:
    # Scalar fields must never become a vehicle for arbitrary nested payloads.
    structured = {"usage": dict, "error": (str, dict, type(None))}
    if provider == "claude":
        structured.update({"modelUsage": dict, "model_usage_since_last_result": dict,
                           "subagent_stats": dict, "permission_denials": list, "errors": list})
    terminal = {key: item for key, item in value.items() if key in fields
                and (isinstance(item, structured[key]) if key in structured
                     else not isinstance(item, (dict, list)))}
    for key in ("total_cost_usd", "duration_ms", "duration_api_ms", "num_turns",
                "queued_turn_count", "result_index", "safety_stops"):
        if key in terminal and not _metric_number(terminal[key]):
            del terminal[key]
    if isinstance(terminal.get("error"), dict):
        terminal["error"] = _public_error(terminal["error"])
    for key in ("response", "result"):
        if isinstance(terminal.get(key), str):
            terminal[key] = _model.truncate_response(terminal[key])
    for key in ("error", "message"):
        if isinstance(terminal.get(key), str):
            terminal[key] = _process._public_text(terminal[key])
    if isinstance(terminal.get("errors"), list):
        terminal["errors"] = [_process._public_text(item) for item in terminal["errors"] if isinstance(item, str)]
    if isinstance(terminal.get("usage"), dict):
        terminal["usage"] = _public_usage(terminal["usage"], provider)
    if provider == "claude":
        metrics = {"inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens",
                   "webSearchRequests", "costUSD", "contextWindow", "maxOutputTokens"}
        for key in ("modelUsage", "model_usage_since_last_result"):
            if isinstance(terminal.get(key), dict):
                models = terminal[key]
                terminal[key] = ({model: _public_metrics(usage, metrics)
                                  for model, usage in models.items()
                                  if isinstance(model, str) and len(model) <= 128 and isinstance(usage, dict)}
                                 if len(models) <= 32 else {})
        if isinstance(terminal.get("subagent_stats"), dict):
            counters = {"spawned", "requested", "background", "foreground", "unset", "started_in_background",
                        "max_depth", "spawned_by_subagents", "completed", "failed", "killed", "parent", "user",
                        "system", "refused", "depth_limit", "concurrency_limit", "budget", "by_type"}
            terminal["subagent_stats"] = _public_metrics(terminal["subagent_stats"], counters,
                                                          {key: counters - {"requested", "killed", "refused", "by_type"}
                                                           for key in ("requested", "killed", "refused", "by_type")})
        if isinstance(terminal.get("permission_denials"), list):
            terminal["permission_denials"] = [{key: item for key, item in denial.items()
                                               if key in {"tool_name", "tool_use_id"} and isinstance(item, str)}
                                              for denial in terminal["permission_denials"] if isinstance(denial, dict)]
    return terminal


class _FixtureAdapter:
    """Private subprocess fixture, available only with an explicit test executable."""

    def validate(self, request: JobRequest) -> None:
        executable = Path(os.environ.get("AGENT_JOB_TEST_FIXTURE_EXECUTABLE", ""))
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("fixture requires an absolute executable test path")

    def build_command(self, request: JobRequest) -> list[str]:
        self.validate(request)
        return [sys.executable, os.environ["AGENT_JOB_TEST_FIXTURE_EXECUTABLE"]]

    def parse(self, request_or_raw, raw_or_code=None, stderr_path=None, exit_code=0) -> ProviderOutcome:
        request, raw_path, stderr_path, exit_code = _parse_args(request_or_raw, raw_or_code, stderr_path, exit_code, default_provider="_fixture")
        value = _store._read_json(raw_path)
        return ProviderOutcome(value["success"], value["response"], value.get("session_id"),
                               value.get("usage", {}), value.get("error"), value.get("model"), value)


__all__ = [
    "ProviderAdapter",
    "_FixtureAdapter",
    "_classify_provider_failure",
    "_diagnostic_text",
    "_jsonl_events",
    "_metric_number",
    "_no_result_failure",
    "_public_error",
    "_public_metrics",
    "_public_terminal",
    "_public_usage",
]
