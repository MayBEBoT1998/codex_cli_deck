"""Drag/drop grants must stay directional and use normal revocation semantics."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from codex_deck.coordination import Coordinator
from codex_deck.relationships import connect_drop, drag_payload, relation_edges, set_relationship


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.manager = Coordinator(root)
        for name in "abcd":
            self.manager.register(SimpleNamespace(id=name, name=name, cwd="/tmp/project"), root / name)
            self.manager.handle_event(name, {"type": "agent-starting", "epoch": name})
            self.manager.handle_event(name, {"type": "agent-bound", "epoch": name,
                                              "thread_id": "thread-" + name, "status": {"type": "idle"}})

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def test_drop_connects_only_requested_direction_and_keeps_other_relationships(self):
        set_relationship(self.manager, "a", "c")
        set_relationship(self.manager, "c", "d")
        payload = drag_payload(self.manager, "a", "panel")
        self.assertEqual(connect_drop(self.manager, payload, "b", "panel"), "a")
        revision = self.manager.revision
        connect_drop(self.manager, payload, "b", "panel")
        self.assertEqual(self.manager.revision, revision)
        self.assertEqual(relation_edges(self.manager), [("a", "b"), ("a", "c"), ("c", "d")])
        self.assertFalse(self.manager.grants["b"])

    def test_any_agent_can_initiate_and_reverse_link_is_explicit(self):
        set_relationship(self.manager, "a", "b")
        connect_drop(self.manager, drag_payload(self.manager, "b", "panel"), "a", "panel")
        self.assertEqual(relation_edges(self.manager), [("a", "b"), ("b", "a")])

    def test_remove_cancels_queued_work_but_keeps_running_work_and_other_grants(self):
        set_relationship(self.manager, "a", "b")
        set_relationship(self.manager, "a", "c")
        queued = self.manager.dispatch("a", "b", "read project", "one")
        running = self.manager.dispatch("a", "b", "existing work", "two")
        running.status = "running"
        set_relationship(self.manager, "a", "b", False)
        self.assertEqual(queued.status, "cancelled")
        self.assertEqual(running.status, "running")
        self.assertEqual(self.manager.grants["a"], {"c"})

    def test_drop_from_another_window_or_stale_session_is_rejected(self):
        payload = drag_payload(self.manager, "a", "panel")
        with self.assertRaises(ValueError):
            connect_drop(self.manager, payload, "b", "other-panel")
        self.manager.agents["a"].thread = "changed"
        with self.assertRaises(ValueError):
            connect_drop(self.manager, payload, "b", "panel")
        self.assertFalse(relation_edges(self.manager))

    def test_self_closed_and_same_thread_targets_are_rejected(self):
        self.manager.agents["b"].thread = self.manager.agents["a"].thread
        for target in ("a", "b", "closed"):
            with self.assertRaises(ValueError):
                set_relationship(self.manager, "a", target)
        self.assertFalse(relation_edges(self.manager))


if __name__ == "__main__":
    unittest.main()
