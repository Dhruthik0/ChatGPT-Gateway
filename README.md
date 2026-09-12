# Local AI Bridge

A localhost service that pipes `input + instruction` through your existing,
already-logged-in ChatGPT **web** session and returns the answer.

```
POST /ask  →  ChatGPT web  →  response
```

Task-agnostic: explain code, rewrite text, translate, summarise, classify —
anything you'd type into ChatGPT.

See `IMPLEMENTATION_PLAN.md` for the feasibility analysis and design rationale.

> Automating the ChatGPT web UI is against OpenAI's Terms of Use. Personal-scale
> use on your own account, at your own risk.

## Install

```bash
sudo apt install python3.12-venv        # one-time, if you don't have it
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Playwright's browsers are **not** needed — the bridge attaches to your system Chrome.

## First run

```bash
bin/start-chrome.sh          # dedicated Chrome profile on CDP port 9333
```

Log into ChatGPT in that window. Once. Cookies persist in
`~/.local/share/ai-bridge/chrome-profile`.

Chrome ≥136 ignores `--remote-debugging-port` on the default profile, so this
*must* be a separate profile — you cannot attach to your everyday browser.

Then, in another terminal:

```bash
.venv/bin/python -m ai_bridge
```

First start writes `~/.config/ai-bridge/config.toml` (mode 0600) with a generated
API token.

## Use

```bash
bin/ai-bridge health
bin/ai-bridge ask -i "Explain this simply" --input 'function add(a,b){return a+b}'
cat main.py | bin/ai-bridge ask -i "Find the bug"
bin/ai-bridge ask -i "Now in one sentence" --continue     # same conversation
```

Stdout carries only the answer, so it pipes.

Raw HTTP:

```bash
TOKEN=$(grep '^token' ~/.config/ai-bridge/config.toml | cut -d'"' -f2)
curl -s localhost:8756/ask -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"Translate to French","input":"good morning"}' | jq -r .response
```

## API

| Endpoint | Notes |
|---|---|
| `POST /ask` | `{instruction, input?, timeout?, new_chat?}` → `{success, response, job_id, duration_ms}` |
| `POST /ask?async=1` | 202 + `{job_id}` |
| `GET /jobs/{id}` | `queued` \| `running` \| `done` \| `failed` \| `cancelled` |
| `DELETE /jobs/{id}` | cancel (clicks stop-generating if running) |
| `GET /health` | no auth; browser / tab / auth / challenge / queue depth |
| `GET /health/dom` | selector canary — run this first when things break |

Errors are always `{"success": false, "error": {"code", "message", "hint"}}`.
Codes are listed in `ai_bridge/errors.py`.

## Run as a service

```bash
mkdir -p ~/.config/systemd/user && cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ai-bridge-chrome ai-bridge
```

The Chrome unit needs a graphical session; it starts with your desktop login.

## Tests

```bash
.venv/bin/pytest              # API surface against a fake adapter, no browser
.venv/bin/pytest -m live      # 3 real ChatGPT round-trips; needs Chrome up + logged in
```

## When it breaks

It will — ChatGPT's frontend changes. In order:

1. `bin/ai-bridge dom` — if a selector reports `false`, they shipped a redesign.
2. Fix `ai_bridge/selectors.py`. That file plus `adapter.py`, `extract.js` and
   `stream.py` are the only ChatGPT-aware code in the project.
3. `.venv/bin/pytest -m live` to confirm.

If `/health` says `challenge: true`, click the Cloudflare checkbox in the Chrome
window yourself. The bridge deliberately does not try to defeat it.

## How the answer is extracted

Two paths, preferred order:

1. **The SSE stream** (`ai_bridge/stream.py`) — the bridge captures ChatGPT's own
   `POST /backend-api/f/conversation` event stream and reconstructs the original
   markdown. This is the source of truth: the rendered page has already thrown
   information away (code fences lose their language).
2. **The DOM** (`ai_bridge/extract.js`) — used when the stream can't be parsed, e.g.
   after a wire-format change. Content is correct; fences come back untagged.

The log line says which was used: `source=stream` or `source=dom`. A sustained
`source=dom` means the wire format moved and `stream.py` needs updating — the
captured fixture in `tests/fixtures/` makes that a test-first fix.

Completion detection stays DOM-driven (stop-button appear-then-vanish), which has
proven reliable.

## Known gaps

- One request at a time, ~8-10s each. Personal scale, not a throughput tool.
- Running a second Chrome costs ~1.8 GB resident. On a memory-tight box the OOM
  killer may take it; the bridge reconnects automatically once it's back.

## Security

Binds `127.0.0.1` only and refuses anything else. Bearer token required; non-loopback
`Host` headers rejected (DNS rebinding). 32 KB input cap. Prompts and responses are
never logged unless you set `log_bodies = true`. No credentials are stored — login is
manual, in the browser. The Chrome profile dir holds live session cookies: keep it 700.
