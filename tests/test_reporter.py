"""Unit tests for ``devpulse.reporter``."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from devpulse.reporter import (
    compute_sprint_summary,
    compute_task_duration_seconds,
    format_duration,
    render_html_report,
    render_markdown_report,
    render_report,
)


def _make_task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "id": 1,
        "title": "Implement reporter",
        "status": "todo",
        "priority": "medium",
        "tags": ["backend"],
        "created_at": "2026-11-19T10:00:00Z",
        "updated_at": "2026-11-19T10:05:00Z",
        "started_at": None,
        "completed_at": None,
        "elapsed_seconds": 0,
        "active_timer_start": None,
    }
    task.update(overrides)
    return task


class TestFormatDuration(unittest.TestCase):
    """Tests for ``format_duration``."""

    def test_formats_valid_durations(self) -> None:
        self.assertEqual(format_duration(0), "00:00:00")
        self.assertEqual(format_duration(5), "00:00:05")
        self.assertEqual(format_duration(65), "00:01:05")
        self.assertEqual(format_duration(3661), "01:01:01")
        self.assertEqual(format_duration(360000), "100:00:00")

    def test_rejects_negative_bool_and_non_int(self) -> None:
        for invalid in (-1, -3600, True, False, 1.5, "10", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    format_duration(invalid)  # type: ignore[arg-type]


class TestComputeTaskDurationSeconds(unittest.TestCase):
    """Tests for ``compute_task_duration_seconds``."""

    def test_returns_elapsed_seconds_when_timer_inactive(self) -> None:
        task = _make_task(elapsed_seconds=125, active_timer_start=None)
        self.assertEqual(compute_task_duration_seconds(task), 125)

    def test_adds_active_timer_delta_with_explicit_now(self) -> None:
        now = datetime(2026, 11, 19, 12, 30, 15, tzinfo=timezone.utc)
        task = _make_task(
            elapsed_seconds=60,
            active_timer_start="2026-11-19T12:00:00Z",
        )
        self.assertEqual(compute_task_duration_seconds(task, now=now), 60 + 1815)

    def test_supports_timezone_offset_timestamps(self) -> None:
        tz_plus2 = timezone(timedelta(hours=2))
        now = datetime(2026, 11, 19, 14, 0, 30, tzinfo=tz_plus2)
        task = _make_task(
            elapsed_seconds=10,
            active_timer_start="2026-11-19T12:00:00+00:00",
        )
        self.assertEqual(compute_task_duration_seconds(task, now=now), 40)

    def test_defaults_now_when_none(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        task = _make_task(elapsed_seconds=10, active_timer_start=past)
        duration = compute_task_duration_seconds(task)
        self.assertGreaterEqual(duration, 14)

    def test_validation_errors(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            compute_task_duration_seconds(_make_task(elapsed_seconds=-1), now=now)
        with self.assertRaises(ValueError):
            compute_task_duration_seconds(_make_task(elapsed_seconds=True), now=now)
        with self.assertRaises(ValueError):
            compute_task_duration_seconds(_make_task(elapsed_seconds=False), now=now)
        with self.assertRaises(ValueError):
            compute_task_duration_seconds(
                _make_task(active_timer_start="2026-11-19T12:00:01Z"),
                now=now,
            )
        with self.assertRaises(ValueError):
            compute_task_duration_seconds(
                _make_task(),
                now=datetime(2026, 11, 19, 12, 0, 0),
            )
        with self.assertRaises(ValueError):
            compute_task_duration_seconds(
                _make_task(active_timer_start="2026-11-19T11:00:00"),
                now=now,
            )


class TestComputeSprintSummary(unittest.TestCase):
    """Tests for ``compute_sprint_summary``."""

    def test_empty_tasks_returns_zero_metrics(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
        summary = compute_sprint_summary([], now=now)
        self.assertEqual(
            summary,
            {
                "total_tasks": 0,
                "status_counts": {"todo": 0, "in_progress": 0, "done": 0},
                "priority_counts": {"low": 0, "medium": 0, "high": 0},
                "completion_rate_pct": 0.0,
                "total_duration_seconds": 0,
                "total_duration_formatted": "00:00:00",
                "active_timers": 0,
            },
        )

    def test_computes_aggregated_counts_and_durations(self) -> None:
        now = datetime(2026, 11, 19, 12, 10, 0, tzinfo=timezone.utc)
        tasks = [
            _make_task(id=1, status="done", priority="high", elapsed_seconds=3600),
            _make_task(
                id=2,
                status="in_progress",
                priority="medium",
                elapsed_seconds=120,
                active_timer_start="2026-11-19T12:05:00Z",
            ),
            _make_task(id=3, status="todo", priority="low", elapsed_seconds=45),
        ]
        summary = compute_sprint_summary(tasks, now=now)
        self.assertEqual(summary["total_tasks"], 3)
        self.assertEqual(summary["status_counts"], {"todo": 1, "in_progress": 1, "done": 1})
        self.assertEqual(summary["priority_counts"], {"low": 1, "medium": 1, "high": 1})
        self.assertEqual(summary["completion_rate_pct"], 33.3)
        self.assertEqual(summary["total_duration_seconds"], 3600 + 120 + 300 + 45)
        self.assertEqual(summary["total_duration_formatted"], "01:07:45")
        self.assertEqual(summary["active_timers"], 1)

    def test_rejects_unknown_status_or_priority_or_naive_now(self) -> None:
        with self.assertRaises(ValueError):
            compute_sprint_summary([_make_task(status="blocked")])
        with self.assertRaises(ValueError):
            compute_sprint_summary([_make_task(priority="urgent")])
        with self.assertRaises(ValueError):
            compute_sprint_summary([], now=datetime(2026, 11, 19, 12, 0, 0))


class TestRenderMarkdownReport(unittest.TestCase):
    """Tests for ``render_markdown_report``."""

    def test_empty_state_renders_zero_metrics_and_notice(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
        md = render_markdown_report([], sprint_title="Hackathon Sprint", now=now)
        self.assertIn("# Hackathon Sprint", md)
        self.assertIn("**Total Tasks:** 0", md)
        self.assertIn("0.0%", md)
        self.assertIn("00:00:00", md)
        self.assertIn("No tasks recorded in this sprint.", md)

    def test_escapes_pipes_and_newlines_in_table_cells(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
        tasks = [
            _make_task(
                id="T|1\n",
                title="Fix A | B\r\nNext line\nThird",
                status="in_progress",
                priority="high",
                tags=["api|v2", "line1\nline2"],
                elapsed_seconds=90,
            )
        ]
        md = render_markdown_report(tasks, sprint_title="Sprint\nOne", now=now)
        self.assertIn("# Sprint One", md)
        self.assertIn("Fix A \\| B Next line Third", md)
        self.assertIn("api\\|v2, line1 line2", md)
        self.assertIn("T\\|1 ", md)

        table_lines = [line for line in md.splitlines() if line.startswith("| ")]
        self.assertEqual(len(table_lines), 3)


class TestRenderHtmlReport(unittest.TestCase):
    """Tests for ``render_html_report``."""

    def test_empty_state_standalone_html5(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
        output = render_html_report([], now=now)
        self.assertTrue(output.startswith("<!DOCTYPE html>"))
        self.assertIn('<html lang="en">', output)
        self.assertIn('<meta charset="utf-8">', output)
        self.assertIn("<style>", output)
        self.assertIn("No tasks recorded in this sprint.", output)
        self.assertNotIn("http://", output)
        self.assertNotIn("https://", output)

    def test_escapes_xss_payloads_in_title_task_and_tags(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
        payload_script = "<script>alert('xss')</script>"
        payload_img = '"><img src=x onerror=alert(1)>'
        tasks = [
            _make_task(
                id=payload_img,
                title=f"Task {payload_script} & {payload_img}",
                status="done",
                priority="high",
                tags=[payload_script, payload_img],
                updated_at=payload_script,
                elapsed_seconds=120,
            )
        ]
        output = render_html_report(
            tasks,
            sprint_title=f"Sprint {payload_script} {payload_img}",
            now=now,
        )

        self.assertNotIn("<script>", output)
        self.assertNotIn("<img", output)
        self.assertNotIn('"><img', output)
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", output)
        self.assertIn("&quot;&gt;&lt;img src=x onerror=alert(1)&gt;", output)
        self.assertIn("00:02:00", output)
        self.assertIn("100.0%", output)


class TestRenderReportAndDecoupling(unittest.TestCase):
    """Tests for ``render_report`` dispatch and strict decoupling."""

    def test_render_report_dispatches_formats(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
        tasks = [_make_task(status="done", elapsed_seconds=30)]
        self.assertEqual(
            render_report(tasks, fmt="md", now=now),
            render_markdown_report(tasks, now=now),
        )
        self.assertEqual(
            render_report(tasks, fmt="markdown", now=now),
            render_markdown_report(tasks, now=now),
        )
        self.assertEqual(
            render_report(tasks, fmt="html", now=now),
            render_html_report(tasks, now=now),
        )
        for bad_fmt in ("txt", "json", "", "HTML"):
            with self.subTest(fmt=bad_fmt):
                with self.assertRaises(ValueError):
                    render_report(tasks, fmt=bad_fmt, now=now)

    def test_reporter_has_zero_imports_from_devpulse_store(self) -> None:
        reporter_path = (
            Path(__file__).resolve().parent.parent / "src" / "devpulse" / "reporter.py"
        )
        tree = ast.parse(reporter_path.read_text(encoding="utf-8"))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)

        self.assertFalse(
            any("store" in mod or mod.startswith("devpulse") for mod in imported_modules),
            f"Forbidden internal import found: {imported_modules}",
        )
        self.assertEqual(
            imported_modules,
            {"__future__", "datetime", "html", "typing"},
        )


if __name__ == "__main__":
    unittest.main()
