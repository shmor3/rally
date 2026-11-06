# Rally

Rally is a lightweight, durable multi-provider agent job runner. It provides a uniform CLI and robust lifecycle management across multiple coding agent backends (AGY, Copilot, Codex, and Claude).

## Key Features

- **Standard Library Only**: Requires Python 3.9+ with zero external runtime dependencies.
- **Install-Free**: Executable directly from checkout via `./rally` or `/path/to/rally`, or installable via `pip`.
- **Multi-Provider Support**: Unified lifecycle for AGY, GitHub Copilot CLI, OpenAI Codex CLI, and Anthropic Claude Code.
- **Durable Lifecycle**: Detached background execution, quiet wait (internally bounded polling without chatter), crash recovery, and resumable follow-up sessions.
- **Clean Result Schema**: Normalized JSON output with truncated payloads and sanitized public errors.
- **Backward Compatibility**: Preserves generic `agent-job` invocation and `AGENT_JOB_*` environment configuration.

## Security and Permission Policy

> [!WARNING]
> All providers execute autonomously with permission and sandbox bypass flags:
> - AGY: `--dangerously-skip-permissions`
> - Copilot: `--allow-all`, `--no-ask-user`
> - Codex: `--dangerously-bypass-approvals-and-sandbox`
> - Claude: `--dangerously-skip-permissions`
>
> These flags grant broader machine access than `--cwd` or `--add-dir`. Every prompt must be treated as full code-execution authority.

## Requirements

- Python >= 3.9 (standard library only; no virtual environment or external packages required).
- Provider CLI tools installed on `PATH` for providers you intend to run (`agy`, `copilot`, `codex`, `claude`).

## Setup and Installation

### Direct Checkout (No Installation Required)

Rally can be run directly from any git checkout:

```bash
git clone https://github.com/shmor3/rally.git
cd rally
./rally --help
```

### Local Installation

You can optionally install Rally as a command-line tool into your current environment:

```bash
pip install -e .
rally --help
```

## Quick Start

Execute jobs directly using `./rally` (or installed `rally`):

```bash
# Check provider readiness
./rally doctor

# Run a blocking job with AGY
./rally agy run --model flash --effort high --prompt 'Review git status.'

# Start a background job with Copilot
./rally copilot start --model auto --max-credits 30 --prompt-file task.txt

# Start a background job with Codex in a specific workspace
./rally codex start --effort low --cwd /path/to/workspace --prompt 'Analyze benchmarks.'

# Run a blocking job with Claude
./rally claude run --model haiku --effort low --prompt 'Format markdown.'

# Wait for a background job to finish
./rally wait 20261105T190000Z-0123456789ab

# Check status of an existing job
./rally status 20261105T190000Z-0123456789ab

# Read the final assistant response text
./rally read 20261105T190000Z-0123456789ab

# Start a follow-up session from a completed job
./rally followup 20261105T190000Z-0123456789ab --prompt 'Address reviewer feedback.'

# Terminate a running job
./rally stop 20261105T190000Z-0123456789ab

# List recent jobs
./rally list
```

## Supported Providers

| Provider | CLI Command | Default Model | Effort Levels | Resumption Selector | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **AGY** | `agy` | `sonnet` | `high` | `--session`, `--conversation` | Supports `sonnet` and `flash` aliases. Supports `--project`, `--conversation`, and `--new-project`. |
| **Copilot** | `copilot` | `auto` | Omitted | `--session` | Explicit effort rejected when model is `auto`. `--max-credits` maps to `--max-ai-credits` (min 30). Supports `--name` and `--add-dir`. |
| **Codex** | `codex` | Provider default | Provider default | `--session` | Supports `--add-dir`. |
| **Claude** | `claude` | Provider default | Provider default | `--session` | Supports `--name` and `--add-dir`. |

`--add-dir` is repeatable across supported providers and requires an absolute directory path.

## Command Reference

### `rally PROVIDER run (--prompt TEXT | --prompt-file FILE) [options]`
Starts a job, streams raw provider progress to durable logs, blocks until the job reaches a terminal state, and prints the normalized terminal JSON result to standard output.

### `rally PROVIDER start (--prompt TEXT | --prompt-file FILE) [options]`
Starts a detached background job and immediately prints the initial descriptor JSON to standard output. Use `wait` or `status` with the returned `job_id`.

### `rally followup JOB_ID (--prompt TEXT | --prompt-file FILE)`
Starts a new background job continuing an existing conversation thread from a terminal source job (whether `SUCCESS`, `ERROR`, or `STOPPED`) that recorded a resumable `session_id`. Returns a descriptor for the new job.

