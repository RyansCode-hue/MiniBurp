"""
Flask web UI for MiniBurp.

Routes:
* GET  /                  -> History table (live via SSE)
* POST /clear             -> Wipe history
* GET  /events            -> SSE stream of add/update events
* GET  /request/<id>      -> Raw request + response view (with decoded body)
* GET  /repeater/<id>     -> Repeater form pre-filled from transaction
* POST /repeater/<id>     -> Send edited request, show response
* GET  /intruder/<id>     -> Intruder setup (supports 4 attack modes)
* POST /intruder/<id>     -> Run attack, show results
* GET  /decoder           -> Encoding/hashing utility (Base64, URL, hex, MD5, etc.)
* POST /decoder           -> Run op
* GET|POST /scope         -> Manage scope patterns
* POST /intercept/toggle  -> Enable or disable intercept
* POST /intercept/<id>/forward -> Forward a held request
* POST /intercept/<id>/drop    -> Drop a held request
* POST /intercept/forward-all  -> Forward everything currently held
* GET  /intercept         -> Held-requests queue UI
* POST /launch            -> Launch a tracked Chromium
* GET  /ca.crt            -> Download MiniBurp CA
"""
from __future__ import annotations

import itertools
import json
import queue
import re
import time
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)

from . import bodydecode
from . import browser as browser_module
from . import ca as ca_module
from . import decoder as decoder_module
from .intercept import INTERCEPT
from .scope import SCOPE
from .sender import send_raw
from .storage import STORE


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(proxy_host: str = "127.0.0.1", proxy_port: int = 8080) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )
    app.secret_key = "miniburp-dev-key"
    app.config["PROXY_HOST"] = proxy_host
    app.config["PROXY_PORT"] = proxy_port

    # ----- helpers ------------------------------------------------------
    def _decode(b: bytes) -> str:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("iso-8859-1", errors="replace")

    app.jinja_env.filters["decode"] = _decode

    # ----- history + SSE ------------------------------------------------
    @app.route("/")
    def history():
        scope_mode = request.args.get("scope", "in")  # "in" | "all"
        txs = list(reversed(STORE.all()))
        if scope_mode == "in":
            txs = [tx for tx in txs if SCOPE.in_scope(tx.host)]
        return render_template(
            "history.html",
            txs=txs,
            scope_mode=scope_mode,
            scope_patterns=SCOPE.patterns(),
            intercept_on=INTERCEPT.is_enabled(),
            held_count=len(INTERCEPT.held_ids()),
        )

    @app.route("/events")
    def events():
        def stream():
            q = STORE.subscribe()
            try:
                # Heartbeat so proxies don't drop the connection.
                yield "event: hello\ndata: {}\n\n"
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    # Respect scope for 'add'/'update' by including scope flag.
                    tx = ev.get("tx") or {}
                    host = tx.get("host", "")
                    ev_out = dict(ev)
                    ev_out["in_scope"] = SCOPE.in_scope(host) if host else True
                    yield f"data: {json.dumps(ev_out)}\n\n"
            finally:
                STORE.unsubscribe(q)
        return Response(stream_with_context(stream()), mimetype="text/event-stream")

    @app.route("/clear", methods=["POST"])
    def clear():
        STORE.clear()
        return redirect(url_for("history"))

    @app.route("/request/<int:tx_id>")
    def request_detail(tx_id: int):
        tx = STORE.get(tx_id)
        if not tx:
            abort(404)
        decoded_body, note = bodydecode.decode_body(tx.resp_body, tx.resp_headers)
        # Reconstruct a "raw response" that shows decoded body if we decoded it.
        if note == "" and decoded_body is not tx.resp_body and tx.raw_response:
            split = tx.raw_response.find(b"\r\n\r\n")
            if split != -1:
                raw_for_display = tx.raw_response[: split + 4] + decoded_body
            else:
                raw_for_display = tx.raw_response
        else:
            raw_for_display = tx.raw_response
        return render_template(
            "request_detail.html",
            tx=tx,
            raw_response_display=raw_for_display,
            decode_note=note,
        )

    # ----- scope --------------------------------------------------------
    @app.route("/scope", methods=["GET", "POST"])
    def scope_page():
        if request.method == "POST":
            text = request.form.get("patterns", "")
            SCOPE.set_patterns(text.splitlines())
            flash("Scope updated.", "ok")
            return redirect(url_for("scope_page"))
        return render_template(
            "scope.html",
            patterns_text="\n".join(SCOPE.patterns()),
        )

    # ----- intercept ----------------------------------------------------
    @app.route("/intercept")
    def intercept_page():
        held = []
        for tid in INTERCEPT.held_ids():
            tx = STORE.get(tid)
            if not tx:
                continue
            held.append({
                "id": tid,
                "url": tx.url,
                "method": tx.method,
                "raw": _decode(INTERCEPT.held_bytes(tid) or b""),
            })
        return render_template(
            "intercept.html",
            enabled=INTERCEPT.is_enabled(),
            held=held,
        )

    @app.route("/intercept/toggle", methods=["POST"])
    def intercept_toggle():
        INTERCEPT.set_enabled(not INTERCEPT.is_enabled())
        flash(f"Intercept { 'ON' if INTERCEPT.is_enabled() else 'OFF' }.", "ok")
        return redirect(request.referrer or url_for("intercept_page"))

    @app.route("/intercept/<int:tx_id>/forward", methods=["POST"])
    def intercept_forward(tx_id: int):
        new_raw_text = request.form.get("raw_request")
        new_bytes = None
        if new_raw_text is not None:
            new_bytes = new_raw_text.replace("\r\n", "\n").replace("\n", "\r\n").encode("iso-8859-1")
        ok = INTERCEPT.forward(tx_id, new_bytes)
        if not ok:
            flash(f"#{tx_id} is no longer held.", "err")
        return redirect(url_for("intercept_page"))

    @app.route("/intercept/<int:tx_id>/drop", methods=["POST"])
    def intercept_drop(tx_id: int):
        INTERCEPT.drop(tx_id)
        return redirect(url_for("intercept_page"))

    @app.route("/intercept/forward-all", methods=["POST"])
    def intercept_forward_all():
        n = INTERCEPT.forward_all()
        flash(f"Forwarded {n} held request(s).", "ok")
        return redirect(url_for("intercept_page"))

    # ----- browser + CA ------------------------------------------------
    @app.route("/launch", methods=["POST"])
    def launch_browser():
        try:
            browser_module.launch(
                proxy_host=app.config["PROXY_HOST"],
                proxy_port=app.config["PROXY_PORT"],
            )
            flash("Browser launched.", "ok")
        except RuntimeError as e:
            flash(str(e), "err")
        except Exception as e:
            flash(f"Launch failed: {e}", "err")
        return redirect(request.referrer or url_for("history"))

    @app.route("/ca.crt")
    def download_ca():
        cert_bytes = ca_module.CA_CERT_PATH.read_bytes()
        return Response(
            cert_bytes,
            mimetype="application/x-x509-ca-cert",
            headers={"Content-Disposition": "attachment; filename=miniburp-ca.crt"},
        )

    # ----- decoder ------------------------------------------------------
    @app.route("/decoder", methods=["GET", "POST"])
    def decoder():
        if request.method == "GET":
            return render_template(
                "decoder.html",
                ops=decoder_module.OPS,
                op="base64-decode",
                text="",
                result="",
                error="",
            )
        op = request.form.get("op", "base64-decode")
        text = request.form.get("text", "")
        result, error = decoder_module.run(op, text)
        return render_template(
            "decoder.html",
            ops=decoder_module.OPS,
            op=op,
            text=text,
            result=result,
            error=error,
        )

    # ----- repeater -----------------------------------------------------
    @app.route("/repeater/<int:tx_id>", methods=["GET", "POST"])
    def repeater(tx_id: int):
        tx = STORE.get(tx_id)
        if not tx:
            abort(404)

        if request.method == "GET":
            return render_template(
                "repeater.html",
                tx=tx,
                raw_request=_decode(tx.raw_request),
                response=None,
            )

        raw_text = request.form.get("raw_request", "")
        host = request.form.get("host") or tx.host
        port = int(request.form.get("port") or tx.port)
        scheme = request.form.get("scheme") or tx.scheme
        raw_bytes = raw_text.replace("\r\n", "\n").replace("\n", "\r\n").encode("iso-8859-1")
        result = send_raw(host, port, scheme, raw_bytes)
        # Decode body for display
        if not result.get("error"):
            decoded, note = bodydecode.decode_body(result.get("body", b""), result.get("headers", {}))
            result["decoded_body"] = decoded
            result["decode_note"] = note
        return render_template(
            "repeater.html",
            tx=tx,
            raw_request=raw_text,
            response=result,
            target_host=host,
            target_port=port,
            target_scheme=scheme,
        )

    # ----- intruder -----------------------------------------------------
    MARK_RE = re.compile(r"§[^§]*§")

    @app.route("/intruder/<int:tx_id>", methods=["GET", "POST"])
    def intruder(tx_id: int):
        tx = STORE.get(tx_id)
        if not tx:
            abort(404)

        template_text = request.form.get("template") if request.method == "POST" else _decode(tx.raw_request)
        n_positions = len(MARK_RE.findall(template_text or ""))

        if request.method == "GET":
            return render_template(
                "intruder.html",
                tx=tx,
                template_text=template_text,
                n_positions=n_positions,
                payload_lists=["" for _ in range(max(1, n_positions))],
                results=None,
                mode="sniper",
                target_host=tx.host,
                target_port=tx.port,
                target_scheme=tx.scheme,
            )

        # POST
        mode = request.form.get("mode", "sniper")
        host = request.form.get("host") or tx.host
        port = int(request.form.get("port") or tx.port)
        scheme = request.form.get("scheme") or tx.scheme

        payload_lists: list[list[str]] = []
        # In sniper/battering-ram, we use payloads_0 as the single list.
        # In pitchfork/clusterbomb, we use one list per position.
        max_lists = max(1, n_positions)
        for i in range(max_lists):
            text = request.form.get(f"payloads_{i}", "")
            payload_lists.append([p for p in text.splitlines() if p.strip() != ""])

        results = _run_intruder(mode, n_positions, payload_lists, template_text or "", host, port, scheme)

        return render_template(
            "intruder.html",
            tx=tx,
            template_text=template_text,
            n_positions=n_positions,
            payload_lists=[request.form.get(f"payloads_{i}", "") for i in range(max_lists)],
            results=results,
            mode=mode,
            target_host=host,
            target_port=port,
            target_scheme=scheme,
        )

    return app


