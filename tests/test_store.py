"""Unit tests for devpulse.store."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from devpulse import store


class StoreTestCase(unittest.TestCase):
    """Comprehensive tests for task/timer persistence, validation, and transitions."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp_dir.cleanup)
        self.root = Path(self._tmp_dir.name)
        self.store_path = self.root / "tasks.json"
        self.t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)

    def test_constants_and_exception_hierarchy(self) -> None:
        self.assertEqual(store.SCHEMA_VERSION, 1)
        self.assertEqual(store.VALID_STATUSES, ("todo", "in_progress", "done"))
        self.assertEqual(store.VALID_PRIORITIES, ("low", "medium", "high"))

        self.assertTrue(issubclass(store.StoreValidationError, store.DevPulseError))
        self.assertTrue(issubclass(store.StoreValidationError, ValueError))
        self.assertTrue(issubclass(store.StoreCorruptError, store.DevPulseError))
        self.assertTrue(issubclass(store.TaskNotFoundError, store.DevPulseError))
        self.assertTrue(issubclass(store.TaskNotFoundError, LookupError))
        self.assertTrue(
            issubclass(store.InvalidStateTransitionError, store.DevPulseError)
        )

    def test_timestamp_parse_and_format(self) -> None:
        self.assertEqual(store.format_utc_iso(self.t0), "2026-11-19T10:00:00Z")

        plus_two = timezone(timedelta(hours=2))
        local_dt = datetime(2026, 11, 19, 12, 0, 0, tzinfo=plus_two)
        self.assertEqual(store.format_utc_iso(local_dt), "2026-11-19T10:00:00Z")
        self.assertEqual(
            store.parse_utc_iso("2026-11-19T12:00:00+02:00"),
            self.t0,
        )
        self.assertEqual(store.parse_utc_iso("2026-11-19T10:00:00Z"), self.t0)

        now_iso = store.format_utc_iso()
        self.assertTrue(now_iso.endswith("Z"))
        self.assertEqual(store.parse_utc_iso(now_iso).tzinfo, timezone.utc)

        naive = datetime(2026, 11, 19, 10, 0, 0)
        with self.assertRaises(store.StoreValidationError):
            store.format_utc_iso(naive)
        with self.assertRaises(store.StoreValidationError):
            store.parse_utc_iso("2026-11-19T10:00:00")
        with self.assertRaises(store.StoreValidationError):
            store.parse_utc_iso(" 2026-11-19T10:00:00Z ")
        with self.assertRaises(store.StoreValidationError):
            store.parse_utc_iso("not-a-timestamp")
        with self.assertRaises(store.StoreValidationError):
            store.parse_utc_iso(123)  # type: ignore[arg-type]

    def test_validate_title_priority_and_tags(self) -> None:
        self.assertEqual(store.validate_title("  Build store  "), "Build store")
        self.assertEqual(store.validate_title("a" * 200), "a" * 200)
        for bad_title in ("", "   ", "line1\nline2", "line1\rline2", "bad\x00title", "x" * 201):
            with self.assertRaises(store.StoreValidationError):
                store.validate_title(bad_title)
        with self.assertRaises(store.StoreValidationError):
            store.validate_title(None)  # type: ignore[arg-type]

        self.assertEqual(store.validate_priority("low"), "low")
        self.assertEqual(store.validate_priority(" HIGH "), "high")
        with self.assertRaises(store.StoreValidationError):
            store.validate_priority("urgent")

        self.assertEqual(store.validate_tags(None), [])
        self.assertEqual(
            store.validate_tags([" api ", "cli", "api", "tests"]),
            ["api", "cli", "tests"],
        )
        self.assertEqual(store.validate_tags(["t" * 40]), ["t" * 40])
        for bad_tags in (
            "api",
            b"api",
            [""],
            ["   "],
            ["a,b"],
            ["a\nb"],
            ["a\x00b"],
            ["t" * 41],
            [123],
        ):
            with self.assertRaises(store.StoreValidationError):
                store.validate_tags(bad_tags)  # type: ignore[arg-type]

    def test_load_store_missing_does_not_create_file(self) -> None:
        self.assertFalse(self.store_path.exists())
        data = store.load_store(self.store_path)
        self.assertEqual(data, {"schema_version": 1, "next_id": 1, "tasks": []})
        self.assertFalse(self.store_path.exists())

    def test_load_store_corrupt_cases_preserve_file(self) -> None:
        valid_task = {
            "id": 1,
            "title": "Valid task",
            "status": "todo",
            "priority": "medium",
            "tags": [],
            "elapsed_seconds": 0,
            "active_timer_start": None,
            "created_at": "2026-11-19T10:00:00Z",
            "updated_at": "2026-11-19T10:00:00Z",
            "completed_at": None,
        }
        corrupt_payloads: list[bytes] = [
            b"",
            b"   \n\t ",
            b"\xff\xfe\x00invalid-utf8",
            b"{bad json",
            b"[]",
            json.dumps({"schema_version": 2, "next_id": 1, "tasks": []}).encode("utf-8"),
            json.dumps({"schema_version": True, "next_id": 1, "tasks": []}).encode("utf-8"),
            json.dumps({"schema_version": 1, "next_id": 0, "tasks": []}).encode("utf-8"),
            json.dumps({"schema_version": 1, "next_id": True, "tasks": []}).encode("utf-8"),
            json.dumps({"schema_version": 1, "next_id": 1, "tasks": "nope"}).encode("utf-8"),
            json.dumps(
                {"schema_version": 1, "next_id": 1, "tasks": [valid_task]}
            ).encode("utf-8"),
            json.dumps(
                {
                    "schema_version": 1,
                    "next_id": 3,
                    "tasks": [valid_task, dict(valid_task)],
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "schema_version": 1,
                    "next_id": 2,
                    "tasks": [{**valid_task, "elapsed_seconds": True}],
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "schema_version": 1,
                    "next_id": 2,
                    "tasks": [{**valid_task, "status": "unknown"}],
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "schema_version": 1,
                    "next_id": 2,
                    "tasks": [
                        {
                            **valid_task,
                            "status": "todo",
                            "active_timer_start": "2026-11-19T10:00:00Z",
                        }
                    ],
                }
            ).encode("utf-8"),
            json.dumps(
                {
                    "schema_version": 1,
                    "next_id": 2,
                    "tasks": [{**valid_task, "status": "done", "completed_at": None}],
                }
            ).encode("utf-8"),
        ]

        for idx, raw_bytes in enumerate(corrupt_payloads):
            corrupt_path = self.root / f"corrupt_{idx}.json"
            corrupt_path.write_bytes(raw_bytes)
            with self.subTest(idx=idx):
                with self.assertRaises(store.StoreCorruptError):
                    store.load_store(corrupt_path)
                self.assertEqual(corrupt_path.read_bytes(), raw_bytes)

    def test_save_store_utf8_no_bom_and_atomic_cleanup_on_error(self) -> None:
        nested_path = self.root / "nested" / "tasks.json"
        store.add_task(nested_path, "Unicode check: café 🚀", now=self.t0)
        raw = nested_path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        decoded = json.loads(raw.decode("utf-8"))
        self.assertEqual(decoded["tasks"][0]["title"], "Unicode check: café 🚀")
        leftover_tmps = list(nested_path.parent.glob("*.tmp"))
        self.assertEqual(leftover_tmps, [])

        original_bytes = raw
        with mock.patch("devpulse.store.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                store.add_task(nested_path, "Should fail cleanly", now=self.t0)

        self.assertEqual(nested_path.read_bytes(), original_bytes)
        self.assertEqual(list(nested_path.parent.glob("*.tmp")), [])

    def test_crud_and_monotonic_ids(self) -> None:
        t1 = store.add_task(
            self.store_path,
            "  First task  ",
            priority="high",
            tags=["core", "api", "core"],
            now=self.t0,
        )
        t2 = store.add_task(
            self.store_path,
            "Second task",
            priority="low",
            now=self.t0 + timedelta(minutes=1),
        )
        self.assertEqual(t1["id"], 1)
        self.assertEqual(t2["id"], 2)
        self.assertEqual(t1["title"], "First task")
        self.assertEqual(t1["tags"], ["core", "api"])

        fetched = store.get_task(self.store_path, 1)
        self.assertEqual(fetched, t1)

        deleted = store.delete_task(self.store_path, 1)
        self.assertEqual(deleted, t1)
        with self.assertRaises(store.TaskNotFoundError):
            store.get_task(self.store_path, 1)

        t3 = store.add_task(self.store_path, "Third task", now=self.t0)
        self.assertEqual(t3["id"], 3)

        all_tasks = store.list_tasks(self.store_path)
        self.assertEqual([t["id"] for t in all_tasks], [2, 3])
        self.assertEqual(
            [t["id"] for t in store.list_tasks(self.store_path, status="todo")],
            [2, 3],
        )
        self.assertEqual(store.list_tasks(self.store_path, status="done"), [])

        with self.assertRaises(store.StoreValidationError):
            store.list_tasks(self.store_path, status="invalid")  # type: ignore[arg-type]

    def test_timer_lifecycle_and_illegal_transitions(self) -> None:
        store.add_task(self.store_path, "Timer task", now=self.t0)

        # Cannot stop before starting
        with self.assertRaises(store.InvalidStateTransitionError):
            store.stop_task(self.store_path, 1, now=self.t0)

        # Start -> running
        started = store.start_task(
            self.store_path, 1, now=self.t0 + timedelta(minutes=5)
        )
        self.assertEqual(started["status"], "in_progress")
        self.assertEqual(started["active_timer_start"], "2026-11-19T10:05:00Z")

        # Cannot start while already running
        with self.assertRaises(store.InvalidStateTransitionError):
            store.start_task(self.store_path, 1, now=self.t0 + timedelta(minutes=6))

        # Cannot stop with negative delta
        with self.assertRaises(store.StoreValidationError):
            store.stop_task(self.store_path, 1, now=self.t0 + timedelta(minutes=4))

        # Stop pauses and accumulates elapsed_seconds
        stopped = store.stop_task(
            self.store_path, 1, now=self.t0 + timedelta(minutes=15)
        )
        self.assertEqual(stopped["status"], "in_progress")
        self.assertIsNone(stopped["active_timer_start"])
        self.assertEqual(stopped["elapsed_seconds"], 600)

        # Cannot stop when already paused
        with self.assertRaises(store.InvalidStateTransitionError):
            store.stop_task(self.store_path, 1, now=self.t0 + timedelta(minutes=16))

        # Resume paused task
        resumed = store.start_task(
            self.store_path, 1, now=self.t0 + timedelta(minutes=20)
        )
        self.assertEqual(resumed["status"], "in_progress")
        self.assertEqual(resumed["active_timer_start"], "2026-11-19T10:20:00Z")

        # Cannot finish with negative delta while running
        with self.assertRaises(store.StoreValidationError):
            store.done_task(self.store_path, 1, now=self.t0 + timedelta(minutes=19))

        # Finish running task settles remaining timer
        done = store.done_task(
            self.store_path, 1, now=self.t0 + timedelta(minutes=25)
        )
        self.assertEqual(done["status"], "done")
        self.assertIsNone(done["active_timer_start"])
        self.assertEqual(done["elapsed_seconds"], 900)
        self.assertEqual(done["completed_at"], "2026-11-19T10:25:00Z")

        # Cannot start, stop, or re-done a completed task
        with self.assertRaises(store.InvalidStateTransitionError):
            store.start_task(self.store_path, 1, now=self.t0 + timedelta(minutes=30))
        with self.assertRaises(store.InvalidStateTransitionError):
            store.stop_task(self.store_path, 1, now=self.t0 + timedelta(minutes=30))
        with self.assertRaises(store.InvalidStateTransitionError):
            store.done_task(self.store_path, 1, now=self.t0 + timedelta(minutes=30))

        # Can complete a todo task directly with 0 elapsed
        t2 = store.add_task(self.store_path, "Quick task", now=self.t0)
        t2_done = store.done_task(
            self.store_path, t2["id"], now=self.t0 + timedelta(minutes=2)
        )
        self.assertEqual(t2_done["status"], "done")
        self.assertEqual(t2_done["elapsed_seconds"], 0)

    def test_reject_bool_task_id_and_naive_now(self) -> None:
        store.add_task(self.store_path, "Task 1", now=self.t0)
        naive = datetime(2026, 11, 19, 10, 0, 0)

        with self.assertRaises(store.StoreValidationError):
            store.get_task(self.store_path, True)
        with self.assertRaises(store.StoreValidationError):
            store.start_task(self.store_path, False, now=self.t0)
        with self.assertRaises(store.StoreValidationError):
            store.stop_task(self.store_path, True, now=self.t0)
        with self.assertRaises(store.StoreValidationError):
            store.done_task(self.store_path, True, now=self.t0)
        with self.assertRaises(store.StoreValidationError):
            store.delete_task(self.store_path, True)

        with self.assertRaises(store.StoreValidationError):
            store.add_task(self.store_path, "Naive", now=naive)
        with self.assertRaises(store.StoreValidationError):
            store.start_task(self.store_path, 1, now=naive)
        with self.assertRaises(store.StoreValidationError):
            store.stop_task(self.store_path, 1, now=naive)
        with self.assertRaises(store.StoreValidationError):
            store.done_task(self.store_path, 1, now=naive)

    def test_validate_task_rejects_bool_and_invalid_schemas(self) -> None:
        base = {
            "id": 1,
            "title": "Sample",
            "status": "todo",
            "priority": "medium",
            "tags": ["a"],
            "elapsed_seconds": 0,
            "active_timer_start": None,
            "created_at": "2026-11-19T10:00:00Z",
            "updated_at": "2026-11-19T10:00:00Z",
            "completed_at": None,
        }
        self.assertEqual(store.validate_task(base), base)

        with self.assertRaises(store.StoreValidationError):
            store.validate_task({**base, "id": True})
        with self.assertRaises(store.StoreValidationError):
            store.validate_task({**base, "elapsed_seconds": False})
        with self.assertRaises(store.StoreValidationError):
            store.validate_task({**base, "elapsed_seconds": -1})
        with self.assertRaises(store.StoreValidationError):
            store.validate_task({**base, "tags": None})
        with self.assertRaises(store.StoreValidationError):
            store.validate_task({**base, "completed_at": "2026-11-19T10:00:00Z"})

        missing_key = dict(base)
        del missing_key["title"]
        with self.assertRaises(store.StoreValidationError):
            store.validate_task(missing_key)

    # ------------------------------------------------------------------
    # Review round 1 additions (PR #5) — map to [COORD:REVIEW] findings
    # ------------------------------------------------------------------

    def _valid_task(self, **overrides: object) -> dict:
        task = {
            "id": 1,
            "title": "Valid task",
            "status": "todo",
            "priority": "medium",
            "tags": [],
            "elapsed_seconds": 0,
            "active_timer_start": None,
            "created_at": "2026-11-19T10:00:00Z",
            "updated_at": "2026-11-19T10:00:00Z",
            "completed_at": None,
        }
        task.update(overrides)
        return task

    def test_save_store_valid_persist_and_schema_validation(self) -> None:
        """Finding 1 [MAJOR]: direct save_store() persistence + schema rejections (AC #4)."""
        task = self._valid_task()

        # Valid payload persists and round-trips through load_store.
        returned = store.save_store(
            self.store_path, {"schema_version": 1, "next_id": 2, "tasks": [task]}
        )
        self.assertEqual(Path(returned), self.store_path)
        self.assertTrue(self.store_path.exists())
        reloaded = store.load_store(self.store_path)
        self.assertEqual(reloaded["schema_version"], 1)
        self.assertEqual(reloaded["next_id"], 2)
        self.assertEqual([t["id"] for t in reloaded["tasks"]], [1])

        # Invalid schema_version.
        with self.assertRaises(store.StoreValidationError):
            store.save_store(
                self.store_path, {"schema_version": 2, "next_id": 2, "tasks": [task]}
            )
        # next_id <= max existing task id.
        with self.assertRaises(store.StoreValidationError):
            store.save_store(
                self.store_path, {"schema_version": 1, "next_id": 1, "tasks": [task]}
            )
        # next_id == 0 (below minimum).
        with self.assertRaises(store.StoreValidationError):
            store.save_store(
                self.store_path, {"schema_version": 1, "next_id": 0, "tasks": []}
            )
        # next_id as bool must be rejected (bool is not a valid int here).
        with self.assertRaises(store.StoreValidationError):
            store.save_store(
                self.store_path, {"schema_version": 1, "next_id": True, "tasks": []}
            )
        # tasks not a list.
        with self.assertRaises(store.StoreValidationError):
            store.save_store(
                self.store_path, {"schema_version": 1, "next_id": 2, "tasks": "nope"}
            )
        # Root not a mapping.
        with self.assertRaises(store.StoreValidationError):
            store.save_store(self.store_path, [1, 2])  # type: ignore[arg-type]
        # Task item not a mapping.
        with self.assertRaises(store.StoreValidationError):
            store.save_store(
                self.store_path, {"schema_version": 1, "next_id": 2, "tasks": [123]}
            )
        # Duplicate task IDs.
        with self.assertRaises(store.StoreValidationError):
            store.save_store(
                self.store_path,
                {"schema_version": 1, "next_id": 3, "tasks": [task, dict(task)]},
            )

    def test_task_not_found_on_mutating_ops_and_empty_list_for_missing_store(self) -> None:
        """Finding 2 [MAJOR]: delete/start/stop/done raise TaskNotFoundError; list_tasks([]) on missing."""
        store.add_task(self.store_path, "Only task", now=self.t0)

        with self.assertRaises(store.TaskNotFoundError):
            store.delete_task(self.store_path, 999)
        with self.assertRaises(store.TaskNotFoundError):
            store.start_task(self.store_path, 999, now=self.t0)
        with self.assertRaises(store.TaskNotFoundError):
            store.stop_task(self.store_path, 999, now=self.t0)
        with self.assertRaises(store.TaskNotFoundError):
            store.done_task(self.store_path, 999, now=self.t0)

        missing_path = self.root / "does_not_exist.json"
        self.assertFalse(missing_path.exists())
        self.assertEqual(store.list_tasks(missing_path), [])
        self.assertFalse(missing_path.exists())

    def test_validate_task_and_load_store_schema_corruption_edge_cases(self) -> None:
        """Finding 3 [MAJOR]: validate_task non-mapping + bad timestamps; load_store non-dict item + BOM."""
        # validate_task: non-mapping input.
        with self.assertRaises(store.StoreValidationError):
            store.validate_task([1, 2])  # type: ignore[arg-type]

        # validate_task: invalid / non-string / naive timestamp fields.
        for field in ("created_at", "updated_at"):
            with self.subTest(field=field, case="naive"):
                with self.assertRaises(store.StoreValidationError):
                    store.validate_task(self._valid_task(**{field: "2026-11-19T10:00:00"}))
            with self.subTest(field=field, case="non_string"):
                with self.assertRaises(store.StoreValidationError):
                    store.validate_task(self._valid_task(**{field: 123}))
            with self.subTest(field=field, case="garbage"):
                with self.assertRaises(store.StoreValidationError):
                    store.validate_task(self._valid_task(**{field: "not-a-date"}))

        # active_timer_start: naive string (and only valid on in_progress).
        with self.assertRaises(store.StoreValidationError):
            store.validate_task(
                self._valid_task(status="in_progress", active_timer_start="2026-11-19T10:00:00")
            )
        with self.assertRaises(store.StoreValidationError):
            store.validate_task(self._valid_task(active_timer_start=123))

        # completed_at: naive / non-string string when status is done.
        with self.assertRaises(store.StoreValidationError):
            store.validate_task(
                self._valid_task(status="done", completed_at="2026-11-19T10:00:00")
            )

        # status=done with a non-None active_timer_start is rejected.
        with self.assertRaises(store.StoreValidationError):
            store.validate_task(
                self._valid_task(
                    status="done",
                    completed_at="2026-11-19T10:00:00Z",
                    active_timer_start="2026-11-19T10:00:00Z",
                )
            )

        # load_store: tasks list containing a non-dict item -> StoreCorruptError, file preserved.
        bad_items_path = self.root / "bad_items.json"
        bad_items_bytes = json.dumps(
            {"schema_version": 1, "next_id": 2, "tasks": [123]}
        ).encode("utf-8")
        bad_items_path.write_bytes(bad_items_bytes)
        with self.assertRaises(store.StoreCorruptError):
            store.load_store(bad_items_path)
        self.assertEqual(bad_items_path.read_bytes(), bad_items_bytes)

        # load_store: UTF-8 BOM-prefixed store file is rejected and bytes preserved untouched.
        bom_path = self.root / "bom.json"
        bom_bytes = b"\xef\xbb\xbf" + json.dumps(
            {"schema_version": 1, "next_id": 2, "tasks": [self._valid_task()]}
        ).encode("utf-8")
        bom_path.write_bytes(bom_bytes)
        with self.assertRaises(store.StoreCorruptError):
            store.load_store(bom_path)
        self.assertEqual(bom_path.read_bytes(), bom_bytes)

    def test_defensive_copy_isolation_and_in_progress_filter(self) -> None:
        """Finding 4 [MINOR]: returned dicts/tags are copies; list_tasks(status=in_progress) filters."""
        created = store.add_task(
            self.store_path, "Isolate me", tags=["api", "core"], now=self.t0
        )

        # Mutating the add_task return value must not affect persisted state.
        created["title"] = "HACKED"
        created["tags"].append("injected")
        persisted = store.get_task(self.store_path, created["id"])
        self.assertEqual(persisted["title"], "Isolate me")
        self.assertEqual(persisted["tags"], ["api", "core"])

        # Mutating a get_task return value must not affect persisted state.
        fetched = store.get_task(self.store_path, created["id"])
        fetched["tags"].append("more")
        self.assertEqual(
            store.get_task(self.store_path, created["id"])["tags"], ["api", "core"]
        )

        # Mutating a list_tasks return value must not affect persisted state.
        listed = store.list_tasks(self.store_path)
        listed[0]["tags"].append("leak")
        self.assertEqual(
            store.list_tasks(self.store_path)[0]["tags"], ["api", "core"]
        )

        # status=in_progress filtering returns only running/paused tasks.
        store.add_task(self.store_path, "Second", now=self.t0)
        store.start_task(self.store_path, created["id"], now=self.t0 + timedelta(minutes=1))
        in_progress = store.list_tasks(self.store_path, status="in_progress")
        self.assertEqual([t["id"] for t in in_progress], [created["id"]])
        self.assertEqual(
            [t["id"] for t in store.list_tasks(self.store_path, status="todo")],
            [2],
        )


if __name__ == "__main__":
    unittest.main()
