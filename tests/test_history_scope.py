"""History browsing must be scoped on the wire, including auxiliary pickers."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from codex_deck.codex_relay import HistoryScope, Router
from codex_deck.relay_transport import RelayHub


class HistoryScopeTests(unittest.TestCase):
    def setUp(self):
        self.directory = "/tmp/deck-project"
        self.scope = HistoryScope([], self.directory)
        self.events = []
        def emit(event):
            self.scope.observe(event)
            self.events.append(event)
        self.hub = RelayHub(emit, lambda notify: Router(notify, self.scope))

    def peer(self, name):
        peer = SimpleNamespace(id=name)
        self.hub.attach(peer)
        return peer

    def bind(self, peer, thread, cwd):
        message = self.hub.from_ui(peer, {"id": 1, "method": "thread/resume", "params": {"threadId": thread}})
        self.hub.from_server(peer, {"id": message["id"], "result": {"thread": {"id": thread, "cwd": cwd}}})

    def test_slash_resume_auxiliary_picker_uses_active_project(self):
        main = self.peer("main")
        self.bind(main, "active-chat", "/tmp/actual-project")
        for index in range(3):
            picker = self.peer("picker-" + str(index))
            # Native /resume opens a separate connection with no directory.
            request = {"id": 2, "method": "thread/list", "params": {"cwd": None, "limit": 25}}
            wire = self.hub.from_ui(picker, request)
            self.assertEqual(wire["params"]["cwd"], "/tmp/actual-project")
            self.assertIsNone(request["params"]["cwd"])
            self.hub.detach(picker)
        self.assertEqual(self.hub.owner, main.id)

    def test_startup_picker_filters_before_any_thread_is_bound(self):
        router = Router(lambda _: None, HistoryScope(["-C", "../project b", "resume"], self.directory))
        for params in (None, {}, {"cwd": None}, {"cwd": []}):
            wire = router.from_ui({"id": 1, "method": "thread/list", "params": params})
            self.assertEqual(wire["params"]["cwd"], "/tmp/project b")

    def test_search_archive_sort_and_every_page_keep_the_filter(self):
        picker = self.peer("picker")
        for cursor in (None, "page-two", "page-three"):
            params = {"cursor": cursor, "limit": 2, "searchTerm": "test", "archived": True,
                      "sortKey": "updated_at", "sortDirection": "desc", "sourceKinds": ["cli"]}
            expected = dict(params, cwd=self.directory)
            wire = self.hub.from_ui(picker, {"id": 4, "method": "thread/list", "params": params})
            self.assertEqual(wire["params"], expected)
            # Forward native pagination intact; never filter only the first page.
            response = {"id": wire["id"], "result": {"data": [], "nextCursor": "next"}}
            returned = self.hub.from_server(picker, response)
            self.assertEqual(returned, {"id": 4, "result": response["result"]})

    def test_resume_all_is_unscoped_but_keeps_explicit_native_filters(self):
        router = Router(lambda _: None, HistoryScope(["resume", "--all"], self.directory))
        for params in ({}, {"cwd": None}, {"cwd": "/tmp/chosen-project"}):
            result = router.from_ui({"id": 1, "method": "thread/list", "params": params})
            self.assertEqual(result["params"], params)

    def test_explicit_native_directory_filter_is_preserved(self):
        router = Router(lambda _: None, self.scope)
        for cwd in ("/tmp/chosen-project", ["/tmp/a", "/tmp/b"]):
            result = router.from_ui({"id": 1, "method": "thread/list", "params": {"cwd": cwd}})
            self.assertEqual(result["params"]["cwd"], cwd)

    def test_switching_active_chat_updates_existing_picker_not_unrelated_reads(self):
        main, picker = self.peer("main"), self.peer("picker")
        self.bind(main, "a", "/tmp/a")
        self.bind(main, "b", "/tmp/b")
        read = self.hub.from_ui(picker, {"id": 2, "method": "thread/read", "params": {"threadId": "c"}})
        self.hub.from_server(picker, {"id": read["id"], "result": {"thread": {"id": "c", "cwd": "/tmp/c"}}})
        result = self.hub.from_ui(picker, {"id": 3, "method": "thread/list"})
        self.assertEqual(result["params"]["cwd"], "/tmp/b")
        failed = self.hub.from_ui(main, {"id": 4, "method": "thread/resume", "params": {"threadId": "d"}})
        self.hub.from_server(main, {"id": failed["id"], "error": {"message": "not found"}})
        self.assertEqual(self.scope.cwd, "/tmp/b")

    def test_queries_do_not_add_cwd_to_other_operations(self):
        router = Router(lambda _: None, self.scope)
        for method in ("thread/read", "thread/resume", "thread/fork", "turn/start", "thread/archive"):
            params = {"threadId": "selected"}
            self.assertEqual(router.from_ui({"id": 5, "method": method, "params": params})["params"], params)
        approval = {"id": 10, "result": {"decision": "decline"}}
        self.assertEqual(router.from_ui(copy.deepcopy(approval)), approval)

    def test_argument_values_and_literal_prompts_cannot_enable_global_history(self):
        for args in (["-m", "--all"], ["-c", "--all"], ["--", "--all"],
                     ["resume", "Explain --all"], ["--config=--all", "resume"]):
            scope = HistoryScope(args, self.directory)
            self.assertFalse(scope.all_projects, args)
            self.assertEqual(scope.cwd, self.directory)
        for args in (["-C../project b", "resume"], ["resume", "--cd=../project b"],
                     ["resume", "-C=../project b"],
                     ["--cd", "../project b", "resume"]):
            self.assertEqual(HistoryScope(args, self.directory).cwd, "/tmp/project b")

    def test_launch_directory_is_canonicalized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "real").mkdir()
            (root / "alias").symlink_to(root / "real", target_is_directory=True)
            scope = HistoryScope(["-C", "alias"], root)
            self.assertEqual(scope.cwd, str(root / "real"))


if __name__ == "__main__":
    unittest.main()
