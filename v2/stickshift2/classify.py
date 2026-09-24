"""Pane classifier: text of a tmux pane -> what state the agent is in.

Ported from src/core/AXState.m and updated with live captures of Claude Code 2.1.282
and Codex 0.156.1 (tmux on Linux, 2026-09-24). The agent KIND comes from the pane's
process (procinfo.py), not from the text; the text only answers "what state is it in".

Everything is bottom-anchored: conversation scrollback can quote any marker, so only
the composer box and the lines around it are trusted. Unknown -> not idle (fail closed).
"""
import re
from dataclasses import dataclass

SPINNER = set("·✢✳✶✻✽✺⚹✷✵*")
CLAUDE_PLACEHOLDERS = ('Try "', "Press up to edit queued messages", "Ask Claude",
                       "Update your working directory")
CODEX_PLACEHOLDERS = {
    # Codex 0.156.1 (strings dump + live capture, 2026-09-24)
    "Ask Codex to do anything", "Ask a follow-up question",
    # Codex 0.144.1 rotation (kept for older installs)
    "Explain this codebase", "Summarize recent commits", "Implement {feature}",
    "Find and fix a bug in @filename", "Write tests for @filename",
    "Improve documentation in @filename", "Run /review on my current changes",
    "Use /skills to list available skills",
    "Check recently modified functions for compatibility",
    "How many files have been modified?", "Will this algorithm scale well?",
    "Ready. What would you like to work on?", "Ask anything",
}
CODEX_PROMPTS = ("›", "»")
_EFFORT_CHIP = re.compile(r"\s(low|medium|high|xhigh|max|ultracode|auto)\s·\s/effort")
_CODEX_FOOTER = re.compile(
    r"(gpt-[A-Za-z0-9._-]+)\s+(extra high|low|medium|high|xhigh|max|ultra)\s+(?:·\s+)?((?:/|~/)[^\n·]*)",
    re.IGNORECASE)


@dataclass
class PaneState:
    agent: str = ""              # "claude" | "codex" | "" (set by the caller from the process)
    model: str = ""              # catalog token when recognized
    model_display: str = ""      # what the screen shows (catalog display when recognized)
    effort: str = ""
    busy: bool = False
    composer: bool = False       # the input box is on screen (not a menu/permission prompt)
    input_empty: bool = False
    switch_dialog: bool = False  # Claude "Switch model?" / "Change effort level?"
    dialog_target: str = ""
    idle: bool = False
    note: str = ""               # why not idle, human readable

    def as_dict(self):
        return dict(self.__dict__)


_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def strip_ansi(text):
    return _ANSI.sub("", text)


def only_dim_after_prompt(styled_line):
    """True when every visible character after the prompt glyph is dim (SGR 2): ghost text,
    i.e. a placeholder or Claude's suggested next prompt, not something a person typed."""
    dim, seen_prompt, i = False, False, 0
    while i < len(styled_line):
        m = _SGR.match(styled_line, i)
        if m:
            for code in (m.group(1) or "0").split(";"):
                if code == "2":
                    dim = True
                elif code in ("0", "22", ""):
                    dim = False
            i = m.end()
            continue
        ch = styled_line[i]
        if styled_line[i] == "\x1b":          # other escape: skip it
            m2 = _ANSI.match(styled_line, i)
            i = m2.end() if m2 else i + 1
            continue
        if not seen_prompt:
            if ch in "❯›»":
                seen_prompt = True
        elif not ch.isspace() and ch != "\xa0" and not dim:
            return False
        i += 1
    return seen_prompt


def _rule(line):
    s = line.strip()
    return len(s) >= 4 and s[:4] == "────"


def _lines(text):
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def classify(text, agent, catalog, styled=None):
    """`text` is plain pane text; pass `styled` (capture-pane -e) to recognize ghost text
    in the input box by its dim attribute instead of by vocabulary alone."""
    st = PaneState(agent=agent or "")
    if styled is not None:
        text = strip_ansi(styled)
    if not text or not agent:
        st.note = "no agent in this pane"
        return st
    lines = _lines(text)
    slines = styled.split("\n")[:len(lines)] if styled is not None else None
    bottom = lines[-16:]
    if agent == "claude":
        _claude(st, lines, bottom, catalog, slines)
    elif agent == "codex":
        _codex(st, lines, bottom, catalog, slines)
    return st


