"""~/.stickshift/config.toml — same file and TOML subset as the macOS app, plus the
model-catalog keys. Unsafe files are refused; unsafe values are ignored (fail closed)."""
import os
import stat

from .catalog import Catalog, token_safe

GEARS = ["1", "2", "3", "4", "5", "R", "ULTRA"]


def config_path():
    return os.environ.get("STICKSHIFT_CONFIG") or os.path.expanduser("~/.stickshift/config.toml")


def _defaults():
    # Effort ladder from the reference in inematds/modelos (guia/esforco): medium as the
    # working gear, high when more reasoning is needed, xhigh for the hard cases.
    # Claude uses opus[1m] (the 1M-context Opus 5.5), NOT opus (200K) — see catalog.py.
    return {
        "1":     {"claude": ("haiku", None),         "codex": ("gpt-6-luna", "medium")},
        "2":     {"claude": ("sonnet", None),        "codex": ("gpt-6-sol", "medium")},
        "3":     {"claude": ("opus[1m]", "medium"),  "codex": ("gpt-6-astra", "medium")},
        "4":     {"claude": ("opus[1m]", "high"),    "codex": ("gpt-6-astra", "high")},
        "5":     {"claude": ("opus[1m]", "xhigh"),   "codex": ("gpt-6-astra", "xhigh")},
        "R":     {"claude": ("opus[1m]", "low"),     "codex": ("gpt-6-sol", "low")},
        "ULTRA": {"claude": ("opus[1m]", "ultracode"), "codex": ("gpt-6-astra", "ultra")},
    }


class Config:
    def __init__(self):
        self.gears = _defaults()
        self.catalog = Catalog()
        self.dialog_policy = "ask"        # ask | confirm | cancel
        self.auto_answer = False
        self.loaded_from_file = False
        self.error = None                 # set when the file was refused

    @classmethod
    def load(cls, path=None):
        c = cls()
        path = path or config_path()
        if not os.path.exists(path):
            return c
        err = _unsafe(path)
        if err:
            c.error = err
            return c
        try:
            with open(path, encoding="utf-8") as f:
                txt = f.read()
        except OSError as e:
            c.error = "unreadable: %s" % e
            return c
        c._apply(txt)
        c.loaded_from_file = True
        return c

    def tuple_for(self, gear, kind):
        g = self.gears.get(str(gear).upper())
        return g.get(kind) if g else None

    def _apply(self, txt):
        for raw in txt.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("[") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            unq = v.strip('"')
            if k == "dialog_policy":
                self.dialog_policy = unq if unq in ("ask", "confirm", "cancel") else "ask"
            elif k == "auto_answer":
                self.auto_answer = unq == "true"
            elif self.catalog.apply(k, unq):
                pass
            elif k.startswith("gear."):
                parts = k.split(".")
                if len(parts) != 3:
                    continue
                g, agent = parts[1].upper(), parts[2]
                if g not in self.gears or agent not in ("claude", "codex"):
                    continue
                toks = unq.split()
                if not 1 <= len(toks) <= 2 or not all(token_safe(t) for t in toks):
                    continue
                self.gears[g][agent] = (toks[0], toks[1] if len(toks) == 2 else None)


def _unsafe(path):
    try:
        st = os.lstat(path)
    except OSError as e:
        return "stat failed: %s" % e
    if stat.S_ISLNK(st.st_mode):
        return "config is a symlink"
    if st.st_uid != os.getuid():
        return "config not owned by user"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "config group/world-writable"
    return None
