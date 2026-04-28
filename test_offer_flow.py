"""
End-to-end offer flow test (DRAFT MODE by default, NO publish unless --publish).

What it does:
  1. Goes through a built-in list of (SKU, price, stock) from your sheet sample.
  2. Searches each SKU until it finds one in the Allegro catalog.
  3. Creates a DRAFT offer (publication.status=INACTIVE — not visible to buyers).
  4. GETs full state, dumps to logs/ for inspection.
  5. Sends a delta PATCH with just the price + stock from your sheet.
  6. Stops. The draft sits in your Sales Center → drafts list, ready to inspect.

You can also override what to use:
    python test_offer_flow.py                       # auto-discover, draft only
    python test_offer_flow.py SKU PRICE STOCK       # explicit values
    python test_offer_flow.py --publish             # auto-discover + LIVE
    python test_offer_flow.py SKU P S --publish     # explicit + LIVE
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

from allegro_api import AllegroClient, DatadomeBlocked, SessionExpired
from config import LOGS_DIR, STORAGE_STATE_PATH


CANDIDATES: list[tuple[str, float, int]] = [
    ("B34STR10GN", 1466.84, 51),
    ("B34STR061P01", 752.87, 21),
    ("STR101P01", 1069.73, 11),
    ("B34STR12GNV1", 1250.80, 22),
    ("STR124V1", 1058.46, 9),
    ("B34STR10FT", 860.65, 49),
    ("STR061Q01", 707.65, 97),
    ("B34STR061C01", 698.60, 138),
    ("B34STR061P01V1", 752.87, 35),
    ("STR101BZ01", 1047.15, 70),
]


def _interactive_pick_start(candidates: list[tuple[str, float, int]]) -> int:
    """Show candidates list and ask which row to start from. Returns 0-based index."""
    print("\nCandidate SKUs:")
    print(f"  {'#':>2}  {'SKU':<18} {'Price (PLN)':>11}   {'Stock':>6}")
    print("  " + "-" * 46)
    for i, (sku, price, stock) in enumerate(candidates, 1):
        print(f"  {i:>2}  {sku:<18} {price:>11.2f}   {stock:>6}")
    print()
    raw = input(
        f"Start from row [1-{len(candidates)}, Enter for 1, "
        f"q to quit]: "
    ).strip().lower()
    if raw in ("q", "quit", "exit"):
        print("Aborted.")
        sys.exit(0)
    if not raw:
        return 0
    try:
        n = int(raw)
        if not 1 <= n <= len(candidates):
            raise ValueError
        return n - 1
    except ValueError:
        print(f"Invalid input {raw!r}, expected 1..{len(candidates)}. Aborting.")
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("sku", nargs="?", help="(optional) explicit SKU to use")
    p.add_argument("price", nargs="?", type=float, help="(optional) price PLN")
    p.add_argument("stock", nargs="?", type=int, help="(optional) stock units")
    p.add_argument(
        "--publish",
        action="store_true",
        help="DANGER: also activate the offer (live to buyers).",
    )
    return p.parse_args()


def _save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {path}")


async def _find_first_in_catalog(
    client: AllegroClient,
    candidates: list[tuple[str, float, int]],
) -> tuple[str, float, int, dict] | None:
    for sku, price, stock in candidates:
        result = await client.search(sku)
        if result.status == "found_single":
            return sku, price, stock, result.products[0]
        if result.status == "found_multi":
            logger.warning(f"{sku}: {len(result.products)} variants — skipping for safety")
            continue
    return None


async def main() -> int:
    args = parse_args()

    if not STORAGE_STATE_PATH.exists():
        logger.error(f"{STORAGE_STATE_PATH} not found. Run import_cookies.py first.")
        return 1

    try:
        async with AllegroClient() as client:
            if args.sku:
                logger.info(f"Using explicit SKU={args.sku}")
                result = await client.search(args.sku)
                if result.status == "not_found":
                    logger.error(f"SKU {args.sku} not in catalog. Aborting.")
                    return 1
                if result.status == "found_multi":
                    logger.warning(
                        f"{len(result.products)} variants for {args.sku}, picking first"
                    )
                product = result.products[0]
                sku = args.sku
                price = args.price if args.price is not None else 0.0
                stock = args.stock if args.stock is not None else 0
                if args.price is None or args.stock is None:
                    logger.error("Explicit SKU given but price/stock missing.")
                    return 1
            else:
                start_idx = _interactive_pick_start(CANDIDATES)
                sublist = CANDIDATES[start_idx:]
                logger.info(
                    f"Searching {len(sublist)} candidate SKUs starting at row {start_idx + 1}..."
                )
                found = await _find_first_in_catalog(client, sublist)
                if not found:
                    logger.error(
                        "None of the remaining candidates were found in the catalog. "
                        "Edit CANDIDATES in this script with SKUs from your sheet."
                    )
                    return 1
                sku, price, stock, product = found
                logger.success(f"First found in catalog: {sku}")

            product_id = product["id"]
            price_str = f"{price:.2f}"
            stock_str = str(stock)

            print()
            print("=" * 60)
            print(f"Will operate on:")
            print(f"  SKU:        {sku}")
            print(f"  Product:    {product.get('name')}")
            print(f"  Product ID: {product_id}")
            print(f"  Price:      {price_str} PLN")
            print(f"  Stock:      {stock_str}")
            print(f"  Mode:       {'PUBLISH (LIVE)' if args.publish else 'DRAFT only'}")
            print("=" * 60)
            print()

            logger.info("Stage 1: POST create draft (INACTIVE)")
            create = await client.create_offer(product_id)
            offer_id = create.offer_id
            _save_json(LOGS_DIR / f"offer_create_{offer_id}.json", create.raw_response)
            logger.success(f"Offer created: {offer_id}")

            logger.info("Stage 2: GET full state (for inspection)")
            state = await client.get_offer(offer_id)
            _save_json(LOGS_DIR / f"offer_initial_{offer_id}.json", state)
            logger.info(f"GET state: {len(state)} top-level keys")

            logger.info("Stage 3: PATCH delta (external.id + price + stock)")
            delta = {
                "external": {"id": sku},
                "sellingMode": {
                    "format": "BUY_NOW",
                    "price": {"amount": price_str, "currency": "PLN"},
                },
                "stock": {"available": stock_str, "unit": "UNIT"},
            }
            _save_json(LOGS_DIR / f"offer_patch_body_{offer_id}.json", delta)
            patched = await client.patch_offer(offer_id, delta)
            _save_json(LOGS_DIR / f"offer_after_patch_{offer_id}.json", patched)
            logger.success("PATCH 200 OK")

            print()
            print("=" * 60)
            print("DRAFT OFFER READY")
            print("=" * 60)
            print(f"  Offer ID:   {offer_id}")
            print(f"  SKU:        {sku}")
            print(f"  Status:     INACTIVE (draft)")
            print()
            print("  Verify here:")
            print("  https://salescenter.allegro.com/my-sales")
            print("  → Filter for 'Inactive' or 'Drafts'")
            print("=" * 60)

            if not args.publish:
                print()
                print("  Not published. To activate this draft, either:")
                print("    a) publish via Allegro UI, or")
                print(f"    b) re-run with: python test_offer_flow.py {sku} {price_str} {stock_str} --publish")
                return 0

            logger.warning("Stage 4: PUBLISH (going LIVE)")
            pub = await client.publish_offer(offer_id)
            _save_json(LOGS_DIR / f"offer_publish_{offer_id}.json", pub.raw_response)
            print()
            print("✓ OFFER ACTIVATED — LIVE on Allegro")
            print(f"  cmd_id: {pub.command_id}")

    except (SessionExpired, DatadomeBlocked) as e:
        logger.error(f"Auth/Datadome problem: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    logger.add(
        LOGS_DIR / "offer_flow_{time:YYYYMMDD_HHmmss}.log",
        level="DEBUG",
    )
    sys.exit(asyncio.run(main()))