# --- Claude Code --------------------------------------------------------------------------
def _claude(st, lines, bottom, catalog, slines=None):
    # Switch-confirm dialogs (title + both options as separate short lines).
    d_title = d_yes = d_no = False
    for ln in bottom:
        s = ln.strip().lstrip("❯").strip()
        s = re.sub(r"^[12]\.\s*", "", s)
        if s in ("Switch model?", "Change effort level?"):
            d_title = True
        elif s.startswith("Yes, switch to"):
            d_yes = True
            st.dialog_target = s[len("Yes, switch to"):].strip()
        elif s == "No, go back":
            d_no = True
    st.switch_dialog = d_title and d_yes and d_no

    # Composer box: rule / "❯ <input>" / rule, searched from the bottom.
    ci = None
    for i in range(len(lines) - 2, 0, -1):
        if lines[i].lstrip().startswith("❯") and _rule(lines[i - 1]) and _rule(lines[i + 1]):
            ci = i
            break
        if len(lines) - i > 40:
            break
    if ci is not None:
        st.composer = True
        rest = lines[ci].strip()[1:].strip()
        st.input_empty = (rest == "" or rest.startswith(CLAUDE_PLACEHOLDERS)
                          or bool(slines and ci < len(slines) and only_dim_after_prompt(slines[ci])))
        above = [l for l in lines[max(0, ci - 12):ci - 1] if l.strip()][-6:]
        for l in above:
            s = l.strip()
            if s and s[0] in SPINNER and "…" in s:
                st.busy = True
        status = "\n".join(lines[ci + 2:])
        m = _EFFORT_CHIP.search(" " + status)
        if m:
            st.effort = m.group(1)
        e = catalog.find_in_text("claude", status)
        if e:
            st.model, st.model_display = e.token, e.display
            if not st.effort:     # custom statuslines often print "<Model>/<effort>"
                m2 = re.search(re.escape(e.display) + r"/(low|medium|high|xhigh|max|ultracode|auto)\b", status)
                if m2:
                    st.effort = m2.group(1)
    if any("esc to interrupt" in l for l in bottom):
        st.busy = True
    _finish(st)


# --- Codex --------------------------------------------------------------------------------
def _codex(st, lines, bottom, catalog, slines=None):
    fi = None
    for i in range(len(lines) - 1, max(-1, len(lines) - 8), -1):
        m = _CODEX_FOOTER.search(lines[i])
        if m:
            fi = i
            raw = m.group(1)
            e = catalog.entry("codex", raw)
            st.model = e.token if e else raw.lower()
            st.model_display = e.display if e else raw
            eff = m.group(2).lower()
            st.effort = "xhigh" if eff == "extra high" else eff
            break
    if fi is not None:
        for i in range(fi - 1, max(-1, fi - 4), -1):
            s = lines[i].strip()
            if s[:1] in CODEX_PROMPTS:        # "›" normally, "»" in Ultra mode (0.156.1)
                rest = s[1:].strip()
                if re.match(r"^\d+\.\s", rest):       # a picker row, not the composer
                    break
                st.composer = True
                st.input_empty = (rest == "" or rest in CODEX_PLACEHOLDERS
                                  or bool(slines and i < len(slines) and only_dim_after_prompt(slines[i])))
                break
    st.busy = any(("• Working" in l or "esc to interrupt" in l) for l in bottom)
    _finish(st)


def _finish(st):
    if st.switch_dialog:
        st.note = "a switch dialog is open"
    elif not st.composer:
        st.note = "the agent is waiting in the terminal (menu, prompt or permission request)"
    elif st.busy:
        st.note = "the agent is working"
    st.idle = st.composer and not st.busy and not st.switch_dialog


# --- helpers used by the engine and the chat ------------------------------------------------
def set_model_targets(text):
    """Every 'Set model to X' target in the text, in order (Claude)."""
    out = []
    for ln in text.split("\n"):
        i = ln.find("Set model to ")
        if i < 0:
            continue
        x = ln[i + len("Set model to "):].strip()
        x = re.split(r" and saved\b| \(saved\b", x)[0].strip()
        out.append(x)
    return out


def set_effort_targets(text):
    out = []
    for ln in text.split("\n"):
        i = ln.find("Set effort level to ")
        if i >= 0:
            out.append(ln[i + len("Set effort level to "):].split()[0].strip(":,."))
    return out


def codex_changes(text):
    """Every 'Model changed to <slug> [effort]' in the text (Codex)."""
    out = []
    for ln in text.split("\n"):
        i = ln.find("Model changed to ")
        if i >= 0:
            out.append(ln[i + len("Model changed to "):].strip())
    return out


def picker_row(label, text):
    """Row number of `label` in a Codex picker ('N. <label> …'), 0 if absent.
    Case-insensitive, full-token (gpt-5.4 never selects gpt-5.4-mini)."""
    want = label.lower()
    for raw in text.split("\n"):
        ln = raw.strip()
        if ln.startswith("›"):
            ln = ln[1:].strip()
        m = re.match(r"^(\d{1,2})\.\s+(.*)$", ln)
        if not m:
            continue
        rest = m.group(2).lower()
        if rest.startswith(want) and (len(rest) == len(want) or rest[len(want)] in " \t("):
            return int(m.group(1))
    return 0


def tail(text, n):
    """Last n non-trailing-blank lines (panes are padded with empty rows at the bottom)."""
    return "\n".join(_lines(text)[-n:])


def occurrences(needle, text):
    return text.count(needle) if needle else 0
