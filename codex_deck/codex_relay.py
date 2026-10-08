"""One native Codex TUI ↔ one app-server, with a scoped collaboration side channel.

The TUI owns its normal requests, approvals and terminal UI. Deck only starts
turns and interrupts the exact turns it owns; every notification still reaches
the TUI. Session identity is captured from actual TUI start/resume responses.
"""
import json
import copy
import os
from pathlib import Path
import selectors
import socket
import signal
import subprocess
import threading
import time
import uuid

from .core import ROOT, emit_event
from .ipc import read_json
from .local_websocket import Decoder, accept_handshake, encode_frame


class Router:
    def __init__(self, emit):
        self.emit = emit
        self.pending = {}
        self.thread = None
        self.ready = False
        self.serial = 0

    def from_ui(self, message):
        message = copy.deepcopy(message)
        if message.get("method") == "initialize":
            params = message.setdefault("params", {})
            capabilities = params.get("capabilities") or {}
            params["capabilities"] = capabilities
            # The relay needs lifecycle events even if this CLI build suppresses
            # them by default; they remain valid native notifications for the TUI.
            required = {"thread/status/changed", "turn/started", "turn/completed", "item/completed"}
            if capabilities.get("optOutNotificationMethods"):
                capabilities["optOutNotificationMethods"] = [m for m in capabilities["optOutNotificationMethods"] if m not in required]
        if "method" in message and "id" in message:
            original = message["id"]
            self.serial += 1
            message["id"] = f"ui:{self.serial}"
            self.pending[message["id"]] = ("ui", original, message["method"])
        if message.get("method") == "initialized":
            self.ready = True
        return message

    def from_deck(self, identity, method, params, expected_thread):
        allowed = {"turn/start", "turn/interrupt", "thread/read"}
        if not self.ready or not self.thread:
            raise RuntimeError("Codex 尚未连接，先完成登录和目录信任提示")
        if method not in allowed:
            raise ValueError("不允许的协作操作")
        if expected_thread != self.thread or params.get("threadId") != self.thread:
            raise RuntimeError("Codex 会话已切换，请重新选择 Agent")
        if method == "turn/start" and set(params) - {"threadId", "input", "clientUserMessageId"}:
            raise ValueError("协作派发不能覆盖执行者权限或工作目录")
        key = "deck:" + identity
        self.pending[key] = ("deck", identity, method)
        return {"id": key, "method": method, "params": params}

    def from_server(self, message):
        if "id" in message and "method" not in message:
            pending = self.pending.pop(message["id"], None)
            if pending:
                owner, identity, method = pending
                if owner == "deck":
                    self.emit({"type": "rpc-result", "request_id": identity,
                               "method": method, "result": message.get("result"), "error": message.get("error")})
                    return None
                if method in ("thread/start", "thread/resume", "thread/fork"):
                    thread = (message.get("result") or {}).get("thread", {})
                    if thread.get("id"):
                        self.thread = thread["id"]
                        self.emit({"type": "agent-bound", "thread_id": self.thread,
                                   "cwd": thread.get("cwd"), "status": thread.get("status", {"type": "idle"})})
                message = dict(message, id=identity)
        method = message.get("method", "")
        params = message.get("params") or {}
        if method in {"thread/status/changed", "turn/started", "turn/completed", "item/completed"}:
            if params.get("threadId") == self.thread:
                event_params = copy.deepcopy(params)
                if method == "item/completed":
                    item = params.get("item", {})
                    if item.get("type") != "agentMessage":
                        return message
                    event_params["item"] = {k: item[k] for k in ("type", "id", "phase") if k in item}
                    event_params["item"]["text"] = item.get("text", "")[:60000]
                elif method in {"turn/started", "turn/completed"}:
                    turn = event_params.get("turn", {})
                    turn["items"] = [{"type": "agentMessage", "phase": item.get("phase"), "text": item.get("text", "")[:60000]}
                                     for item in turn.get("items", []) if item.get("type") == "agentMessage"][-4:]
                self.emit({"type": "agent-event", "method": method, "params": event_params})
        # Server-initiated approvals stay with the native TUI, never auto-accepted.
        return message


def supports_managed(args):
    if any(a in {"--help", "-h", "--version", "-V", "--remote", "--no-daemon"} or a.startswith("--remote=") for a in args):
        return False
    commands = {"exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server", "agents",
                "queue", "completion", "update", "doctor", "sandbox", "debug", "apply", "archive",
                "delete", "cloud", "remote-control", "features", "help", "exec-server", "unarchive", "migrate-rollouts"}
    takes_value = {"-c", "--config", "-m", "--model", "-p", "--profile", "-C", "--cd", "-s", "--sandbox",
                   "-a", "--ask-for-approval", "--enable", "--disable", "--add-dir", "-i", "--image", "--local-provider"}
    skip = False
    for argument in args:
        if skip:
            skip = False
            continue
        if argument in takes_value:
            skip = True
        elif not argument.startswith("-"):
            return argument not in commands
    return True


