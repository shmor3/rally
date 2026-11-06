# Security Policy

## Supported Versions

| Version | Supported          |
| :---    | :---               |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability or sensitive information exposure in Rally, **please do not open a public GitHub issue**.

Instead, please report security vulnerabilities via **GitHub Private Vulnerability Reporting**:
- Navigate to the **Security** tab of the `shmor3/rally` repository.
- Click **Report a vulnerability** (or go to [https://github.com/shmor3/rally/security/advisories/new](https://github.com/shmor3/rally/security/advisories/new)).
- Provide a detailed description of the vulnerability, steps to reproduce, and any relevant sanitized logs.

We will acknowledge receipt within 48 hours and work with you to coordinate a disclosure timeline.

## Warning: Public Transcript and Credential Disclosure

Rally stores local execution transcripts for every job under:
`<cwd>/.artifacts/agent-jobs/<job_id>/`

These local transcript directories contain:
- `prompt.txt`: The exact prompt passed to the agent.
- `stdout.jsonl` & `stderr.log`: Raw provider event streams and diagnostic logs.
- `request.json` & `response.txt`: Task configuration and assistant output.

> [!CAUTION]
> **Never publish raw transcript directories, logs, or un-redacted job artifacts to public forums, issues, or pull requests.**
> Transcripts may contain confidential codebase contents, proprietary prompts, file paths, or authorization tokens generated during provider execution. Always verify and redact all sensitive information before sharing diagnostic data.

While Rally actively sanitizes terminal error objects and suppresses prompts and raw streams in `rally list` and `rally status`, local transcripts retain raw execution records for durability and debugging.

## Autonomous Execution Model

Rally is designed for autonomous agent execution and invokes provider CLI backends with permission and sandbox bypass flags:
- **AGY**: `--dangerously-skip-permissions`
- **Copilot**: `--allow-all`, `--no-ask-user`
- **Codex**: `--dangerously-bypass-approvals-and-sandbox`
- **Claude**: `--dangerously-skip-permissions`

These flags allow providers to operate without blocking on interactive confirmation prompts. Callers must understand that passing prompts to Rally grants code execution authority within the target workspace. Do not execute unreviewed prompts against untrusted codebases or sensitive production systems.
