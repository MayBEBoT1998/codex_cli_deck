"""Multiple native TUI clients connected to the same native app-server.

Codex opens separate connections for history/session pickers. Each frontend
gets its own backend connection and handshake; browsing never shares request IDs
or changes the thread bound to Deck. Closing a picker leaves the TUI connected.
"""
import json
from pathlib import Path
import queue
import selectors
import socket
import threading
import time
import uuid

from .ipc import read_json
from .local_websocket import Decoder, accept_handshake, connect_handshake, encode_frame


MAX_PENDING = 32_000_000


class RelayHub:
    def __init__(self, emit, router_factory):
        self.emit = emit
        self.router_factory = router_factory
        self.lock = threading.RLock()
        self.peers = {}
        self.owner = None

    def attach(self, peer):
        with self.lock:
            peer.router = self.router_factory(lambda event: self.event(peer, event))
            self.peers[peer.id] = peer

    def event(self, peer, event):
        # Invoked inside the lock by Router, including its binding response.
        if event.get("type") == "agent-bound":
            previous = self.peers.get(self.owner)
            if previous and previous.id != peer.id and previous.router.thread == event["thread_id"]:
                return  # A second subscriber to the same chat is not a new owner.
            self.owner = peer.id
            self.emit(event)
        elif self.owner == peer.id:
            self.emit(event)

    def from_ui(self, peer, message):
        with self.lock:
            return peer.router.from_ui(message)

    def from_server(self, peer, message):
        with self.lock:
            return peer.router.from_server(message)

    def detach(self, peer, reason="连接已关闭"):
        with self.lock:
            if self.peers.pop(peer.id, None) is None:
                return
            for owner, identity, method in peer.router.pending.values():
                if owner == "deck":
                    self.emit({"type": "rpc-result", "request_id": identity, "method": method,
                               "error": {"message": reason + "；请核对终端中的实际任务状态", "outcomeUnknown": True}})
            if self.owner == peer.id:
                replacement = next((p for p in self.peers.values()
                                    if p.router.ready and p.router.thread == peer.router.thread), None)
                self.owner = replacement.id if replacement else None
                if replacement is None:
                    self.emit({"type": "agent-unavailable", "message": reason})

    def dispatch(self, command):
        with self.lock:
            peer = self.peers.get(self.owner)
            if peer is None:
                raise RuntimeError("Codex 主会话尚未连接，请先完成登录和目录信任提示")
            message = peer.router.from_deck(command["id"], command["method"], command["params"], command["thread_id"])
            try:
                peer.enqueue(message)
            except Exception:
                peer.router.pending.pop("deck:" + command["id"], None)
                raise