### `rally status JOB_ID`
Prints summary JSON for the specified job. If a worker process crashed or exited unexpectedly, `status` detects dead workers and reconciles the job state into a durable terminal `ERROR` result.

### `rally wait JOB_ID [--timeout SECONDS]`
Waits for the specified job to enter a terminal state and outputs the final result JSON. Emits no polling chatter on standard streams, internally performing bounded polling. If `--timeout` expires before completion, `wait` exits with code 124 without killing the worker.

### `rally read JOB_ID`
Extracts and prints the assistant's final response text from standard storage.

### `rally stop JOB_ID`
Sends termination signals (`SIGTERM`, followed by `SIGKILL` if necessary) to cleanly drain and stop owned worker and provider processes.

### `rally list [--cwd DIR]`
Lists tracked jobs from the store in reverse chronological order, optionally filtering by workspace directory.

### `rally doctor [PROVIDER ...]`
Verifies local binary availability, executable paths, and version output for specified providers (or all providers by default). Does not send prompts, connect to remote APIs, or probe live billing/quotas.

## Result Schema (Version 1)

Terminal executions produce a standardized JSON object:

```json
{
  "schema_version": 1,
  "job_id": "20261105T190000Z-0123456789ab",
  "provider": "agy",
  "status": "SUCCESS",
  "exit_code": 0,
  "model": "flash",
  "effort": "high",
  "session_id": "session-xyz",
  "response": "Final assistant text response...",
  "usage": {
    "input_tokens": 120,
    "output_tokens": 450
  },
  "error": null,
  "cwd": "/path/to/workspace",
  "transcript_dir": "/path/to/workspace/.artifacts/agent-jobs/20261105T190000Z-0123456789ab",
  "created_at": 1762369200.0,
  "started_at": 1762369201.0,
  "finished_at": 1762369215.0,
  "source_job_id": null,
  "provider_result": {}
}
```

- Terminal states: `SUCCESS`, `ERROR`, `STOPPED`. Only `SUCCESS` exits zero.
- Semantic failures detected by provider adapters override zero process exit codes.
- Oversized assistant responses are deterministically truncated at UTF-8 boundaries up to 1 MB with a trailing `\n[truncated]` indicator.

## Transcript Storage and Privacy

All job files are stored in a dedicated directory per job:
`<cwd>/.artifacts/agent-jobs/<job_id>`

Each directory contains:
- `prompt.txt`: The input prompt text.
- `request.json`: The invocation parameters and configuration.
- `metadata.json`: Job creation metadata and session identifiers.
- `stdout.jsonl`: Raw streaming provider events.
- `stderr.log`: Raw provider standard error output.
- `manager.stdout.log` / `manager.stderr.log`: Supervisor logging.
- `response.txt`: Extracted final assistant output.
- `state`: Internal lifecycle state marker.
- `worker.json`: Supervisor process and worker process identifiers.
- `result.json`: The final normalized terminal result object.

### Privacy Guarantees
- Prompts, raw stderr, and raw event streams remain in the job transcript directory and are omitted from `rally list` and `rally status` summaries.
- Error payloads are sanitized to exclude private filesystem paths, authentication tokens, and billing endpoints.
- Diagnostic probing (`rally doctor`) avoids sending network requests or logging environment secrets.

## Compatibility and Portability

- **Entrypoint Compatibility**: `./agent-job` is provided alongside `./rally` as an identical entrypoint shim for backwards compatibility.
- **Environment Variables**:
  - `AGENT_JOB_DIR`: Overrides the transcript root directory (defaults to `.artifacts/agent-jobs` relative to working directory).
  - `AGENT_JOB_LAUNCHER`: Process supervision backend (`auto`, `launchctl`, or `detached`). `auto` selects macOS `launchctl` when available, falling back to detached POSIX sessions.
  - `AGENT_JOB_LAUNCHCTL`: Custom path to `launchctl` binary when using `launchctl` supervision.

## Development and Testing

Tests run using Python's standard `unittest` framework:

```bash
# Run focused repository and metadata tests
python3 -m unittest tests.test_rally.RallyRepositoryTests

# Run privacy regression tests
python3 -m unittest tests.test_rally.AgentJobPrivacyRegressionTests

# Run full test suite
python3 -m unittest discover tests
```

## Contributing, Security, and License

- **Contributing**: Please review [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on code style, testing, and pull requests.
- **Security Policy**: See [SECURITY.md](SECURITY.md) for details on vulnerability reporting via GitHub Private Vulnerability Reporting and warnings regarding sensitive transcript disclosures.
- **License**: Rally is open-source software licensed under the [MIT License](LICENSE). Copyright (c) 2026 shmor3.
