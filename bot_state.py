"""
Persistent bot state (small JSON file).

Tracks: cookies metadata, last sheet info, list of completed runs.
Per-run progress lives in storage/runs/<id>.json (separate, written row-by-row).
"""

import json
from datetime import datetime
from typing import Any

from config import STATE_PATH


def _empty_state() -> dict[str, Any]:
    return {
        "cookies": None,
        "completed_runs": [],
    }


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return _empty_state()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return _empty_state()


def save_state(state: dict[str, Any]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str, ensure_ascii=False)


def update_cookies_meta(count: int, names: list[str]) -> None:
    state = load_state()
    state["cookies"] = {
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "count": count,
        "names": sorted(names),
        "datadome_present": "datadome" in names,
    }
    save_state(state)


def append_completed_run(run_meta: dict[str, Any]) -> None:
    state = load_state()
    runs = state.setdefault("completed_runs", [])
    runs.append(run_meta)
    if len(runs) > 50:
        state["completed_runs"] = runs[-50:]
    save_state(state)


def format_state_summary(state: dict[str, Any]) -> str:
    lines: list[str] = []

    cookies = state.get("cookies")
    if cookies:
        lines.append(
            f"🍪 Cookies: {cookies['count']} loaded "
            f"(imported {cookies['imported_at']})"
        )
        if not cookies.get("datadome_present"):
            lines.append("⚠️ datadome cookie missing — re-export may be needed")
    else:
        lines.append("🍪 Cookies: NOT LOADED — run /upload_cookies")

    runs = state.get("completed_runs", [])
    if runs:
        lines.append(f"📋 Past runs: {len(runs)} stored")
        last = runs[-1]
        lines.append(
            f"   last: #{last.get('id')} {last.get('status')} "
            f"({last.get('processed', 0)} rows)"
        )
    else:
        lines.append("📋 Past runs: none")

    return "\n".join(lines)
