"""Real socket-pair/WebSocket regression for concurrent Codex session pickers.

The sandbox prohibits binding sockets. Only the listener's accept queue and
backend socket connection are substituted; framing, handshakes, both relay
directions, per-connection routing and the accept loop are production code.
"""
import json
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from codex_deck.codex_relay import Router
from codex_deck.ipc import write_json
from codex_deck.local_websocket import Decoder, accept_handshake, connect_handshake, encode_frame
from codex_deck.relay_transport import ConnectionPair, LocalRelay, RelayHub


class AcceptQueue:
    def __init__(self):
        self.connections = queue.Queue()

    def settimeout(self, _timeout):
        pass

    def accept(self):
        try:
            return self.connections.get(timeout=.01), None
        except queue.Empty:
            raise socket.timeout()

    def close(self):
        pass


class WebSocketClient:
    def __init__(self, connection):
        self.connection = connection
        self.decoder = Decoder(expect_masked=False)
        self.messages = []
        remainder = connect_handshake(connection)
        connection.settimeout(2)
        if remainder:
            self.receive(remainder)

    def send(self, message):
        self.connection.sendall(encode_frame(json.dumps(message).encode(), masked=True))

    def receive(self, data):
        messages, replies = self.decoder.feed(data)
        if replies:
            self.connection.sendall(replies)
        self.messages.extend(json.loads(m) for m in messages)

    def response(self, identity):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            for index, message in enumerate(self.messages):
                if message.get("id") == identity:
                    return self.messages.pop(index)
            self.receive(self.connection.recv(65536))
        raise AssertionError("No response for " + str(identity))

    def initialize(self):
        self.send({"id": 0, "method": "initialize", "params": {"capabilities": {}}})
        result = self.response(0)
        self.send({"method": "initialized"})
        return result

    def close(self):
        try:
            self.connection.sendall(encode_frame(b"", 8, masked=True))
        except OSError:
            pass
        self.connection.close()


