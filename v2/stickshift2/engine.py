"""Shift engine: plan -> prove -> type -> verify. Ported from Protocol.m + Switch.m.

Every refusal returns a reason code instead of typing (fail closed). Verification uses
NEW occurrences of the agent's own confirmation line, counted against a baseline taken
before typing, so an old line in scrollback can never fake success:
  Claude: "Set model to <display>" / "Set effort level to <effort>"
  Codex:  "Model changed to <slug> [effort]"
The status line is not required (default Claude Code shows none).

Note: exactly like typing /model by hand, a shift also becomes the agent's default for
new sessions (Claude saves it to settings.json, Codex to config.toml).
"""
import fcntl
import os
import time
from dataclasses import dataclass, field

from . import classify as C
from .procinfo import identify, is_self

REASONS = ("CHANGED", "ALREADY_SET", "PLAN", "UNCHANGED", "UNKNOWN_FINAL_STATE", "DIALOG_OPEN",
           "NO_PANE", "NO_AGENT", "SELF_TARGET", "BUSY", "DRAFT_PRESENT", "AGENT_WAITING",
           "UNSUPPORTED_MODEL", "UNSUPPORTED_EFFORT", "BAD_CONFIG", "LOCKED", "INJECT_DROPPED",
           "AGENT_CHANGED")


@dataclass
class Outcome:
    reason: str
    detail: str = ""
    pane: str = ""
    agent: str = ""
    plan: list = field(default_factory=list)

    @property
    def ok(self):
        return self.reason in ("CHANGED", "ALREADY_SET", "PLAN")

    def as_dict(self):
        return {"reason": self.reason, "detail": self.detail, "pane": self.pane,
                "agent": self.agent, "plan": self.plan, "ok": self.ok,
                "warn": self.reason in ("DIALOG_OPEN", "UNKNOWN_FINAL_STATE", "PLAN")}


# --- plans ----------------------------------------------------------------------------
_CODEX_EFFORT_LABEL = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high",
                       "max": "Max", "ultra": "Ultra"}


def plan(kind, model, effort, catalog, current_model=None):
    """List of steps: ("text", s) ("enter",) ("wait", needle) ("select", label)
    ("select", label, "model"|"effort") ("verify_model", display) ("verify_effort", effort)
    ("verify_codex", slug, effort)."""
    e = catalog.entry(kind, model)
    if not e:
        return None
    steps = []
    if kind == "claude":
        if not (effort and current_model and current_model == e.token):
            steps += [("text", "/model " + e.token), ("enter",), ("verify_model", e.display)]
        if effort:
            steps += [("text", "/effort " + effort), ("enter",), ("verify_effort", effort)]
    else:
        steps += [("text", "/model"), ("enter",), ("wait", "Select Model and Effort"), ("select", e.display, "model")]
        if effort:
            steps.append(("wait", "Select Reasoning Level"))
            if effort in ("max", "ultra"):      # Codex 0.156.1: behind "More reasoning…"
                steps += [("select", "More reasoning…", "effort"), ("wait", "Advanced Reasoning")]
            steps.append(("select", _CODEX_EFFORT_LABEL[effort], "effort"))
        else:
            steps.append(("enter",))
        steps.append(("verify_codex", e.token, effort or ""))
    return steps


