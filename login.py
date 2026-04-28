"""
Playwright login flow for Allegro Sales Center.

Uses launch_persistent_context with a saved Chrome profile directory so cookies,
history, and Datadome trust signals accumulate between runs. Combined with
playwright-stealth and humanlike timing this should mostly bypass Datadome
after the FIRST manual challenge solve.

First run (HEADLESS=false): you may see a Datadome captcha — solve it manually.
Subsequent runs: profile remembers everything, should fly through.

Run:
    python login.py
"""

import asyncio
import random
import sys
import time
from typing import Literal

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)
from playwright_stealth import Stealth
from loguru import logger

from config import (
    ALLEGRO_EMAIL,
    ALLEGRO_PASSWORD,
    BROWSER_USER_AGENT,
    HEADLESS,
    SALES_CENTER_URL,
    STORAGE_DIR,
    STORAGE_STATE_PATH,
)


State = Literal["dashboard", "login", "challenge", "unknown"]

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
]

PROFILE_DIR = STORAGE_DIR / "chrome_profile"


async def _human_pause(min_s: float, max_s: float) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _accept_cookies_if_present(page: Page) -> None:
    selectors = [
        'button[data-role="accept-consent"]',
        'button:has-text("OK, I agree")',
        'button:has-text("Accept all")',
        'button:has-text("Sutinku")',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1000):
                await _human_pause(0.5, 1.2)
                await btn.click()
                logger.info(f"Cookie consent dismissed via {sel}")
                return
        except Exception:
            continue


async def _is_visible(page: Page, selector: str, timeout_ms: float = 500) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=timeout_ms)
    except Exception:
        return False


async def _detect_state(page: Page) -> State:
    challenge_selectors = [
        'iframe[src*="captcha-delivery"]',
        'iframe[src*="datadome"]',
        'iframe[title*="captcha" i]',
        'iframe[title*="DataDome" i]',
        '#datadome-captcha',
        '[id*="datadome"]',
    ]
    for sel in challenge_selectors:
        if await _is_visible(page, sel):
            return "challenge"

    if await _is_visible(page, 'input[type="password"]'):
        return "login"

    dashboard_selectors = [
        'a[href*="/my-sales"]',
        'a[href*="/offer"]',
        'nav a:has-text("My Sales")',
        'aside a:has-text("Assortment")',
    ]
    if "salescenter.allegro.com" in page.url and "/login" not in page.url:
        for sel in dashboard_selectors:
            if await _is_visible(page, sel):
                return "dashboard"

    return "unknown"


async def _fill_login_form_slowly(page: Page, email: str, password: str) -> None:
    """Type credentials char-by-char with random pauses, like a human."""
    logger.info("Filling login form (humanlike pacing)")

    email_loc = page.locator('input[name="login"], input[type="email"], #login').first
    pwd_loc = page.locator(
        'input[name="password"], input[type="password"], #password'
    ).first

    await _human_pause(1.0, 2.5)
    await email_loc.click()
    await _human_pause(0.3, 0.8)
    await email_loc.type(email, delay=random.randint(60, 130))
    logger.debug("Email typed")

    await _human_pause(0.8, 1.8)
    await pwd_loc.click()
    await _human_pause(0.3, 0.7)
    await pwd_loc.type(password, delay=random.randint(70, 140))
    logger.debug("Password typed")

    await _human_pause(1.2, 2.6)

    submit_selectors = [
        'button[type="submit"]',
        'button:has-text("Log in")',
        'button:has-text("Prisijungti")',
        'button:has-text("Zaloguj")',
    ]
    for sel in submit_selectors:
        try:
            await page.locator(sel).first.click(timeout=3000)
            logger.debug(f"Submit clicked via {sel}")
            return
        except PWTimeout:
            continue
    raise RuntimeError("Could not find submit button on login page")


