#!/usr/bin/python3
"""Observe the real Codex resume TUI's project filter; no model is called.

Checks `/resume` inside an idle TUI, `codex resume`, and `codex resume --all`
through the same relay as Deck. Records the filter before/after forwarding and
checks returned directories without saving titles, history, or personal paths.
Model-turn requests are rejected by this check's relay.
"""
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.core import ROOT
from codex_deck.codex_relay import HistoryScope, Router, managed_tui_args
from codex_deck.relay_transport import LocalRelay


def observe_picker(codex, frontend, user_args, history_requests, bound, slash=False):
    history_requests.clear()
    bound.clear()
    argv = [codex, "--remote", "unix://" + str(frontend), *managed_tui_args(user_args, Path.cwd())]
    child, master = pty.fork()
    if child == 0:
        environment = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        os.execvpe(codex, argv, environment)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    os.set_blocking(master, False)
    deadline = time.monotonic() + 25
    buffer = b""
    exited = False
    first_request = None
    bound_at = None
    slash_typed_at = None
    slash_sent = False
    try:
        while time.monotonic() < deadline:
            pid, _status = os.waitpid(child, os.WNOHANG)
            if pid:
                exited = True
                break
            if select.select([master], [], [], .04)[0]:
                try:
                    part = os.read(master, 65536)
                except OSError as error:
                    if error.errno in (errno.EIO, errno.EAGAIN):
                        continue
                    raise
                buffer += part
                if b"\x1b[6n" in buffer:
                    os.write(master, b"\x1b[1;1R")
                    buffer = buffer.replace(b"\x1b[6n", b"")
                if b"\x1b[c" in buffer:
                    os.write(master, b"\x1b[?1;2c")
                    buffer = buffer.replace(b"\x1b[c", b"")
                buffer = buffer[-1000:]
            if slash and bound.is_set():
                bound_at = bound_at or time.monotonic()
                if slash_typed_at is None and time.monotonic() - bound_at >= 1:
                    history_requests.clear()
                    os.write(master, b"/resume")
                    slash_typed_at = time.monotonic()
                elif slash_typed_at is not None and not slash_sent and time.monotonic() - slash_typed_at >= .3:
                    os.write(master, b"\r")
                    slash_sent = True
            if history_requests and (not slash or slash_sent):
                first_request = first_request or time.monotonic()
                if time.monotonic() - first_request >= .6 and all(r.get("response_received") for r in history_requests):
                    break
        if not history_requests or (slash and not slash_sent):
            raise RuntimeError("Codex 历史选择器未发出列表请求（可能停在登录或首次启动界面）")
        return [dict(record) for record in history_requests]
    finally:
        if not exited:
            try:
                os.write(master, b"\x1b\x03")
            except OSError:
                pass
            for signum in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(child, signum)
                except ProcessLookupError:
                    pass
                end = time.monotonic() + 1
                while time.monotonic() < end:
                    pid, _ = os.waitpid(child, os.WNOHANG)
                    if pid:
                        exited = True
                        break
                    time.sleep(.02)
                if exited:
                    break
            if not exited:
                os.waitpid(child, 0)
        os.close(master)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/resume-filter-check.json")
    args = parser.parse_args()
    result = {"passed": False, "model_calls": 0, "checks": [], "error": None}
    requests, errors = [], []
    stop = threading.Event()
    bound = threading.Event()
    scope = HistoryScope([], Path.cwd())
    server = listener = relay = pump = None
    class Observer(Router):
        def __init__(self, emit):
            super().__init__(emit, scope)
            self.history_checks = {}

        def from_ui(self, message):
            if message.get("method") in ("turn/start", "turn/steer"):
                errors.append("测试拒绝了模型调用请求")
                raise ValueError(errors[-1])
            forwarded = super().from_ui(message)
            if message.get("method") == "thread/list":
                cwd = (forwarded.get("params") or {}).get("cwd")
                record = {"method": "thread/list",
                          "native_has_cwd": bool((message.get("params") or {}).get("cwd")),
                          "has_cwd": bool(cwd), "matches_current_project": cwd == self.history_scope.cwd,
                          "response_received": False}
                requests.append(record)
                self.history_checks[forwarded["id"]] = (record, cwd)
            return forwarded

        def from_server(self, message):
            check = self.history_checks.pop(message.get("id"), None) if "method" not in message else None
            if check:
                record, cwd = check
                result = message.get("result") or {}
                data = result.get("data")
                record["response_received"] = True
                record["response_ok"] = isinstance(data, list) and not message.get("error")
                record["returned_count"] = len(data) if isinstance(data, list) else 0
                allowed = cwd if isinstance(cwd, list) else [cwd]
                record["returned_rows_match_filter"] = bool(record["response_ok"] and
                    (not cwd or all(row.get("cwd") in allowed for row in data)))
            return super().from_server(message)

    def emit(event):
        scope.observe(event)
        if event.get("type") == "agent-bound":
            bound.set()
    with tempfile.TemporaryDirectory(prefix="deck-resume-filter-") as directory:
        root = Path(directory)
        try:
            codex = shutil.which("codex")
            if not codex:
                raise RuntimeError("未找到 Codex CLI")
            result["codex_version"] = subprocess.check_output([codex, "--version"], text=True).strip()
            backend, frontend = root / "backend.sock", root / "tui.sock"
            with (root / "server.log").open("w") as log:
                server = subprocess.Popen([codex, "app-server", "--listen", "unix://" + str(backend)],
                                          stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 20
            while not backend.exists():
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Codex app-server 未启动")
                time.sleep(.04)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(frontend))
            frontend.chmod(0o600)
            listener.listen(16)
            relay = LocalRelay(listener, backend, root / "control", "test", emit, Observer)
            def run():
                try:
                    while not stop.is_set():
                        relay.step()
                except Exception as error:
                    errors.append(str(error))
            pump = threading.Thread(target=run, daemon=True)
            pump.start()
            for label, arguments, scoped, slash in (("slash_resume_current_project", [], True, True),
                                                    ("resume_current_project", ["resume"], True, False),
                                                    ("resume_all_projects", ["resume", "--all"], False, False)):
                scope = HistoryScope(arguments, Path.cwd())
                observed = observe_picker(codex, frontend, arguments, requests, bound, slash)
                passed = all(r["matches_current_project"] for r in observed) if scoped else all(not r["has_cwd"] for r in observed)
                passed = passed and all(r.get("response_ok") and r.get("returned_rows_match_filter") for r in observed)
                result["checks"].append({"name": label, "passed": passed, "requests": observed})
                # Let the previous TUI and its temporary picker disconnect.
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    with relay.hub.lock:
                        if not relay.hub.peers:
                            break
                    time.sleep(.04)
                else:
                    raise RuntimeError("测试 TUI 的连接未关闭")
            if errors:
                raise RuntimeError("; ".join(errors))
            result["passed"] = all(c["passed"] for c in result["checks"])
        except Exception as error:
            result["error"] = str(error)
        finally:
            stop.set()
            if pump:
                pump.join(3)
            if relay:
                relay.close()
            elif listener:
                listener.close()
            if server and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
