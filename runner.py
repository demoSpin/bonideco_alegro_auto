"""
Bulk run controller — processes a slice of SheetRow objects through the full
Allegro flow (search → create → patch → optional publish), updating an Excel
log row-by-row and reporting progress via a callback.

Stops automatically after MAX_CONSECUTIVE_ERRORS API errors in a row.
A "skipped" (SKU not in catalog) is NOT counted as an error.
"""

import asyncio
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Awaitable, Callable

from loguru import logger

from allegro_api import AllegroClient, DatadomeBlocked, SessionExpired
from config import (
    ALLOWED_BRANDS,
    LOGS_DIR,
    LONG_PAUSE_EVERY_N_ROWS,
    LONG_PAUSE_MAX,
    LONG_PAUSE_MIN,
    OUTPUT_DIR,
    ROW_DELAY_MAX,
    ROW_DELAY_MIN,
    RUNS_DIR,
)


BRAND_PARAM_NAMES = {"brand", "marka", "producent", "manufacturer"}


def _extract_brand(product: dict) -> str | None:
    for param in product.get("parameters", []):
        name = (param.get("name") or "").strip().lower()
        if name in BRAND_PARAM_NAMES:
            vals = param.get("valuesLabels") or []
            if vals:
                return vals[0].strip()
    return None
from excel_writer import RunExcelWriter
from sheets import SheetRow


MAX_CONSECUTIVE_ERRORS = 10
PROGRESS_EVERY_N_ROWS = 25


ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class RunMeta:
    id: str
    started_at: str
    sheet_url: str
    from_row: int
    to_row: int
    mode: str
    excel_path: str
    log_path: str
    status: str = "running"  # running | completed | stopped_errors | cancelled | crashed
    finished_at: str | None = None
    processed: int = 0
    success: int = 0
    skipped: int = 0
    multi_variant: int = 0
    errors: int = 0
    last_good_row: int | None = None
    last_error: str | None = None


def make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_run_meta(meta: RunMeta) -> None:
    path = RUNS_DIR / f"{meta.id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(meta), f, indent=2, default=str, ensure_ascii=False)


def load_run_meta(run_id: str) -> RunMeta | None:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return RunMeta(**data)


def list_runs(limit: int = 20) -> list[RunMeta]:
    files = sorted(RUNS_DIR.glob("*.json"), reverse=True)[:limit]
    runs = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                runs.append(RunMeta(**json.load(fp)))
        except Exception as e:
            logger.warning(f"Could not load run meta {f}: {e}")
    return runs


