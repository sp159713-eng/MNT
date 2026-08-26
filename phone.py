"""The book, the orders and the screen, as a phone page, served over Tailscale.

Carried over from NNS and reimplemented here rather than imported - MNT is
standalone, and a production system that breaks when an experiment is edited is
not production. What it serves is MNT's own machinery: production.picks() for
the book, orders.plan() for the difference against the paper account, and
screen.py for the health of the names being held.

A web page rather than an iOS app because an iOS app cannot be built on Windows:
compiling and signing need macOS and Xcode, and installing on a personal device
needs a paid Apple account. Safari's "Add to Home Screen" gives the icon and the
fullscreen window, which is the part of an app that was wanted.

IT CANNOT PLACE AN ORDER, AND THE GATE IS NOT REACHABLE FROM HERE

orders.execute is never called and never imported. risk.armed() is READ, so the
page can say whether the gate is open, and there is no route that arms, disarms
or halts anything - those stay in risk.py behind a CLI and a deadline. The one
broker call in this file is holdings/cash, through orders.plan, which reads.

This matters more here than it did in NNS. NNS is a sandbox whose order path has
never placed a real trade; MNT is the book that is meant to be trusted. A phone
page that could reach the live gate would be a way to trade by accident from a
lock screen, so it is not one.

WHY IT BINDS TO TAILSCALE AND NOTHING ELSE BY DEFAULT

The page shows positions and account equity. The default is the tailnet - the
private network between your own devices - and if no Tailscale address is found
the server refuses to start rather than falling back to something wider. 100.64/
10 is CGNAT space and is not routable from the public internet, which is what
makes it a safe default rather than a private-looking one. Binding elsewhere has
to be typed out with --host.

WHY THE BOOK IS COMPUTED IN THE BACKGROUND

production.picks() takes about forty seconds - it scores the whole universe.
Recomputing inside a request would mean a phone waiting forty seconds per tap.
Everything slow runs on a thread; the page renders the last answer with its age.

Run with:  py -3.13 phone.py
           py -3.13 phone.py --port 8765
           py -3.13 phone.py --host 0.0.0.0     (says why that is a bad idea)
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import risk
import screen as screen_module
from theme import Palette

DEFAULT_PORT = 8765
TAILSCALE_PREFIX = "100."

_BOOK = {"orders": [], "picks": [], "equity": None, "computed_at": None,
         "status": "idle", "error": None}
_SCREEN = {"rows": [], "what": "book", "computed_at": None, "status": "idle",
           "error": None, "progress": None, "source": ""}
_BOOK_LOCK = threading.Lock()
_SCREEN_LOCK = threading.Lock()


def tailscale_address() -> str | None:
    """This machine's tailnet address, or None when Tailscale is not up."""
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        addresses = []
    for address in addresses:
        if address.startswith(TAILSCALE_PREFIX):
            return address

    for probe in ("100.100.100.100", "8.8.8.8"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((probe, 80))
            address = sock.getsockname()[0]
            if address.startswith(TAILSCALE_PREFIX):
                return address
        except OSError:
            pass
        finally:
            sock.close()
    return None


# --------------------------------------------------------------------------
# the book
# --------------------------------------------------------------------------

def _rebuild_book():
    try:
        import broker as broker_module
        import orders as orders_module
        import production

        picks = production.picks()
        venue = broker_module.broker()
        plan = orders_module.plan(picks, venue=venue)
        equity = venue.cash() + sum(
            position["qty"] * position.get("avg", 0.0)
            for position in venue.holdings().values())

        with _BOOK_LOCK:
            _BOOK.update(orders=plan, picks=list(picks), equity=float(equity),
                         computed_at=time.time(), status="idle", error=None)
    except Exception as exc:
        with _BOOK_LOCK:
            _BOOK.update(status="error", error=f"{type(exc).__name__}: {exc}")


def start_book() -> bool:
    with _BOOK_LOCK:
        if _BOOK["status"] == "working":
            return False
        _BOOK.update(status="working", error=None)
    threading.Thread(target=_rebuild_book, daemon=True).start()
    return True


def book_snapshot() -> dict:
    with _BOOK_LOCK:
        state = dict(_BOOK)
    return {
        "status": state["status"],
        "error": state["error"],
        "picks": state["picks"],
        "equity": state["equity"],
        "age_seconds": ((time.time() - state["computed_at"])
                        if state["computed_at"] else None),
        # Read, never written. See the module docstring.
        "armed": risk.armed(),
        "arm_remaining": risk.arm_remaining() if risk.armed() else 0,
        "halted": risk.halted(),
        "orders": [
            {"side": o["side"], "symbol": o["symbol"], "qty": o["qty"],
             "price": o.get("price", 0.0), "value": o.get("value", 0.0),
             "reason": o.get("reason", ""), "cost_bp": o.get("cost_bp", 0.0)}
            for o in state["orders"]],
    }


# --------------------------------------------------------------------------
# the screen
# --------------------------------------------------------------------------

def _rescreen(what: str):
    try:
        def progress(done, total, _symbol):
            with _SCREEN_LOCK:
                _SCREEN["progress"] = (done, total)

        symbols = (list(config.UNIVERSE) if what == "book" else
                   [s for s in screen_module.CANDIDATES
                    if s.upper() not in {u.upper() for u in config.UNIVERSE}])
        rows = screen_module.scan(symbols, progress=progress)
        with _SCREEN_LOCK:
            _SCREEN.update(rows=rows, what=what, computed_at=time.time(),
                           status="idle", error=None, progress=None,
                           source=screen_module.describe_source())
    except Exception as exc:
        with _SCREEN_LOCK:
            _SCREEN.update(status="error", progress=None,
                           error=f"{type(exc).__name__}: {exc}")


def start_screen(what: str) -> bool:
    with _SCREEN_LOCK:
        if _SCREEN["status"] == "working":
            return False
        _SCREEN.update(status="working", error=None, what=what, progress=None)
    threading.Thread(target=_rescreen, args=(what,), daemon=True).start()
    return True


def screen_snapshot() -> dict:
    with _SCREEN_LOCK:
        state = dict(_SCREEN)
    return {
        "status": state["status"],
        "error": state["error"],
        "what": state["what"],
        "progress": state["progress"],
        "source": state["source"] or screen_module.describe_source(),
        "age_seconds": ((time.time() - state["computed_at"])
                        if state["computed_at"] else None),
        "rows": state["rows"],
    }


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------

MANIFEST = {
    "name": "MNT Book", "short_name": "MNT", "display": "standalone",
    "background_color": Palette.bg, "theme_color": Palette.bg,
    "start_url": "/",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
}

ICON = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">
<rect width="180" height="180" rx="40" fill="{Palette.bg}"/>
<rect x="34" y="96" width="22" height="46" rx="5" fill="{Palette.accent}"/>
<rect x="68" y="70" width="22" height="72" rx="5" fill="{Palette.accent}"/>
<rect x="102" y="46" width="22" height="96" rx="5" fill="{Palette.good}"/>
<rect x="136" y="80" width="22" height="62" rx="5" fill="{Palette.accent}"/>
</svg>"""

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="MNT">
<meta name="theme-color" content="__BG__">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon.svg">
<title>MNT Book</title>
<style>
  :root {
    --bg: __BG__; --panel: __PANEL__; --border: __BORDER__; --fg: __TEXT__;
    --muted: __MUTED__; --faint: __FAINT__; --accent: __ACCENT__;
    --good: __GOOD__; --bad: __BAD__; --warn: __WARN__;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    padding: env(safe-area-inset-top) env(safe-area-inset-right)
             env(safe-area-inset-bottom) env(safe-area-inset-left);
  }
  header {
    position: sticky; top: 0; z-index: 2; background: var(--bg);
    padding: 14px 16px 10px; border-bottom: 1px solid var(--border);
  }
  .title { display: flex; align-items: center; justify-content: space-between; }
  h1 { font-size: 17px; margin: 0; font-weight: 600; }
  button {
    background: var(--accent); color: #fff; border: 0; border-radius: 8px;
    font: 600 14px/1 -apple-system, sans-serif; padding: 9px 14px; cursor: pointer;
  }
  button:disabled { background: var(--border); color: var(--muted); }
  .state { margin-top: 9px; font: 12px/1.5 ui-monospace, Menlo, Consolas, monospace;
           color: var(--muted); }
  .armed { color: var(--bad); font-weight: 700; }
  .tabs { display: flex; gap: 6px; margin-top: 11px; }
  .tabs button { flex: 1; background: var(--panel); color: var(--muted); padding: 8px 0; }
  .tabs button.on { background: var(--accent); color: #fff; }
  .note { margin: 0; padding: 12px 16px; color: var(--warn); background: var(--panel);
          font-size: 12.5px; line-height: 1.5; border-bottom: 1px solid var(--border); }
  ul { list-style: none; margin: 0; padding: 0 0 40px; }
  li { display: grid; grid-template-columns: 62px 1fr auto; gap: 10px;
       align-items: baseline; padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .side { font: 700 12px/1.6 ui-monospace, Menlo, Consolas, monospace; }
  .BUY { color: var(--good); } .SELL { color: var(--bad); }
  .avoid, .listed { color: var(--bad); font-weight: 700; }
  .caution { color: var(--warn); } .clean { color: var(--faint); }
  .sym { font-weight: 600; }
  .sub { color: var(--muted); font-size: 12px; }
  .num { text-align: right; font: 13px/1.4 ui-monospace, Menlo, Consolas, monospace; }
  .empty { padding: 40px 16px; color: var(--muted); text-align: center; }
  .seg { display: flex; gap: 6px; padding: 11px 16px; border-bottom: 1px solid var(--border); }
  .seg button { flex: 1; background: var(--panel); color: var(--muted); }
  .seg button.on { background: var(--border); color: var(--fg); }
</style></head>
<body>
<header>
  <div class="title"><h1>MNT Book</h1><button id="go">Refresh</button></div>
  <div class="state" id="state">loading...</div>
  <div class="tabs">
    <button id="tab-book" class="on">Orders</button>
    <button id="tab-screen">Screen</button>
  </div>
</header>
<div id="view-book"><ul id="orders"></ul></div>
<div id="view-screen" hidden>
  <div class="seg">
    <button id="seg-book" class="on">The book</button>
    <button id="seg-other">Other names</button>
  </div>
  <p class="note" id="source"></p>
  <ul id="screen"></ul>
</div>
<script>
const $ = (id) => document.getElementById(id);
let tab = "book", what = "book", bpoll = null, spoll = null;

function money(n) { return (n||0).toLocaleString(undefined, {maximumFractionDigits: 0}); }
function age(s) {
  if (s === null) return "never computed";
  if (s < 90) return Math.round(s) + "s ago";
  if (s < 5400) return Math.round(s/60) + "m ago";
  return Math.round(s/3600) + "h ago";
}

function renderBook(d) {
  let bits = ["gate " + (d.armed ? "<span class='armed'>ARMED " + d.arm_remaining + "m</span>" : "closed")];
  if (d.halted) bits.push("<span class='armed'>HALTED</span>");
  if (d.equity !== null) bits.push("equity " + money(d.equity));
  let line = bits.join(" &middot; ") + "<br>" + age(d.age_seconds);
  if (d.status === "working") line += " &middot; recomputing...";
  if (d.status === "error") line += " &middot; <span class='armed'>" + d.error + "</span>";
  $("state").innerHTML = line;
  $("go").disabled = (d.status === "working");

  $("orders").innerHTML = d.orders.length ? d.orders.map(o =>
    "<li><span class='side " + o.side + "'>" + o.side + "</span>" +
    "<span><span class='sym'>" + o.symbol + "</span><br>" +
    "<span class='sub'>" + o.reason + " &middot; " + o.cost_bp + "bp</span></span>" +
    "<span class='num'>" + o.qty + " sh<br><span class='sub'>" +
    money(o.value) + "</span></span></li>").join("")
    : (d.status === "working"
        ? "<li class='empty'>Scoring the universe. Takes about forty seconds.</li>"
        : "<li class='empty'>No difference between the book and the account.</li>");
}

function renderScreen(d) {
  $("source").textContent = d.source;
  if (d.status === "working") {
    const p = d.progress ? (" " + d.progress[0] + "/" + d.progress[1]) : "";
    $("screen").innerHTML = "<li class='empty'>Screening" + p + "...</li>";
    return;
  }
  if (d.status === "error") { $("screen").innerHTML = "<li class='empty'>" + d.error + "</li>"; return; }
  $("screen").innerHTML = d.rows.length ? d.rows.map(r =>
    "<li><span class='side " + (r.listed ? "listed" : r.severity) + "'>" +
      (r.listed ? "LISTED" : r.severity.toUpperCase()) + "</span>" +
    "<span><span class='sym'>" + r.symbol + "</span><br>" +
    "<span class='sub'>" + (r.flags || r.verdict) + "</span></span>" +
    "<span class='num'>" + money(r.price) + "<br><span class='sub'>" +
    (r.turnover === null ? "unknown" : (r.turnover/1e7).toFixed(1) + " Cr") +
    "</span></span></li>").join("")
    : "<li class='empty'>Nothing screened yet.</li>";
}

async function loadBook() {
  const d = await (await fetch("/api/book")).json();
  renderBook(d);
  if (d.status === "working" && !bpoll) bpoll = setInterval(loadBook, 3000);
  if (d.status !== "working" && bpoll) { clearInterval(bpoll); bpoll = null; }
}
async function loadScreen() {
  const d = await (await fetch("/api/screen")).json();
  renderScreen(d);
  if (d.status === "working" && !spoll) spoll = setInterval(loadScreen, 2000);
  if (d.status !== "working" && spoll) { clearInterval(spoll); spoll = null; }
}

function showTab(which) {
  tab = which;
  $("tab-book").className = which === "book" ? "on" : "";
  $("tab-screen").className = which === "screen" ? "on" : "";
  $("view-book").hidden = which !== "book";
  $("view-screen").hidden = which === "book";
  $("go").textContent = which === "book" ? "Refresh" : "Screen";
  if (which === "screen") loadScreen();
}
$("tab-book").onclick = () => showTab("book");
$("tab-screen").onclick = () => showTab("screen");
$("seg-book").onclick = () => { what = "book";
  $("seg-book").className = "on"; $("seg-other").className = ""; };
$("seg-other").onclick = () => { what = "candidates";
  $("seg-other").className = "on"; $("seg-book").className = ""; };

$("go").onclick = async () => {
  if (tab === "book") { await fetch("/api/rebuild", {method: "POST"}); loadBook(); }
  else {
    await fetch("/api/rescreen", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({what: what})});
    loadScreen();
  }
};
loadBook();
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  loadBook();
  if (tab === "screen") loadScreen();
});
</script>
</body></html>"""


def render_page() -> str:
    page = PAGE
    for token, value in (
            ("__BG__", Palette.bg), ("__PANEL__", Palette.panel),
            ("__BORDER__", Palette.border), ("__TEXT__", Palette.text),
            ("__MUTED__", Palette.muted), ("__FAINT__", Palette.faint),
            ("__ACCENT__", Palette.accent), ("__GOOD__", Palette.good),
            ("__BAD__", Palette.bad), ("__WARN__", Palette.warn)):
        page = page.replace(token, value)
    return page


class Handler(BaseHTTPRequestHandler):
    server_version = "MNTPhone/1.0"

    def _send(self, body: bytes, content_type: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload):
        self._send(json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(render_page().encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/manifest.webmanifest":
            self._send(json.dumps(MANIFEST).encode("utf-8"),
                       "application/manifest+json")
        elif self.path == "/icon.svg":
            self._send(ICON.encode("utf-8"), "image/svg+xml")
        elif self.path == "/api/book":
            self._json(book_snapshot())
        elif self.path == "/api/screen":
            self._json(screen_snapshot())
        else:
            self._send(b"not found", "text/plain", code=404)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_POST(self):
        if self.path == "/api/rebuild":
            self._json({"started": start_book()})
        elif self.path == "/api/rescreen":
            what = str(self._body().get("what") or "book")
            self._json({"started": start_screen(
                "candidates" if what == "candidates" else "book")})
        else:
            self._send(b"not found", "text/plain", code=404)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", help="interface to bind. Defaults to this "
                                       "machine's Tailscale address.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--warm", action="store_true",
                        help="score the book at startup rather than on the "
                             "first tap")
    args = parser.parse_args()

    host = args.host
    if host is None:
        host = tailscale_address()
        if host is None:
            raise SystemExit(
                "No Tailscale address found on this machine.\n"
                "  This page shows the book and the account, so it will not "
                "fall back to binding\n"
                "  every interface. Start Tailscale, or say where to listen:  "
                "py -3.13 phone.py --host 0.0.0.0")
    elif host in ("0.0.0.0", "::"):
        print(f"  WARNING: binding every interface. Anything that can reach "
              f"this machine on\n           port {args.port} can read your "
              f"book. There is no login on this page.")

    if args.warm:
        start_book()

    server = ThreadingHTTPServer((host, args.port), Handler)
    print(f"\n  MNT Book on http://{host}:{args.port}")
    print("  Open that on your phone, then Share -> Add to Home Screen.")
    print(f"  Live gate: {'ARMED' if risk.armed() else 'closed'}"
          f"{'  HALTED' if risk.halted() else ''}")
    print("  This page reads. It cannot place, arm or halt anything.  "
          "Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
