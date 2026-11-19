"""JSON task and timer store with strict validation and atomic persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION: int = 1
VALID_STATUSES: tuple[str, ...] = ("todo", "in_progress", "done")
VALID_PRIORITIES: tuple[str, ...] = ("low", "medium", "high")

MAX_TITLE_LENGTH: int = 200
MAX_TAG_LENGTH: int = 40

__all__ = [
    "SCHEMA_VERSION",
    "VALID_STATUSES",
    "VALID_PRIORITIES",
    "DevPulseError",
    "StoreValidationError",
    "StoreCorruptError",
    "TaskNotFoundError",
    "InvalidStateTransitionError",
    "parse_utc_iso",
    "format_utc_iso",
    "validate_title",
    "validate_priority",
    "validate_tags",
    "validate_task",
    "load_store",
    "save_store",
    "add_task",
    "get_task",
    "list_tasks",
    "start_task",
    "stop_task",
    "done_task",
    "delete_task",
]


class DevPulseError(Exception):
    """Base domain exception for DevPulse."""


class StoreValidationError(DevPulseError, ValueError):
    """Raised when input arguments or task fields fail validation."""


class StoreCorruptError(DevPulseError):
    """Raised when an existing store file cannot be parsed or fails schema checks."""


class TaskNotFoundError(DevPulseError, LookupError):
    """Raised when a requested task ID does not exist in the store."""


class InvalidStateTransitionError(DevPulseError):
    """Raised when a task or timer transition is illegal for its current state."""


def _validate_int(value: Any, field_name: str, *, min_value: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreValidationError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value < min_value:
        raise StoreValidationError(
            f"{field_name} must be >= {min_value}, got {value}"
        )
    return value


def _resolve_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime):
        raise StoreValidationError(
            f"now must be a datetime or None, got {type(now).__name__}"
        )
    if now.tzinfo is None or now.utcoffset() is None:
        raise StoreValidationError("now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc)


def parse_utc_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp with timezone info into a UTC datetime."""
    if not isinstance(value, str):
        raise StoreValidationError(
            f"Timestamp must be a string, got {type(value).__name__}"
        )
    stripped = value.strip()
    if not stripped or stripped != value:
        raise StoreValidationError("Timestamp must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(stripped)
    except ValueError as exc:
        raise StoreValidationError(f"Invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StoreValidationError(
            f"Timestamp must include timezone info: {value!r}"
        )
    return parsed.astimezone(timezone.utc)


def format_utc_iso(dt: datetime | None = None) -> str:
    """Format a timezone-aware datetime (or current UTC time) as UTC ISO-8601."""
    utc_dt = _resolve_now(dt)
    return utc_dt.isoformat().replace("+00:00", "Z")


def validate_title(title: str) -> str:
    """Validate and normalize a task title."""
    if not isinstance(title, str):
        raise StoreValidationError(
            f"Title must be a string, got {type(title).__name__}"
        )
    if "\n" in title or "\r" in title or "\x00" in title:
        raise StoreValidationError("Title must not contain newlines or NUL bytes")
    cleaned = title.strip()
    if not cleaned:
        raise StoreValidationError("Title must not be empty or whitespace-only")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise StoreValidationError(
            f"Title must not exceed {MAX_TITLE_LENGTH} characters"
        )
    return cleaned


def validate_priority(priority: str) -> str:
    """Validate and normalize a task priority."""
    if not isinstance(priority, str):
        raise StoreValidationError(
            f"Priority must be a string, got {type(priority).__name__}"
        )
    normalized = priority.strip().lower()
    if normalized not in VALID_PRIORITIES:
        raise StoreValidationError(
            f"Priority must be one of {VALID_PRIORITIES}, got {priority!r}"
        )
    return normalized


def validate_tags(tags: Sequence[str] | None) -> list[str]:
    """Validate and deduplicate tags while preserving order."""
    if tags is None:
        return []
    if isinstance(tags, (str, bytes)) or not isinstance(tags, Sequence):
        raise StoreValidationError(
            f"Tags must be a sequence of strings, got {type(tags).__name__}"
        )
    seen: set[str] = set()
    result: list[str] = []
    for raw_tag in tags:
        if not isinstance(raw_tag, str):
            raise StoreValidationError(
                f"Each tag must be a string, got {type(raw_tag).__name__}"
            )
        if "," in raw_tag or "\n" in raw_tag or "\r" in raw_tag or "\x00" in raw_tag:
            raise StoreValidationError(
                f"Tag {raw_tag!r} contains forbidden characters (comma/newline/NUL)"
            )
        cleaned = raw_tag.strip()
        if not cleaned:
            raise StoreValidationError("Tags must not be empty or whitespace-only")
        if len(cleaned) > MAX_TAG_LENGTH:
            raise StoreValidationError(
                f"Tag {cleaned!r} exceeds maximum length of {MAX_TAG_LENGTH}"
            )
        if cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _validate_optional_timestamp(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StoreValidationError(
            f"{field_name} must be an ISO-8601 string or None, got {type(value).__name__}"
        )
    return format_utc_iso(parse_utc_iso(value))


def validate_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a task mapping into canonical dict form."""
    if not isinstance(task, Mapping):
        raise StoreValidationError(
            f"Task must be a mapping, got {type(task).__name__}"
        )

    required_keys = (
        "id",
        "title",
        "status",
        "priority",
        "tags",
        "elapsed_seconds",
        "active_timer_start",
        "created_at",
        "updated_at",
        "completed_at",
    )
    missing = [key for key in required_keys if key not in task]
    if missing:
        raise StoreValidationError(f"Task missing required keys: {', '.join(missing)}")

    task_id = _validate_int(task["id"], "id", min_value=1)
    title = validate_title(task["title"])

    status = task["status"]
    if not isinstance(status, str) or status not in VALID_STATUSES:
        raise StoreValidationError(
            f"Task status must be one of {VALID_STATUSES}, got {status!r}"
        )

    priority = validate_priority(task["priority"])

    raw_tags = task["tags"]
    if raw_tags is None:
        raise StoreValidationError("Task 'tags' must be a sequence of strings, not None")
    tags = validate_tags(raw_tags)

    elapsed_seconds = _validate_int(task["elapsed_seconds"], "elapsed_seconds", min_value=0)
    active_timer_start = _validate_optional_timestamp(
        task["active_timer_start"], "active_timer_start"
    )
    created_at = format_utc_iso(parse_utc_iso(task["created_at"]))
    updated_at = format_utc_iso(parse_utc_iso(task["updated_at"]))
    completed_at = _validate_optional_timestamp(task["completed_at"], "completed_at")

    if active_timer_start is not None and status != "in_progress":
        raise StoreValidationError(
            "active_timer_start may only be set when status is 'in_progress'"
        )
    if status == "done" and completed_at is None:
        raise StoreValidationError("completed_at must be set when status is 'done'")
    if status != "done" and completed_at is not None:
        raise StoreValidationError("completed_at must be None unless status is 'done'")

    return {
        "id": task_id,
        "title": title,
        "status": status,
        "priority": priority,
        "tags": tags,
        "elapsed_seconds": elapsed_seconds,
        "active_timer_start": active_timer_start,
        "created_at": created_at,
        "updated_at": updated_at,
        "completed_at": completed_at,
    }


def _validate_store_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise StoreValidationError(
            f"Store root must be a mapping, got {type(data).__name__}"
        )
    if "schema_version" not in data or "next_id" not in data or "tasks" not in data:
        raise StoreValidationError(
            "Store root must contain 'schema_version', 'next_id', and 'tasks'"
        )

    schema_version = _validate_int(data["schema_version"], "schema_version", min_value=1)
    if schema_version != SCHEMA_VERSION:
        raise StoreValidationError(
            f"Unsupported schema_version {schema_version}; expected {SCHEMA_VERSION}"
        )

    next_id = _validate_int(data["next_id"], "next_id", min_value=1)

    raw_tasks = data["tasks"]
    if not isinstance(raw_tasks, list):
        raise StoreValidationError("Store 'tasks' must be a list")

    validated_tasks: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    max_id = 0
    for raw_task in raw_tasks:
        clean_task = validate_task(raw_task)
        tid = clean_task["id"]
        if tid in seen_ids:
            raise StoreValidationError(f"Duplicate task id {tid} in store")
        seen_ids.add(tid)
        if tid > max_id:
            max_id = tid
        validated_tasks.append(clean_task)

    if next_id <= max_id:
        raise StoreValidationError(
            f"next_id ({next_id}) must be greater than max existing task id ({max_id})"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "next_id": next_id,
        "tasks": validated_tasks,
    }


def load_store(store_path: Path | str) -> dict[str, Any]:
    """Load and validate the JSON task store, or return a fresh store if missing."""
    path = Path(store_path)
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "next_id": 1,
            "tasks": [],
        }

    if path.is_dir():
        raise StoreCorruptError(f"Store path is a directory: {path}")

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise StoreCorruptError(f"Unable to read store file {path}: {exc}") from exc

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StoreCorruptError(f"Store file {path} is not valid UTF-8") from exc

    if not raw_text.strip():
        raise StoreCorruptError(f"Store file {path} is empty")

    try:
        raw_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise StoreCorruptError(f"Store file {path} contains invalid JSON: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise StoreCorruptError("Store root JSON value must be an object")

    try:
        return _validate_store_payload(raw_data)
    except StoreValidationError as exc:
        raise StoreCorruptError(f"Store schema invalid: {exc}") from exc


def save_store(store_path: Path | str, data: Mapping[str, Any]) -> Path:
    """Validate and atomically persist the store as UTF-8 JSON (no BOM)."""
    path = Path(store_path)
    validated = _validate_store_payload(data)
    payload = json.dumps(validated, indent=2, ensure_ascii=False) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return path


def _find_task_index(tasks: list[dict[str, Any]], task_id: int) -> int:
    for idx, task in enumerate(tasks):
        if task["id"] == task_id:
            return idx
    raise TaskNotFoundError(f"Task {task_id} not found")


def _copy_task(task: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(task)
    copied["tags"] = list(task["tags"])
    return copied


def add_task(
    store_path: Path | str,
    title: str,
    *,
    priority: str = "medium",
    tags: Sequence[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a new task with a monotonic ID and persist it."""
    clean_title = validate_title(title)
    clean_priority = validate_priority(priority)
    clean_tags = validate_tags(tags)
    now_utc = _resolve_now(now)

    data = load_store(store_path)
    task_id = data["next_id"]
    data["next_id"] = task_id + 1

    ts = format_utc_iso(now_utc)
    task: dict[str, Any] = {
        "id": task_id,
        "title": clean_title,
        "status": "todo",
        "priority": clean_priority,
        "tags": clean_tags,
        "elapsed_seconds": 0,
        "active_timer_start": None,
        "created_at": ts,
        "updated_at": ts,
        "completed_at": None,
    }
    data["tasks"].append(task)
    save_store(store_path, data)
    return _copy_task(task)


def get_task(store_path: Path | str, task_id: int) -> dict[str, Any]:
    """Retrieve a single task by ID."""
    tid = _validate_int(task_id, "task_id", min_value=1)
    data = load_store(store_path)
    idx = _find_task_index(data["tasks"], tid)
    return _copy_task(data["tasks"][idx])


def list_tasks(
    store_path: Path | str,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List all tasks, optionally filtered by status."""
    if status is not None:
        if not isinstance(status, str) or status not in VALID_STATUSES:
            raise StoreValidationError(
                f"status filter must be one of {VALID_STATUSES}, got {status!r}"
            )
    data = load_store(store_path)
    tasks = data["tasks"]
    if status is not None:
        tasks = [task for task in tasks if task["status"] == status]
    return [_copy_task(task) for task in tasks]


def start_task(
    store_path: Path | str,
    task_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Start or resume the timer for a task."""
    tid = _validate_int(task_id, "task_id", min_value=1)
    now_utc = _resolve_now(now)
    data = load_store(store_path)
    idx = _find_task_index(data["tasks"], tid)
    task = data["tasks"][idx]

    if task["status"] == "done":
        raise InvalidStateTransitionError(f"Task {tid} is already done")
    if task["active_timer_start"] is not None:
        raise InvalidStateTransitionError(f"Task {tid} timer is already running")

    ts = format_utc_iso(now_utc)
    task["status"] = "in_progress"
    task["active_timer_start"] = ts
    task["updated_at"] = ts

    save_store(store_path, data)
    return _copy_task(task)


def stop_task(
    store_path: Path | str,
    task_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pause a running task timer and accumulate elapsed seconds."""
    tid = _validate_int(task_id, "task_id", min_value=1)
    now_utc = _resolve_now(now)
    data = load_store(store_path)
    idx = _find_task_index(data["tasks"], tid)
    task = data["tasks"][idx]

    if task["active_timer_start"] is None:
        raise InvalidStateTransitionError(f"Task {tid} timer is not running")

    start_dt = parse_utc_iso(task["active_timer_start"])
    delta_seconds = (now_utc - start_dt).total_seconds()
    if delta_seconds < 0:
        raise StoreValidationError(
            "Stop timestamp cannot be earlier than active_timer_start"
        )

    task["elapsed_seconds"] += int(delta_seconds)
    task["active_timer_start"] = None
    task["updated_at"] = format_utc_iso(now_utc)

    save_store(store_path, data)
    return _copy_task(task)


def done_task(
    store_path: Path | str,
    task_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark a task as done, settling any active running timer first."""
    tid = _validate_int(task_id, "task_id", min_value=1)
    now_utc = _resolve_now(now)
    data = load_store(store_path)
    idx = _find_task_index(data["tasks"], tid)
    task = data["tasks"][idx]

    if task["status"] == "done":
        raise InvalidStateTransitionError(f"Task {tid} is already done")

    if task["active_timer_start"] is not None:
        start_dt = parse_utc_iso(task["active_timer_start"])
        delta_seconds = (now_utc - start_dt).total_seconds()
        if delta_seconds < 0:
            raise StoreValidationError(
                "Completion timestamp cannot be earlier than active_timer_start"
            )
        task["elapsed_seconds"] += int(delta_seconds)
        task["active_timer_start"] = None

    ts = format_utc_iso(now_utc)
    task["status"] = "done"
    task["completed_at"] = ts
    task["updated_at"] = ts

    save_store(store_path, data)
    return _copy_task(task)


def delete_task(store_path: Path | str, task_id: int) -> dict[str, Any]:
    """Delete a task by ID without decrementing next_id."""
    tid = _validate_int(task_id, "task_id", min_value=1)
    data = load_store(store_path)
    idx = _find_task_index(data["tasks"], tid)
    removed = data["tasks"].pop(idx)
    save_store(store_path, data)
    return _copy_task(removed)
