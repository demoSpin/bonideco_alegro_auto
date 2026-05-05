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
        "allowed_brands": None,  # None = use .env fallback; list (incl. []) = state wins
        "completed_runs": [],
    }


def get_active_brands_display() -> list[str]:
    """Brand whitelist as the user typed it (original casing). Empty list = no filter."""
    state = load_state()
    sb = state.get("allowed_brands")
    if sb is not None:
        return [b.strip() for b in sb if b and b.strip()]
    # Fallback: read raw env var so we keep original casing for display.
    import os
    raw = os.getenv("ALLOWED_BRANDS", "")
    return [b.strip() for b in raw.split(",") if b.strip()]


def get_active_brands_set() -> set[str]:
    """Lowercased set used by the runner for case-insensitive matching."""
    return {b.lower() for b in get_active_brands_display()}


def set_allowed_brands(brands: list[str]) -> None:
    """Replace the active brand whitelist. Pass [] to clear (no filter)."""
    state = load_state()
    state["allowed_brands"] = [b.strip() for b in brands if b and b.strip()]
    save_state(state)


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

    brands = get_active_brands_display()
    if brands:
        lines.append(f"🏷 Brand filter: {', '.join(brands)}")
    else:
        lines.append("🏷 Brand filter: none (all brands processed)")

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
