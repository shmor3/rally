# Contributing to Rally

Thank you for your interest in contributing to Rally! Rally is a lightweight, durable multi-provider agent job runner designed to coordinate autonomous coding agents across diverse CLI backends.

## Core Principles

- **Zero Runtime Dependencies**: Rally relies exclusively on the Python standard library (Python >= 3.9). No external runtime packages or virtual environments are required.
- **Durable Lifecycle & Portability**: Preserves background supervision, quiet waiting, dead worker recovery, and clean session chaining.
- **Backward Compatibility**: Maintains `./agent-job` as an identical entrypoint shim alongside `AGENT_JOB_*` environment variables.
- **Privacy & Safety First**: Sanitizes error messages, protects credentials, and never leaks prompt contents or raw event streams into summary listings.

## Local Setup

You can run Rally directly from a local git checkout without any build step:

```bash
# Clone the repository
git clone https://github.com/shmor3/rally.git
cd rally

# Verify the CLI works directly
./rally --help
./rally doctor
```

Alternatively, install Rally in editable mode with development test extras:

```bash
pip install -e '.[test]'
rally --help
```

## Running Tests

Tests are written using Python's standard `unittest` framework and require the optional `test` dependency group (e.g. PyYAML):

```bash
# Install development test dependencies
pip install '.[test]'

# Run the entire test suite
python3 -m unittest discover tests

# Run focused repository and metadata tests
python3 -m unittest tests.test_rally.RallyRepositoryTests

# Run privacy and sanitization tests
python3 -m unittest tests.test_rally.AgentJobPrivacyRegressionTests
```

All contributions must pass the full test suite and preserve Python 3.9 compatibility.

## Making Changes

1. **Test-Driven Development**: When adding features or fixing bugs, add focused unit tests reproducing the issue or verifying the new behavior before writing implementation code.
2. **Preserve Public Contracts**: Do not break the normalized schema version 1 result format or provider adapter contracts.
3. **No Credential Leaks**: Never hardcode API keys, session secrets, or personal paths in test fixtures or documentation. Use synthetic placeholders (e.g. `credential-sentinel`, `test-session-id`).
4. **Code Style**: Keep code concise, readable, and typed with deferred annotations (`from __future__ import annotations`).

## Reporting Security Issues

Please **do not** report security vulnerabilities or sensitive transcript exposures in public GitHub issues. Instead, follow the instructions in [SECURITY.md](SECURITY.md) to submit a report via GitHub Private Vulnerability Reporting.

## License

By contributing to Rally, you agree that your contributions will be licensed under the repository's [MIT License](LICENSE).
