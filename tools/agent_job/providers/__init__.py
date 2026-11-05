"""Provider adapter implementations and registry."""

from __future__ import annotations

import os

from ..model import ProviderAdapter
from .base import (
    _FixtureAdapter,
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
from .agy import AgyAdapter
from .copilot import CopilotAdapter
from .codex import CodexAdapter
from .claude import ClaudeAdapter


def provider_adapters() -> dict[str, ProviderAdapter]:
    adapters = {"agy": AgyAdapter(), "copilot": CopilotAdapter(), "codex": CodexAdapter(), "claude": ClaudeAdapter()}
    if os.environ.get("AGENT_JOB_TEST_FIXTURE_EXECUTABLE"):
        adapters["_fixture"] = _FixtureAdapter()
    return adapters


__all__ = [
    "ProviderAdapter",
    "_FixtureAdapter",
    "AgyAdapter",
    "CopilotAdapter",
    "CodexAdapter",
    "ClaudeAdapter",
    "provider_adapters",
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
