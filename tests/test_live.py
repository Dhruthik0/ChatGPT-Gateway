"""Real round-trips against ChatGPT. Excluded by default.

Run with:  pytest -m live
This is the regression suite for ChatGPT redesigns -- run it when something smells.
"""
import pytest

from ai_bridge import config as cfg_mod
from ai_bridge.adapter import ChatGPTAdapter
from ai_bridge.browser import BrowserManager

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


@pytest.fixture
async def adapter():
    cfg = cfg_mod.load()
    br = BrowserManager(cfg.cdp_url)
    await br.start()
    try:
        yield ChatGPTAdapter(br)
    finally:
        await br.close()


async def test_prose(adapter):
    out = await adapter.ask("Reply with exactly the word: pong", timeout=90)
    assert "pong" in out.lower()


async def test_code_keeps_fences(adapter):
    out = await adapter.ask("Write a python hello world. Code block only.", timeout=120)
    assert "```" in out and "print" in out


async def test_fence_language_survives(adapter):
    """The DOM drops the language; this passes only via the SSE path."""
    out = await adapter.ask(
        "Show a Python hello world and a JavaScript hello world, each in its own "
        "fenced code block with the language named.", timeout=120)
    assert "```python" in out and "```javascript" in out


async def test_long_input(adapter):
    from ai_bridge.prompt import build
    out = await adapter.ask(build("Count the lines. Reply with only the number.",
                                  "\n".join(f"line {i}" for i in range(200))), timeout=120)
    assert out.strip()
