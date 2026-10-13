"""Tab bookkeeping. A parked tab used to poison health, dom_probe and ask alike."""
import pytest

from ai_bridge.browser import BrowserManager
from ai_bridge.errors import BridgeError

CHAT = "https://chatgpt.com/"
PARKED = "chrome-error://chromewebdata/"


class FakePage:
    def __init__(self, url):
        self.url = url
        self.closed = False
        self.gotos = []

    def is_closed(self):
        return self.closed

    async def goto(self, url, **kw):
        self.gotos.append(url)
        self.url = url


class FakeContext:
    def __init__(self, pages):
        self.pages = pages
        self.opened = []

    async def new_page(self):
        page = FakePage("about:blank")
        self.opened.append(page)
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, pages):
        self.contexts = [FakeContext(pages)]

    def is_connected(self):
        return True


def manager(pages, cached=None):
    mgr = BrowserManager("http://127.0.0.1:9333")
    mgr._browser = FakeBrowser(pages)
    mgr._page = cached
    return mgr


async def test_a_live_chatgpt_tab_is_reused_untouched():
    page = FakePage(CHAT)
    mgr = manager([page], cached=page)
    assert await mgr.page() is page
    assert page.gotos == []


async def test_a_parked_tab_is_steered_back_not_left_to_rot():
    """The bug: one failed goto and health/dom_probe/ask all misread the tab."""
    page = FakePage(PARKED)
    mgr = manager([page], cached=page)
    got = await mgr.page()
    assert got is page                       # same tab, not a second one
    assert page.gotos == ["https://chatgpt.com"]
    assert mgr._browser.contexts[0].opened == []


async def test_a_parked_tab_does_not_leak_a_new_tab_each_time():
    page = FakePage(PARKED)
    mgr = manager([page], cached=page)
    for _ in range(3):
        page.url = PARKED                    # it keeps failing to load
        await mgr.page()
    assert len(mgr._browser.contexts[0].pages) == 1


async def test_a_real_chatgpt_tab_elsewhere_wins_over_reopening():
    parked, other = FakePage(PARKED), FakePage("https://chatgpt.com/c/abc")
    mgr = manager([parked, other], cached=parked)
    assert await mgr.page() is other
    assert parked.gotos == []


async def test_a_closed_tab_is_replaced():
    page = FakePage(CHAT)
    page.closed = True
    mgr = manager([], cached=page)      # playwright drops closed pages from ctx.pages
    got = await mgr.page()
    assert got is not page
    assert mgr._browser.contexts[0].opened == [got]


async def test_tab_trouble_surfaces_as_a_typed_error():
    class Exploding(FakeContext):
        async def new_page(self):
            raise RuntimeError("no renderer")

    mgr = manager([])
    mgr._browser.contexts = [Exploding([])]
    with pytest.raises(BridgeError) as err:
        await mgr.page()
    assert err.value.code == "CHATGPT_TAB_UNAVAILABLE"
