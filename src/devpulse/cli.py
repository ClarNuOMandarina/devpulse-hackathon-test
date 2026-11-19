"""Command-line interface for DevPulse built on the Python 3.11 stdlib.

Supports task/timer tracking (``add``/``start``/``stop``/``done``/``list``) and
Markdown/HTML sprint reporting (``report``) on top of :mod:`devpulse.store` and
:mod:`devpulse.reporter`.

Runnable both as a module (``python -m devpulse.cli ...``) and as a direct
script (``python src/devpulse/cli.py ...``) thanks to the import guard below.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

# Import guard: allow direct script execution (``python src/devpulse/cli.py``)
# in addition to module execution (``python -m devpulse.cli``). When run as a
# script, ``__package__`` is None/"" and ``devpulse`` is not importable until
# the ``src`` directory (this file's parent's parent) is on ``sys.path``.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from devpulse import reporter, store
else:
    from . import reporter, store

DEFAULT_STORE_PATH = Path(".devpulse.json")


def _format_elapsed(task: dict) -> str:
    """Format a task's accumulated elapsed seconds as ``HH:MM:SS``."""
    return reporter.format_duration(int(task["elapsed_seconds"]))


def _resolve_store_path(args: argparse.Namespace) -> Path:
    """Resolve the store path from CLI args or the ``DEVPULSE_STORE`` env var.

    Precedence: an explicit ``--store`` (before or after the subcommand) wins;
    then ``DEVPULSE_STORE``; otherwise :data:`DEFAULT_STORE_PATH`.
    """
    store_value = getattr(args, "store", None)
    if store_value is not None:
        return Path(store_value)
    env_value = os.environ.get("DEVPULSE_STORE")
    if env_value:
        return Path(env_value)
    return DEFAULT_STORE_PATH


def _build_store_parent() -> argparse.ArgumentParser:
    """Build the shared ``--store`` parent parser for subcommands.

    ``add_help=False`` keeps ``-h`` owned by the top-level parser, and
    ``default=argparse.SUPPRESS`` ensures a subcommand-level ``--store`` only
    appears in the namespace when actually provided, so it does not clobber a
    top-level ``--store`` placed before the subcommand.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--store",
        default=argparse.SUPPRESS,
        metavar="PATH",
        help="Path to the JSON store file (overrides DEVPULSE_STORE).",
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with all six subcommands."""
    parser = argparse.ArgumentParser(
        prog="devpulse",
        description="DevPulse: hackathon task & time tracking CLI.",
    )
    parser.add_argument(
        "--store",
        default=None,
        metavar="PATH",
        help=(
            "Path to the JSON store file. May be given before or after the "
            "subcommand. Falls back to the DEVPULSE_STORE env var, then "
            f"{DEFAULT_STORE_PATH}."
        ),
    )

    store_parent = _build_store_parent()
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    add_p = subparsers.add_parser(
        "add", parents=[store_parent], help="Add a new task."
    )
    add_p.add_argument("title", help="Task title.")
    add_p.add_argument(
        "--priority",
        choices=list(store.VALID_PRIORITIES),
        default="medium",
        help="Task priority (default: medium).",
    )
    add_p.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        metavar="TAG",
        help="Add a tag (repeatable).",
    )
    add_p.set_defaults(func=_cmd_add)

    start_p = subparsers.add_parser(
        "start", parents=[store_parent], help="Start/resume a task timer."
    )
    start_p.add_argument("task_id", type=int, help="Task ID.")
    start_p.set_defaults(func=_cmd_start)

    stop_p = subparsers.add_parser(
        "stop", parents=[store_parent], help="Stop a running task timer."
    )
    stop_p.add_argument("task_id", type=int, help="Task ID.")
    stop_p.set_defaults(func=_cmd_stop)

    done_p = subparsers.add_parser(
        "done", parents=[store_parent], help="Mark a task as done."
    )
    done_p.add_argument("task_id", type=int, help="Task ID.")
    done_p.set_defaults(func=_cmd_done)

    list_p = subparsers.add_parser(
        "list", parents=[store_parent], help="List tasks."
    )
    list_p.add_argument(
        "--status",
        choices=list(store.VALID_STATUSES),
        default=None,
        help="Filter tasks by status.",
    )
    list_p.set_defaults(func=_cmd_list)

    report_p = subparsers.add_parser(
        "report", parents=[store_parent], help="Render a sprint report."
    )
    report_p.add_argument(
        "--format",
        dest="format",
        choices=["md", "html"],
        default="md",
        help="Report format (default: md).",
    )
    report_p.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write the report to PATH instead of stdout.",
    )
    report_p.add_argument(
        "--title",
        default=reporter.DEFAULT_SPRINT_TITLE,
        help="Report title.",
    )
    report_p.set_defaults(func=_cmd_report)

    return parser


def _cmd_add(args: argparse.Namespace) -> int:
    path = _resolve_store_path(args)
    task = store.add_task(path, args.title, priority=args.priority, tags=args.tags)
    print(
        f"Added task #{task['id']}: {task['title']} "
        f"[status={task['status']}, priority={task['priority']}]"
    )
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    path = _resolve_store_path(args)
    task = store.start_task(path, args.task_id)
    print(f"Started task #{task['id']}: {task['title']} [status={task['status']}]")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    path = _resolve_store_path(args)
    task = store.stop_task(path, args.task_id)
    print(
        f"Stopped task #{task['id']}: {task['title']} "
        f"[elapsed={_format_elapsed(task)}]"
    )
    return 0


def _cmd_done(args: argparse.Namespace) -> int:
    path = _resolve_store_path(args)
    task = store.done_task(path, args.task_id)
    print(
        f"Completed task #{task['id']}: {task['title']} "
        f"[status={task['status']}, elapsed={_format_elapsed(task)}]"
    )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    path = _resolve_store_path(args)
    tasks = store.list_tasks(path, status=args.status)
    if not tasks:
        print("No tasks found.")
        return 0
    for task in tasks:
        tags = ",".join(task["tags"]) if task["tags"] else "-"
        print(
            f"#{task['id']} [{task['status']}] {task['title']} "
            f"(priority={task['priority']}, elapsed={_format_elapsed(task)}, tags={tags})"
        )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    path = _resolve_store_path(args)
    tasks = store.list_tasks(path)
    content = reporter.render_report(tasks, fmt=args.format, sprint_title=args.title)
    if args.out is None:
        print(content, end="" if content.endswith("\n") else "\n")
        return 0
    out_path = Path(args.out)
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {args.format} report to {out_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code (0 success, 1 domain error, 2 usage)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (store.DevPulseError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