# --- engine -----------------------------------------------------------------------------
class Engine:
    def __init__(self, tmux, config, log=None):
        self.tmux = tmux
        self.cfg = config
        self.log = log or (lambda *a, **k: None)

    # state of one pane (used by CLI status, the web panel and the chat)
    def state(self, pane):
        ident = identify(pane["pid"]) if pane.get("pid") else None
        if not ident:
            return None, C.PaneState(note="no Claude Code or Codex in this pane")
        styled = self.tmux.capture(pane["id"], styled=True)
        return ident, C.classify(None, ident.kind, self.cfg.catalog, styled=styled)

    def _lock(self, pane_id):
        root = os.path.expanduser("~/.stickshift")
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        d = os.path.join(root, "locks")
        os.makedirs(d, mode=0o700, exist_ok=True)
        f = open(os.path.join(d, pane_id.replace("%", "p").replace("/", "_") + ".lock"), "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return None
        return f

    def prove(self, pane_id, need_empty=True):
        """Common prechecks. Returns (pane, ident, state, None) or (…, Outcome refusal)."""
        pane = self.tmux.pane(pane_id)
        if not pane:
            return None, None, None, Outcome("NO_PANE", "no tmux pane %s" % pane_id, pane_id)
        if is_self(pane["pid"]):
            return pane, None, None, Outcome("SELF_TARGET", "that pane is running StickShift itself", pane["id"])
        ident, st = self.state(pane)
        if not ident:
            return pane, None, st, Outcome("NO_AGENT", st.note, pane["id"])
        if st.switch_dialog:
            return pane, ident, st, Outcome("DIALOG_OPEN", "a switch dialog is open in the terminal", pane["id"], ident.kind)
        if not st.composer:
            return pane, ident, st, Outcome("AGENT_WAITING", st.note, pane["id"], ident.kind)
        if st.busy:
            return pane, ident, st, Outcome("BUSY", "the agent is working; try again when it is idle", pane["id"], ident.kind)
        if need_empty and not st.input_empty:
            return pane, ident, st, Outcome("DRAFT_PRESENT", "there is unsent text in the input box", pane["id"], ident.kind)
        return pane, ident, st, None

    def shift(self, pane_id, model, effort=None, commit=False):
        pane, ident, st, refusal = self.prove(pane_id)
        if refusal:
            return refusal
        kind, cat = ident.kind, self.cfg.catalog
        e = cat.entry(kind, model)
        if not e:
            return Outcome("UNSUPPORTED_MODEL", "%s is not in the %s catalog" % (model, kind), pane["id"], kind)
        if effort and not cat.accepts(kind, e.token, effort):
            return Outcome("UNSUPPORTED_EFFORT", "%s does not take effort %s" % (e.token, effort), pane["id"], kind)
        if st.model == e.token and (not effort or st.effort == effort):
            return Outcome("ALREADY_SET", "%s%s" % (e.display, (" / " + effort) if effort else ""), pane["id"], kind)
        steps = plan(kind, e.token, effort, cat, current_model=st.model or None)
        human = [" ".join(str(x) for x in s) for s in steps]
        if not commit:
            return Outcome("PLAN", "dry run — nothing typed", pane["id"], kind, human)
        lock = self._lock(pane["id"])
        if not lock:
            return Outcome("LOCKED", "another shift is running on this pane", pane["id"], kind)
        try:
            out = self._run(pane, ident, steps)
            out.plan = human
            self.log("shift", pane=pane["name"], agent=kind, model=e.token, effort=effort or "", reason=out.reason, detail=out.detail)
            return out
        finally:
            lock.close()

    def _still_same(self, pane, ident):
        cur = identify(pane["pid"])
        return cur is not None and cur.pid == ident.pid

    def _run(self, pane, ident, steps):
        pid, kind = pane["id"], ident.kind
        base = self.tmux.capture(pid, history=True)
        b_model, b_effort, b_codex = C.set_model_targets(base), C.set_effort_targets(base), C.codex_changes(base)
        for s in steps:
            if not self._still_same(pane, ident):
                return Outcome("AGENT_CHANGED", "the process in the pane changed mid-shift", pid, kind)
            op = s[0]
            if op == "text":
                before = C.occurrences(s[1], self.tmux.capture(pid))
                self.tmux.send_literal(pid, s[1])
                if not self._poll(lambda: C.occurrences(s[1], self.tmux.capture(pid)) > before, 2.0):
                    return Outcome("INJECT_DROPPED", "typed %r but it never appeared in the pane" % s[1], pid, kind)
            elif op == "enter":
                self.tmux.send_key(pid, "Enter")
            elif op == "wait":
                if not self._poll(lambda: s[1] in C.tail(self.tmux.capture(pid), 24), 6.0):
                    return Outcome("UNKNOWN_FINAL_STATE", "did not see %r" % s[1], pid, kind)
            elif op == "select":
                row = C.picker_row(s[1], self.tmux.capture(pid))
                if row <= 0 or row > 9:
                    self.tmux.send_key(pid, "Escape")
                    return Outcome("UNSUPPORTED_MODEL" if s[2] == "model" else "UNSUPPORTED_EFFORT",
                                   "%r is not offered in the picker" % s[1], pid, kind)
                self.tmux.send_literal(pid, str(row))
                time.sleep(0.4)
            elif op == "verify_model":
                r = self._verify_claude(pid, lambda t: C.set_model_targets(t).count(s[1]) > b_model.count(s[1]), s[1])
                if r:
                    return r
            elif op == "verify_effort":
                r = self._verify_claude(pid, lambda t: C.set_effort_targets(t).count(s[1]) > b_effort.count(s[1]), s[1])
                if r:
                    return r
            elif op == "verify_codex":
                want = s[1]

                def ok(t):
                    new = [x for x in C.codex_changes(t)]
                    return sum(1 for x in new if x.split()[0] == want) > sum(1 for x in b_codex if x.split()[0] == want)
                if not self._poll(lambda: ok(self.tmux.capture(pid, history=True)), 8.0):
                    return Outcome("UNKNOWN_FINAL_STATE", "no 'Model changed to %s' confirmation" % want, pid, kind)
            time.sleep(0.15)
        return Outcome("CHANGED", "; ".join(" ".join(str(x) for x in s) for s in steps if s[0].startswith("verify")), pid, kind)

    def _verify_claude(self, pid, ok, target):
        """Wait for the confirmation line; handle Claude's switch dialog per policy."""
        deadline = time.time() + 8.0
        answered = False
        while time.time() < deadline:
            full = self.tmux.capture(pid, history=True)
            if ok(full):
                return None
            screen = self.tmux.capture(pid)
            bottom = C.tail(screen, 14)
            if "Model '" in bottom and "not found" in bottom:
                return Outcome("UNSUPPORTED_MODEL", "the agent says the model was not found", pid, "claude")
            if "Invalid argument" in bottom:
                return Outcome("UNSUPPORTED_EFFORT", "the agent rejected the effort", pid, "claude")
            st = C.classify(screen, "claude", self.cfg.catalog)
            if st.switch_dialog and not answered:
                from .catalog import display_matches
                ours = display_matches(st.dialog_target, target) or st.dialog_target.startswith(target)
                if not ours:
                    return Outcome("DIALOG_OPEN", "a switch dialog is open but it is not ours", pid, "claude")
                pol = self.cfg.dialog_policy if self.cfg.auto_answer else "ask"
                if pol == "ask":
                    return Outcome("DIALOG_OPEN", "Claude asks to confirm the switch; answer in the terminal "
                                                  "or set dialog_policy=confirm + auto_answer=true", pid, "claude")
                if pol == "cancel":
                    self.tmux.send_literal(pid, "2")
                    return Outcome("UNCHANGED", "switch dialog cancelled per policy", pid, "claude")
                self.tmux.send_key(pid, "Enter")       # "Yes, switch" is the highlighted default
                answered = True
            time.sleep(0.25)
        return Outcome("UNKNOWN_FINAL_STATE", "no confirmation for %s" % target, pid, "claude")

    @staticmethod
    def _poll(cond, timeout, every=0.15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if cond():
                    return True
            except Exception:
                pass
            time.sleep(every)
        return False
