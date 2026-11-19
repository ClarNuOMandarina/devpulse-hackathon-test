"""Markdown and XSS-escaped HTML sprint report generator for DevPulse.

This module is intentionally decoupled from ``devpulse.store`` and consumes
generic task mappings matching the canonical DevPulse task schema.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html
from typing import Any, Mapping, Sequence

VALID_STATUSES: tuple[str, ...] = ("todo", "in_progress", "done")
VALID_PRIORITIES: tuple[str, ...] = ("low", "medium", "high")
DEFAULT_SPRINT_TITLE = "DevPulse Sprint Report"


def _resolve_now(now: datetime | None) -> datetime:
    """Return a timezone-aware UTC datetime for calculations."""
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime):
        raise ValueError("now must be a datetime instance or None")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now


def _parse_timestamp(value: Any, *, field_name: str) -> datetime:
    """Parse an ISO-8601 string or timezone-aware datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        raise ValueError(f"{field_name} must be an ISO-8601 string or datetime")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return dt


def _escape_markdown_cell(value: Any) -> str:
    """Escape pipes and collapse newlines so Markdown table cells stay intact."""
    text = str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def _escape_html(value: Any) -> str:
    """Escape dynamic text for safe HTML5 rendering."""
    return html.escape(str(value), quote=True)


def format_duration(seconds: int) -> str:
    """Format non-negative integer seconds as ``HH:MM:SS``.

    Raises:
        ValueError: If ``seconds`` is a ``bool``, not an ``int``, or negative.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError("seconds must be a non-negative integer, not bool")
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def compute_task_duration_seconds(
    task: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> int:
    """Compute total duration in seconds for a task, including any active timer.

    Raises:
        ValueError: If ``elapsed_seconds`` is invalid/negative, ``now`` is naive,
            ``active_timer_start`` is invalid/naive, or ``now < active_timer_start``.
    """
    resolved_now = _resolve_now(now)
    elapsed = task.get("elapsed_seconds", 0)
    if isinstance(elapsed, bool) or not isinstance(elapsed, int):
        raise ValueError("elapsed_seconds must be a non-negative integer, not bool")
    if elapsed < 0:
        raise ValueError("elapsed_seconds cannot be negative")

    active_start = task.get("active_timer_start")
    if active_start is None:
        return elapsed

    start_dt = _parse_timestamp(active_start, field_name="active_timer_start")
    delta_seconds = (resolved_now - start_dt).total_seconds()
    if delta_seconds < 0:
        raise ValueError("active_timer_start cannot be later than now")
    return elapsed + int(delta_seconds)


def compute_sprint_summary(
    tasks: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate task counts, completion percentage, and tracked durations."""
    resolved_now = _resolve_now(now)

    status_counts: dict[str, int] = {status: 0 for status in VALID_STATUSES}
    priority_counts: dict[str, int] = {priority: 0 for priority in VALID_PRIORITIES}
    total_duration_seconds = 0
    active_timers = 0

    for task in tasks:
        status = task.get("status")
        if status not in status_counts:
            raise ValueError(f"Unknown status: {status!r}")
        status_counts[status] += 1

        priority = task.get("priority", "medium")
        if priority is None:
            priority = "medium"
        if priority not in priority_counts:
            raise ValueError(f"Unknown priority: {priority!r}")
        priority_counts[priority] += 1

        total_duration_seconds += compute_task_duration_seconds(task, now=resolved_now)
        if task.get("active_timer_start") is not None:
            active_timers += 1

    total_tasks = len(tasks)
    completion_rate_pct = (
        0.0 if total_tasks == 0 else round((status_counts["done"] / total_tasks) * 100, 1)
    )

    return {
        "total_tasks": total_tasks,
        "status_counts": status_counts,
        "priority_counts": priority_counts,
        "completion_rate_pct": completion_rate_pct,
        "total_duration_seconds": total_duration_seconds,
        "total_duration_formatted": format_duration(total_duration_seconds),
        "active_timers": active_timers,
    }


