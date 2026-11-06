# Agent CLI fixtures

## Claude Code 2.1.311

Captured 2026-10-27 with print/stream-json/verbose, permission bypass,
permission-prompts none, no Chrome, haiku model, and low effort. The actual
dash-prefixed prompt passed after `--`; init reported `bypassPermissions`.
The CLI returned HTTP 403 `oauth_not_allowed_for_organization` and exited 1.
The captured terminal nevertheless has subtype `success`, plus `is_error: true`
and `terminal_reason: api_error`. Tests replay it with process exits 0 and 1.

`claude-2.1.311-oauth-error.jsonl` retains selected assistant/result
event schemas with generic sanitized messages and synthetic identifiers;
hooks, URLs, request IDs, and account details are omitted. Raw capture,
command vector, help, and version stay in local ignored
`.artifacts/reports/task-6-claude/`.

Files named `synthetic-success` and `synthetic-resume` are synthetic, derived
from that captured schema by removing API errors and setting response, usage,
cost, model usage, and a stable fixture session `claude-fixture-session`.
They are not live successful Claude calls. Live success/resume acceptance
requires working credentials.

## Codex CLI 0.164.0

Captured 2026-10-27 using `codex exec --json`, full permission/sandbox bypass,
skip-repository check, explicit temporary workspace, and low reasoning effort.
The dash-prefixed new prompt requested one harmless `pwd` command and
the exact response `CODEX_FIXTURE_OK`. The dash-prefixed resume prompt requested
`CODEX_FIXTURE_RESUME_OK` without tools. Both share thread
`codex-fixture-thread`. These fixtures preserve complete captured
event structures, including command execution and all usage fields, with
synthetic non-UUID identifiers and workspace paths.

An invalid-effort parse probe captured actual `error` and `turn.failed` events
and exited 1. Both new and resume probes accepted `--` before positional prompts
and sessions, including prompts beginning with a dash. Resume CLI help confirms
`--all` and `--skip-git-repo-check`; it does not expose `-C` or `--add-dir`.

Raw stdout/stderr, command vectors, help, and parse probe evidence remain in
local ignored `.artifacts/reports/task-5-codex/`. Tests inject diagnostics,
additional completed messages, truncation, and failure events into these shapes.

## Copilot CLI 1.0.73

Captured GitHub Copilot CLI 1.0.73 on 2026-10-27 using prompt mode, JSON output,
stream off, full permissions, automatic model selection, enabled project
instructions, and a 30 AI credit session limit.

Success and resume share session `copilot-fixture-session`.
Fixtures retain selected complete event structures with synthetic non-UUID
identifiers; unrelated events and volatile request/service/API-call
identifiers are omitted. The resume model event retains only fields
relevant to normalization. Raw captures remain in the local ignored
`.artifacts/reports/task-4-copilot/` directory.

The invalid-model stream contains MCP startup events but no result. Its process
exits 1 and stderr reports:

```text
Error: Model "invalid-task4-fixture" from --model flag is not available.
```

Tests inject non-JSON diagnostics, commentary, missing events, terminal errors,
and result exit failures into these captured event shapes.
