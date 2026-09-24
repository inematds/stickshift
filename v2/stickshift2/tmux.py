"""tmux backend: list panes, read their text, type into them. argv only, never a shell."""
import os
import shutil
import subprocess

_FMT = "\t".join(["#{pane_id}", "#{session_name}", "#{window_index}", "#{pane_index}",
                  "#{pane_pid}", "#{pane_current_path}", "#{pane_active}", "#{window_active}",
                  "#{session_attached}", "#{client_activity}"])


class TmuxError(RuntimeError):
    pass


class Tmux:
    def __init__(self, socket=None):
        self.bin = shutil.which("tmux")
        self.socket = socket or os.environ.get("STICKSHIFT_TMUX_SOCKET") or None

    def _run(self, args, check=True, timeout=5):
        if not self.bin:
            raise TmuxError("tmux not installed")
        cmd = [self.bin] + (["-L", self.socket] if self.socket else []) + args
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise TmuxError((r.stderr or r.stdout).strip() or "tmux failed")
        return r.stdout

    def available(self):
        try:
            self._run(["list-sessions"])
            return True
        except (TmuxError, subprocess.SubprocessError, OSError):
            return False

    def panes(self):
        try:
            out = self._run(["list-panes", "-a", "-F", _FMT])
        except (TmuxError, subprocess.SubprocessError, OSError):
            return []
        res = []
        for ln in out.splitlines():
            p = ln.split("\t")
            if len(p) < 9:
                continue
            res.append({
                "id": p[0], "session": p[1], "window": p[2], "pane": p[3],
                "pid": int(p[4]) if p[4].isdigit() else 0, "cwd": p[5],
                "active": p[6] == "1" and p[7] == "1", "attached": p[8] not in ("", "0"),
                "name": "%s:%s.%s" % (p[1], p[2], p[3]),
            })
        return res

    def pane(self, pane_id):
        for p in self.panes():
            if p["id"] == pane_id or p["name"] == pane_id:
                return p
        return None

    def capture(self, pane_id, history=False, styled=False):
        args = ["capture-pane", "-p", "-J", "-t", pane_id]
        if styled:
            args.insert(2, "-e")                # keep SGR attributes (dim ghost text)
        if history:
            args += ["-S", "-"]
        return self._run(args)

    def send_literal(self, pane_id, text):
        # "--" so a text starting with "-" is never parsed as a tmux option
        self._run(["send-keys", "-t", pane_id, "-l", "--", text])

    def send_key(self, pane_id, key):
        if key not in ("Enter", "Escape"):
            raise TmuxError("key not allowed: %r" % key)
        self._run(["send-keys", "-t", pane_id, key])
