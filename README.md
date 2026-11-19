# devpulse

Hackathon sprint & time/task tracking CLI with JSON persistence and
Markdown / XSS-escaped HTML summary reporting, built entirely on the
**Python 3.11 standard library** (zero external packages).

## Features

- Task lifecycle: `add` → `start` → `stop` → `done`, plus `list` and `report`.
- Per-task stopwatch: accumulated time is tracked in seconds and shown as `HH:MM:SS`.
- JSON persistence with atomic writes and strict schema validation (`src/devpulse/store.py`).
- Sprint reports in Markdown or standalone HTML, with all dynamic text
  HTML-escaped to prevent XSS (`src/devpulse/reporter.py`).
- Clean exit codes for scripting: `0` success, `1` domain/validation error,
  `2` argparse usage error.

## Requirements

- Python 3.11+ (standard library only; nothing to `pip install`).

## Setup

The package lives under `src/`, so put `src` on `PYTHONPATH` and run the CLI as
a module.

**bash / zsh (Linux, macOS):**

```bash
PYTHONPATH=src python -m devpulse.cli --help
```

**PowerShell (Windows):**

```powershell
$env:PYTHONPATH = "src"
python -m devpulse.cli --help
```

You can also run the file directly thanks to its import guard:

```bash
python src/devpulse/cli.py --help
```

## Store location

Each command reads and writes a JSON store file. The path is resolved in this
order:

1. An explicit `--store PATH` (accepted **before or after** the subcommand).
2. The `DEVPULSE_STORE` environment variable.
3. The default `.devpulse.json` in the current directory.

```bash
# equivalent: --store before or after the subcommand
PYTHONPATH=src python -m devpulse.cli --store sprint.json add "Task"
PYTHONPATH=src python -m devpulse.cli add "Task" --store sprint.json

# via environment variable
DEVPULSE_STORE=sprint.json PYTHONPATH=src python -m devpulse.cli list
```

## CLI usage

All examples assume `PYTHONPATH=src` is set (see Setup). Output shown below is
the exact output the CLI produces.

### Add a task

```bash
PYTHONPATH=src python -m devpulse.cli --store sprint.json add "Write CLI docs" --priority high --tag docs --tag cli
```

```text
Added task #1: Write CLI docs [status=todo, priority=high]
```

- `--priority` is one of `low`, `medium`, `high` (default `medium`).
- `--tag` is repeatable; each use adds one tag.

### Start / stop / complete a task

```bash
PYTHONPATH=src python -m devpulse.cli --store sprint.json start 1
PYTHONPATH=src python -m devpulse.cli --store sprint.json stop 1
PYTHONPATH=src python -m devpulse.cli --store sprint.json done 1
```

```text
Started task #1: Write CLI docs [status=in_progress]
Stopped task #1: Write CLI docs [elapsed=00:00:00]
Completed task #1: Write CLI docs [status=done, elapsed=00:00:00]
```

`start` begins (or resumes) the timer, `stop` pauses it and banks the elapsed
time, and `done` settles any running timer and marks the task complete.

### List tasks

```bash
PYTHONPATH=src python -m devpulse.cli --store sprint.json list
PYTHONPATH=src python -m devpulse.cli --store sprint.json list --status done
```

```text
#1 [done] Write CLI docs (priority=high, elapsed=00:00:00, tags=docs,cli)
```

`--status` filters by `todo`, `in_progress`, or `done`. When nothing matches,
the CLI prints `No tasks found.`

### Reporting (Markdown & HTML)

Render a sprint report to stdout, or write it to a file with `--out`
(parent directories are created automatically; files are written as UTF-8
without a BOM).

```bash
# Markdown to stdout
PYTHONPATH=src python -m devpulse.cli --store sprint.json report --format md

# Standalone HTML to a file
PYTHONPATH=src python -m devpulse.cli --store sprint.json report --format html --out reports/sprint.html --title "Demo Sprint"
```

```text
Wrote html report to reports/sprint.html
```

The Markdown report begins:

```markdown
# DevPulse Sprint Report

## Summary

- **Total Tasks:** 1
- **Completion Rate:** 100.0%
```

The HTML report is a self-contained HTML5 document with inline CSS. All task
titles, tags, and other dynamic values are HTML-escaped, so a title like
`<script>alert("xss")</script>` is rendered as inert `&lt;script&gt;...` text
rather than executable markup.

## Exit codes

| Code | Meaning                                                              |
|------|---------------------------------------------------------------------|
| `0`  | Success.                                                            |
| `1`  | Domain / validation error (`DevPulseError`, `ValueError`, `OSError`); a `Error: <message>` line is printed to stderr with no raw traceback. |
| `2`  | Argparse usage error (unknown command, bad flag, invalid choice).  |

## Verification

Run the full test suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

The CLI end-to-end tests (`tests/test_cli.py`) run `python -m devpulse.cli` in
child processes with `PYTHONPATH=src`, so the command above works without any
extra setup.
