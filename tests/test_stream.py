"""Parser tests against a real captured stream -- no browser needed.

tests/fixtures/sse_code_block.txt is a verbatim capture from chatgpt.com on
2026-09-11. When ChatGPT changes the wire format these fail, which is the point.
"""
import json
from pathlib import Path

from ai_bridge import stream

FIXTURE = (Path(__file__).parent / "fixtures/sse_code_block.txt").read_text()


def test_parses_real_capture():
    assert stream.parse(FIXTURE) == "```python\nx = 1\n```"


def test_recovers_what_the_dom_loses():
    """The rendered DOM drops the fence language; the stream keeps it."""
    assert "```python" in stream.parse(FIXTURE)


def _sse(*deltas):
    return "\n".join("data: " + json.dumps(d) for d in deltas) + "\ndata: [DONE]\n"


def test_shorthand_continuation_appends():
    body = _sse(
        {"p": "", "o": "add", "v": {"message": {"author": {"role": "assistant"},
                                                "content": {"parts": [""]}}}},
        {"p": "/message/content/parts/0", "o": "append", "v": "Hello"},
        {"v": " world"},                       # implied path
    )
    assert stream.parse(body) == "Hello world"


def test_patch_batches_are_applied():
    body = _sse(
        {"p": "", "o": "add", "v": {"message": {"author": {"role": "assistant"},
                                                "content": {"parts": ["a"]}}}},
        {"o": "patch", "v": [{"p": "/message/content/parts/0", "o": "append", "v": "b"},
                             {"p": "/message/status", "o": "replace", "v": "finished_successfully"}]},
    )
    assert stream.parse(body) == "ab"


def test_user_message_is_not_mistaken_for_the_answer():
    body = _sse(
        {"v": {"message": {"author": {"role": "user"}, "content": {"parts": ["my question"]}}}},
        {"p": "", "o": "add", "v": {"message": {"author": {"role": "assistant"},
                                                "content": {"parts": [""]}}}},
        {"p": "/message/content/parts/0", "o": "append", "v": "my answer"},
    )
    assert stream.parse(body) == "my answer"


def test_unparseable_returns_none_so_the_dom_fallback_wins():
    assert stream.parse("garbage\ndata: not json\n") is None
    assert stream.parse("") is None