# ---------------------------------------------------------------------------
# Intruder attack engine
# ---------------------------------------------------------------------------


_MARK_RE = re.compile(r"§[^§]*§")


def _replace_markers(template: str, replacements: list[str]) -> str:
    """Replace each §...§ with the next entry of `replacements`."""
    it = iter(replacements)
    return _MARK_RE.sub(lambda _m: next(it, ""), template)


def _run_intruder(
    mode: str,
    n_positions: int,
    payload_lists: list[list[str]],
    template: str,
    host: str,
    port: int,
    scheme: str,
) -> list[dict]:
    results: list[dict] = []
    if n_positions == 0:
        return [{"payload": "(no §...§ markers)", "status": None, "length": 0,
                 "duration_ms": 0, "error": "mark at least one position with §"}]

    # Sniper: one marker at a time; others left as empty string.
    if mode == "sniper":
        payloads = payload_lists[0] if payload_lists else []
        if not payloads:
            return [{"payload": "(no payloads)", "status": None, "length": 0,
                     "duration_ms": 0, "error": "supply payloads"}]
        for pos in range(n_positions):
            for payload in payloads:
                replacements = ["" for _ in range(n_positions)]
                replacements[pos] = payload
                attempt = _replace_markers(template, replacements)
                results.append(_send(host, port, scheme, attempt, f"pos {pos+1}: {payload}"))
        return results

    # Battering-ram: same payload in every marker, same round.
    if mode == "battering-ram":
        payloads = payload_lists[0] if payload_lists else []
        if not payloads:
            return [{"payload": "(no payloads)", "status": None, "length": 0,
                     "duration_ms": 0, "error": "supply payloads"}]
        for payload in payloads:
            attempt = _replace_markers(template, [payload] * n_positions)
            results.append(_send(host, port, scheme, attempt, payload))
        return results

    # Pitchfork: iterate lists in lockstep, stop at the shortest.
    if mode == "pitchfork":
        if len(payload_lists) < n_positions or any(not pl for pl in payload_lists[:n_positions]):
            return [{"payload": "(missing payload list)", "status": None, "length": 0,
                     "duration_ms": 0, "error": f"supply {n_positions} non-empty payload lists"}]
        for row in zip(*payload_lists[:n_positions]):
            attempt = _replace_markers(template, list(row))
            results.append(_send(host, port, scheme, attempt, " | ".join(row)))
        return results

    # Cluster bomb: cartesian product over lists.
    if mode == "clusterbomb":
        if len(payload_lists) < n_positions or any(not pl for pl in payload_lists[:n_positions]):
            return [{"payload": "(missing payload list)", "status": None, "length": 0,
                     "duration_ms": 0, "error": f"supply {n_positions} non-empty payload lists"}]
        combos = itertools.product(*payload_lists[:n_positions])
        for row in combos:
            attempt = _replace_markers(template, list(row))
            results.append(_send(host, port, scheme, attempt, " | ".join(row)))
        return results

    return [{"payload": f"(unknown mode {mode})", "status": None, "length": 0,
             "duration_ms": 0, "error": "unknown attack mode"}]


def _send(host: str, port: int, scheme: str, text: str, label: str) -> dict:
    raw = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("iso-8859-1")
    r = send_raw(host, port, scheme, raw)
    return {
        "payload": label,
        "status": r.get("status"),
        "length": len(r.get("body", b"")),
        "duration_ms": r.get("duration_ms", 0),
        "error": r.get("error"),
    }


def serve(
    host: str = "127.0.0.1",
    port: int = 8888,
    proxy_host: str = "127.0.0.1",
    proxy_port: int = 8080,
) -> None:
    app = create_app(proxy_host=proxy_host, proxy_port=proxy_port)
    print(f"[webui] open http://{host}:{port}/")
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
