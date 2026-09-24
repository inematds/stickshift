"""Level-1 chat: send a prompt to the agent in a pane, read its reply back from the screen.

Rules (docs/PLANO-LINUX-WINDOWS.md, "Chat nível 1"):
- Same proofs as a shift: a recognized agent, idle, composer on screen and empty.
- The text is a prompt, never a command: after stripping leading spaces it must not start
  with "/" (slash commands), "!" (shell mode) or "#" (Claude memory mode); no control
  characters (an embedded newline would submit early and smuggle a second line); length
  capped. Sent with `tmux send-keys -l --` (literal), then one Enter.
- Permission requests are NOT answered here (that is v3): if the agent stops to ask, the
  chat reports AGENT_WAITING and leaves the decision in the terminal.
"""
import re
import threading
import time

from . import classify as C

MAX_LEN = 2000
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_CLAUDE_DONE = re.compile(r"^[✻✢✳✶✽*·]\s+\S+ for \d")        # "✻ Brewed for 1s · done 8:31 PM"
_CODEX_TIME = re.compile(r"^\d{1,2}:\d{2}\s?(AM|PM)?$")


def validate(text):
    """Returns (clean_text, None) or (None, (reason, detail))."""
    if text is None:
        return None, ("BAD_INPUT", "empty message")
    t = text.lstrip()
    if not t:
        return None, ("BAD_INPUT", "empty message")
    if len(t) > MAX_LEN:
        return None, ("BAD_INPUT", "message longer than %d characters" % MAX_LEN)
    if _CTRL.search(t):
        return None, ("BAD_INPUT", "line breaks and control characters are not allowed (send one line)")
    if t[0] in "/!#":
        return None, ("BAD_INPUT", "messages starting with %r are commands, not prompts; use the gearbox "
                                   "for model/effort and the terminal for anything else" % t[0])
    return t.rstrip(), None


def _echo_index(lines, kind, prompt):
    key = prompt[:40]
    marks = ("❯",) if kind == "claude" else C.CODEX_PROMPTS
    found = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s[:1] in marks and s[1:].strip().startswith(key):
            found = i
    return found


def extract_reply(text, kind, prompt):
    lines = C._lines(text)
    ei = _echo_index(lines, kind, prompt)
    if ei is None:
        return None
    end = len(lines)
    if kind == "claude":
        for i in range(len(lines) - 2, ei, -1):
            if lines[i].lstrip().startswith("❯") and C._rule(lines[i - 1]) and C._rule(lines[i + 1]):
                end = i - 1
                break
    else:
        for i in range(len(lines) - 1, ei, -1):
            if lines[i].strip()[:1] in C.CODEX_PROMPTS:
                end = i
                break
    body = []
    for ln in lines[ei + 1:end]:
        s = ln.rstrip()
        st = s.strip()
        if kind == "claude" and (_CLAUDE_DONE.match(st) or st.startswith("⎿  Tip:")):
            continue
        if kind == "codex" and (_CODEX_TIME.match(st) or st.startswith("• Working")):
            continue
        for bullet in ("● ", "• "):
            if st.startswith(bullet):
                s = s.replace(bullet, "", 1)
        body.append(s[2:] if s.startswith("  ") else s)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body)


