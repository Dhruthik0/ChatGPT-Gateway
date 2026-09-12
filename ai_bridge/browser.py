import asyncio
import logging

from playwright.async_api import Error as PWError, Page, async_playwright

from . import selectors as S
from .errors import BridgeError

log = logging.getLogger("ai_bridge.browser")


class BrowserManager:
    """Attaches to a Chrome the user launched. Never launches one itself.

    A Playwright-launched Chromium gets flagged by Cloudflare almost immediately;
    a warm real-Chrome profile with no automation flags is the only approach with
    decent odds. See IMPLEMENTATION_PLAN.md 1.1.
    """

    def __init__(self, cdp_url: str):
        self.cdp_url = cdp_url
        self._pw = None
        self._browser = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._pw = await async_playwright().start()

    async def close(self) -> None:
        try:
            if self._browser and self._browser.is_connected():
                # we attached, we don't own it -- disconnect, never close
                await self._browser.close()
        except PWError:
            pass
        finally:
            if self._pw:
                await self._pw.stop()

    @property
    def connected(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def _connect(self) -> None:
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url, timeout=5000)
        except Exception as exc:
            raise BridgeError("BROWSER_UNAVAILABLE", f"Cannot reach CDP at {self.cdp_url}.") from exc
        log.info("browser.attached cdp=%s chrome=%s", self.cdp_url, self._browser.version)

    async def page(self) -> Page:
        """The ChatGPT tab, reconnecting and reopening as needed."""
        async with self._lock:
            if not self.connected:
                self._page = None
                await self._connect()
            if self._page is not None and not self._page.is_closed():
                return self._page
            self._page = await self._find_or_open()
            return self._page

    async def _find_or_open(self) -> Page:
        try:
            contexts = self._browser.contexts
            for ctx in contexts:
                for pg in ctx.pages:
                    if pg.url.startswith(S.TAB_URL_PREFIXES):
                        log.info("browser.tab_found url=%s", pg.url.split("?")[0])
                        return pg
            ctx = contexts[0] if contexts else await self._browser.new_context()
            pg = await ctx.new_page()
            await pg.goto(S.BASE_URL, wait_until="domcontentloaded", timeout=45000)
            log.info("browser.tab_opened")
            return pg
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError("CHATGPT_TAB_UNAVAILABLE", str(exc)) from exc
