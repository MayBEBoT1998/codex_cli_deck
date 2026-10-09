"""Native Codex TUI and auxiliary clients ↔ one app-server, with a scoped collaboration side channel.

The TUI owns its normal requests, approvals and terminal UI. Deck only starts
turns and interrupts the exact turns it owns; every notification still reaches
the TUI. Session identity is captured from actual TUI start/resume responses.
"""
import json
import copy
import os
from pathlib import Path
import socket
import signal
import subprocess
import time
import uuid

from .core import ROOT, emit_event
from .relay_transport import LocalRelay


_VALUE_OPTIONS = {"-c", "--config", "-m", "--model", "-p", "--profile", "-C", "--cd",
                  "-s", "--sandbox", "-a", "--ask-for-approval", "--enable", "--disable",
                  "--add-dir", "-i", "--image", "--local-provider", "--remote-auth-token-env"}


def _history_options(args):
    """Read directory/all flags without interpreting prompts or option values."""
    directory, all_projects = None, False
    arguments = iter(args)
    for argument in arguments:
        if argument == "--":
            break
        if argument in ("-C", "--cd"):
            directory = next(arguments, None)
        elif argument.startswith("--cd="):
            directory = argument.partition("=")[2]
        elif argument.startswith("-C") and len(argument) > 2:
            directory = argument[2:].removeprefix("=")
        elif argument == "--all":
            all_projects = True
        elif argument in _VALUE_OPTIONS:
            next(arguments, None)
    return directory, all_projects


class HistoryScope:
    """History scope shared by the main TUI and all its temporary pickers.

    Remote TUIs can request an unscoped list even with --cd. Apply the
    directory on thread/list itself, before server-side search/pagination.
    This is a browsing default, not an access-control boundary: explicit cwd
    filters and launching with resume --all still work.
    """
    def __init__(self, user_args, cwd):
        directory, self.all_projects = _history_options(user_args)
        path = Path(directory).expanduser() if directory is not None else Path(cwd)
        if not path.is_absolute():
            path = Path(cwd) / path
        self.cwd = str(path.resolve())

    def observe(self, event):
        # Only the hub's active binding may change the shared directory.
        # Reading an unrelated history item must not change this scope.
        if event.get("type") == "agent-bound" and event.get("cwd"):
            self.cwd = event["cwd"]

    def apply(self, message):
        if message.get("method") == "thread/list" and not self.all_projects:
            params = message.get("params") or {}
            message["params"] = params
            if not params.get("cwd"):
                params["cwd"] = self.cwd


class Router:
    def __init__(self, emit, history_scope=None):
        self.emit = emit
        self.history_scope = history_scope
        self.pending = {}
        self.thread = None
        self.ready = False
        self.serial = 0

    def from_ui(self, message):
        message = copy.deepcopy(message)
        if self.history_scope is not None:
            self.history_scope.apply(message)
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


def managed_tui_args(user_args, cwd):
    """Our remote endpoint is local: give the native TUI its actual project cwd.

    This controls the working root; HistoryScope separately scopes history RPCs.
    Keep an explicit user -C/--cd intact.
    """
    directory, _ = _history_options(user_args)
    if directory is not None:
        return list(user_args)
    return ["--cd", str(cwd), *user_args]


def run_managed(config, user_args, callback):
    directory = Path(config["events"]).parent
    epoch = uuid.uuid4().hex
    channel = directory / "control"
    channel.mkdir(mode=0o700, exist_ok=True)
    socket_path = directory / "tui.sock"
    backend_path = directory / "backend.sock"
    socket_path.unlink(missing_ok=True)
    backend_path.unlink(missing_ok=True)
    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server_socket.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server_socket.listen(16)
    except OSError:
        server_socket.close()
        raise
    history_scope = HistoryScope(user_args, Path.cwd())
    def emit(event):
        history_scope.observe(event)
        emit_event(config["events"], dict(event, epoch=epoch))
    emit({"type": "agent-starting"})
    options = ["-c", "notify=" + json.dumps(callback, ensure_ascii=False)]
    mcp = {"command": "/usr/bin/python3",
           "args": [str(ROOT / "scripts" / "deck_mcp.py"), str(directory / "session.json"), epoch],
           "startup_timeout_sec": 10, "tool_timeout_sec": 20}
    for key, value in mcp.items():
        options += ["-c", f"mcp_servers.deck.{key}=" + json.dumps(value, ensure_ascii=False)]
    server = None
    tui = None
    relay = None
    old_handlers = {}
    def end_session(signum, _frame):
        raise SystemExit(128 + signum)
    for signum in (signal.SIGHUP, signal.SIGTERM):
        old_handlers[signum] = signal.signal(signum, end_session)
    try:
        with (directory / "app-server.log").open("w") as log:
            server = subprocess.Popen([config["codex"], *options, "app-server", "--listen", "unix://" + str(backend_path)],
                                      stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            # Each native client must initialize its own connection. The session
            # picker uses an auxiliary connection while the main TUI stays open.
            deadline = time.monotonic() + 30
            while not backend_path.exists():
                if server.poll() is not None:
                    message = (directory / "app-server.log").read_text(errors="replace")[-1200:]
                    raise RuntimeError("Codex app-server 启动失败：" + message)
                if time.monotonic() > deadline:
                    raise RuntimeError("Codex app-server 本机连接启动超时")
                time.sleep(.04)
            os.chmod(backend_path, 0o600)
            relay = LocalRelay(server_socket, backend_path, channel, epoch, emit,
                               lambda notify: Router(notify, history_scope))
            # Stdout/stderr inherit the PTY. All user flags and approval UI stay native.
            tui = subprocess.Popen([config["codex"], "--remote", "unix://" + str(socket_path),
                                    *managed_tui_args(user_args, Path.cwd())])
            while tui.poll() is None:
                if server.poll() is not None:
                    raise RuntimeError("Codex app-server 退出：" + (directory / "app-server.log").read_text(errors="replace")[-1200:])
                relay.step()
            return tui.returncode
    finally:
        if relay:
            relay.close()
        else:
            server_socket.close()
        for process in (tui, server):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        socket_path.unlink(missing_ok=True)
        backend_path.unlink(missing_ok=True)
        emit({"type": "agent-disconnected"})
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
