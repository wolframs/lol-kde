"""An append-only record of everything this tool has done to the machine.

`CHANGELOG.md` is written by hand and depends on somebody remembering. This
does not. Every mutation appends one JSON object; nothing is ever rewritten or
deleted, so a corrupted line costs one entry rather than the file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import snapshot


def path() -> Path:
    return snapshot.store() / "journal.jsonl"


def record(action: str, **fields) -> None:
    """Append one entry. Never raises -- a failed journal write must not
    abort the operation it was describing."""
    entry = {"at": datetime.now(timezone.utc).isoformat(), "action": action,
             "pid": os.getpid(), **fields}
    try:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        pass


def entries(limit: int = 0) -> list[dict]:
    target = path()
    if not target.is_file():
        return []
    found = []
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                found.append(json.loads(line))
            except ValueError:
                continue          # one bad line, not a bad file
    except OSError:
        return []
    return found[-limit:] if limit else found


def changelog_rows(report, before_id: str, after_id: str) -> list[str]:
    """Paste-ready CHANGELOG.md table rows for the settings that changed.

    The tool fills in the part it can verify. The turn number stays a human's
    job, which is the correct split.
    """
    rows = ["| what | file | old value | new value | revert |",
            "|---|---|---|---|---|"]
    for change in report.settings:
        old = change.before or "*(absent)*"
        new = change.after or "*(absent)*"
        rows.append(f"| {change.what} | `{change.where}` | `{old}` | `{new}` | "
                    f"`lol-kde restore {before_id}` |")
    if len(rows) == 2:
        rows.append("| *(no key-level changes)* | | | | |")
    rows.append("")
    rows.append(f"Snapshots: `{before_id}` → `{after_id}`")
    return rows
