import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from codex_deck.core import ROOT, Session, Settings, drain_events, emit_event, prepare_session


class CompletionTests(unittest.TestCase):
    def test_only_completion_not_output_or_bell_marks_unread(self):
        session = Session("test", "/tmp")
        for event in ({"type": "output", "data": "Done!"}, {"type": "bell"}, {"type": "codex-start"}):
            self.assertFalse(session.apply(event))
            self.assertFalse(session.unread)
        self.assertTrue(session.codex_running)
        self.assertTrue(session.apply({"type": "agent-turn-complete", "turn-id": "one", "last-assistant-message": "完成"}))
        self.assertTrue(session.unread)
        self.assertEqual(session.summary, "完成")

    def test_duplicate_turn_does_not_alert_again_after_acknowledgement(self):
        session = Session("test", "/tmp")
        event = {"type": "agent-turn-complete", "turn-id": "one"}
        session.apply(event)
        session.unread = False
        self.assertFalse(session.apply(event))
        self.assertFalse(session.unread)
        self.assertEqual(session.turns, 1)
        self.assertTrue(session.apply(dict(event, **{"turn-id": "two"})))

    def test_completion_survives_codex_exit(self):
        session = Session("test", "/tmp")
        session.apply({"type": "codex-start"})
        session.apply({"type": "agent-turn-complete"})
        session.apply({"type": "codex-exit"})
        self.assertFalse(session.codex_running)
        self.assertTrue(session.unread)
        self.assertEqual(session.status, "完成 · 待查看")

    def test_event_queues_are_isolated_and_single_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = Path(directory) / "one", Path(directory) / "two"
            one.mkdir()
            two.mkdir()
            emit_event(one, {"type": "agent-turn-complete", "turn-id": "1"})
            self.assertEqual(drain_events(two), [])
            self.assertEqual(len(drain_events(one)), 1)
            self.assertEqual(drain_events(one), [])

    def test_partial_malformed_and_oversized_events_do_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "partial.tmp").write_text("{")
            (path / "broken.json").write_text("{")
            (path / "array.json").write_text("[]")
            (path / "huge.json").write_text(" " * 70000)
            self.assertEqual(drain_events(directory), [])
            self.assertTrue((path / "partial.tmp").exists())

    def test_callback_uses_official_payload_and_ignores_other_events(self):
        with tempfile.TemporaryDirectory() as directory:
            callback = ["/usr/bin/python3", str(ROOT / "scripts" / "notify.py"), directory]
            payload = {"type": "agent-turn-complete", "thread-id": "thread", "turn-id": "turn", "last-assistant-message": "中文完成 " * 1000}
            subprocess.run(callback + [json.dumps(payload)], check=True)
            events = drain_events(directory)
            self.assertEqual(events[0]["turn-id"], "turn")
            self.assertEqual(len(events[0]["last-assistant-message"]), 4000)
            subprocess.run(callback + [json.dumps({"type": "approval-requested"})], check=True)
            self.assertEqual(drain_events(directory), [])
            self.assertEqual(subprocess.run(callback + ["{"], capture_output=True).returncode, 2)

    def test_closed_session_callback_is_harmless(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "closed"
            self.assertFalse(emit_event(missing, {"type": "agent-turn-complete"}))
            self.assertFalse(missing.exists())


class WrapperTests(unittest.TestCase):
    def test_real_wrapper_passes_notify_and_arguments_without_shell_expansion(self):
        with tempfile.TemporaryDirectory(prefix="deck test '") as directory:
            path = Path(directory)
            fake = path / "fixture-codex"
            fake.write_text('''#!/usr/bin/python3
import json, subprocess, sys
assert sys.argv[1] == '-c'
callback = json.loads(sys.argv[2].split('=', 1)[1])
assert sys.argv[3:] == ['resume', '$(touch should-not-exist)', '中文']
subprocess.run(callback + [json.dumps({'type':'agent-turn-complete', 'turn-id':'fixture', 'last-assistant-message':'完成'})], check=True)
sys.exit(7)
''')
            fake.chmod(0o700)
            session = Session("test", directory)
            prepare_session(path / "session", session, str(fake), clean_shell=True)
            wrapper = path / "session" / "bin" / "codex"
            result = subprocess.run([str(wrapper), "resume", "$(touch should-not-exist)", "中文"], cwd=directory, capture_output=True)
            self.assertEqual(result.returncode, 7, result.stderr.decode())
            events = drain_events(path / "session" / "events")
            self.assertEqual([e["type"] for e in events], ["codex-start", "agent-turn-complete", "codex-exit"])
            self.assertFalse((path / "should-not-exist").exists())

    def test_missing_cli_returns_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session("test", directory)
            prepare_session(directory, session, None, clean_shell=True)
            result = subprocess.run([str(Path(directory) / "bin" / "codex")], capture_output=True)
            self.assertEqual(result.returncode, 127)
            self.assertIn("未找到 Codex", result.stderr.decode())


class SettingsTests(unittest.TestCase):
    def test_only_metadata_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            session = Session("中文终端", "/tmp", avatar="portrait-02")
            session.unread = True
            session.summary = "private output"
            settings = Settings(path)
            settings.save([session])
            saved = Settings(path).values["sessions"][0]
            self.assertEqual(saved, {"name": "中文终端", "cwd": "/tmp", "avatar": "portrait-02"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_settings_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for data in ("{", "[]", '{"font_size":"oops","sessions":null,"animate":{}}'):
                path.write_text(data)
                settings = Settings(path)
                self.assertEqual(settings.values["font_size"], 12)
                self.assertEqual(settings.values["sessions"], [])


if __name__ == "__main__":
    unittest.main()