async def _state_loop(
    page: Page,
    context: BrowserContext,
    max_seconds: int = 600,
) -> bool:
    start = time.monotonic()
    last_state: State | None = None
    last_challenge_log = 0.0
    login_filled = False

    while time.monotonic() - start < max_seconds:
        state = await _detect_state(page)

        if state != last_state:
            elapsed = int(time.monotonic() - start)
            logger.info(f"[{elapsed}s] State: {state}  URL: {page.url}")
            last_state = state

        if state == "dashboard":
            cookies = await context.cookies()
            if len(cookies) >= 5:
                logger.success(f"Dashboard reached with {len(cookies)} cookies")
                return True
            logger.debug(f"Dashboard but {len(cookies)} cookies, waiting for hydration...")

        elif state == "login" and not login_filled:
            try:
                await _fill_login_form_slowly(page, ALLEGRO_EMAIL, ALLEGRO_PASSWORD)
                login_filled = True
            except Exception as e:
                logger.error(f"Auto-fill failed: {e}")
                logger.warning("Please complete login manually in the browser.")

        elif state == "challenge":
            now = time.monotonic()
            if now - last_challenge_log > 20:
                logger.warning("=" * 60)
                logger.warning("DATADOME / CAPTCHA challenge detected.")
                logger.warning(">>> SOLVE IT MANUALLY in the browser window <<<")
                logger.warning(f"Will keep waiting (up to {max_seconds}s total).")
                logger.warning("=" * 60)
                last_challenge_log = now

        await _human_pause(2.0, 3.5)

    logger.error(
        f"Timed out after {max_seconds}s. Last state: {last_state}, URL: {page.url}"
    )
    return False


async def _launch_persistent_context(p):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    base_kwargs = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": HEADLESS,
        "args": LAUNCH_ARGS,
        "user_agent": BROWSER_USER_AGENT,
        "viewport": {"width": 1366, "height": 800},
        "locale": "en-US",
    }
    for channel in ("chrome", None):
        try:
            kwargs = dict(base_kwargs)
            if channel:
                kwargs["channel"] = channel
            ctx = await p.chromium.launch_persistent_context(**kwargs)
            logger.info(
                f"Launched persistent context "
                f"(channel={channel or 'chromium-bundled'}, profile={PROFILE_DIR})"
            )
            return ctx
        except Exception as e:
            logger.warning(f"Launch failed for channel={channel}: {e}")
    raise RuntimeError("Could not launch any browser")


async def login_and_save_state() -> None:
    if not ALLEGRO_EMAIL or not ALLEGRO_PASSWORD:
        logger.error("ALLEGRO_EMAIL / ALLEGRO_PASSWORD not set in .env")
        sys.exit(1)

    if HEADLESS:
        logger.warning(
            "HEADLESS=true — if Datadome challenge appears you cannot solve it. "
            "Set HEADLESS=false for first runs."
        )

    async with Stealth().use_async(async_playwright()) as p:
        context = await _launch_persistent_context(p)
        page = context.pages[0] if context.pages else await context.new_page()

        logger.info("Letting browser settle for 3s before navigating...")
        await _human_pause(2.5, 4.0)

        logger.info(f"Navigating to {SALES_CENTER_URL}")
        try:
            await page.goto(SALES_CENTER_URL, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            logger.warning("Initial navigation timed out, continuing")

        await _human_pause(2.0, 4.0)
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except PWTimeout:
            logger.debug("networkidle timeout, continuing")

        await _accept_cookies_if_present(page)
        await _human_pause(1.0, 2.0)

        ok = await _state_loop(page, context, max_seconds=600)

        if not ok:
            logger.error("Login flow did not complete. Browser left open.")
            logger.error("Inspect manually, then Ctrl+C the terminal.")
            await asyncio.sleep(3600)
            await context.close()
            sys.exit(1)

        cookies = await context.cookies()
        logger.info(f"Final cookie count: {len(cookies)}")
        logger.info(f"Cookie names: {', '.join(sorted(c['name'] for c in cookies))}")

        await context.storage_state(path=str(STORAGE_STATE_PATH))
        logger.success(f"State saved → {STORAGE_STATE_PATH}")

        await context.close()


if __name__ == "__main__":
    logger.add(
        "logs/login_{time:YYYYMMDD}.log",
        rotation="00:00",
        retention="14 days",
        level="DEBUG",
    )
    asyncio.run(login_and_save_state())
