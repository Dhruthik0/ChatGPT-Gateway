# Local AI Bridge — Implementation Plan

Target machine: Linux (X11), Google Chrome 150, Python 3.12, Node 20.
Status: plan only. No code written yet.

---

## 0. Verdict

**Feasible.** A local service that drives an authenticated ChatGPT web session over
CDP and returns the response to a caller is buildable in roughly 500–700 lines of
Python. A working MVP is ~1 day of work; a version you'd actually leave running is
~3 days.

Three things you must accept before starting:

1. **This violates OpenAI's Terms of Use** (automated access to the web UI). For
   personal use on your own account the practical consequence is an account
   suspension risk, not a legal one. Your call — the plan assumes you've made it.
2. **It will break.** Not "if". ChatGPT's frontend ships changes weekly. The plan is
   built around *containing* that breakage (one selector file, a DOM health probe,
   loud typed errors instead of silent timeouts), not avoiding it.
3. **The OpenAI API does this in 20 lines with a 99.9% SLA.** The only reason to
   build this is to use a subscription you already pay for instead of per-token
   billing. That is a legitimate reason. Just be clear that's the trade: you're
   spending engineering reliability to save money.

Everything below assumes you're proceeding.

---

## 1. Feasibility analysis — the four hard problems

### 1.1 Bot detection (the one that can kill the project) — **HIGH RISK**

chatgpt.com sits behind Cloudflare bot management. This is the failure mode that
sinks most "automate ChatGPT web" projects, and it decides your entire architecture.

What gets flagged:

| Approach | Outcome |
|---|---|
| Playwright/Puppeteer launching **bundled Chromium**, headless | Blocked almost immediately. Wrong browser build, wrong UA, headless signals, `navigator.webdriver`. |
| Playwright launching **real Chrome**, headed, persistent profile | Works sometimes. Playwright injects automation flags (`--enable-automation`, `--disable-blink-features` juggling) and a fresh profile has no trust history. Expect periodic "Verify you are human" interstitials. |
| **Attach over CDP to a Chrome the user launched normally** | Best odds. The browser process has a real fingerprint, a warm profile with cookies and history, and no launch-time automation flags. This is the recommended path. |

Attaching is not undetectable — enabling the CDP `Runtime` domain is observable from
JS (the classic `console.log` on an object with a getter trick), and Cloudflare could
use it. But detection today is concentrated at page-load / challenge time, and a
warm real-Chrome profile passes that far more often than a Playwright-launched one.

**The decisive mitigation: run headed, and let the human solve challenges.**
The browser window is visible on your desktop. When Cloudflare throws a challenge,
the service does not try to defeat it — it returns
`CHATGPT_CHALLENGE_REQUIRED` and you click the checkbox once. This turns a
project-ending problem into an occasional 5-second interruption. Do not add stealth
plugins, UA spoofing, or captcha-solving services; they raise the fingerprint-anomaly
score and are what actually gets accounts banned.

**Chrome 150 constraint (verified on this machine):** since Chrome 136,
`--remote-debugging-port` is *ignored* when using the default user-data-dir. You
cannot attach to your everyday browser. You must launch a **dedicated profile**:

```
google-chrome \
  --user-data-dir="$HOME/.local/share/ai-bridge/chrome-profile" \
  --remote-debugging-port=9333 \
  --remote-allow-origins=http://127.0.0.1:9333
```

You log into ChatGPT in that window once. Cookies persist in that directory across
restarts. This is also better hygiene — the automation never touches your main
profile.

**Application-layer throttling is the limit you will actually hit, not Cloudflare
(observed 2026-09-16).** Cloudflare stayed quiet across a long adversarial test
session — zero challenges, exactly as §1.1 predicted. What *did* fire was ChatGPT's
own app-level throttle:

> **Too many requests** — You're making requests too quickly. We've temporarily
> limited access to your conversations to protect your data.

