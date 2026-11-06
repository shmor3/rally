"""Portable Rally foundation tests; no provider installation is required."""

import ast
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import yaml


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT
RUNNER = ROOT / "rally"
COMPAT_RUNNER = ROOT / "agent-job"
sys.path.insert(0, str(ROOT))
import rally_core
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rally"
CREDENTIAL_SENTINEL = "credential-sentinel"


class RallyIdentityTests(unittest.TestCase):
    def test_primary_runner_is_rally(self):
        completed = subprocess.run(
            [str(RUNNER), "--help"], text=True, capture_output=True, check=False
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("usage: rally", completed.stdout)

    def test_rally_core_compatibility_runner_uses_same_cli(self):
        primary = subprocess.run([str(RUNNER), "--help"], text=True, capture_output=True)
        compat = subprocess.run([str(COMPAT_RUNNER), "--help"], text=True, capture_output=True)
        self.assertEqual(primary.returncode, 0)
        self.assertEqual(compat.returncode, 0)
        self.assertEqual(primary.stdout, compat.stdout)


class RallyRepositoryTests(unittest.TestCase):
    def test_repository_has_no_nomiru_runtime_dependency(self):
        forbidden = ("tools/" + "agent_job", "agy_" + "nomiru.sh", "NOMIRU" + "_AGY_")
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        tracked_paths = [
            ROOT / os.fsdecode(entry)
            for entry in proc.stdout.split(b"\0")
            if entry
        ]
        self.assertTrue(tracked_paths, "Tracked file set must not be empty")
        regular_files = [path for path in tracked_paths if path.is_file() and not path.is_symlink()]
        self.assertTrue(regular_files, "Tracked regular file set must not be empty")
        for path in regular_files:
            text = path.read_text(errors="replace")
            for marker in forbidden:
                self.assertNotIn(marker, text)

    def test_project_metadata_exposes_rally_script(self):
        metadata = (ROOT / "pyproject.toml").read_text()
        self.assertIn('rally = "rally_core.cli:main"', metadata)

    def test_project_metadata_packaging_and_license_boundaries(self):
        pyproject_path = ROOT / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file())
        metadata = pyproject_path.read_text()
        self.assertIn('requires-python = ">=3.9"', metadata)
        self.assertIn("dependencies = []", metadata)
        self.assertIn("[project.optional-dependencies]", metadata)
        self.assertIn('test = ["PyYAML>=6.0,<7"]', metadata)
        self.assertIn('license = { file = "LICENSE" }', metadata)
        self.assertIn('packages = ["rally_core", "rally_core.providers"]', metadata)
        self.assertIn('authors = [{ name = "shmor3" }]', metadata)
        self.assertIn('readme = "README.md"', metadata)
        self.assertIn("https://github.com/shmor3/rally", metadata)

    def test_project_metadata_parses_cleanly(self):
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                tomllib = None
        if tomllib is not None:
            data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertEqual(data["project"]["name"], "rally-agent")
            self.assertEqual(data["project"]["requires-python"], ">=3.9")
            self.assertEqual(data["project"]["dependencies"], [])
            self.assertEqual(
                data["project"]["optional-dependencies"],
                {"test": ["PyYAML>=6.0,<7"]},
            )
            self.assertEqual(data["project"]["authors"], [{"name": "shmor3"}])
            self.assertEqual(data["project"]["license"], {"file": "LICENSE"})
            self.assertEqual(data["project"]["readme"], "README.md")
            self.assertEqual(data["project"]["scripts"]["rally"], "rally_core.cli:main")
            self.assertEqual(data["tool"]["setuptools"]["packages"], ["rally_core", "rally_core.providers"])
            self.assertIn("Repository", data["project"]["urls"])
            self.assertIn("Issues", data["project"]["urls"])
            self.assertIn("Documentation", data["project"]["urls"])

    def test_standalone_documentation_exists(self):
        self.assertTrue((ROOT / "README.md").is_file())
        self.assertTrue((ROOT / "docs" / "architecture.md").is_file())

    def test_tracked_files_contain_no_forbidden_token(self):
        forbidden = ("r" + "tk").lower()
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        tracked_paths = [p for p in proc.stdout.decode("utf-8").split("\0") if p]
        self.assertTrue(tracked_paths, "Tracked regular file set must not be empty")

        expected_public_files = {
            "README.md",
            "docs/architecture.md",
            "pyproject.toml",
            "LICENSE",
            "CONTRIBUTING.md",
            "SECURITY.md",
            ".github/workflows/ci.yml",
            "tests/fixtures/rally/current-help-contract.json",
            "tests/fixtures/rally/README.md",
            "tests/fixtures/rally/codex-0.164.0-success.jsonl",
            "tests/test_rally.py",
        }
        scanned_files = set()

        for rel_path in tracked_paths:
            file_path = ROOT / rel_path
            if not file_path.is_file():
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            scanned_files.add(rel_path)
            self.assertNotIn(
                forbidden,
                content.lower(),
                f"Tracked file {rel_path} contains forbidden reference",
            )

        self.assertTrue(
            expected_public_files.issubset(scanned_files),
            f"Expected public files missing from scan: {expected_public_files - scanned_files}",
        )
        self.assertNotIn(
            forbidden,
            rally_core.model.HELP_TEXT.lower(),
            "CLI HELP_TEXT contains forbidden reference",
        )

    def test_readme_structure_and_direct_commands(self):
        readme_path = ROOT / "README.md"
        self.assertTrue(readme_path.is_file())
        text = readme_path.read_text(encoding="utf-8")
        forbidden = ("r" + "tk").lower()
        self.assertNotIn(forbidden, text.lower(), "README must not contain forbidden token")
        self.assertIn("./rally", text, "README must document direct checkout execution via ./rally")
        self.assertIn("rally", text, "README must document installed rally command")
        self.assertIn(".[test]", text, "README must document test extra installation")
        self.assertIn("LICENSE", text, "README must link to LICENSE")
        self.assertIn("CONTRIBUTING.md", text, "README must link to CONTRIBUTING.md")
        self.assertIn("SECURITY.md", text, "README must link to SECURITY.md")
        sections = [
            "Rally",
            "Security and Permission Policy",
            "Requirements",
            "Setup and Installation",
            "Quick Start",
            "Supported Providers",
            "Command Reference",
            "Result Schema (Version 1)",
            "Transcript Storage and Privacy",
            "Compatibility and Portability",
            "Development and Testing",
            "Contributing, Security, and License",
        ]
        indices = [text.index(heading) for heading in sections]
        self.assertEqual(indices, sorted(indices), f"README sections must appear in first-user order: {sections}")

    def test_mit_license_file(self):
        license_path = ROOT / "LICENSE"
        self.assertTrue(license_path.is_file(), "LICENSE file must exist")
        text = license_path.read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2026 shmor3", text)
        self.assertIn("Permission is hereby granted, free of charge", text)

    def test_contributing_and_security_policies(self):
        forbidden = ("r" + "tk").lower()
        contrib_path = ROOT / "CONTRIBUTING.md"
        self.assertTrue(contrib_path.is_file(), "CONTRIBUTING.md must exist")
        contrib_text = contrib_path.read_text(encoding="utf-8")
        self.assertNotIn(forbidden, contrib_text.lower())
        self.assertIn("Python", contrib_text)
        self.assertIn("unittest", contrib_text)
        self.assertIn(".[test]", contrib_text)
        self.assertIn("SECURITY.md", contrib_text)

        sec_path = ROOT / "SECURITY.md"
        self.assertTrue(sec_path.is_file(), "SECURITY.md must exist")
        sec_text = sec_path.read_text(encoding="utf-8")
        self.assertNotIn(forbidden, sec_text.lower())
        self.assertIn("private vulnerability reporting", sec_text.lower())
        self.assertIn("transcript", sec_text.lower())
        self.assertIn("credential", sec_text.lower())

    def test_cli_help_examples_use_direct_commands(self):
        forbidden = ("r" + "tk").lower()
        help_text = rally_core.model.HELP_TEXT
        self.assertNotIn(forbidden, help_text.lower(), "HELP_TEXT must not mention forbidden token")
        self.assertIn("Providers and examples:", help_text)
        self.assertIn("rally agy run", help_text)
        self.assertIn("rally copilot start", help_text)
        self.assertIn("rally codex start", help_text)
        self.assertIn("rally claude run", help_text)
        self.assertIn("rally wait JOB_ID", help_text)
        self.assertIn("rally followup JOB_ID", help_text)

    def test_continuous_integration_workflow_contract(self):
        ci_path = ROOT / ".github" / "workflows" / "ci.yml"
        self.assertTrue(ci_path.is_file(), "CI workflow file .github/workflows/ci.yml must exist")
        raw_text = ci_path.read_text(encoding="utf-8")

        # Absences of forbidden / dangerous constructs
        forbidden = ("r" + "tk").lower()
        self.assertNotIn(forbidden, raw_text.lower(), "Workflow must not mention forbidden wrapper token")
        self.assertNotIn("secrets.", raw_text.lower(), "Workflow must not reference GitHub secrets")
        self.assertNotIn("write", raw_text.lower(), "Workflow must not grant or request write permissions")
        for cred in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "COPILOT_", "AWS_", "AZURE_"):
            self.assertNotIn(cred, raw_text, f"Workflow must not reference provider credential {cred}")
        for tool in ("@anthropic-ai", "@github/copilot", "gemini-cli"):
            self.assertNotIn(tool, raw_text.lower(), f"Workflow must not install provider CLI {tool}")
        for pub in ("twine", "pypi", "gh release", "publish"):
            self.assertNotIn(pub, raw_text.lower(), f"Workflow must not publish or release packages ({pub})")
        self.assertNotIn("upload-artifact", raw_text, "Workflow must not upload artifacts")
        self.assertNotIn("download-artifact", raw_text, "Workflow must not download artifacts")

        parsed = yaml.safe_load(raw_text)
        self.assertIsInstance(parsed, dict, "Workflow must parse into a YAML mapping")
        self.assertEqual(parsed.get("name"), "CI")

        # Triggers: handle PyYAML's YAML 1.1 'on' boolean key behavior explicitly
        triggers = parsed.get("on") if "on" in parsed else parsed.get(True)
        self.assertIsInstance(triggers, dict, "Workflow triggers ('on') must parse to a dictionary")
        self.assertEqual(set(triggers.keys()), {"push", "pull_request"})
        self.assertEqual(triggers["push"], {"branches": ["main"]})
        self.assertEqual(triggers["pull_request"], {"branches": ["main"]})

        # Top-level permissions
        self.assertEqual(parsed.get("permissions"), {"contents": "read"})

        # Recursive permissions validation and escalation prevention
        def collect_permissions(node, path=""):
            found = []
            if isinstance(node, dict):
                for k, v in node.items():
                    cur = f"{path}.{k}" if path else str(k)
                    if k == "permissions":
                        found.append((cur, v))
                    found.extend(collect_permissions(v, cur))
            elif isinstance(node, list):
                for idx, item in enumerate(node):
                    found.extend(collect_permissions(item, f"{path}[{idx}]"))
            return found

        permission_entries = collect_permissions(parsed)
        self.assertEqual(len(permission_entries), 1, "Only top-level permissions must be defined")
        self.assertEqual(permission_entries[0][0], "permissions")
        self.assertEqual(permission_entries[0][1], {"contents": "read"})

        # Verify no job or step escalates permissions
        for job_name, job_body in parsed.get("jobs", {}).items():
            self.assertNotIn("permissions", job_body, f"Job {job_name} must not define or escalate permissions")
            for step_idx, step_body in enumerate(job_body.get("steps", [])):
                self.assertNotIn("permissions", step_body, f"Step {step_idx} in job {job_name} must not define or escalate permissions")

        # Recursively validate permission values
        for path, perm_spec in permission_entries:
            if isinstance(perm_spec, dict):
                for perm_name, perm_val in perm_spec.items():
                    self.assertIn(perm_val, {"read", "none"}, f"Permission {perm_name} at {path} must be read-only")
            elif isinstance(perm_spec, str):
                self.assertIn(perm_spec, {"read-all", "contents: read"}, f"Permission string at {path} must be read-only")

        # Concurrency
        concurrency = parsed.get("concurrency")
        self.assertEqual(concurrency, {
            "group": "${{ github.workflow }}-${{ github.ref }}",
            "cancel-in-progress": True,
        })

        # Jobs and matrix
        jobs = parsed.get("jobs", {})
        self.assertEqual(list(jobs.keys()), ["test"])
        test_job = jobs["test"]
        self.assertEqual(test_job.get("runs-on"), "ubuntu-latest")
        strategy = test_job.get("strategy", {})
        self.assertEqual(strategy.get("fail-fast"), False)
        matrix = strategy.get("matrix", {})
        self.assertEqual(matrix, {"python-version": ["3.9", "3.12", "3.15"]})

        # Steps
        steps = test_job.get("steps", [])
        self.assertTrue(len(steps) >= 6, "Workflow must contain required step pipeline")

        # Ordered third-party action uses bounded strictly to checkout and setup-python
        action_steps = [s for s in steps if "uses" in s]
        self.assertEqual(
            [s["uses"] for s in action_steps],
            ["actions/checkout@v4", "actions/setup-python@v5"],
        )
        self.assertEqual(
            action_steps[1].get("with", {}).get("python-version"),
            "${{ matrix.python-version }}",
        )

        run_steps = [s for s in steps if "run" in s]
        self.assertEqual(len(action_steps) + len(run_steps), len(steps))
        for step in run_steps:
            self.assertTrue(step.get("name"), "Run step must have a descriptive name")
            self.assertTrue(step.get("run"), "Run step must have a non-empty run script")

        # 1. Dependency installation: must install '.[test]' extra and upgrade tooling
        install_step = next(s for s in run_steps if ".[test]" in s["run"])
        self.assertIn("pip install --upgrade pip setuptools wheel", install_step["run"])
        self.assertIn("pip install '.[test]'", install_step["run"])

        # 2. Test suite execution
        unit_step = next(s for s in run_steps if "unittest tests.test_rally" in s["run"])
        self.assertEqual(unit_step["run"].strip(), "python -m unittest tests.test_rally")

        # 3. Byte-compilation
        compile_step = next(s for s in run_steps if "py_compile" in s["run"])
        self.assertIn("python -m py_compile", compile_step["run"])
        self.assertIn("rally", compile_step["run"])
        self.assertIn("agent-job", compile_step["run"])
        self.assertIn("rally_core/*.py", compile_step["run"])
        self.assertIn("rally_core/providers/*.py", compile_step["run"])

        # 4. Isolated plain-install smoke: proves runtime has no dependency on PyYAML
        plain_smoke_step = next(s for s in run_steps if "pip install ." in s["run"] and ".[test]" not in s["run"])
        self.assertIn("venv", plain_smoke_step["run"])
        self.assertIn("pip install .", plain_smoke_step["run"])
        self.assertIn("rally --help", plain_smoke_step["run"])
        self.assertIn("import rally_core", plain_smoke_step["run"])
        self.assertIn("import yaml", plain_smoke_step["run"])

        # 5. CLI help and privacy smoke checks
        smoke_step = next(s for s in run_steps if "RallyRepositoryTests" in s["run"])
        self.assertIn("./rally --help", smoke_step["run"])
        self.assertIn("./agent-job --help", smoke_step["run"])
        self.assertIn("rally --help", smoke_step["run"])
        self.assertIn("AgentJobPrivacyRegressionTests", smoke_step["run"])


def parse_provider_fixture(case, provider, name, exit_code=0):
    """Replay a bounded fixture through the registered adapter's parser."""
    path = Path(case.workspace) / "fixture-parse.jsonl"
    path.write_bytes((FIXTURES / name).read_bytes())
    stderr_path = Path(case.workspace) / "fixture-stderr.log"
    stderr_path.write_text("")
    request = rally_core.JobRequest(provider=provider, prompt="fixture test", cwd=Path(case.workspace))
    return rally_core.provider_adapters()[provider].parse(request, path, stderr_path, exit_code)


def assert_public_failure(case, outcome, error_type):
    """Failures stay unsuccessful, typed, and free of credential sentinels."""
    case.assertFalse(outcome.success)
    case.assertEqual(outcome.error["type"], error_type)
    case.assertNotIn(CREDENTIAL_SENTINEL, json.dumps({
        "error": outcome.error, "usage": outcome.usage, "provider_result": outcome.provider_result,
        "response": outcome.response}))


class AgentJobFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.root = self.workspace / "jobs"
        self.store = rally_core.JobStore(self.root)

    def request(self, **changes):
        fields = dict(provider="fixture", prompt="review secret prompt", cwd=self.workspace)
        fields.update(changes)
        return rally_core.JobRequest(**fields)

    def test_annotations_are_deferred_and_source_accepts_python_39_grammar(self):
        ast.parse(RUNNER.read_text(), feature_version=(3, 9))
        tree = ast.parse((RUNNER.parent / "rally_core" / "model.py").read_text(), feature_version=(3, 9))
        first_import = tree.body[1]
        self.assertIsInstance(first_import, ast.ImportFrom)
        self.assertEqual(first_import.module, "__future__")
        self.assertEqual([alias.name for alias in first_import.names], ["annotations"])
        self.assertIsInstance(rally_core.JobRequest.__annotations__["model"], str)

    def test_foundation_symbols_live_in_focused_modules(self):
        from rally_core.model import JobRequest, ProviderOutcome
        from rally_core.store import Job, JobStore
        from rally_core.process import JobManager
        self.assertEqual(JobRequest.__module__, "rally_core.model")
        self.assertEqual(JobStore.__module__, "rally_core.store")
        self.assertEqual(JobManager.__module__, "rally_core.process")

    def test_each_adapter_has_its_own_module(self):
        provider_adapters = rally_core.provider_adapters
        adapters = provider_adapters()
        self.assertEqual(adapters["agy"].__class__.__module__, "rally_core.providers.agy")
        self.assertEqual(adapters["copilot"].__class__.__module__, "rally_core.providers.copilot")
        self.assertEqual(adapters["codex"].__class__.__module__, "rally_core.providers.codex")
        self.assertEqual(adapters["claude"].__class__.__module__, "rally_core.providers.claude")

    def test_provider_adapter_protocol_specifies_explicit_parse_contract(self):
        import inspect
        from rally_core.model import ProviderAdapter
        sig = inspect.signature(ProviderAdapter.parse)
        param_names = list(sig.parameters.keys())
        self.assertEqual(param_names, ["self", "request", "raw_path", "stderr_path", "exit_code"])

    def test_compatibility_wrapper_two_way_synchronization(self):
        import rally_core.model as model
        import rally_core.store as store
        import rally_core.process as process
        import rally_core.providers as providers
        import rally_core.cli as cli

        # 1. Model symbol: SCHEMA_VERSION
        orig_schema = model.SCHEMA_VERSION
        try:
            rally_core.SCHEMA_VERSION = 999
            self.assertEqual(model.SCHEMA_VERSION, 999)
            self.assertEqual(rally_core.SCHEMA_VERSION, 999)

            model.SCHEMA_VERSION = 888
            self.assertEqual(rally_core.SCHEMA_VERSION, 888)
            self.assertEqual(model.SCHEMA_VERSION, 888)

            del rally_core.SCHEMA_VERSION
            self.assertFalse(hasattr(rally_core, "SCHEMA_VERSION"))
            self.assertFalse(hasattr(model, "SCHEMA_VERSION"))

            model.SCHEMA_VERSION = orig_schema
            self.assertTrue(hasattr(rally_core, "SCHEMA_VERSION"))
            self.assertEqual(rally_core.SCHEMA_VERSION, orig_schema)
        finally:
            model.SCHEMA_VERSION = orig_schema
            rally_core.SCHEMA_VERSION = orig_schema

        # 2. Store symbol: atomic_write
        orig_write = store.atomic_write
        try:
            sentinel_write = lambda *args, **kwargs: None
            rally_core.atomic_write = sentinel_write
            self.assertIs(store.atomic_write, sentinel_write)
            self.assertIs(rally_core.atomic_write, sentinel_write)

            sentinel_write_2 = lambda *args, **kwargs: None
            store.atomic_write = sentinel_write_2
            self.assertIs(rally_core.atomic_write, sentinel_write_2)

            del rally_core.atomic_write
            self.assertFalse(hasattr(rally_core, "atomic_write"))
            self.assertFalse(hasattr(store, "atomic_write"))

            store.atomic_write = orig_write
            self.assertIs(rally_core.atomic_write, orig_write)
        finally:
            store.atomic_write = orig_write
            rally_core.atomic_write = orig_write

        # 3. Process symbol: JobManager
        orig_manager = process.JobManager
        try:
            dummy_manager = object()
            rally_core.JobManager = dummy_manager
            self.assertIs(process.JobManager, dummy_manager)
            self.assertIs(rally_core.JobManager, dummy_manager)

            dummy_manager_2 = object()
            process.JobManager = dummy_manager_2
            self.assertIs(rally_core.JobManager, dummy_manager_2)

            del rally_core.JobManager
            self.assertFalse(hasattr(rally_core, "JobManager"))
            self.assertFalse(hasattr(process, "JobManager"))

            process.JobManager = orig_manager
            self.assertIs(rally_core.JobManager, orig_manager)
        finally:
            process.JobManager = orig_manager
            rally_core.JobManager = orig_manager

        # 4. CLI symbol: doctor_provider
        orig_doctor = cli.doctor_provider
        try:
            dummy_doctor = lambda p: {"status": "TEST"}
            rally_core.doctor_provider = dummy_doctor
            self.assertIs(cli.doctor_provider, dummy_doctor)
            self.assertIs(rally_core.doctor_provider, dummy_doctor)

            dummy_doctor_2 = lambda p: {"status": "TEST2"}
            cli.doctor_provider = dummy_doctor_2
            self.assertIs(rally_core.doctor_provider, dummy_doctor_2)

            del rally_core.doctor_provider
            self.assertFalse(hasattr(rally_core, "doctor_provider"))
            self.assertFalse(hasattr(cli, "doctor_provider"))

            cli.doctor_provider = orig_doctor
            self.assertIs(rally_core.doctor_provider, orig_doctor)
        finally:
            cli.doctor_provider = orig_doctor
            rally_core.doctor_provider = orig_doctor

        # 5. Providers symbol: provider_adapters
        import rally_core.providers as providers
        orig_adapters = providers.provider_adapters
        try:
            dummy_adapters = lambda: {"test": None}
            rally_core.provider_adapters = dummy_adapters
            self.assertIs(providers.provider_adapters, dummy_adapters)
            self.assertIs(rally_core.provider_adapters, dummy_adapters)

            dummy_adapters_2 = lambda: {"test2": None}
            providers.provider_adapters = dummy_adapters_2
            self.assertIs(rally_core.provider_adapters, dummy_adapters_2)

            del rally_core.provider_adapters
            self.assertFalse(hasattr(rally_core, "provider_adapters"))
            self.assertFalse(hasattr(providers, "provider_adapters"))

            providers.provider_adapters = orig_adapters
            self.assertIs(rally_core.provider_adapters, orig_adapters)
        finally:
            providers.provider_adapters = orig_adapters
            rally_core.provider_adapters = orig_adapters

    def test_canonical_package_and_submodule_parent_identity(self):
        import rally_core.model as model
        import rally_core.store as store
        import rally_core.process as process
        import rally_core.providers as providers
        import rally_core.cli as cli

        # 1. Under test loading, rally_core is registered in sys.modules with package identity
        self.assertIn("rally_core", sys.modules)
        self.assertIs(sys.modules["rally_core"], rally_core)
        self.assertTrue(hasattr(rally_core, "__path__"))
        self.assertEqual(rally_core.__package__, "rally_core")
        expected_pkg_dir = str(RUNNER.parent / "rally_core")
        self.assertEqual(list(rally_core.__path__), [expected_pkg_dir])
        self.assertIsNotNone(rally_core.__spec__)
        self.assertEqual(list(rally_core.__spec__.submodule_search_locations), [expected_pkg_dir])

        # 2. Submodule parent identity
        for sub_name, sub_mod in (("model", model), ("store", store), ("process", process), ("providers", providers), ("cli", cli)):
            self.assertIs(sys.modules[f"rally_core.{sub_name}"], sub_mod)
            self.assertIs(getattr(rally_core, sub_name), sub_mod)
            expected_pkg = "rally_core.providers" if sub_name == "providers" else "rally_core"
            self.assertEqual(sub_mod.__package__, expected_pkg)

        # 3. Under normal python import (isolated invocation)
        probe_code = (
            "import sys, os, json\n"
            f"sys.path.insert(0, {str(RUNNER.parent)!r})\n"
            "import rally_core\n"
            "import rally_core.model as m\n"
            "output = {\n"
            "  'package': rally_core.__package__,\n"
            "  'path': list(rally_core.__path__),\n"
            "  'spec_locations': list(rally_core.__spec__.submodule_search_locations) if rally_core.__spec__ else None,\n"
            "  'parent_is_mod': getattr(rally_core, 'model') is m,\n"
            "  'submodule_parent': m.__package__,\n"
            "}\n"
            "print(json.dumps(output))\n"
        )
        res = subprocess.run([sys.executable, "-c", probe_code], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        self.assertEqual(data["package"], "rally_core")
        self.assertEqual(data["path"], [expected_pkg_dir])
        self.assertEqual(data["spec_locations"], [expected_pkg_dir])
        self.assertTrue(data["parent_is_mod"])
        self.assertEqual(data["submodule_parent"], "rally_core")

    def test_provider_modules_observe_dynamic_process_and_base_patches(self):
        import rally_core.process as process
        import rally_core.providers.base as base

        dummy_raw = self.workspace / "dummy_raw.jsonl"
        dummy_raw.write_text('{"status": "ERROR", "error": "test error message"}\n')

        # 1. _public_text dynamic dispatch (owner-side & wrapper-side)
        orig_public_text = process._public_text
        try:
            # (a) Owner-side patch
            process._public_text = lambda text, limit=300: f"[OWNER_PATCHED:{text}]"
            outcome = rally_core.provider_adapters()["agy"].parse(dummy_raw, 1)
            self.assertEqual(outcome.error.get("message"), "[OWNER_PATCHED:test error message]")

            # (b) Wrapper-side patch
            rally_core._public_text = lambda text, limit=300: f"[WRAPPER_PATCHED:{text}]"
            outcome = rally_core.provider_adapters()["agy"].parse(dummy_raw, 1)
            self.assertEqual(outcome.error.get("message"), "[WRAPPER_PATCHED:test error message]")
        finally:
            process._public_text = orig_public_text
            rally_core._public_text = orig_public_text

        # 2. _partial_session dynamic dispatch (owner-side & wrapper-side)
        empty_raw = self.workspace / "empty_raw.jsonl"
        empty_raw.write_text("")
        orig_partial_session = process._partial_session
        try:
            # (a) Owner-side patch
            process._partial_session = lambda raw_path, provider: "owner-session-456"
            outcome = rally_core.provider_adapters()["agy"].parse(empty_raw, 0)
            self.assertEqual(outcome.session_id, "owner-session-456")
            self.assertEqual(rally_core.provider_adapters()["agy"].extract_session_id(empty_raw), "owner-session-456")

            # (b) Wrapper-side patch
            rally_core._partial_session = lambda raw_path, provider: "wrapper-session-789"
            outcome = rally_core.provider_adapters()["agy"].parse(empty_raw, 0)
            self.assertEqual(outcome.session_id, "wrapper-session-789")
            self.assertEqual(rally_core.provider_adapters()["agy"].extract_session_id(empty_raw), "wrapper-session-789")
        finally:
            process._partial_session = orig_partial_session
            rally_core._partial_session = orig_partial_session

        # 3. _jsonl_events dynamic dispatch (owner-side & wrapper-side)
        orig_jsonl_events = getattr(base, "_jsonl_events")
        try:
            synthetic_events = [
                {"type": "thread.started", "thread_id": "thread-owner-123"},
                {"type": "turn.completed", "usage": {}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "owner message text"}},
            ]
            # (a) Owner-side patch
            base._jsonl_events = lambda raw_path: iter(synthetic_events)
            outcome = rally_core.provider_adapters()["codex"].parse(dummy_raw, 0)
            self.assertEqual(outcome.session_id, "thread-owner-123")
            self.assertEqual(outcome.response, "owner message text")
            self.assertTrue(outcome.success)

            # (b) Wrapper-side patch
            synthetic_events_2 = [
                {"type": "thread.started", "thread_id": "thread-wrapper-456"},
                {"type": "turn.completed", "usage": {}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "wrapper message text"}},
            ]
            rally_core._jsonl_events = lambda raw_path: iter(synthetic_events_2)
            outcome = rally_core.provider_adapters()["codex"].parse(dummy_raw, 0)
            self.assertEqual(outcome.session_id, "thread-wrapper-456")
            self.assertEqual(outcome.response, "wrapper message text")
            self.assertTrue(outcome.success)
        finally:
            base._jsonl_events = orig_jsonl_events
            rally_core._jsonl_events = orig_jsonl_events

    def test_create_uses_private_files_and_collision_resistant_id(self):
        job = self.store.create(self.request())
        self.assertRegex(job.job_id, r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
        self.assertEqual(job.directory, self.root / job.job_id)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(job.directory.stat().st_mode & 0o777, 0o700)
        for path in job.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, path.name)
        self.assertEqual((job.directory / "prompt.txt").read_text(), "review secret prompt")
        self.assertEqual((job.directory / "state").read_text().strip(), "STARTING")
        request = json.loads((job.directory / "request.json").read_text())
        metadata = json.loads((job.directory / "metadata.json").read_text())
        self.assertEqual(request["cwd"], str(self.workspace))
        self.assertEqual(metadata["job_id"], job.job_id)
        self.assertEqual(metadata["schema_version"], 1)

    def test_list_only_exposes_safe_summary_fields(self):
        job = self.store.create(self.request(provider_options={"token": "private-token"}))
        (job.directory / "stdout.jsonl").write_text("private output")
        summary = self.store.list_summaries()[0]
        self.assertEqual(summary["job_id"], job.job_id)
        self.assertEqual(summary["status"], "STARTING")
        serialized = json.dumps(summary)
        for secret in ("review", "private-token", "private output", "provider_options"):
            self.assertNotIn(secret, serialized)

    def test_empty_list_does_not_create_root(self):
        self.assertEqual(self.store.list_summaries(), [])
        self.assertFalse(self.root.exists())

    def test_private_modes_are_exact_even_with_restrictive_umask(self):
        previous_umask = os.umask(0o777)
        try:
            job = self.store.create(self.request())
        finally:
            os.umask(previous_umask)
        self.assertEqual(job.directory.stat().st_mode & 0o777, 0o700)
        for path in job.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_nested_and_default_roots_remain_traversable_with_restrictive_umask(self):
        for root in (self.workspace / "nested" / "private" / "jobs", None):
            with self.subTest(root=root), mock.patch.dict(os.environ, {}, clear=True):
                store = rally_core.JobStore(root, cwd=self.workspace)
                previous_umask = os.umask(0o777)
                try:
                    job = store.create(self.request())
                finally:
                    os.umask(previous_umask)
                parent = job.directory
                while parent != self.workspace:
                    self.assertEqual(parent.stat().st_mode & 0o777, 0o700, parent)
                    parent = parent.parent

    def test_list_ignores_incomplete_and_non_object_metadata(self):
        for content in ("{", "[]", "null"):
            job = self.store.create(self.request())
            (job.directory / "metadata.json").write_text(content)
        self.assertEqual(self.store.list_summaries(), [])

    def test_job_id_validation_rejects_traversal_and_malformed_ids(self):
        for value in ("../outside", "/tmp/job", "x/../y", "", ".", "..", "20261027T120000Z-ABCDEF123456", "20261027T120000Z-abcdef123456\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rally_core.validate_job_id(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.get(value)
        self.assertFalse(self.root.exists())

    def test_valid_job_can_be_reopened(self):
        job = self.store.create(self.request())
        self.assertEqual(self.store.get(job.job_id), job)
        self.assertEqual(rally_core.validate_job_id(job.job_id), job.job_id)

    def test_symlink_root_is_rejected_without_writing_target(self):
        target = self.workspace / "target"
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.create(self.request())
        with self.assertRaises(ValueError):
            self.store.list_summaries()
        self.assertEqual(list(target.iterdir()), [])

    def test_symlink_job_directory_is_rejected_and_not_listed(self):
        job = self.store.create(self.request())
        alias = "20261027T120000Z-abcdef123456"
        (self.root / alias).symlink_to(job.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.get(alias)
        self.assertEqual([row["job_id"] for row in self.store.list_summaries()], [job.job_id])

    def test_control_characters_in_command_fields_fail_before_creation(self):
        for field in ("provider", "session_id", "model", "name"):
            for value in ("value\ninjected", "value\rinjected", "value\0injected"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.store.create(self.request(**{field: value}))
        self.assertFalse(self.root.exists())

    def test_invalid_provider_effort_source_and_options_fail_before_creation(self):
        for changes in ({"provider": "../fixture"}, {"effort": "infinite"}, {"source_job_id": "../other"}, {"provider_options": {"bad": object()}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.create(self.request(**changes))
        self.assertFalse(self.root.exists())

    def test_missing_workspace_and_non_directory_fail_before_creation(self):
        file_path = self.workspace / "file"
        file_path.write_text("not directory")
        for cwd in (self.workspace / "missing", file_path):
            with self.subTest(cwd=cwd), self.assertRaises(ValueError):
                self.store.create(self.request(cwd=cwd))
        self.assertFalse(self.root.exists())

    def test_extra_directories_must_exist_and_be_absolute(self):
        for extra in (Path("relative"), self.workspace / "missing"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.store.create(self.request(add_dirs=(extra,)))
        self.assertFalse(self.root.exists())
        job = self.store.create(self.request(add_dirs=(self.workspace,)))
        request = json.loads((job.directory / "request.json").read_text())
        self.assertEqual(request["add_dirs"], [str(self.workspace)])

    def test_default_root_uses_workspace_and_environment_override(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            store = rally_core.JobStore(cwd=self.workspace)
            self.assertEqual(store.root, self.workspace / ".artifacts" / "agent-jobs")
        with mock.patch.dict(os.environ, {"AGENT_JOB_DIR": str(self.root)}):
            self.assertEqual(rally_core.JobStore(cwd=self.workspace).root, self.root)
        self.assertFalse(self.root.exists())

    def test_creation_retries_id_collision_without_overwriting_prompt(self):
        fixed_time = rally_core.datetime.now(rally_core.timezone.utc)
        with mock.patch.object(rally_core, "datetime") as clock, mock.patch.object(rally_core.secrets, "token_hex", side_effect=["a" * 12, "a" * 12, "b" * 12]) as token:
            clock.now.return_value = fixed_time
            first = self.store.create(self.request(prompt="first"))
            second = self.store.create(self.request(prompt="second"))
        self.assertEqual(token.call_args_list, [mock.call(6)] * 3)
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertEqual((first.directory / "prompt.txt").read_text(), "first")

    def test_atomic_write_replaces_contents_privately_and_syncs_directory(self):
        path = self.workspace / "artifact"
        path.write_text("old")
        path.chmod(0o644)
        events = []
        original_fsync = os.fsync
        original_replace = os.replace

        def fsync(fd):
            events.append("fsync")
            return original_fsync(fd)

        def replace(source, destination):
            events.append("replace")
            self.assertEqual(Path(source).parent, self.workspace)
            self.assertEqual(Path(source).read_text(), "new")
            return original_replace(source, destination)

        with mock.patch.object(rally_core.os, "fsync", side_effect=fsync), mock.patch.object(rally_core.os, "replace", side_effect=replace):
            rally_core.atomic_write(path, "new")
        self.assertEqual(events, ["fsync", "replace", "fsync"])
        self.assertEqual(path.read_text(), "new")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.workspace.iterdir()), [path])

    def test_atomic_write_cleans_temporary_file_on_failure(self):
        path = self.workspace / "artifact"
        path.write_text("old")
        with mock.patch.object(rally_core.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                rally_core.atomic_write(path, b"new")
        self.assertEqual(path.read_text(), "old")
        self.assertEqual(list(self.workspace.iterdir()), [path])

    def test_atomic_write_rejects_symlink_destination(self):
        target = self.workspace / "target"
        target.write_text("original")
        alias = self.workspace / "alias"
        alias.symlink_to(target)
        with self.assertRaises(ValueError):
            rally_core.atomic_write(alias, "replacement")
        self.assertEqual(target.read_text(), "original")

    def test_provider_outcome_and_adapter_contract_are_provider_neutral(self):
        outcome = rally_core.ProviderOutcome(True, "done", "session", {"tokens": 1}, None, "model", {"status": "ok"})
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.response, "done")
        for name in ("validate", "build_command", "parse"):
            self.assertTrue(callable(getattr(rally_core.ProviderAdapter, name)))

    def test_cli_rejects_duplicate_or_missing_prompt_sources_before_creation(self):
        prompt_file = self.workspace / "prompt.txt"
        prompt_file.write_text("prompt")
        for flags in (["--prompt", "text", "--prompt-file", str(prompt_file)], []):
            with self.subTest(flags=flags), mock.patch.dict(os.environ, {"AGENT_JOB_DIR": str(self.root)}), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rally_core.main(["agy", "run", "--cwd", str(self.workspace), *flags]), 2)
        self.assertFalse(self.root.exists())

    def test_cli_validates_fields_before_creating_job(self):
        for flags in (["--session", "bad\nsession"], ["--effort", "infinite"], ["--cwd", str(self.workspace / "missing")], ["--add-dir", "relative"]):
            with self.subTest(flags=flags), mock.patch.dict(os.environ, {"AGENT_JOB_DIR": str(self.root)}), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rally_core.main(["agy", "--prompt", "text", *flags]), 2)
        self.assertFalse(self.root.exists())

    def test_cli_help_documents_autonomous_permission_policy(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(rally_core.main(["--help"]), 0)
        self.assertIn("bypass", output.getvalue())


class AgentJobPrivacyRegressionTests(unittest.TestCase):
    """Ensure tracked fixtures and acceptance docs use synthetic identifiers without leaks."""

    def test_fixtures_and_acceptance_docs_contain_no_live_uuids_or_billing_data(self):
        uuid_pattern = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
        req_pattern = re.compile(r"\breq_[0-9A-Za-z]{8,}\b")
        var_folders_pattern = re.compile(r"/var/folders/[^\s\"\']+")

        fixtures_dir = FIXTURES
        self.assertTrue(fixtures_dir.is_dir(), f"Fixtures dir missing: {fixtures_dir}")
        for fixture_path in fixtures_dir.iterdir():
            if fixture_path.name.startswith("."):
                continue
            text = fixture_path.read_text(encoding="utf-8")
            self.assertNotRegex(text, uuid_pattern, f"Fixture {fixture_path.name} contains live-looking UUID")
            self.assertNotRegex(text, req_pattern, f"Fixture {fixture_path.name} contains live-looking request ID")
            self.assertNotIn("https://claude.ai/settings/billing", text, f"Fixture {fixture_path.name} contains billing URL")
            self.assertNotRegex(text, var_folders_pattern, f"Fixture {fixture_path.name} contains /var/folders path")

        results_path = REPO_ROOT / "docs" / "testing" / "mac-mvp-results.md"
        if results_path.is_file():
            content = results_path.read_text(encoding="utf-8")
            # Check agent-job acceptance lines (around lines 110-160)
            rally_core_section = "\n".join(
                line for line in content.splitlines()
                if any(k in line for k in ("AGY Flash", "Copilot auto", "Codex low", "Claude Haiku", "concurrency acceptance", "TASK7_"))
            )
            self.assertNotRegex(rally_core_section, uuid_pattern, "mac-mvp-results.md agent-job section contains live UUIDs")
            self.assertNotIn("claude.ai/settings/billing", rally_core_section)


class AgentJobDoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.bin = self.workspace / "bin"
        self.bin.mkdir()
        self.capture = self.workspace / "probe-argv.jsonl"
        self.root = self.workspace / "jobs"
        self.environment = mock.patch.dict(os.environ, {
            "PATH": str(self.bin), "AGENT_JOB_DIR": str(self.root),
            "DOCTOR_TEST_SECRET": "credential-sentinel-never-print",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def executable(self, provider, *, version_body=None, auth_body=None):
        versions = {"agy": "1.4.8", "copilot": "GitHub Copilot CLI 1.0.73.",
                    "codex": "codex-cli 0.164.0", "claude": "2.1.311 (Claude Code)"}
        if version_body is None:
            version_body = "print(" + repr(versions[provider] + "\ncredential-sentinel-never-print") + ")"
        if auth_body is None:
            auth_body = ("print('{\"loggedIn\": true, \"email\": \"private@example.invalid\", \"token\": \"credential-sentinel-never-print\"}')"
                         if provider == "claude" else "print('Logged in using ChatGPT', file=sys.stderr)")
        path = self.bin / provider
        path.write_text(
            "#!" + sys.executable + "\nimport json, os, subprocess, sys, time\n"
            "with open(" + repr(str(self.capture)) + ", 'a') as stream: stream.write(json.dumps(["
            + repr(provider) + ", sys.argv[1:]]) + '\\n')\n"
            "if sys.argv[1:] == ['--version']:\n " + version_body.replace("\n", "\n ") + "\n"
            "elif sys.argv[1:] in (['auth', 'status', '--json'], ['login', 'status']):\n "
            + auth_body.replace("\n", "\n ") + "\n"
            "else: sys.exit(90)\n"
        )
        path.chmod(0o700)
        return path

    def cli(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = rally_core.main(list(arguments))
        return code, output.getvalue(), errors.getvalue()

    def doctor(self, *providers):
        code, output, errors = self.cli("doctor", *providers)
        self.assertEqual(code, 0, errors)
        self.assertEqual(errors, "")
        self.assertFalse(self.root.exists())
        self.assertNotIn("credential-sentinel-never-print", output)
        self.assertNotIn("private@example.invalid", output)
        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], 1)
        return {entry["provider"]: entry for entry in payload["providers"]}

    def test_help_documents_complete_lifecycle_risk_schema_and_examples(self):
        code, output, errors = self.cli("--help")
        self.assertEqual((code, errors), (0, ""))
        for phrase in ("agy", "copilot", "codex", "claude", "run", "start", "status", "wait",
                       "read", "stop", "list", "followup", "doctor", "bypass", "broader machine access",
                       "completed", "resumable session", ".artifacts/agent-jobs", "AGENT_JOB_DIR",
                       "AGENT_JOB_LAUNCHER", "AGENT_JOB_LAUNCHCTL", "schema_version", "provider_result",
                       "start once", "wait once", "agent-job", "--max-credits", "--prompt-file"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, output)

    def test_doctor_defaults_to_all_providers_and_missing_does_not_create_jobs(self):
        providers = self.doctor()
        self.assertEqual(set(providers), {"agy", "copilot", "codex", "claude"})
        for entry in providers.values():
            self.assertEqual(entry["status"], "MISSING")
            self.assertIsNone(entry["executable"])
            self.assertIsNone(entry["version"])
            self.assertEqual(entry["service_access"], "UNVERIFIED")
            self.assertIn("PATH", entry["reason"])

    def test_selected_provider_resolves_executable_and_only_emits_version_number(self):
        executable = self.executable("copilot")
        providers = self.doctor("copilot")
        self.assertEqual(set(providers), {"copilot"})
        entry = providers["copilot"]
        self.assertEqual(entry["status"], "READY")
        self.assertEqual(entry["executable"], str(executable))
        self.assertEqual(entry["version"], "1.0.73")
        self.assertEqual(entry["auth"], "NOT_CHECKED")
        self.assertEqual(entry["service_access"], "UNVERIFIED")
        self.assertIn("local", entry["reason"])
        self.assertEqual([json.loads(line) for line in self.capture.read_text().splitlines()],
                         [["copilot", ["--version"]]])

    def test_local_auth_probes_do_not_claim_live_service_access_or_print_identity(self):
        self.executable("claude")
        self.executable("codex")
        providers = self.doctor("claude", "codex")
        for name, version in (("claude", "2.1.311"), ("codex", "0.164.0")):
            self.assertEqual(providers[name]["status"], "READY")
            self.assertEqual(providers[name]["auth"], "SIGNED_IN")
            self.assertEqual(providers[name]["version"], version)
            self.assertEqual(providers[name]["service_access"], "UNVERIFIED")
            self.assertIn("service access unverified", providers[name]["reason"])
        self.assertEqual([json.loads(line) for line in self.capture.read_text().splitlines()], [
            ["claude", ["--version"]], ["claude", ["auth", "status", "--json"]],
            ["codex", ["--version"]], ["codex", ["login", "status"]],
        ])

    def test_auth_unavailable_is_fixed_reason_without_raw_provider_output(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.executable(provider, auth_body="print('credential-sentinel-never-print', file=sys.stderr)\nsys.exit(1)")
                entry = self.doctor(provider)[provider]
                self.assertEqual(entry["status"], "UNAVAILABLE")
                self.assertEqual(entry["auth"], "UNAVAILABLE")
                self.assertIn("local authentication", entry["reason"])

    def test_claude_logged_out_and_malformed_auth_are_unavailable(self):
        for output in ('{"loggedIn": false}', '{"loggedIn": "true"}', '{', '[]'):
            with self.subTest(output=output):
                self.executable("claude", auth_body="print(" + repr(output) + ")")
                entry = self.doctor("claude")["claude"]
                self.assertEqual(entry["status"], "UNAVAILABLE")
                self.assertEqual(entry["auth"], "UNAVAILABLE")

    def test_claude_deeply_nested_auth_is_sanitized_unavailable(self):
        output = b"[" * 1500 + b'"credential-sentinel-never-print"' + b"]" * 1500
        self.assertLessEqual(len(output), rally_core.DOCTOR_OUTPUT_LIMIT)
        with mock.patch.object(rally_core.shutil, "which", return_value="/fake/claude"), mock.patch.object(
            rally_core, "_bounded_probe", side_effect=[(b"2.1.311 (Claude Code)\n", 0, None), (output, 0, None)]
        ) as probe:
            try:
                entry = self.doctor("claude")["claude"]
            except RecursionError:
                self.fail("bounded auth JSON must return UNAVAILABLE instead of raising RecursionError")
        self.assertEqual(entry["status"], "UNAVAILABLE")
        self.assertEqual(entry["auth"], "UNAVAILABLE")
        self.assertEqual(entry["reason"], "local authentication probe unavailable")
        self.assertEqual(entry["service_access"], "UNVERIFIED")
        self.assertEqual(probe.call_args_list, [
            mock.call(["/fake/claude", "--version"]),
            mock.call(["/fake/claude", "auth", "status", "--json"]),
        ])

    def test_version_failure_and_unrecognized_output_are_unavailable(self):
        for body in ("print('credential-sentinel-never-print', file=sys.stderr)\nsys.exit(3)",
                     "print('private build credential-sentinel-never-print')"):
            with self.subTest(body=body):
                self.executable("agy", version_body=body)
                entry = self.doctor("agy")["agy"]
                self.assertEqual(entry["status"], "UNAVAILABLE")
                self.assertIsNone(entry["version"])
                self.assertIn("version", entry["reason"])

    def test_version_output_has_byte_limit(self):
        self.executable("agy", version_body="print('1.4.8\\n' + 'x' * 100000)")
        started = time.monotonic()
        entry = self.doctor("agy")["agy"]
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(entry["status"], "UNAVAILABLE")
        self.assertIn("output limit", entry["reason"])

    def test_version_timeout_kills_probe_and_descendants(self):
        child_pid = self.workspace / "child.pid"
        child = "import os,time;open(" + repr(str(child_pid)) + ",'w').write(str(os.getpid()));time.sleep(30)"
        self.executable("agy", version_body="subprocess.Popen([sys.executable, '-c', " + repr(child) + "])\ntime.sleep(30)")
        with mock.patch.object(rally_core, "DOCTOR_TIMEOUT_SECONDS", 0.4, create=True):
            entry = self.doctor("agy")["agy"]
        self.assertEqual(entry["status"], "UNAVAILABLE")
        self.assertIn("timed out", entry["reason"])
        self.assertTrue(child_pid.exists())
        pid = int(child_pid.read_text())
        state = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), state)

    def test_auth_probe_uses_same_output_and_time_bounds(self):
        self.executable("claude", auth_body="time.sleep(30)")
        with mock.patch.object(rally_core, "DOCTOR_TIMEOUT_SECONDS", 0.2, create=True):
            entry = self.doctor("claude")["claude"]
        self.assertEqual(entry["status"], "UNAVAILABLE")
        self.assertIn("timed out", entry["reason"])
        self.executable("claude", auth_body="print('x' * 100000)")
        entry = self.doctor("claude")["claude"]
        self.assertEqual(entry["status"], "UNAVAILABLE")
        self.assertIn("output limit", entry["reason"])

    def test_doctor_help_discloses_local_only_checks_and_no_prompt_probes(self):
        code, output, errors = self.cli("doctor", "--help")
        self.assertEqual((code, errors), (0, ""))
        for phrase in ("local", "READY", "MISSING", "UNAVAILABLE", "no prompt", "quota", "billing", "OAuth"):
            self.assertIn(phrase, output)

    def test_unknown_provider_is_rejected_without_probing(self):
        code, output, errors = self.cli("doctor", "invalid")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("invalid", errors)
        self.assertFalse(self.capture.exists())

    def test_bounded_probe_skips_killpg_when_birth_is_missing_or_mismatched(self):
        from rally_core.cli import _bounded_probe
        child_pid_file = self.workspace / "probe_child.pid"
        script = f"import os, time; open({str(child_pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(5)"

        def cleanup_child():
            if child_pid_file.exists():
                try:
                    os.kill(int(child_pid_file.read_text()), signal.SIGKILL)
                except (OSError, ValueError):
                    pass

        self.addCleanup(cleanup_child)
        with mock.patch("rally_core.cli.os.killpg") as mock_killpg, \
             mock.patch("rally_core.cli._process_birth", return_value=None):
            with mock.patch("rally_core.cli.DOCTOR_TIMEOUT_SECONDS", 0.05):
                out, code, err = _bounded_probe([sys.executable, "-c", script])
                self.assertEqual(err, "timed out")
                self.assertFalse(mock_killpg.called, "os.killpg must not be called when birth is missing")
        cleanup_child()

        with mock.patch("rally_core.cli.os.killpg") as mock_killpg, \
             mock.patch("rally_core.cli._process_birth", side_effect=["initial-birth", "reused-pid-birth"]):
            with mock.patch("rally_core.cli.DOCTOR_TIMEOUT_SECONDS", 0.05):
                out, code, err = _bounded_probe([sys.executable, "-c", script])
                self.assertEqual(err, "timed out")
                self.assertFalse(mock_killpg.called, "os.killpg must not be called when birth is mismatched")
        cleanup_child()

    def test_current_help_contract_covers_every_provider(self):
        contract = json.loads((FIXTURES / "current-help-contract.json").read_text())
        self.assertEqual(set(contract), {"agy", "copilot", "codex", "claude"})
        for provider, entry in contract.items():
            self.assertEqual(entry["provider"], provider)
            self.assertTrue(entry["bypass_flags"])
            self.assertIn("new", entry["modes"])
            self.assertIn("resume", entry["modes"])
            example = entry.get("example")
            self.assertIsNotNone(example, f"Provider {provider} must specify a help contract example")
            forbidden = ("r" + "tk").lower()
            self.assertNotIn(forbidden, example.lower())
            self.assertTrue(example.startswith(f"rally {provider}"), f"Example must start with 'rally {provider}'")
            self.assertIn(example, rally_core.model.HELP_TEXT, f"Example {example} must be present in HELP_TEXT")


class FixtureAdapter:
    def validate(self, request):
        if request.model == "invalid":
            raise ValueError("invalid fixture model")

    def build_command(self, request):
        return [sys.executable, os.environ["AGENT_JOB_TEST_FIXTURE_EXECUTABLE"]]

    def parse(self, *args, **kwargs):
        if len(args) == 4:
            _, raw_path, _, exit_code = args
        elif len(args) == 2:
            raw_path, exit_code = args
        else:
            raw_path = kwargs.get("raw_path", args[0])
            exit_code = kwargs.get("exit_code", 0)
        data = json.loads(Path(raw_path).read_text())
        return rally_core.ProviderOutcome(
            data["success"], data["response"], data.get("session_id"),
            data.get("usage", {}), data.get("error"), data.get("model"), data,
        )


class AgentJobLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.store = rally_core.JobStore(self.workspace / "jobs")
        self.fixture = self.workspace / "fixture.py"
        self.fixture.write_text(
            "import json, os, signal, sys, time\n"
            "if os.environ.get('FIXTURE_IGNORE_TERM'): signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "if os.environ.get('FIXTURE_DESCENDANT_READY'):\n"
            " import subprocess\n"
            " subprocess.Popen([sys.executable, '-c', 'import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open(os.environ[\"FIXTURE_DESCENDANT_READY\"], \"w\").write(str(os.getpid())); time.sleep(30)'])\n"
            "if os.environ.get('FIXTURE_READY'): open(os.environ['FIXTURE_READY'], 'w').write(str(os.getpid()))\n"
            "time.sleep(float(os.environ.get('FIXTURE_DELAY', '0')))\n"
            "if os.environ.get('FIXTURE_MALFORMED'): print('{')\n"
            "else: print(json.dumps({'success': not bool(os.environ.get('FIXTURE_ERROR')), 'response': os.environ.get('FIXTURE_RESPONSE', 'final answer'), 'session_id': 'fixture-session', 'usage': {'tokens': 2}, 'cwd': os.getcwd()}))\n"
            "print('fixture diagnostic', file=sys.stderr)\n"
            "sys.exit(int(os.environ.get('FIXTURE_EXIT', '0')))\n"
        )
        self.fixture.chmod(0o700)
        self.environment = mock.patch.dict(os.environ, {
            "AGENT_JOB_TEST_FIXTURE_EXECUTABLE": str(self.fixture),
            "AGENT_JOB_LAUNCHER": "detached",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = rally_core.JobManager(self.store, adapters={"_fixture": FixtureAdapter()})
        self.addCleanup(self.stop_remaining)

    def stop_remaining(self):
        for summary in self.store.list_summaries():
            self.manager.stop(summary["job_id"])

    def request(self, **changes):
        fields = dict(provider="_fixture", prompt="private prompt", cwd=self.workspace)
        fields.update(changes)
        return rally_core.JobRequest(**fields)

    def start(self, **environment):
        with mock.patch.dict(os.environ, environment):
            return self.manager.start(self.request())

    def eventually(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.02)
        self.fail("condition did not become true")

    def test_starting_running_success_and_required_private_artifacts(self):
        original_launch = self.manager._launch
        observed = []

        def launch(job, token):
            observed.append((job.directory / "state").read_text().strip())
            return original_launch(job, token)

        with mock.patch.object(self.manager, "_launch", side_effect=launch):
            descriptor = self.start(FIXTURE_DELAY="0.3")
        self.assertEqual(observed, ["STARTING"])
        job_id = descriptor["job_id"]
        self.eventually(lambda: self.manager.status(job_id)["status"] == "RUNNING")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.manager.wait(job_id)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["session_id"], "fixture-session")
        self.assertEqual(result["provider_result"]["cwd"], str(self.workspace))
        directory = self.store.get(job_id).directory
        required = {"metadata.json", "request.json", "prompt.txt", "state", "worker.json", "stdout.jsonl", "stderr.log", "manager.stdout.log", "manager.stderr.log", "response.txt", "exit_code", "result.json"}
        self.assertTrue(required <= {path.name for path in directory.iterdir()})
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600, path)

    def test_timeout_does_not_mutate_running_job_and_read_is_response_only(self):
        descriptor = self.start(FIXTURE_DELAY="0.4")
        job_id = descriptor["job_id"]
        self.eventually(lambda: self.manager.status(job_id)["status"] == "RUNNING")
        directory = self.store.get(job_id).directory
        before = {name: (directory / name).read_bytes() for name in ("metadata.json", "state", "worker.json")}
        result = self.manager.wait(job_id, timeout=0.01)
        self.assertEqual(result["exit_code"], 124)
        self.assertEqual(result["status"], "RUNNING")
        self.assertFalse((directory / "result.json").exists())
        self.assertEqual(before, {name: (directory / name).read_bytes() for name in before})
        with self.assertRaises(ValueError):
            self.manager.read(job_id)
        self.manager.wait(job_id)
        self.assertEqual(self.manager.read(job_id), "final answer")

    def test_terminal_operations_are_idempotent_and_result_is_immutable(self):
        job_id = self.start()["job_id"]
        result = self.manager.wait(job_id)
        directory = self.store.get(job_id).directory
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir() if path.is_file()}
        for _ in range(2):
            self.assertEqual(self.manager.wait(job_id), result)
            self.assertEqual(self.manager.stop(job_id), result)
            self.assertEqual(self.manager.status(job_id)["status"], "SUCCESS")
            self.assertEqual(self.manager.read(job_id), "final answer")
            self.assertEqual(self.manager.finalize(job_id, status="ERROR", exit_code=1), result)
        self.assertEqual(before, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir() if path.is_file()})

    def test_stop_escalates_term_to_kill_for_owned_process_group(self):
        ready = self.workspace / "ready"
        job_id = self.start(FIXTURE_DELAY="30", FIXTURE_IGNORE_TERM="1", FIXTURE_READY=str(ready))["job_id"]
        self.eventually(ready.exists)
        original_killpg = os.killpg
        signals = []

        def killpg(pgid, sig):
            signals.append(sig)
            return original_killpg(pgid, sig)

        with mock.patch.object(rally_core.os, "killpg", side_effect=killpg):
            started = time.monotonic()
            result = self.manager.stop(job_id)
        self.assertEqual(result["status"], "STOPPED")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertGreaterEqual(time.monotonic() - started, 2.8)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue((self.store.get(job_id).directory / "stop_requested").exists())
        identity = json.loads((self.store.get(job_id).directory / "worker.json").read_text())
        state = subprocess.run(["ps", "-p", str(identity["sentinel_pid"]), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), state)

    def test_graceful_stop_does_not_count_process_inspector_as_descendant(self):
        ready = self.workspace / "ready"
        job_id = self.start(FIXTURE_DELAY="30", FIXTURE_READY=str(ready))["job_id"]
        self.eventually(ready.exists)
        original_killpg = os.killpg
        signals = []

        def killpg(pgid, sig):
            signals.append(sig)
            return original_killpg(pgid, sig)

        with mock.patch.object(rally_core.os, "killpg", side_effect=killpg):
            started = time.monotonic()
            self.assertEqual(self.manager.stop(job_id)["status"], "STOPPED")
        self.assertEqual(signals, [signal.SIGTERM])
        self.assertLess(time.monotonic() - started, 1)

    def test_stop_before_launch_prevents_provider_execution(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        self.assertEqual(self.manager.stop(job_id)["status"], "STOPPED")
        with mock.patch.object(FixtureAdapter, "build_command", side_effect=AssertionError("provider launched")):
            self.assertEqual(rally_core.worker_main(self.store.root, job_id, token, adapters={"_fixture": FixtureAdapter()}), 130)
        self.assertEqual((job.directory / "stdout.jsonl").read_text(), "")

    def test_dead_worker_reconciles_into_durable_error_after_launch_grace(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        self.assertEqual(self.manager.status(job_id)["status"], "STARTING")
        self.manager.launch_grace = 0
        self.assertEqual(self.manager.status(job_id)["status"], "ERROR")
        result = self.manager.wait(job_id)
        self.assertEqual(result["error"]["type"], "worker_lost")
        self.assertEqual(self.manager.wait(job_id), result)

    def test_pid_reuse_with_wrong_token_never_signals_unrelated_process(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        path = self.store.get(job_id).directory / "worker.json"
        identity = json.loads(path.read_text())
        identity.update(pid=os.getpid(), pgid=os.getpgrp(), executable=sys.executable)
        rally_core.atomic_write(path, json.dumps(identity))
        with mock.patch.object(rally_core.os, "killpg") as killpg:
            self.assertEqual(self.manager.stop(job_id)["status"], "STOPPED")
        killpg.assert_not_called()

    def test_owned_pid_requires_exact_runner_path_and_exact_token_argument(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())
        identity.update(pid=12345, birth="mock-birth", executable=sys.executable)
        valid_command = [sys.executable, str(RUNNER), "--_worker", str(self.store.root), job_id, identity["token"]]
        with mock.patch.object(rally_core, "_process_birth", return_value="mock-birth"), \
             mock.patch.object(self.manager, "_process_argv", return_value=valid_command):
            self.assertTrue(self.manager._owns_worker(job, identity))
        for command in (
            [sys.executable, "/tmp/agent-job", "--_worker", str(self.store.root), job_id, identity["token"]],
            [sys.executable, str(RUNNER), "--_worker", str(self.store.root), job_id, identity["token"] + "suffix"],
        ):
            with self.subTest(command=command), \
                 mock.patch.object(rally_core, "_process_birth", return_value="mock-birth"), \
                 mock.patch.object(self.manager, "_process_argv", return_value=command):
                self.assertFalse(self.manager._owns_worker(job, identity))

    def test_provider_stream_symlinks_cannot_append_outside_job(self):
        for name in ("stdout.jsonl", "stderr.log"):
            with self.subTest(name=name), mock.patch.object(self.manager, "_launch"):
                job_id = self.manager.start(self.request())["job_id"]
            job = self.store.get(job_id)
            target = self.workspace / f"outside-{name}"
            target.write_text("outside remains unchanged\n")
            target.chmod(0o640)
            path = job.directory / name
            path.unlink()
            path.symlink_to(target)
            token = json.loads((job.directory / "worker.json").read_text())["token"]
            rally_core.worker_main(self.store.root, job_id, token, adapters={"_fixture": FixtureAdapter()})
            with self.subTest(name=name):
                self.assertEqual(target.read_text(), "outside remains unchanged\n")
                self.assertEqual(target.stat().st_mode & 0o777, 0o640)
                self.assertEqual(self.manager.wait(job_id)["status"], "ERROR")

    def test_manager_log_symlinks_are_rejected_before_launch(self):
        for launcher in ("detached", "launchctl"):
            for name in ("manager.stdout.log", "manager.stderr.log"):
                with self.subTest(launcher=launcher, name=name), mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": launcher}), mock.patch.object(self.manager, "_launch"):
                    job_id = self.manager.start(self.request())["job_id"]
                job = self.store.get(job_id)
                target = self.workspace / f"outside-{launcher}-{name}"
                target.write_text("outside remains unchanged\n")
                target.chmod(0o640)
                path = job.directory / name
                path.unlink()
                path.symlink_to(target)
                token = json.loads((job.directory / "worker.json").read_text())["token"]
                with self.subTest(launcher=launcher, name=name), mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": launcher}), mock.patch.object(rally_core.subprocess, "Popen") as popen, mock.patch.object(rally_core.subprocess, "run") as run:
                    with self.assertRaises((OSError, ValueError)):
                        self.manager._launch(job, token)
                    popen.assert_not_called()
                    run.assert_not_called()
                    self.assertEqual(target.read_text(), "outside remains unchanged\n")
                    self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_recreated_streams_and_manager_logs_are_private(self):
        for mode in ("provider", "detached", "launchctl"):
            with self.subTest(mode=mode), mock.patch.object(self.manager, "_launch"):
                job_id = self.manager.start(self.request())["job_id"]
            job = self.store.get(job_id)
            names = ("stdout.jsonl", "stderr.log") if mode == "provider" else ("manager.stdout.log", "manager.stderr.log")
            for name in names:
                (job.directory / name).unlink()
            token = json.loads((job.directory / "worker.json").read_text())["token"]
            previous_umask = os.umask(0)
            try:
                if mode == "provider":
                    rally_core.worker_main(self.store.root, job_id, token, adapters={"_fixture": FixtureAdapter()})
                else:
                    with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": mode}), mock.patch.object(rally_core.subprocess, "Popen"), mock.patch.object(rally_core.subprocess, "run"):
                        self.manager._launch(job, token)
            finally:
                os.umask(previous_umask)
            for name in names:
                with self.subTest(mode=mode, name=name):
                    self.assertTrue((job.directory / name).exists())
                    self.assertEqual((job.directory / name).stat().st_mode & 0o777, 0o600)

    def stop_during_parse(self, parse_error):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        entered, release, descendant = (self.workspace / name for name in ("parse-entered", "parse-release", "descendant"))
        source = (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import rally_core\n"
            "original = rally_core._FixtureAdapter.parse\n"
            "def parse(self, *args, **kwargs):\n"
            " Path(sys.argv[5]).write_text('entered')\n"
            " while not Path(sys.argv[6]).exists(): time.sleep(0.01)\n"
            " if sys.argv[7] == 'error': raise ValueError('parse barrier error')\n"
            " return original(self, *args, **kwargs)\n"
            "rally_core._FixtureAdapter.parse = parse\n"
            "sys.exit(rally_core.worker_main(Path(sys.argv[2]), sys.argv[3], sys.argv[4]))\n"
        )
        with mock.patch.dict(os.environ, {"FIXTURE_DESCENDANT_READY": str(descendant)}):
            child = subprocess.Popen([sys.executable, "-c", source, str(RUNNER.parent), str(self.store.root), job_id, token, str(entered), str(release), "error" if parse_error else "success"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.manager._children[job_id] = child
        original_argv = self.manager._process_argv

        def process_argv(pid):
            if pid == child.pid and child.poll() is None:
                return [str(Path(sys.executable).resolve()), str(RUNNER), "--_worker", str(self.store.root), job_id, token]
            return original_argv(pid)

        def release_parser():
            self.eventually(lambda: (job.directory / "stop_requested").exists())
            release.write_text("release")

        original_killpg, signals = os.killpg, []

        def killpg(pgid, sig):
            signals.append(sig)
            return original_killpg(pgid, sig)

        try:
            self.eventually(entered.exists)
            self.eventually(descendant.exists)
            releaser = threading.Thread(target=release_parser)
            releaser.start()
            with mock.patch.object(self.manager, "_process_argv", side_effect=process_argv), mock.patch.object(rally_core.os, "killpg", side_effect=killpg):
                self.assertEqual(self.manager.stop(job_id)["status"], "STOPPED")
            releaser.join(timeout=2)
            self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
            state = subprocess.run(["ps", "-p", descendant.read_text(), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)
        finally:
            try:
                original_killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=3)

    def test_stop_during_successful_parse_drains_owned_descendants(self):
        self.stop_during_parse(parse_error=False)

    def test_stop_during_failed_parse_drains_owned_descendants(self):
        self.stop_during_parse(parse_error=True)

    def test_worker_identity_uses_actual_framework_executable_path(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())
        executable = "/Library/Frameworks/Python.framework/Resources/Python.app/Contents/MacOS/Python"
        identity.update(pid=12345, birth="mock-birth", executable=executable)
        command = [executable, str(RUNNER), "--_worker", str(self.store.root), job_id, identity["token"]]
        with mock.patch.object(rally_core, "_process_birth", return_value="mock-birth"), \
             mock.patch.object(self.manager, "_process_argv", return_value=command):
            self.assertTrue(self.manager._owns_worker(job, identity))
        identity["executable"] = "/unrelated/python"
        with mock.patch.object(rally_core, "_process_birth", return_value="mock-birth"), \
             mock.patch.object(self.manager, "_process_argv", return_value=command):
            self.assertFalse(self.manager._owns_worker(job, identity))

    def test_wrong_worker_token_is_rejected_without_mutation(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        before = (job.directory / "worker.json").read_bytes()
        self.assertEqual(rally_core.worker_main(self.store.root, job_id, "wrong", adapters={"_fixture": FixtureAdapter()}), 2)
        self.assertEqual((job.directory / "worker.json").read_bytes(), before)
        self.assertEqual((job.directory / "state").read_text().strip(), "STARTING")

    def test_concurrent_finalizers_publish_result_last_once(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        barrier = threading.Barrier(2)
        results, failures = [], []
        original = rally_core.atomic_write
        published = []

        def write(path, text):
            if Path(path).name == "result.json":
                self.assertEqual((job.directory / "state").read_text().strip(), "ERROR")
                self.assertTrue((job.directory / "response.txt").exists())
                self.assertTrue((job.directory / "exit_code").exists())
            original(path, text)
            published.append(Path(path).name)

        def finalize(code):
            try:
                barrier.wait()
                results.append(self.manager.finalize(job_id, status="ERROR", exit_code=code))
            except Exception as error:
                failures.append(error)

        with mock.patch.object(rally_core, "atomic_write", side_effect=write):
            threads = [threading.Thread(target=finalize, args=(code,)) for code in (1, 2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=5)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(published.count("result.json"), 1)
        self.assertEqual(published[-1], "result.json")

    def test_parser_failures_and_invalid_outcomes_become_error_results(self):
        for kind in ("truncated", "wrong-shape", "raises"):
            with self.subTest(kind=kind), mock.patch.object(self.manager, "_launch"):
                job_id = self.manager.start(self.request())["job_id"]
            job = self.store.get(job_id)
            token = json.loads((job.directory / "worker.json").read_text())["token"]
            adapter = FixtureAdapter()
            if kind == "truncated":
                context = mock.patch.dict(os.environ, {"FIXTURE_MALFORMED": "1"})
            elif kind == "wrong-shape":
                context = mock.patch.object(adapter, "parse", return_value=rally_core.ProviderOutcome(True, None, None, {}, None, None, {}))
            else:
                context = mock.patch.object(adapter, "parse", side_effect=ValueError("bad output"))
            with context:
                self.assertNotEqual(rally_core.worker_main(self.store.root, job_id, token, adapters={"_fixture": adapter}), 0)
            result = self.manager.wait(job_id)
            self.assertEqual(result["status"], "ERROR")
            self.assertEqual(result["error"]["type"], "parse_error")

    def test_process_exit_and_provider_semantic_errors_override_success(self):
        for environment in ({"FIXTURE_EXIT": "7"}, {"FIXTURE_ERROR": "1"}):
            with self.subTest(environment=environment):
                result = self.manager.wait(self.start(**environment)["job_id"])
                self.assertEqual(result["status"], "ERROR")
                self.assertNotEqual(result["exit_code"], 0)

    def test_status_and_list_never_expose_prompt_raw_output_or_worker_token(self):
        job_id = self.start(FIXTURE_RESPONSE="private provider response")["job_id"]
        self.manager.wait(job_id)
        token = json.loads((self.store.get(job_id).directory / "worker.json").read_text())["token"]
        summary = json.dumps([self.manager.status(job_id), self.manager.list_jobs(cwd=self.workspace)])
        for secret in ("private prompt", "private provider response", token):
            self.assertNotIn(secret, summary)
        self.assertEqual(self.manager.list_jobs(cwd=self.workspace / "other"), [])

    def test_invalid_timeout_launcher_and_adapter_fail_before_creating_job(self):
        for timeout in (-1, float("nan"), float("inf"), "invalid"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.manager.wait("20261027T120000Z-abcdef123456", timeout=timeout)
        with self.assertRaises(ValueError):
            self.manager.start(self.request(model="invalid"))
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "unknown"}), self.assertRaises(ValueError):
            self.manager.start(self.request())
        self.assertFalse(self.store.root.exists())

    def test_launch_failure_is_durable_error(self):
        with mock.patch.object(self.manager, "_launch", side_effect=OSError("cannot launch")):
            job_id = self.manager.start(self.request())["job_id"]
        self.assertEqual(self.manager.wait(job_id)["error"]["type"], "launch_error")

    def test_terminal_stop_reaps_worker_started_by_this_manager(self):
        job_id = self.start()["job_id"]
        child = self.manager._children[job_id]
        self.eventually(lambda: (self.store.get(job_id).directory / "result.json").exists())
        self.manager.stop(job_id)
        self.assertIsNotNone(child.poll())
        self.assertNotIn(job_id, self.manager._children)

    def test_dead_finalizer_claim_is_recovered_without_hanging(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        (job.directory / ".finalize").mkdir(mode=0o700)
        finished = threading.Event()

        def recover():
            self.manager.finalize(job_id, status="ERROR", exit_code=1)
            finished.set()

        thread = threading.Thread(target=recover, daemon=True)
        thread.start()
        recovered = finished.wait(timeout=0.3)
        if not recovered:
            (job.directory / ".finalize").rmdir()
            thread.join(timeout=2)
        self.assertTrue(recovered, "abandoned finalizer claim blocked recovery")

    def test_stop_racing_success_cannot_rewrite_committed_terminal_artifacts(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        original_write = rally_core.atomic_write
        reached, release, finalized = threading.Event(), threading.Event(), threading.Event()
        results = []

        def write(path, content):
            if Path(path).name == "stop_requested":
                reached.set()
                release.wait(timeout=2)
            return original_write(path, content)

        def finalize():
            self.manager.finalize(job_id, status="ERROR", exit_code=1)
            finalized.set()

        with mock.patch.object(rally_core, "atomic_write", side_effect=write):
            stop_thread = threading.Thread(target=lambda: results.append(self.manager.stop(job_id)))
            stop_thread.start()
            self.assertTrue(reached.wait(timeout=2))
            finalizer = threading.Thread(target=finalize)
            finalizer.start()
            finalized.wait(timeout=0.3)
            release.set()
            stop_thread.join(timeout=3)
            finalizer.join(timeout=3)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual((job.directory / "state").read_text().strip(), result["status"])
        self.assertEqual(self.manager.status(job_id)["status"], result["status"])

    def test_detached_job_root_with_spaces_preserves_worker_ownership(self):
        self.manager = rally_core.JobManager(rally_core.JobStore(self.workspace / "jobs with spaces"), adapters={"_fixture": FixtureAdapter()})
        self.store = self.manager.store
        job_id = self.start(FIXTURE_DELAY="0.4")["job_id"]
        self.eventually(lambda: self.manager.status(job_id)["status"] == "RUNNING")
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())
        self.assertTrue(self.manager._owns_worker(job, identity))
        self.assertEqual(self.manager.wait(job_id)["status"], "SUCCESS")

    def test_worker_killed_during_execution_reconciles_to_error(self):
        ready = self.workspace / "ready"
        job_id = self.start(FIXTURE_DELAY="30", FIXTURE_READY=str(ready))["job_id"]
        self.eventually(ready.exists)
        identity = json.loads((self.store.get(job_id).directory / "worker.json").read_text())
        self.assertTrue(self.manager._owns_worker(self.store.get(job_id), identity))
        os.killpg(identity["pid"], signal.SIGKILL)
        self.manager.launch_grace = 0
        self.eventually(lambda: self.manager.status(job_id)["status"] == "ERROR")
        self.assertEqual(self.manager.wait(job_id)["error"]["type"], "worker_lost")

    def test_worker_only_death_drains_owned_provider_group_before_error(self):
        ready, descendant = self.workspace / "ready", self.workspace / "descendant"
        job_id = self.start(FIXTURE_DELAY="30", FIXTURE_IGNORE_TERM="1", FIXTURE_READY=str(ready), FIXTURE_DESCENDANT_READY=str(descendant))["job_id"]
        self.eventually(ready.exists)
        self.eventually(descendant.exists)
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())
        self.assertTrue(self.manager._owns_worker(job, identity))
        provider_pids = [int(ready.read_text()), int(descendant.read_text())]
        os.kill(identity["pid"], signal.SIGKILL)
        self.manager._children[job_id].wait(timeout=2)
        self.manager.launch_grace = 0
        original_killpg, original_write = os.killpg, rally_core.atomic_write
        signals, active_at_publication = [], []

        def active_pids():
            active = []
            for pid in provider_pids:
                state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                if state and not state.startswith("Z"):
                    active.append(pid)
            return active

        def killpg(pgid, sig):
            signals.append((pgid, sig))
            return original_killpg(pgid, sig)

        def write(path, text):
            if Path(path).name == "result.json":
                active_at_publication.append(active_pids())
            return original_write(path, text)

        try:
            self.assertEqual(active_pids(), provider_pids)
            with mock.patch.object(rally_core.os, "killpg", side_effect=killpg), mock.patch.object(rally_core, "atomic_write", side_effect=write):
                self.assertEqual(self.manager.status(job_id)["status"], "ERROR")
            self.assertEqual(signals, [(identity["provider_pgid"], signal.SIGTERM), (identity["provider_pgid"], signal.SIGKILL)])
            self.assertEqual(active_at_publication, [[]])
            self.assertEqual(active_pids(), [])
            self.assertEqual(self.manager.wait(job_id)["error"]["type"], "worker_lost")
        finally:
            try:
                original_killpg(identity["provider_pgid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_recovery_never_signals_unrelated_group_with_reused_identity(self):
        child = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"], start_new_session=True, stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        try:
            for changes in ({}, {"runner": "/unrelated/agent-job"}, {"token": "bad"}, {"executable": "relative/python"}, {"pgid": 0}, {"provider_command": []}):
                with self.subTest(changes=changes), mock.patch.object(self.manager, "_launch"):
                    job_id = self.manager.start(self.request())["job_id"]
                job = self.store.get(job_id)
                identity = json.loads((job.directory / "worker.json").read_text())
                identity.update(pid=child.pid, pgid=child.pid, provider_pgid=child.pid, runner=str(RUNNER), executable=str(Path(sys.executable).resolve()), provider_command=[sys.executable, "-c", "unrelated"])
                identity["provider_process"] = {"pid": child.pid, "birth": "stale-boot:1", "token": identity["token"], "executable": identity["executable"]}
                identity.update(changes)
                rally_core.atomic_write(job.directory / "worker.json", json.dumps(identity))
                self.manager.launch_grace = 0
                with self.subTest(changes=changes), mock.patch.object(rally_core.os, "killpg") as killpg:
                    self.assertEqual(self.manager.status(job_id)["status"], "ERROR")
                    killpg.assert_not_called()
                    self.assertIsNone(child.poll())
        finally:
            child.kill()
            child.wait(timeout=2)
            child.stdout.close()

    def test_forged_current_provider_birth_cannot_authorize_unrelated_group(self):
        child = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"], env={}, start_new_session=True, stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())
        identity.update(pid=child.pid, pgid=child.pid, provider_pgid=child.pid, runner=str(RUNNER), executable=str(Path(sys.executable).resolve()), provider_command=[sys.executable, "-c", "unrelated"])
        identity["provider_process"] = {"pid": child.pid, "birth": rally_core._process_birth(child.pid), "token": identity["token"], "executable": identity["executable"]}
        rally_core.atomic_write(job.directory / "worker.json", json.dumps(identity))
        self.manager.launch_grace = 0
        try:
            with mock.patch.object(rally_core.os, "killpg", wraps=os.killpg) as killpg:
                self.assertEqual(self.manager.status(job_id)["status"], "ERROR")
                killpg.assert_not_called()
            self.assertIsNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            child.stdout.close()

    def test_token_field_in_argv_cannot_authorize_unrelated_empty_environment_group(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())
        field = "AGENT_JOB_WORKER_TOKEN=" + identity["token"]
        child = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)", field], env={}, start_new_session=True, stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        identity.update(pid=child.pid, pgid=child.pid, provider_pgid=child.pid, runner=str(RUNNER), executable=str(Path(sys.executable).resolve()), provider_command=[sys.executable, "-c", "unrelated"])
        identity["provider_process"] = {"pid": child.pid, "birth": rally_core._process_birth(child.pid), "token": identity["token"], "executable": identity["executable"]}
        rally_core.atomic_write(job.directory / "worker.json", json.dumps(identity))
        self.manager.launch_grace = 0
        try:
            self.assertFalse(self.manager._process_has_job_token(child.pid, identity["token"]))
            with mock.patch.object(rally_core.os, "killpg", wraps=os.killpg) as killpg:
                self.assertEqual(self.manager.status(job_id)["status"], "ERROR")
                killpg.assert_not_called()
            self.assertIsNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            child.stdout.close()

    def test_stop_after_worker_death_drains_owned_provider_group(self):
        ready = self.workspace / "ready"
        job_id = self.start(FIXTURE_DELAY="30", FIXTURE_IGNORE_TERM="1", FIXTURE_READY=str(ready))["job_id"]
        self.eventually(ready.exists)
        identity = json.loads((self.store.get(job_id).directory / "worker.json").read_text())
        os.kill(identity["pid"], signal.SIGKILL)
        self.manager._children[job_id].wait(timeout=2)
        original_killpg, signals = os.killpg, []

        def killpg(pgid, sig):
            signals.append(sig)
            return original_killpg(pgid, sig)

        try:
            with mock.patch.object(rally_core.os, "killpg", side_effect=killpg):
                self.assertEqual(self.manager.stop(job_id)["status"], "STOPPED")
            self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
            state = subprocess.run(["ps", "-p", ready.read_text(), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)
        finally:
            try:
                original_killpg(identity["provider_pgid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_worker_death_recovers_provider_after_clean_environment_exec(self):
        ready = self.workspace / "clean-exec-ready"
        code = f"import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open({str(ready)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
        self.fixture.write_text(f"import os, sys\nos.execve(sys.executable, [sys.executable, '-c', {code!r}], {{}})\n")
        job_id = self.start()["job_id"]
        self.eventually(ready.exists)
        identity = json.loads((self.store.get(job_id).directory / "worker.json").read_text())
        os.kill(identity["pid"], signal.SIGKILL)
        self.manager._children[job_id].wait(timeout=2)
        self.manager.launch_grace = 0
        original_killpg, signals = os.killpg, []

        def killpg(pgid, sig):
            signals.append(sig)
            return original_killpg(pgid, sig)

        try:
            with mock.patch.object(rally_core.os, "killpg", side_effect=killpg):
                self.assertEqual(self.manager.status(job_id)["status"], "ERROR")
            self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
            state = subprocess.run(["ps", "-p", ready.read_text(), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)
        finally:
            try:
                original_killpg(identity["provider_pgid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_provider_birth_identity_is_durable_before_provider_executes(self):
        self.fixture.write_text(
            "import json, os\nfrom pathlib import Path\n"
            f"job = next(Path({str(self.store.root)!r}).iterdir())\n"
            "identity = json.loads((job / 'worker.json').read_text())\n"
            "record = identity.get('provider_process', {})\n"
            "print(json.dumps({'success': record.get('pid') == os.getpid() and bool(record.get('birth')), 'response': 'identity checked'}))\n"
        )
        result = self.manager.wait(self.start()["job_id"])
        self.assertEqual(result["status"], "SUCCESS")

    def test_running_state_already_has_durable_provider_birth(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        original_write, identities_at_running = rally_core.atomic_write, []

        def write(path, content):
            if Path(path).name == "state" and content == "RUNNING\n":
                identities_at_running.append(bool(json.loads((job.directory / "worker.json").read_text()).get("provider_process")))
            return original_write(path, content)

        with mock.patch.object(rally_core, "atomic_write", side_effect=write):
            self.assertEqual(rally_core.worker_main(self.store.root, job_id, token, adapters={"_fixture": FixtureAdapter()}), 0)
        self.assertEqual(identities_at_running, [True])

    def test_terminal_success_and_error_drain_background_children_and_sentinel(self):
        for success in (True, False):
            ready = self.workspace / f"background-{success}"
            code = f"import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open({str(ready)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
            self.fixture.write_text(
                "import json, subprocess, sys, time\nfrom pathlib import Path\n"
                f"subprocess.Popen([sys.executable, '-c', {code!r}], env={{}})\n"
                f"while not Path({str(ready)!r}).exists(): time.sleep(0.01)\n"
                f"print(json.dumps({{'success': {success!r}, 'response': 'provider finished'}}))\n"
            )
            job_id = self.start()["job_id"]
            self.eventually(ready.exists)
            child = int(ready.read_text())
            identity = json.loads((self.store.get(job_id).directory / "worker.json").read_text())
            try:
                result = self.manager.wait(job_id, timeout=10)
                self.assertEqual(result["status"], "SUCCESS" if success else "ERROR")
                state = subprocess.run(["ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                self.assertTrue(not state or state.startswith("Z"), state)
                self.assertIn("sentinel_pid", identity)
                state = subprocess.run(["ps", "-p", str(identity["sentinel_pid"]), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                self.assertTrue(not state or state.startswith("Z"), state)
            finally:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_worker_death_recovers_clean_environment_child_after_provider_exit(self):
        ready, exit_provider = self.workspace / "child-ready", self.workspace / "exit-provider"
        code = f"import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open({str(ready)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
        self.fixture.write_text(
            "import subprocess, sys, time\nfrom pathlib import Path\n"
            f"subprocess.Popen([sys.executable, '-c', {code!r}], env={{}})\n"
            f"while not Path({str(exit_provider)!r}).exists(): time.sleep(0.01)\n"
        )
        job_id = self.start()["job_id"]
        self.eventually(ready.exists)
        identity = json.loads((self.store.get(job_id).directory / "worker.json").read_text())
        os.kill(identity["pid"], signal.SIGKILL)
        self.manager._children[job_id].wait(timeout=2)
        exit_provider.write_text("exit")

        def provider_exited():
            process = identity.get("provider_process", {}).get("pid")
            state = subprocess.run(["ps", "-p", str(process), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            return not state or state.startswith("Z")

        self.eventually(provider_exited)
        self.manager.launch_grace = 0
        child = int(ready.read_text())
        try:
            self.assertEqual(self.manager.status(job_id)["status"], "ERROR")
            state = subprocess.run(["ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)
            self.assertIn("sentinel_pid", identity)
            state = subprocess.run(["ps", "-p", str(identity["sentinel_pid"]), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)
        finally:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def sentinel_without_manager(self, with_child):
        ready, exit_provider, child_ready = (self.workspace / name for name in ("provider-ready", "provider-exit", "child-ready"))
        code = f"import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open({str(child_ready)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
        self.fixture.write_text(
            "import os, subprocess, sys, time\nfrom pathlib import Path\n"
            + (f"subprocess.Popen([sys.executable, '-c', {code!r}], env={{}})\n" if with_child else "")
            + f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
            + f"while not Path({str(exit_provider)!r}).exists(): time.sleep(0.01)\n"
        )
        job_id = self.start()["job_id"]
        self.eventually(ready.exists)
        if with_child:
            self.eventually(child_ready.exists)
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())

        def inactive(pid):
            state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
            return not state or state.startswith("Z")

        try:
            os.kill(identity["pid"], signal.SIGKILL)
            self.manager._children[job_id].wait(timeout=2)
            exit_provider.write_text("exit")
            self.eventually(lambda: inactive(int(ready.read_text())))
            if with_child:
                time.sleep(0.25)
                self.assertFalse(inactive(identity["sentinel_pid"]), "sentinel left live descendants without ownership anchor")
                os.kill(int(child_ready.read_text()), signal.SIGKILL)
            self.eventually(lambda: inactive(identity["sentinel_pid"]), timeout=2)
            self.assertFalse((job.directory / "result.json").exists())
        finally:
            try:
                os.killpg(identity["provider_pgid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_lone_sentinel_exits_after_worker_and_provider_without_manager(self):
        self.sentinel_without_manager(with_child=False)

    def test_sentinel_retains_anchor_until_last_descendant_exits_without_manager(self):
        self.sentinel_without_manager(with_child=True)

    def test_launchctl_command_contains_absolute_runner_and_token(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl"}), mock.patch.object(rally_core.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            job_id = self.manager.start(self.request())["job_id"]
        command = run.call_args.args[0]
        identity = json.loads((self.store.get(job_id).directory / "worker.json").read_text())
        self.assertEqual(command[:2], ["launchctl", "submit"])
        self.assertEqual(command[command.index("--") + 1:command.index("--_worker")], [sys.executable, str(RUNNER)])
        self.assertEqual(identity["runner"], str(RUNNER))
        self.assertIn(identity["token"], command)
        self.assertEqual(command[command.index("--_worker") + 1:][:2], [str(self.store.root), job_id])

    def test_launchctl_environment_is_private_token_checked_and_consumed_before_provider(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl", "FIXTURE_RESPONSE": "caller response", "FIXTURE_SECRET": "private environment value"}), mock.patch.object(rally_core.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        environment_file = job.directory / "launch-environment.json"
        self.assertTrue(environment_file.exists())
        self.assertEqual(environment_file.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private environment value", json.dumps(run.call_args.args[0]))
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        adapter = FixtureAdapter()
        original_build = adapter.build_command

        def build(request):
            self.assertFalse(environment_file.exists())
            self.assertEqual(os.environ["FIXTURE_SECRET"], "private environment value")
            return original_build(request)

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(rally_core.worker_main(self.store.root, job_id, "wrong", adapters={"_fixture": adapter}), 2)
            self.assertTrue(environment_file.exists())
            self.assertNotIn("FIXTURE_SECRET", os.environ)
            with mock.patch.object(adapter, "build_command", side_effect=build):
                self.assertEqual(rally_core.worker_main(self.store.root, job_id, token, adapters={"_fixture": adapter}), 0)
        self.assertEqual(self.manager.read(job_id), "caller response")
        self.assertNotIn("private environment value", json.dumps(self.manager.wait(job_id)))

    def test_stale_starting_recovery_removes_unconsumed_launch_environment(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl"}), mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        path = self.store.get(job_id).directory / "launch-environment.json"
        self.assertTrue(path.exists())
        self.manager.launch_grace = 0
        self.assertEqual(self.manager.status(job_id)["status"], "ERROR")
        self.assertFalse(path.exists())

    def test_delayed_launchctl_worker_is_not_prematurely_finalized(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl"}), mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        metadata = json.loads(job.metadata.read_text())
        metadata["created_at"] = time.time() - 2.0
        job.metadata.write_text(json.dumps(metadata))

        with mock.patch.object(self.manager, "_is_launchctl_active", return_value=True):
            status = self.manager.status(job_id)
            self.assertEqual(status["status"], "STARTING")
            self.assertFalse((job.directory / "result.json").exists())

        with mock.patch.object(self.manager, "_is_launchctl_active", return_value=False):
            status = self.manager.status(job_id)
            self.assertEqual(status["status"], "ERROR")
            result = json.loads(job.result.read_text())
            self.assertEqual(result["error"]["type"], "worker_lost")

    def test_launchctl_probe_tristate_and_conservative_extended_grace(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl"}), mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)

        # 1. Tri-state probe return values
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as mock_run:
            self.assertIs(self.manager._is_launchctl_active(job, timeout=0.5), True)
            self.assertEqual(mock_run.call_args[1].get("timeout"), 0.5)

        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
            self.assertIs(self.manager._is_launchctl_active(job), False)

        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("launchctl", 0.5)):
            self.assertIsNone(self.manager._is_launchctl_active(job))

        with mock.patch("subprocess.run", side_effect=OSError("binary missing")):
            self.assertIsNone(self.manager._is_launchctl_active(job))

        # 2. Conservative extended grace for timeout, error, and unknown
        for probe_result in (
            subprocess.TimeoutExpired("launchctl", 1.0),
            OSError("launchctl error"),
            None,
        ):
            metadata = json.loads(job.metadata.read_text())
            metadata["created_at"] = time.time() - 2.0
            job.metadata.write_text(json.dumps(metadata))

            if isinstance(probe_result, Exception):
                patcher = mock.patch.object(self.manager, "_is_launchctl_active", side_effect=probe_result)
            else:
                patcher = mock.patch.object(self.manager, "_is_launchctl_active", return_value=probe_result)

            with patcher:
                status = self.manager.status(job_id)
                self.assertEqual(status["status"], "STARTING")
                self.assertFalse((job.directory / "result.json").exists())

    def test_wait_timeout_remains_truthful_when_launchctl_probe_hangs(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl"}), mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]

        def hanging_run(cmd, *args, **kwargs):
            timeout = kwargs.get("timeout", 1.0)
            time.sleep(timeout)
            raise subprocess.TimeoutExpired(cmd, timeout)

        with mock.patch("subprocess.run", side_effect=hanging_run):
            start = time.monotonic()
            res = self.manager.wait(job_id, timeout=0.1)
            elapsed = time.monotonic() - start
            self.assertEqual(res["exit_code"], 124)
            self.assertLess(elapsed, 0.4)

    def test_wait_timeout_on_stale_launchctl_job_skips_recovery_and_does_not_create_result(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl"}), mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        # Make job stale (older than extended grace 5.0s)
        metadata = json.loads(job.metadata.read_text())
        metadata["created_at"] = time.time() - 10.0
        job.metadata.write_text(json.dumps(metadata))

        probe_calls = []

        def hanging_run(cmd, *args, **kwargs):
            timeout = kwargs.get("timeout", 1.0)
            probe_calls.append(timeout)
            time.sleep(timeout)
            raise subprocess.TimeoutExpired(cmd, timeout)

        with mock.patch("subprocess.run", side_effect=hanging_run):
            start = time.monotonic()
            res = self.manager.wait(job_id, timeout=0.1)
            elapsed = time.monotonic() - start
            # Wait timeout must remain truthful (near requested budget, not consuming multiple probe timeouts)
            self.assertEqual(len(probe_calls), 1)
            self.assertLessEqual(sum(probe_calls), 0.1 + 1e-3)
            self.assertLess(elapsed, 0.4)
            self.assertEqual(res["exit_code"], 124)
            # Must not finalize or create result.json
            self.assertFalse((job.directory / "result.json").exists())

        # Direct status() without an external deadline still performs recovery on stale worker
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
            status = self.manager.status(job_id)
            self.assertEqual(status["status"], "ERROR")
            self.assertTrue((job.directory / "result.json").exists())
            result = json.loads((job.directory / "result.json").read_text())
            self.assertEqual(result["error"]["type"], "worker_lost")

    def test_invalid_launch_environment_is_error_without_spawning_provider(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl"}), mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        rally_core.atomic_write(job.directory / "launch-environment.json", json.dumps({"BAD": "value\0invalid"}))
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        with mock.patch.object(FixtureAdapter, "build_command", side_effect=AssertionError("provider launched")):
            self.assertNotEqual(rally_core.worker_main(self.store.root, job_id, token, adapters={"_fixture": FixtureAdapter()}), 0)
        self.assertEqual(self.manager.wait(job_id)["status"], "ERROR")
        self.assertFalse((job.directory / "launch-environment.json").exists())

    def test_stale_worker_snapshot_does_not_finalize_live_worker(self):
        original_launch = self.manager._launch
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        original_owns = self.manager._owns_worker
        stale, release = threading.Event(), threading.Event()
        summaries = []

        def owns(job, identity):
            if identity.get("pid") is None:
                stale.set()
                release.wait(timeout=3)
                return False
            return original_owns(job, identity)

        ready = self.workspace / "ready"
        self.manager.launch_grace = 0
        with mock.patch.object(self.manager, "_owns_worker", side_effect=owns):
            thread = threading.Thread(target=lambda: summaries.append(self.manager.status(job_id)))
            thread.start()
            self.assertTrue(stale.wait(timeout=2))
            with mock.patch.dict(os.environ, {"FIXTURE_DELAY": "3", "FIXTURE_READY": str(ready)}):
                original_launch(job, token)
            self.eventually(ready.exists)
            self.eventually(lambda: (job.directory / "state").read_text().strip() == "RUNNING")
            release.set()
            thread.join(timeout=3)
        identity = json.loads((job.directory / "worker.json").read_text())
        try:
            self.assertEqual(summaries[0]["status"], "RUNNING")
            self.assertFalse((job.directory / "result.json").exists())
        finally:
            if original_owns(job, identity):
                os.killpg(identity["pid"], signal.SIGKILL)

    def test_stop_kills_term_ignoring_descendant_after_provider_exits(self):
        ready = self.workspace / "descendant"
        job_id = self.start(FIXTURE_DELAY="30", FIXTURE_DESCENDANT_READY=str(ready))["job_id"]
        self.eventually(ready.exists)
        descendant = int(ready.read_text())
        original_killpg = os.killpg
        signals = []

        def killpg(pgid, sig):
            signals.append(sig)
            return original_killpg(pgid, sig)

        try:
            with mock.patch.object(rally_core.os, "killpg", side_effect=killpg):
                self.assertEqual(self.manager.stop(job_id)["status"], "STOPPED")
            self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        finally:
            try:
                os.kill(descendant, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_cli_wait_prints_single_result_and_read_prints_exact_response(self):
        job_id = self.start(FIXTURE_DELAY="0.2", FIXTURE_RESPONSE="answer\nwith newline")["job_id"]
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENT_JOB_DIR": str(self.store.root)}), contextlib.redirect_stdout(output):
            code = rally_core.main(["wait", job_id])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["response"], "answer\nwith newline")
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"AGENT_JOB_DIR": str(self.store.root)}), contextlib.redirect_stdout(output):
            code = rally_core.main(["read", job_id])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "answer\nwith newline")

    def test_cli_timeout_status_stop_and_list_use_managed_job(self):
        job_id = self.start(FIXTURE_DELAY="30")["job_id"]
        self.eventually(lambda: self.manager.status(job_id)["status"] == "RUNNING")
        for arguments, expected_code in ((["wait", job_id, "--timeout", "0"], 124), (["status", job_id], 0), (["list", "--cwd", str(self.workspace)], 0), (["stop", job_id], 130)):
            output = io.StringIO()
            with self.subTest(arguments=arguments), mock.patch.dict(os.environ, {"AGENT_JOB_DIR": str(self.store.root)}), contextlib.redirect_stdout(output):
                self.assertEqual(rally_core.main(arguments), expected_code)
            value = json.loads(output.getvalue())
            self.assertEqual((value[0] if isinstance(value, list) else value)["job_id"], job_id)

    def test_cli_rejects_invalid_management_arguments_without_creating_root(self):
        for arguments in (["wait", "../outside"], ["wait", "20261027T120000Z-abcdef123456", "--timeout", "nan"], ["status"], ["read", "../outside"], ["stop", "../outside"]):
            with self.subTest(arguments=arguments), mock.patch.dict(os.environ, {"AGENT_JOB_DIR": str(self.store.root)}), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rally_core.main(arguments), 2)
        self.assertFalse(self.store.root.exists())

    def test_private_fixture_is_unavailable_without_explicit_test_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("_fixture", rally_core.provider_adapters())
            with self.assertRaises(ValueError):
                self.request()

    def assertProcessAlive(self, pid: int) -> None:
        try:
            os.kill(pid, 0)
        except OSError:
            self.fail(f"process {pid} is not alive")
        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        self.assertTrue(bool(state) and not state.startswith("Z"), f"process {pid} is dead or zombie: {state}")

    def assertProcessTreeGone(self, pgid: int) -> None:
        self.assertFalse(rally_core._group_has_members(pgid), f"process group {pgid} still has members")

    def test_corrupt_metadata_reconciles_to_durable_error_and_cleans_launch_env(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        (job.directory / "metadata.json").write_text("{corrupt-json\n")
        job.launch_env.write_text(json.dumps({"SECRET": "test"}))
        self.assertTrue(os.path.exists(job.launch_env))
        self.manager.launch_grace = 0
        summary = self.manager.status(job_id)
        self.assertEqual(summary["status"], "ERROR")
        result = self.manager.wait(job_id)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "metadata_corrupt")
        self.assertFalse(os.path.exists(job.launch_env))
        self.assertEqual(self.manager.wait(job_id), result)

    def test_truncated_result_reconciles_to_durable_error(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        job.result.write_text('{"status": "SUCC')
        self.manager.launch_grace = 0
        summary = self.manager.status(job_id)
        self.assertEqual(summary["status"], "ERROR")
        result = self.manager.wait(job_id)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "result_corrupt")
        self.assertEqual(self.manager.result(job), result)
        self.assertEqual(self.manager.wait(job_id), result)

    def test_concurrent_status_and_wait_during_finalization(self):
        job_id = self.start(FIXTURE_DELAY="0.3")["job_id"]
        job = self.store.get(job_id)
        results = []
        errors = []
        barrier = threading.Barrier(4)

        def worker_wait():
            try:
                barrier.wait()
                results.append(self.manager.wait(job_id, timeout=5))
            except Exception as e:
                errors.append(e)

        def worker_status():
            try:
                barrier.wait()
                for _ in range(10):
                    s = self.manager.status(job_id)
                    self.assertIn(s["status"], ("STARTING", "RUNNING", "SUCCESS"))
                    time.sleep(0.02)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker_wait),
            threading.Thread(target=worker_wait),
            threading.Thread(target=worker_status),
            threading.Thread(target=worker_status),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["status"], "SUCCESS")
        self.assertEqual(results[0], results[1])
        final = self.manager.result(job)
        self.assertEqual(results[0], final)

    def test_stop_before_provider_launch_cleans_launch_env_and_prevents_execution(self):
        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        job.launch_env.write_text(json.dumps({"ENV": "val"}))
        self.assertTrue(os.path.exists(job.launch_env))
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        result = self.manager.stop(job_id)
        self.assertEqual(result["status"], "STOPPED")
        self.assertFalse(os.path.exists(job.launch_env))
        with mock.patch.object(FixtureAdapter, "build_command", side_effect=AssertionError("provider launched")):
            self.assertEqual(rally_core.worker_main(self.store.root, job_id, token, adapters={"_fixture": FixtureAdapter()}), 130)
        self.assertEqual((job.directory / "stdout.jsonl").read_text(), "")
        self.assertEqual(self.manager.result(job)["status"], "STOPPED")

    def test_stop_after_terminal_result_preserves_existing_result_and_cleans_launch_env(self):
        job_id = self.start()["job_id"]
        job = self.store.get(job_id)
        initial = self.manager.wait(job_id)
        self.assertEqual(initial["status"], "SUCCESS")
        self.assertFalse(os.path.exists(job.launch_env))
        stopped = self.manager.stop(job_id)
        self.assertEqual(stopped["status"], "SUCCESS")
        self.assertEqual(stopped, initial)
        self.assertEqual(self.manager.result(job), initial)
        self.assertFalse(os.path.exists(job.launch_env))

    def test_dead_manager_with_live_unrelated_pid_protects_unrelated_process(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"],
            start_new_session=True, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: (child.kill(), child.wait()) if child.poll() is None else None)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        unrelated_pid = child.pid
        self.assertProcessAlive(unrelated_pid)

        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        job.launch_env.write_text(json.dumps({"KEY": "SECRET"}))
        identity = json.loads((job.directory / "worker.json").read_text())
        identity.update(pid=unrelated_pid, pgid=unrelated_pid, executable=sys.executable)
        rally_core.atomic_write(job.directory / "worker.json", json.dumps(identity))

        self.manager.launch_grace = 0
        summary = self.manager.status(job_id)
        self.assertEqual(summary["status"], "ERROR")
        self.assertFalse(os.path.exists(job.launch_env))
        self.assertProcessAlive(unrelated_pid)

        result = self.manager.stop(job_id)
        self.assertEqual(result["status"], "ERROR")
        self.assertProcessAlive(unrelated_pid)

    def test_stop_descendants_ignoring_sigterm_escalates_to_kill_preserving_unrelated_pid(self):
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"],
            start_new_session=True, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: (unrelated.kill(), unrelated.wait()) if unrelated.poll() is None else None)
        self.assertEqual(unrelated.stdout.readline().strip(), "ready")
        unrelated_pid = unrelated.pid
        self.assertProcessAlive(unrelated_pid)

        ready, descendant = self.workspace / "ready", self.workspace / "descendant"
        job_id = self.start(
            FIXTURE_DELAY="30", FIXTURE_IGNORE_TERM="1",
            FIXTURE_READY=str(ready), FIXTURE_DESCENDANT_READY=str(descendant),
        )["job_id"]
        self.eventually(ready.exists)
        self.eventually(descendant.exists)
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())
        owned_pgid = identity["provider_pgid"]

        result = self.manager.stop(job_id)
        self.assertEqual(result["status"], "STOPPED")
        self.assertFalse(os.path.exists(job.launch_env))
        self.assertProcessTreeGone(owned_pgid)
        self.assertProcessAlive(unrelated_pid)

    def test_missing_birth_worker_record_is_not_owned_and_never_signaled(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"],
            start_new_session=True, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: (child.kill(), child.wait()) if child.poll() is None else None)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        unrelated_pid = child.pid
        self.assertProcessAlive(unrelated_pid)

        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        identity = json.loads(job.worker.read_text())
        identity.update(pid=unrelated_pid, pgid=unrelated_pid, executable=sys.executable)
        identity.pop("birth", None)
        rally_core.atomic_write(job.worker, json.dumps(identity))

        valid_command = [sys.executable, str(RUNNER), "--_worker", str(self.store.root), job_id, identity["token"]]
        with mock.patch.object(self.manager, "_process_argv", return_value=valid_command):
            self.assertFalse(self.manager._owns_worker(job, identity))

        with mock.patch.object(rally_core.os, "kill") as kill:
            self.manager.stop(job_id)
            kill.assert_not_called()
        self.assertProcessAlive(unrelated_pid)

    def test_missing_birth_provider_group_record_never_signals_group(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"],
            start_new_session=True, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: (child.kill(), child.wait()) if child.poll() is None else None)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        unrelated_pid = child.pid
        self.assertProcessAlive(unrelated_pid)

        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        identity = json.loads(job.worker.read_text())
        identity.update(
            pid=os.getpid(), pgid=os.getpgrp(), provider_pgid=unrelated_pid,
            runner=str(RUNNER), executable=str(Path(sys.executable).resolve()),
            provider_command=[sys.executable, "-c", "fake"],
            provider_process={"pid": unrelated_pid, "token": identity["token"], "executable": sys.executable},
        )
        rally_core.atomic_write(job.worker, json.dumps(identity))

        with mock.patch.object(self.manager, "_process_has_job_token", return_value=True), \
             mock.patch.object(rally_core.os, "killpg") as killpg:
            self.manager._cleanup_lost_group(identity)
            killpg.assert_not_called()
        self.assertProcessAlive(unrelated_pid)

    def test_stop_identity_replacement_between_term_loop_and_kill_skips_kill(self):
        worker = subprocess.Popen(
            [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"],
            start_new_session=True, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: (worker.kill(), worker.wait()) if worker.poll() is None else None)
        self.assertEqual(worker.stdout.readline().strip(), "ready")
        worker_pid = worker.pid
        self.assertProcessAlive(worker_pid)

        with mock.patch.object(self.manager, "_launch"):
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        worker_birth = rally_core._process_birth(worker_pid)
        identity = json.loads(job.worker.read_text())
        identity.update(
            pid=worker_pid, pgid=worker_pid, birth=worker_birth,
            executable=str(Path(sys.executable).resolve()),
        )
        rally_core.atomic_write(job.worker, json.dumps(identity))

        loop_checked = False
        original_owns = self.manager._owns_worker

        def owns_hook(j, ident):
            nonlocal loop_checked
            if not loop_checked:
                loop_checked = True
                rally_core.atomic_write(job.worker, json.dumps({"token": identity["token"], "pid": None, "birth": None}))
            return original_owns(j, ident)

        valid_command = [sys.executable, str(RUNNER), "--_worker", str(self.store.root), job_id, identity["token"]]
        with mock.patch.object(self.manager, "_process_argv", return_value=valid_command), \
             mock.patch.object(self.manager, "_owns_worker", side_effect=owns_hook), \
             mock.patch.object(rally_core.os, "kill") as kill_mock:
            self.manager.stop(job_id)
            kill_mock.assert_not_called()

    def test_provider_gate_cleanup_skips_terminate_when_birth_is_missing_or_mismatched(self):
        job_id = self.start()["job_id"]
        job = self.store.get(job_id)
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        if job_id in self.manager._children:
            self.manager._children[job_id].kill()
            self.manager._children[job_id].wait(timeout=2)

        spawned = []
        original_popen = subprocess.Popen

        def popen_wrapper(*args, **kwargs):
            proc = original_popen(*args, **kwargs)
            spawned.append(proc)
            return proc

        def cleanup_spawned():
            for proc in spawned:
                try:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=1)
                except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                    pass
                for stream in (proc.stdout, proc.stderr, proc.stdin):
                    if stream:
                        try:
                            stream.close()
                        except OSError:
                            pass

        self.addCleanup(cleanup_spawned)

        # 1. When birth is None (missing birth), terminate must not be called
        with mock.patch("rally_core.process.subprocess.Popen", side_effect=popen_wrapper), \
             mock.patch("rally_core.process.subprocess.Popen.terminate") as mock_terminate, \
             mock.patch("rally_core.process._process_birth", return_value=None):
            code = rally_core.worker_main(self.store.root, job_id, token, adapters=self.manager.adapters)
            self.assertEqual(code, 1)
            self.assertFalse(mock_terminate.called, "terminate must not be called when birth is missing")
        cleanup_spawned()

        # 2. When birth is mismatched (simulating PID reuse), terminate must not be called
        with mock.patch("rally_core.process.subprocess.Popen", side_effect=popen_wrapper), \
             mock.patch("rally_core.process.subprocess.Popen.terminate") as mock_terminate, \
             mock.patch("rally_core.process._process_birth", side_effect=["initial-birth", "reused-pid-birth"]):
            with mock.patch("rally_core.process.atomic_write", side_effect=[None, OSError("disk full")]):
                rally_core.worker_main(self.store.root, job_id, token, adapters=self.manager.adapters)
                self.assertFalse(mock_terminate.called, "terminate must not be called when birth is mismatched")
        cleanup_spawned()

    def test_start_launch_exception_redacts_credentials_and_paths(self):
        secret_path = "/Users/alice/private/sentinel-path/key.pem"
        error_msg = f"launch failed with {CREDENTIAL_SENTINEL} at {secret_path}"
        with mock.patch.object(self.manager, "_launch", side_effect=OSError(error_msg)):
            summary = self.manager.start(self.request())
        result = self.manager.result(self.store.get(summary["job_id"]))
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "launch_error")
        self.assertNotIn(CREDENTIAL_SENTINEL, json.dumps(result))
        self.assertNotIn(secret_path, json.dumps(result))
        self.assertIn("[redacted]", result["error"]["message"])
        self.assertIn("[path]", result["error"]["message"])

    def test_worker_main_outer_exception_redacts_credentials_and_paths(self):
        job_id = self.start()["job_id"]
        job = self.store.get(job_id)
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        if job_id in self.manager._children:
            self.manager._children[job_id].kill()
            self.manager._children[job_id].wait(timeout=2)

        secret_path = "/Users/alice/private/sentinel-path/key.pem"
        error_msg = f"worker failure with {CREDENTIAL_SENTINEL} at {secret_path}"
        with mock.patch("rally_core.process._restore_launch_environment", side_effect=ValueError(error_msg)):
            code = rally_core.worker_main(self.store.root, job_id, token, adapters=self.manager.adapters)
        self.assertEqual(code, 1)
        result = self.manager.result(job)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "worker_error")
        self.assertNotIn(CREDENTIAL_SENTINEL, json.dumps(result))
        self.assertNotIn(secret_path, json.dumps(result))
        self.assertIn("[redacted]", result["error"]["message"])
        self.assertIn("[path]", result["error"]["message"])

    def test_start_launch_exception_redacts_spaced_api_key_and_quoted_path_with_spaces(self):
        quoted_path = "/Users/example/My Project/private.txt"
        secret = "secret-token-xyz"
        error_msg = f"launch failed with API key: {secret} at '{quoted_path}'"
        with mock.patch.object(self.manager, "_launch", side_effect=OSError(error_msg)):
            summary = self.manager.start(self.request())
        result = self.manager.result(self.store.get(summary["job_id"]))
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "launch_error")
        message = result["error"]["message"]
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(quoted_path, json.dumps(result))
        self.assertNotIn("My Project", json.dumps(result))
        self.assertNotIn("private.txt", json.dumps(result))
        self.assertNotIn("/Users/example", json.dumps(result))
        self.assertIn("API key: [redacted]", message)
        self.assertIn("[path]", message)

    def test_worker_main_outer_exception_redacts_spaced_api_key_and_quoted_path_with_spaces(self):
        job_id = self.start()["job_id"]
        job = self.store.get(job_id)
        token = json.loads((job.directory / "worker.json").read_text())["token"]
        if job_id in self.manager._children:
            self.manager._children[job_id].kill()
            self.manager._children[job_id].wait(timeout=2)

        quoted_path = "/Users/example/My Project/private.txt"
        secret = "secret-token-xyz"
        error_msg = f"worker failure with API key: {secret} at '{quoted_path}'"
        with mock.patch("rally_core.process._restore_launch_environment", side_effect=ValueError(error_msg)):
            code = rally_core.worker_main(self.store.root, job_id, token, adapters=self.manager.adapters)
        self.assertEqual(code, 1)
        result = self.manager.result(job)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "worker_error")
        message = result["error"]["message"]
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(quoted_path, json.dumps(result))
        self.assertNotIn("My Project", json.dumps(result))
        self.assertNotIn("private.txt", json.dumps(result))
        self.assertNotIn("/Users/example", json.dumps(result))
        self.assertIn("API key: [redacted]", message)
        self.assertIn("[path]", message)

    def test_public_text_redacts_api_key_variants_and_quoted_paths_without_over_redacting_prose(self):
        # API key variants
        for label in ("API key:", "api key:", "Api Key =", "api-key:", "API_KEY:", "api_key ="):
            text = rally_core.process._public_text(f"Error with {label} secret_value_99")
            self.assertNotIn("secret_value_99", text)
            self.assertIn("[redacted]", text)
            clean_label = label.split()[0].rstrip(":=") if " " in label else label.rstrip(":=")
            self.assertIn(clean_label, text)

        # Quoted paths with spaces
        quoted_cases = (
            "'/Users/example/My Project/private.txt'",
            "\"/Users/example/My Project/private.txt\"",
            "'/Users/alice/Application Support/app/config.json'",
        )
        for path_str in quoted_cases:
            text = rally_core.process._public_text(f"Failed opening {path_str} for write")
            self.assertNotIn("private.txt", text)
            self.assertNotIn("My Project", text)
            self.assertNotIn("Application Support", text)
            self.assertIn("[path]", text)

        # Ordinary prose is preserved
        prose_cases = (
            "Select option yes/no to proceed",
            "Either / or decision needed",
            "Path calculation 10 / 5 = 2",
            "He said 'hello / world' to us",
        )
        for prose in prose_cases:
            self.assertEqual(rally_core.process._public_text(prose), prose)

    def test_detached_command_construction_and_module_bootstrap(self):
        # Checkout branch: runner exists as regular file
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "detached"}), \
             mock.patch("subprocess.Popen") as popen_mock:
            popen_mock.return_value = mock.MagicMock(pid=99999, wait=mock.MagicMock())
            job_id = self.manager.start(self.request())["job_id"]
        job = self.store.get(job_id)
        identity = json.loads((job.directory / "worker.json").read_text())
        call_args = popen_mock.call_args
        command = call_args.args[0]
        kwargs = call_args.kwargs
        self.assertEqual(command[:2], [sys.executable, str(RUNNER)])
        self.assertEqual(command[2], "--_worker")
        self.assertEqual(command[3:], [str(self.store.root), job_id, identity["token"]])
        self.assertEqual(identity["runner"], str(RUNNER))
        self.assertIn("PYTHONPATH", kwargs.get("env", {}))
        self.assertIn(str(ROOT), kwargs["env"]["PYTHONPATH"])

        # Installed branch: runner does not exist as regular file
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "detached"}), \
             mock.patch.object(rally_core.process, "RUNNER_PATH", self.workspace / "missing-runner"), \
             mock.patch("subprocess.Popen") as popen_mock_inst:
            popen_mock_inst.return_value = mock.MagicMock(pid=99998, wait=mock.MagicMock())
            job_id_inst = self.manager.start(self.request())["job_id"]
        job_inst = self.store.get(job_id_inst)
        identity_inst = json.loads((job_inst.directory / "worker.json").read_text())
        call_args_inst = popen_mock_inst.call_args
        command_inst = call_args_inst.args[0]
        self.assertEqual(command_inst[:3], [sys.executable, "-m", "rally_core"])
        self.assertEqual(command_inst[3], "--_worker")
        self.assertEqual(command_inst[4:], [str(self.store.root), job_id_inst, identity_inst["token"]])
        self.assertEqual(identity_inst["runner"], "rally_core")

    def test_worker_command_selector_branches(self):
        # Checkout branch: existing regular file
        custom_runner = self.workspace / "custom-runner"
        custom_runner.write_text("#!/usr/bin/env python3\n")
        custom_runner.chmod(0o755)
        cmd_checkout = rally_core.process._worker_command(self.store.root, "job-1", "token-1", runner_path=custom_runner)
        self.assertEqual(cmd_checkout, [sys.executable, str(custom_runner), "--_worker", str(self.store.root), "job-1", "token-1"])
        self.assertEqual(rally_core.process._worker_runner(runner_path=custom_runner), str(custom_runner))

        # Installed branch: nonexistent path
        missing_runner = self.workspace / "no-such-runner"
        cmd_installed = rally_core.process._worker_command(self.store.root, "job-2", "token-2", runner_path=missing_runner)
        self.assertEqual(cmd_installed, [sys.executable, "-m", "rally_core", "--_worker", str(self.store.root), "job-2", "token-2"])
        self.assertEqual(rally_core.process._worker_runner(runner_path=missing_runner), "rally_core")

    def test_launchctl_source_checkout_worker_lifecycle_from_foreign_cwd(self):
        fake_state_dir = self.workspace / "launchctl_state"
        fake_state_dir.mkdir()
        fake_launchctl = self.workspace / "fake_launchctl"
        fake_launchctl.write_text(
            f"#!{sys.executable}\n"
            "import os, sys, subprocess, signal\n"
            "from pathlib import Path\n"
            f"state_dir = Path({str(fake_state_dir)!r})\n"
            "args = sys.argv[1:]\n"
            "if not args:\n"
            "    sys.exit(1)\n"
            "action = args[0]\n"
            "if action == 'submit':\n"
            "    label = args[args.index('-l') + 1]\n"
            "    cmd = args[args.index('--') + 1:]\n"
            "    clean_env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}\n"
            f"    proc = subprocess.Popen(cmd, cwd={str(self.workspace)!r}, env=clean_env, start_new_session=True)\n"
            "    (state_dir / f'{label}.pid').write_text(str(proc.pid))\n"
            "    sys.exit(0)\n"
            "elif action == 'list':\n"
            "    label = args[1]\n"
            "    pid_file = state_dir / f'{label}.pid'\n"
            "    if not pid_file.exists():\n"
            "        sys.exit(1)\n"
            "    try:\n"
            "        pid = int(pid_file.read_text().strip())\n"
            "        os.kill(pid, 0)\n"
            "        sys.exit(0)\n"
            "    except (OSError, ValueError):\n"
            "        sys.exit(1)\n"
            "elif action == 'remove':\n"
            "    label = args[1]\n"
            "    pid_file = state_dir / f'{label}.pid'\n"
            "    if pid_file.exists():\n"
            "        try:\n"
            "            pid = int(pid_file.read_text().strip())\n"
            "            os.kill(pid, signal.SIGTERM)\n"
            "        except OSError:\n"
            "            pass\n"
            "        pid_file.unlink(missing_ok=True)\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        fake_launchctl.chmod(0o755)

        foreign_cwd = self.workspace / "foreign_project"
        foreign_cwd.mkdir()
        env = {
            "AGENT_JOB_LAUNCHER": "launchctl",
            "AGENT_JOB_LAUNCHCTL": str(fake_launchctl),
        }
        with mock.patch.dict(os.environ, env):
            req = self.request(cwd=foreign_cwd)
            summary = self.manager.start(req)
            job_id = summary["job_id"]
            result = self.manager.wait(job_id, timeout=10.0)
            self.assertEqual(result["status"], "SUCCESS")
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["response"], "final answer")

    def test_main_module_execution_prints_usage(self):
        proc = subprocess.run([sys.executable, "-m", "rally_core", "--help"],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage: rally", proc.stdout)

    def test_source_checkout_worker_lifecycle_from_foreign_cwd(self):
        foreign_cwd = self.workspace / "foreign_project"
        foreign_cwd.mkdir()
        req = self.request(cwd=foreign_cwd)
        summary = self.manager.start(req)
        job_id = summary["job_id"]
        result = self.manager.wait(job_id, timeout=10.0)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["exit_code"], 0)

    def test_isolated_installed_layout_lifecycle_without_checkout_executables(self):
        install_root = self.workspace / "installed_env"
        site_packages = install_root / "lib" / "python" / "site-packages"
        bin_dir = install_root / "bin"
        site_packages.mkdir(parents=True)
        bin_dir.mkdir(parents=True)

        # Copy rally_core into site-packages
        shutil.copytree(ROOT / "rally_core", site_packages / "rally_core")

        # Crucial check: verify that <package_parent>/rally does NOT exist in the installed layout
        installed_runner_path = site_packages / "rally"
        self.assertFalse(installed_runner_path.exists())

        # Create installed entrypoint console script (similar to pip-generated script)
        script = bin_dir / "rally"
        script.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "from rally_core.cli import main\n"
            "if __name__ == '__main__':\n"
            "    sys.exit(main())\n"
        )
        script.chmod(0o755)

        # Work in an isolated project directory
        proj_dir = self.workspace / "installed_proj"
        proj_dir.mkdir()
        fake_bin = self.workspace / "fake_bin"
        fake_bin.mkdir()
        fake_agy = fake_bin / "agy"
        fake_agy.write_text(f"#!{sys.executable}\nimport sys\nprint('{{\"status\":\"SUCCESS\",\"response\":\"INSTALLED_OK\",\"conversation_id\":\"conv-1\",\"usage\":{{}}}}')\n")
        fake_agy.chmod(0o755)

        # Run with isolated PATH and PYTHONPATH, with checkout completely absent
        env = {
            "PATH": f"{bin_dir}:{fake_bin}:{os.environ.get('PATH', '')}",
            "PYTHONPATH": str(site_packages),
            "AGENT_JOB_LAUNCHER": "detached",
        }
        # Run rally via the installed entrypoint script (both run and start/wait)
        proc = subprocess.run(
            [str(script), "agy", "run", "--prompt", "test installed execution"],
            cwd=proj_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stdout: {proc.stdout}, stderr: {proc.stderr}")
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["response"], "INSTALLED_OK")

        # Also test start + wait lifecycle under installed layout
        start_proc = subprocess.run(
            [str(script), "agy", "start", "--prompt", "test installed start"],
            cwd=proj_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(start_proc.returncode, 0, f"stdout: {start_proc.stdout}, stderr: {start_proc.stderr}")
        start_desc = json.loads(start_proc.stdout)
        job_id = start_desc["job_id"]
        wait_proc = subprocess.run(
            [str(script), "wait", job_id],
            cwd=proj_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(wait_proc.returncode, 0, f"stdout: {wait_proc.stdout}, stderr: {wait_proc.stderr}")
        wait_result = json.loads(wait_proc.stdout)
        self.assertEqual(wait_result["status"], "SUCCESS")
        self.assertEqual(wait_result["response"], "INSTALLED_OK")


class AgyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.bin = self.workspace / "bin"
        self.bin.mkdir()
        self.args_file = self.workspace / "args.json"
        executable = self.bin / "agy"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "open(os.environ['FAKE_AGY_ARGS'], 'w').write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
            "print(os.environ.get('FAKE_AGY_OUTPUT', '{\"status\":\"SUCCESS\",\"response\":\"AGY_OK\",\"conversation_id\":\"conversation-1\",\"usage\":{\"tokens\":3}}'))\n"
            "sys.exit(int(os.environ.get('FAKE_AGY_EXIT', '0')))\n"
        )
        executable.chmod(0o700)
        self.root = self.workspace / "jobs"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "AGENT_JOB_DIR": str(self.root), "AGENT_JOB_LAUNCHER": "detached",
                    "FAKE_AGY_ARGS": str(self.args_file)}

    def command(self, *args, environment=None):
        return subprocess.run([sys.executable, str(RUNNER), *args], cwd=self.workspace,
                              env={**self.env, **(environment or {})}, text=True,
                              capture_output=True, timeout=10)

    def run_job(self, *args, environment=None):
        return self.command("agy", "run", "--prompt", "literal `x`; $(no-eval)\nsecond line", *args,
                            environment=environment)

    def test_alias_defaults_exact_argv_and_normalized_session_usage(self):
        extra = self.workspace / "extra directory"
        extra.mkdir()
        completed = self.run_job("--model", "flash", "--add-dir", str(extra))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["provider"], "agy")
        self.assertEqual(result["model"], "gemini-3.8-flash-high")
        self.assertEqual(result["effort"], "high")
        self.assertEqual(result["response"], "AGY_OK")
        self.assertEqual(result["usage"], {"tokens": 3})
        self.assertEqual(result["session_id"], "conversation-1")
        captured = json.loads(self.args_file.read_text())
        self.assertEqual(captured, {"cwd": str(self.workspace), "argv": [
            "--dangerously-skip-permissions", "--output-format", "json", "--model", "gemini-3.8-flash-high",
            "--effort", "high", "--add-dir", str(self.workspace), "--add-dir", str(extra),
            "--new-project", "--prompt", "literal `x`; $(no-eval)\nsecond line"]})
        metadata = json.loads((Path(result["transcript_dir"]) / "result.json").read_text())
        self.assertEqual(metadata["session_id"], "conversation-1")

    def test_project_and_conversation_coexist_and_session_alias_resumes(self):
        for flag in ("--conversation", "--session"):
            with self.subTest(flag=flag):
                completed = self.run_job("--project", "project-1", flag, "conversation-0", "--effort", "max")
                self.assertEqual(completed.returncode, 0, completed.stderr)
                argv = json.loads(self.args_file.read_text())["argv"]
                self.assertIn("claude-sonnet-5-5-high", argv)
                self.assertEqual(argv[argv.index("--project") + 1], "project-1")
                self.assertEqual(argv[argv.index("--conversation") + 1], "conversation-0")
                self.assertNotIn("--new-project", argv)

    def test_invalid_option_combinations_fail_before_creation(self):
        for flags in (["--new-project", "--project", "p"], ["--new-project", "--conversation", "c"],
                      ["--new-project", "--session", "c"], ["--conversation", "c", "--session", "other"],
                      ["--project", "bad\nproject"], ["--name", "unsupported"], ["--effort", "minimal"]):
            with self.subTest(flags=flags):
                completed = self.run_job(*flags)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertFalse(self.root.exists())

    def test_whole_pretty_and_noisy_jsonl_select_terminal_provider_object(self):
        terminal = {"status": "SUCCESS", "response": "final", "conversation_id": "session"}
        for raw in (json.dumps(terminal), json.dumps(terminal, indent=2),
                    'diagnostic before\n{"event":"started"}\n' + json.dumps(terminal) + '\nnot JSON\n{"diagnostic":true}',
                    'diagnostic before\n' + json.dumps(terminal, indent=2) + '\ndiagnostic after'):
            with self.subTest(raw=raw):
                completed = self.run_job(environment={"FAKE_AGY_OUTPUT": raw})
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["provider_result"], terminal)

    def test_success_without_resumable_conversation_id_is_allowed(self):
        for raw in (
            '{"status":"SUCCESS","response":"no session","usage":{"tokens":1}}',
            '{"status":"SUCCESS","response":"no session","conversation_id":null,"usage":{"tokens":1}}',
            '{"status":"SUCCESS","response":"no session","conversation_id":"","usage":{"tokens":1}}',
        ):
            with self.subTest(raw=raw):
                completed = self.run_job(environment={"FAKE_AGY_OUTPUT": raw})
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["response"], "no session")
                self.assertIsNone(result["session_id"])

    def test_invalid_conversation_id_control_characters_still_fail(self):
        raw = '{"status":"SUCCESS","response":"bad id","conversation_id":"bad\\nid","usage":{"tokens":1}}'
        completed = self.run_job(environment={"FAKE_AGY_OUTPUT": raw})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "parse_error")

    def test_semantic_error_process_failure_and_malformed_output_are_errors(self):
        fixtures = [({"FAKE_AGY_OUTPUT": '{"status":"ERROR","error":"quota exhausted","conversation_id":"failed-session"}'}, 1, "quota_error"),
                    ({"FAKE_AGY_EXIT": "7"}, 7, "provider_exit"),
                    ({"FAKE_AGY_OUTPUT": 'diagnostic\n{"unrelated":true}\n{'}, 1, "parse_error")]
        for environment, code, kind in fixtures:
            with self.subTest(environment=environment):
                completed = self.run_job(environment=environment)
                self.assertEqual(completed.returncode, code, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual((result["status"], result["exit_code"]), ("ERROR", code))
                self.assertEqual(result["error"]["type"], kind)
                if kind in ("provider_error", "quota_error"):
                    self.assertEqual(result["session_id"], "failed-session")
                    self.assertIn("quota exhausted", result["error"]["message"])

    def test_json_diagnostic_with_structured_status_does_not_hide_terminal_result(self):
        raw = self.workspace / "stdout.jsonl"
        raw.write_text('{"status":"ERROR","error":"earlier failure"}\n'
                       '{"status":"SUCCESS","response":"final"}\n'
                       '{"status":{"diagnostic":true}}\n')
        outcome = rally_core.AgyAdapter().parse(raw, 0)
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.response, "final")

    def test_bare_nonzero_exit_without_result_classifies_as_provider_exit(self):
        raw = self.workspace / "bare-exit.jsonl"
        raw.write_text("")
        outcome = rally_core.AgyAdapter().parse(raw, 7)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "provider_exit")
        self.assertEqual(outcome.error["message"], "agy exited with status 7")
        completed = self.run_job(environment={"FAKE_AGY_OUTPUT": "", "FAKE_AGY_EXIT": "7"})
        self.assertEqual(completed.returncode, 7, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["exit_code"], result["error"]["type"]), ("ERROR", 7, "provider_exit"))

    def test_current_success_and_resume_fixtures_have_exact_response_session_and_bounded_usage(self):
        for name, response, usage in (
                ("agy-synthetic-success.jsonl", "AGY_FIXTURE_OK",
                 {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}),
                ("agy-synthetic-resume.jsonl", "AGY_FIXTURE_RESUME_OK",
                 {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25})):
            with self.subTest(name=name):
                outcome = parse_provider_fixture(self, "agy", name)
                self.assertTrue(outcome.success)
                self.assertEqual((outcome.response, outcome.session_id), (response, "agy-fixture-session"))
                self.assertEqual(outcome.usage, usage)
                self.assertNotIn(CREDENTIAL_SENTINEL, json.dumps(outcome.provider_result))

    def test_structured_quota_failure_keeps_provider_error_without_credentials(self):
        outcome = parse_provider_fixture(self, "agy", "agy-synthetic-quota-error.jsonl", 1)
        assert_public_failure(self, outcome, "quota_error")
        self.assertEqual(outcome.session_id, "agy-fixture-session")
        self.assertIn("Usage limit reached", outcome.error["message"])

    def test_truncated_and_no_result_streams_with_zero_exit_are_parse_errors(self):
        for name in ("agy-synthetic-truncated.jsonl", "agy-synthetic-no-result.jsonl"):
            with self.subTest(name=name):
                assert_public_failure(self, parse_provider_fixture(self, "agy", name), "parse_error")

    def test_no_result_diagnostics_classify_quota_and_auth_without_publishing_text(self):
        for text, kind in (("Usage limit reached. token=credential-sentinel", "quota_error"),
                           ("Error: not logged in; Bearer credential-sentinel", "auth_error")):
            for exit_code in (0, 1):
                with self.subTest(kind=kind, exit_code=exit_code):
                    raw = self.workspace / "diagnostic.jsonl"
                    raw.write_text(text + "\n")
                    outcome = rally_core.AgyAdapter().parse(raw, exit_code)
                    assert_public_failure(self, outcome, kind)
                    self.assertEqual(outcome.error["message"], f"agy {'quota' if kind == 'quota_error' else 'authentication'} unavailable")

    def test_assistant_text_cannot_masquerade_as_quota_diagnostics(self):
        raw = self.workspace / "assistant-quota.jsonl"
        raw.write_text('{"event":"progress","message":"usage limit unauthorized quota"}\n{"status":')
        assert_public_failure(self, rally_core.AgyAdapter().parse(raw, 0), "parse_error")

    def test_truncated_utf8_tail_and_deep_nesting_are_parse_errors_not_exceptions(self):
        raw = self.workspace / "bytes.jsonl"
        for content in (b'{"status":"SUCCESS","response":"caf\xc3', b"[" * 5000 + b"\n"):
            with self.subTest(content=content[:20]):
                raw.write_bytes(content)
                assert_public_failure(self, rally_core.AgyAdapter().parse(raw, 0), "parse_error")

    def test_error_messages_are_redacted_single_line_and_bounded(self):
        raw = self.workspace / "long-error.jsonl"
        message = "line one\nAuthorization: Bearer credential-sentinel " + "x" * 600
        raw.write_text(json.dumps({"status": "ERROR", "error": {"message": message, "code": "api_key=credential-sentinel",
                                                                  "type": "quota", "status": 429, "stderr": "credential-sentinel"}}))
        outcome = rally_core.AgyAdapter().parse(raw, 1)
        assert_public_failure(self, outcome, "quota_error")
        self.assertLessEqual(len(outcome.error["message"]), 300)
        self.assertNotIn("\n", outcome.error["message"])
        self.assertEqual(outcome.error["status"], 429)

    def test_zero_exit_truncated_result_is_durable_parse_error_with_recovered_session(self):
        raw = '{"conversation_id":"agy-fixture-session"}\n' + (FIXTURES / "agy-synthetic-truncated.jsonl").read_text()
        completed = self.run_job(environment={"FAKE_AGY_OUTPUT": raw})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["error"]["type"]), ("ERROR", "parse_error"))
        self.assertEqual(result["session_id"], "agy-fixture-session")
        self.assertNotIn(CREDENTIAL_SENTINEL, json.dumps(result))

    def test_followup_resumes_persisted_session_and_preserves_source(self):
        completed = self.run_job("--project", "project-1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        directory = Path(source["transcript_dir"])
        before = (directory / "result.json").read_bytes()
        followed = self.command("followup", source["job_id"], "--prompt", "next prompt")
        self.assertEqual(followed.returncode, 0, followed.stderr)
        descriptor = json.loads(followed.stdout)
        self.assertNotEqual(descriptor["job_id"], source["job_id"])
        waited = self.command("wait", descriptor["job_id"], "--timeout", "5")
        self.assertEqual(waited.returncode, 0, waited.stderr)
        result = json.loads(waited.stdout)
        self.assertEqual(result["source_job_id"], source["job_id"])
        argv = json.loads(self.args_file.read_text())["argv"]
        self.assertEqual(argv[argv.index("--conversation") + 1], "conversation-1")
        self.assertEqual(argv[argv.index("--project") + 1], "project-1")
        self.assertNotIn("--new-project", argv)
        self.assertEqual((directory / "result.json").read_bytes(), before)

    def test_followup_without_resumable_session_creates_no_job(self):
        completed = self.run_job(environment={"FAKE_AGY_OUTPUT": '{"status":"SUCCESS","response":"no session"}'})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        before = set(self.root.iterdir())
        followed = self.command("followup", source["job_id"], "--prompt", "next")
        self.assertEqual(followed.returncode, 2)
        self.assertIn("session", followed.stderr)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_generic_prompt_file_and_followup_preserve_cr_crlf(self):
        prompt = "first\r\nsecond\rthird\n\n"
        prompt_file = self.workspace / "mixed-newlines.txt"
        prompt_file.write_bytes(prompt.encode("utf-8"))
        completed = self.command("agy", "run", "--prompt-file", str(prompt_file))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        self.assertEqual((Path(source["transcript_dir"]) / "prompt.txt").read_bytes(), prompt.encode("utf-8"))
        self.assertEqual(json.loads(self.args_file.read_text())["argv"][-1], prompt)
        followed = self.command("followup", source["job_id"], "--prompt-file", str(prompt_file))
        self.assertEqual(followed.returncode, 0, followed.stderr)
        descriptor = json.loads(followed.stdout)
        waited = self.command("wait", descriptor["job_id"], "--timeout", "5")
        self.assertEqual(waited.returncode, 0, waited.stderr)
        self.assertEqual((Path(descriptor["transcript_dir"]) / "prompt.txt").read_bytes(), prompt.encode("utf-8"))
        self.assertEqual(json.loads(self.args_file.read_text())["argv"][-1], prompt)

    def test_stderr_only_quota_and_auth_failures_normalize_without_publishing_text(self):
        empty_raw = self.workspace / "empty-stdout.jsonl"
        empty_raw.write_text("")
        stderr_file = self.workspace / "test-stderr.log"
        req = rally_core.JobRequest(provider="agy", prompt="hello", cwd=self.workspace)
        adapter = rally_core.AgyAdapter()

        stderr_file.write_text("Usage limit reached. Authorization: Bearer credential-sentinel\n")
        outcome = adapter.parse(req, empty_raw, stderr_file, 1)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "quota_error")
        self.assertEqual(outcome.error["message"], "agy quota unavailable")
        self.assertNotIn("credential-sentinel", json.dumps(outcome.error))
        self.assertNotIn("Usage limit reached", json.dumps(outcome.error))

        stderr_file.write_text("Error: not logged in; Unauthorized\n")
        outcome = adapter.parse(req, empty_raw, stderr_file, 1)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "auth_error")
        self.assertEqual(outcome.error["message"], "agy authentication unavailable")


class CopilotAdapterTests(unittest.TestCase):
    """Copilot 1.0.73 streams captured 2026-10-27, with synthetic failure edits."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.bin = self.workspace / "bin"
        self.bin.mkdir()
        self.args_file = self.workspace / "arguments.json"
        fixtures = Path(__file__).parent / "fixtures" / "rally"
        self.raw = (fixtures / "copilot-1.0.73-success.jsonl").read_text()
        self.events = [json.loads(line) for line in self.raw.splitlines()]
        self.terminal = self.events[-1]
        self.invalid_model = (fixtures / "copilot-1.0.73-invalid-model.jsonl").read_text()
        self.resumed = (fixtures / "copilot-1.0.73-resume.jsonl").read_text()
        executable = self.bin / "copilot"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "open(os.environ['FAKE_COPILOT_ARGS'], 'w').write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
            "print(os.environ['FAKE_COPILOT_OUTPUT'])\n"
            "print(os.environ.get('FAKE_COPILOT_STDERR', ''), file=sys.stderr)\n"
            "sys.exit(int(os.environ.get('FAKE_COPILOT_EXIT', '0')))\n"
        )
        executable.chmod(0o700)
        self.root = self.workspace / "jobs"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "AGENT_JOB_DIR": str(self.root), "AGENT_JOB_LAUNCHER": "detached",
                    "FAKE_COPILOT_ARGS": str(self.args_file), "FAKE_COPILOT_OUTPUT": self.raw}
        self.prompt = "literal `x`; $(no-eval)\nsecond line"

    def command(self, *args, environment=None):
        return subprocess.run([sys.executable, str(RUNNER), *args], cwd=self.workspace,
                              env={**self.env, **(environment or {})}, text=True,
                              capture_output=True, timeout=10)

    def run_job(self, *args, environment=None):
        return self.command("copilot", "run", "--prompt", self.prompt, *args, environment=environment)

    @staticmethod
    def stream(events):
        return "\n".join(json.dumps(event) for event in events)

    def test_defaults_exact_argv_and_normalized_final_session_usage_model(self):
        completed = self.run_job()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["provider"], result["status"], result["response"]),
                         ("copilot", "SUCCESS", "COPILOT_FIXTURE_OK"))
        self.assertEqual(result["session_id"], self.terminal["sessionId"])
        self.assertEqual(result["usage"], {"premiumRequests": 0, "totalApiDurationMs": 1842,
                                           "sessionDurationMs": 3997,
                                           "codeChanges": {"linesAdded": 0, "linesRemoved": 0}})
        self.assertEqual(result["model"], "mai-code-1.1-flash")
        self.assertIsNone(result["effort"])
        self.assertEqual(result["provider_result"], {**self.terminal, "usage": result["usage"]})
        request = json.loads((Path(result["transcript_dir"]) / "request.json").read_text())
        self.assertEqual((request["model"], request["effort"]), ("auto", None))
        self.assertEqual(json.loads(self.args_file.read_text()), {"cwd": str(self.workspace), "argv": [
            "--prompt", self.prompt, "--output-format", "json", "--stream", "off", "--allow-all",
            "--no-ask-user", "--no-auto-update", "--no-remote-export", "-C", str(self.workspace),
            "--model", "auto"]})

    def test_explicit_model_effort_name_session_directories_and_credit_mapping(self):
        extra = self.workspace / "extra directory"
        extra.mkdir()
        completed = self.run_job("--model", "gpt-5.4", "--effort", "high", "--name", "session name",
                                 "--session", "prior-session", "--add-dir", str(extra), "--max-credits", "30.5")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        argv = json.loads(self.args_file.read_text())["argv"]
        self.assertEqual(argv[12:], ["--model", "gpt-5.4", "--effort", "high", "--name", "session name",
                                    "--resume=prior-session", "--add-dir", str(extra), "--max-ai-credits", "30.5"])
        result = json.loads(completed.stdout)
        request = json.loads((Path(result["transcript_dir"]) / "request.json").read_text())
        self.assertEqual(request["provider_options"], {"max_credits": "30.5"})

    def test_invalid_option_combinations_fail_before_job_creation(self):
        for flags in (["--effort", "high"], ["--model", "auto", "--effort", "none"],
                      ["--project", "p"], ["--new-project"], ["--conversation", "c"]):
            with self.subTest(flags=flags):
                completed = self.run_job(*flags)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertFalse(self.root.exists())

    def test_credit_domain_is_finite_at_least_thirty_and_copilot_only(self):
        for value in ("0", "-1", "29.99", "nan", "NaN", "inf", "1e309", "no", "30\n", "30_0", "٣٠"):
            with self.subTest(value=value):
                completed = self.run_job("--max-credits=" + value)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertFalse(self.root.exists())
        for provider in ("agy", "codex", "claude"):
            completed = self.command(provider, "--prompt", "text", "--max-credits", "30")
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(self.root.exists())

    def test_non_json_mcp_noise_and_unrelated_messages_do_not_replace_last_final(self):
        events = self.events[:-1] + [
            {"type": "assistant.message", "data": {"phase": "final_answer", "content": "last final"}},
            {"type": "assistant.message", "data": {"phase": "commentary", "content": "not final"}},
            {"type": "assistant.message_delta", "data": {"deltaContent": "not final either"}},
            self.terminal, {"type": "diagnostic", "data": ["unrelated"]}, ["not an event"],
        ]
        raw = "MCP startup diagnostic\n" + self.stream(events) + "\nnot JSON\n{\n"
        completed = self.run_job(environment={"FAKE_COPILOT_OUTPUT": raw})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["response"], "last final")

    def test_process_exit_and_result_exit_both_required_for_success(self):
        for environment, expected_code, error_type in (
                ({"FAKE_COPILOT_EXIT": "7"}, 7, "provider_exit"),
                ({"FAKE_COPILOT_OUTPUT": self.stream(self.events[:-1] + [{**self.terminal, "exitCode": 2}])},
                 1, "provider_error")):
            with self.subTest(environment=environment):
                completed = self.run_job(environment=environment)
                self.assertEqual(completed.returncode, expected_code, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual((result["status"], result["exit_code"]), ("ERROR", expected_code))
                self.assertEqual(result["error"]["type"], error_type)
                self.assertEqual(result["session_id"], self.terminal["sessionId"])

    def test_bare_nonzero_exit_without_result_classifies_as_provider_exit(self):
        raw = self.workspace / "bare-exit.jsonl"
        raw.write_text("")
        outcome = rally_core.CopilotAdapter().parse(raw, 7)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "provider_exit")
        self.assertEqual(outcome.error["message"], "copilot exited with status 7")
        completed = self.run_job(environment={"FAKE_COPILOT_OUTPUT": "", "FAKE_COPILOT_EXIT": "7"})
        self.assertEqual(completed.returncode, 7, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["exit_code"], result["error"]["type"]), ("ERROR", 7, "provider_exit"))

    def test_current_success_and_resume_fixtures_have_exact_response_session_and_bounded_usage(self):
        for name, response, usage in (
                ("copilot-1.0.73-success.jsonl", "COPILOT_FIXTURE_OK",
                 {"premiumRequests": 0, "totalApiDurationMs": 1842, "sessionDurationMs": 3997,
                  "codeChanges": {"linesAdded": 0, "linesRemoved": 0}}),
                ("copilot-1.0.73-resume.jsonl", "COPILOT_FIXTURE_RESUME_OK",
                 {"premiumRequests": 0, "totalApiDurationMs": 3129, "sessionDurationMs": 28233,
                  "codeChanges": {"linesAdded": 0, "linesRemoved": 0}})):
            with self.subTest(name=name):
                outcome = parse_provider_fixture(self, "copilot", name)
                self.assertTrue(outcome.success)
                self.assertEqual((outcome.response, outcome.session_id, outcome.model),
                                 (response, "copilot-fixture-session", "mai-code-1.1-flash"))
                self.assertEqual(outcome.usage, usage)

    def test_structured_quota_failure_keeps_provider_error_without_credentials(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                outcome = parse_provider_fixture(self, "copilot", "copilot-1.0.73-quota-error.jsonl", exit_code)
                assert_public_failure(self, outcome, "quota_error")
                self.assertEqual(outcome.session_id, "copilot-fixture-session")
                self.assertIn("quota exceeded", outcome.error["message"])

    def test_truncated_and_no_result_streams_with_zero_exit_are_parse_errors(self):
        for name in ("copilot-1.0.73-truncated.jsonl", "copilot-1.0.73-no-result.jsonl"):
            with self.subTest(name=name):
                outcome = parse_provider_fixture(self, "copilot", name)
                assert_public_failure(self, outcome, "parse_error")
                self.assertEqual(outcome.session_id, "copilot-fixture-session")
        outcome = parse_provider_fixture(self, "copilot", "copilot-1.0.73-invalid-model.jsonl", 1)
        assert_public_failure(self, outcome, "provider_exit")

    def test_no_result_diagnostics_classify_quota_and_auth_without_publishing_text(self):
        for text, kind, public in (("Usage limit reached. token=credential-sentinel", "quota_error", "quota"),
                                   ("Error: not logged in; Bearer credential-sentinel", "auth_error", "authentication")):
            for exit_code in (0, 1):
                with self.subTest(kind=kind, exit_code=exit_code):
                    raw = self.workspace / "diagnostic.jsonl"
                    raw.write_text(text + "\n" + self.stream(self.events[:3]) + "\n")
                    outcome = rally_core.CopilotAdapter().parse(raw, exit_code)
                    assert_public_failure(self, outcome, kind)
                    self.assertEqual(outcome.error["message"], f"copilot {public} unavailable")

    def test_hostile_stream_tails_are_parse_errors_not_exceptions(self):
        raw = self.workspace / "hostile.jsonl"
        for content in (self.raw.encode()[:-40] + "é".encode()[:1], b"[" * 5000 + b"\n" + self.raw.encode()[:300]):
            with self.subTest(content=content[-20:]):
                raw.write_bytes(content)
                assert_public_failure(self, rally_core.CopilotAdapter().parse(raw, 0), "parse_error")

    def test_error_messages_are_redacted_single_line_and_bounded(self):
        message = "line one\nAuthorization: Bearer credential-sentinel " + "x" * 600
        for terminal in ({**self.terminal, "error": {"message": message, "code": "api_key=credential-sentinel"}},
                         {**self.terminal, "error": message}):
            with self.subTest(terminal=type(terminal["error"])):
                outcome = self.parse_events(self.events[:-1] + [terminal])
                assert_public_failure(self, outcome, "provider_error")
                self.assertLessEqual(len(outcome.error["message"]), 300)
                self.assertNotIn("\n", outcome.error["message"])

    def parse_events(self, events, exit_code=0):
        raw = self.workspace / "events.jsonl"
        raw.write_text(self.stream(events))
        return rally_core.CopilotAdapter().parse(raw, exit_code)

    def test_zero_exit_truncated_result_is_durable_parse_error_with_recovered_session(self):
        raw = (FIXTURES / "copilot-1.0.73-truncated.jsonl").read_text()
        completed = self.run_job(environment={"FAKE_COPILOT_OUTPUT": raw})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["error"]["type"]), ("ERROR", "parse_error"))
        self.assertEqual(result["session_id"], "copilot-fixture-session")

    def test_result_and_nonempty_final_response_are_required(self):
        for events in (self.events[:-1], [self.terminal],
                       [{"type": "assistant.message", "data": {"phase": "final_answer", "content": "  \n"}}, self.terminal],
                       self.events[:-1] + [{**self.terminal, "exitCode": False}],
                       self.events[:-1] + [{key: value for key, value in self.terminal.items() if key != "exitCode"}]):
            with self.subTest(events=events):
                completed = self.run_job(environment={"FAKE_COPILOT_OUTPUT": self.stream(events)})
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["status"], "ERROR")

    def test_structured_provider_errors_override_zero_process_and_result_exit(self):
        for events in (self.events[:-1] + [{**self.terminal, "error": {"message": "quota exhausted"}}],
                       self.events[:-1] + [{"type": "session.error", "data": {"message": "quota exhausted"}}, self.terminal]):
            completed = self.run_job(environment={"FAKE_COPILOT_OUTPUT": self.stream(events)})
            self.assertEqual(completed.returncode, 1, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "ERROR")
            self.assertEqual(result["error"]["type"], "quota_error")
            self.assertIn("quota exhausted", result["error"]["message"])

    def test_empty_session_error_event_overrides_successful_message_and_exits(self):
        events = self.events[:-1] + [{"type": "session.error", "data": {}}, self.terminal]
        completed = self.run_job(environment={"FAKE_COPILOT_OUTPUT": self.stream(events)})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["exit_code"]), ("ERROR", 1))
        self.assertEqual(result["error"], {"type": "provider_error", "message": "Copilot reported session.error"})
        self.assertEqual(result["response"], "COPILOT_FIXTURE_OK")
        self.assertEqual(result["session_id"], self.terminal["sessionId"])

    def test_auto_resolved_model_takes_precedence_over_message_model_in_either_order(self):
        resolved = {"type": "session.auto_mode_resolved", "data": {"chosenModel": "resolved-model"}}
        message = {"type": "assistant.message", "data": {
            "phase": "final_answer", "content": "final answer", "model": "message-model"}}
        for events in ([resolved, message, self.terminal], [message, resolved, self.terminal]):
            with self.subTest(events=events):
                completed = self.run_job(environment={"FAKE_COPILOT_OUTPUT": self.stream(events)})
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["model"], "resolved-model")

    def test_captured_invalid_model_is_normalized_error_with_raw_diagnostics(self):
        diagnostic = 'Error: Model "invalid-task4-fixture" from --model flag is not available.'
        completed = self.run_job("--model", "invalid-task4-fixture", environment={
            "FAKE_COPILOT_OUTPUT": self.invalid_model, "FAKE_COPILOT_EXIT": "1", "FAKE_COPILOT_STDERR": diagnostic})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "provider_exit")
        self.assertIn(diagnostic, (Path(result["transcript_dir"]) / "stderr.log").read_text())

    def test_followup_resumes_same_session_and_preserves_source_request_semantics(self):
        completed = self.run_job("--max-credits", "30", "--name", "source session")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        source_directory = Path(source["transcript_dir"])
        before = {path.name: path.read_bytes() for path in source_directory.iterdir() if path.is_file()}
        followed = self.command("followup", source["job_id"], "--prompt", "next prompt", environment={
            "FAKE_COPILOT_OUTPUT": self.resumed})
        self.assertEqual(followed.returncode, 0, followed.stderr)
        descriptor = json.loads(followed.stdout)
        waited = self.command("wait", descriptor["job_id"], "--timeout", "5")
        self.assertEqual(waited.returncode, 0, waited.stderr)
        result = json.loads(waited.stdout)
        self.assertEqual(result["session_id"], source["session_id"])
        self.assertEqual(result["source_job_id"], source["job_id"])
        self.assertEqual(result["response"], "COPILOT_FIXTURE_RESUME_OK")
        argv = json.loads(self.args_file.read_text())["argv"]
        self.assertIn("--resume=" + source["session_id"], argv)
        self.assertEqual(argv[argv.index("--model") + 1], "auto")
        self.assertEqual(argv[argv.index("--max-ai-credits") + 1], "30")
        request = json.loads((Path(result["transcript_dir"]) / "request.json").read_text())
        self.assertEqual(request["provider_options"], {"max_credits": "30"})
        self.assertEqual(request["name"], "source session")
        self.assertIsNone(request["effort"])
        self.assertEqual(before, {path.name: path.read_bytes() for path in source_directory.iterdir() if path.is_file()})

    def test_followup_without_session_fails_before_creating_another_job(self):
        terminal = {key: value for key, value in self.terminal.items() if key != "sessionId"}
        completed = self.run_job(environment={"FAKE_COPILOT_OUTPUT": self.stream(self.events[:-1] + [terminal])})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        before = {path.name for path in self.root.iterdir()}
        followed = self.command("followup", source["job_id"], "--prompt", "next")
        self.assertEqual(followed.returncode, 2, followed.stderr)
        self.assertEqual(before, {path.name for path in self.root.iterdir()})
        self.assertIn("no resumable session ID", followed.stderr)

    def test_stderr_only_quota_and_auth_failures_normalize_without_publishing_text(self):
        empty_raw = self.workspace / "empty-stdout.jsonl"
        empty_raw.write_text("")
        stderr_file = self.workspace / "test-stderr.log"
        req = rally_core.JobRequest(provider="copilot", prompt="hello", cwd=self.workspace)
        adapter = rally_core.CopilotAdapter()

        stderr_file.write_text("Error: usage limit reached; Bearer credential-sentinel\n")
        outcome = adapter.parse(req, empty_raw, stderr_file, 1)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "quota_error")
        self.assertEqual(outcome.error["message"], "copilot quota unavailable")
        self.assertNotIn("credential-sentinel", json.dumps(outcome.error))

        stderr_file.write_text("Error: not logged in; Unauthorized\n")
        outcome = adapter.parse(req, empty_raw, stderr_file, 1)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "auth_error")
        self.assertEqual(outcome.error["message"], "copilot authentication unavailable")


class CodexAdapterTests(unittest.TestCase):
    """Codex 0.164.0 captures, with focused synthetic protocol failures."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.bin = self.workspace / "bin"
        self.bin.mkdir()
        self.args_file = self.workspace / "arguments.json"
        fixtures = Path(__file__).parent / "fixtures" / "rally"
        self.raw = (fixtures / "codex-0.164.0-success.jsonl").read_text()
        self.events = [json.loads(line) for line in self.raw.splitlines()]
        self.terminal = self.events[-1]
        self.thread_id = self.events[0]["thread_id"]
        self.resumed = (fixtures / "codex-0.164.0-resume.jsonl").read_text()
        self.invalid_effort = (fixtures / "codex-0.164.0-invalid-effort.jsonl").read_text()
        executable = self.bin / "codex"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "open(os.environ['FAKE_CODEX_ARGS'], 'w').write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
            "print(os.environ['FAKE_CODEX_OUTPUT'])\n"
            "print(os.environ.get('FAKE_CODEX_STDERR', ''), file=sys.stderr)\n"
            "sys.exit(int(os.environ.get('FAKE_CODEX_EXIT', '0')))\n"
        )
        executable.chmod(0o700)
        self.root = self.workspace / "jobs"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "AGENT_JOB_DIR": str(self.root), "AGENT_JOB_LAUNCHER": "detached",
                    "FAKE_CODEX_ARGS": str(self.args_file), "FAKE_CODEX_OUTPUT": self.raw}
        self.prompt = "--literal `touch injected`; $(touch injected)\nsecond line"

    def command(self, *args, environment=None):
        return subprocess.run([sys.executable, str(RUNNER), *args], cwd=self.workspace,
                              env={**self.env, **(environment or {})}, text=True,
                              capture_output=True, timeout=10)

    def run_job(self, *args, environment=None):
        return self.command("codex", "run", "--prompt=" + self.prompt, *args, environment=environment)

    @staticmethod
    def stream(events):
        return "\n".join(json.dumps(event) for event in events)

    def test_defaults_exact_argv_and_normalized_final_thread_usage(self):
        completed = self.run_job()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["provider"], result["status"], result["response"]),
                         ("codex", "SUCCESS", "CODEX_FIXTURE_OK"))
        self.assertEqual(result["session_id"], self.thread_id)
        self.assertEqual(result["usage"], self.terminal["usage"])
        self.assertEqual(result["provider_result"], self.terminal)
        self.assertIsNone(result["model"])
        self.assertIsNone(result["effort"])
        self.assertEqual(json.loads(self.args_file.read_text()), {"cwd": str(self.workspace), "argv": [
            "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
            "-C", str(self.workspace), "--", self.prompt]})
        self.assertFalse((self.workspace / "injected").exists())

    def test_new_explicit_model_effort_and_multiple_directories_use_separate_arguments(self):
        first = self.workspace / "extra directory"
        second = self.workspace / "other"
        first.mkdir()
        second.mkdir()
        completed = self.run_job("--model", "chosen-model", "--effort", "low", "--add-dir", str(first),
                                 "--add-dir", str(second))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(self.args_file.read_text())["argv"], [
            "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
            "-C", str(self.workspace), "--model", "chosen-model", "--add-dir", str(first),
            "--add-dir", str(second), "-c", 'model_reasoning_effort="low"', "--", self.prompt])
        result = json.loads(completed.stdout)
        self.assertEqual((result["model"], result["effort"]), ("chosen-model", "low"))

    def test_resume_exact_supported_flags_and_dash_prompt_with_recorded_thread(self):
        extra = self.workspace / "extra"
        extra.mkdir()
        completed = self.run_job("--session", self.thread_id, "--model", "chosen-model", "--effort", "high",
                                 "--add-dir", str(extra), environment={"FAKE_CODEX_OUTPUT": self.resumed})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(self.args_file.read_text()), {"cwd": str(self.workspace), "argv": [
            "exec", "resume", "--dangerously-bypass-approvals-and-sandbox", "--json", "--all",
            "--skip-git-repo-check", "--model", "chosen-model", "-c", 'model_reasoning_effort="high"',
            "--", self.thread_id, self.prompt]})
        self.assertEqual(json.loads(completed.stdout)["session_id"], self.thread_id)

    def test_unsupported_options_fail_before_job_creation(self):
        for flags in (["--name", "unsupported"], ["--project", "p"], ["--new-project"],
                      ["--conversation", "c"], ["--max-credits", "30"]):
            with self.subTest(flags=flags):
                completed = self.run_job(*flags)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertFalse(self.root.exists())
        adapter = rally_core.provider_adapters().get("codex")
        self.assertIsNotNone(adapter)
        with self.assertRaisesRegex(ValueError, "unsupported Codex provider options"):
            adapter.validate(rally_core.JobRequest(provider="codex", prompt="p", cwd=self.workspace,
                                                  provider_options={"unknown": True}))

    def test_last_completed_agent_message_wins_amid_tools_and_unrelated_diagnostics(self):
        events = self.events[:-1] + [
            {"type": "item.completed", "item": {"id": "last", "type": "agent_message", "text": "last final"}},
            {"type": "item.started", "item": {"type": "agent_message", "text": "unfinished"}},
            {"type": "item.updated", "item": {"type": "agent_message", "text": "delta"}},
            self.events[3], self.terminal, {"type": "diagnostic", "data": {"status": "ERROR"}},
            ["unrelated"], {"message": "unrelated"},
        ]
        raw = "MCP diagnostic\n" + self.stream(events) + "\nnon-JSON tail\n{\n"
        completed = self.run_job(environment={"FAKE_CODEX_OUTPUT": raw})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["response"], "last final")

    def test_nonzero_process_exit_preserves_thread_response_and_usage(self):
        completed = self.run_job(environment={"FAKE_CODEX_EXIT": "7"})
        self.assertEqual(completed.returncode, 7, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["exit_code"]), ("ERROR", 7))
        self.assertEqual(result["error"]["type"], "provider_exit")
        self.assertEqual(result["session_id"], self.thread_id)
        self.assertEqual(result["response"], "CODEX_FIXTURE_OK")
        self.assertEqual(result["usage"], self.terminal["usage"])

    def test_completed_turn_and_nonempty_completed_message_are_required(self):
        for events in (self.events[:-1], self.events[:2] + [self.terminal],
                       self.events[:2] + [{"type": "item.completed", "item": {
                           "type": "agent_message", "text": " \n"}}, self.terminal],
                       self.events + [{"type": "turn.started"}]):
            with self.subTest(events=events):
                completed = self.run_job(environment={"FAKE_CODEX_OUTPUT": self.stream(events)})
                self.assertEqual(completed.returncode, 1, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["status"], "ERROR")
                self.assertEqual(result["session_id"], self.thread_id)

    def test_error_failed_and_incomplete_events_override_completed_turn_with_zero_exit(self):
        for failure, expected_type in (({"type": "error", "message": "quota exhausted"}, "quota_error"),
                                       ({"type": "error"}, "provider_error"),
                                       ({"type": "turn.failed", "error": {"message": "quota exhausted"}}, "quota_error"),
                                       ({"type": "turn.incomplete"}, "provider_error"),
                                       ({"type": "item.failed"}, "provider_error"),
                                       ({"type": "item.completed", "item": {"type": "error", "message": "tool error"}}, "provider_error"),
                                       ({**self.terminal, "error": {}}, "provider_error")):
            for events in (self.events + [failure], self.events[:-1] + [failure, self.terminal]):
                with self.subTest(failure=failure, events=events):
                    completed = self.run_job(environment={"FAKE_CODEX_OUTPUT": self.stream(events)})
                    self.assertEqual(completed.returncode, 1, completed.stderr)
                    result = json.loads(completed.stdout)
                    self.assertEqual((result["status"], result["exit_code"]), ("ERROR", 1))
                    self.assertEqual(result["error"]["type"], expected_type)
                    self.assertEqual(result["session_id"], self.thread_id)

    def test_recognized_thread_started_requires_nonempty_single_line_thread_id(self):
        for thread in ({"type": "thread.started"}, {"type": "thread.started", "thread_id": None},
                       {"type": "thread.started", "thread_id": ""},
                       {"type": "thread.started", "thread_id": " \t"},
                       {"type": "thread.started", "thread_id": self.thread_id + "\nother"}):
            with self.subTest(thread=thread):
                events = [thread] + self.events[1:]
                completed = self.run_job(environment={"FAKE_CODEX_OUTPUT": self.stream(events)})
                self.assertEqual(completed.returncode, 1, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual((result["status"], result["exit_code"]), ("ERROR", 1))
                self.assertEqual(result["error"]["type"], "parse_error")
                self.assertIn("Codex thread ID", result["error"]["message"])

    def test_malformed_known_protocol_fields_become_durable_error(self):
        for events in ([{**self.events[0], "thread_id": 5}] + self.events[1:],
                       self.events[:-1] + [{**self.terminal, "usage": []}],
                       self.events[:2] + [{"type": "item.completed", "item": []}, self.terminal],
                       self.events[:2] + [{"type": "item.completed", "item": {
                           "type": "agent_message", "text": 5}}, self.terminal]):
            with self.subTest(events=events):
                completed = self.run_job(environment={"FAKE_CODEX_OUTPUT": self.stream(events)})
                self.assertEqual(completed.returncode, 1, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["status"], "ERROR")
                self.assertEqual(result["error"]["type"], "parse_error")

    def test_captured_error_and_failed_turn_retain_thread_on_zero_and_nonzero_exit(self):
        thread_id = json.loads(self.invalid_effort.splitlines()[0])["thread_id"]
        for exit_code in ("0", "1"):
            with self.subTest(exit_code=exit_code):
                completed = self.run_job(environment={"FAKE_CODEX_OUTPUT": self.invalid_effort,
                                                       "FAKE_CODEX_EXIT": exit_code})
                self.assertEqual(completed.returncode, 1, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["status"], "ERROR")
                self.assertEqual(result["session_id"], thread_id)
                self.assertIn("invalid-task5-probe", result["error"]["message"])

    def test_current_success_and_resume_fixtures_have_exact_response_session_and_bounded_usage(self):
        thread_id = "codex-fixture-thread"
        for name, response, usage in (
                ("codex-0.164.0-success.jsonl", "CODEX_FIXTURE_OK",
                 {"input_tokens": 36742, "cached_input_tokens": 24320, "cache_write_input_tokens": 0,
                  "output_tokens": 142, "reasoning_output_tokens": 42}),
                ("codex-0.164.0-resume.jsonl", "CODEX_FIXTURE_RESUME_OK",
                 {"input_tokens": 55469, "cached_input_tokens": 42368, "cache_write_input_tokens": 0,
                  "output_tokens": 153, "reasoning_output_tokens": 42})):
            with self.subTest(name=name):
                outcome = parse_provider_fixture(self, "codex", name)
                self.assertTrue(outcome.success)
                self.assertEqual((outcome.response, outcome.session_id, outcome.usage), (response, thread_id, usage))

    def test_structured_quota_failure_keeps_provider_error_without_credentials(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                outcome = parse_provider_fixture(self, "codex", "codex-0.164.0-quota-error.jsonl", exit_code)
                assert_public_failure(self, outcome, "quota_error")
                self.assertEqual(outcome.session_id, "codex-fixture-thread")
                self.assertIn("usage limit", outcome.error["message"])

    def test_truncated_and_no_result_streams_with_zero_exit_are_parse_errors(self):
        for name in ("codex-0.164.0-truncated.jsonl", "codex-0.164.0-no-result.jsonl"):
            with self.subTest(name=name):
                outcome = parse_provider_fixture(self, "codex", name)
                assert_public_failure(self, outcome, "parse_error")
                self.assertEqual(outcome.session_id, "codex-fixture-thread")
                self.assertEqual(outcome.response, "CODEX_FIXTURE_OK" if "truncated" in name else "")
        outcome = parse_provider_fixture(self, "codex", "codex-0.164.0-no-result.jsonl", 7)
        self.assertEqual(outcome.error["type"], "provider_exit")

    def test_no_result_diagnostics_classify_quota_and_auth_without_publishing_text(self):
        for text, kind, public in (("Usage limit reached. token=credential-sentinel", "quota_error", "quota"),
                                   ("Error: not logged in; Bearer credential-sentinel", "auth_error", "authentication")):
            for exit_code in (0, 1):
                with self.subTest(kind=kind, exit_code=exit_code):
                    raw = self.workspace / "diagnostic.jsonl"
                    raw.write_text(text + "\n" + self.stream(self.events[:2]) + "\n")
                    outcome = rally_core.CodexAdapter().parse(raw, exit_code)
                    assert_public_failure(self, outcome, kind)
                    self.assertEqual(outcome.error["message"], f"codex {public} unavailable")

    def test_hostile_stream_tails_are_parse_errors_not_exceptions(self):
        raw = self.workspace / "hostile.jsonl"
        for content in (self.raw.encode()[:-40] + "é".encode()[:1], b"[" * 5000 + b"\n" + self.raw.encode()[:100]):
            with self.subTest(content=content[-20:]):
                raw.write_bytes(content)
                assert_public_failure(self, rally_core.CodexAdapter().parse(raw, 0), "parse_error")

    def test_error_messages_are_redacted_single_line_and_bounded(self):
        message = "line one\nAuthorization: Bearer credential-sentinel " + "x" * 600
        for failure in ({"type": "error", "message": message}, {"type": "turn.failed", "error": {"message": message}},
                        {"type": "turn.failed", "error": message}):
            with self.subTest(failure=failure["type"], shape=type(failure.get("error"))):
                raw = self.workspace / "events.jsonl"
                raw.write_text(self.stream(self.events[:-1] + [failure]))
                outcome = rally_core.CodexAdapter().parse(raw, 0)
                assert_public_failure(self, outcome, "provider_error")
                self.assertLessEqual(len(outcome.error["message"]), 300)
                self.assertNotIn("\n", outcome.error["message"])

    def test_zero_exit_truncated_result_is_durable_parse_error_with_recovered_session(self):
        raw = (FIXTURES / "codex-0.164.0-truncated.jsonl").read_text()
        completed = self.run_job(environment={"FAKE_CODEX_OUTPUT": raw})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["error"]["type"]), ("ERROR", "parse_error"))
        self.assertEqual(result["session_id"], self.thread_id)

    def test_followup_resumes_same_thread_and_preserves_source_and_saved_options(self):
        extra = self.workspace / "extra"
        extra.mkdir()
        completed = self.run_job("--model", "chosen-model", "--effort", "low", "--add-dir", str(extra))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        source_directory = Path(source["transcript_dir"])
        before = {path.name: path.read_bytes() for path in source_directory.iterdir() if path.is_file()}
        followed = self.command("followup", source["job_id"], "--prompt=--next literal prompt",
                                environment={"FAKE_CODEX_OUTPUT": self.resumed})
        self.assertEqual(followed.returncode, 0, followed.stderr)
        descriptor = json.loads(followed.stdout)
        waited = self.command("wait", descriptor["job_id"], "--timeout", "5")
        self.assertEqual(waited.returncode, 0, waited.stderr)
        result = json.loads(waited.stdout)
        self.assertEqual((result["session_id"], result["source_job_id"], result["response"]),
                         (source["session_id"], source["job_id"], "CODEX_FIXTURE_RESUME_OK"))
        self.assertEqual(json.loads(self.args_file.read_text()), {"cwd": str(self.workspace), "argv": [
            "exec", "resume", "--dangerously-bypass-approvals-and-sandbox", "--json", "--all",
            "--skip-git-repo-check", "--model", "chosen-model", "-c", 'model_reasoning_effort="low"',
            "--", self.thread_id, "--next literal prompt"]})
        request = json.loads((Path(result["transcript_dir"]) / "request.json").read_text())
        self.assertEqual(request["add_dirs"], [str(extra)])
        self.assertEqual(before, {path.name: path.read_bytes() for path in source_directory.iterdir() if path.is_file()})

    def test_followup_without_thread_fails_before_creating_another_job(self):
        completed = self.run_job(environment={"FAKE_CODEX_OUTPUT": self.stream(self.events[1:])})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        before = {path.name for path in self.root.iterdir()}
        followed = self.command("followup", source["job_id"], "--prompt", "next")
        self.assertEqual(followed.returncode, 2, followed.stderr)
        self.assertEqual(before, {path.name for path in self.root.iterdir()})
        self.assertIn("no resumable session ID", followed.stderr)

    def test_start_returns_descriptor_and_wait_normalizes_terminal_output(self):
        started = self.command("codex", "start", "--prompt", "start prompt")
        self.assertEqual(started.returncode, 0, started.stderr)
        descriptor = json.loads(started.stdout)
        self.assertNotIn("response", descriptor)
        waited = self.command("wait", descriptor["job_id"], "--timeout", "5")
        self.assertEqual(waited.returncode, 0, waited.stderr)
        self.assertEqual(json.loads(waited.stdout)["response"], "CODEX_FIXTURE_OK")

    def test_stderr_only_quota_and_auth_failures_normalize_without_publishing_text(self):
        empty_raw = self.workspace / "empty-stdout.jsonl"
        empty_raw.write_text("")
        stderr_file = self.workspace / "test-stderr.log"
        req = rally_core.JobRequest(provider="codex", prompt="hello", cwd=self.workspace)
        adapter = rally_core.CodexAdapter()

        stderr_file.write_text("Error: Rate limit exceeded; token=credential-sentinel\n")
        outcome = adapter.parse(req, empty_raw, stderr_file, 1)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "quota_error")
        self.assertEqual(outcome.error["message"], "codex quota unavailable")
        self.assertNotIn("credential-sentinel", json.dumps(outcome.error))

        stderr_file.write_text("Error: login required / unauthorized\n")
        outcome = adapter.parse(req, empty_raw, stderr_file, 1)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "auth_error")
        self.assertEqual(outcome.error["message"], "codex authentication unavailable")


