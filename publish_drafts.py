"""
Publish specific draft offer IDs (one-shot recovery tool).

Use it when /run finished but publish failed for some offers, or to manually
activate specific drafts you saw in the Allegro UI.

Usage:
    python publish_drafts.py <offer_id> [<offer_id> ...]

Example:
    python publish_drafts.py 18540926288 18540927004 18540927116
"""

import asyncio
import sys

from loguru import logger

from allegro_api import AllegroClient, DatadomeBlocked, SessionExpired
from config import LOGS_DIR, STORAGE_STATE_PATH


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python publish_drafts.py <offer_id> [<offer_id> ...]")
        return 1

    offer_ids = sys.argv[1:]

    if not STORAGE_STATE_PATH.exists():
        logger.error("No cookies. Run /upload_cookies via bot or import_cookies.py first.")
        return 1

    success: list[str] = []
    failed: list[tuple[str, str]] = []

    async with AllegroClient() as client:
        for oid in offer_ids:
            try:
                result = await client.publish_offer(oid)
                logger.success(f"✓ {oid} → published (cmd={result.command_id})")
                success.append(oid)
            except (SessionExpired, DatadomeBlocked) as e:
                logger.error(f"✗ {oid}: auth error — aborting (cookies dead?)")
                failed.append((oid, str(e)))
                break
            except Exception as e:
                logger.error(f"✗ {oid}: {e}")
                failed.append((oid, str(e)))

    print()
    print("=" * 50)
    print(f"  Published OK: {len(success)}")
    for oid in success:
        print(f"    ✓ {oid}")
    if failed:
        print(f"  Failed: {len(failed)}")
        for oid, err in failed:
            print(f"    ✗ {oid}: {err[:120]}")
    print("=" * 50)
    return 0 if not failed else 1


if __name__ == "__main__":
    logger.add(LOGS_DIR / "publish_drafts_{time:YYYYMMDD_HHmmss}.log", level="DEBUG")
    sys.exit(asyncio.run(main()))
