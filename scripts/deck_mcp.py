#!/usr/bin/python3
"""Codex Deck tools over MCP stdio. Authority comes from this process's endpoint."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.ipc import read_json, request
from codex_deck.core import emit_event


TOOLS = [
    {"name": "list_workers", "description": "列出用户在 Codex Deck 中允许当前 Agent 调度的其他终端，先查询再派发。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "dispatch_task", "description": "向一个授权 Agent 的当前 Codex 会话派发任务；忙碌时排队，完成后自动回传。每个逻辑任务使用唯一 request_id，重试相同任务必须沿用它。",
     "inputSchema": {"type": "object", "properties": {"agent_id": {"type": "string"}, "prompt": {"type": "string"}, "request_id": {"type": "string"}}, "required": ["agent_id", "prompt", "request_id"]}},
    {"name": "get_task_result", "description": "查看自己派发或收到的任务状态与结果；结果可包含执行者的总结和验证情况。",
     "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
    {"name": "list_tasks", "description": "查看当前 Agent 参与的协作任务。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "cancel_task", "description": "取消自己派发的任务；排队任务直接取消，执行中只中断对应回合。",
     "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
]


def serve(config_path, epoch=""):
    config = read_json(config_path)
    endpoint = config["coordination"]
    for line in sys.stdin.buffer:
        if len(line) > 1_000_000:
            continue
        try:
            message = json.loads(line)
            identity = message.get("id")
            method = message.get("method")
            if identity is None:
                continue
            if method == "initialize":
                result = {"protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "codex-deck", "version": "1.1.0"},
                          "instructions": "你可以通过 Deck 工具调度用户勾选的其他 Agent。任意 Agent 都可发起；范围由用户在工作台界面设置。先 list_workers。派发后可结束当前响应，工作台会把结果作为后续消息回传；不要反复轮询或重复派发。"}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
                emit_event(config["events"], {"type": "agent-tools-ready", "epoch": epoch})
            elif method == "tools/call":
                params = message.get("params", {})
                try:
                    if params.get("name") not in {t["name"] for t in TOOLS}:
                        raise ValueError("未知工具")
                    if not isinstance(params.get("arguments", {}), dict):
                        raise ValueError("工具参数必须是对象")
                    answer = request(endpoint, params["name"], params.get("arguments", {}), epoch=epoch)
                    result = {"content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}]}
                except Exception as error:
                    result = {"isError": True, "content": [{"type": "text", "text": str(error)}]}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "Method not found"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": identity, "result": result}, ensure_ascii=False), flush=True)
        except (ValueError, TypeError, KeyError) as error:
            print(str(error), file=sys.stderr, flush=True)


if __name__ == "__main__":
    serve(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