class ConnectionPair:
    def __init__(self, ui, backend_path, hub, stop):
        self.id = uuid.uuid4().hex
        self.ui = ui
        self.backend_path = backend_path
        self.hub = hub
        self.stop_event = stop
        self.backend = None
        self.router = None
        self.commands = queue.Queue(maxsize=256)
        self.queued_bytes = 0
        self.queue_lock = threading.Lock()
        self.stopped = False
        hub.attach(self)
        self.thread = threading.Thread(target=self.run, daemon=True, name="deck-codex-connection")

    def enqueue(self, message):
        frame = encode_frame(json.dumps(message, ensure_ascii=False).encode(), masked=True)
        with self.queue_lock:
            if self.stopped:
                raise RuntimeError("Codex 连接已关闭")
            if self.queued_bytes + len(frame) > MAX_PENDING or self.commands.full():
                raise RuntimeError("Codex 控制队列已满")
            self.commands.put_nowait(frame)
            self.queued_bytes += len(frame)

    def close(self):
        with self.queue_lock:
            self.stopped = True
        for connection in (self.ui, self.backend):
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()

    def run(self):
        reason = "Codex 会话连接已关闭"
        try:
            self.backend = self.connect_backend()
            backend_remainder = connect_handshake(self.backend)
            ui_remainder = accept_handshake(self.ui)
            self.forward(ui_remainder, backend_remainder)
        except (OSError, ValueError, RuntimeError) as error:
            reason = "Codex 连接失败：" + str(error)
        finally:
            self.close()
            self.hub.detach(self, reason)

    def connect_backend(self):
        backend = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            backend.settimeout(10)
            backend.connect(str(self.backend_path))
        except OSError:
            backend.close()
            raise
        return backend

    def forward(self, ui_remainder, backend_remainder):
        sockets = {"ui": self.ui, "backend": self.backend}
        decoders = {"ui": Decoder(), "backend": Decoder(expect_masked=False)}
        outgoing = {"ui": bytearray(), "backend": bytearray()}
        closing_at = None

        def receive(side, data):
            nonlocal closing_at
            messages, replies = decoders[side].feed(data)
            outgoing[side].extend(replies)
            other = "backend" if side == "ui" else "ui"
            for line in messages:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Codex 协议消息必须是 JSON 对象")
                result = self.hub.from_ui(self, value) if side == "ui" else self.hub.from_server(self, value)
                if result is not None:
                    outgoing[other].extend(encode_frame(json.dumps(result, ensure_ascii=False).encode(), masked=other == "backend"))
            if decoders[side].closed:
                closing_at = time.monotonic() + .5

        with selectors.DefaultSelector() as selector:
            for side, connection in sockets.items():
                selector.register(connection, selectors.EVENT_READ, side)
            if ui_remainder:
                receive("ui", ui_remainder)
            if backend_remainder:
                receive("backend", backend_remainder)
            while not self.stop_event.is_set() and not self.stopped:
                with self.queue_lock:
                    while not self.commands.empty():
                        command = self.commands.get_nowait()
                        self.queued_bytes -= len(command)
                        outgoing["backend"].extend(command)
                for side, connection in sockets.items():
                    if len(outgoing[side]) > MAX_PENDING:
                        raise RuntimeError("Codex 消息接收过慢，连接已关闭")
                    events = selectors.EVENT_READ | (selectors.EVENT_WRITE if outgoing[side] else 0)
                    selector.modify(connection, events, side)
                for key, events in selector.select(.04):
                    side = key.data
                    if events & selectors.EVENT_READ:
                        try:
                            data = key.fileobj.recv(65536)
                        except BlockingIOError:
                            continue
                        if not data:
                            return
                        receive(side, data)
                    if events & selectors.EVENT_WRITE and outgoing[side]:
                        try:
                            written = key.fileobj.send(outgoing[side])
                            del outgoing[side][:written]
                        except BlockingIOError:
                            pass
                if closing_at is not None and (not any(outgoing.values()) or time.monotonic() >= closing_at):
                    return


class LocalRelay:
    def __init__(self, listener, backend_path, channel, epoch, emit, router_factory):
        self.listener = listener
        self.backend_path = Path(backend_path)
        self.channel = Path(channel)
        self.epoch = epoch
        self.emit = emit
        self.hub = RelayHub(emit, router_factory)
        self.stop_event = threading.Event()
        self.connections = []
        self.listener.settimeout(.04)

    def step(self):
        try:
            connection, _ = self.listener.accept()
        except socket.timeout:
            connection = None
        self.connections = [p for p in self.connections if p.thread.is_alive()]
        if connection is not None:
            if len(self.connections) >= 16:
                connection.close()
            else:
                pair = ConnectionPair(connection, self.backend_path, self.hub, self.stop_event)
                self.connections.append(pair)
                pair.thread.start()
        for path in sorted(self.channel.glob("*.json"))[:20]:
            command = None
            try:
                command = read_json(path)
                if not isinstance(command, dict):
                    raise ValueError("控制请求必须是 JSON 对象")
                if command.get("epoch") != self.epoch or command.get("expires", 0) < time.time():
                    raise RuntimeError("控制请求已过期或目标会话已重启")
                self.hub.dispatch(command)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                self.emit({"type": "rpc-result", "request_id": command.get("id") if isinstance(command, dict) else path.stem,
                           "error": {"message": str(error)}})
            finally:
                path.unlink(missing_ok=True)

    def close(self):
        self.stop_event.set()
        self.listener.close()
        for pair in self.connections:
            pair.close()
        deadline = time.monotonic() + 3
        for pair in self.connections:
            pair.thread.join(max(0, deadline - time.monotonic()))