class Chat:
    def __init__(self, engine, log=None):
        self.eng = engine
        self.log = log
        self.history = {}          # pane_id -> list of messages
        self.status = {}           # pane_id -> {"state": ..., "detail": ...}
        self._mu = threading.Lock()

    def _add(self, pane, role, text, reason=""):
        with self._mu:
            self.history.setdefault(pane, []).append(
                {"role": role, "text": text, "reason": reason, "t": time.strftime("%H:%M:%S")})
            self.history[pane] = self.history[pane][-100:]

    def _set(self, pane, state, detail=""):
        with self._mu:
            self.status[pane] = {"state": state, "detail": detail}

    def _key(self, pane_id):
        p = self.eng.tmux.pane(pane_id)
        return p["id"] if p else pane_id

    def view(self, pane):
        pane = self._key(pane)
        with self._mu:
            return {"history": list(self.history.get(pane, [])),
                    "status": dict(self.status.get(pane, {"state": "idle", "detail": ""}))}

    def start(self, pane_id, text, timeout=300):
        """Non-blocking (web): validate + prove now, run the exchange in a thread."""
        clean, err = validate(text)
        if err:
            return {"ok": False, "reason": err[0], "detail": err[1]}
        pane_id = self._key(pane_id)
        if self.status.get(pane_id, {}).get("state") in ("sending", "working"):
            return {"ok": False, "reason": "LOCKED", "detail": "a message is already in flight for this pane"}
        _, _, _, refusal = self.eng.prove(pane_id)
        if refusal:
            return {"ok": False, "reason": refusal.reason, "detail": refusal.detail}
        self._set(pane_id, "sending")
        threading.Thread(target=self.ask, args=(pane_id, clean, timeout), daemon=True).start()
        return {"ok": True, "reason": "SENT", "detail": ""}

    def ask(self, pane_id, text, timeout=180):
        clean, err = validate(text)
        if err:
            return {"ok": False, "reason": err[0], "detail": err[1]}
        pane, ident, st, refusal = self.eng.prove(pane_id)
        if refusal:
            self._set(pane_id, "error", refusal.detail)
            return {"ok": False, "reason": refusal.reason, "detail": refusal.detail}
        pid, kind = pane["id"], ident.kind
        lock = self.eng._lock(pid)
        if not lock:
            return {"ok": False, "reason": "LOCKED", "detail": "a shift or message is running on this pane"}
        try:
            self._add(pid, "user", clean)
            self._set(pid, "sending")
            key = clean[:40]
            before = C.occurrences(key, self.eng.tmux.capture(pid))
            self.eng.tmux.send_literal(pid, clean)
            if not self.eng._poll(lambda: C.occurrences(key, self.eng.tmux.capture(pid)) > before, 3.0):
                self._set(pid, "error", "text never appeared in the input box")
                self._add(pid, "system", "", "INJECT_DROPPED")
                return {"ok": False, "reason": "INJECT_DROPPED", "detail": "text never appeared in the input box"}
            # still the same idle agent, input now holds exactly our text -> submit
            cur = C.classify(self.eng.tmux.capture(pid), kind, self.eng.cfg.catalog)
            if cur.busy or not cur.composer or cur.switch_dialog:
                self._set(pid, "error", "agent state changed before submit")
                return {"ok": False, "reason": "AGENT_CHANGED", "detail": "agent state changed before submit"}
            self.eng.tmux.send_key(pid, "Enter")
            if self.log:
                self.log("chat_sent", pane=pane["name"], agent=kind, chars=len(clean), text=clean)
            self._set(pid, "working")
            return self._await(pid, kind, clean, timeout)
        finally:
            lock.close()

    def _await(self, pid, kind, prompt, timeout):
        end = time.time() + timeout
        last, stable, seen_echo = None, 0, False
        time.sleep(0.8)
        while time.time() < end:
            screen = self.eng.tmux.capture(pid)
            st = C.classify(screen, kind, self.eng.cfg.catalog)
            reply = extract_reply(self.eng.tmux.capture(pid, history=True), kind, prompt)
            seen_echo = seen_echo or reply is not None
            if not st.composer and not st.busy:
                detail = "the agent is waiting in the terminal (permission or menu) — answer it there"
                self._set(pid, "waiting", detail)
                self._add(pid, "agent", reply or "", "AGENT_WAITING")
                return {"ok": False, "reason": "AGENT_WAITING", "detail": detail, "reply": reply or ""}
            if st.idle and seen_echo and reply:
                stable = stable + 1 if reply == last else 0
                if stable >= 1:
                    self._set(pid, "idle")
                    self._add(pid, "agent", reply, "DONE")
                    if self.log:
                        self.log("chat_reply", pane=pid, agent=kind, chars=len(reply))
                    return {"ok": True, "reason": "DONE", "reply": reply}
            last = reply
            time.sleep(1.0)
        self._set(pid, "timeout", "no complete reply yet; check the terminal")
        self._add(pid, "agent", last or "", "TIMEOUT")
        return {"ok": False, "reason": "TIMEOUT", "detail": "no complete reply before the timeout", "reply": last or ""}