def render_markdown_report(
    tasks: Sequence[Mapping[str, Any]],
    *,
    sprint_title: str = DEFAULT_SPRINT_TITLE,
    now: datetime | None = None,
) -> str:
    """Render a sprint summary and task table as Markdown."""
    resolved_now = _resolve_now(now)
    summary = compute_sprint_summary(tasks, now=resolved_now)
    generated_at = resolved_now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_title = sprint_title.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

    lines: list[str] = [
        f"# {safe_title}",
        "",
        f"Generated at: `{generated_at}`",
        "",
        "## Summary",
        "",
        f"- **Total Tasks:** {summary['total_tasks']}",
        f"- **Completion Rate:** {summary['completion_rate_pct']:.1f}%",
        (
            f"- **Total Tracked Time:** {summary['total_duration_formatted']} "
            f"({summary['total_duration_seconds']}s)"
        ),
        f"- **Active Timers:** {summary['active_timers']}",
        (
            "- **By Status:** "
            f"todo={summary['status_counts']['todo']}, "
            f"in_progress={summary['status_counts']['in_progress']}, "
            f"done={summary['status_counts']['done']}"
        ),
        (
            "- **By Priority:** "
            f"high={summary['priority_counts']['high']}, "
            f"medium={summary['priority_counts']['medium']}, "
            f"low={summary['priority_counts']['low']}"
        ),
        "",
        "## Tasks",
        "",
    ]

    if not tasks:
        lines.append("No tasks recorded in this sprint.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| ID | Title | Status | Priority | Duration | Active | Tags |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for task in tasks:
        task_id = _escape_markdown_cell(task.get("id", ""))
        title = _escape_markdown_cell(task.get("title", ""))
        status = _escape_markdown_cell(task.get("status", ""))
        priority = _escape_markdown_cell(task.get("priority", "medium") or "medium")
        duration = format_duration(compute_task_duration_seconds(task, now=resolved_now))
        active = "yes" if task.get("active_timer_start") is not None else "no"
        raw_tags = task.get("tags") or []
        tags = _escape_markdown_cell(", ".join(str(tag) for tag in raw_tags)) if raw_tags else "-"
        lines.append(
            f"| {task_id} | {title} | {status} | {priority} | {duration} | {active} | {tags} |"
        )

    return "\n".join(lines) + "\n"


def render_html_report(
    tasks: Sequence[Mapping[str, Any]],
    *,
    sprint_title: str = DEFAULT_SPRINT_TITLE,
    now: datetime | None = None,
) -> str:
    """Render a standalone, XSS-escaped HTML5 sprint report with inline CSS."""
    resolved_now = _resolve_now(now)
    summary = compute_sprint_summary(tasks, now=resolved_now)
    generated_at = resolved_now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    escaped_title = _escape_html(sprint_title)
    escaped_generated_at = _escape_html(generated_at)

    task_rows: list[str] = []
    for task in tasks:
        task_id = _escape_html(task.get("id", ""))
        title = _escape_html(task.get("title", ""))
        status = str(task.get("status", ""))
        escaped_status = _escape_html(status)
        priority = str(task.get("priority", "medium") or "medium")
        escaped_priority = _escape_html(priority)
        duration = _escape_html(
            format_duration(compute_task_duration_seconds(task, now=resolved_now))
        )
        active_label = "Running" if task.get("active_timer_start") is not None else "Idle"
        escaped_active = _escape_html(active_label)

        raw_tags = task.get("tags") or []
        if raw_tags:
            tags_html = " ".join(
                f'<span class="tag">{_escape_html(tag)}</span>' for tag in raw_tags
            )
        else:
            tags_html = '<span class="muted">-</span>'

        updated_at = task.get("updated_at")
        updated_html = _escape_html(updated_at) if updated_at is not None else "-"

        task_rows.append(
            "          <tr>\n"
            f"            <td><code>{task_id}</code></td>\n"
            f"            <td>{title}</td>\n"
            f'            <td><span class="badge status-{escaped_status}">{escaped_status}</span></td>\n'
            f'            <td><span class="badge priority-{escaped_priority}">{escaped_priority}</span></td>\n'
            f"            <td><code>{duration}</code></td>\n"
            f"            <td>{escaped_active}</td>\n"
            f"            <td>{tags_html}</td>\n"
            f"            <td><code>{updated_html}</code></td>\n"
            "          </tr>"
        )

    if task_rows:
        tasks_section = (
            "      <table>\n"
            "        <thead>\n"
            "          <tr>\n"
            "            <th>ID</th>\n"
            "            <th>Title</th>\n"
            "            <th>Status</th>\n"
            "            <th>Priority</th>\n"
            "            <th>Duration</th>\n"
            "            <th>Timer</th>\n"
            "            <th>Tags</th>\n"
            "            <th>Updated</th>\n"
            "          </tr>\n"
            "        </thead>\n"
            "        <tbody>\n"
            + "\n".join(task_rows)
            + "\n        </tbody>\n"
            "      </table>"
        )
    else:
        tasks_section = (
            '      <p class="empty-state">No tasks recorded in this sprint.</p>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --card: #ffffff;
      --text: #0f172a;
      --muted: #475569;
      --border: #e2e8f0;
      --accent: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 2rem 1rem;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    main {{
      max-width: 1040px;
      margin: 0 auto;
    }}
    header {{
      margin-bottom: 1.5rem;
    }}
    h1 {{
      margin: 0 0 0.25rem 0;
      font-size: 1.75rem;
    }}
    .meta {{
      margin: 0;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem;
    }}
    .card-label {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
    }}
    .card-value {{
      font-size: 1.4rem;
      font-weight: 700;
      margin-top: 0.25rem;
    }}
    .breakdown {{
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 1.5rem;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .panel {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem;
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
    }}
    th, td {{
      text-align: left;
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .badge, .tag {{
      display: inline-block;
      padding: 0.15rem 0.45rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 600;
      background: #e2e8f0;
      color: #1e293b;
      margin-right: 0.25rem;
      margin-bottom: 0.15rem;
    }}
    .status-done {{ background: #dcfce7; color: #166534; }}
    .status-in_progress {{ background: #dbeafe; color: #1d4ed8; }}
    .status-todo {{ background: #f1f5f9; color: #334155; }}
    .priority-high {{ background: #fee2e2; color: #991b1b; }}
    .priority-medium {{ background: #fef3c7; color: #92400e; }}
    .priority-low {{ background: #e0f2fe; color: #075985; }}
    .empty-state {{
      margin: 0.5rem 0;
      color: var(--muted);
      font-style: italic;
    }}
    .muted {{ color: var(--muted); }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{escaped_title}</h1>
      <p class="meta">Generated at: <code>{escaped_generated_at}</code></p>
    </header>
    <section class="grid" aria-label="Sprint Summary">
      <div class="card">
        <div class="card-label">Total Tasks</div>
        <div class="card-value">{_escape_html(summary["total_tasks"])}</div>
      </div>
      <div class="card">
        <div class="card-label">Completion Rate</div>
        <div class="card-value">{_escape_html(f"{summary['completion_rate_pct']:.1f}%")}</div>
      </div>
      <div class="card">
        <div class="card-label">Total Duration</div>
        <div class="card-value">{_escape_html(summary["total_duration_formatted"])}</div>
      </div>
      <div class="card">
        <div class="card-label">Active Timers</div>
        <div class="card-value">{_escape_html(summary["active_timers"])}</div>
      </div>
    </section>
    <div class="breakdown">
      <span>Status: todo={_escape_html(summary["status_counts"]["todo"])}, in_progress={_escape_html(summary["status_counts"]["in_progress"])}, done={_escape_html(summary["status_counts"]["done"])}</span>
      <span>Priority: high={_escape_html(summary["priority_counts"]["high"])}, medium={_escape_html(summary["priority_counts"]["medium"])}, low={_escape_html(summary["priority_counts"]["low"])}</span>
    </div>
    <section class="panel" aria-label="Tasks">
{tasks_section}
    </section>
  </main>
</body>
</html>
"""


def render_report(
    tasks: Sequence[Mapping[str, Any]],
    *,
    fmt: str = "md",
    sprint_title: str = DEFAULT_SPRINT_TITLE,
    now: datetime | None = None,
) -> str:
    """Render a sprint report in ``md`` or ``html`` format.

    Raises:
        ValueError: If ``fmt`` is not ``"md"``, ``"markdown"``, or ``"html"``.
    """
    if fmt in ("md", "markdown"):
        return render_markdown_report(tasks, sprint_title=sprint_title, now=now)
    if fmt == "html":
        return render_html_report(tasks, sprint_title=sprint_title, now=now)
    raise ValueError(f"Unknown report format: {fmt!r}")
