# MiniBurp improvements batch — Description

**Branch:** `miniburp-improvements` (based on `suppress-chrome-chatter`, which
carries prerequisites: `recompute_content_length`, theme-aware syntax, parallel
Intruder, follow-redirects).
**Ticket:** none (feature branch, per developer's choice).

## What this is

A batch of 17 improvements derived from analysing what MiniBurp needs to comfortably
solve PortSwigger Web Security Academy labs (the trigger was *File path traversal,
simple case*). Each is a self-contained commit; the work is one PR.

MiniBurp could already solve the path-traversal lab (intercept the image request,
edit `filename` to `../../../etc/passwd`, forward, read the response). These changes
remove friction, add Burp-parity primitives, and fix silent-failure footguns.

## Changes (one commit each)

### Robustness (silent-failure fixes)
1. **Chunked → Content-Length on forward + tolerant status parsing.** `read_request`
   decodes chunked bodies and the forward path strips `Transfer-Encoding`, leaving
   no length declaration — chunked POSTs were forwarded as zero-length. `ensure_content_length`
   fixes it. `parse_status_code` replaces `int(resp_start.split(...))` so a malformed
   upstream status line no longer tears down the CONNECT keep-alive loop.
2. **Validate hand-edited intercept requests.** A malformed edited request line / missing
   Host used to raise `ValueError` deep in the worker and drop the connection silently.
   `validate_request_bytes` now returns a 400 explaining the problem and records it on the tx.
3. **UTF-8 fallback in the editor encoder.** `bytes_value()` and the Intruder send path
   used `errors="replace"`, turning pasted/multibyte payloads into `?`. `encode_http_text`
   falls back to UTF-8; also fixes a latent Intruder worker crash on non-Latin-1 payloads.

### Quick wins
4. **`curl.to_curl`** — render a captured request as a copy-pasteable curl command.
5. **Proxy history right-click menu** — Send to Repeater/Intruder, Copy URL/request/curl;
   double-click → Repeater. (README documented this; it didn't exist.)
6. **Keyboard shortcuts** — Repeater Send / Intruder Start `⌘↵`; Intercept Forward `⌘↵`,
   Drop `⌘⌫`.
7. **Send to Repeater/Intruder from a held Intercept request**, carrying the edited bytes.
8. **Auto Content-Length on edited bodies** — Intercept (default on), Repeater (opt-in).
16. **Save / Load session** — `session_io` serialises history to JSON; `Store.load`
    repopulates via pub/sub; two toolbar actions.
17. **Inline hint strips** per major tab + a just-in-time "captured but not held — out of
    scope?" status nudge.

### Bigger features
9. **Repeater per-session response history** (`◀ n/m ▶`) + **Diff** of decoded bodies.
10. **Match & Replace** — `rules` engine (request line/headers/body, literal/regex,
    composing) wired into both proxy forward paths, scope-gated, CL kept honest; new
    *Match/Replace* tab. (Response rules deferred: response bodies are often gzipped, so
    a raw replace would silently miss.)
11. **Request parameter grid** — `params` parse/serialize + a *Params* table in the editors;
    editing a value rewrites the raw request (`/` kept literal so traversal payloads survive;
    JSON bodies untouched).
12. **Reflection highlighting** — `reflect` extracts request inputs; response viewers
    wash-highlight where they echo and show an *N reflected* badge.
13. **Render response view** — third toggle (QTextBrowser, no network) for HTML bodies.
14. **Encode/Decode selection** context menu in the request editors (reuses `decoder`).
15. **Bundled payload library** — `payloads` (traversal, XSS, SQLi, creds, cmd-injection),
    surfaced as Intruder *Load list…* and an editor *Insert payload* menu.

## Files

**New headless modules:** `miniburp/curl.py`, `rules.py`, `params.py`, `reflect.py`,
`payloads.py`, `session_io.py`.
**New Qt widgets:** `miniburp/qt/widgets/hint_strip.py`.
**New Qt tab:** `miniburp/qt/tabs/rules_tab.py`.
**Modified headless:** `http_io.py` (encode/validate/CL/status helpers), `proxy.py`
(rules + robustness wiring), `storage.py` (`Store.load`).
**Modified Qt:** `qt/widgets/http_viewer.py` (UTF-8 encode, context menu, render mode,
params panel, reflection needles), `qt/syntax.py` (reflection pass),
`qt/main_window.py` (Match/Replace tab, session actions, intercept→repeater/intruder,
scope nudge), `qt/tabs/{proxy,repeater,intruder,intercept}_tab.py`.
**Tests:** `tests/test_http_io.py`, `test_curl.py`, `test_rules.py`, `test_params.py`,
`test_reflect.py`, `test_payloads.py`, `test_session_io.py`, `test_qt_smoke.py`.
**Docs:** `README.md` (Tabs table + layout), `tasks/todo.md`.

## Endpoints

None. MiniBurp is a desktop app; no HTTP endpoints are introduced or modified. The proxy
continues to forward the user's own browser traffic — Match & Replace and the param grid
modify *outgoing* requests the user is already authorised to send.

## Smoke tests

Setup: `python run.py --url https://<lab-id>.web-security-academy.net`; trust the CA once;
add `*.web-security-academy.net` to Scope.

- [ ] Path-traversal via **Params grid** — open the product image request, Repeater/Intercept →
      *Params* → set `filename` to `../../../etc/passwd` → the raw request shows the literal
      slashes (not `%2F`) → Send/Forward → response contains `root:x:0:0`.
- [ ] Path-traversal via **Intercept raw edit** — Intercept ON, load image, edit request line,
      Forward → response shows `/etc/passwd`.
- [ ] **Right-click history** → Copy as curl produces a runnable command; double-click opens Repeater.
- [ ] **Repeater history/diff** — send twice with different payloads → `◀ ▶` steps through;
      *Diff* shows the body delta.
- [ ] **Match & Replace** — add a `req-header` rule injecting `X-Forwarded-For: 127.0.0.1` →
      every in-scope request carries it; an out-of-scope host does not.
- [ ] **Auto Content-Length** — edit a POST body in Intercept with the box checked → server
      receives the full body (no truncation/hang).
- [ ] **Reflection** — send a marker in a query value to a reflecting endpoint → it's highlighted
      with an *N reflected* badge.
- [ ] **Render** — a `text/html` response shows the *Render* toggle; it displays the page and
      makes no network request.
- [ ] **Save/Load session** — Save, Clear history, Load → the requests come back.
- [ ] **Robustness** — delete the HTTP version from an intercepted request line and Forward →
      a 400 "malformed" is returned instead of a dead connection.

## Verification

`pytest` — 184 passed, 1 skipped. `flake8 --select=E9,F63,F7,F82` — clean. Headless
(offscreen) MainWindow smoke builds every tab under both themes.

A self-review of the full diff caught and fixed 4 bugs (final commit), each with a
regression test:
- Match & Replace rule that *empties* a body left a stale Content-Length (would hang
  upstream) — now recomputed to 0.
- `params` request-line rebuild corrupted non-canonical (double-space) lines and dropped
  scheme+authority on absolute-form targets — now split on whitespace runs and preserve
  scheme/host.
- `Transaction.duration_ms` crashed on a session file with a null `started_at` — guarded.

## Promotion target

local → dev/UAT. Evidence: tests passing, smoke checklist above, this doc + the security
considerations doc.
