"""Web panel: the gearbox (src/app/gearbox.html, unchanged) + a pane list + the level-1 chat.

Security (docs/PLANO-LINUX-WINDOWS.md):
- binds 127.0.0.1 by default; --remote binds 0.0.0.0 (e.g. over Tailscale)
- a random token is required on EVERY endpoint, reads included (they carry terminal text)
- Host header must be 127.0.0.1/localhost:<port> (DNS-rebinding guard) unless --remote
- Origin, when present, must match Host; no CORS headers are ever sent
- keystrokes are serialized per pane (engine lock); everything sent is logged (0600)
"""
import hmac
import json
import os
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import VERSION
from .chat import Chat
from .config import config_path

HERE = os.path.dirname(os.path.abspath(__file__))
GEARBOX = os.path.normpath(os.path.join(HERE, "..", "..", "src", "app", "gearbox.html"))

SHIM = """<script>
/* StickShift v2 web bridge: stands in for the macOS WKWebView message handlers. */
(function(){
  var Q=new URLSearchParams(location.search), T=Q.get('t')||'', PANE=Q.get('pane')||'';
  function H(){ return {'Content-Type':'application/json','X-StickShift-Token':T}; }
  function post(u,b){ return fetch(u,{method:'POST',headers:H(),body:JSON.stringify(b)}).then(function(r){return r.json();}); }
  window.webkit={messageHandlers:{
    shift:{postMessage:function(m){ m.pane=PANE; post('/api/shift',m).then(function(o){ window.outcome&&window.outcome(o); parent.postMessage({ss:'shifted'},location.origin); }).catch(function(e){ window.outcome&&window.outcome({reason:'NETWORK',detail:String(e),ok:false}); }); }},
    policy:{postMessage:function(m){ post('/api/policy',m).then(function(r){ window.policySaved&&window.policySaved(r); }); }},
    resize:{postMessage:function(){}}, drag:{postMessage:function(){}}
  }};
  function poll(){ if(!PANE) return; fetch('/api/state?pane='+encodeURIComponent(PANE),{headers:H()}).then(function(r){return r.json();}).then(function(s){ window.setLive&&window.setLive(s); }).catch(function(){}); }
  window.addEventListener('load',function(){
    fetch('/api/profiles',{headers:H()}).then(function(r){return r.json();}).then(function(p){ window.setProfiles&&window.setProfiles(p); poll(); });
    fetch('/api/policy',{headers:H()}).then(function(r){return r.json();}).then(function(p){ window.setPolicy&&window.setPolicy(p.policy); });
    setInterval(poll,1500);
  });
})();
</script>"""


def _effort_label(kind, e):
    return "extra high" if (kind == "codex" and e == "xhigh") else (e or "")


class App:
    def __init__(self, engine, log, port, remote):
        self.eng, self.log, self.port, self.remote = engine, log, port, remote
        self.token = secrets.token_urlsafe(18)
        self.chat = Chat(engine, log.write)

    # --- data ---
    def panes(self):
        out = []
        for p in self.eng.tmux.panes():
            ident, st = self.eng.state(p)
            if not ident:
                continue
            home = os.path.expanduser("~")
            cwd = "~" + p["cwd"][len(home):] if p["cwd"].startswith(home) else p["cwd"]
            out.append({"id": p["id"], "name": p["name"], "cwd": cwd, "agent": ident.kind,
                        "version": ident.version, "model": st.model_display, "token": st.model,
                        "effort": _effort_label(ident.kind, st.effort), "idle": st.idle,
                        "busy": st.busy, "note": st.note,
                        "chat": self.chat.view(p["id"])["status"]["state"]})
        return out

    def state(self, pane_id):
        p = self.eng.tmux.pane(pane_id)
        if not p:
            return {"agent": "", "model": "", "token": "", "effort": ""}
        ident, st = self.eng.state(p)
        if not ident:
            return {"agent": "", "model": "", "token": "", "effort": ""}
        return {"agent": ident.kind, "model": st.model_display, "token": st.model,
                "effort": _effort_label(ident.kind, st.effort), "idle": st.idle, "note": st.note}

    def shift(self, body):
        pane, model = str(body.get("pane", "")), str(body.get("model", ""))
        effort = str(body.get("effort", "")) or None
        o = self.eng.shift(pane, model, effort, commit=True)
        d = o.as_dict()
        d["activeGate"] = str(body.get("gate", "")) if o.ok else ""
        self.log.write("web_shift", pane=pane, model=model, effort=effort or "", reason=o.reason)
        return d

    def set_policy(self, body):
        pol = body.get("policy")
        pol = pol if pol in ("ask", "confirm", "cancel") else "ask"
        cfg = self.eng.cfg
        cfg.dialog_policy, cfg.auto_answer = pol, pol != "ask"
        try:
            _persist_policy(pol, cfg.auto_answer)
            return {"policy": pol, "ok": True, "err": ""}
        except OSError as e:
            return {"policy": pol, "ok": False, "err": str(e)}

    def gearbox(self):
        with open(GEARBOX, encoding="utf-8") as f:
            html = f.read()
        return html.replace("<head>", "<head>\n" + SHIM, 1)


