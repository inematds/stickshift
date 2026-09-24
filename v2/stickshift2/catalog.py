"""Model catalog: which models exist, how each is named on screen, which efforts it takes.

Same idea as src/core/Models.m on the macOS branch, verified against the live TUIs on
Linux (tmux capture, 2026-09-24):

- Claude Code 2.1.282: `/model opus` -> "Set model to Opus 5.5" (200K context) and
  `/model opus[1m]` -> "Set model to Opus 5.5 (1M context)". They are different models,
  so both are catalog entries.
- Codex 0.156.1: the footer and picker show display names ("GPT-6-Astra"), while
  "Model changed to gpt-6-astra medium" uses the slug. Entries keep both.

config.toml can add or override entries (see config.py):
    claude_model.<token> = "<display>"
    codex_model.<slug>   = "<efforts>"          (display defaults to the slug)
"""
import re
from dataclasses import dataclass, field

CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultracode"]
CODEX_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
ALL_EFFORTS = {
    "claude": set(CLAUDE_EFFORTS) | {"auto"},
    "codex": set(CODEX_EFFORTS) | {"ultra"},
}
_TOKEN_OK = re.compile(r"^[A-Za-z0-9._\[\]-]+$")      # becomes keystrokes
_DISPLAY_OK = re.compile(r"^[A-Za-z0-9 .()\-]{1,48}$")  # reaches the web page


def token_safe(s):
    return bool(s) and bool(_TOKEN_OK.match(s))


def display_safe(s):
    return bool(s) and bool(_DISPLAY_OK.match(s))


def display_matches(raw, expect):
    """True when `raw` is `expect`, optionally followed by decoration.

    The character after the name must not be a letter, digit or '.', so
    "Opus 5.5 (1M context)" and "Sonnet 5/high" match their names, but
    "Opus 5.5" never matches "Opus 5" and "Fable 5.1" never matches "Fable 5".
    """
    if not raw or not expect:
        return False
    if len(raw) < len(expect) or raw[: len(expect)].lower() != expect.lower():
        return False
    if len(raw) == len(expect):
        return True
    nxt = raw[len(expect)]
    return not (nxt.isalnum() or nxt == ".")


@dataclass
class Entry:
    token: str
    display: str
    efforts: list = field(default_factory=list)   # empty -> agent base list


_TO_MAX = ["low", "medium", "high", "xhigh", "max"]
_TO_ULTRA = _TO_MAX + ["ultra"]


def _defaults():
    return {
        "claude": [
            Entry("haiku", "Haiku 4.5"),
            Entry("sonnet", "Sonnet 5"),
            Entry("opus[1m]", "Opus 5.5 (1M context)"),
            Entry("opus", "Opus 5.5"),
            Entry("fable", "Fable 5.1"),
        ],
        "codex": [
            Entry("gpt-6-luna", "GPT-6-Luna", list(_TO_MAX)),
            Entry("gpt-6-sol", "GPT-6-Sol", list(_TO_ULTRA)),
            Entry("gpt-6-astra", "GPT-6-Astra", list(_TO_ULTRA)),
            Entry("gpt-5.6-sol", "GPT-5.6-Sol", list(_TO_ULTRA)),
            Entry("gpt-5.6-terra", "GPT-5.6-Terra", list(_TO_ULTRA)),
            Entry("gpt-5.5", "GPT-5.5", ["low", "medium", "high", "xhigh"]),
            Entry("gpt-5.6-luna", "GPT-5.6-Luna", list(_TO_MAX)),
        ],
    }


class Catalog:
    def __init__(self):
        self._m = _defaults()

    def entries(self, kind):
        return list(self._m.get(kind, []))

    def entry(self, kind, token_or_display):
        if not token_or_display:
            return None
        k = token_or_display.lower()
        for e in self._m.get(kind, []):
            if e.token.lower() == k or e.display.lower() == k:
                return e
        return None

    def efforts(self, kind, token):
        e = self.entry(kind, token)
        if e and e.efforts:
            return list(e.efforts)
        return list(CLAUDE_EFFORTS if kind == "claude" else CODEX_EFFORTS)

    def accepts(self, kind, token, effort):
        if not effort:
            return True
        return effort in self.efforts(kind, token)

    def canonical(self, kind, raw):
        """Longest catalog display that `raw` starts with (boundary-checked), or None."""
        best = None
        for e in self._m.get(kind, []):
            if display_matches(raw, e.display) and (best is None or len(e.display) > len(best.display)):
                best = e
        return best

    def find_in_text(self, kind, text):
        """Best catalog entry named anywhere in `text` (status lines), longest match wins."""
        best = None
        low = text.lower()
        for e in self._m.get(kind, []):
            start = 0
            d = e.display.lower()
            while True:
                i = low.find(d, start)
                if i < 0:
                    break
                prev_ok = i == 0 or not (text[i - 1].isalnum() or text[i - 1] == ".")
                if prev_ok and display_matches(text[i:], e.display):
                    if best is None or len(e.display) > len(best.display):
                        best = e
                    break
                start = i + 1
        return best

    # --- config overlay -------------------------------------------------------------
    def apply(self, key, value):
        """Handle claude_model.* / codex_model.* lines. Returns False if not a model key."""
        if key.startswith("claude_model."):
            kind, token = "claude", key[len("claude_model."):]
        elif key.startswith("codex_model."):
            kind, token = "codex", key[len("codex_model."):]
        else:
            return False
        if not token_safe(token):
            return True                                   # handled: ignored
        if kind == "claude":
            if not display_safe(value):
                return True
            self._upsert(kind, Entry(token, value))
        else:
            effs = value.split()
            if any(e not in ALL_EFFORTS["codex"] for e in effs):
                return True
            self._upsert(kind, Entry(token, token, effs))
        return True

    def _upsert(self, kind, entry):
        lst = self._m[kind]
        for i, e in enumerate(lst):
            if e.token.lower() == entry.token.lower():
                lst[i] = entry
                return
        lst.append(entry)

    # --- gearbox profiles (same shape the macOS app pushes) ---------------------------
    def profiles(self):
        def eff_label(kind, t):
            return "extra high" if (kind == "codex" and t == "xhigh") else t

        out = {}
        gates = ["1", "2", "3", "4", "5", "R"]
        for kind, name in (("claude", "Claude Code"), ("codex", "Codex")):
            ents = self._m[kind]
            models = [{"gate": g, "token": e.token, "label": e.display.replace(" (1M context)", " 1M")}
                      for g, e in zip(gates, ents)]
            base = CLAUDE_EFFORTS if kind == "claude" else CODEX_EFFORTS
            efforts = [{"token": t, "label": eff_label(kind, t)} for t in base]
            me = {}
            for e in ents:
                if e.efforts:
                    me[e.token] = [{"token": t, "label": eff_label(kind, t)} for t in e.efforts]
            out[kind] = {"name": name, "models": models, "efforts": efforts, "modelEfforts": me}
        return out