def run_managed(config, user_args, callback):
    directory = Path(config["events"]).parent
    epoch = uuid.uuid4().hex
    channel = directory / "control"
    channel.mkdir(mode=0o700, exist_ok=True)
    socket_path = directory / "tui.sock"
    socket_path.unlink(missing_ok=True)
    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server_socket.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server_socket.listen(1)
        server_socket.settimeout(.2)
    except OSError:
        server_socket.close()
        raise
    emit = lambda event: emit_event(config["events"], dict(event, epoch=epoch))
    emit({"type": "agent-starting"})
    options = ["-c", "notify=" + json.dumps(callback, ensure_ascii=False)]
    mcp = {"command": "/usr/bin/python3",
           "args": [str(ROOT / "scripts" / "deck_mcp.py"), str(directory / "session.json"), epoch],
           "startup_timeout_sec": 10, "tool_timeout_sec": 20}
    for key, value in mcp.items():
        options += ["-c", f"mcp_servers.deck.{key}=" + json.dumps(value, ensure_ascii=False)]
    server = None
    tui = None
    stop = threading.Event()
    old_handlers = {}
    def end_session(signum, _frame):
        raise SystemExit(128 + signum)
    for signum in (signal.SIGHUP, signal.SIGTERM):
        old_handlers[signum] = signal.signal(signum, end_session)
    try:
        with (directory / "app-server.log").open("w") as log:
            server = subprocess.Popen([config["codex"], *options, "app-server", "--listen", "stdio://"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, start_new_session=True)
            # Stdout/stderr inherit the PTY. The TUI keeps the user's flags and UI.
            tui = subprocess.Popen([config["codex"], "--remote", "unix://" + str(socket_path), *user_args])
            deadline = time.monotonic() + 30
            while True:
                if server.poll() is not None:
                    message = (directory / "app-server.log").read_text(errors="replace")[-1200:]
                    raise RuntimeError("Codex app-server 启动失败：" + message)
                if tui.poll() is not None:
                    return tui.returncode
                if time.monotonic() > deadline:
                    raise RuntimeError("Codex 界面未连接，请确认 CLI 支持 --remote unix://")
                try:
                    connection, _ = server_socket.accept()
                    break
                except socket.timeout:
                    continue
            with connection:
                remainder = accept_handshake(connection)
                decoder = Decoder()
                os.set_blocking(server.stdout.fileno(), False)
                selector = selectors.DefaultSelector()
                selector.register(connection, selectors.EVENT_READ, "ui")
                selector.register(server.stdout, selectors.EVENT_READ, "server")
                router = Router(emit)
                buffer = b""
                outgoing = bytearray()
                if remainder:
                    lines, replies = decoder.feed(remainder)
                    outgoing.extend(replies)
                    for line in lines:
                        server.stdin.write(json.dumps(router.from_ui(json.loads(line)), ensure_ascii=False).encode() + b"\n")
                        server.stdin.flush()
                while tui.poll() is None and server.poll() is None:
                    for key, _ in selector.select(.04):
                        owner = key.data
                        try:
                            data = connection.recv(65536) if owner == "ui" else os.read(server.stdout.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if not data:
                            stop.set()
                            break
                        if owner == "ui":
                            lines, replies = decoder.feed(data)
                            outgoing.extend(replies)
                            if decoder.closed:
                                stop.set()
                        else:
                            buffer += data
                            if len(buffer) > 16_000_000:
                                raise RuntimeError("Codex 协议消息超过大小限制")
                            lines = []
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                lines.append(line)
                        for line in lines:
                            if not line.strip():
                                continue
                            message = json.loads(line)
                            if owner == "ui":
                                forwarded = router.from_ui(message)
                                server.stdin.write(json.dumps(forwarded, ensure_ascii=False).encode() + b"\n")
                                server.stdin.flush()
                            else:
                                forwarded = router.from_server(message)
                                if forwarded is not None:
                                    outgoing.extend(encode_frame(json.dumps(forwarded, ensure_ascii=False).encode()))
                    if stop.is_set():
                        break
                    for path in sorted(channel.glob("*.json"))[:20]:
                        command = None
                        try:
                            command = read_json(path)
                            if command.get("epoch") != epoch or command.get("expires", 0) < time.time():
                                raise RuntimeError("控制请求已过期或目标会话已重启")
                            message = router.from_deck(command["id"], command["method"], command["params"], command["thread_id"])
                            server.stdin.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
                            server.stdin.flush()
                        except Exception as error:
                            emit({"type": "rpc-result", "request_id": command.get("id") if isinstance(command, dict) else path.stem,
                                  "error": {"message": str(error)}})
                        finally:
                            path.unlink(missing_ok=True)
                    if outgoing:
                        try:
                            sent = connection.send(outgoing)
                            del outgoing[:sent]
                        except BlockingIOError:
                            pass
                        if len(outgoing) > 32_000_000:
                            raise RuntimeError("Codex 界面读取过慢，协作连接关闭")
                selector.close()
            if tui.poll() is not None:
                return tui.returncode
            if server.poll() not in (None, 0):
                raise RuntimeError("Codex app-server 退出：" + (directory / "app-server.log").read_text(errors="replace")[-1200:])
            # On normal /quit the TUI may close its socket just before exiting.
            try:
                return tui.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return 1
    finally:
        for process in (tui, server):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        server_socket.close()
        socket_path.unlink(missing_ok=True)
        emit({"type": "agent-disconnected"})
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
