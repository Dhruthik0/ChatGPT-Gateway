"""Every ChatGPT selector lives here. A redesign is a one-file fix.

verified 2026-10-06 against chatgpt.com
"""

BASE_URL = "https://chatgpt.com"
TEMP_CHAT_URL = f"{BASE_URL}/?temporary-chat=true"
TAB_URL_PREFIXES = ("https://chatgpt.com", "https://chat.openai.com")

# Logged-out chatgpt.com still renders a composer, so selector presence is NOT an
# auth signal. This endpoint returns {} logged out, {user:...} logged in.
SESSION_ENDPOINT = "/api/auth/session"

# 2026-10-06 redesign dropped #prompt-textarea, every data-testid and
# data-message-author-role. aria-labels are English-UI only: keep ChatGPT in English.
COMPOSER = 'form div.ProseMirror[contenteditable="true"]'
SEND_BUTTON = 'form button[aria-label="Send"]'
STOP_BUTTON = 'form button[aria-label="Stop"]'
ASSISTANT_TURN = '[data-markdown-text-style="assistant-message"]'

LOGIN_MARKER = '[data-testid="login-button"], [data-testid="mobile-login-button"], a[href*="/auth/login"]'
CHALLENGE_MARKER = '#challenge-running, #cf-challenge-running, iframe[src*="challenges.cloudflare.com"]'
# "Too many requests" is the throttle modal, observed 2026-09-16: it overlays the
# composer, so it surfaces as a failed click rather than a failed generation.
RATE_LIMIT_MARKER = ('text=/You.ve reached your (data analysis |image generation |)limit'
                     '|message limit|Too many requests|making requests too quickly|Please wait/i')

# probed by GET /health/dom; missing any of these means they shipped a redesign
REQUIRED = {
    "composer": COMPOSER,
    "send_button": SEND_BUTTON,
}
