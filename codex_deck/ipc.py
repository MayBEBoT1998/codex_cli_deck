"""Private local mailboxes: the GUI, CLI relay and MCP process need no TCP port."""
import json
import os
from pathlib import Path
import time
import uuid


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False)
    temporary.replace(path)


def read_json(path, limit=2_000_000):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError("消息超过大小限制")
    return json.loads(path.read_text(encoding="utf-8"))


def request(directory, operation, arguments, timeout=15, epoch=""):
    directory = Path(directory)
    if not (directory / "online").exists():
        raise RuntimeError("工作台已关闭，协作连接不可用")
    identity = uuid.uuid4().hex
    target = directory / "requests" / (identity + ".json")
    response = directory / "responses" / (identity + ".json")
    write_json(target, {"id": identity, "operation": operation, "arguments": arguments,
                        "expires": time.time() + timeout, "epoch": epoch})
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if response.exists():
                result = read_json(response)
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result["result"]
            if not (directory / "online").exists():
                raise RuntimeError("协作连接已关闭")
            time.sleep(.05)
        raise TimeoutError("工作台响应超时；请检查任务面板，不要重复派发同一个任务")
    finally:
        target.unlink(missing_ok=True)
        response.unlink(missing_ok=True)
