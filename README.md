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

pyinstaller miniburp.spec --clean --noconfirm
open dist/MiniBurp.app
```

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
| **Proxy** | Filterable history. Click any row to inspect the request / response, syntax-highlighted, with a hex toggle and Cmd-F find. Right-click → *Send to Repeater* / *Send to Intruder*. |
| **Intercept** | Pause requests, edit raw bytes, forward or drop. Toggle from the toolbar. |
| **Repeater** | Multi-tab editable request + response. Send on demand. |
| **Intruder** | 4 attack modes — Sniper, Battering-Ram, Pitchfork (lockstep lists), Cluster Bomb (cartesian product). Streams results into a live table. |
| **Decoder** | Base64 / URL / hex / HTML-entity encode + decode. MD5 / SHA-1 / SHA-256 hashes. Chain output back to input. |
| **Scope** | Glob allowlist (`*.web-security-academy.net`) for filtering history and intercept. |

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

Useful environment variables:

- `MINIBURP_CHROME=/path/to/chrome` — override browser auto-detection
- `MINIBURP_CA_DIR=/some/path` — override the CA directory
- `python run.py --proxy-port 9080` — pick a different proxy port

## License

MIT — see [LICENSE](LICENSE).
