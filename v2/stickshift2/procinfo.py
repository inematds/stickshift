"""Which agent runs in a pane, proven from /proc (Linux/WSL) instead of from screen text.

pane_pid (the pane's shell) -> foreground process group (tpgid in /proc/<pid>/stat)
-> realpath of /proc/<tpgid>/exe (+ cmdline). Accepted shapes (live, 2026-09-24):

- Claude Code: native binary under .../claude/versions/<version>
- Codex: `node .../@openai/codex/bin/codex.js`, or its native `codex` binary

There is no code signature to check on Linux (macOS verifies Anthropic/OpenAI team ids);
identity here is: known install location + version + the process actually in front.
Anything else -> None (NO_AGENT).
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass


@dataclass
class Identity:
    kind: str          # "claude" | "codex"
    pid: int
    exe: str
    version: str
    sha256: str = ""


def _stat_fields(pid):
    with open("/proc/%d/stat" % pid) as f:
        data = f.read()
    # comm may contain spaces/parens: split after the LAST ')'
    rest = data[data.rindex(")") + 2:].split()
    return rest   # rest[0]=state, rest[1]=ppid, rest[2]=pgrp, rest[3]=session, rest[4]=tty_nr, rest[5]=tpgid


def foreground_pid(pane_pid):
    try:
        tpgid = int(_stat_fields(pane_pid)[5])
    except (OSError, ValueError, IndexError):
        return None
    return tpgid if tpgid > 0 else None


def parent_pid(pid):
    try:
        return int(_stat_fields(pid)[1])
    except (OSError, ValueError, IndexError):
        return None


def ancestors(pid):
    out, seen = [], set()
    while pid and pid > 1 and pid not in seen:
        seen.add(pid)
        out.append(pid)
        pid = parent_pid(pid)
    return out


def _cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


def _sha256(path, limit=512 * 1024 * 1024):
    try:
        if os.path.getsize(path) > limit:
            return ""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _codex_version(script_path):
    d = os.path.dirname(os.path.dirname(script_path))   # .../@openai/codex
    try:
        with open(os.path.join(d, "package.json")) as f:
            return json.load(f).get("version", "")
    except (OSError, ValueError):
        return ""


def identify(pane_pid, with_hash=False):
    fg = foreground_pid(pane_pid)
    if not fg:
        return None
    try:
        exe = os.path.realpath("/proc/%d/exe" % fg)
    except OSError:
        return None
    argv = _cmdline(fg)
    m = re.search(r"/claude/versions/([0-9][0-9A-Za-z.\-+]*)$", exe)
    if m:
        return Identity("claude", fg, exe, m.group(1), _sha256(exe) if with_hash else "")
    base = os.path.basename(exe)
    if base.startswith("node"):
        for a in argv[1:3]:
            script = os.path.realpath(a) if a.startswith("/") else a   # ~/.npm-global/bin/codex is a symlink
            if script.endswith("/@openai/codex/bin/codex.js"):
                return Identity("codex", fg, script, _codex_version(script),
                                _sha256(script) if with_hash else "")
        return None
    if base == "codex" and "/@openai/codex" in exe:
        return Identity("codex", fg, exe, "", _sha256(exe) if with_hash else "")
    return None


def is_self(pane_pid):
    """True when our own process runs inside this pane (never act on ourselves)."""
    return pane_pid in ancestors(os.getpid())
