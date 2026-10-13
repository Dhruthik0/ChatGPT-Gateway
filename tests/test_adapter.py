"""Adapter logic that has no browser in it: the probe wait, the retry, rotation."""
import asyncio
import time
from pathlib import Path

import pytest
from playwright.async_api import Error as PWError

from ai_bridge import adapter as A
from ai_bridge.errors import BridgeError


class FakeLocator:
    """Invisible until `ready_at`, like an element the page has not hydrated yet."""

    def __init__(self, ready_at):
        self.ready_at = ready_at

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def is_visible(self):
        return time.monotonic() >= self.ready_at

    async def wait_for(self, state, timeout):
        limit = time.monotonic() + timeout / 1000
        while time.monotonic() < limit:
            if await self.is_visible():
                return
            await asyncio.sleep(0.01)
        raise PWError("timeout")


class FakePage:
    def __init__(self, ready_in=0.0):
        self.loc = FakeLocator(time.monotonic() + ready_in)
        self.shots = []

    def locator(self, selector):
        return self.loc

    async def screenshot(self, path, timeout):
        Path(path).write_bytes(b"png")
        self.shots.append(path)


class FakeBrowser:
    def __init__(self, page=None):
        self._page = page

    async def page(self):
        if self._page is None:
            raise BridgeError("BROWSER_UNAVAILABLE")
        return self._page


# ---------- the probe wait (the canary called a hydrating page "drift") ----------

async def test_a_snapshot_misses_an_element_that_is_still_rendering():
    page = FakePage(ready_in=0.2)
    assert await A._visible(page, "sel") is False
    assert await A._visible(page, "sel", timeout=2000) is True


async def test_dom_probe_waits_instead_of_reporting_drift():
    adapter = A.ChatGPTAdapter(FakeBrowser(FakePage(ready_in=0.2)))
    assert all((await adapter.dom_probe()).values())


async def test_dom_probe_still_reports_a_selector_that_never_shows(monkeypatch):
    monkeypatch.setattr(A, "PROBE_TIMEOUT", 50)
    adapter = A.ChatGPTAdapter(FakeBrowser(FakePage(ready_in=999)))
    assert not any((await adapter.dom_probe()).values())


# ---------- a throttle modal is not a broken composer ----------

class BlockedPage:
    """The composer is there; a modal on top of it swallows the click."""

    def __init__(self, rate_limited):
        self.rate_limited = rate_limited
        self.keyboard = self

    async def insert_text(self, text):
        pass

    def locator(self, selector):
        page = self

        class Loc:
            @property
            def first(self):
                return self

            async def click(self, timeout=None):
                if selector == A.S.SEND_BUTTON:
                    raise PWError("intercepts pointer events")

            async def count(self):
                return 1 if selector == A.S.RATE_LIMIT_MARKER and page.rate_limited else 0

            async def is_visible(self):
                return page.rate_limited

        return Loc()


async def test_a_throttle_modal_reports_rate_limited_not_a_broken_composer():
    """Observed 2026-09-16: "Too many requests" overlays the composer. Reporting
    PROMPT_SUBMIT_FAILED sends you to fix selectors, and retries the throttle."""
    adapter = A.ChatGPTAdapter(FakeBrowser())
    with pytest.raises(BridgeError) as err:
        await adapter._submit(BlockedPage(rate_limited=True), "hi")
    assert err.value.code == "CHATGPT_RATE_LIMITED"
    assert err.value.code not in A.RETRY_ON      # retrying a throttle makes it worse


async def test_a_genuinely_broken_send_button_still_reports_submit_failed():
    adapter = A.ChatGPTAdapter(FakeBrowser())
    with pytest.raises(BridgeError) as err:
        await adapter._submit(BlockedPage(rate_limited=False), "hi")
    assert err.value.code == "PROMPT_SUBMIT_FAILED"


# ---------- the retry ----------

def _adapter_failing(monkeypatch, codes, calls):
    adapter = A.ChatGPTAdapter(FakeBrowser())

    async def fake_once(prompt, deadline, new_chat):
        calls.append(new_chat)
        code = codes[len(calls) - 1]
        if code:
            raise BridgeError(code)
        return "answer"

    monkeypatch.setattr(adapter, "_ask_once", fake_once)
    return adapter


async def test_retries_a_stale_page_once_in_a_fresh_chat(monkeypatch):
    calls = []
    adapter = _adapter_failing(monkeypatch, ["PROMPT_SUBMIT_FAILED", None], calls)
    assert await adapter.ask("hi", timeout=30, new_chat=False) == "answer"
    assert calls == [False, True]        # the retry forces a fresh temporary chat


async def test_gives_up_after_one_retry(monkeypatch):
    calls = []
    adapter = _adapter_failing(monkeypatch, ["CHATGPT_DOM_CHANGED"] * 2, calls)
    with pytest.raises(BridgeError) as err:
        await adapter.ask("hi", timeout=30)
    assert err.value.code == "CHATGPT_DOM_CHANGED"
    assert len(calls) == 2


async def test_does_not_retry_errors_a_reload_cannot_fix(monkeypatch):
    calls = []
    adapter = _adapter_failing(monkeypatch, ["CHATGPT_RATE_LIMITED", None], calls)
    with pytest.raises(BridgeError):
        await adapter.ask("hi", timeout=30)
    assert len(calls) == 1


async def test_does_not_retry_past_the_deadline(monkeypatch):
    calls = []
    adapter = _adapter_failing(monkeypatch, ["PROMPT_SUBMIT_FAILED", None], calls)
    with pytest.raises(BridgeError):
        await adapter.ask("hi", timeout=0)
    assert len(calls) == 1


# ---------- failure screenshots ----------

async def test_failure_screenshot_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "FAILURE_DIR", tmp_path / "failures")
    page = FakePage()
    adapter = A.ChatGPTAdapter(FakeBrowser(page))
    await adapter._shot("PROMPT_SUBMIT_FAILED-try1")
    written = list((tmp_path / "failures").glob("*.png"))
    assert len(written) == 1
    assert written[0].name.endswith("-PROMPT_SUBMIT_FAILED-try1.png")


async def test_only_the_last_20_screenshots_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "FAILURE_DIR", tmp_path)
    page = FakePage()
    for i in range(25):
        await A._write_shot(page, f"{i:02d}")
    assert len(list(tmp_path.glob("*.png"))) == A.FAILURE_KEEP


async def test_a_dead_browser_does_not_mask_the_real_error():
    adapter = A.ChatGPTAdapter(FakeBrowser(None))
    await adapter._shot("anything")     # must not raise
