<p align="center">
  <img src="assets/icon.png" alt="MiniBurp logo" width="220" />
</p>

<h1 align="center">MiniBurp</h1>

<p align="center">
  An educational desktop intercepting proxy for the
  <a href="https://portswigger.net/web-security">PortSwigger Web Security Academy</a>.<br/>
  Same idea as Burp Suite or OWASP ZAP, scoped down to coursework.
</p>

---

> **Use only on systems you own or have explicit written permission to test.**
> Pointing an intercepting proxy at third-party services without authorisation
> is illegal under most computer-misuse laws.

## What it does

- Intercepts plain HTTP traffic on `127.0.0.1:8080`.
- Intercepts HTTPS via `CONNECT` + on-the-fly certificate generation using a
  locally-minted root CA. Import the CA into your OS trust store once
  (instructions below) and HTTPS labs load without warnings.
- Logs every request + response into a live, filterable history.
- Six Burp-style tabs: **Proxy**, **Intercept**, **Repeater**, **Intruder**,
  **Decoder**, **Scope**.
- Light and dark themes.

## Quickstart (macOS)

You need Python 3.11+ and Google Chrome (or Chromium / Brave / Edge).

```bash
git clone https://github.com/RyansCode-hue/MiniBurp.git
cd MiniBurp

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pyinstaller

./build.sh --run
```

`build.sh` always does a fully clean build (wipes `build/` + `dist/` and
passes `--clean` to PyInstaller) and prints the source SHA it bundled, so
you never end up launching stale bytecode after a code change. Add
`--run` to launch the resulting `dist/MiniBurp.app` immediately.

The bundle also self-checks at launch: if the `.app` lives next to its
source repo and the source has moved on since the build, MiniBurp shows a
**Rebuild and quit** prompt before opening the main window. Move the
`.app` out of the repo (e.g. to `/Applications`) or set
`MINIBURP_BUNDLE_CHECK=off` to silence it.

That's it. The bundle that lands at `dist/MiniBurp.app` is a standalone
macOS application — no Python required to run it after that point. Drag it
to `/Applications` if you want it persistent.

On first launch you'll see something like:

```
[ca] Generated new CA at ~/Library/Application Support/MiniBurp/ca/ca.crt
[proxy] listening on 127.0.0.1:8080
[browser] launched Google Chrome (pid 12345) through 127.0.0.1:8080
```

The MiniBurp window will open and a tracked Chrome window will appear next to
it. Use that Chrome window for your lab traffic — every request it makes
flows through the proxy. A separate Chrome window you open yourself is **not**
tracked.

If you want to see those `[ca] / [proxy] / [browser]` logs streamed live —
useful while debugging — launch the binary directly instead of via `open`:

```bash
./dist/MiniBurp.app/Contents/MacOS/MiniBurp
```

## CA setup

On first run, MiniBurp writes its root CA to a stable per-user location:

- **macOS:** `~/Library/Application Support/MiniBurp/ca/ca.crt`
- **Linux:** `$XDG_DATA_HOME/miniburp/ca/ca.crt` (or `~/.local/share/miniburp/ca/ca.crt`)
- **Windows:** `%APPDATA%\MiniBurp\ca\ca.crt`

Import the CA into the OS trust store once. The tracked Chrome instance and
any other browser on the machine will then accept MiniBurp's MITM certs
without warnings.

**macOS (one-liner, no sudo)**:

```bash
security add-trusted-cert -d -r trustRoot \
  -k "$HOME/Library/Keychains/login.keychain-db" \
  ~/Library/Application\ Support/MiniBurp/ca/ca.crt
```

You'll get a Touch ID / password prompt. After that, fully quit Chrome (⌘Q)
and relaunch MiniBurp.

**macOS (GUI alternative)** — open `~/Library/Application Support/MiniBurp/ca/ca.crt`
in Finder. Keychain Access opens. Drag the *MiniBurp Root CA* entry into the
**login** keychain, double-click it, expand **Trust**, set **When using this
certificate** to **Always Trust**, close the window, enter your password to
confirm.

**Firefox (any OS)** — Settings → Privacy & Security → Certificates → View
Certificates → Authorities → Import. Tick *Trust this CA to identify
websites*.

The CA private key never leaves your machine. Each user generates their own
on first run; nothing in this repo carries CA material.

## Tabs

| Tab | What it does |
| --- | --- |
| **Proxy** | Filterable history with full-text search across URL, raw request, raw response, and the decoded response body (so `csrf-token` matches gzipped pages). Optional regex. Click any row to inspect the request / response, syntax-highlighted, with a hex toggle, a **Render** view for HTML bodies, and Cmd-F find. Where your input echoes back in the response is **highlighted** with an *N reflected* badge. **Right-click** a row → *Send to Repeater* / *Send to Intruder*, *Copy URL*, *Copy request*, *Copy as curl*; **double-click** sends to Repeater. |
| **Intercept** | Pause requests, edit raw bytes (or as a **Params** key/value table), forward or drop. *Auto Content-Length* keeps an edited body's length honest. *Send to Repeater / Intruder* carries your edits across. Forward `⌘↵` / Drop `⌘⌫`. Only **in-scope** requests are held. |
| **Repeater** | Multi-tab editable request + response. Send with `⌘↵`. Step through every past send (`◀ n/m ▶`) and **Diff** their response bodies. Optional *Update CL* recomputes Content-Length after a body edit. Optional *Follow redirects* with standard HTTP semantics (301/302/303 → GET; 307/308 preserve method + body) and a minimal cookie jar so multi-hop login flows reach the final page; the hop chain is shown in the status bar. |
| **Intruder** | 4 attack modes — Sniper, Battering-Ram, Pitchfork (lockstep lists), Cluster Bomb (cartesian product). *Load list…* fills a payload panel from the built-in library. Start with `⌘↵`. Streams results into a live, sortable table. |
| **Decoder** | Base64 / URL / hex / HTML-entity encode + decode. MD5 / SHA-1 / SHA-256 hashes. Chain output back to input. The same ops are available by **right-clicking a selection** in any request editor. |
| **Match/Replace** | Standing find/replace (literal or regex) applied to every in-scope request before forwarding — inject a header, rewrite a cookie, swap a path/parameter. |
| **Scope** | Glob allowlist (`*.web-security-academy.net`) for filtering history and intercept. |

