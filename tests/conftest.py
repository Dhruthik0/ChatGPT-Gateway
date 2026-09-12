import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_bridge.api import create_app          # noqa: E402
from ai_bridge.config import Config           # noqa: E402
from ai_bridge.errors import BridgeError      # noqa: E402

TOKEN = "test-token"


class FakeAdapter:
    """Stands in for ChatGPT. Lets the whole API surface be tested without a browser."""

    def __init__(self):
        self.raise_code = None
        self.delay = 0
        self.calls = []
        self.stopped = 0

    async def ask(self, prompt, timeout, new_chat=True):
        self.calls.append((prompt, timeout, new_chat))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_code:
            raise BridgeError(self.raise_code)
        return "fake answer"

    async def health(self):
        return {"browser": True, "chatgpt_tab": True, "authenticated": True, "challenge": False}

    async def dom_probe(self):
        return {"composer": True, "send_button": True}

    async def stop(self):
        self.stopped += 1


@pytest.fixture
def cfg():
    return Config(host="127.0.0.1", port=8756, token=TOKEN, cdp_url="http://127.0.0.1:9333",
                  profile_dir=Path("/tmp/none"), max_input_bytes=1000, default_timeout=30,
                  max_queue=2, log_bodies=False)


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def client(cfg, adapter):
    with TestClient(create_app(adapter=adapter, config=cfg), base_url="http://127.0.0.1:8756") as c:
        c.headers.update({"Authorization": f"Bearer {TOKEN}"})
        yield c