class ClaudeAdapterTests(unittest.TestCase):
    """Real 2.1.311 OAuth failure and explicitly synthetic success/resume streams."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.bin = self.workspace / "bin"
        self.bin.mkdir()
        self.args_file = self.workspace / "arguments.json"
        fixtures = Path(__file__).parent / "fixtures" / "rally"
        self.raw = (fixtures / "claude-2.1.311-synthetic-success.jsonl").read_text()
        self.events = [json.loads(line) for line in self.raw.splitlines()]
        self.init, self.assistant, self.terminal = self.events
        self.session_id = self.terminal["session_id"]
        self.resumed = (fixtures / "claude-2.1.311-synthetic-resume.jsonl").read_text()
        self.oauth_error = (fixtures / "claude-2.1.311-oauth-error.jsonl").read_text()
        executable = self.bin / "claude"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "open(os.environ['FAKE_CLAUDE_ARGS'], 'w').write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
            "print(os.environ['FAKE_CLAUDE_OUTPUT'])\n"
            "print(os.environ.get('FAKE_CLAUDE_STDERR', ''), file=sys.stderr)\n"
            "sys.exit(int(os.environ.get('FAKE_CLAUDE_EXIT', '0')))\n"
        )
        executable.chmod(0o700)
        self.root = self.workspace / "jobs"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "AGENT_JOB_DIR": str(self.root), "AGENT_JOB_LAUNCHER": "detached",
                    "FAKE_CLAUDE_ARGS": str(self.args_file), "FAKE_CLAUDE_OUTPUT": self.raw}
        self.prompt = "--literal `touch injected`; $(touch injected)\nsecond line"
        self.base_argv = ["--print", "--output-format", "stream-json", "--verbose",
                          "--dangerously-skip-permissions", "--permission-prompts", "none", "--no-chrome"]
        self.expected_usage = {key: self.terminal["usage"][key] for key in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens",
            "output_tokens_details", "server_tool_use", "cache_creation")}

    def command(self, *args, environment=None):
        return subprocess.run([sys.executable, str(RUNNER), *args], cwd=self.workspace,
                              env={**self.env, **(environment or {})}, text=True,
                              capture_output=True, timeout=10)

    def run_job(self, *args, environment=None):
        return self.command("claude", "run", "--prompt=" + self.prompt, *args, environment=environment)

    @staticmethod
    def stream(events):
        return "\n".join(json.dumps(event) for event in events)

    def parse(self, events, exit_code=0):
        adapter = rally_core.provider_adapters().get("claude")
        self.assertIsNotNone(adapter, "Claude adapter must be registered")
        path = self.workspace / "parse.jsonl"
        path.write_text(self.stream(events))
        return adapter.parse(path, exit_code)

    def test_defaults_exact_argv_and_terminal_response_session_usage_cost(self):
        completed = self.run_job()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["provider"], result["status"], result["response"]),
                         ("claude", "SUCCESS", "CLAUDE_FIXTURE_OK"))
        self.assertEqual(result["session_id"], self.session_id)
        self.assertEqual(result["usage"], self.expected_usage)
        self.assertEqual(result["provider_result"], {**self.terminal, "usage": self.expected_usage})
        self.assertEqual(result["provider_result"]["total_cost_usd"], 0.0002)
        self.assertEqual(result["model"], "claude-haiku-5-5")
        self.assertIsNone(result["effort"])
        self.assertEqual(json.loads(self.args_file.read_text()), {
            "cwd": str(self.workspace), "argv": self.base_argv + ["--", self.prompt]})
        self.assertFalse((self.workspace / "injected").exists())

    def test_optional_model_effort_name_and_repeatable_directories_are_separate_arguments(self):
        first, second = self.workspace / "extra directory", self.workspace / "other"
        first.mkdir()
        second.mkdir()
        completed = self.run_job("--model", "haiku", "--effort", "low", "--name", "named session",
                                 "--add-dir", str(first), "--add-dir", str(second))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(self.args_file.read_text())["argv"], self.base_argv + [
            "--model", "haiku", "--effort", "low", "--name", "named session", "--add-dir", str(first),
            "--add-dir", str(second), "--", self.prompt])

    def test_resume_uses_explicit_session_and_supported_flags_with_dash_prompt(self):
        completed = self.run_job("--session", self.session_id, "--model", "haiku", "--effort", "high",
                                 environment={"FAKE_CLAUDE_OUTPUT": self.resumed})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(self.args_file.read_text())["argv"], self.base_argv + [
            "--model", "haiku", "--effort", "high", "--resume", self.session_id, "--", self.prompt])
        self.assertEqual(json.loads(completed.stdout)["session_id"], self.session_id)

    def test_unsupported_options_and_efforts_fail_before_job_creation(self):
        for flags in (["--project", "p"], ["--new-project"], ["--conversation", "c"],
                      ["--max-credits", "30"], ["--effort", "none"], ["--effort", "minimal"]):
            with self.subTest(flags=flags):
                completed = self.run_job(*flags)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertFalse(self.root.exists())
        adapter = rally_core.provider_adapters().get("claude")
        self.assertIsNotNone(adapter)
        with self.assertRaisesRegex(ValueError, "unsupported Claude provider options"):
            adapter.validate(rally_core.JobRequest(provider="claude", prompt="p", cwd=self.workspace,
                                                  provider_options={"unknown": True}))

    def test_terminal_result_wins_over_init_assistant_tools_and_unrelated_diagnostics(self):
        events = [{**self.init, "session_id": "different-init-session"},
                  self.assistant, {"type": "assistant", "message": {"model": "claude-haiku-5-5", "content": [
                      {"type": "text", "text": "interim assistant text"},
                      {"type": "tool_use", "id": "tool", "name": "Bash", "input": {"command": "pwd"}}]}},
                  {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}},
                  {"type": "system", "subtype": "hook_response", "exit_code": 1}, self.terminal,
                  {"type": "diagnostic", "error": "unrelated"}, ["unrelated"], {"message": "unrelated"}]
        raw = "MCP diagnostic\n" + self.stream(events) + "\nnon-JSON tail\n{\n"
        completed = self.run_job(environment={"FAKE_CLAUDE_OUTPUT": raw})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["response"], result["session_id"]), ("CLAUDE_FIXTURE_OK", self.session_id))

    def test_result_session_preferred_and_init_session_is_fallback(self):
        terminal = {key: value for key, value in self.terminal.items() if key != "session_id"}
        self.assertEqual(self.parse([self.init, self.assistant, terminal]).session_id, self.session_id)
        self.assertEqual(self.parse([{**self.init, "session_id": "different-init-session"}, self.terminal]).session_id,
                         self.session_id)

    def test_recognized_terminal_and_nonempty_result_required_without_assistant_fallback(self):
        for events in (self.events[:-1], [self.init],
                       [self.init, self.assistant, {**self.terminal, "result": " \n"}],
                       [self.init, self.assistant, {key: value for key, value in self.terminal.items() if key != "result"}]):
            with self.subTest(events=events):
                outcome = self.parse(events)
                self.assertFalse(outcome.success)
                self.assertEqual(outcome.session_id, self.session_id)
                self.assertIsNotNone(outcome.error)

    def test_nonzero_process_exit_preserves_result_session_usage_and_cost(self):
        completed = self.run_job(environment={"FAKE_CLAUDE_EXIT": "7"})
        self.assertEqual(completed.returncode, 7, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["exit_code"], result["error"]["type"]), ("ERROR", 7, "provider_exit"))
        self.assertEqual(result["session_id"], self.session_id)
        self.assertEqual(result["response"], "CLAUDE_FIXTURE_OK")
        self.assertEqual(result["usage"], self.expected_usage)
        self.assertEqual(result["provider_result"]["total_cost_usd"], 0.0002)

    def test_actual_oauth_error_normalizes_code_message_status_even_with_process_zero(self):
        terminal = json.loads(self.oauth_error.splitlines()[-1])
        for process_exit in ("0", "1"):
            with self.subTest(process_exit=process_exit):
                completed = self.run_job(environment={"FAKE_CLAUDE_OUTPUT": self.oauth_error,
                                                       "FAKE_CLAUDE_EXIT": process_exit})
                self.assertEqual(completed.returncode, 1, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual((result["status"], result["exit_code"]), ("ERROR", 1))
                self.assertEqual(result["error"]["type"], "auth_error")
                self.assertEqual(result["error"]["code"], "oauth_not_allowed_for_organization")
                self.assertEqual(result["error"]["status"], 403)
                self.assertIn("organization not allowed", result["error"]["message"])
                self.assertLessEqual(len(result["error"]["message"]), 300)
                self.assertEqual(result["session_id"], terminal["session_id"])
                self.assertEqual(result["usage"], {**self.expected_usage,
                                                    "input_tokens": terminal["usage"]["input_tokens"],
                                                    "output_tokens": terminal["usage"]["output_tokens"]})
                self.assertEqual(result["provider_result"]["api_error_origin"], "first_party")
                self.assertEqual(result["provider_result"]["total_cost_usd"], 0)

    def test_each_terminal_semantic_error_signal_overrides_process_zero(self):
        for fields in ({"is_error": True}, {"terminal_reason": "api_error"},
                       {"api_error_code": "quota_exhausted"}, {"api_error_status": 429},
                       {"is_api_error_message": True}, {"error": {}}, {"error": ""}, {"error": None},
                       {"errors": []}, {"errors": ["quota exhausted"]},
                       {"subtype": "error_during_execution", "is_error": False}):
            with self.subTest(fields=fields):
                outcome = self.parse(self.events[:-1] + [{**self.terminal, **fields}])
                self.assertFalse(outcome.success)
                self.assertIn(outcome.error["type"], ("provider_error", "quota_error"))

    def test_empty_error_events_are_sticky_before_or_after_successful_terminal(self):
        for event in ({"type": "error"}, {"type": "error", "error": {}}, {"type": "error", "message": ""},
                      {"type": "api_error"}, {"type": "system", "subtype": "error"},
                      {**self.assistant, "error": {}}, {**self.assistant, "api_error_code": "quota_exhausted"}):
            for events in (self.events[:-1] + [event, self.terminal], self.events + [event]):
                with self.subTest(event=event, events=events):
                    outcome = self.parse(events)
                    self.assertFalse(outcome.success)
                    self.assertIn(outcome.error["type"], ("provider_error", "quota_error"))
                    self.assertEqual(outcome.session_id, self.session_id)

    def test_assistant_api_error_without_terminal_preserves_provider_details(self):
        events = [json.loads(line) for line in self.oauth_error.splitlines()][:-1]
        outcome = self.parse(events)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["code"], "oauth_not_allowed_for_organization")
        self.assertEqual(outcome.error["status"], 403)
        self.assertIn("organization not allowed", outcome.error["message"])

    def test_nested_captured_oauth_alias_uses_readable_assistant_content(self):
        init, assistant, _ = [json.loads(line) for line in self.oauth_error.splitlines()]
        expected_message = assistant["message"]["content"][0]["text"]
        for field in ("error", "is_api_error_message", "api_error_status", "api_error_code", "api_error_origin"):
            assistant["message"][field] = assistant.pop(field)
        self.assertEqual(assistant["message"]["type"], "message")
        for suffix in ([], [{**self.terminal, "session_id": init["session_id"]}]):
            with self.subTest(has_terminal=bool(suffix)):
                outcome = self.parse([init, assistant] + suffix)
                self.assertFalse(outcome.success)
                self.assertEqual(outcome.error["code"], "oauth_not_allowed_for_organization")
                self.assertEqual(outcome.error["status"], 403)
                self.assertEqual(outcome.error["message"], expected_message)

    def test_malformed_recognized_protocol_fields_are_rejected(self):
        invalid = [
            [{**self.init, "session_id": value}, self.terminal]
            for value in (None, "", " \t", "bad\nline", 4)
        ]
        invalid += [[{key: value for key, value in self.init.items() if key != "session_id"}, self.terminal]]
        invalid += [self.events[:-1] + [{**self.terminal, key: value}] for key, value in (
            ("session_id", ""), ("session_id", 4), ("result", []), ("usage", []), ("usage", None),
            ("modelUsage", []), ("is_error", "false"), ("is_error", 0), ("subtype", "unknown"),
            ("subtype", []), ("total_cost_usd", True), ("total_cost_usd", -1), ("total_cost_usd", float("nan")),
            ("api_error_status", True), ("api_error_status", "403"), ("api_error_code", []),
            ("api_error_code", ""), ("terminal_reason", []), ("model", []), ("errors", {}),
            ("error", 42), ("is_api_error_message", "true"))]
        invalid += [[self.init, {**self.assistant, "message": []}, self.terminal],
                    [self.init, {**self.assistant, "message": {"content": "text"}}, self.terminal],
                    [self.init, {**self.assistant, "message": {"content": [{"type": "text", "text": 4}]}}, self.terminal],
                    [self.init, {**self.assistant, "message": {"content": [4]}}, self.terminal],
                    [self.init, {**self.assistant, "message": {"model": 4, "content": []}}, self.terminal],
                    [self.init, {"type": "error", "message": []}, self.terminal]]
        invalid += [self.events[:-1] + [{key: value for key, value in self.terminal.items() if key != field}]
                    for field in ("is_error", "subtype")]
        for events in invalid:
            with self.subTest(events=events):
                with self.assertRaises(ValueError):
                    self.parse(events)

    def test_invalid_recognized_fields_become_durable_parse_errors(self):
        for terminal in ({**self.terminal, "usage": []}, {**self.terminal, "is_error": "false"}):
            completed = self.run_job(environment={"FAKE_CLAUDE_OUTPUT": self.stream([self.init, terminal])})
            self.assertEqual(completed.returncode, 1, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual((result["status"], result["error"]["type"]), ("ERROR", "parse_error"))

    def test_unknown_terminal_fields_do_not_leak_into_provider_metadata(self):
        outcome = self.parse(self.events[:-1] + [{**self.terminal, "authorization": "secret", "unknown": {"token": "secret"}}])
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.provider_result, {**self.terminal, "usage": self.expected_usage})

    def test_nested_assistant_error_fields_are_sticky_even_when_empty(self):
        for fields in ({"error": {}}, {"error": None}, {"errors": []}, {"api_error_code": "quota_exhausted"},
                       {"api_error_status": 429}, {"is_error": True}):
            with self.subTest(fields=fields):
                assistant = {**self.assistant, "message": {**self.assistant["message"], **fields}}
                outcome = self.parse([self.init, assistant, self.terminal])
                self.assertFalse(outcome.success)
                self.assertIn(outcome.error["type"], ("provider_error", "quota_error"))

    def test_structured_error_details_are_validated_and_only_public_fields_survive(self):
        for payload in ({"message": []}, {"code": []}, {"code": "bad\ncode"}, {"status": True}, {"type": []}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.parse([self.init, {"type": "error", "error": payload}, self.terminal])
        terminal = {**self.terminal, "error": {"type": "rate_limit_error", "message": "quota exhausted",
                                               "status": 429, "authorization": "secret"}}
        outcome = self.parse(self.events[:-1] + [terminal])
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error, {"type": "quota_error", "code": "rate_limit_error", "status": 429,
                                         "message": "quota exhausted"})
        self.assertNotIn("authorization", outcome.provider_result["error"])

    def test_terminal_errors_messages_take_priority_over_an_existing_success_response(self):
        outcome = self.parse(self.events[:-1] + [{**self.terminal, "errors": ["quota exhausted"]}])
        self.assertEqual(outcome.error["message"], "quota exhausted")

    def test_terminal_string_error_takes_priority_over_nonempty_result(self):
        terminal = {**self.terminal, "error": "quota exhausted", "is_error": True}
        completed = self.run_job(environment={"FAKE_CLAUDE_OUTPUT": self.stream(self.events[:-1] + [terminal])})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["response"]), ("ERROR", "CLAUDE_FIXTURE_OK"))
        self.assertEqual(result["error"]["message"], "quota exhausted")

    def test_error_event_top_level_status_preserves_valid_http_integers(self):
        for status in (100, 403, 599):
            with self.subTest(status=status):
                outcome = self.parse(self.events + [{"type": "error", "message": "quota exhausted", "status": status}])
                self.assertFalse(outcome.success)
                self.assertEqual(outcome.error["status"], status)

    def test_error_event_top_level_status_rejects_invalid_types_and_range(self):
        for event in ({"type": "error"}, {"type": "api_error"}, {"type": "system", "subtype": "error"}):
            for status in ("403", True, False, 403.0, 99, 600, -1):
                with self.subTest(event=event, status=status), self.assertRaisesRegex(ValueError, "Claude status"):
                    self.parse(self.events + [{**event, "status": status}])

    def test_invalid_top_level_error_status_becomes_durable_parse_error(self):
        for status in ("403", True, 600):
            with self.subTest(status=status):
                event = {"type": "error", "message": "quota exhausted", "status": status}
                completed = self.run_job(environment={"FAKE_CLAUDE_OUTPUT": self.stream(self.events + [event])})
                self.assertEqual(completed.returncode, 1, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual((result["status"], result["error"]["type"]), ("ERROR", "parse_error"))
                self.assertIn("Claude status", result["error"]["message"])

    def test_generic_later_error_does_not_replace_meaningful_api_message(self):
        events = [json.loads(line) for line in self.oauth_error.splitlines()]
        outcome = self.parse(events + [{"type": "error"}])
        self.assertEqual(outcome.error["code"], "oauth_not_allowed_for_organization")
        self.assertEqual(outcome.error["status"], 403)
        self.assertIn("organization not allowed", outcome.error["message"])

    def test_system_error_message_field_requires_text(self):
        with self.assertRaises(ValueError):
            self.parse(self.events + [{"type": "system", "subtype": "error", "message": []}])

    def test_followup_resumes_stable_session_and_preserves_source_and_saved_options(self):
        extra = self.workspace / "extra"
        extra.mkdir()
        completed = self.run_job("--model", "haiku", "--effort", "low", "--name", "source session", "--add-dir", str(extra))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        source_directory = Path(source["transcript_dir"])
        before = {path.name: path.read_bytes() for path in source_directory.iterdir() if path.is_file()}
        followed = self.command("followup", source["job_id"], "--prompt=--next literal prompt",
                                environment={"FAKE_CLAUDE_OUTPUT": self.resumed})
        self.assertEqual(followed.returncode, 0, followed.stderr)
        descriptor = json.loads(followed.stdout)
        waited = self.command("wait", descriptor["job_id"], "--timeout", "5")
        self.assertEqual(waited.returncode, 0, waited.stderr)
        result = json.loads(waited.stdout)
        self.assertEqual((result["session_id"], result["source_job_id"], result["response"]),
                         (source["session_id"], source["job_id"], "CLAUDE_FIXTURE_RESUME_OK"))
        self.assertEqual(json.loads(self.args_file.read_text()), {"cwd": str(self.workspace), "argv": self.base_argv + [
            "--model", "haiku", "--effort", "low", "--name", "source session", "--add-dir", str(extra),
            "--resume", self.session_id, "--", "--next literal prompt"]})
        self.assertEqual(before, {path.name: path.read_bytes() for path in source_directory.iterdir() if path.is_file()})

    def test_followup_without_session_fails_before_creating_another_job(self):
        events = [{key: value for key, value in event.items() if key != "session_id"} for event in self.events[1:]]
        completed = self.run_job(environment={"FAKE_CLAUDE_OUTPUT": self.stream(events)})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = json.loads(completed.stdout)
        before = {path.name for path in self.root.iterdir()}
        followed = self.command("followup", source["job_id"], "--prompt", "next")
        self.assertEqual(followed.returncode, 2, followed.stderr)
        self.assertEqual(before, {path.name for path in self.root.iterdir()})
        self.assertIn("no resumable session ID", followed.stderr)

    def test_start_returns_descriptor_and_wait_normalizes_terminal_output(self):
        started = self.command("claude", "start", "--prompt", "start prompt")
        self.assertEqual(started.returncode, 0, started.stderr)
        descriptor = json.loads(started.stdout)
        self.assertNotIn("response", descriptor)
        waited = self.command("wait", descriptor["job_id"], "--timeout", "5")
        self.assertEqual(waited.returncode, 0, waited.stderr)
        self.assertEqual(json.loads(waited.stdout)["response"], "CLAUDE_FIXTURE_OK")

    def test_current_success_and_resume_fixtures_have_exact_response_session_and_bounded_usage(self):
        for name, response in (
                ("claude-2.1.311-synthetic-success.jsonl", "CLAUDE_FIXTURE_OK"),
                ("claude-2.1.311-synthetic-resume.jsonl", "CLAUDE_FIXTURE_RESUME_OK")):
            with self.subTest(name=name):
                outcome = parse_provider_fixture(self, "claude", name)
                self.assertTrue(outcome.success)
                self.assertEqual((outcome.response, outcome.session_id, outcome.usage),
                                 (response, "claude-fixture-session", self.expected_usage))
                self.assertNotIn(CREDENTIAL_SENTINEL, json.dumps(outcome.provider_result))

    def test_structured_quota_failure_keeps_provider_error_without_credentials(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                outcome = parse_provider_fixture(self, "claude", "claude-2.1.311-quota-error.jsonl", exit_code)
                assert_public_failure(self, outcome, "quota_error")
                self.assertEqual(outcome.session_id, "claude-fixture-session")
                self.assertEqual(outcome.error["code"], "rate_limit_error")
                self.assertEqual(outcome.error["status"], 429)
                self.assertIn("Quota exhausted", outcome.error["message"])

    def test_truncated_and_no_result_streams_with_zero_exit_are_parse_errors(self):
        for name in ("claude-2.1.311-truncated.jsonl", "claude-2.1.311-no-result.jsonl"):
            with self.subTest(name=name):
                outcome = parse_provider_fixture(self, "claude", name)
                assert_public_failure(self, outcome, "parse_error")
                self.assertEqual(outcome.session_id, "claude-fixture-session")
                self.assertEqual(outcome.response, "")
        outcome = parse_provider_fixture(self, "claude", "claude-2.1.311-no-result.jsonl", 7)
        self.assertEqual(outcome.error["type"], "provider_exit")

    def test_no_result_diagnostics_classify_quota_and_auth_without_publishing_text(self):
        for text, kind, public in (("Usage limit reached. token=credential-sentinel", "quota_error", "quota"),
                                   ("Error: not logged in; Bearer credential-sentinel", "auth_error", "authentication")):
            for exit_code in (0, 1):
                with self.subTest(kind=kind, exit_code=exit_code):
                    raw = self.workspace / "diagnostic.jsonl"
                    raw.write_text(text + "\n" + self.stream(self.events[:2]) + "\n")
                    outcome = rally_core.ClaudeAdapter().parse(raw, exit_code)
                    assert_public_failure(self, outcome, kind)
                    self.assertEqual(outcome.error["message"], f"claude {public} unavailable")

    def test_hostile_stream_tails_are_parse_errors_not_exceptions(self):
        raw = self.workspace / "hostile.jsonl"
        for content in (self.raw.encode()[:-40] + "é".encode()[:1], b"[" * 5000 + b"\n" + self.raw.encode()[:300]):
            with self.subTest(content=content[-20:]):
                raw.write_bytes(content)
                assert_public_failure(self, rally_core.ClaudeAdapter().parse(raw, 0), "parse_error")

    def test_error_messages_are_redacted_single_line_and_bounded(self):
        message = "line one\nAuthorization: Bearer credential-sentinel " + "x" * 600
        for terminal in ({**self.terminal, "error": {"message": message, "code": "api_key=credential-sentinel"}, "is_error": True},
                         {**self.terminal, "error": message, "is_error": True},
                         {**self.terminal, "errors": [message], "is_error": True}):
            with self.subTest(terminal=type(terminal.get("error") or terminal.get("errors"))):
                raw = self.workspace / "events.jsonl"
                raw.write_text(self.stream(self.events[:-1] + [terminal]))
                outcome = rally_core.ClaudeAdapter().parse(raw, 0)
                self.assertFalse(outcome.success)
                self.assertIn(outcome.error["type"], ("provider_error", "auth_error"))
                self.assertNotIn(CREDENTIAL_SENTINEL, json.dumps({
                    "error": outcome.error, "usage": outcome.usage, "provider_result": outcome.provider_result,
                    "response": outcome.response, "session_id": outcome.session_id,
                }))
                self.assertLessEqual(len(outcome.error["message"]), 300)
                self.assertNotIn("\n", outcome.error["message"])

    def test_zero_exit_truncated_result_is_durable_parse_error_with_recovered_session(self):
        raw = (FIXTURES / "claude-2.1.311-truncated.jsonl").read_text()
        completed = self.run_job(environment={"FAKE_CLAUDE_OUTPUT": raw})
        self.assertEqual(completed.returncode, 1, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["status"], result["error"]["type"]), ("ERROR", "parse_error"))
        self.assertEqual(result["session_id"], self.session_id)

    def test_live_claude_2_1_317_success_fixture_with_null_api_error_status_parses(self):
        fixture_text = (FIXTURES / "claude-2.1.317-success.jsonl").read_text()
        self.assertNotRegex(fixture_text, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        for forbidden in ("total_cost_usd", "costUSD", "duration_ms", "duration_api_ms", "inference_geo",
                          "permission_denials", "subagent_stats", "modelUsage", "model_usage_since_last_result",
                          "uuid", "iterations"):
            self.assertNotIn(forbidden, fixture_text)

        outcome = parse_provider_fixture(self, "claude", "claude-2.1.317-success.jsonl")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.response, "PROVIDER_OK")
        self.assertEqual(outcome.session_id, "claude-fixture-session")
        self.assertIsNone(outcome.error)
        self.assertEqual(outcome.usage, {
            "input_tokens": 12, "output_tokens": 4, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        })
        self.assertIn("api_error_status", outcome.provider_result)
        self.assertIsNone(outcome.provider_result["api_error_status"])
        self.assertNotIn(CREDENTIAL_SENTINEL, json.dumps({
            "error": outcome.error, "usage": outcome.usage,
            "provider_result": outcome.provider_result, "response": outcome.response}))

    def test_nullable_api_error_status_and_status_treated_as_absent(self):
        for status_key in ("api_error_status", "status"):
            with self.subTest(status_key=status_key, case="null_in_terminal"):
                events = [self.init, self.assistant, {**self.terminal, status_key: None, "result": "PROVIDER_OK"}]
                outcome = self.parse(events)
                self.assertTrue(outcome.success)
                self.assertEqual(outcome.response, "PROVIDER_OK")
                self.assertIsNone(outcome.error)
                self.assertNotIn(CREDENTIAL_SENTINEL, json.dumps({
                    "error": outcome.error, "usage": outcome.usage,
                    "provider_result": outcome.provider_result, "response": outcome.response}))

        # Non-null values still must be integer 100..599; booleans and invalid values remain rejected
        for status_key in ("api_error_status", "status"):
            for bad in (True, False, "200", 200.0, 99, 600, -1):
                with self.subTest(status_key=status_key, bad=bad), self.assertRaises(ValueError):
                    self.parse([self.init, self.assistant, {**self.terminal, status_key: bad}])

    def test_stderr_only_quota_and_auth_failures_normalize_without_publishing_text(self):
        empty_raw = self.workspace / "empty-stdout.jsonl"
        empty_raw.write_text("")
        stderr_file = self.workspace / "test-stderr.log"
        req = rally_core.JobRequest(provider="claude", prompt="hello", cwd=self.workspace)
        adapter = rally_core.ClaudeAdapter()

        stderr_file.write_text("Error: rate limit reached; Bearer credential-sentinel\n")
        outcome = adapter.parse(req, empty_raw, stderr_file, 1)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "quota_error")
        self.assertEqual(outcome.error["message"], "claude quota unavailable")
        self.assertNotIn("credential-sentinel", json.dumps(outcome.error))

        stderr_file.write_text("Error: unauthorized oauth\n")
        outcome = adapter.parse(req, empty_raw, stderr_file, 1)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error["type"], "auth_error")
        self.assertEqual(outcome.error["message"], "claude authentication unavailable")


class AgentJobFinalReviewTests(unittest.TestCase):
    """Whole-branch regressions with real, delayed provider processes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.bin = self.workspace / "bin"
        self.bin.mkdir()
        self.release = self.workspace / "release"
        self.store = rally_core.JobStore(self.workspace / "jobs")
        self.manager = rally_core.JobManager(self.store)
        self.environment = mock.patch.dict(os.environ, {
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "AGENT_JOB_DIR": str(self.store.root), "AGENT_JOB_LAUNCHER": "detached",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.stop_remaining)

    def stop_remaining(self):
        for summary in self.store.list_summaries():
            self.manager.stop(summary["job_id"])

    def events(self, provider):
        session = "review-session"
        return {
            "agy": ([{"status": "RUNNING", "conversation_id": session}],
                    [{"status": "SUCCESS", "response": "final", "conversation_id": session,
                      "usage": {"tokens": 2}}]),
            "copilot": ([{"type": "session.start", "data": {"sessionId": session}}],
                        [{"type": "assistant.message", "data": {"phase": "final_answer", "content": "final"}},
                         {"type": "result", "sessionId": session, "exitCode": 0,
                          "usage": {"premiumRequests": 0}}]),
            "codex": ([{"type": "thread.started", "thread_id": session}],
                      [{"type": "item.completed", "item": {"type": "agent_message", "text": "final"}},
                       {"type": "turn.completed", "usage": {"input_tokens": 2}}]),
            "claude": ([{"type": "system", "subtype": "init", "session_id": session}],
                       [{"type": "result", "subtype": "success", "is_error": False,
                         "session_id": session, "result": "final", "usage": {"input_tokens": 2}}]),
        }[provider]

    def install(self, provider, prefix, terminal, *, delayed=False):
        executable = self.bin / provider
        executable.write_text(
            "#!" + sys.executable + "\nimport os, time\n"
            f"print({prefix!r}, end='', flush=True)\n"
            + (f"while not os.path.exists({str(self.release)!r}): time.sleep(0.02)\n" if delayed else "")
            + f"print({terminal!r}, flush=True)\n"
        )
        executable.chmod(0o700)

    @staticmethod
    def stream(events):
        return "\n".join(json.dumps(event) for event in events) + "\n"

    def start(self, provider):
        # Keep failed subtests from leaving more than one delayed job active.
        self.stop_remaining()
        return self.manager.start(rally_core.JobRequest(provider=provider, prompt="task", cwd=self.workspace))["job_id"]

    def eventually(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.02)
        self.fail("condition did not become true")

    def test_active_session_is_durable_and_stop_allows_followup(self):
        for provider in rally_core.PROVIDERS:
            with self.subTest(provider=provider):
                self.release.unlink(missing_ok=True)
                early, terminal = self.events(provider)
                self.install(provider, "diagnostic\n" + self.stream(early) + '{"partial":',
                             "\n" + self.stream(terminal), delayed=True)
                job_id = self.start(provider)
                directory = self.store.get(job_id).directory
                self.eventually(lambda: "review-session" in (directory / "stdout.jsonl").read_text())
                self.eventually(lambda: self.manager.status(job_id)["session_id"] == "review-session")
                self.assertFalse((directory / "result.json").exists())
                self.assertEqual(json.loads((directory / "metadata.json").read_text())["session_id"], "review-session")
                stopped = self.manager.stop(job_id)
                self.assertEqual((stopped["status"], stopped["session_id"]), ("STOPPED", "review-session"))
                source = (directory / "result.json").read_bytes()
                self.release.touch()
                followup = self.manager.followup(job_id, "next task")
                result = self.manager.wait(followup["job_id"], timeout=5)
                self.assertEqual((result["status"], result["session_id"]), ("SUCCESS", "review-session"))
                request = json.loads((self.store.get(followup["job_id"]).directory / "request.json").read_text())
                self.assertEqual(request["session_id"], "review-session")
                self.assertEqual((directory / "result.json").read_bytes(), source)

    def test_complete_pretty_or_unterminated_events_publish_sessions_while_running(self):
        for provider in ("agy", "codex", "claude"):
            with self.subTest(provider=provider):
                self.release.unlink(missing_ok=True)
                early, terminal = self.events(provider)
                event = json.dumps(early[0], indent=2) if provider == "agy" else json.dumps(early[0])
                self.install(provider, "diagnostic\n" + event, "\n" + self.stream(terminal), delayed=True)
                job_id = self.start(provider)
                directory = self.store.get(job_id).directory
                try:
                    self.eventually(lambda: event in (directory / "stdout.jsonl").read_text())
                    self.eventually(lambda: self.manager.status(job_id)["session_id"] == "review-session")
                    self.assertEqual(self.manager.status(job_id)["status"], "RUNNING")
                    self.assertFalse((directory / "result.json").exists())
                    self.assertEqual(json.loads((directory / "metadata.json").read_text())["session_id"],
                                     "review-session")
                finally:
                    self.release.touch()
                self.assertEqual(self.manager.wait(job_id, timeout=5)["status"], "SUCCESS")

    def test_pretty_agy_nested_value_on_new_line_retains_outer_session(self):
        early, terminal = self.events("agy")
        event = ('{\n  "status": "RUNNING",\n  "conversation_id": "review-session",\n'
                 '  "usage":\n  {"tokens": 2}\n}')
        self.install("agy", "diagnostic\n" + event, "\n" + self.stream(terminal), delayed=True)
        job_id = self.start("agy")
        directory = self.store.get(job_id).directory
        try:
            self.eventually(lambda: event in (directory / "stdout.jsonl").read_text())
            self.eventually(lambda: self.manager.status(job_id)["session_id"] == early[0]["conversation_id"])
            self.assertFalse((directory / "result.json").exists())
        finally:
            self.release.touch()
        self.assertEqual(self.manager.wait(job_id, timeout=5)["status"], "SUCCESS")

    def test_pretty_agy_ignores_nested_conversation_even_with_error_markers(self):
        for nested in ({"conversation_id": "other"},
                       {"conversation_id": "other", "status": "ERROR",
                        "error": {"type": "provider_error", "message": "nested only"}}):
            with self.subTest(nested=nested):
                raw = self.workspace / "pretty-nested.jsonl"
                raw.write_text('{\n  "status": "RUNNING",\n  "conversation_id": "real",\n'
                               '  "metadata":\n  ' + json.dumps(nested) + '\n}')
                scanner = rally_core._IncrementalSessionScanner(raw, "agy")
                self.assertEqual(scanner.scan(), "real")

    def test_running_agy_persists_outer_not_nested_conversation(self):
        _, terminal = self.events("agy")
        event = ('{\n  "status": "RUNNING",\n  "conversation_id": "real",\n'
                 '  "metadata":\n  {"conversation_id": "other", "status": "ERROR",'
                 ' "error": {"message": "nested only"}}\n}')
        self.install("agy", "diagnostic\n" + event, "\n" + self.stream(terminal), delayed=True)
        job_id = self.start("agy")
        directory = self.store.get(job_id).directory
        try:
            self.eventually(lambda: event in (directory / "stdout.jsonl").read_text())
            self.eventually(lambda: self.manager.status(job_id)["session_id"] == "real")
            self.assertFalse((directory / "result.json").exists())
            self.assertEqual(json.loads((directory / "metadata.json").read_text())["session_id"], "real")
        finally:
            self.release.touch()
        self.assertEqual(self.manager.wait(job_id, timeout=5)["status"], "SUCCESS")

    def test_truncated_nested_prefix_does_not_hide_following_codex_jsonl_session(self):
        early, terminal = self.events("codex")
        event = json.dumps(early[0])
        self.install("codex", 'diagnostic\n{"outer":{"nested":\n' + event + "\n",
                     self.stream(terminal), delayed=True)
        job_id = self.start("codex")
        directory = self.store.get(job_id).directory
        try:
            self.eventually(lambda: event in (directory / "stdout.jsonl").read_text())
            self.eventually(lambda: self.manager.status(job_id)["session_id"] == early[0]["thread_id"])
            self.assertFalse((directory / "result.json").exists())
        finally:
            self.release.touch()
        self.assertEqual(self.manager.wait(job_id, timeout=5)["status"], "SUCCESS")

    def test_worker_death_reconciliation_retains_emitted_session(self):
        for provider in rally_core.PROVIDERS:
            with self.subTest(provider=provider):
                early, terminal = self.events(provider)
                self.install(provider, self.stream(early) + '{"partial":', self.stream(terminal), delayed=True)
                job_id = self.start(provider)
                directory = self.store.get(job_id).directory
                self.eventually(lambda: "review-session" in (directory / "stdout.jsonl").read_text())
                # Kill immediately after emission; recovery must also cover the publication race.
                identity = json.loads((directory / "worker.json").read_text())
                os.kill(identity["pid"], signal.SIGKILL)
                self.manager._children[job_id].wait(timeout=2)
                self.manager.launch_grace = 0
                result = self.manager.wait(job_id, timeout=5)
                self.assertEqual(result["error"]["type"], "worker_lost")
                self.assertEqual(result["session_id"], "review-session")

    def test_parse_failure_preserves_earlier_session(self):
        for provider in rally_core.PROVIDERS:
            with self.subTest(provider=provider):
                early, terminal = self.events(provider)
                invalid = {**terminal[-1], "usage": []}
                self.install(provider, self.stream(early), self.stream([invalid]))
                result = self.manager.wait(self.start(provider), timeout=5)
                self.assertEqual(result["error"]["type"], "parse_error")
                self.assertEqual(result["session_id"], "review-session")

    def test_terminal_metadata_and_errors_allow_only_public_fields(self):
        private = {"authorization": "private-auth", "headers": {"token": "private-header"},
                   "token": "private-token", "secret": "private-secret", "unknown": "private-unknown"}
        for provider in rally_core.PROVIDERS:
            with self.subTest(provider=provider):
                early, events = self.events(provider)
                terminal = {**events[-1], **private}
                terminal["usage"] = {**terminal["usage"], **private}
                terminal["error"] = {"type": "quota", "code": "quota_exhausted", "status": 429,
                                     "message": "quota exhausted", **private,
                                     "nested": {"secret": "private-nested"}}
                if provider == "agy":
                    terminal["status"] = "ERROR"
                if provider == "claude":
                    terminal["is_error"] = True
                self.install(provider, self.stream(early + events[:-1]), self.stream([terminal]))
                completed = subprocess.run([sys.executable, str(RUNNER), provider, "run", "--prompt", "task"],
                                           cwd=self.workspace, text=True, capture_output=True, timeout=10)
                self.assertEqual(completed.returncode, 1, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertIn("quota exhausted", result["error"]["message"])
                self.assertLessEqual(set(result["error"]), {"type", "code", "status", "message"})
                self.assertLessEqual(set(result["provider_result"]["error"]), {"type", "code", "status", "message"})
                self.assertNotIn("private-", completed.stdout)
                self.assertNotIn("unknown", result["provider_result"])
                raw = (Path(result["transcript_dir"]) / "stdout.jsonl").read_text()
                for marker in ("private-auth", "private-header", "private-token", "private-secret", "private-nested"):
                    self.assertIn(marker, raw)

    def test_start_missing_launcher_returns_failed_descriptor_exit_code(self):
        with mock.patch.dict(os.environ, {"AGENT_JOB_LAUNCHER": "launchctl",
                                         "AGENT_JOB_LAUNCHCTL": str(self.workspace / "missing-launchctl")}):
            completed = subprocess.run([sys.executable, str(RUNNER), "codex", "start", "--prompt", "task"],
                                       cwd=self.workspace, text=True, capture_output=True, timeout=5)
        descriptor = json.loads(completed.stdout)
        self.assertEqual(descriptor["status"], "ERROR")
        self.assertEqual(completed.returncode, descriptor["exit_code"])
        self.assertNotEqual(completed.returncode, 0)

    def test_structured_error_public_fields_cannot_hide_nested_payloads(self):
        for provider in ("agy", "copilot", "codex"):
            with self.subTest(provider=provider):
                early, events = self.events(provider)
                terminal = {**events[-1], "error": {"message": {"secret": "private-nested-message"},
                                                       "code": {"token": "private-nested-code"}},
                            "message": {"authorization": "private-terminal-message"}}
                if provider == "agy":
                    terminal["status"] = "ERROR"
                self.install(provider, self.stream(early + events[:-1]), self.stream([terminal]))
                result = self.manager.wait(self.start(provider), timeout=5)
                self.assertEqual(result["status"], "ERROR")
                self.assertNotIn("private-", json.dumps(result))

    def test_claude_nested_terminal_metadata_drops_credentials(self):
        early, events = self.events("claude")
        terminal = {**events[-1], "modelUsage": {"haiku": {"inputTokens": 2,
                                                         "headers": {"authorization": "private-model"}}},
                    "subagent_stats": {"spawned": 0, "secret": "private-stats"}}
        self.install("claude", self.stream(early), self.stream([terminal]))
        result = self.manager.wait(self.start("claude"), timeout=5)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertNotIn("private-", json.dumps(result))
        self.assertEqual(result["provider_result"]["modelUsage"]["haiku"], {"inputTokens": 2})

    def test_structured_terminal_fields_reject_unexpected_container_shapes(self):
        cases = [(provider, {"error": [{"authorization": "private-error-list"}]})
                 for provider in ("agy", "copilot", "codex")]
        cases.extend(("claude", {field: value}) for field, value in (
            ("subagent_stats", [{"secret": "private-stats-list"}]),
            ("permission_denials", {"token": "private-denial-object"}),
            ("model_usage_since_last_result", [{"headers": "private-usage-list"}]),
        ))
        for provider, fields in cases:
            with self.subTest(provider=provider, fields=fields):
                early, events = self.events(provider)
                terminal = {**events[-1], **fields}
                if provider == "agy":
                    terminal["status"] = "ERROR"
                self.install(provider, self.stream(early + events[:-1]), self.stream([terminal]))
                result = self.manager.wait(self.start(provider), timeout=5)
                self.assertNotIn("private-", json.dumps(result))

    def test_partial_extraction_ignores_invalid_ids_and_incomplete_tail(self):
        raw = self.workspace / "partial.jsonl"
        for provider in rally_core.PROVIDERS:
            with self.subTest(provider=provider):
                early, terminal = self.events(provider)
                invalid = {"agy": {"conversation_id": ["invalid"]},
                           "copilot": {"type": "result", "sessionId": "bad\nid"},
                           "codex": {"type": "thread.started", "thread_id": {}},
                           "claude": {"type": "result", "session_id": ""}}[provider]
                raw.write_text("noise\n" + self.stream(early + [invalid]) + '{"partial":')
                extract = self.manager.adapters[provider].extract_session_id
                self.assertEqual(extract(raw), "review-session")
                raw.write_text(self.stream(early) + '{"deep":' * 2000)
                try:
                    recovered = extract(raw)
                except (ValueError, RecursionError) as error:
                    self.fail(f"malformed partial output must not fail session extraction: {type(error).__name__}")
                self.assertEqual(recovered, "review-session")
                # IDs emitted only in terminal records are recoverable before process exit.
                if provider != "codex":
                    raw.write_text(self.stream([terminal[-1]]) + '{"partial":')
                    self.assertEqual(extract(raw), "review-session")

    def test_malformed_agy_model_keeps_existing_parse_error_behavior(self):
        early, terminal = self.events("agy")
        self.install("agy", self.stream(early), self.stream([{**terminal[-1], "model": {"invalid": True}}]))
        result = self.manager.wait(self.start("agy"), timeout=5)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["type"], "parse_error")

    def test_usage_metric_values_are_numeric_and_private_values_stay_raw(self):
        metric = {"agy": "tokens", "copilot": "premiumRequests", "codex": "input_tokens", "claude": "input_tokens"}
        nested = {"copilot": ("codeChanges", "linesAdded"),
                  "claude": ("output_tokens_details", "thinking_tokens")}
        bad = ["private-token", ["private-token"], {"secret": "private-token"},
               float("nan"), float("inf"), True, -1]
        for provider in rally_core.PROVIDERS:
            for value in bad:
                with self.subTest(provider=provider, value=repr(value)):
                    early, events = self.events(provider)
                    terminal = dict(events[-1])
                    terminal["usage"] = {metric[provider]: value, "secret": "private-token"}
                    if provider in nested:
                        outer, inner = nested[provider]
                        terminal["usage"][outer] = {inner: value, "safe": "private-token"}
                    self.install(provider, self.stream(early + events[:-1]), self.stream([terminal]))
                    result = self.manager.wait(self.start(provider), timeout=5)
                    self.assertNotIn("private-token", json.dumps(result))
                    self.assertNotIn(metric[provider], result["usage"])
                    self.assertNotIn(metric[provider], result["provider_result"].get("usage", {}))
                    self.assertIn("private-token", (Path(result["transcript_dir"]) / "stdout.jsonl").read_text())

    def test_claude_terminal_metric_values_reject_nonfinite_and_private_payloads(self):
        early, events = self.events("claude")
        terminal = {**events[-1], "total_cost_usd": 1.0,
                    "modelUsage": {"haiku": {"inputTokens": 2, "costUSD": float("nan"),
                                              "outputTokens": "private-token"}},
                    "subagent_stats": {"spawned": float("inf"), "by_type": {"user": "private-token"}}}
        self.install("claude", self.stream(early), self.stream([terminal]))
        result = self.manager.wait(self.start("claude"), timeout=5)
        self.assertNotIn("private-token", json.dumps(result))
        self.assertNotIn("costUSD", result["provider_result"]["modelUsage"]["haiku"])
        self.assertNotIn("spawned", result["provider_result"]["subagent_stats"])

    def test_public_error_status_accepts_only_http_integer(self):
        for status in ("private-token", "429", True, 99, 600, -1, [429]):
            with self.subTest(status=status):
                self.assertNotIn("status", rally_core._public_error({"status": status}))
        self.assertEqual(rally_core._public_error({"status": 429}), {"status": 429})

    def test_metric_numbers_bound_oversized_integers(self):
        self.assertEqual(rally_core._public_usage({"tokens": 10 ** 400}, "agy"), {})

    def test_terminal_state_not_visible_until_result_is_committed(self):
        original_write = rally_core.atomic_write
        entered = threading.Event()
        release = threading.Event()
        failures = []

        def write(path, value):
            result = original_write(path, value)
            if Path(path).name == "state" and value == "ERROR\n":
                entered.set()
                if not release.wait(5):
                    failures.append("publication barrier timed out")
            return result

        with mock.patch.object(rally_core.JobManager, "_launch", return_value=None), mock.patch.object(rally_core, "atomic_write", side_effect=write):
            output = io.StringIO()
            def finish_started_job(job):
                try:
                    self.manager.finalize(job.job_id, status="ERROR", exit_code=1,
                                          error={"type": "provider_error", "message": "failed"})
                except Exception as error:
                    failures.append(str(error))

            with mock.patch.object(rally_core.JobManager, "_launch", side_effect=lambda job, token: threading.Thread(
                    target=finish_started_job, args=(job,), daemon=True).start()), contextlib.redirect_stdout(output):
                try:
                    code = rally_core.main(["codex", "start", "--cwd", str(self.workspace), "--prompt", "task"])
                    self.assertTrue(entered.wait(5))
                    descriptor = json.loads(output.getvalue())
                    self.assertEqual((descriptor["status"], code), ("STARTING", 0))
                    job = self.store.get(descriptor["job_id"])
                    self.assertEqual(self.manager._summary(job)["status"], "STARTING")
                finally:
                    release.set()
            self.eventually(lambda: (self.store.get(descriptor["job_id"]).directory / "result.json").exists())
            self.assertEqual(self.manager._summary(job)["status"], "ERROR")
            after = io.StringIO()
            with contextlib.redirect_stdout(after):
                committed_code = rally_core.main(["status", descriptor["job_id"]])
            self.assertEqual((json.loads(after.getvalue())["status"], committed_code), ("ERROR", 1))
            self.assertEqual(failures, [])

    def test_incremental_scan_reads_only_new_bytes_and_stops_after_session(self):
        raw = self.workspace / "growing.jsonl"
        raw.write_bytes(b"")
        scanner = rally_core._IncrementalSessionScanner(raw, "codex")
        original_open = Path.open
        counts = []

        class TrackedStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def seek(self, *args):
                return self.stream.seek(*args)
            def read(self, size):
                data = self.stream.read(size)
                counts.append(len(data))
                return data

        def opened(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return TrackedStream(stream) if path == raw and args == ("rb",) else stream

        with mock.patch.object(Path, "open", opened):
            for _ in range(8):
                with raw.open("ab") as stream:
                    stream.write((json.dumps({"type": "noise", "payload": "x" * 100000}) + "\n").encode())
                self.assertIsNone(scanner.scan())
            self.assertLessEqual(sum(counts), raw.stat().st_size + 8 * 65536)
            with raw.open("ab") as stream:
                stream.write(b'{"type":"thread.started","thread_id":"early-session"}\n')
            self.assertEqual(scanner.scan(), "early-session")
            before = len(counts)
            self.assertEqual(scanner.scan(), "early-session")
            self.assertEqual(len(counts), before)

    def test_incremental_scan_frames_split_utf8_and_escaped_strings_without_newline(self):
        raw = self.workspace / "split.jsonl"
        scanner = rally_core._IncrementalSessionScanner(raw, "codex")
        event = json.dumps({"type": "thread.started", "message": 'brace } and escaped " quote',
                            "thread_id": "sess-é"}, ensure_ascii=False).encode()
        split = event.index("é".encode()) + 1
        raw.write_bytes(b"noise {ignored}\n" + event[:split])
        self.assertIsNone(scanner.scan())
        with raw.open("ab") as stream:
            stream.write(event[split:])
        self.assertEqual(scanner.scan(), "sess-é")

    def test_incremental_scan_bounds_large_incomplete_frame_and_reads_appends_once(self):
        raw = self.workspace / "incomplete.jsonl"
        raw.write_bytes(b'{"type":"noise","data":"')
        scanner = rally_core._IncrementalSessionScanner(raw, "codex")
        original_open = Path.open
        bytes_read = []

        class TrackedStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def seek(self, *args):
                return self.stream.seek(*args)
            def read(self, size):
                data = self.stream.read(size)
                bytes_read.append(len(data))
                return data

        def opened(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return TrackedStream(stream) if path == raw and args == ("rb",) else stream

        with mock.patch.object(Path, "open", opened), mock.patch.object(rally_core.json, "loads", wraps=json.loads) as decode:
            for _ in range(12):
                with raw.open("ab") as stream:
                    stream.write(b"x" * 100000)
                self.assertIsNone(scanner.scan())
                self.assertLessEqual(len(scanner.pending), scanner.max_line_size)
            self.assertEqual(decode.call_count, 0, "incomplete frame was reparsed")
            self.assertLessEqual(sum(bytes_read), raw.stat().st_size + 12 * 65536)
            with raw.open("ab") as stream:
                stream.write(b'\n{"type":"thread.started","thread_id":"after-large"}')
            self.assertEqual(scanner.scan(), "after-large")

    def test_session_scan_runs_outside_job_lock_and_stops_after_persistence(self):
        job = self.store.create(rally_core.JobRequest(provider="codex", prompt="task", cwd=self.workspace))
        raw = job.directory / "stdout.jsonl"
        raw.write_text('{"type":"thread.started","thread_id":"early-session"}\n')
        scanner = rally_core._IncrementalSessionScanner(raw, "codex")
        entered, release, acquired = threading.Event(), threading.Event(), threading.Event()
        original_scan = scanner.scan
        failures = []

        def blocked_scan():
            entered.set()
            if not release.wait(5):
                failures.append("scan barrier timed out")
            return original_scan()

        def recover():
            try:
                self.manager._recover_session(job, scanner=scanner)
            except Exception as error:
                failures.append(str(error))

        with mock.patch.object(scanner, "scan", side_effect=blocked_scan):
            worker = threading.Thread(target=recover, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                def take_lock():
                    with rally_core._job_lock(job):
                        acquired.set()
                contender = threading.Thread(target=take_lock, daemon=True)
                contender.start()
                self.assertTrue(acquired.wait(2), "scan held job lock")
            finally:
                release.set()
            worker.join(5)
            contender.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(json.loads((job.directory / "metadata.json").read_text())["session_id"], "early-session")
        with mock.patch.object(scanner, "scan", side_effect=AssertionError("durable ID was rescanned")):
            self.manager._recover_session(job, scanner=scanner)

    def test_runner_executable_is_thin_bootstrap_without_proxy_or_reexports(self):
        source = RUNNER.read_text()
        lines = [line.strip() for line in source.splitlines() if line.strip() and not line.strip().startswith("#")]
        self.assertTrue(source.startswith("#!/usr/bin/env python3\n"))
        self.assertNotIn("_CompatibilityModule", source)
        self.assertNotIn("_OWNER_MAP", source)
        self.assertNotIn("_SUBMODULE_NAMES", source)
        self.assertIn("from rally_core.cli import main", source)
        self.assertIn("raise SystemExit(main())", source)
        self.assertLessEqual(len(lines), 10)

    def test_copied_executable_and_package_runs_help_and_doctor_from_unrelated_cwd(self):
        temp_dir = self.workspace / "portable_runner"
        temp_dir.mkdir()
        runner_copy = temp_dir / "rally"
        shutil.copy2(RUNNER, runner_copy)
        runner_copy.chmod(0o755)
        pkg_copy = temp_dir / "rally_core"
        shutil.copytree(RUNNER.parent / "rally_core", pkg_copy)

        unrelated_cwd = self.workspace / "unrelated_caller"
        unrelated_cwd.mkdir()

        help_res = subprocess.run([str(runner_copy), "--help"], cwd=unrelated_cwd, capture_output=True, text=True)
        self.assertEqual(help_res.returncode, 0)
        self.assertIn("rally PROVIDER", help_res.stdout)

        doctor_res = subprocess.run([str(runner_copy), "doctor"], cwd=unrelated_cwd, capture_output=True, text=True)
        self.assertEqual(doctor_res.returncode, 0)
        doctor_data = json.loads(doctor_res.stdout)
        self.assertEqual(doctor_data["schema_version"], rally_core.SCHEMA_VERSION)
        self.assertEqual(len(doctor_data["providers"]), 4)

    def test_copied_executable_runs_jobs_and_spawns_subprocesses_finding_sibling_package(self):
        temp_dir = self.workspace / "portable_runner_job"
        temp_dir.mkdir()
        runner_copy = temp_dir / "rally"
        shutil.copy2(RUNNER, runner_copy)
        runner_copy.chmod(0o755)
        pkg_copy = temp_dir / "rally_core"
        shutil.copytree(RUNNER.parent / "rally_core", pkg_copy)

        early, terminal = self.events("agy")
        self.install("agy", "diagnostic\n" + self.stream(early), "\n" + self.stream(terminal))
        unrelated_cwd = self.workspace / "unrelated_repo"
        unrelated_cwd.mkdir()

        run_res = subprocess.run(
            [str(runner_copy), "agy", "run", "--prompt", "portable task", "--cwd", str(unrelated_cwd)],
            cwd=unrelated_cwd,
            env={**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
        )
        self.assertEqual(run_res.returncode, 0, run_res.stderr)
        data = json.loads(run_res.stdout)
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(data["session_id"], "review-session")


class AgentJobBoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()

    def test_truncate_response_at_valid_utf8_boundary_with_indicator(self):
        from rally_core.model import truncate_response, MAX_PUBLIC_RESPONSE_BYTES, TRUNCATION_MARKER
        self.assertEqual(truncate_response("short text"), "short text")

        oversized_ascii = "a" * (MAX_PUBLIC_RESPONSE_BYTES + 500)
        truncated_ascii = truncate_response(oversized_ascii)
        self.assertEqual(len(truncated_ascii.encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(truncated_ascii.endswith(TRUNCATION_MARKER))

        repeat = (MAX_PUBLIC_RESPONSE_BYTES // 4) + 100
        oversized_mb = "🎉" * repeat
        truncated_mb = truncate_response(oversized_mb)
        self.assertLessEqual(len(truncated_mb.encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(truncated_mb.endswith(TRUNCATION_MARKER))
        truncated_mb.encode("utf-8").decode("utf-8")

    def test_provider_outcome_and_finalization_enforce_response_bounds(self):
        from rally_core.model import ProviderOutcome, MAX_PUBLIC_RESPONSE_BYTES, TRUNCATION_MARKER
        oversized = "x" * (MAX_PUBLIC_RESPONSE_BYTES + 1000)
        outcome = ProviderOutcome(True, oversized, "sess-1", {}, None, "model-1", {})
        self.assertLessEqual(len(outcome.response.encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(outcome.response.endswith(TRUNCATION_MARKER))

    def test_agy_parses_large_transcript_with_diagnostics(self):
        raw_file = self.workspace / "large-agy.json"
        padding = ("x" * 1024 + "\n") * 3000
        terminal = json.dumps({"status": "SUCCESS", "response": "final agy answer", "conversation_id": "agy-large-sess", "usage": {"tokens": 100}})
        raw_file.write_text(padding + terminal + "\n")

        adapter = rally_core.AgyAdapter()
        req = rally_core.JobRequest(provider="agy", prompt="hi", cwd=self.workspace)
        outcome = adapter.parse(req, raw_file, self.workspace / "stderr.log", 0)
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.response, "final agy answer")
        self.assertEqual(outcome.session_id, "agy-large-sess")

    def test_jsonl_events_bounds_oversized_line_and_recovers_next_event(self):
        from rally_core.providers.base import _jsonl_events
        raw_file = self.workspace / "oversized.jsonl"
        with raw_file.open("wb") as stream:
            stream.write(b"x" * 2_000_000 + b"\n")
            stream.write(json.dumps({"type": "valid", "ok": True}).encode("utf-8") + b"\n")

        events = list(_jsonl_events(raw_file))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {"type": "valid", "ok": True})

    def test_lifecycle_finalization_and_read_bound_public_response(self):
        from rally_core.model import MAX_PUBLIC_RESPONSE_BYTES, TRUNCATION_MARKER
        store = rally_core.JobStore(self.workspace / "jobs")
        fixture = self.workspace / "fixture.py"
        fixture.write_text(
            "import json, os, sys\n"
            "resp = 'r' * 1_200_000\n"
            "print(json.dumps({'success': True, 'response': resp, 'session_id': 'sess-1', 'usage': {}}))\n"
        )
        fixture.chmod(0o700)
        with mock.patch.dict(os.environ, {"AGENT_JOB_TEST_FIXTURE_EXECUTABLE": str(fixture),
                                          "AGENT_JOB_LAUNCHER": "detached"}):
            manager = rally_core.JobManager(store, adapters={"_fixture": FixtureAdapter()})
            desc = manager.start(rally_core.JobRequest(provider="_fixture", prompt="bound check", cwd=self.workspace))
            result = manager.wait(desc["job_id"])
            self.assertEqual(result["status"], "SUCCESS")
            self.assertLessEqual(len(result["response"].encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
            self.assertTrue(result["response"].endswith(TRUNCATION_MARKER))

            # read command
            read_output = manager.read(desc["job_id"])
            self.assertEqual(read_output, result["response"])

            # private file response.txt is also bounded
            job_dir = store.get(desc["job_id"]).directory
            response_file = job_dir / "response.txt"
            self.assertLessEqual(len(response_file.read_bytes()), MAX_PUBLIC_RESPONSE_BYTES)

    def test_provider_result_response_fields_and_serialized_result_are_bounded(self):
        from rally_core.model import ProviderOutcome, MAX_PUBLIC_RESPONSE_BYTES, TRUNCATION_MARKER
        oversized = "y" * (MAX_PUBLIC_RESPONSE_BYTES + 5000)
        outcome = ProviderOutcome(True, oversized, "sess-1", {"tokens": 10}, None, "model-1",
                                  {"response": oversized, "result": oversized, "status": "SUCCESS", "keep_me": "val"})
        self.assertLessEqual(len(outcome.provider_result["response"].encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(outcome.provider_result["response"].endswith(TRUNCATION_MARKER))
        self.assertLessEqual(len(outcome.provider_result["result"].encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(outcome.provider_result["result"].endswith(TRUNCATION_MARKER))
        self.assertEqual(outcome.provider_result["status"], "SUCCESS")
        self.assertEqual(outcome.provider_result["keep_me"], "val")

    def test_provider_adapters_sanitize_nested_response_metadata_while_preserving_non_response_fields(self):
        from rally_core.model import MAX_PUBLIC_RESPONSE_BYTES, TRUNCATION_MARKER
        oversized = "z" * (MAX_PUBLIC_RESPONSE_BYTES + 2000)
        # AGY adapter
        agy_raw = self.workspace / "agy-oversized.json"
        agy_raw.write_text(json.dumps({"status": "SUCCESS", "response": oversized, "conversation_id": "sess-agy",
                                      "usage": {"tokens": 5}, "model": "agy-model"}))
        outcome_agy = rally_core.AgyAdapter().parse(agy_raw, 0)
        self.assertLessEqual(len(outcome_agy.provider_result["response"].encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(outcome_agy.provider_result["response"].endswith(TRUNCATION_MARKER))
        self.assertEqual(outcome_agy.provider_result["conversation_id"], "sess-agy")
        self.assertEqual(outcome_agy.provider_result["status"], "SUCCESS")

        # Claude terminal sanitization in _public_terminal
        from rally_core.providers.base import _public_terminal
        terminal_claude = _public_terminal({"type": "result", "subtype": "success", "is_error": False,
                                            "result": oversized, "session_id": "sess-claude",
                                            "total_cost_usd": 0.05, "duration_ms": 1200},
                                           rally_core.ClaudeAdapter.terminal_fields, "claude")
        self.assertLessEqual(len(terminal_claude["result"].encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(terminal_claude["result"].endswith(TRUNCATION_MARKER))
        self.assertEqual(terminal_claude["total_cost_usd"], 0.05)
        self.assertEqual(terminal_claude["duration_ms"], 1200)

    def test_lifecycle_finalization_bounds_nested_provider_result_and_serialized_public_result(self):
        from rally_core.model import MAX_PUBLIC_RESPONSE_BYTES, TRUNCATION_MARKER
        store = rally_core.JobStore(self.workspace / "jobs")
        oversized = "w" * (MAX_PUBLIC_RESPONSE_BYTES + 50_000)
        job = store.create(rally_core.JobRequest(provider="agy", prompt="test", cwd=self.workspace))
        outcome = rally_core.ProviderOutcome(
            True, oversized, "sess-1", {"tokens": 42}, None, "mod-1",
            {"response": oversized, "result": oversized, "tokens": 42, "status": "SUCCESS"}
        )
        manager = rally_core.JobManager(store, adapters={"_fixture": FixtureAdapter()})
        res = manager.finalize(job.job_id, status="SUCCESS", exit_code=0, outcome=outcome)
        self.assertLessEqual(len(res["provider_result"]["response"].encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(res["provider_result"]["response"].endswith(TRUNCATION_MARKER))
        self.assertLessEqual(len(res["provider_result"]["result"].encode("utf-8")), MAX_PUBLIC_RESPONSE_BYTES)
        self.assertTrue(res["provider_result"]["result"].endswith(TRUNCATION_MARKER))
        serialized = job.result.read_bytes()
        self.assertLessEqual(len(serialized), 3 * MAX_PUBLIC_RESPONSE_BYTES + 8192)

    def test_partial_session_bounds_oversized_no_result_transcript_without_read_text(self):
        raw_file = self.workspace / "oversized-no-result.jsonl"
        # 3 MiB of noise events with no session ID
        line = json.dumps({"type": "noise", "data": "x" * 500}) + "\n"
        raw_file.write_text(line * 6000)
        self.assertGreater(raw_file.stat().st_size, 3_000_000)

        original_read_text = Path.read_text

        def guarded_read_text(path_self, *args, **kwargs):
            if path_self.resolve() == raw_file.resolve():
                raise AssertionError("oversized no-result transcript called read_text()")
            return original_read_text(path_self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", guarded_read_text):
            for provider in ("agy", "copilot", "claude", "codex"):
                with self.subTest(provider=provider):
                    session = rally_core._partial_session(raw_file, provider)
                    self.assertIsNone(session)

    def test_provider_adapters_no_result_on_oversized_transcript_remain_bounded(self):
        raw_file = self.workspace / "oversized-no-result-adapter.jsonl"
        line = json.dumps({"type": "log", "msg": "y" * 500}) + "\n"
        raw_file.write_text(line * 6000)
        self.assertGreater(raw_file.stat().st_size, 3_000_000)

        original_read_text = Path.read_text

        def guarded_read_text(path_self, *args, **kwargs):
            if path_self.resolve() == raw_file.resolve():
                raise AssertionError("oversized no-result transcript called read_text()")
            return original_read_text(path_self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", guarded_read_text):
            outcome_agy = rally_core.AgyAdapter().parse(raw_file, 7)
            self.assertFalse(outcome_agy.success)
            self.assertIsNone(outcome_agy.session_id)
            self.assertEqual(outcome_agy.error["type"], "provider_exit")

            outcome_copilot = rally_core.CopilotAdapter().parse(raw_file, 7)
            self.assertFalse(outcome_copilot.success)
            self.assertIsNone(outcome_copilot.session_id)
            self.assertEqual(outcome_copilot.error["type"], "provider_exit")

    def test_partial_session_extracts_session_from_oversized_transcript_head_or_tail(self):
        head_file = self.workspace / "oversized-head-session.jsonl"
        head_event = json.dumps({"type": "thread.started", "thread_id": "head-session-id"}) + "\n"
        noise = (json.dumps({"type": "log", "data": "z" * 500}) + "\n") * 6000
        head_file.write_text(head_event + noise)
        self.assertGreater(head_file.stat().st_size, 3_000_000)
        self.assertEqual(rally_core._partial_session(head_file, "codex"), "head-session-id")

        tail_file = self.workspace / "oversized-tail-session.jsonl"
        tail_event = json.dumps({"type": "result", "session_id": "tail-session-id"}) + "\n"
        tail_file.write_text(noise + tail_event)
        self.assertGreater(tail_file.stat().st_size, 3_000_000)
        self.assertEqual(rally_core._partial_session(tail_file, "claude"), "tail-session-id")


if __name__ == "__main__":
    unittest.main()
