# MiniBurp improvements batch — Security Considerations

MiniBurp is a local, single-user, educational intercepting proxy run against the
developer's own browser traffic and PortSwigger lab targets. There is no server,
no multi-tenant data, and no authentication boundary inside the app. The relevant
profiles are **#1 UI Feature** and **#7 Third-Party Integration** (none added).
Crafting and modifying requests is the *intended* function of an attack proxy, so
the analysis below focuses on data exposure, the lab-only invariants, and not
introducing network-facing surface.

## 1. UI Feature

- **Sensitive data rendered in the UI?** The viewers show full request/response
  bytes — that's the point of a proxy. No new privilege boundary; everything shown
  was already captured locally. The new *reflection highlighting* and *curl* render
  only data already present in the transaction.
- **Permission/grant checks?** N/A — single-user desktop app.
- **Input sanitised before display? XSS?** The new **Render** view (`http_viewer`)
  loads a captured HTML body into a `QTextBrowser`. `QTextBrowser` does **not** run
  JavaScript and does **not** fetch remote resources on its own, and we set
  `setOpenExternalLinks(False)` / `setOpenLinks(False)`. So rendering a malicious lab
  page cannot execute script or phone home past the proxy — the worst case is static
  rich-text rendering. (We deliberately chose `QTextBrowser` over `QWebEngineView`
  precisely to avoid a live JS engine and network stack.)
- **New environment-specific values / API keys client-side?** None.

## Data handling

- **Save / Load session (`session_io`)** writes full request/response bodies —
  which may include credentials, cookies, and CSRF tokens — to a user-chosen JSON
  file (base64 for the byte fields). This is flagged in the module docstring, the
  README, and the file is **not** committed. Load only parses JSON into the in-memory
  store; it executes nothing and opens no network connection. Malformed input raises
  `ValueError` and surfaces a dialog rather than crashing.
- **Match & Replace (`rules`) and the Params grid (`params`)** modify *outgoing*
  requests the user is already authorised to send. Match & Replace is **scope-gated**
  (`SCOPE.in_scope(host)` in `proxy._apply_request_rules`), preserving the lab-only
  invariant: rules never touch out-of-scope/background traffic. An invalid regex is a
  no-op (never crashes the proxy worker).
- **Payload library (`payloads`)** is static local data (traversal / XSS / SQLi /
  credential / command-injection strings). It is offensive content by design, scoped
  to a local testing tool; it is never sent automatically — the user must choose a
  list and start an attack.

## Lab-only invariants (preserved)

- The noise filter and scope gating are unchanged. Match & Replace runs only for
  in-scope hosts, so it cannot leak into or mutate Chrome background chatter.
- Upstream TLS verification remains `CERT_NONE` (intentional, for labs). No change.
- The headless/Qt boundary holds: every new headless module (`curl`, `rules`,
  `params`, `reflect`, `payloads`, `session_io`) is Qt-free; `proxy.py` imports only
  headless modules.

## Robustness (defensive hardening added)

- `validate_request_bytes` makes a malformed hand-edit return a **400** instead of an
  uncaught `ValueError` that silently dropped the connection.
- `parse_status_code` + a broadened CONNECT-loop `except` stop one malformed upstream
  response from tearing down a keep-alive connection (denial of the rest of the
  session's traffic).
- `encode_http_text` stops silent `?`-corruption of non-Latin-1 payloads, and removes
  a latent `UnicodeEncodeError` that killed the Intruder worker thread.

## 7. Third-Party Integration / Dependency

- **No new dependencies.** Everything uses the standard library plus the already-present
  PySide6 (`QTextBrowser` ships with QtWidgets). No new package to vet.

## 8. Infrastructure / Config

- No Docker/CI/secret/port changes. No new network exposure: the proxy still listens
  only on `127.0.0.1` and the new features add no listeners or outbound calls (the
  render view explicitly cannot fetch remote resources).

## "What did I miss?"

- **Match & Replace altering history fidelity.** Applied rules rewrite `raw_request`
  before `STORE.add`, so history shows what was actually sent; `req_headers`/`req_body`
  dicts still reflect the pre-rule capture, so *Copy as curl* on a rule-injected header
  would omit it. Acceptable for v1 (standing rules are user-defined and known), noted
  for follow-up.
- **Session files are unencrypted** like the existing CA key — fine for a lab, called
  out so no one treats a saved session as safe to share casually.
- **`QTextBrowser` rich-text parser** is the only place untrusted HTML is parsed; it has
  no JS/network, so the residual risk is a malformed-document parser bug in Qt, which is
  low and not reachable over the network.