class MultiConnectionTests(unittest.TestCase):
    def setUp(self):
        probe = socket.socketpair()
        try:
            probe[0].sendall(b"check")
        except PermissionError:
            self.skipTest("Sandbox blocks local socket I/O; run this transport integration on the desktop")
        finally:
            for sock in probe:
                sock.close()
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.listener = AcceptQueue()
        self.events = []
        self.backend_sockets = queue.Queue()
        self.backend_calls = []
        self.errors = []
        self.servers = []
        self.clients = []
        self.fixture_sockets = []
        self.stop = threading.Event()
        self.relay = LocalRelay(self.listener, "/tmp/native-app-server.sock", self.directory,
                                "epoch-test", self.events.append, Router)
        connect = lambda _: self.backend_sockets.get(timeout=2)
        self.patch = patch.object(ConnectionPair, "connect_backend", connect)
        self.patch.start()
        self.pump = threading.Thread(target=self.run_relay, daemon=True)
        self.pump.start()

    def tearDown(self):
        self.stop.set()
        self.pump.join(3)
        for client in self.clients:
            client.close()
        self.relay.close()
        for sock in self.fixture_sockets:
            sock.close()
        for server in self.servers:
            server.join(3)
        self.patch.stop()
        self.temp.cleanup()

    def run_relay(self):
        try:
            while not self.stop.is_set():
                self.relay.step()
        except Exception as error:
            self.errors.append(error)

    def add_client(self):
        frontend, client_socket = socket.socketpair()
        backend, server_socket = socket.socketpair()
        self.fixture_sockets.extend([frontend, client_socket, backend, server_socket])
        self.backend_sockets.put(backend)
        number = len(self.servers)
        thread = threading.Thread(target=self.run_backend, args=(server_socket, number), daemon=True)
        self.servers.append(thread)
        thread.start()
        self.listener.connections.put(frontend)
        client = WebSocketClient(client_socket)
        self.clients.append(client)
        return client

    def run_backend(self, connection, number):
        initialized = False
        decoder = Decoder()
        try:
            remainder = accept_handshake(connection)
            connection.settimeout(.1)
            data = remainder
            while not self.stop.is_set():
                if not data:
                    try:
                        data = connection.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        return
                messages, replies = decoder.feed(data)
                data = b""
                if replies:
                    connection.sendall(replies)
                if decoder.closed:
                    return
                for text in messages:
                    message = json.loads(text)
                    self.backend_calls.append((number, message))
                    method = message.get("method")
                    if "id" not in message or not method:
                        continue
                    if method == "initialize":
                        self.assertFalse(initialized, "A picker reused an already initialized connection")
                        initialized = True
                        result = {"connection": number}
                    else:
                        self.assertTrue(initialized)
                        if method == "thread/start":
                            result = {"thread": {"id": "original-thread", "cwd": "/tmp/project", "status": {"type": "idle"}}}
                        elif method == "thread/resume":
                            result = {"thread": {"id": message["params"]["threadId"], "status": {"type": "idle"}}}
                        elif method == "thread/list":
                            result = {"data": [{"id": "original-thread"}, {"id": "historical-thread"}], "nextCursor": None}
                        elif method == "turn/start":
                            result = {"turn": {"id": "delegated-turn", "status": "inProgress"}}
                        else:
                            result = {"connection": number}
                    connection.sendall(encode_frame(json.dumps({"id": message["id"], "result": result}).encode()))
        except OSError:
            pass  # Expected while tearing down socket pairs.
        except Exception as error:
            self.errors.append(error)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate(), self.errors)

    def test_repeated_picker_connections_preserve_main_session_and_control(self):
        main = self.add_client()
        main.initialize()
        main.send({"id": 1, "method": "thread/start", "params": {}})
        self.assertEqual(main.response(1)["result"]["thread"]["id"], "original-thread")
        for _ in range(3):
            picker = self.add_client()
            picker.initialize()
            # Same request ID on both sockets must never collide.
            main.send({"id": 2, "method": "thread/read", "params": {"threadId": "original-thread"}})
            picker.send({"id": 2, "method": "thread/list", "params": {}})
            self.assertEqual(len(picker.response(2)["result"]["data"]), 2)
            self.assertEqual(main.response(2)["result"]["connection"], 0)
            picker.close()
            self.wait_for(lambda: len(self.relay.hub.peers) == 1)
            self.assertFalse(any(e["type"] == "agent-unavailable" for e in self.events))
            self.assertEqual(len([e for e in self.events if e["type"] == "agent-bound"]), 1)
        write_json(self.directory / "task.json", {"id": "task", "epoch": "epoch-test", "expires": time.time()+10,
                   "thread_id": "original-thread", "method": "turn/start",
                   "params": {"threadId": "original-thread", "input": [{"type": "text", "text": "test"}]}})
        self.wait_for(lambda: any(e.get("request_id") == "task" for e in self.events))
        event = next(e for e in self.events if e.get("request_id") == "task")
        self.assertEqual(event["result"]["turn"]["id"], "delegated-turn")
        dispatched = [(n, m) for n, m in self.backend_calls if m.get("method") == "turn/start"]
        self.assertEqual([n for n, _ in dispatched], [0])
        self.assertEqual(self.errors, [])

    def test_resume_selection_rebinds_original_connection_and_rejects_old_control(self):
        main = self.add_client()
        main.initialize()
        main.send({"id": 1, "method": "thread/start", "params": {}})
        main.response(1)
        picker = self.add_client()
        picker.initialize()
        picker.send({"id": 1, "method": "thread/list", "params": {}})
        picker.response(1)
        picker.close()
        main.send({"id": 2, "method": "thread/resume", "params": {"threadId": "historical-thread"}})
        main.response(2)
        self.assertEqual([e["thread_id"] for e in self.events if e["type"] == "agent-bound"],
                         ["original-thread", "historical-thread"])
        write_json(self.directory / "old-task.json", {"id": "old-task", "epoch": "epoch-test", "expires": time.time()+10,
                   "thread_id": "original-thread", "method": "turn/start", "params": {"threadId": "original-thread"}})
        self.wait_for(lambda: any(e.get("request_id") == "old-task" for e in self.events))
        event = next(e for e in self.events if e.get("request_id") == "old-task")
        self.assertIn("error", event)
        self.assertFalse(any(m.get("method") == "turn/start" for _, m in self.backend_calls))
        self.assertEqual(self.errors, [])

    def test_unfinished_picker_handshake_does_not_block_main(self):
        main = self.add_client()
        main.initialize()
        main.send({"id": 1, "method": "thread/start", "params": {}})
        main.response(1)
        frontend, slow = socket.socketpair()
        backend, native = socket.socketpair()
        self.fixture_sockets.extend([frontend, slow, backend, native])
        self.backend_sockets.put(backend)
        self.listener.connections.put(frontend)
        main.send({"id": 2, "method": "thread/read", "params": {"threadId": "original-thread"}})
        self.assertIn("result", main.response(2))
        self.assertEqual(self.errors, [])


class RelayHubTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.hub = RelayHub(self.events.append, Router)

    def peer(self, identity):
        peer = SimpleNamespace(id=identity, queued=[], router=None)
        peer.enqueue = peer.queued.append
        self.hub.attach(peer)
        self.hub.from_ui(peer, {"method": "initialized"})
        return peer

    def bind(self, peer, thread):
        request = self.hub.from_ui(peer, {"id": 1, "method": "thread/resume", "params": {"threadId": thread}})
        return self.hub.from_server(peer, {"id": request["id"], "result": {"thread": {"id": thread}}})

    def test_picker_lifecycle_does_not_change_owner_or_emit_disconnect(self):
        main = self.peer("main")
        self.bind(main, "history")
        for index in range(3):
            picker = self.peer("picker" + str(index))
            request = self.hub.from_ui(picker, {"id": 1, "method": "thread/list", "params": {}})
            response = self.hub.from_server(picker, {"id": request["id"], "result": {"data": [{"id": "other"}]}})
            self.assertEqual(response["id"], 1)
            self.hub.detach(picker)
            self.assertEqual(self.hub.owner, "main")
        self.assertEqual([e["type"] for e in self.events], ["agent-bound"])
        self.hub.dispatch({"id": "task", "method": "turn/start", "thread_id": "history", "params": {"threadId": "history"}})
        self.assertEqual(main.queued[0]["params"]["threadId"], "history")

    def test_identical_request_ids_stay_with_their_own_connection(self):
        main, picker = self.peer("main"), self.peer("picker")
        one = self.hub.from_ui(main, {"id": 4, "method": "thread/read"})
        two = self.hub.from_ui(picker, {"id": 4, "method": "thread/list"})
        a = self.hub.from_server(main, {"id": one["id"], "result": "main reply"})
        b = self.hub.from_server(picker, {"id": two["id"], "result": "picker reply"})
        self.assertEqual(a, {"id": 4, "result": "main reply"})
        self.assertEqual(b, {"id": 4, "result": "picker reply"})

    def test_resume_rebind_rejects_old_thread_and_ignores_old_notifications(self):
        main = self.peer("main")
        self.bind(main, "old")
        self.bind(main, "resumed")
        with self.assertRaises(RuntimeError):
            self.hub.dispatch({"id": "task", "method": "turn/start", "thread_id": "old", "params": {"threadId": "old"}})
        count = len(self.events)
        self.hub.from_server(main, {"method": "turn/completed", "params": {"threadId": "old", "turn": {"id": "turn", "status": "completed"}}})
        self.assertEqual(len(self.events), count)
        self.assertFalse(main.queued)

    def test_connection_handoff_only_to_same_thread(self):
        main = self.peer("main")
        self.bind(main, "thread")
        other = self.peer("other")
        self.bind(other, "thread")
        self.assertEqual(self.hub.owner, "main")
        self.hub.detach(main)
        self.assertEqual(self.hub.owner, "other")
        self.assertFalse(any(e["type"] == "agent-unavailable" for e in self.events))
        self.hub.detach(other)
        self.assertIsNone(self.hub.owner)
        self.assertEqual(self.events[-1]["type"], "agent-unavailable")

    def test_disconnect_marks_pending_dispatch_uncertain(self):
        main = self.peer("main")
        self.bind(main, "thread")
        self.hub.dispatch({"id": "task", "method": "turn/start", "thread_id": "thread", "params": {"threadId": "thread"}})
        self.hub.detach(main)
        result = next(e for e in self.events if e.get("request_id") == "task")
        self.assertTrue(result["error"]["outcomeUnknown"])


if __name__ == "__main__":
    unittest.main()
