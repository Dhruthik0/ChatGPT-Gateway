"""Every ChatGPT selector lives here. A redesign is a one-file fix.

verified 2026-09-10 against chatgpt.com
"""

BASE_URL = "https://chatgpt.com"
TEMP_CHAT_URL = f"{BASE_URL}/?temporary-chat=true"
TAB_URL_PREFIXES = ("https://chatgpt.com", "https://chat.openai.com")

# Logged-out chatgpt.com still renders a composer, so selector presence is NOT an
# auth signal. This endpoint returns {} logged out, {user:...} logged in.
SESSION_ENDPOINT = "/api/auth/session"

COMPOSER = "#prompt-textarea"
SEND_BUTTON = '[data-testid="send-button"]'
STOP_BUTTON = '[data-testid="stop-button"]'
ASSISTANT_TURN = '[data-message-author-role="assistant"]'

LOGIN_MARKER = '[data-testid="login-button"], [data-testid="mobile-login-button"], a[href*="/auth/login"]'
CHALLENGE_MARKER = '#challenge-running, #cf-challenge-running, iframe[src*="challenges.cloudflare.com"]'
RATE_LIMIT_MARKER = 'text=/You.ve reached your (data analysis |image generation |)limit|message limit|Please wait/i'

# probed by GET /health/dom; missing any of these means they shipped a redesign
REQUIRED = {
    "composer": COMPOSER,
    "send_button": SEND_BUTTON,
}
