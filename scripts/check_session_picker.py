#!/usr/bin/python3
"""Check main + repeated picker connections against native Codex, without a model turn.

Uses the same relay as the terminal UI, initializes independent connections and
lists one history entry without printing its contents. No conversation is started
or resumed. Output: artifacts/session-picker-check.json.
"""
import argparse
import json
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.core import ROOT
from codex_deck.codex_relay import Router
from codex_deck.local_websocket import Decoder, connect_handshake, encode_frame
from codex_deck.relay_transport import LocalRelay


class Client:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket.settimeout(8)
            self.socket.connect(str(path))
            remainder = connect_handshake(self.socket)
            self.socket.settimeout(8)
            self.decoder = Decoder(expect_masked=False)
            self.messages = []
            if remainder:
                self.consume(remainder)
        except Exception:
            self.socket.close()
            raise

    def send(self, message):
        self.socket.sendall(encode_frame(json.dumps(message).encode(), masked=True))

    def consume(self, data):
        if not data:
            raise ConnectionError("Codex 连接关闭")
        messages, replies = self.decoder.feed(data)
        if replies:
            self.socket.sendall(replies)
        self.messages.extend(json.loads(text) for text in messages)

    def result(self, identity):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for index, message in enumerate(self.messages):
                if message.get("id") == identity and "method" not in message:
                    message = self.messages.pop(index)
                    if "error" in message:
                        raise RuntimeError(str(message["error"]))
                    return message["result"]
            self.consume(self.socket.recv(65536))
        raise TimeoutError("Codex 响应超时")

    def initialize(self):
        self.send({"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "codex_deck_picker_check", "version": "1.1.0"}}})
        self.result(0)
        self.send({"method": "initialized"})

    def close(self):
        try:
            self.socket.sendall(encode_frame(b"", 8, masked=True))
        except OSError:
            pass
        self.socket.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/session-picker-check.json")
    args = parser.parse_args()
    result = {"passed": False, "checks": [], "error": None, "model_calls": 0}
    server, listener, relay, pump = None, None, None, None
    clients = []
    errors = queue.Queue()
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="deck-picker-check-") as directory:
        root = Path(directory)
        log_path = root / "app-server.log"
        try:
            codex = shutil.which("codex")
            if not codex:
                raise RuntimeError("未找到 Codex CLI")
            result["codex_version"] = subprocess.check_output([codex, "--version"], text=True).strip()
            backend = root / "backend.sock"
            frontend = root / "tui.sock"
            with log_path.open("w") as log:
                server = subprocess.Popen([codex, "app-server", "--listen", "unix://" + str(backend)],
                                          stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 20
            while not backend.exists():
                if server.poll() is not None:
                    raise RuntimeError("Codex app-server 未启动")
                if time.monotonic() > deadline:
                    raise TimeoutError("Codex app-server 启动超时")
                time.sleep(.04)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(frontend))
            frontend.chmod(0o600)
            listener.listen(16)
            relay = LocalRelay(listener, backend, root / "control", "test", lambda _: None, Router)
            def run():
                try:
                    while not stop.is_set():
                        relay.step()
                except Exception as error:
                    errors.put(str(error))
            pump = threading.Thread(target=run, daemon=True)
            pump.start()
            main_client = Client(frontend)
            clients.append(main_client)
            main_client.initialize()
            result["checks"].append({"name": "main_connection_initialized", "passed": True})
            for index in range(3):
                picker = Client(frontend)
                clients.append(picker)
                picker.initialize()
                # Concurrent same-numbered requests on distinct live clients.
                query = {"id": index + 1, "method": "thread/list", "params": {"limit": 1}}
                picker.send(query)
                main_client.send(query)
                if "data" not in picker.result(index + 1) or "data" not in main_client.result(index + 1):
                    raise RuntimeError("历史会话列表响应缺少 data")
                picker.close()
                clients.remove(picker)
                deadline = time.monotonic() + 5
                while True:
                    with relay.hub.lock:
                        count = len(relay.hub.peers)
                    if count == 1:
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("关闭历史选择器后连接未释放")
                    time.sleep(.02)
                result["checks"].append({"name": f"picker_open_list_close_{index+1}_main_survives", "passed": True})
            if not errors.empty():
                raise RuntimeError(errors.get())
            result["passed"] = True
        except Exception as error:
            result["error"] = str(error)
            if log_path.exists():
                result["server_log_tail"] = log_path.read_text(errors="replace")[-2500:]
        finally:
            for client in clients:
                client.close()
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