def _persist_policy(policy, auto):
    path = config_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    lines = []
    if os.path.exists(path):
        if os.path.islink(path):
            raise OSError("config is a symlink")
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    new = {"dialog_policy": 'dialog_policy = "%s"' % policy, "auto_answer": "auto_answer = %s" % ("true" if auto else "false")}
    seen = set()
    for i, ln in enumerate(lines):
        for k, v in new.items():
            if ln.strip().startswith(k):
                lines[i] = v
                seen.add(k)
    lines += [v for k, v in new.items() if k not in seen]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        server_version = "stickshift2/" + VERSION
        sys_version = ""

        def log_message(self, fmt, *a):      # quiet; our own log has the events
            pass

        # --- guards ---
        def _host_ok(self):
            if app.remote:
                return True
            return self.headers.get("Host", "") in ("127.0.0.1:%d" % app.port, "localhost:%d" % app.port)

        def _origin_ok(self):
            o = self.headers.get("Origin")
            return o is None or o == "http://" + self.headers.get("Host", "")

        def _token_ok(self, q):
            t = self.headers.get("X-StickShift-Token") or (q.get("t") or [""])[0]
            return bool(t) and hmac.compare_digest(t, app.token)

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else (
                json.dumps(body, ensure_ascii=False).encode() if ctype == "application/json" else body.encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, q):
            if not self._host_ok():
                self._send(403, {"error": "bad Host"}); return False
            if not self._origin_ok():
                self._send(403, {"error": "bad Origin"}); return False
            if not self._token_ok(q):
                self._send(401, {"error": "token required"}); return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._guard(q):
                return
            pane = (q.get("pane") or [""])[0]
            if u.path == "/":
                with open(os.path.join(HERE, "static", "panel.html"), encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html")
            if u.path == "/gearbox":
                return self._send(200, app.gearbox(), "text/html")
            if u.path == "/api/panes":
                return self._send(200, {"panes": app.panes(), "version": VERSION})
            if u.path == "/api/state":
                return self._send(200, app.state(pane))
            if u.path == "/api/profiles":
                return self._send(200, app.eng.cfg.catalog.profiles())
            if u.path == "/api/policy":
                c = app.eng.cfg
                return self._send(200, {"policy": c.dialog_policy if c.auto_answer else "ask"})
            if u.path == "/api/chat":
                return self._send(200, app.chat.view(pane))
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            if not self._guard(parse_qs(u.query)):
                return
            try:
                n = min(int(self.headers.get("Content-Length", "0")), 16384)
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                return self._send(400, {"error": "bad JSON"})
            if u.path == "/api/shift":
                return self._send(200, app.shift(body))
            if u.path == "/api/policy":
                return self._send(200, app.set_policy(body))
            if u.path == "/api/chat":
                return self._send(200, app.chat.start(str(body.get("pane", "")), body.get("text")))
            return self._send(404, {"error": "not found"})
    return H


def build(engine, log, port=8765, remote=False):
    app = App(engine, log, port, remote)
    srv = ThreadingHTTPServer(("0.0.0.0" if remote else "127.0.0.1", port), make_handler(app))
    srv.daemon_threads = True
    return app, srv


def serve(engine, log, port=8765, remote=False):
    app, srv = build(engine, log, port, remote)
    print("StickShift v2 %s — web panel" % VERSION)
    print("  open:  http://127.0.0.1:%d/?t=%s" % (port, app.token))
    if remote:
        host = socket.gethostname()
        print("  remote (token required): http://%s:%d/?t=%s" % (host, port, app.token))
    print("  Ctrl+C to stop. Log: ~/.stickshift/v2.log")
    log.write("web_start", port=port, remote=remote)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
