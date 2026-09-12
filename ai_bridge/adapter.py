import asyncio
import logging
import time
from pathlib import Path

from playwright.async_api import Error as PWError, Page

from . import selectors as S
from .browser import BrowserManager
from .errors import BridgeError

log = logging.getLogger("ai_bridge.adapter")

_EXTRACT_JS = (Path(__file__).parent / "extract.js").read_text()

POLL = 0.15          # s between state polls
STABLE_READS = 3     # identical extractions required after the stop button vanishes
STABLE_GAP = 0.25    # s between stability reads  -> ~750ms settle


async def _visible(page: Page, selector: str) -> bool:
    try:
        loc = page.locator(selector).first
        return await loc.count() > 0 and await loc.is_visible()
    except PWError:
        return False


class ChatGPTAdapter:
    """The only class that knows ChatGPT exists.

    Interface used by everything above: ask / health / stop / close.
    An OpenAI-API backend later is one new file implementing the same three.
    """

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    # ---------- public ----------

    async def health(self) -> dict:
        report = {"browser": False, "chatgpt_tab": False, "authenticated": False, "challenge": False}
        try:
            page = await self.browser.page()
        except BridgeError as exc:
            report["error"] = exc.code
            return report
        report["browser"] = True
        report["chatgpt_tab"] = page.url.startswith(S.TAB_URL_PREFIXES)
        report["url"] = page.url.split("?")[0]
        report["challenge"] = await _visible(page, S.CHALLENGE_MARKER)
        report["authenticated"] = bool(await self._authenticated(page))
        return report

    async def dom_probe(self) -> dict:
        """The canary. Tells you they shipped a redesign before a caller finds out."""
        page = await self.browser.page()
        return {name: await _visible(page, sel) for name, sel in S.REQUIRED.items()}

    async def stop(self) -> None:
        """Cancel an in-flight generation."""
        try:
            page = await self.browser.page()
            if await _visible(page, S.STOP_BUTTON):
                await page.locator(S.STOP_BUTTON).first.click(timeout=3000)
        except (BridgeError, PWError):
            pass

    async def ask(self, prompt: str, timeout: int, new_chat: bool = True) -> str:
        deadline = time.monotonic() + timeout
        page = await self.browser.page()

        # a stray tab (an OAuth redirect, say) must not be treated as a chat
        if new_chat or not page.url.startswith(S.TAB_URL_PREFIXES):
            await self._goto(page, S.TEMP_CHAT_URL)

        await self._assert_ready(page)
        before = await page.locator(S.ASSISTANT_TURN).count()

        t0 = time.monotonic()
        await self._submit(page, prompt)
        await self._wait_started(page, before, deadline)
        log.info("adapter.generation_started after_ms=%d", (time.monotonic() - t0) * 1000)

        t1 = time.monotonic()
        await self._wait_finished(page, deadline)
        text = await self._wait_stable(page, deadline)

        log.info("adapter.generation_complete stream_ms=%d chars=%d source=dom",
                 (time.monotonic() - t1) * 1000, len(text))
        return text

    # ---------- state machine ----------

    async def _authenticated(self, page: Page) -> bool | None:
        """Ask ChatGPT itself, not the DOM. Returns None if we cannot tell."""
        if not page.url.startswith(S.TAB_URL_PREFIXES):
            return None
        try:
            data = await page.evaluate(
                "async (path) => {"
                "  const r = await fetch(path, {credentials: 'include'});"
                "  if (!r.ok) return null;"
                "  try { return await r.json(); } catch (e) { return null; }"
                "}", S.SESSION_ENDPOINT)
        except PWError:
            return None
        if data is None:
            return None
        return bool(data.get("user"))

    async def _goto(self, page: Page, url: str) -> None:
        """chatgpt.com is a SPA; a client-side route change can abort a goto."""
        for attempt in (1, 2):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                return
            except PWError as exc:
                if attempt == 2 or "ERR_ABORTED" not in str(exc):
                    raise BridgeError("CHATGPT_PAGE_LOAD_FAILED", str(exc).splitlines()[0]) from exc
                await asyncio.sleep(1)

    async def _assert_ready(self, page: Page) -> None:
        if await _visible(page, S.CHALLENGE_MARKER):
            raise BridgeError("CHATGPT_CHALLENGE_REQUIRED")
        if await self._authenticated(page) is False:
            raise BridgeError("CHATGPT_NOT_AUTHENTICATED")
        try:
            await page.locator(S.COMPOSER).first.wait_for(state="visible", timeout=15000)
        except PWError:
            if await _visible(page, S.CHALLENGE_MARKER):
                raise BridgeError("CHATGPT_CHALLENGE_REQUIRED") from None
            if await _visible(page, S.LOGIN_MARKER):
                raise BridgeError("CHATGPT_NOT_AUTHENTICATED") from None
            raise BridgeError("CHATGPT_DOM_CHANGED", f"composer {S.COMPOSER} not found") from None

    async def _submit(self, page: Page, prompt: str) -> None:
        try:
            composer = page.locator(S.COMPOSER).first
            await composer.click(timeout=5000)
            # The composer is a ProseMirror contenteditable: .value / .innerText are
            # ignored by its document model. insertText fires real input events.
            # Newlines here do not submit -- we submit by clicking the button.
            await page.keyboard.insert_text(prompt)
            await page.locator(S.SEND_BUTTON).first.click(timeout=10000)
        except PWError as exc:
            raise BridgeError("PROMPT_SUBMIT_FAILED", str(exc).splitlines()[0]) from exc

    async def _wait_started(self, page: Page, before: int, deadline: float) -> None:
        """Wait for the stop button to APPEAR (or a new turn to land).

        Waiting only for "stop button gone" races a button that has not changed
        yet and returns an empty answer instantly. This ordering is the guard.
        """
        limit = min(deadline, time.monotonic() + 30)
        while time.monotonic() < limit:
            if await _visible(page, S.STOP_BUTTON):
                return
            if await page.locator(S.ASSISTANT_TURN).count() > before:
                return  # answer was fast enough that we missed the stop button
            await asyncio.sleep(POLL)
        if await _visible(page, S.RATE_LIMIT_MARKER):
            raise BridgeError("CHATGPT_RATE_LIMITED")
        raise BridgeError("PROMPT_SUBMIT_FAILED", "generation never started")

    async def _wait_finished(self, page: Page, deadline: float) -> None:
        while time.monotonic() < deadline:
            if not await _visible(page, S.STOP_BUTTON):
                return
            await asyncio.sleep(POLL)
        raise BridgeError("GENERATION_TIMEOUT")

    async def _wait_stable(self, page: Page, deadline: float) -> str:
        """ChatGPT re-renders once after the stream ends; wait for it to settle."""
        last, same = None, 0
        while time.monotonic() < deadline:
            text = await self._extract(page)
            if text and text == last:
                same += 1
                if same >= STABLE_READS - 1:
                    return text
            else:
                same = 0
            last = text
            await asyncio.sleep(STABLE_GAP)
        if last:
            return last
        raise BridgeError("GENERATION_TIMEOUT", "response never stabilised")

    async def _extract(self, page: Page) -> str:
        try:
            turns = page.locator(S.ASSISTANT_TURN)
            if await turns.count() == 0:
                raise BridgeError("RESPONSE_EXTRACT_FAILED", "no assistant message on the page")
            handle = await turns.last.element_handle(timeout=5000)
            return (await page.evaluate(_EXTRACT_JS, handle) or "").strip()
        except PWError as exc:
            raise BridgeError("RESPONSE_EXTRACT_FAILED", str(exc).splitlines()[0]) from exc
