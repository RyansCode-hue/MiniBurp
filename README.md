# MiniBurp

A small educational web proxy that mimics a slice of Burp Suite / OWASP ZAP.
Intended for coursework and personal lab use only. Do not point it at systems
you do not own or have explicit permission to test.

## What it does

- Intercepts plain HTTP traffic on `127.0.0.1:8080`.
- Intercepts HTTPS via `CONNECT` + on-the-fly certificate generation using a
  locally-minted root CA (`ca/ca.crt`). The tracked Chrome instance trusts it
  automatically via SPKI pinning.
- Logs every request + response into an in-memory store with live updates.
- Ships two UIs:
  - **Native desktop app** (PySide6) — default. Dark theme, tabs across the
    top: Proxy / Intercept / Repeater / Intruder / Decoder / Scope.
  - **Flask web UI** — optional fallback (`python run.py --web`).

## Features

- **Proxy tab** – filterable history (method / host / path / status / scope),
  syntax-highlighted request + response viewers, text/hex toggle, Cmd-F find.
- **Intercept tab** – pause, edit raw bytes, forward or drop in-scope requests.
- **Repeater tab** – one editable request + response per sub-tab, send on demand.
- **Intruder tab** – 4 attack modes: Sniper, Battering-Ram, Pitchfork (lockstep
  lists), Cluster Bomb (cartesian product). Streams results into a live table.
- **Decoder tab** – Base64 / URL / hex / HTML-entity encode+decode, MD5 / SHA-1
  / SHA-256 hashes; chain results from output back to input.
- **Scope tab** – glob allowlist (`*.web-security-academy.net`).
- **Gzip / deflate / brotli** response decoding.

## Install (run from source)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run from source

```bash
python run.py                              # desktop app + tracked Chrome
python run.py --url LAB_URL                # desktop + open Chrome to LAB_URL
python run.py --no-browser                 # desktop only
python run.py --web                        # legacy Flask UI
python run.py --web --url LAB_URL
```

On first run you'll see:

```
[ca] Generated new CA at ~/Library/Application Support/MiniBurp/ca/ca.crt
[proxy] listening on 127.0.0.1:8080
[browser] launched Google Chrome (pid 12345) through 127.0.0.1:8080
```

Use the Chrome window MiniBurp launched for your lab traffic — every request
it makes flows through the proxy. A separate Chrome window you open yourself
will NOT be tracked.

## Build a standalone macOS `.app`

The PySide6 desktop UI can be bundled into a double-clickable `MiniBurp.app`
using PyInstaller. This is what you ship for grading / handoff.

```bash
source .venv/bin/activate
pip install pyinstaller
pyinstaller miniburp.spec --clean --noconfirm
```

The bundle lands at `./dist/MiniBurp.app`. Two ways to launch it:

```bash
# Quiet launch (double-clickable equivalent). Stdout/stderr go to Console.app.
open dist/MiniBurp.app

# Verbose launch — best for first runs and troubleshooting. Stderr streams
# to your terminal so you see the [ca] / [proxy] / [browser] logs live.
./dist/MiniBurp.app/Contents/MacOS/MiniBurp
```

The bundled app uses the same `~/Library/Application Support/MiniBurp/ca/`
location as running from source, so a CA you've already imported into
Keychain keeps working across rebuilds.

### Options

- `python run.py --no-browser` — skip auto-launch. Useful if you prefer to
  configure Firefox/Safari/etc. manually.
- `MINIBURP_CHROME=/path/to/chrome python run.py` — override browser
  auto-detection.
- `python run.py --proxy-port 9080` — pick a different proxy port.
- `python run.py --web --ui-port 9888` — pick a different Flask port.
- `MINIBURP_CA_DIR=/some/path python run.py` — override the CA directory.

## CA setup

On first run, MiniBurp writes its root CA to a stable per-user location:

