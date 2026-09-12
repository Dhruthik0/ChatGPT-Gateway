"""Parse ChatGPT's SSE stream into the assistant's original markdown.

Why this exists: the rendered DOM has lost information the stream still has --
as of 2026-09-10 code fences come back untagged because no `language-*` class
survives into the page. The stream carries the real markdown.

Wire format (delta_encoding "v1"), verified 2026-09-11:

    event: delta
    data: {"p":"","o":"add","v":{"message":{...}}}          <- message object
    data: {"v":{"message":{...}},"c":1}                      <- ditto, shorthand
    data: {"p":"/message/content/parts/0","o":"append","v":"```"}
    data: {"o":"patch","v":[{"p":"/message/content/parts/0","o":"append","v":"python\\n"}, ...]}
    data: {"v":"more text"}                                  <- implied path == previous append
    data: [DONE]
"""
import json

PART0 = "/message/content/parts/0"


def parse(body: str) -> str | None:
    """Return the assistant's markdown, or None if the stream wasn't parseable."""
    buf: str | None = None
    appending = False          # was the last op an append to PART0?

    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            delta = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(delta, dict):
            continue

        value = delta.get("v")

        # a whole message object: starts (or restarts) the buffer
        if isinstance(value, dict) and "message" in value:
            message = value["message"]
            if message.get("author", {}).get("role") == "assistant":
                parts = message.get("content", {}).get("parts") or [""]
                buf = parts[0] if isinstance(parts[0], str) else ""
            appending = False
            continue

        ops = value if delta.get("o") == "patch" and isinstance(value, list) else [delta]
        for op in ops:
            if not isinstance(op, dict):
                continue
            path, kind, val = op.get("p"), op.get("o"), op.get("v")
            if buf is None or not isinstance(val, str):
                if path == PART0:
                    appending = False
                continue
            if path == PART0 and kind == "append":
                buf += val
                appending = True
            elif path == PART0 and kind == "replace":
                buf = val
                appending = True
            elif path is None and kind is None and appending:
                buf += val          # shorthand continuation of the previous append
            elif path is not None:
                appending = False

    return buf.strip() if buf else None


import re  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

_SSE_PATH = re.compile(r"/backend-api/(f/)?conversation$")


def is_stream(response) -> bool:
    """True for the SSE response that carries a generation."""
    ctype = response.headers.get("content-type") or ""
    return "event-stream" in ctype and _SSE_PATH.search(urlsplit(response.url).path) is not None
