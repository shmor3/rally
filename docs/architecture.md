# Rally Architecture

Rally is a durable multi-provider agent job runner implemented with standard library Python (>= 3.9). It coordinates independent agent processes across multiple CLI providers while maintaining durable transcripts and lifecycle tracking.

## Migration Note

Rally originated as a standalone extraction and has no dependency on NOMIRU.

## System Overview

```
                      +-------------------+
                      |   CLI Invocation  |
                      | (rally / agent-job)|
                      +---------+---------+
                                |
                                v
                      +-------------------+
                      |   rally_core.cli  |
                      +---------+---------+
                                |
            +-------------------+-------------------+
            |                                       |
            v                                       v
   +-----------------+                     +-----------------+
   | rally_core.store|                     |rally_core.process|
   +--------+--------+                     +--------+--------+
            |                                       |
            |                                       v
            |                              +-----------------+
            |                              |    Launcher     |
            |                              |(launchctl/detach|
            |                              +--------+--------+
            |                                       |
            |                                       v
            |                              +-----------------+
            |                              | Worker Process  |
            |                              +--------+--------+
            |                                       |
            |                                       v
            |                              +-----------------+
            |                              |Provider Adapter |
            |                              |(agy/copilot/    |
            |                              | codex/claude)   |
            |                              +--------+--------+
            |                                       |
            +<--------------------------------------+
            |
            v
  Durable Transcripts
(.artifacts/agent-jobs/<job-id>/)
```

## Package Boundaries

### 1. Root Executables
- `rally`: Primary entrypoint script (executable 755). Adds repository root to `sys.path` and delegates execution to `rally_core.cli:main()`.
- `agent-job`: Backward-compatibility wrapper (executable 755). Identical shim delegating directly to `rally_core.cli:main()`.

### 2. `rally_core.cli`
- Argument parsing with subcommands: `run`, `start`, `followup`, `status`, `wait`, `read`, `stop`, `list`, `doctor`.
- Provider flag mapping and normalization.
- Local provider diagnostics (`doctor`) executing bounded subprocess checks without external network requests.
- Formats terminal JSON results and standard error messages.

### 3. `rally_core.model`
- Canonical dataclasses: `JobRequest`, `JobDescriptor`, `JobResult`, `ProviderOutcome`.
- Protocol definitions: `ProviderAdapter`.
- Constants: `SCHEMA_VERSION = 1`, effort levels, truncation thresholds, help text contracts.
- Deterministic UTF-8 truncation helpers.

### 4. `rally_core.process`
- Supervised process management supporting two execution backends:
  - `launchctl`: Native macOS background job registration using system launch services.
  - `detached`: POSIX double-fork / detached session creation using `setsid` and `close_fds`.
- Process supervision and crash detection.
- Graceful drain and forced termination mechanisms (`SIGTERM` -> `SIGKILL`).
- Authentication token and token verification for internal worker supervision.

### 5. `rally_core.store`
- Durable file store managing `<cwd>/.artifacts/agent-jobs/<job_id>`.
- Configurable root via `AGENT_JOB_DIR` environment variable.
- Atomic state file management (`prompt.txt`, `request.json`, `metadata.json`, `stdout.jsonl`, `stderr.log`, `manager.*.log`, `response.txt`, `state`, `worker.json`, `result.json`).
- Job enumeration, retrieval, and status recovery for orphaned worker processes.

### 6. `rally_core.providers`
- Provider adapter abstraction (`base.py`) providing standard lifecycle parsing, token and credit extraction, public error categorization, and diagnostic formatting.
- `agy.py`: Google Antigravity (AGY) provider integration (`--dangerously-skip-permissions`, JSON stream format, `sonnet` / `flash` model alias resolution).
- `copilot.py`: GitHub Copilot CLI integration (`--allow-all`, `--no-ask-user`, JSONL event stream, soft credit limits via `--max-credits`).
- `codex.py`: OpenAI Codex CLI integration (`--dangerously-bypass-approvals-and-sandbox`, JSONL event stream).
- `claude.py`: Anthropic Claude Code integration (`--dangerously-skip-permissions`, JSONL event stream, multi-model token metrics).

## Durable Job Lifecycle Flow

```
+------------+       +------------+       +------------+       +------------+
|   START    |  -->  |  RUNNING   |  -->  | RECONCILE  |  -->  |  TERMINAL  |
|            |       |            |       |            |       |  SUCCESS / |
| Create dir |       | Worker logs|       | Check PID/ |       |   ERROR /  |
| Record req |       | raw stream |       | exit code  |       |   STOPPED  |
+------------+       +------------+       +------------+       +------------+
```

1. **Job Creation**:
   - `start` or `run` creates an isolated directory in `.artifacts/agent-jobs/<job_id>` with opaque UTC timestamp identifier `YYYYMMDDTHHMMSSZ-<12hex>`.
   - Initial parameters written to `request.json` and `prompt.txt`.
2. **Worker Launch**:
   - Launcher invokes `rally --_worker <root> <job_id>` under a detached session or `launchctl` job.
   - Worker writes `worker.json` recording launcher type and process PID.
3. **Execution & Streaming**:
   - Worker launches the target provider subprocess with autonomous bypass flags.
   - Provider stdout is continuously streamed to `stdout.jsonl`; stderr streams to `stderr.log`.
4. **Completion & Classification**:
   - Worker passes raw transcripts to the registered provider adapter.
   - Adapter extracts assistant response, session ID, usage metrics, and error classifications.
   - Semantic failures recorded in the provider event stream override zero process exit codes.
   - `result.json` written atomically, and state transitions to `SUCCESS`, `ERROR`, or `STOPPED`.
5. **Recovery**:
   - When `status` or `wait` checks a non-terminal job, it verifies worker process viability.
   - If the worker process has died without recording a terminal result, Rally reconciles the job and persists an `ERROR` result with diagnostic details.
6. **Session Continuity (Followup)**:
   - Resumable provider session IDs captured in `result.json` allow `followup` to spawn a new job continuing the conversation thread, establishing an explicit `source_job_id` link.