- **macOS:** `~/Library/Application Support/MiniBurp/ca/ca.crt`
- **Linux:** `$XDG_DATA_HOME/miniburp/ca/ca.crt` (or `~/.local/share/miniburp/ca/ca.crt`)
- **Windows:** `%APPDATA%\MiniBurp\ca\ca.crt`

For the tracked Chrome instance MiniBurp launches, you don't need to import
anything — it's wired to accept this specific CA via SPKI pinning.

For other browsers (Safari, a normal Chrome window, Firefox), import the CA
into the OS trust store once:

**macOS** — open `~/Library/Application Support/MiniBurp/ca/ca.crt` in Finder.
Keychain Access opens. Drag the "MiniBurp Root CA" entry into the **login**
(or **System**) keychain, double-click it, expand **Trust**, and set
**When using this certificate** to **Always Trust**. Close the window —
macOS will prompt for your password to save.

**Firefox (any OS)** — Settings → Privacy & Security → Certificates →
View Certificates → Authorities → Import. Tick "Trust this CA to identify
websites."

### Manual browser setup (only with `--no-browser`)

Set your browser's HTTP and HTTPS proxy to `127.0.0.1:8080`, then import the
CA per the section above.

## Project layout

```
miniburp/
├── run.py                      # entry point (desktop default, --web for Flask)
├── requirements.txt
├── miniburp/
│   ├── ca.py                   # root CA + per-host cert minting
│   ├── http_io.py              # raw HTTP/1.1 read/write helpers
│   ├── proxy.py                # socket server + CONNECT MITM
│   ├── sender.py               # send raw bytes, return raw response
│   ├── storage.py              # thread-safe in-memory transaction store
│   ├── intercept.py            # pause / edit / forward / drop coordinator
│   ├── scope.py                # host allowlist (fnmatch globs)
│   ├── bodydecode.py           # gzip / deflate / brotli body decoding
│   ├── browser.py              # Chromium auto-launch with SPKI pinning
│   ├── decoder.py              # Base64, URL, hex, hash ops
│   ├── webui.py                # legacy Flask UI (`--web` mode)
│   ├── templates/              # Jinja2 templates for Flask
│   ├── static/style.css
│   └── qt/                     # PySide6 desktop UI
│       ├── app.py              # QApplication entry + proxy thread + Chrome
│       ├── main_window.py      # QMainWindow + tabs + toolbar
│       ├── signals.py          # STORE pub/sub → Qt signal bridge
│       ├── syntax.py           # HTTP/JSON syntax highlighter
│       ├── widgets/
│       │   ├── http_viewer.py  # viewer + editor with hex toggle + find
│       │   └── find_bar.py     # Cmd-F reusable bar
│       └── tabs/
│           ├── proxy_tab.py
│           ├── intercept_tab.py
│           ├── repeater_tab.py
│           ├── intruder_tab.py
│           ├── decoder_tab.py
│           └── scope_tab.py
├── miniburp.spec               # PyInstaller config for the macOS .app
├── LICENSE                     # MIT
└── README.md
```

The CA + per-host leaf certs live outside the repo at the per-user path
described in the *CA setup* section. They are machine-local secrets and must
never be committed.

## Ethics & scope

Only use MiniBurp against:

- PortSwigger Web Security Academy labs.
- Your own applications running locally.
- Systems where you have explicit, written authorisation.

Running an intercepting proxy against third-party services without permission
is likely illegal under most computer misuse laws.

## Known limitations

- Forces `Connection: close` on every upstream hop, so throughput is modest.
- No WebSocket support; WS frames after an HTTP Upgrade are not intercepted.
- CA key is stored unencrypted on disk. Fine for a lab, not for shipping.
- Upstream TLS validation is disabled (`verify_mode = CERT_NONE`) so labs with
  self-signed certs work.
- In-memory storage only — history vanishes on restart.

## License

MIT — see [LICENSE](LICENSE).