The toolbar also has **Save session… / Load session…** to write history to a
JSON file and reload it (raw bytes base64-encoded). Saved files contain full
request/response bodies — treat them as sensitive and don't commit them.

## Themes

Toggle between dark and light from the right-hand side of the toolbar
(*Theme: Dark* / *Theme: Light*). Your choice persists across launches.

## Project layout

```
MiniBurp/
├── run.py                      # entry point (desktop default, --web for Flask)
├── miniburp.spec               # PyInstaller config for the macOS .app
├── requirements.txt
├── assets/
│   ├── icon.png                # source logo (1024×1024)
│   └── MiniBurp.icns           # generated macOS app icon
├── miniburp/
│   ├── ca.py                   # root CA + per-host cert minting
│   ├── http_io.py              # raw HTTP/1.1 read/write helpers
│   ├── proxy.py                # socket server + CONNECT MITM
│   ├── sender.py               # send raw bytes, return raw response
│   ├── storage.py              # thread-safe in-memory transaction store
│   ├── intercept.py            # pause / edit / forward / drop coordinator
│   ├── scope.py                # host allowlist (fnmatch globs)
│   ├── rules.py                # Match & Replace request engine
│   ├── params.py               # request query/cookie/body parse + serialize
│   ├── reflect.py              # extract request inputs to highlight reflections
│   ├── payloads.py             # bundled, described payload library
│   ├── curl.py                 # render a request as a curl command
│   ├── session_io.py           # save / restore history to JSON
│   ├── bodydecode.py           # gzip / deflate / brotli body decoding
│   ├── browser.py              # Chromium auto-launch through the proxy
│   ├── decoder.py              # Base64, URL, hex, hash ops
│   ├── webui.py                # legacy Flask UI (--web mode)
│   ├── templates/              # Jinja2 templates for Flask
│   ├── static/style.css
│   └── qt/                     # PySide6 desktop UI
│       ├── app.py
│       ├── main_window.py
│       ├── signals.py
│       ├── syntax.py
│       ├── widgets/
│       └── tabs/
├── LICENSE
└── README.md
```

The CA + per-host leaf certs live outside the repo at the per-user path
described above. They are machine-local secrets and must never be committed.

## Known limitations

- Forces `Connection: close` on every upstream hop, so throughput is modest.
- No WebSocket support; WS frames after an HTTP Upgrade are not intercepted.
- CA key is stored unencrypted on disk. Fine for a lab, not for shipping.
- Upstream TLS validation is disabled (`verify_mode = CERT_NONE`) so labs
  with self-signed certs work.
- In-memory storage only — history vanishes on restart.

## Development

If you'd rather run from source without bundling — handy while iterating on
the UI — skip the PyInstaller step:

```bash
source .venv/bin/activate
python run.py                         # desktop app + tracked Chrome
python run.py --url LAB_URL           # open Chrome straight to a lab URL
python run.py --no-browser            # desktop only
python run.py --web                   # legacy Flask UI at http://127.0.0.1:8888/
```

### Double-click launcher that updates with `git pull` (recommended for devs)

`./build.sh` re-freezes the app with PyInstaller every time, and each rebuild
gets a fresh code-signing identity — so macOS re-prompts for permissions
(Accessibility, firewall, …) on every build. If you're working from the repo and
just want a double-click app for daily lab work, install the **launcher** once
instead of building.

**One-time setup** (from a fresh clone):

```bash
git clone https://github.com/RyansCode-hue/MiniBurp.git
cd MiniBurp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
tools/make_launcher.sh            # creates ~/Applications/MiniBurp.app
```

Then launch it like any Mac app — Spotlight (⌘-Space → "MiniBurp"), the
`~/Applications` folder, or `open ~/Applications/MiniBurp.app`. Drag it to the
Dock once for one-click access.

The launcher is a tiny wrapper `MiniBurp.app` that runs your live source
(`.venv/bin/python run.py`). It has no frozen bootloader and no argv-emulation,
so it never asks for Accessibility, and its bytes never change, so its identity —
and every permission you grant it — is stable forever.

**Updating from then on is just `git pull`** — no rebuild; the app always runs
the checked-out source, so reopening it picks up the new code. Re-run
`tools/make_launcher.sh` only if you move the repo or it tells you to (e.g. a
changed launcher script). Run `pip install -r requirements.txt` after a pull that
changes dependencies.

> The launcher bakes in your repo + venv path, so it's per-machine — each
> developer runs `tools/make_launcher.sh` once on their own clone. It is **not**
> a shareable binary. To hand MiniBurp to someone who won't clone the repo, use
> `./build.sh` to cut a standalone, self-contained `.app`.

Useful environment variables:

- `MINIBURP_CHROME=/path/to/chrome` — override browser auto-detection
- `MINIBURP_CA_DIR=/some/path` — override the CA directory
- `python run.py --proxy-port 9080` — pick a different proxy port

## License

MIT — see [LICENSE](LICENSE).
