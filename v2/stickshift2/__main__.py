"""stickshift2 — CLI.

  stickshift2 list                          panes running Claude Code / Codex, with state
  stickshift2 status [--pane P]             one pane in detail
  stickshift2 shift <gear|model[:effort]> [--pane P] [--commit]
                                            dry run by default; --commit types
  stickshift2 chat "<prompt>" [--pane P]    level-1 chat: prompt the agent, print its reply
  stickshift2 web [--port N] [--remote]     web panel (gearbox + chat) on 127.0.0.1
  stickshift2 keys                          tmux bind-key lines for gears 1-5/R

Pane: --pane accepts a tmux pane id (%3) or session:window.pane. Default: the pane the
current tmux client is looking at.
"""
import argparse
import json
import sys

from . import VERSION
from .config import Config, GEARS
from .engine import Engine
from .log import Log
from .tmux import Tmux


def _pane_arg(tmux, value):
    if value:
        return value
    try:
        return tmux._run(["display-message", "-p", "#{pane_id}"]).strip()
    except Exception:
        return ""


def _resolve(cfg, target, kind):
    """gear name or model[:effort] -> (model, effort)."""
    if target.upper() in GEARS:
        t = cfg.tuple_for(target, kind)
        return t if t else (None, None)
    model, _, effort = target.partition(":")
    return model, (effort or None)


def cmd_list(eng, args):
    rows = []
    for p in eng.tmux.panes():
        ident, st = eng.state(p)
        if not ident:
            continue
        rows.append((p["id"], p["name"], ident.kind, st.model_display or "?", st.effort or "?",
                     "idle" if st.idle else (st.note or "?"), p["cwd"]))
    if args.json:
        print(json.dumps([dict(zip(("id", "name", "agent", "model", "effort", "state", "cwd"), r)) for r in rows], indent=2))
        return 0
    if not rows:
        print("no Claude Code or Codex found in any tmux pane")
        return 1
    for r in rows:
        print("%-5s %-18s %-6s %-24s %-9s %s" % r[:6])
    return 0


def cmd_status(eng, args):
    pid = _pane_arg(eng.tmux, args.pane)
    pane = eng.tmux.pane(pid) if pid else None
    if not pane:
        print("NO_PANE — %s" % (pid or "not inside tmux; use --pane"))
        return 2
    ident, st = eng.state(pane)
    info = {"pane": pane["id"], "name": pane["name"], "cwd": pane["cwd"],
            "agent": ident.kind if ident else "", "version": ident.version if ident else "",
            "exe": ident.exe if ident else ""}
    info.update(st.as_dict())
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def cmd_shift(eng, args):
    pid = _pane_arg(eng.tmux, args.pane)
    pane = eng.tmux.pane(pid) if pid else None
    if not pane:
        print("NO_PANE — %s" % (pid or "not inside tmux; use --pane"))
        return 2
    ident, _ = eng.state(pane)
    if not ident:
        print("NO_AGENT — no Claude Code or Codex in %s" % pane["name"])
        return 2
    model, effort = _resolve(eng.cfg, args.target, ident.kind)
    if not model:
        print("BAD_CONFIG — unknown gear %s" % args.target)
        return 2
    o = eng.shift(pane["id"], model, effort, commit=args.commit)
    print("%s — %s" % (o.reason, o.detail) if o.detail else o.reason)
    for s in o.plan:
        print("   ", s)
    return 0 if o.ok else 1


def cmd_chat(eng, args, log):
    from .chat import Chat
    pid = _pane_arg(eng.tmux, args.pane)
    ch = Chat(eng, log.write)
    r = ch.ask(pid, args.prompt, timeout=args.timeout)
    if r.get("ok"):
        print(r.get("reply", ""))
        return 0
    print("%s — %s" % (r.get("reason"), r.get("detail", "")))
    return 1


def cmd_keys(_eng, _args):
    exe = sys.argv[0] if sys.argv[0].endswith("stickshift2") else "stickshift2"
    print("# add to ~/.tmux.conf  (prefix + Alt+<gear>)")
    for g in ("1", "2", "3", "4", "5", "R"):
        key = "M-" + g.lower()
        print("bind-key %s run-shell -b '%s shift %s --pane \"#{pane_id}\" --commit'" % (key, exe, g))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stickshift2", description="StickShift v2 %s (tmux)" % VERSION)
    ap.add_argument("--socket", help="tmux socket name (-L), for tests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("status"); s.add_argument("--pane")
    s = sub.add_parser("shift"); s.add_argument("target"); s.add_argument("--pane"); s.add_argument("--commit", action="store_true")
    s = sub.add_parser("chat"); s.add_argument("prompt"); s.add_argument("--pane"); s.add_argument("--timeout", type=float, default=180)
    s = sub.add_parser("web"); s.add_argument("--port", type=int, default=8765); s.add_argument("--remote", action="store_true")
    sub.add_parser("keys")
    args = ap.parse_args(argv)

    cfg = Config.load()
    if cfg.error and args.cmd in ("shift", "chat", "web"):
        print("BAD_CONFIG — ~/.stickshift/config.toml refused: %s" % cfg.error)
        return 2
    log = Log()
    eng = Engine(Tmux(args.socket), cfg, log.write)
    if args.cmd == "list":
        return cmd_list(eng, args)
    if args.cmd == "status":
        return cmd_status(eng, args)
    if args.cmd == "shift":
        return cmd_shift(eng, args)
    if args.cmd == "chat":
        return cmd_chat(eng, args, log)
    if args.cmd == "keys":
        return cmd_keys(eng, args)
    if args.cmd == "web":
        from .web import serve
        return serve(eng, log, port=args.port, remote=args.remote)
    return 2


if __name__ == "__main__":
    sys.exit(main())
