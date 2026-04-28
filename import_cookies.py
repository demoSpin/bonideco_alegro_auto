"""
Import cookies exported from Cookie-Editor (or compatible) Chrome extension
into Playwright storage_state.json format.

Workflow:
  1. Open Chrome (normal browser) → log in to https://salescenter.allegro.com
  2. Install "Cookie-Editor" extension (or similar)
  3. Click extension icon → Export → JSON → save to storage/cookies_import.json
  4. Run: python import_cookies.py storage/cookies_import.json
  5. Now test_today.py will work with these cookies

Usage:
    python import_cookies.py <path_to_exported_cookies.json>
    python import_cookies.py                    # defaults to storage/cookies_import.json
"""

import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from config import STORAGE_STATE_PATH, STORAGE_DIR


SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified": "None",
    "lax": "Lax",
    "strict": "Strict",
}


def _normalize_same_site(raw: str | None) -> str:
    if not raw:
        return "None"
    return SAME_SITE_MAP.get(raw.lower(), "None")


def _convert_cookie(c: dict[str, Any]) -> dict[str, Any] | None:
    name = c.get("name")
    value = c.get("value")
    domain = c.get("domain")
    if not (name and value is not None and domain):
        return None

    out = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": c.get("path", "/"),
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", False)),
        "sameSite": _normalize_same_site(c.get("sameSite")),
    }

    if "expirationDate" in c and c["expirationDate"]:
        out["expires"] = float(c["expirationDate"])
    elif "expires" in c and c["expires"]:
        out["expires"] = float(c["expires"])
    else:
        out["expires"] = -1

    return out


def import_cookies(import_path: Path, output_path: Path) -> int:
    if not import_path.exists():
        logger.error(f"Import file not found: {import_path}")
        sys.exit(1)

    with open(import_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    if not isinstance(raw, list):
        logger.error(
            "Unexpected JSON shape — expected list of cookie objects "
            'or {"cookies": [...]}'
        )
        sys.exit(1)

    converted: list[dict[str, Any]] = []
    skipped = 0
    for c in raw:
        out = _convert_cookie(c)
        if out is None:
            skipped += 1
            continue
        converted.append(out)

    storage_state = {
        "cookies": converted,
        "origins": [],
    }

    output_path.parent.mkdir(exist_ok=True, parents=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(storage_state, f, indent=2, ensure_ascii=False)

    logger.success(
        f"Imported {len(converted)} cookies → {output_path} "
        f"(skipped {skipped} malformed)"
    )

    domains: dict[str, int] = {}
    for c in converted:
        d = c["domain"]
        domains[d] = domains.get(d, 0) + 1
    logger.info("Cookie distribution by domain:")
    for d, n in sorted(domains.items(), key=lambda x: -x[1]):
        logger.info(f"  {d}: {n}")

    critical = ["datadome", "wdctx", "JSESSIONID", "_csrf"]
    found_critical = [c["name"] for c in converted if c["name"] in critical]
    if found_critical:
        logger.success(f"Critical cookies present: {', '.join(found_critical)}")
    else:
        logger.warning(
            f"Missing some critical cookies. Looked for: {critical}. "
            "Did you export from a logged-in salescenter.allegro.com session?"
        )

    return len(converted)


if __name__ == "__main__":
    default = STORAGE_DIR / "cookies_import.json"
    if len(sys.argv) > 1:
        import_path = Path(sys.argv[1])
    else:
        import_path = default
        logger.info(f"No path given, using default: {import_path}")

    import_cookies(import_path, STORAGE_STATE_PATH)
