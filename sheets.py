"""
Read a Google Sheet via its public CSV export endpoint.

The sheet MUST be set to "Anyone with the link can view" — we don't use any
auth. We only read; we never modify the user's sheet.

Public Sheets CSV URL:
  https://docs.google.com/spreadsheets/d/{ID}/export?format=csv&gid={gid}
"""

import csv
import io
import re
from dataclasses import dataclass

import httpx
from loguru import logger


_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
_GID_RE = re.compile(r"[#&?]gid=(\d+)")


@dataclass
class SheetRow:
    row: int  # 1-indexed within DATA rows (1 = first data row, ignoring header)
    sku: str
    price: float
    stock: int


@dataclass
class SheetData:
    sheet_id: str
    gid: str
    rows: list[SheetRow]
    header: list[str]


class SheetNotPublicError(Exception):
    """Sheet is not shared as 'Anyone with link can view'."""


class SheetParseError(Exception):
    """Sheet content has wrong shape (missing columns, etc.)."""


def parse_sheet_url(url: str) -> tuple[str, str]:
    """Extract (sheet_id, gid) from a Google Sheets URL."""
    url = url.strip()
    m = _SHEET_ID_RE.search(url)
    if not m:
        raise ValueError(
            "Not a Google Sheets URL. Expected something like "
            "https://docs.google.com/spreadsheets/d/<ID>/edit"
        )
    sheet_id = m.group(1)
    gid_m = _GID_RE.search(url)
    gid = gid_m.group(1) if gid_m else "0"
    return sheet_id, gid


async def fetch_sheet(url: str) -> SheetData:
    """Fetch a public Google Sheet and parse it into SheetRow objects."""
    sheet_id, gid = parse_sheet_url(url)
    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid={gid}"
    )
    logger.info(f"Fetching sheet {sheet_id} (gid={gid})")

    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        resp = await client.get(csv_url)

    if resp.status_code in (401, 403):
        raise SheetNotPublicError(
            "Sheet is not public. In Google Sheets: Share → "
            "General access → Anyone with the link → Viewer."
        )
    if resp.status_code == 404:
        raise SheetParseError(f"Sheet not found (404). Check URL.")
    resp.raise_for_status()

    text = resp.text
    rows = _parse_csv_rows(text)
    header = _read_header(text)
    logger.info(f"Parsed {len(rows)} data rows from sheet")
    return SheetData(sheet_id=sheet_id, gid=gid, rows=rows, header=header)


def _read_header(csv_text: str) -> list[str]:
    reader = csv.reader(io.StringIO(csv_text))
    try:
        return next(reader)
    except StopIteration:
        return []


def _parse_csv_rows(csv_text: str) -> list[SheetRow]:
    reader = csv.reader(io.StringIO(csv_text))
    all_rows = list(reader)
    if not all_rows:
        raise SheetParseError("Sheet is empty.")

    header = [h.strip().lower() for h in all_rows[0]]
    try:
        sku_i = header.index("sku")
        price_i = header.index("price")
        stock_i = header.index("stock")
    except ValueError:
        raise SheetParseError(
            f"Missing required column. Expected SKU/Price/Stock as header. "
            f"Got: {all_rows[0]}"
        )

    out: list[SheetRow] = []
    skipped = 0
    data_idx = 0
    for n, row in enumerate(all_rows[1:], start=1):
        if not row or len(row) <= sku_i or not row[sku_i].strip():
            skipped += 1
            continue
        try:
            price = float(row[price_i].replace(",", ".").strip())
            stock_raw = row[stock_i].replace(",", ".").strip()
            stock = int(float(stock_raw)) if stock_raw else 0
        except (ValueError, IndexError):
            logger.warning(f"Sheet row {n + 1}: bad numeric data, skipping. row={row}")
            skipped += 1
            continue
        data_idx += 1
        out.append(
            SheetRow(
                row=data_idx,
                sku=row[sku_i].strip(),
                price=price,
                stock=stock,
            )
        )

    if skipped:
        logger.info(f"Skipped {skipped} blank/invalid rows")
    return out
