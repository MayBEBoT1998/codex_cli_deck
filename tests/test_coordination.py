import json
from pathlib import Path
import tempfile
import time
import unittest
import subprocess
import threading
import selectors
from types import SimpleNamespace

from codex_deck.coordination import Coordinator
from codex_deck.codex_relay import Router, supports_managed
from codex_deck.core import ROOT
from codex_deck.ipc import read_json, write_json
from codex_deck.local_websocket import Decoder, encode_frame


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hub = Coordinator(self.root, self.root / "history.json")
        for name in ("a", "b", "c"):
            self.hub.register(SimpleNamespace(id=name, name="Agent " + name, cwd="/tmp/" + name), self.root / name)
            self.event(name, {"type": "agent-starting"})
            self.event(name, {"type": "agent-bound", "thread_id": "thread-" + name, "status": {"type": "idle"}})

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def event(self, actor, data):
        self.hub.handle_event(actor, dict(data, epoch="epoch-" + actor))

    def notify(self, actor, method, **params):
        self.event(actor, {"type": "agent-event", "method": method, "params": dict(params, threadId="thread-" + actor)})

    def command(self, actor):
        files = sorted((self.root / actor / "control").glob("*.json"))
        self.assertTrue(files)
        data = read_json(files[-1])
        files[-1].unlink()
        return data

    def start(self, task):
        self.hub.tick()
        command = self.command(task.target)
        self.assertEqual(command["params"]["threadId"], "thread-" + task.target)
        self.event(task.target, {"type": "rpc-result", "request_id": command["id"], "result": {"turn": {"id": "turn-" + task.id}}})
        return command

    def complete(self, task, text="完成并验证", status="completed"):
        self.notify(task.target, "item/completed", turnId=task.turn,
                    item={"type": "agentMessage", "phase": "final_answer", "text": text})
        self.notify(task.target, "turn/completed", turn={"id": task.turn, "status": status, "items": []})

    def test_any_agent_can_initiate_but_only_towards_checked_targets(self):
        self.hub.set_grants("b", ["a"])
        task = self.hub.dispatch("b", "a", "实现接口", "one")
        self.assertEqual((task.source, task.target), ("b", "a"))
        with self.assertRaises(ValueError):
            self.hub.dispatch("b", "c", "越界任务", "two")
        with self.assertRaises(ValueError):
            self.hub.dispatch("a", "b", "没有反向授权", "three")

    def test_busy_worker_queues_then_executes_and_returns_to_origin(self):
        self.hub.set_grants("a", ["b"])
        self.notify("b", "turn/started", turn={"id": "manual-turn"})
        task = self.hub.dispatch("a", "b", "测试中文与引号 $(not a command)", "one")
        self.hub.tick()
        self.assertEqual(task.status, "queued")
        self.assertFalse(list((self.root / "b/control").glob("*.json")))
        self.notify("b", "turn/completed", turn={"id": "manual-turn", "status": "completed"})
        command = self.start(task)
        self.assertIn(task.prompt, command["params"]["input"][0]["text"])
        self.complete(task)
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.result, "完成并验证")
        self.hub.tick()
        report = self.command("a")
        self.assertIn("完成并验证", report["params"]["input"][0]["text"])
        self.event("a", {"type": "rpc-result", "request_id": report["id"], "result": {"turn": {"id": "report"}}})
        self.assertEqual(task.delivery_status, "delivered")

    def test_same_request_id_is_idempotent_and_different_prompt_rejected(self):
        self.hub.set_grants("a", ["b"])
        one = self.hub.dispatch("a", "b", "task", "same")
        two = self.hub.dispatch("a", "b", "task", "same")
        self.assertIs(one, two)
        with self.assertRaises(ValueError):
            self.hub.dispatch("a", "b", "different", "same")

    def test_revoking_cancels_waiting_task_before_any_dispatch(self):
        self.hub.set_grants("a", ["b"])
        task = self.hub.dispatch("a", "b", "task", "one", deliver=False)
        self.hub.set_grants("a", [])
        self.hub.tick()
        self.assertEqual(task.status, "cancelled")
        self.assertFalse(list((self.root / "b/control").glob("*.json")))

    def test_cancel_interrupts_only_the_owned_turn(self):
        self.hub.set_grants("a", ["b"])
        task = self.hub.dispatch("a", "b", "task", "one")
        self.start(task)
        self.hub.cancel(task.id)
        command = self.command("b")
        self.assertEqual(command["method"], "turn/interrupt")
        self.assertEqual(command["params"]["turnId"], task.turn)
        self.complete(task, status="interrupted")
        self.assertEqual(task.status, "cancelled")

    def test_turn_completed_before_start_reply_is_not_lost(self):
        self.hub.set_grants("a", ["b"])
        task = self.hub.dispatch("a", "b", "task", "one")
        self.hub.tick()
        command = self.command("b")
        self.notify("b", "item/completed", turnId="fast", item={"type": "agentMessage", "text": "fast result"})
        self.notify("b", "turn/completed", turn={"id": "fast", "status": "completed"})
        self.event("b", {"type": "rpc-result", "request_id": command["id"], "result": {"turn": {"id": "fast"}}})
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.result, "fast result")

    def test_restart_revokes_grants_and_ignores_old_generation(self):
        self.hub.set_grants("a", ["b"])
        task = self.hub.dispatch("a", "b", "task", "one")
        self.hub.handle_event("b", {"type": "agent-starting", "epoch": "new"})
        self.assertEqual(task.status, "disconnected")
        self.assertNotIn("b", self.hub.grants["a"])
        self.event("b", {"type": "agent-bound", "thread_id": "old"})
        self.assertEqual(self.hub.agents["b"].thread, "")

    def test_cycle_is_rejected_even_if_both_grants_exist(self):
        self.hub.set_grants("a", ["b"])
        self.hub.set_grants("b", ["a", "c"])
        task = self.hub.dispatch("a", "b", "task", "one")
        self.start(task)
        with self.assertRaises(ValueError):
            self.hub.dispatch("b", "a", "循环任务", "two")
        nested = self.hub.dispatch("b", "c", "有效子任务", "three")
        self.assertEqual(nested.ancestors, ["a"])

    def test_history_retains_results_without_replaying_or_restoring_grants(self):
        self.hub.set_grants("a", ["b"])
        task = self.hub.dispatch("a", "b", "task", "one")
        self.start(task)
        restored = Coordinator(self.root / "restart", self.root / "history.json")
        self.assertEqual(restored.tasks[task.id].status, "disconnected")
        self.assertEqual(restored.grants, {})

    def test_private_mailbox_cannot_override_actor(self):
        self.hub.set_grants("a", ["b"])
        identity = "request"
        endpoint = self.hub.agents["c"].endpoint
        write_json(endpoint / "requests" / (identity + ".json"), {"id": identity, "operation": "dispatch_task",
                   "expires": time.time() + 10, "epoch": "epoch-c",
                   "arguments": {"source": "a", "agent_id": "b", "prompt": "task", "request_id": "one"}})
        self.hub.tick()
        answer = read_json(endpoint / "responses" / (identity + ".json"))
        self.assertIn("error", answer)
        self.assertFalse(self.hub.tasks)

    def test_two_windows_for_same_codex_thread_cannot_delegate_to_each_other(self):
        self.hub.set_grants("a", ["b"])
        self.hub.agents["b"].thread = "thread-a"
        with self.assertRaises(ValueError):
            self.hub.dispatch("a", "b", "task", "one")

    def test_old_mcp_process_cannot_send_after_restart(self):
        self.hub.set_grants("a", ["b"])
        endpoint = self.hub.agents["a"].endpoint
        write_json(endpoint / "requests/old.json", {"id": "old", "operation": "dispatch_task",
                   "expires": time.time() + 10, "epoch": "previous-epoch",
                   "arguments": {"agent_id": "b", "prompt": "stale task", "request_id": "stale"}})
        self.hub.tick()
        self.assertIn("error", read_json(endpoint / "responses/old.json"))
        self.assertFalse(self.hub.tasks)

    def test_large_chinese_final_response_survives_file_transport(self):
        from codex_deck.core import emit_event, drain_events
        text = "结果已验证，包含中文。" * 5000
        emit_event(self.root, {"type": "agent-event", "method": "item/completed", "params": {"item": {"text": text}}})
        events = drain_events(self.root)
        event = next(e for e in events if e.get("type") == "agent-event")
        self.assertEqual(event["params"]["item"]["text"], text)

    def test_unrelated_completed_turn_cannot_complete_delegated_task(self):
        self.hub.set_grants("a", ["b"])
        task = self.hub.dispatch("a", "b", "task", "one")
        self.start(task)
        self.notify("b", "turn/completed", turn={"id": "other", "status": "completed"})
        self.assertEqual(task.status, "running")

    def test_timeout_does_not_redispatch_potentially_started_task(self):
        self.hub.set_grants("a", ["b"])
        task = self.hub.dispatch("a", "b", "task", "one", deliver=False)
        self.hub.tick()
        identity, info = next(iter(self.hub.pending.items()))
        self.hub.pending[identity] = (*info[:4], time.time() - 1)
        self.hub.tick()
        self.hub.tick()
        self.assertEqual(task.status, "unknown")
        self.assertFalse(self.hub.pending)
        self.assertFalse(list(self.hub.agents['b'].channel.glob('*.json')))

    def test_mcp_stdio_process_lists_then_dispatches_using_private_mailbox(self):
        self.hub.set_grants("b", ["c"])
        config = self.root / "mcp.json"
        write_json(config, {"coordination": str(self.hub.agents["b"].endpoint), "events": str(self.root)})
        process = subprocess.Popen(['/usr/bin/python3', str(ROOT / 'scripts/deck_mcp.py'), str(config), 'epoch-b'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        def exchange(identity, method, params):
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identity, "method": method, "params": params})+'\n')
            process.stdin.flush()
            deadline = time.monotonic()+4
            while time.monotonic()<deadline:
                self.hub.tick()
                if selector.select(.02):
                    result = json.loads(process.stdout.readline())
                    self.assertEqual(result['id'], identity)
                    return result['result']
            self.fail('MCP subprocess response timed out')
        try:
            result = exchange(1,'initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
            self.assertIn('tools',result['capabilities'])
            result = exchange(2,'tools/list',{})
            self.assertEqual(len(result['tools']),5)
            result = exchange(3,'tools/call',{'name':'list_workers','arguments':{}})
            workers=json.loads(result['content'][0]['text'])['workers']
            self.assertEqual([w['agent_id'] for w in workers],['c'])
            result = exchange(4,'tools/call',{'name':'dispatch_task','arguments':{'agent_id':'c','prompt':'中文测试','request_id':'mcp-test'}})
            task=json.loads(result['content'][0]['text'])
            self.assertEqual(self.hub.tasks[task['task_id']].source,'b')
        finally:
            selector.close()
            process.terminate()
            process.communicate(timeout=3)


class RelayTests(unittest.TestCase):
    def test_initialization_handles_null_capabilities_and_preserves_other_options(self):
        router = Router(lambda event: None)
        result = router.from_ui({"id": 1, "method": "initialize", "params": {"capabilities": None}})
        self.assertEqual(result['params']['capabilities'], {})
        result = router.from_ui({"id": 2, "method": "initialize", "params": {'capabilities': {
            'experimentalApi': True, 'optOutNotificationMethods': ['item/completed','noise']}}})
        self.assertTrue(result['params']['capabilities']['experimentalApi'])
        self.assertEqual(result['params']['capabilities']['optOutNotificationMethods'],['noise'])

    def test_command_modes_and_option_values_are_not_confused(self):
        for args in ([], ['resume','session-id'], ['-m','model-name'], ['-C','review'], ['--sandbox','read-only']):
            self.assertTrue(supports_managed(args),args)
        for args in (['exec','task'], ['--version'], ['--no-daemon'], ['--remote','unix:///tmp/demo']):
            self.assertFalse(supports_managed(args),args)

    def test_native_approval_round_trip_is_preserved(self):
        events = []
        router = Router(events.append)
        approval = {"id": 17, "method": "item/commandExecution/requestApproval", "params": {"command": "test"}}
        self.assertEqual(router.from_server(approval), approval)
        response = {"id": 17, "result": {"decision": "decline"}}
        self.assertEqual(router.from_ui(response), response)

    def test_binding_uses_tui_response_and_rejects_different_thread(self):
        events = []
        router = Router(events.append)
        command = router.from_ui({"id": 3, "method": "thread/start", "params": {}})
        reply = router.from_server({"id": command["id"], "result": {"thread": {"id": "actual", "cwd": "/tmp/project"}}})
        self.assertEqual(reply["id"], 3)
        self.assertEqual(events[0]["thread_id"], "actual")
        router.from_ui({"method": "initialized"})
        with self.assertRaises(RuntimeError):
            router.from_deck("x", "turn/start", {"threadId": "wrong"}, "wrong")
        with self.assertRaises(ValueError):
            router.from_deck("x", "turn/start", {"threadId": "actual", "sandboxPolicy": {}}, "actual")

    def test_deck_results_do_not_leak_into_tui_response_ids(self):
        events = []
        router = Router(events.append)
        router.ready, router.thread = True, "actual"
        request = router.from_deck("one", "turn/start", {"threadId": "actual", "input": []}, "actual")
        self.assertIsNone(router.from_server({"id": request["id"], "result": {"turn": {"id": "turn"}}}))
        self.assertEqual(events[-1]["request_id"], "one")
        notification = {"method": "turn/started", "params": {"threadId": "actual", "turn": {"id": "turn"}}}
        self.assertEqual(router.from_server(notification), notification)


class WebSocketTests(unittest.TestCase):
    def client_frame(self, payload, opcode=1, final=True):
        data = bytearray(encode_frame(payload, opcode))
        if not final:
            data[0] &= 127
        header = 2 if len(payload) < 126 else 4 if len(payload) < 65536 else 10
        data[1] |= 128
        mask = b"abcd"
        return bytes(data[:header]) + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload))

    def test_fragmented_chinese_message_and_ping(self):
        decoder = Decoder()
        first = self.client_frame('中文'.encode(), final=False)
        self.assertEqual(decoder.feed(first[:3]), ([], bytearray()))
        self.assertEqual(decoder.feed(first[3:])[0], [])
        message, pong = decoder.feed(self.client_frame(b"ping", 9) + self.client_frame(b" done", 0))
        self.assertEqual(message, ["中文 done"])
        self.assertEqual(pong, encode_frame(b"ping", 10))

    def test_extended_message_sizes_and_unmasked_rejection(self):
        for count in (10, 200, 70000):
            self.assertEqual(Decoder().feed(self.client_frame(b"x" * count))[0], ["x" * count])
        with self.assertRaises(ValueError):
            Decoder().feed(encode_frame(b"unmasked"))


if __name__ == "__main__":
    unittest.main()