class RunController:
    def __init__(
        self,
        sheet_rows: list[SheetRow],
        from_row: int,
        to_row: int,
        mode: str = "draft",
        sheet_url: str = "",
        progress_cb: ProgressCallback | None = None,
    ) -> None:
        assert mode in ("draft", "live"), f"bad mode {mode!r}"
        self.from_row = from_row
        self.to_row = to_row
        self.mode = mode
        self.sheet_url = sheet_url
        self.progress_cb = progress_cb

        self.rows = [r for r in sheet_rows if from_row <= r.row <= to_row]

        self.run_id = make_run_id()
        self.excel_path = OUTPUT_DIR / f"run_{self.run_id}.xlsx"
        self.log_path = LOGS_DIR / f"run_{self.run_id}.log"

        self.excel = RunExcelWriter(self.excel_path, self.rows)
        self._log_handle_id = logger.add(self.log_path, level="DEBUG")

        self.consecutive_errors = 0
        self._cancel_requested = False

        self.meta = RunMeta(
            id=self.run_id,
            started_at=datetime.now().isoformat(timespec="seconds"),
            sheet_url=sheet_url,
            from_row=from_row,
            to_row=to_row,
            mode=mode,
            excel_path=str(self.excel_path),
            log_path=str(self.log_path),
        )
        save_run_meta(self.meta)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    async def _notify(self, msg: str) -> None:
        if self.progress_cb:
            try:
                await self.progress_cb(msg)
            except Exception as e:
                logger.warning(f"progress_cb failed: {e}")

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep but wake up promptly if cancellation is requested."""
        end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < end:
            if self._cancel_requested:
                return
            remaining = end - asyncio.get_event_loop().time()
            await asyncio.sleep(min(0.5, max(0.0, remaining)))

    async def run(self) -> RunMeta:
        total = len(self.rows)
        logger.info(
            f"Run {self.run_id}: rows {self.from_row}..{self.to_row} "
            f"({total} to process), mode={self.mode}"
        )
        avg_delay = (ROW_DELAY_MIN + ROW_DELAY_MAX) / 2
        eta_seconds = int(total * (3 + avg_delay))
        eta_h, eta_m = divmod(eta_seconds // 60, 60)
        eta_str = f"{eta_h}h {eta_m}m" if eta_h else f"{eta_m}m"

        await self._notify(
            f"▶ Run {self.run_id} starting\n"
            f"  Rows: {self.from_row}..{self.to_row} ({total} items)\n"
            f"  Mode: {self.mode.upper()}\n"
            f"  Throttle: {ROW_DELAY_MIN:.1f}-{ROW_DELAY_MAX:.1f}s between rows"
            f"{f', plus {LONG_PAUSE_MIN:.0f}-{LONG_PAUSE_MAX:.0f}s every {LONG_PAUSE_EVERY_N_ROWS} rows' if LONG_PAUSE_EVERY_N_ROWS else ''}\n"
            f"  ETA: ~{eta_str}"
        )

        try:
            async with AllegroClient() as client:
                for i, sr in enumerate(self.rows, 1):
                    if self._cancel_requested:
                        self.meta.status = "cancelled"
                        await self._notify(f"✗ Cancelled at row {sr.row}")
                        break

                    await self._process_one(client, sr)

                    if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        self.meta.status = "stopped_errors"
                        await self._notify(
                            f"⛔ STOPPED at row {sr.row}\n"
                            f"  {MAX_CONSECUTIVE_ERRORS} errors in a row\n"
                            f"  Last good row: {self.meta.last_good_row}\n"
                            f"  Last error: {self.meta.last_error}"
                        )
                        break

                    if i % PROGRESS_EVERY_N_ROWS == 0 or i == total:
                        pct = i * 100 // total if total else 0
                        await self._notify(
                            f"Progress: {i}/{total} ({pct}%)\n"
                            f"  ✓ {self.meta.success}  "
                            f"− {self.meta.skipped}  "
                            f"⚠ {self.meta.multi_variant}  "
                            f"⛔ {self.meta.errors}"
                        )

                    save_run_meta(self.meta)

                    if i < total:
                        if (
                            LONG_PAUSE_EVERY_N_ROWS > 0
                            and i % LONG_PAUSE_EVERY_N_ROWS == 0
                        ):
                            pause = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
                            logger.info(f"Long pause: {pause:.1f}s after {i} rows")
                            await self._notify(f"😴 Long pause {int(pause)}s (anti-throttling)")
                            await self._interruptible_sleep(pause)
                        else:
                            await self._interruptible_sleep(
                                random.uniform(ROW_DELAY_MIN, ROW_DELAY_MAX)
                            )
                else:
                    self.meta.status = "completed"
                    await self._notify(
                        f"✓ Done\n"
                        f"  ✓ Success:        {self.meta.success}\n"
                        f"  − Skipped:        {self.meta.skipped}\n"
                        f"  ⚠ Multi-variant:  {self.meta.multi_variant}\n"
                        f"  ⛔ Errors:        {self.meta.errors}"
                    )
        except Exception as e:
            logger.exception(f"Run crashed: {e}")
            self.meta.status = "crashed"
            self.meta.last_error = str(e)
            await self._notify(f"💥 Run crashed: {e}")
        finally:
            self.meta.finished_at = datetime.now().isoformat(timespec="seconds")
            save_run_meta(self.meta)
            try:
                logger.remove(self._log_handle_id)
            except Exception:
                pass

        return self.meta

    async def _process_one(self, client: AllegroClient, sr: SheetRow) -> None:
        self.meta.processed += 1
        sku = sr.sku

        try:
            search = await client.search(sku)
        except (DatadomeBlocked, SessionExpired) as e:
            self._record_error(sr, f"search auth: {e}")
            return
        except Exception as e:
            self._record_error(sr, f"search failed: {e}")
            return

        if search.status == "not_found":
            self.meta.skipped += 1
            self.consecutive_errors = 0
            self.meta.last_good_row = sr.row
            self.excel.update(sr.row, status="skipped", notes="not in catalog")
            return

        products_to_process = list(search.products)
        brand_filtered_out: list[str] = []
        if ALLOWED_BRANDS:
            allowed: list[dict] = []
            for p in search.products:
                brand = _extract_brand(p)
                if brand and brand.lower() in ALLOWED_BRANDS:
                    allowed.append(p)
                else:
                    name = (p.get("name") or "?")[:40]
                    brand_filtered_out.append(f"{name} (brand={brand or 'unknown'})")

            if not allowed:
                self.meta.skipped += 1
                self.consecutive_errors = 0
                self.meta.last_good_row = sr.row
                details = "; ".join(brand_filtered_out[:5])
                self.excel.update(
                    sr.row,
                    status="skipped",
                    notes=f"brand-filtered all {len(search.products)} variants: {details}"[:500],
                )
                return
            products_to_process = allowed

        n_variants = len(products_to_process)
        is_multi = n_variants > 1

        offer_ids: list[str] = []
        per_variant_errors: list[str] = []

        delta = {
            "external": {"id": sku},
            "sellingMode": {
                "format": "BUY_NOW",
                "price": {"amount": f"{sr.price:.2f}", "currency": "PLN"},
            },
            "stock": {"available": str(sr.stock), "unit": "UNIT"},
        }

        for idx, product in enumerate(products_to_process, start=1):
            product_id = product["id"]
            product_name = (product.get("name") or "")[:60]
            try:
                create = await client.create_offer(product_id)
                offer_id = create.offer_id
                await client.patch_offer(offer_id, delta)
                if self.mode == "live":
                    await client.publish_offer(offer_id)
                offer_ids.append(offer_id)
                logger.info(
                    f"row {sr.row} variant {idx}/{n_variants}: "
                    f"offer {offer_id} ({product_name})"
                )
            except (DatadomeBlocked, SessionExpired) as e:
                per_variant_errors.append(f"v{idx}: auth: {e}")
                logger.error(f"row {sr.row} variant {idx}: auth error — bailing variants loop")
                break
            except Exception as e:
                per_variant_errors.append(f"v{idx} ({product_name}): {e}")
                logger.exception(f"row {sr.row} variant {idx}: failed")

        if offer_ids:
            self.consecutive_errors = 0
            self.meta.last_good_row = sr.row
            offer_id_str = ", ".join(offer_ids)
            if is_multi or brand_filtered_out:
                self.meta.multi_variant += 1
                note_parts = [f"{len(offer_ids)}/{n_variants} variants OK"]
                if brand_filtered_out:
                    note_parts.append(
                        f"brand-filtered: {'; '.join(brand_filtered_out[:3])}"
                    )
                if per_variant_errors:
                    note_parts.append("; ".join(per_variant_errors))
                self.excel.update(
                    sr.row,
                    status="multi_variant",
                    offer_id=offer_id_str,
                    notes=" | ".join(note_parts)[:500],
                )
            else:
                self.meta.success += 1
                self.excel.update(
                    sr.row,
                    status="success",
                    offer_id=offer_id_str,
                    notes=(products_to_process[0].get("name") or "")[:80],
                )
        else:
            err_msg = "; ".join(per_variant_errors)[:300] or "all variants failed"
            self._record_error(sr, err_msg)

    def _record_error(self, sr: SheetRow, msg: str) -> None:
        self.meta.errors += 1
        self.consecutive_errors += 1
        self.meta.last_error = msg[:300]
        self.excel.update(sr.row, status="error", notes=msg[:300])
        logger.error(f"row {sr.row} ({sr.sku}): {msg}")
