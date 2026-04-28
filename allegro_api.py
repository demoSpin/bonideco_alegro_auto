"""
Allegro Sales Center API client.

Loads session cookies from storage_state.json (produced by login.py) and
calls the internal salescenter API directly via httpx.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from config import (
    EDGE_API,
    STORAGE_STATE_PATH,
    BROWSER_USER_AGENT,
    BROWSER_SEC_CH_UA,
    BROWSER_SEC_CH_UA_PLATFORM,
)


COMMON_HEADERS = {
    "accept": "application/vnd.allegro.form.v1+json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/vnd.allegro.form.v1+json",
    "origin": "https://salescenter.allegro.com",
    "referer": "https://salescenter.allegro.com/",
    "x-platform-channel": "BROWSER_FORM",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "sec-ch-ua": BROWSER_SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": BROWSER_SEC_CH_UA_PLATFORM,
    "user-agent": BROWSER_USER_AGENT,
}


class SessionExpired(Exception):
    """Raised when the API returns 401 — caller should re-login."""


class DatadomeBlocked(Exception):
    """Raised when Datadome returns a challenge page instead of JSON."""


@dataclass
class SearchResult:
    sku: str
    status: str  # "found_single" | "found_multi" | "not_found"
    products: list[dict[str, Any]]
    raw_response: dict[str, Any]

    @property
    def first_product_id(self) -> str | None:
        if self.products:
            return self.products[0].get("id")
        return None


@dataclass
class CreateOfferResult:
    offer_id: str
    raw_response: dict[str, Any]


@dataclass
class PublishResult:
    command_id: str
    raw_response: dict[str, Any]


def _load_cookies_from_storage_state() -> list[dict[str, Any]]:
    if not STORAGE_STATE_PATH.exists():
        raise FileNotFoundError(
            f"{STORAGE_STATE_PATH} not found. Run `python login.py` first."
        )
    with open(STORAGE_STATE_PATH, "r", encoding="utf-8") as f:
        state = json.load(f)
    return state.get("cookies", [])


def _build_cookie_jar(cookies: list[dict[str, Any]]) -> httpx.Cookies:
    jar = httpx.Cookies()
    for c in cookies:
        domain = c.get("domain", "")
        if domain.startswith("."):
            domain = domain[1:]
        jar.set(
            name=c["name"],
            value=c["value"],
            domain=domain,
            path=c.get("path", "/"),
        )
    return jar


class AllegroClient:
    def __init__(self) -> None:
        cookies = _load_cookies_from_storage_state()
        logger.debug(f"Loaded {len(cookies)} cookies from storage state")
        jar = _build_cookie_jar(cookies)

        self._client = httpx.AsyncClient(
            base_url=EDGE_API,
            headers=COMMON_HEADERS,
            cookies=jar,
            timeout=30.0,
            follow_redirects=False,
        )

    async def __aenter__(self) -> "AllegroClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def search(self, sku: str) -> SearchResult:
        """Search Allegro catalog by SKU/GTIN/EAN phrase."""
        params = {
            "phrase": sku,
            "category.narrow": "true",
            "page.size": 6,
        }
        logger.debug(f"GET /sale/products phrase={sku}")
        resp = await self._client.get("/sale/products", params=params)

        ct = resp.headers.get("content-type", "")
        if "text/html" in ct or "datadome" in resp.text.lower()[:2000]:
            raise DatadomeBlocked(
                f"Got HTML response (Datadome challenge page) for {sku}. "
                f"Status {resp.status_code}. Cookies / UA likely invalid."
            )

        if resp.status_code == 401:
            raise SessionExpired(f"Got 401 on search — session expired, re-import cookies")
        if resp.status_code == 403:
            raise DatadomeBlocked(
                f"Got 403 on search — Datadome blocked the request. "
                f"Body preview: {resp.text[:300]}"
            )
        resp.raise_for_status()

        data = resp.json()
        products = data.get("products", [])

        if not products:
            status = "not_found"
        elif len(products) == 1:
            status = "found_single"
        else:
            status = "found_multi"

        logger.info(f"[{sku}] -> {status} ({len(products)} variant(s))")
        return SearchResult(sku=sku, status=status, products=products, raw_response=data)

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        context: str = "",
        max_retries: int = 4,
    ) -> dict[str, Any]:
        """Issue a JSON request, auto-retrying on transient errors.

        Retried (with exponential backoff 0.5s -> 1s -> 2s -> 4s):
          - 409 OfferConflict (Allegro still processing previous edit)
          - 5xx server errors (500/502/503/504)
          - Network errors (ConnectError, ReadTimeout, RemoteProtocolError)

        NOT retried (fail-fast):
          - 401 (cookies expired) -> SessionExpired
          - 403 / Datadome challenge -> DatadomeBlocked
          - Other 4xx (client errors, retry won't help)
        """
        last_network_error: Exception | None = None
        for attempt in range(max_retries):
            logger.debug(
                f"{method} {url}  ({context})"
                + (f"  [retry {attempt}]" if attempt else "")
            )
            try:
                resp = await self._client.request(
                    method, url, json=json_body, headers=extra_headers
                )
            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.RemoteProtocolError,
            ) as e:
                last_network_error = e
                if attempt < max_retries - 1:
                    wait = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"{method} {url} → network error {type(e).__name__}: {e}; "
                        f"retry {attempt + 1}/{max_retries - 1} in {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

            ct = resp.headers.get("content-type", "")
            if "text/html" in ct or "datadome" in resp.text.lower()[:2000]:
                raise DatadomeBlocked(
                    f"Got HTML/Datadome response on {method} {url}. "
                    f"Status {resp.status_code}."
                )
            if resp.status_code == 401:
                raise SessionExpired(f"401 on {method} {url} — re-import cookies")
            if resp.status_code == 403:
                raise DatadomeBlocked(
                    f"403 on {method} {url}. Body preview: {resp.text[:300]}"
                )

            transient = resp.status_code == 409 or 500 <= resp.status_code <= 599
            if transient and attempt < max_retries - 1:
                wait = 0.5 * (2 ** attempt)
                logger.warning(
                    f"{method} {url} → {resp.status_code} ({context}); "
                    f"retry {attempt + 1}/{max_retries - 1} in {wait:.1f}s"
                )
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"{method} {url} → {resp.status_code}: {resp.text[:500]}",
                    request=resp.request,
                    response=resp,
                )
            return resp.json()
        # Should be unreachable — last iteration either returns or raises.
        if last_network_error:
            raise last_network_error
        raise RuntimeError("retry loop exhausted unexpectedly")

    async def create_offer(self, product_id: str) -> CreateOfferResult:
        """Create a draft offer (publication.status=INACTIVE) for a catalog product."""
        body = {
            "publication": {"status": "INACTIVE"},
            "language": "en-US",
            "fundraisingCampaign": None,
            "isFulfillment": False,
            "productSet": [{"product": {"id": product_id}}],
        }
        data = await self._request_json(
            "POST",
            "/sale/product-offers",
            json_body=body,
            context=f"create offer for product {product_id}",
        )
        offer_id = data.get("id")
        if not offer_id:
            raise RuntimeError(f"Create offer response has no 'id': {data}")
        logger.info(f"Draft offer created: {offer_id}")
        return CreateOfferResult(offer_id=offer_id, raw_response=data)

    async def get_offer(self, offer_id: str) -> dict[str, Any]:
        """Fetch full offer state (with Allegro-populated defaults)."""
        return await self._request_json(
            "GET",
            f"/sale/product-offers/{offer_id}",
            context=f"get offer {offer_id}",
        )

    async def patch_offer(
        self,
        offer_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Update offer fields. Body can be partial (delta) or full."""
        return await self._request_json(
            "PATCH",
            f"/sale/product-offers/{offer_id}",
            json_body=body,
            context=f"patch offer {offer_id} ({len(body)} top-level keys)",
        )

    async def publish_offer(self, offer_id: str) -> PublishResult:
        """Activate the offer — makes it LIVE on Allegro. No going back without unlist."""
        command_id = str(uuid.uuid4())
        body = {
            "offerCriteria": [
                {"offers": [{"id": offer_id}], "type": "CONTAINS_OFFERS"}
            ],
            "publication": {"action": "ACTIVATE", "scheduledFor": None},
        }
        # Publication endpoint uses a different media type than the form endpoints.
        publish_headers = {
            "accept": "application/vnd.allegro.public.v1+json",
            "content-type": "application/vnd.allegro.public.v1+json",
        }
        data = await self._request_json(
            "PUT",
            f"/sale/offer-publication-commands/{command_id}",
            json_body=body,
            extra_headers=publish_headers,
            context=f"PUBLISH offer {offer_id}",
        )
        logger.warning(f"Offer {offer_id} sent for ACTIVATE (cmd={command_id})")
        return PublishResult(command_id=command_id, raw_response=data)