The trigger is the **shape** of the traffic, not just the count: dozens of
near-identical very short prompts ("Reply with exactly the word: ok"), each opening a
fresh temporary chat, fired back to back with no human pacing. That is a bot
signature regardless of how good the browser fingerprint is — §1.1's attach-over-CDP
strategy buys you nothing here, because nothing is being fingerprinted.

It bites harder than it looks, because the modal **overlays the composer** and
swallows the click, so it breaks §1.2's injection rather than §1.3's completion
detection. That is why `_submit` asks the page what happened before blaming the
composer. The recovery is a few minutes of quiet; retrying escalates it, which is
why `CHATGPT_RATE_LIMITED` is deliberately absent from `RETRY_ON`.

Consequences for this repo:

- `bin/ai-bridge-check` makes **two** real ChatGPT round trips, not three — the
  SSE-source check reuses the first answer instead of asking again. A canary that
  provokes the failure it watches for is worse than useless.
- Real use (a prompt here and there, with substance) is nowhere near this. The
  throttle is a testing hazard, not a usage limit.
- If you need to hammer it, pace it and vary the prompts.

### 1.2 Prompt injection into the composer — **LOW RISK, one gotcha**

The composer is a **ProseMirror contenteditable** (`#prompt-textarea`), not a
`<textarea>`. Consequences:

- Setting `.value` or `.innerText` does nothing. ProseMirror's internal document
  model won't see it and the send button stays disabled.
- The correct method is CDP `Input.insertText` (Playwright:
  `page.keyboard.insert_text()`). It fires real `beforeinput`/`input` events, so
  ProseMirror updates its model. It is also ~instant for large inputs, unlike
  per-character typing.
