"""End-to-end subprocess tests for the DevPulse CLI (``devpulse.cli``).

Each test spawns ``python -m devpulse.cli`` as a child process with
``PYTHONPATH=src`` so the ``devpulse`` package is importable, exercising the
real argparse entry point, exit codes, and generated report files.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Repository root = two levels up from this file (tests/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"


def _child_env() -> dict[str, str]:
    """Return a child environment with PYTHONPATH pointed at ``src``."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(SRC_DIR) + (os.pathsep + existing if existing else "")
    )
    # Ensure a clean, deterministic store location per invocation unless the
    # test overrides it explicitly via --store or DEVPULSE_STORE.
    env.pop("DEVPULSE_STORE", None)
    return env


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run ``python -m devpulse.cli`` with the given args and captured output."""
    return subprocess.run(
        [sys.executable, "-m", "devpulse.cli", *args],
        capture_output=True,
        text=True,
        env=env if env is not None else _child_env(),
    )


class CliLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.store_path = self.tmp_path / "store.json"

    def test_add_start_stop_done_list_report_flow(self) -> None:
        store = str(self.store_path)

        # add
        res = _run("--store", store, "add", "Build CLI", "--priority", "high", "--tag", "cli")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Added task #1: Build CLI", res.stdout)
        self.assertIn("status=todo", res.stdout)
        self.assertIn("priority=high", res.stdout)

        # start
        res = _run("--store", store, "start", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Started task #1: Build CLI", res.stdout)
        self.assertIn("status=in_progress", res.stdout)

        # stop
        res = _run("--store", store, "stop", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Stopped task #1: Build CLI", res.stdout)
        self.assertIn("elapsed=", res.stdout)

        # done
        res = _run("--store", store, "done", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Completed task #1: Build CLI", res.stdout)
        self.assertIn("status=done", res.stdout)
        self.assertIn("elapsed=", res.stdout)

        # list
        res = _run("--store", store, "list")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("#1", res.stdout)
        self.assertIn("Build CLI", res.stdout)
        self.assertIn("[done]", res.stdout)

        # list filtered by status
        res = _run("--store", store, "list", "--status", "done")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Build CLI", res.stdout)

        res = _run("--store", store, "list", "--status", "todo")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("No tasks found.", res.stdout)

        # report html --out
        out_html = self.tmp_path / "nested" / "report.html"
        res = _run(
            "--store", store, "report", "--format", "html",
            "--out", str(out_html), "--title", "Sprint One",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(f"Wrote html report to {out_html}", res.stdout)
        self.assertTrue(out_html.exists())
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", html_text)
        self.assertIn("Sprint One", html_text)
        self.assertIn("Build CLI", html_text)
        # UTF-8 no BOM
        self.assertFalse(out_html.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_store_flag_before_and_after_subcommand(self) -> None:
        store = str(self.store_path)

        # --store BEFORE the subcommand
        res = _run("--store", store, "add", "Before task")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Added task #1: Before task", res.stdout)

        # --store AFTER the subcommand (same store -> next id is 2)
        res = _run("add", "After task", "--store", store)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Added task #2: After task", res.stdout)

        res = _run("list", "--store", store)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Before task", res.stdout)
        self.assertIn("After task", res.stdout)

    def test_devpulse_store_env_fallback(self) -> None:
        env = _child_env()
        env["DEVPULSE_STORE"] = str(self.store_path)
        res = _run("add", "Env task", env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Added task #1: Env task", res.stdout)
        self.assertTrue(self.store_path.exists())

    def test_html_report_escapes_xss(self) -> None:
        store = str(self.store_path)
        payload = '<script>alert("xss")</script>'

        res = _run("--store", store, "add", payload)
        self.assertEqual(res.returncode, 0, res.stderr)

        out_html = self.tmp_path / "xss.html"
        res = _run("--store", store, "report", "--format", "html", "--out", str(out_html))
        self.assertEqual(res.returncode, 0, res.stderr)

        html_text = out_html.read_text(encoding="utf-8")
        # Raw payload must NOT appear; escaped entities must.
        self.assertNotIn("<script>alert", html_text)
        self.assertIn("&lt;script&gt;", html_text)
        self.assertIn("&quot;", html_text)

    def test_markdown_report_to_stdout(self) -> None:
        store = str(self.store_path)
        res = _run("--store", store, "add", "Doc task")
        self.assertEqual(res.returncode, 0, res.stderr)

        res = _run("--store", store, "report", "--format", "md")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("# DevPulse Sprint Report", res.stdout)
        self.assertIn("Doc task", res.stdout)

    def test_exit_code_0_on_success(self) -> None:
        res = _run("--store", str(self.store_path), "add", "OK task")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_exit_code_1_on_domain_error(self) -> None:
        # Starting a non-existent task raises TaskNotFoundError (DevPulseError).
        res = _run("--store", str(self.store_path), "start", "999")
        self.assertEqual(res.returncode, 1)
        self.assertIn("Error:", res.stderr)
        # No raw traceback leaked to stderr.
        self.assertNotIn("Traceback (most recent call last)", res.stderr)

    def test_exit_code_1_on_value_error(self) -> None:
        # Empty/whitespace title triggers StoreValidationError (ValueError).
        res = _run("--store", str(self.store_path), "add", "   ")
        self.assertEqual(res.returncode, 1)
        self.assertIn("Error:", res.stderr)
        self.assertNotIn("Traceback (most recent call last)", res.stderr)

    def test_exit_code_2_on_usage_error(self) -> None:
        # Unknown subcommand is an argparse usage error -> exit code 2.
        res = _run("--store", str(self.store_path), "bogus-command")
        self.assertEqual(res.returncode, 2)

    def test_exit_code_2_on_invalid_choice(self) -> None:
        res = _run("--store", str(self.store_path), "add", "x", "--priority", "urgent")
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
