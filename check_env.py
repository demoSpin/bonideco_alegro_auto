"""
Sanity-check .env values, especially the browser fingerprint headers.
Catches broken quoting before you waste a Datadome attempt.

Run:
    python check_env.py
"""

import sys

from config import (
    ALLEGRO_EMAIL,
    ALLEGRO_PASSWORD,
    BROWSER_USER_AGENT,
    BROWSER_SEC_CH_UA,
    BROWSER_SEC_CH_UA_PLATFORM,
    DEFAULT_UA,
    DEFAULT_SEC_CH_UA,
    DEFAULT_SEC_CH_UA_PLATFORM,
)


def _show(label: str, value: str, *, hide: bool = False) -> None:
    shown = "***" if hide and value else value
    print(f"  {label:<30} = {shown!r}")


def main() -> int:
    print("=== .env loaded values ===")
    _show("ALLEGRO_EMAIL", ALLEGRO_EMAIL)
    _show("ALLEGRO_PASSWORD", ALLEGRO_PASSWORD, hide=True)
    _show("BROWSER_USER_AGENT", BROWSER_USER_AGENT)
    _show("BROWSER_SEC_CH_UA", BROWSER_SEC_CH_UA)
    _show("BROWSER_SEC_CH_UA_PLATFORM", BROWSER_SEC_CH_UA_PLATFORM)

    print("\n=== Validation ===")
    issues: list[str] = []

    if not ALLEGRO_EMAIL:
        issues.append("ALLEGRO_EMAIL is empty")
    if not ALLEGRO_PASSWORD:
        issues.append("ALLEGRO_PASSWORD is empty")

    if BROWSER_USER_AGENT == DEFAULT_UA:
        issues.append(
            "BROWSER_USER_AGENT is the DEFAULT — set it to your real Chrome UA "
            "(chrome://version)"
        )
    if BROWSER_SEC_CH_UA == DEFAULT_SEC_CH_UA:
        issues.append(
            "BROWSER_SEC_CH_UA is the DEFAULT — set it to your Chrome's sec-ch-ua"
        )
    if BROWSER_SEC_CH_UA_PLATFORM == DEFAULT_SEC_CH_UA_PLATFORM:
        if '"Windows"' not in BROWSER_SEC_CH_UA_PLATFORM:
            issues.append(
                "BROWSER_SEC_CH_UA_PLATFORM is default but malformed (no quotes)"
            )

    if BROWSER_SEC_CH_UA and '"' not in BROWSER_SEC_CH_UA:
        issues.append(
            "BROWSER_SEC_CH_UA does not contain inner double quotes — quoting is "
            "broken. Wrap value in single quotes: BROWSER_SEC_CH_UA='\"...\"'"
        )
    if BROWSER_SEC_CH_UA_PLATFORM and '"' not in BROWSER_SEC_CH_UA_PLATFORM:
        issues.append(
            "BROWSER_SEC_CH_UA_PLATFORM does not contain inner double quotes — "
            "should be like '\"Windows\"'. Wrap in single quotes."
        )

    if BROWSER_USER_AGENT and BROWSER_SEC_CH_UA:
        ua_chrome_ver = _extract_chrome_version(BROWSER_USER_AGENT)
        ch_chrome_ver = _extract_chrome_version_from_sec_ch_ua(BROWSER_SEC_CH_UA)
        if ua_chrome_ver and ch_chrome_ver and ua_chrome_ver != ch_chrome_ver:
            issues.append(
                f"VERSION MISMATCH: User-Agent says Chrome {ua_chrome_ver}, "
                f"sec-ch-ua says {ch_chrome_ver}. Datadome WILL flag this."
            )

    if issues:
        print("\nIssues:")
        for i in issues:
            print(f"  ✗ {i}")
        print("\nFix .env (see .env.example) and re-run.")
        return 1

    print("  ✓ All checks passed")
    return 0


def _extract_chrome_version(ua: str) -> str | None:
    import re
    m = re.search(r"Chrome/(\d+)", ua)
    return m.group(1) if m else None


def _extract_chrome_version_from_sec_ch_ua(value: str) -> str | None:
    import re
    m = re.search(r'(?:Google Chrome|Chromium)";v="(\d+)"', value)
    return m.group(1) if m else None


if __name__ == "__main__":
    sys.exit(main())
