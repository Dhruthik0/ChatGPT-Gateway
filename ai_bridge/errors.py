"""Typed errors. Every failure path maps to one of these codes."""

# code -> (http status, default message, hint)
_ERRORS = {
    "BROWSER_UNAVAILABLE": (503, "No browser is listening on the CDP port.", "Run bin/start-chrome.sh"),
    "BROWSER_DISCONNECTED": (503, "The browser connection dropped.", "Check that Chrome is still running."),
    "CHATGPT_TAB_UNAVAILABLE": (503, "The ChatGPT tab is gone and could not be reopened.", None),
    "CHATGPT_NOT_AUTHENTICATED": (401, "The ChatGPT session requires authentication.", "Log in manually in the ai-bridge Chrome window."),
    "CHATGPT_CHALLENGE_REQUIRED": (503, "Cloudflare is showing a human-verification challenge.", "Solve it once in the ai-bridge Chrome window."),
    "CHATGPT_PAGE_LOAD_FAILED": (503, "The ChatGPT page failed to load.", None),
    "CHATGPT_RATE_LIMITED": (429, "ChatGPT is rate limiting this account.", "A message cap, or the 'Too many requests' throttle. Wait a few minutes; retrying now makes it worse."),
    "PROMPT_SUBMIT_FAILED": (502, "The prompt could not be submitted.", None),
    "GENERATION_TIMEOUT": (504, "ChatGPT did not finish generating in time.", "Raise `timeout` in the request."),
    "RESPONSE_EXTRACT_FAILED": (502, "Generation finished but no response could be extracted.", None),
    "CHATGPT_DOM_CHANGED": (502, "The ChatGPT page structure changed.", "Update ai_bridge/selectors.py; see GET /health/dom."),
    "REQUEST_CANCELLED": (499, "The request was cancelled.", None),
    "INPUT_TOO_LARGE": (413, "The request exceeds the maximum input size.", None),
    "BUSY": (503, "Too many queued requests.", None),
    "UNAUTHORIZED": (401, "Missing or invalid bearer token.", "The token lives in ~/.config/ai-bridge/config.toml"),
    "FORBIDDEN_HOST": (403, "Non-loopback Host header rejected.", None),
    "JOB_NOT_FOUND": (404, "Unknown or expired job id.", None),
    "INTERNAL": (500, "Internal error.", None),
}


class BridgeError(Exception):
    def __init__(self, code: str, message: str | None = None, hint: str | None = None):
        status, default_msg, default_hint = _ERRORS[code]
        self.code = code
        self.status = status
        self.message = message or default_msg
        self.hint = hint or default_hint
        super().__init__(f"{code}: {self.message}")

    def payload(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.hint:
            err["hint"] = self.hint
        return {"success": False, "error": err}