- Newlines inside `insert_text` do **not** submit — good, multi-line prompts are safe.
  Submit explicitly by clicking `[data-testid="send-button"]`, never by pressing Enter
  (Enter's binding changes with settings).

Solved problem. The only real work is escaping/framing so the user's input can't be
mistaken for instructions (see §6.1).

### 1.3 Response completion detection — **MEDIUM RISK, two independent solutions**

Never sleep a fixed duration. Two viable mechanisms; build one, keep the other in
your pocket.

**A. DOM state machine (MVP).** ChatGPT swaps the send button for a stop button while
generating: `[data-testid="send-button"]` → `[data-testid="stop-button"]`. So:

```
count assistant turns before send  →  submit
  →  wait for stop-button to APPEAR   (generation started, ~2s timeout)
  →  wait for stop-button to VANISH   (generation ended, long timeout)
  →  wait for last turn's text to be stable for 750ms  (settle guard)
  →  extract
```

The appear-then-vanish ordering matters: waiting only for "vanish" races against a
button that hasn't changed yet and returns instantly with an empty answer. The
750ms stability guard catches the post-stream re-render that ChatGPT does.

**B. Network SSE interception (hardening, Phase 5).** The frontend streams from
`/backend-api/*/conversation` as server-sent events terminated by `data: [DONE]`.
Watching that via CDP `Network`/Playwright `page.on("response")` gives you an exact
completion signal and the raw text, entirely independent of the DOM. It is strictly
more robust and it's what you fall back to when a redesign breaks the selectors. It's
also the foundation for a future token-streaming endpoint.

Start with A because it's 40 lines and debuggable by eye. Add B when A first breaks.

### 1.4 Response extraction fidelity — **LOW/MEDIUM RISK**

Assistant turns are `[data-message-author-role="assistant"]`. Taking `.innerText` of
the last one gets you readable prose but mangles code: it loses fences, and it picks
up the "Copy"/"Edit" button labels that live inside code-block headers.

Correct approach: run a small in-page JS extractor that walks the message subtree and
reconstructs markdown — `<pre><code>` → fenced block with the language from the
`class="language-*"`, `<li>` → `- `, and so on. ~40 lines of JS, worth it on day one
because half your use cases are code.

Belt-and-braces alternative: click the message's copy button and read the clipboard —
you get ChatGPT's own canonical markdown. But it requires clipboard permissions and
steals focus. Keep it as a documented fallback, don't build it first.

### 1.5 What this thing will *not* do

- **Throughput.** One browser tab, one request at a time, 5–30s each. It is a
  personal-scale tool, ~100–200 requests/day before you hit account message caps.
- **Reliability suitable for anything automated and unattended.** Assume 90–95%
  success in steady state, with occasional manual re-login.
- **Concurrency.** Parallel tabs multiply detection risk for near-zero gain.

---

## 2. Technology decisions

| Decision | Choice | Why | Rejected |
|---|---|---|---|
| Language | **Python 3.12** | Already on the box; the whole thing is glue + async I/O. | Node — equally fine, no advantage. Pick Python and stop deliberating. |
| HTTP server | **FastAPI + uvicorn** | Free request validation and OpenAPI docs; async matches the browser driver. | Flask (sync, needs a thread bridge). |
| Browser driver | **Playwright (Python), `connect_over_cdp`** | Best-in-class waiting primitives; attaches to real Chrome rather than launching Chromium. | Raw CDP (you'd rewrite Playwright's waiting logic). Selenium (heavier, worse waits). Puppeteer (needs Node). |
| Browser | **System Google Chrome, dedicated profile, headed** | §1.1. | Bundled Chromium; headless anything. |
| Config | **TOML at `~/.config/ai-bridge/config.toml`**, mode 0600 | Stdlib `tomllib`. | .env + a settings library. |
| Process mgmt | **systemd `--user` units** | Native, handles restart and logging. | supervisord, custom daemon. |
| Deps | `fastapi`, `uvicorn`, `playwright`, `httpx` (client/tests) | Four. | Anything else needs a written justification. |

---

## 3. Architecture

```
CLI / VS Code / scripts
        │  HTTP + Bearer token, 127.0.0.1 only
        ▼
┌──────────────────────────────────────────────┐
│ api.py        FastAPI: /ask /jobs /health     │
│ jobs.py       Job registry + single-worker    │
│               queue (global lock)             │
│ prompt.py     instruction + input → prompt    │
├──────────────────────────────────────────────┤
│ adapter.py    ChatGPTAdapter (the only file   │
│               that knows ChatGPT exists)      │
│ selectors.py  every CSS selector, alone       │
│ extract.js    DOM → markdown                  │
│ browser.py    CDP attach, reconnect, tab      │
├──────────────────────────────────────────────┤
│ errors.py     typed error codes               │
│ config.py     TOML + token                    │
└──────────────────────────────────────────────┘
```

Two rules keep this maintainable:

1. **`adapter.py` + `selectors.py` + `extract.js` are the only ChatGPT-aware files.**
   Everything above the line speaks a three-method interface:
   `async def ask(prompt, timeout) -> str`, `async def health() -> HealthReport`,
   `async def close()`. Swapping in an OpenAI-API backend later means writing one
   new file implementing those three methods (§11).
2. **Every selector lives in `selectors.py` as a named constant with a comment
   recording when it was last verified.** When ChatGPT redesigns, the fix is one file.

### Project layout

```
free_chatGpt_Api/
├── ai_bridge/
│   ├── __main__.py        entry: uvicorn + lifespan
│   ├── api.py             routes, auth, validation
│   ├── jobs.py            Job, JobStore, worker loop
│   ├── prompt.py          prompt assembly
│   ├── adapter.py         ChatGPTAdapter
│   ├── browser.py         CDP attach/reconnect
│   ├── selectors.py       ← the blast radius of a redesign
│   ├── extract.js         DOM → markdown
│   ├── errors.py          BridgeError + codes
│   └── config.py
├── bin/
│   ├── ai-bridge          CLI client (stdlib only, no deps)
│   └── start-chrome.sh    launches the dedicated profile
├── systemd/
│   ├── ai-bridge-chrome.service
│   └── ai-bridge.service
├── tests/
│   ├── test_api.py        fake adapter, no browser
│   ├── test_prompt.py
│   └── test_live.py       marked live, real browser
└── IMPLEMENTATION_PLAN.md
```

---

## 4. Local API

Bind `127.0.0.1:8756`. Every endpoint except `/health` requires
`Authorization: Bearer <token>`.

### `POST /ask` — synchronous

```json
{ "input": "function add(a,b){return a+b}",
  "instruction": "Explain this in simple terms",
  "timeout": 120,
  "new_chat": true }
```

`input` optional (instruction-only requests are valid). `instruction` required.
Combined size capped at 32 KB.

```json
{ "success": true,
  "response": "This function takes two numbers and returns their sum.",
  "job_id": "j_01H...",
  "duration_ms": 8412 }
```

Errors always:

```json
{ "success": false,
  "error": { "code": "CHATGPT_NOT_AUTHENTICATED",
             "message": "The ChatGPT session requires authentication.",
             "hint": "Run bin/start-chrome.sh and log in." } }
```

### The rest

| Endpoint | Purpose |
|---|---|
| `POST /ask?async=1` | Returns `{job_id, status:"queued"}` immediately (202). |
| `GET /jobs/{id}` | `queued` \| `running` \| `done` \| `failed` \| `cancelled` + result. |
| `DELETE /jobs/{id}` | Cancel. Queued → dropped; running → clicks stop-generating. |
| `GET /health` | No auth. `{status, browser, chatgpt_tab, authenticated, queue_depth}`. |
| `GET /health/dom` | Probes every selector, reports which are missing. **Your canary.** |

Sync and async are the same machinery: `/ask` creates a job, awaits it, and renders
the result. Async is free once jobs exist, so build jobs from the start — it's ~60
lines and retrofitting it later isn't.

Not building: config endpoints (edit the TOML), session/conversation management
(§7), streaming (Phase 7 if wanted).

---

## 5. Browser & session strategy

**Profile:** `~/.local/share/ai-bridge/chrome-profile/`, `chmod 700`. Contains live
ChatGPT session cookies — treat it like a credential store. Never in the repo, never
in backups you don't encrypt.

**Login:** once, by hand, in the visible window. The service never sees, stores, or
handles credentials. If the session expires, `/health` flips
`authenticated: false` and requests fail fast with `CHATGPT_NOT_AUTHENTICATED`
instead of timing out mysteriously.

**Attach, don't launch.** `bin/start-chrome.sh` (or its systemd unit) owns the
browser. The bridge connects with
`playwright.chromium.connect_over_cdp("http://127.0.0.1:9333")`. Benefits: browser
survives service restarts (no re-login on every code change), the human can use and
inspect the window, and you get real-Chrome fingerprinting.

**Tab management.** The bridge finds an existing chatgpt.com tab or opens one, tags
it, and reuses it for the process lifetime. It never closes tabs it didn't open. If
the tab is gone at request time → `CHATGPT_TAB_UNAVAILABLE`, reopen once, then fail.

**Auth detection** (cheap, in order): presence of the composer → logged in; presence
of a login/signup button → not; a Cloudflare challenge marker → `CHALLENGE_REQUIRED`.
Checked at startup, on every `/health`, and once before each request.

**Reconnect:** if the CDP websocket drops, the service does not die. It marks itself
degraded, fails in-flight requests with `BROWSER_DISCONNECTED`, and retries the
attach every 5s with backoff.

---

## 6. Prompt & conversation strategy

### 6.1 Prompt assembly

```
{instruction}

--- INPUT ---
{input}
--- END INPUT ---

Respond with only the result. Do not add preamble, explanation of your process,
or follow-up questions.
```

The delimiters exist so a user's input containing "ignore the above and..." reads as
data, not instruction. Not a security boundary — the LLM can still be talked out of
it — but it removes the common accidental case. If the caller's input contains the
delimiter string, the builder switches to a randomized nonce delimiter.

The trailing constraint matters: without it ChatGPT reliably wraps answers in
"Sure! Here's an explanation:" which callers then have to strip.

### 6.2 Conversation management — **Option A, temporary chats**

Chosen: **a fresh temporary chat per request**, via
`https://chatgpt.com/?temporary-chat=true`.

- vs. Option B (one persistent conversation): context bleeds between unrelated
  requests — a caller asking for a translation gets flavored by the previous
  caller's code review — and eventually you hit the context limit and have to
  handle it anyway.
- vs. Option C (client-managed sessions): the right long-term answer, wrong MVP
  answer. Build it when someone actually needs multi-turn.

Temporary chats additionally keep your ChatGPT history clean and are excluded from
training. Cost is a page navigation (~2s) per request; acceptable at this scale.
`new_chat: false` in the request body reuses the current conversation, which is the
30% of Option C anyone actually wants, for free.

---

## 7. Errors

`errors.py` defines one exception carrying a code, an HTTP status, and a hint. Every
failure path maps to one of these — no bare exceptions reach the client, and no
failure is reported as a successful empty response.

| Code | HTTP | Meaning |
|---|---|---|
| `BROWSER_UNAVAILABLE` | 503 | Nothing listening on the CDP port. |
| `BROWSER_DISCONNECTED` | 503 | CDP dropped mid-request. |
| `CHATGPT_TAB_UNAVAILABLE` | 503 | Tab closed/crashed, reopen failed. |
| `CHATGPT_NOT_AUTHENTICATED` | 401 | Logged out or session expired. |
| `CHATGPT_CHALLENGE_REQUIRED` | 503 | Cloudflare interstitial — human must click. |
| `CHATGPT_PAGE_LOAD_FAILED` | 503 | Navigation failed/timed out. |
| `CHATGPT_RATE_LIMITED` | 429 | Account message cap hit. |
| `PROMPT_SUBMIT_FAILED` | 502 | Composer or send button not usable. |
| `GENERATION_TIMEOUT` | 504 | Never finished within the deadline. |
| `RESPONSE_EXTRACT_FAILED` | 502 | Finished but nothing extractable. |
| `CHATGPT_DOM_CHANGED` | 502 | A required selector is missing → they shipped a redesign. |
| `REQUEST_CANCELLED` | 499 | Client cancelled. |
| `INPUT_TOO_LARGE` | 413 | Over 32 KB. |
| `BUSY` | 503 | Queue full (>10 waiting). |

`CHATGPT_DOM_CHANGED` is the important one. Distinguishing "the page changed" from
"generation was slow" is the difference between a 10-second fix and an hour of
confused debugging — which is why the DOM probe runs *before* submitting, not after
a timeout.

---

## 8. Security

Threat model: other local processes and other users on this machine; malicious web
pages using your browser as a confused deputy.

- **Bind `127.0.0.1` only.** Never `0.0.0.0`. Assert it at startup and refuse to
  start otherwise.
- **Bearer token**, 32 random bytes, generated on first run into the 0600 config
  file. Compared with `secrets.compare_digest`. Loopback alone is insufficient —
  any local process, including a browser extension or a malicious npm postinstall,
  can reach localhost.
- **Reject non-loopback `Host` headers** to block DNS-rebinding, where a web page
  you visit resolves an attacker domain to 127.0.0.1 and POSTs to your bridge.
- **No CORS.** No `Access-Control-Allow-Origin`. A Chrome-extension client uses the
  extension's background page, which isn't subject to page CORS.
- **32 KB input cap**, enforced before the browser is touched.
- **Logging:** INFO logs event + job id + duration + byte counts, never prompt or
  response bodies. Bodies only at DEBUG, off by default, and the config file says so
  in a comment. Logs go to the journal, which is world-readable-ish on some
  configs — treat DEBUG as "I am deliberately writing my prompts to disk".
- **Profile directory 700.** It holds live session cookies; anyone who can read it
  is logged into your ChatGPT.
- **No credentials in the app**, ever. Login is manual, in the browser.
- **Job results are in-memory only**, evicted 10 minutes after completion. Nothing
  persists to disk.

---

## 9. Logging

Structured, one line per event, at INFO:

```
[INFO] service.start port=8756
[INFO] browser.attached cdp=127.0.0.1:9333 chrome=150.0.7871
[INFO] chatgpt.authenticated=true
[INFO] job.received id=j_01H instruction_len=31 input_len=42
[INFO] job.submitted id=j_01H
[INFO] job.generation_started id=j_01H after_ms=1204
[INFO] job.generation_complete id=j_01H stream_ms=7208
[INFO] job.extracted id=j_01H chars=54
[INFO] job.done id=j_01H total_ms=8412
```

Timing each phase separately is what lets you answer "is ChatGPT slow, or is my
completion detector broken?" without a debugger. On any failure, log the phase
reached, plus a screenshot to `~/.cache/ai-bridge/failures/` — one image usually
identifies a redesign instantly. Keep the last 20, delete the rest.

---

## 10. Phase-by-phase implementation

Each phase ends in something runnable. Do not start the next until the current one
is demonstrably done.

### Phase 0 — Feasibility spike (2h) — **STOP-OR-GO**

Do this *before* writing any service code. It's ~60 lines in a throwaway script and
it answers the only question that matters.

1. `bin/start-chrome.sh` with the dedicated profile + `--remote-debugging-port=9333`.
2. Log into ChatGPT by hand in that window.
3. Script: `connect_over_cdp` → find/open the tab → `insert_text` a prompt →
   click send → poll the stop-button → dump the last assistant message.
4. Kill Chrome, rerun the script. **Confirm the session persisted** (no re-login).
5. Run it 10 times over an hour with varied prompts. Record: successes, Cloudflare
   challenges, malformed extractions.

**Go if ≥8/10 succeed and no hard block appears.** If you get challenged on every
request or hit a permanent block, stop — the rest of the plan is worthless and the
answer is the OpenAI API. Everything downstream is straightforward engineering;
this is the only genuine unknown.

*Done when: the spike script prints a correct ChatGPT answer twice across a browser
restart.*

### Phase 1 — Adapter (4h)

Promote the spike into `adapter.py` / `browser.py` / `selectors.py` / `extract.js`.

- `ChatGPTAdapter.ask()` implementing the full state machine of §1.3(A).
- The markdown extractor from §1.4 — verify on a code answer, a bulleted list, and
  a table.
- Every selector named in `selectors.py` with a `# verified 2026-09-09` comment.
- `health()` returning browser / tab / auth / dom-probe status.
- Reconnect-with-backoff in `browser.py`.

*Done when: a `python -m ai_bridge.adapter "explain this" "def f(): pass"` smoke
entrypoint returns clean markdown, and unplugging the browser mid-request raises
`BROWSER_DISCONNECTED` rather than hanging.*

### Phase 2 — Service (4h)

- `jobs.py`: `Job` dataclass, in-memory store with TTL eviction, an `asyncio.Queue`
  and a **single** worker task. One worker = one tab = no interleaving.
  `# ponytail: single worker; only revisit if you ever want parallel tabs, which
  you probably don't — see §1.5.`
- `api.py`: `/ask` (sync + `?async=1`), `/jobs/{id}` GET/DELETE, `/health`,
  `/health/dom`. Bearer auth dependency, Host-header check, size cap.
- `prompt.py` per §6.1, with the nonce-delimiter escape.
- `config.py`: TOML load, token generation on first run.
- Structured logging per §9.

*Done when: `curl -H "Authorization: Bearer $T" -d '{"instruction":"..."}'
localhost:8756/ask` returns the answer, and a second concurrent curl queues rather
than corrupting the first.*

### Phase 3 — CLI + tests (3h)

- `bin/ai-bridge`: stdlib-only (`urllib`, `argparse`) so it has zero install cost.
  `ai-bridge ask -i "Explain simply" --input-file f.py`, reads stdin when piped,
  prints only the response to stdout so it composes in shell pipelines. Errors and
  progress to stderr.
- `tests/test_api.py` against a **fake adapter** — the whole API surface, error
  mapping, auth rejection, size cap, queueing. Fast, no browser, runs in CI.
- `tests/test_prompt.py` — delimiter escaping when input contains the delimiter.
- `tests/test_live.py`, marked `@pytest.mark.live`, excluded by default: three real
  round-trips (prose, code, long input). This is your regression suite for
  ChatGPT redesigns; run it when something smells wrong.

*Done when: `echo 'def f(): pass' | ai-bridge ask -i "explain"` prints the
explanation and nothing else; `pytest` is green without a browser.*

### Phase 4 — Background service (2h)

- `ai-bridge-chrome.service` (user unit, `After=graphical-session.target`,
  `Restart=on-failure`) launching the profile.
- `ai-bridge.service`, `After=ai-bridge-chrome.service`, `Restart=always`,
  `RestartSec=5`.
- `systemctl --user enable --now`; `loginctl enable-linger` if you want it up
  without a desktop login — though note the browser needs a display, so on X11 it
  realistically lives with your session.
- README: install, first login, token retrieval, the four curl examples.

*Done when: reboot, log in, and `curl localhost:8756/health` reports
`authenticated: true` with no manual steps.*

### Phase 5 — Hardening (4h) — **done**

- [x] **SSE interception** (§1.3B) — done 2026-09-11, ahead of schedule, because the
  defect it fixes showed up on day one: ChatGPT's DOM no longer carries code-fence
  languages anywhere, so `` ```python `` came back as `` ``` ``. `ai_bridge/stream.py`
  parses the `POST /backend-api/f/conversation` event stream and is now the preferred
  extraction source, with `extract.js` as fallback. The log says which ran
  (`source=stream` / `source=dom`). A verbatim capture is checked in at
  `tests/fixtures/sse_code_block.txt`, so a wire-format change is a test-first fix.
  Completion detection deliberately stays DOM-driven -- it has proven reliable and
  swapping it buys nothing today.

- [x] **Rate-limit detection** — `RATE_LIMIT_MARKER` is checked when generation
  never starts, and raises `CHATGPT_RATE_LIMITED` (429). No reset time: ChatGPT
  does not always show one, and a wrong one is worse than none.
- [x] **Failure screenshots** (§9) — every `BridgeError` out of `ask()` drops a PNG
  in `~/.cache/ai-bridge/failures/`, named `<timestamp>-<CODE>-try<n>.png`, last 20
  kept. Best-effort: a screenshot failure never masks the real error.
- [x] **One retry after a reload** on `PROMPT_SUBMIT_FAILED` / `CHATGPT_DOM_CHANGED`.
  Attempt 2 forces a fresh temporary chat, which is both the reload and the guard
  against a half-landed prompt being submitted twice into one thread. Not retried
  past the caller's deadline, and never for codes a reload cannot fix.
- [x] **Weekly drift canary** — `bin/ai-bridge-check` runs 11 checks against the
  *running* deployment (CDP up, session valid, selectors present, auth/host/size
  rejections, a real round trip, `source=stream`, an async job) and exits non-zero.
  `systemd/ai-bridge-check.timer` runs it weekly and `notify-send`s on failure.

  Its first run earned its keep: it reported `send_button` drift on a page that was
  merely still hydrating. `_visible()` was a zero-wait snapshot, so `dom_probe` raced
  the render and called it a redesign — a canary that cries wolf weekly gets muted,
  which is the same as not having one. `_visible()` now takes an optional timeout and
  the probe passes `PROBE_TIMEOUT`; the hot polling loops keep the snapshot.

**Phase 5 is complete. The project is done unless Phase 6 or 7 acquires a real
reason to exist.**

#### What a full adversarial retest turned up (2026-09-16)

Four defects, all found by running the thing rather than reading it:

1. **The canary cried wolf.** `_visible()` was a zero-wait snapshot, so `dom_probe`
   raced page hydration and reported a redesign. Fixed with an optional timeout.
2. **A parked tab poisoned everything.** One failed `goto` left the tab on
   `chrome-error://` permanently: `BrowserManager.page()` cached it and never
   re-checked the URL, so `health` said "logged out", `dom_probe` said "redesign" and
   only `ask` recovered. Fixed once in `page()`, which every caller routes through.
3. **`DELETE /jobs/{id}` lied.** Cancelling a *running* job returned `status:
   "running"` — the worker flipped it to `cancelled` moments later. `cancel()` now
   waits for the transition it asked for (bounded by `CANCEL_GRACE`).
4. **A throttle was misdiagnosed.** ChatGPT's "Too many requests" modal overlays the
   composer, so the send click fails and it surfaced as `PROMPT_SUBMIT_FAILED` — a
   code that is *retried*, hammering the service that just asked us to slow down.
   `_submit` now asks the page what happened and raises `CHATGPT_RATE_LIMITED`.

Both screenshots and the retry earned their place in the same session: #4 was
diagnosed by opening `~/.cache/ai-bridge/failures/*.png` and reading the modal.

Still open, observed once under throttling and not reproducible since: an answer came
back with `source=dom` when the stream should have won. The DOM fallback returned the
correct text, so this cost nothing but a canary failure. Worth watching, not worth
speculating about — if it recurs off-throttle, `_read_stream` swallows its errors
silently and that is the first thing to instrument.

### Phase 6 — Second client (optional)

Whatever you actually wanted this for — VS Code extension, shell alias, Chrome
extension. If nothing pulls you here, the project is finished at Phase 5. Resist
building clients speculatively.

### Phase 7 — Streaming / sessions (optional)

Only once Phase 5's SSE interception exists, since it hands you tokens for free:
`GET /jobs/{id}/stream` as SSE. Client-managed conversation sessions (Option C) if
multi-turn ever becomes a real need.

---

## 11. Extensibility

The `ask/health/close` interface is the whole extensibility story. Adding an
OpenAI-API backend is one file plus a config line:

```toml
[backend]
kind = "chatgpt-web"   # or "openai-api"
```

`/ask` doesn't change; callers never learn which backend answered. Worth adding once
you have an API key, because it gives you a fallback the day ChatGPT web breaks and
a way to A/B whether the web session is still worth the hassle.

**Do not build a plugin system, an adapter registry, or a backend abstraction layer
for this.** Two backends is a dict lookup and a factory function of five lines.

---

## 12. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cloudflare blocks automation outright | Medium | **Fatal** | Phase 0 spike decides before you invest. Attach to real Chrome, headed, human solves challenges. |
| DOM redesign breaks selectors | **High** (weeks–months) | Medium | One selector file; `/health/dom` canary; live test suite; SSE path as DOM-independent fallback. |
| Account suspended for ToS violation | Low–Medium | High | Personal volume only; no stealth tooling; no captcha-solving; accept the risk knowingly. |
| Session expires at an inconvenient time | High | Low | Fast typed error + `/health`; manual re-login is 20 seconds. |
| Account message caps hit | Medium | Low | Typed `CHATGPT_RATE_LIMITED`; this is a personal-scale tool. |
| Local process abuses the API | Low | Medium | Bearer token, loopback bind, Host check. |
| Response extraction subtly wrong (mangled code) | Medium | Medium | Markdown extractor tested against code/list/table on day one. |
| Silent empty responses | Medium | High | Never return success on empty; the appear-then-vanish ordering + stability guard exists exactly for this. |

**The two failure modes that will actually cost you time:** silent empty responses
from racing the completion detector, and mysterious timeouts that are really a
redesign. Both are designed against above. Do not skip the appear-then-vanish
ordering or the DOM probe to save twenty lines.

---

## 13. Summary

- Phase 0 first, always. It's two hours and it's the only real unknown.
- ~500–700 lines total. Four dependencies. Ten source files.
- MVP after Phase 3 (~13h). Left-running quality after Phase 5 (~19h).
- The architecture's one job is containing the ChatGPT dependency in three files, so
  the inevitable redesign is a one-file fix rather than a rewrite.
