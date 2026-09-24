"""StickShift v2 tests. Run: python3 -m unittest discover -s v2/tests -t v2

Fixtures are live captures (tmux, 2026-09-24: Claude Code 2.1.282, Codex 0.156.1),
trimmed to the lines the tests need and anonymized (no user, host, paths or usage)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stickshift2 import classify as C                     # noqa: E402
from stickshift2.catalog import Catalog, display_matches  # noqa: E402
from stickshift2.chat import extract_reply, validate       # noqa: E402
from stickshift2.config import Config                      # noqa: E402
from stickshift2.engine import plan                        # noqa: E402

RULE = "─" * 50
CLAUDE_IDLE = "\n".join([
    "● pronto", "✻ Brewed for 1s · done 8:31 PM", RULE, "❯ ", RULE,
    "  user@host:~/proj Opus 5.5 (1M context)/medium 5h:1% w:2%",
    "  ⏵⏵ auto mode on (shift+tab to cycle)", "", "", ""])
CLAUDE_BUSY = "\n".join([
    "❯ Responda apenas com a palavra: pronto", "· Pontificating…",
    "  ⎿  Tip: something", RULE, "❯ ", RULE, "  user@host:~/proj Sonnet 5/high"])
CLAUDE_AFTER_SWITCH = "\n".join([
    "❯ /model sonnet", "  ⎿  Set model to Sonnet 5 and saved as your default for new sessions",
    RULE, "❯ ", RULE, "  user@host:~/proj Sonnet 5/high            ● high · /effort"])
CLAUDE_GHOST_STYLED = "\n".join([
    "● 8 × 9 = 72", "✻ Churned for 1s · done 8:43 PM", RULE,
    "\x1b[39m❯\xa0\x1b[2mResponda em uma linha: quanto é 12 vezes 12?\x1b[0m", RULE,
    "  user@host:~/proj Opus 5.5 (1M context)/medium"])
CLAUDE_DRAFT_STYLED = CLAUDE_GHOST_STYLED.replace("\x1b[2m", "")
CLAUDE_DIALOG = "\n".join([
    "  Switch model?", "  This conversation is cached for the current model.",
    "  ❯ 1. Yes, switch to Opus 5.5 (1M context)", "    2. No, go back"])
CODEX_IDLE = "\n".join([
    "• pronto", "  8:32 PM", "› Ask Codex to do anything",
    "  GPT-6-Astra medium · ~/proj · Responder pronto                ⚠ 3 warnings · f2 to view", "", ""])
CODEX_BUSY = "\n".join([
    "› Responda apenas com a palavra: pronto", "• Working (1s • esc to interrupt)",
    "› Ask Codex to do anything", "  GPT-6-Astra medium · ~/proj · ⠹"])
CODEX_ULTRA = "\n".join([
    "• Model changed to gpt-6-astra ultra for this conversation",
    "» Ask Codex to do anything", "  GPT-6-Astra ultra · ~/proj"])
CODEX_PICKER = "\n".join([
    "  Select Model and Effort",
    "  1. GPT-6-Sol (default)    Workhorse model for coding and everyday work.",
    "› 2. GPT-6-Astra (current)  Frontier intelligence for the most demanding work.",
    "  3. GPT-6-Luna             Fast and affordable model for easier tasks.",
    "  4. GPT-5.6-Sol            Older coding model for complex work.",
    "  7. GPT-5.5                Legacy coding model.", "  enter select · esc back"])
CODEX_EFFORT = "\n".join([
    "  Select Reasoning Level for GPT-6-Astra",
    "  1. Low                         Fast responses with lighter reasoning",
    "› 2. Medium (default) (current)  Balances speed and reasoning depth",
    "  4. Extra high                  Extra high reasoning depth",
    "  5. More reasoning…             Max and Ultra consume usage limits faster"])
CODEX_ADVANCED = "\n".join([
    "  Advanced Reasoning", "› 1. Max    For difficult problems", "  2. Ultra  For demanding work"])


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.c = Catalog()

    def test_boundary(self):
        self.assertTrue(display_matches("Opus 5.5 (1M context)", "Opus 5.5"))
        self.assertTrue(display_matches("Sonnet 5/high", "Sonnet 5"))
        self.assertFalse(display_matches("Opus 5.5", "Opus 5"))
        self.assertFalse(display_matches("Fable 5.1", "Fable 5"))

    def test_opus_1m_is_its_own_model(self):
        self.assertEqual(self.c.find_in_text("claude", "x Opus 5.5 (1M context)/medium").token, "opus[1m]")
        self.assertEqual(self.c.find_in_text("claude", "x Opus 5.5/medium").token, "opus")
        self.assertIsNone(self.c.find_in_text("claude", "x Opus 5/medium"))

    def test_codex_efforts(self):
        self.assertTrue(self.c.accepts("codex", "gpt-6-astra", "ultra"))
        self.assertFalse(self.c.accepts("codex", "gpt-6-luna", "ultra"))
        self.assertFalse(self.c.accepts("codex", "gpt-5.5", "max"))
        self.assertEqual(self.c.entry("codex", "GPT-6-Astra").token, "gpt-6-astra")

    def test_overlay(self):
        self.assertTrue(self.c.apply("claude_model.mythos", "Mythos 1"))
        self.assertEqual(self.c.entry("claude", "mythos").display, "Mythos 1")
        self.c.apply("claude_model.evil", "x');alert(1)//")
        self.assertIsNone(self.c.entry("claude", "evil"))
        self.c.apply("codex_model.gpt-7-nova", "low medium high")
        self.assertEqual(self.c.efforts("codex", "gpt-7-nova"), ["low", "medium", "high"])
        self.c.apply("codex_model.gpt-7-bad", "low turbo")
        self.assertIsNone(self.c.entry("codex", "gpt-7-bad"))
        self.assertFalse(self.c.apply("dialog_policy", "ask"))


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.c = Catalog()

    def test_claude_idle(self):
        s = C.classify(CLAUDE_IDLE, "claude", self.c)
        self.assertTrue(s.idle and s.input_empty and not s.busy)
        self.assertEqual((s.model, s.effort), ("opus[1m]", "medium"))

    def test_claude_busy_without_esc_to_interrupt(self):
        s = C.classify(CLAUDE_BUSY, "claude", self.c)
        self.assertTrue(s.busy)
        self.assertFalse(s.idle)

    def test_claude_effort_chip(self):
        s = C.classify(CLAUDE_AFTER_SWITCH, "claude", self.c)
        self.assertEqual((s.model, s.effort), ("sonnet", "high"))

    def test_claude_ghost_suggestion_is_empty(self):
        s = C.classify(None, "claude", self.c, styled=CLAUDE_GHOST_STYLED)
        self.assertTrue(s.input_empty, "dim suggested prompt must count as empty")
        s2 = C.classify(None, "claude", self.c, styled=CLAUDE_DRAFT_STYLED)
        self.assertFalse(s2.input_empty, "the same text typed (not dim) is a draft")

    def test_claude_dialog_not_idle(self):
        s = C.classify(CLAUDE_DIALOG, "claude", self.c)
        self.assertTrue(s.switch_dialog)
        self.assertEqual(s.dialog_target, "Opus 5.5 (1M context)")
        self.assertFalse(s.idle)

    def test_codex_states(self):
        s = C.classify(CODEX_IDLE, "codex", self.c)
        self.assertTrue(s.idle and s.input_empty)
        self.assertEqual((s.model, s.effort), ("gpt-6-astra", "medium"))
        self.assertTrue(C.classify(CODEX_BUSY, "codex", self.c).busy)
        u = C.classify(CODEX_ULTRA, "codex", self.c)
        self.assertTrue(u.idle and u.effort == "ultra", "» prompt in Ultra mode")
        self.assertFalse(C.classify(CODEX_PICKER, "codex", self.c).idle)

    def test_picker_rows(self):
        self.assertEqual(C.picker_row("GPT-6-Astra", CODEX_PICKER), 2)
        self.assertEqual(C.picker_row("gpt-6-sol", CODEX_PICKER), 1)
        self.assertEqual(C.picker_row("Extra high", CODEX_EFFORT), 4)
        self.assertEqual(C.picker_row("More reasoning…", CODEX_EFFORT), 5)
        self.assertEqual(C.picker_row("Max", CODEX_EFFORT), 0)
        self.assertEqual(C.picker_row("Ultra", CODEX_ADVANCED), 2)

    def test_confirmation_parsing(self):
        t = ("  ⎿  Set model to Opus 5.5 and saved as your default for new sessions\n"
             "  ⎿  Set model to Opus 5.5 (1M context) and saved as your default for new sessions\n"
             "  ⎿  Set effort level to high (saved as your default for new sessions): x\n")
        self.assertEqual(C.set_model_targets(t), ["Opus 5.5", "Opus 5.5 (1M context)"])
        self.assertEqual(C.set_effort_targets(t), ["high"])
        self.assertEqual(C.codex_changes(CODEX_ULTRA), ["gpt-6-astra ultra for this conversation"])


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.c = Catalog()

    def test_claude_effort_only(self):
        self.assertEqual(plan("claude", "opus[1m]", "high", self.c, current_model="opus[1m]"),
                         [("text", "/effort high"), ("enter",), ("verify_effort", "high")])

    def test_claude_model_and_effort(self):
        p = plan("claude", "opus[1m]", "medium", self.c, current_model="sonnet")
        self.assertEqual(p[0], ("text", "/model opus[1m]"))
        self.assertIn(("verify_model", "Opus 5.5 (1M context)"), p)

    def test_codex_ultra_goes_through_submenu(self):
        p = plan("codex", "gpt-6-astra", "ultra", self.c)
        self.assertIn(("select", "More reasoning…", "effort"), p)
        self.assertIn(("wait", "Advanced Reasoning"), p)
        self.assertEqual(p[-1], ("verify_codex", "gpt-6-astra", "ultra"))
        self.assertNotIn(("select", "More reasoning…", "effort"), plan("codex", "gpt-6-astra", "high", self.c))

    def test_unknown_model(self):
        self.assertIsNone(plan("codex", "gpt-9", "low", self.c))


class TestChat(unittest.TestCase):
    def test_validate(self):
        self.assertIsNone(validate("diga olá")[1])
        self.assertIsNone(validate("-n não é opção")[1])
        for bad in ("/model x", "  /compact", "!rm -rf /", "# lembrar", "a\nb", "a\rb", "a\x1bb", "", "x" * 2001):
            self.assertIsNotNone(validate(bad)[1], repr(bad[:10]))

    def test_extract(self):
        claude = "❯ Qual a capital?\n● Montevidéu\n✻ Brewed for 1s · done 8:31 PM\n" + RULE + "\n❯ \n" + RULE + "\n  status"
        self.assertEqual(extract_reply(claude, "claude", "Qual a capital?"), "Montevidéu")
        codex = "› Qual a capital?\n• Santiago.\n  8:32 PM\n› Ask Codex to do anything\n  GPT-6-Astra medium · ~/p"
        self.assertEqual(extract_reply(codex, "codex", "Qual a capital?"), "Santiago.")
        self.assertIsNone(extract_reply(codex, "codex", "outra pergunta"))


class TestConfig(unittest.TestCase):
    def test_defaults_follow_the_ladder(self):
        c = Config()
        self.assertEqual(c.tuple_for("3", "claude"), ("opus[1m]", "medium"))
        self.assertEqual(c.tuple_for("5", "codex"), ("gpt-6-astra", "xhigh"))
        for g in c.gears:
            for kind in ("claude", "codex"):
                m, e = c.tuple_for(g, kind)
                self.assertIsNotNone(plan(kind, m, e, c.catalog), (g, kind))
                self.assertTrue(c.catalog.accepts(kind, m, e), (g, kind))

    def test_file_overlay_and_safety(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.toml")
            with open(p, "w") as f:
                f.write('gear.3.claude = "sonnet high"\ngear.4.codex = "bad;x high"\n'
                        'claude_model.mythos = "Mythos 1"\ndialog_policy = "confirm"\nauto_answer = true\n')
            os.chmod(p, 0o600)
            c = Config.load(p)
            self.assertEqual(c.tuple_for("3", "claude"), ("sonnet", "high"))
            self.assertEqual(c.tuple_for("4", "codex"), ("gpt-6-astra", "high"))
            self.assertIsNotNone(c.catalog.entry("claude", "mythos"))
            self.assertEqual((c.dialog_policy, c.auto_answer), ("confirm", True))
            os.chmod(p, 0o666)
            self.assertIsNotNone(Config.load(p).error, "world-writable config is refused")


class _FakeTmux:
    def panes(self):
        return []

    def pane(self, _):
        return None


class TestWebGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from stickshift2.engine import Engine
        from stickshift2.log import Log
        from stickshift2.web import build
        cls.tmp = tempfile.TemporaryDirectory()
        log = Log(os.path.join(cls.tmp.name, "v2.log"))
        eng = Engine(_FakeTmux(), Config(), log.write)
        cls.app, cls.srv = build(eng, log, port=0)
        cls.port = cls.srv.server_address[1]
        cls.app.port = cls.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def req(self, path, headers=None, body=None):
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), headers=headers or {},
                                   data=json.dumps(body).encode() if body is not None else None,
                                   method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=5) as x:
                return x.status, json.loads(x.read() or b"null") if "api" in path else x.read()
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_token_required_everywhere(self):
        for p in ("/", "/gearbox", "/api/panes", "/api/state?pane=%250", "/api/chat?pane=%250", "/api/profiles"):
            self.assertEqual(self.req(p)[0], 401, p)
        self.assertEqual(self.req("/api/panes", {"X-StickShift-Token": "wrong"})[0], 401)

    def test_host_and_origin(self):
        t = {"X-StickShift-Token": self.app.token}
        self.assertEqual(self.req("/api/panes", dict(t, Host="evil.example:%d" % self.port))[0], 403)
        self.assertEqual(self.req("/api/shift", dict(t, Origin="http://evil.example"), {})[0], 403)
        self.assertEqual(self.req("/api/panes", t)[0], 200)

    def test_no_cors_and_chat_rejects_commands(self):
        t = {"X-StickShift-Token": self.app.token, "Content-Type": "application/json"}
        code, r = self.req("/api/chat", t, {"pane": "%0", "text": "!whoami"})
        self.assertEqual((code, r["reason"]), (200, "BAD_INPUT"))
        r = urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:%d/api/profiles" % self.port, headers=t), timeout=5)
        self.assertIsNone(r.headers.get("Access-Control-Allow-Origin"))

    def test_log_is_private(self):
        self.app.log.write("probe", x=1)
        self.assertEqual(os.stat(self.app.log.path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
