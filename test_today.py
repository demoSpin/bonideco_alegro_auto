"""
Tonight's smoke test:
  1. Verify session by listing 5 SKUs (mix of found / not-found).
  2. Dump full raw JSON for one found SKU into logs/ for inspection.

DOES NOT create offers, edit anything, or hit Allegro write endpoints.
Pure read-only.

Run:
    python test_today.py
"""

import asyncio
import json
from pathlib import Path

from loguru import logger

from allegro_api import AllegroClient, SessionExpired, DatadomeBlocked
from config import LOGS_DIR, STORAGE_STATE_PATH


SKUS_TO_TEST = [
    "B34SYL30HBK",
    "SYL30HV1",
    "B34SYL30LBK",
    "STR141C01",
    "B34STR10GN",
]


async def main() -> None:
    if not STORAGE_STATE_PATH.exists():
        logger.error(f"{STORAGE_STATE_PATH} not found.")
        logger.error(
            "Either run `python login.py` (Playwright login) "
            "OR `python import_cookies.py <exported.json>` (manual Chrome export)."
        )
        return

    summary: list[tuple[str, str, int]] = []
    dump_path: Path | None = None

    try:
        async with AllegroClient() as client:
            for sku in SKUS_TO_TEST:
                try:
                    result = await client.search(sku)
                except SessionExpired as e:
                    logger.error(f"{e}")
                    logger.error("Cookies expired — re-export from Chrome and re-run import_cookies.py")
                    return
                except DatadomeBlocked as e:
                    logger.error(f"{e}")
                    logger.error("Datadome blocked the request. Possible causes:")
                    logger.error("  1. UA in .env doesn't match the Chrome that exported cookies")
                    logger.error("  2. Cookies are stale / IP is on Datadome's blocklist")
                    logger.error("  3. Missing critical cookies (especially 'datadome')")
                    return

                summary.append((sku, result.status, len(result.products)))

                if result.status in ("found_single", "found_multi") and dump_path is None:
                    dump_path = LOGS_DIR / f"search_response_{sku}.json"
                    with open(dump_path, "w", encoding="utf-8") as f:
                        json.dump(result.raw_response, f, indent=2, ensure_ascii=False)
                    logger.info(f"Raw response for {sku} dumped to {dump_path}")
    except FileNotFoundError as e:
        logger.error(str(e))
        return

    print("\n" + "=" * 60)
    print("SEARCH SMOKE TEST RESULTS")
    print("=" * 60)
    print(f"{'SKU':<20} {'Status':<15} {'Variants':<10}")
    print("-" * 60)
    for sku, status, n in summary:
        print(f"{sku:<20} {status:<15} {n:<10}")
    print("=" * 60)

    if dump_path:
        print(f"\nFull JSON for one found SKU saved to: {dump_path}")
        print("Open it to see exact response shape Allegro returns.")
    else:
        print("\nNo SKU returned a product — could not dump example response.")


if __name__ == "__main__":
    logger.add(
        LOGS_DIR / "test_{time:YYYYMMDD_HHmmss}.log",
        level="DEBUG",
    )
    asyncio.run(main())
