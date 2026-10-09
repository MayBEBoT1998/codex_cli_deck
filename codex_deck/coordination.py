"""Directed Agent grants, serialized tasks and explicit result delivery.

All mutations run on the GTK thread (or the test's pump). MCP callers use private
mailboxes. A grant applies only to this launch of this terminal and is never
restored automatically after an application restart.
"""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import time
import uuid

from .ipc import read_json, write_json


FINISHED = {"completed", "failed", "cancelled", "disconnected", "unknown"}
STATUS_NAMES = {"queued": "排队中", "starting": "正在派发", "running": "执行中",
                "cancelling": "正在取消", "completed": "已完成", "failed": "失败",
                "cancelled": "已取消", "disconnected": "连接中断", "unknown": "状态待确认"}


@dataclass
class Agent:
    id: str
    name: str
    cwd: str
    channel: Path
    endpoint: Path
    epoch: str = ""
    thread: str = ""
    state: str = "offline"
    turn: str = ""
    error: str = ""
    tools_ready: bool = False


@dataclass
class Task:
    id: str
    source: str
    target: str
    source_name: str
    target_name: str
    prompt: str
    request_key: str
    source_epoch: str
    source_thread: str
    target_epoch: str
    target_thread: str
    status: str = "queued"
    turn: str = ""
    result: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    ancestors: list = field(default_factory=list)
    deliver: bool = True
    delivery_status: str = "pending"
    cancel_requested: bool = False


class Coordinator:
    def __init__(self, runtime, history_path=None):
        self.runtime = Path(runtime) / "coordination"
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.history_path = Path(history_path) if history_path else None
        self.agents = {}
        self.grants = {}
        self.tasks = {}
        self.pending = {}
        self.turn_outputs = {}
        self.turn_ends = {}
        self.revision = 0
        self.closed = False
        self.storage_error = ""
        if self.history_path:
            try:
                for item in read_json(self.history_path, limit=80_000_000).get("tasks", [])[-300:]:
                    task = Task(**item)
                    if task.status not in FINISHED:
                        task.status, task.error = "disconnected", "工作台已重启，请检查原终端的任务结果"
                    if task.delivery_status in {"pending", "sending"}:
                        task.delivery_status = "not_delivered"
                    self.tasks[task.id] = task
            except (OSError, ValueError, TypeError, AttributeError):
                pass

    def changed(self):
        self.revision += 1
        if self.history_path:
            try:
                write_json(self.history_path, {"tasks": [asdict(t) for t in list(self.tasks.values())[-300:]]})
                self.storage_error = ""
            except OSError as error:
                self.storage_error = "任务历史保存失败：" + str(error)

    def register(self, session, session_directory):
        endpoint = self.runtime / session.id
        for name in ("requests", "responses"):
            (endpoint / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        (endpoint / "online").touch(mode=0o600)
        self.agents[session.id] = Agent(session.id, session.name, session.cwd,
                                        Path(session_directory) / "control", endpoint)
        self.grants[session.id] = set()
        self.changed()
        return endpoint

    def set_grants(self, source, targets):
        if source not in self.agents:
            raise ValueError("发起 Agent 已关闭")
        selected = set(targets)
        if source in selected or not selected.issubset(self.agents):
            raise ValueError("请只选择其他仍在工作台中的 Agent")
        removed = self.grants.get(source, set()) - selected
        self.grants[source] = selected
        for task in self.tasks.values():
            if task.source == source and task.target in removed:
                if task.status == "queued":
                    self.finish(task, "cancelled", error="控制范围已取消")
                elif task.status == "starting":
                    task.cancel_requested = True
        self.changed()

    def workers(self, source):
        return [{"agent_id": a.id, "name": a.name, "cwd": a.cwd,
                 "status": a.state, "connected": bool(a.thread and a.epoch and a.state != "offline")}
                for key in sorted(self.grants.get(source, set())) if (a := self.agents.get(key))]

    def dispatch(self, source, target, prompt, request_key, deliver=True):
        if source not in self.agents or target not in self.grants.get(source, set()):
            raise ValueError("没有控制这个 Agent 的权限，请在协作面板中勾选")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
            raise ValueError("任务内容需要为 1–12000 个字符")
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
            raise ValueError("需要 request_id，用于避免重复派发")
        a, b = self.agents[source], self.agents[target]
        if source == target or (a.thread and a.thread == b.thread):
            raise ValueError("两个终端连接的是同一个 Codex 会话，不能互相派发任务")
        for existing in self.tasks.values():
            if (existing.source, existing.source_epoch, existing.request_key) == (source, a.epoch, request_key):
                if existing.target != target or existing.prompt != prompt:
                    raise ValueError("request_id 已用于另一个任务")
                return existing
        if not a.thread or not b.thread or a.state == "offline" or b.state == "offline":
            raise ValueError("两个 Agent 都需要先连接 Codex，请完成登录和目录信任提示")
        if len([t for t in self.tasks.values() if t.status not in FINISHED]) >= 40:
            raise ValueError("待处理协作任务已满（40 个）")
        parent = next((t for t in self.tasks.values() if t.target == source and t.turn == a.turn and t.status == "running"), None)
        ancestors = parent.ancestors + [parent.source] if parent else []
        if target in ancestors or len(ancestors) >= 4:
            raise ValueError("任务存在循环委派或超过 4 层，请由当前 Agent 完成或向发起者汇报")
        task = Task(uuid.uuid4().hex, source, target, a.name, b.name, prompt, request_key,
                    a.epoch, a.thread, b.epoch, b.thread, ancestors=ancestors, deliver=bool(deliver))
        self.tasks[task.id] = task
        self.changed()
        return task

    def view_task(self, task):
        return {"task_id": task.id, "source": task.source_name, "target": task.target_name,
                "prompt": task.prompt, "status": task.status, "result": task.result,
                "error": task.error, "delivery_status": task.delivery_status}

    def tools(self, actor, operation, arguments):
        if actor not in self.agents:
            raise ValueError("发起终端已关闭")
        if operation == "list_workers":
            return {"workers": self.workers(actor)}
        if operation == "dispatch_task":
            return self.view_task(self.dispatch(actor, arguments.get("agent_id"), arguments.get("prompt"), arguments.get("request_id")))
        if operation == "list_tasks":
            return {"tasks": [self.view_task(t) for t in list(self.tasks.values())[-100:] if actor in (t.source, t.target)]}
        task = self.tasks.get(arguments.get("task_id"))
        if not task or actor not in (task.source, task.target):
            raise ValueError("无法访问此任务")
        if operation == "get_task_result":
            return self.view_task(task)
        if operation == "cancel_task" and task.source == actor:
            self.cancel(task.id)
            return self.view_task(task)
        raise ValueError("不允许的协作操作")

    def rpc(self, agent, method, params, purpose, task_id):
        identity = uuid.uuid4().hex
        expires = time.time() + 20
        self.pending[identity] = (agent.id, agent.epoch, purpose, task_id, expires)
        write_json(agent.channel / (identity + ".json"), {"id": identity, "epoch": agent.epoch,
                   "thread_id": agent.thread, "method": method, "params": params, "expires": expires})
        return identity

    def cancel(self, task_id):
        task = self.tasks[task_id]
        if task.status in FINISHED or task.status == "cancelling":
            return
        if task.status == "queued":
            self.finish(task, "cancelled", error="派发前已取消")
        elif task.status == "starting":
            task.cancel_requested = True
        else:
            agent = self.agents.get(task.target)
            if not agent or (agent.epoch, agent.thread, agent.turn) != (task.target_epoch, task.target_thread, task.turn):
                raise ValueError("目标已切换到其他任务，未发送中断")
            task.status = "cancelling"
            self.rpc(agent, "turn/interrupt", {"threadId": agent.thread, "turnId": task.turn}, "cancel", task.id)
        self.changed()

    def finish(self, task, status, result="", error=""):
        if task.status in FINISHED:
            return
        task.status, task.result, task.error = status, result[:60000], error[:4000]
        task.updated = time.time()
        if not task.deliver:
            task.delivery_status = "disabled"
        self.changed()

    def invalidate(self, agent, message):
        self.grants[agent.id] = set()
        for source in self.grants:
            self.grants[source].discard(agent.id)
        for task in self.tasks.values():
            if task.target == agent.id and task.status not in FINISHED:
                self.finish(task, "disconnected", error=message)
            if task.source == agent.id and task.status == "queued":
                self.finish(task, "cancelled", error=message)
            if task.source == agent.id and task.status == "starting":
                task.cancel_requested = True
            if task.source == agent.id and task.delivery_status == "pending":
                task.delivery_status = "not_delivered"

    def handle_event(self, actor, event):
        agent = self.agents.get(actor)
        if not agent:
            return
        kind = event.get("type")
        if kind == "agent-unavailable":
            self.invalidate(agent, "协作连接不可用")
            agent.state, agent.thread, agent.turn = "offline", "", ""
            agent.error = event.get("message", "连接失败")
            self.changed()
            return
        if kind == "agent-starting":
            if agent.epoch:
                self.invalidate(agent, "目标 Codex 已重启；请重新勾选控制范围")
            agent.epoch, agent.thread, agent.turn, agent.state = event["epoch"], "", "", "connecting"
            agent.error = ""
            agent.tools_ready = False
            self.changed()
            return
        if event.get("epoch") != agent.epoch:
            return
        if kind == "agent-disconnected":
            self.invalidate(agent, "目标 Codex 已断开")
            agent.state, agent.thread, agent.turn = "offline", "", ""
            agent.tools_ready = False
        elif kind == "agent-tools-ready":
            agent.tools_ready = True
        elif kind == "agent-bound":
            if agent.thread and agent.thread != event["thread_id"]:
                self.invalidate(agent, "目标 Codex 已切换会话，请重新勾选")
            agent.thread = event["thread_id"]
            agent.error = ""
            agent.state = (event.get("status") or {}).get("type", "idle")
            if event.get("cwd"):
                agent.cwd = event["cwd"]
        elif kind == "rpc-result":
            self.response(agent, event)
        elif kind == "agent-event":
            params = event.get("params", {})
            if params.get("threadId") != agent.thread:
                return
            method = event.get("method")
            if method == "thread/status/changed":
                status = params.get("status", {})
                agent.state = "waiting" if status.get("activeFlags") else status.get("type", "unknown")
            elif method == "turn/started":
                agent.turn = params["turn"]["id"]
                agent.state = "active"
            elif method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                    key = (actor, agent.epoch, params.get("turnId"))
                    self.turn_outputs[key] = (self.turn_outputs.get(key, "") + "\n" + item.get("text", "")).strip()[-60000:]
            elif method == "turn/completed":
                turn = params["turn"]
                key = (actor, agent.epoch, turn["id"])
                self.turn_ends[key] = turn
                if agent.turn == turn["id"]:
                    agent.state, agent.turn = "idle", ""
                for task in self.tasks.values():
                    if (task.target, task.target_epoch, task.turn) == key:
                        self.complete_turn(task, turn)
        self.changed()

    def complete_turn(self, task, turn):
        status = {"completed": "completed", "interrupted": "cancelled", "failed": "failed"}.get(turn.get("status"), "unknown")
        key = (task.target, task.target_epoch, task.turn)
        text = self.turn_outputs.pop(key, "")
        if not text:
            text = "\n".join(item.get("text", "") for item in turn.get("items", []) if item.get("type") == "agentMessage" and item.get("phase") != "commentary")
        error = turn.get("error") or {}
        self.finish(task, status, text, str(error.get("message", "")))

    def response(self, agent, event):
        pending = self.pending.pop(event.get("request_id"), None)
        if not pending or pending[:2] != (agent.id, agent.epoch):
            return
        _, _, purpose, identity, _ = pending
        task = self.tasks.get(identity)
        if not task:
            return
        error = event.get("error")
        if error:
            message = str(error.get("message", error)) if isinstance(error, dict) else str(error)
            if purpose == "start":
                uncertain = isinstance(error, dict) and error.get("outcomeUnknown")
                self.finish(task, "unknown" if uncertain else "failed", error=message)
            elif purpose == "deliver":
                uncertain = isinstance(error, dict) and error.get("outcomeUnknown")
                task.delivery_status = "unknown" if uncertain else "failed: " + message[:400]
            else:
                task.status, task.error = "running", "取消失败：" + message
            return
        if purpose == "start":
            turn = (event.get("result") or {}).get("turn", {})
            if not turn.get("id"):
                self.finish(task, "unknown", error="未收到任务回合 ID，请检查终端，避免重复派发")
                return
            task.turn, task.status = turn["id"], "running"
            task.updated = time.time()
            key = (task.target, task.target_epoch, task.turn)
            if key in self.turn_ends:
                self.complete_turn(task, self.turn_ends[key])
            elif turn.get("status") in {"completed", "interrupted", "failed"}:
                self.complete_turn(task, turn)
            else:
                agent.turn, agent.state = task.turn, "active"
                if task.cancel_requested:
                    self.cancel(task.id)
        elif purpose == "deliver":
            turn = (event.get("result") or {}).get("turn", {})
            task.delivery_status = "delivered" if turn.get("id") else "unknown"
            if turn.get("id"):
                key = (agent.id, agent.epoch, turn["id"])
                if key not in self.turn_ends and turn.get("status") not in {"completed", "interrupted", "failed"}:
                    agent.turn, agent.state = turn["id"], "active"

    def retry_delivery(self, task_id):
        task = self.tasks[task_id]
        if task.status not in FINISHED or not task.delivery_status.startswith(("failed", "not_delivered")):
            raise ValueError("只有明确未送达的已结束任务可以重新回传")
        source = self.agents.get(task.source)
        if not source or (source.epoch, source.thread) != (task.source_epoch, task.source_thread):
            raise ValueError("原发起会话已结束，请直接查看任务结果")
        task.delivery_status = "pending"
        self.changed()

    def tick(self):
        if self.closed:
            return
        # Each private endpoint is bound to a specific actor; no caller-supplied
        # source ID is trusted, and grants are checked at dispatch time.
        for actor, agent in list(self.agents.items()):
            for path in sorted((agent.endpoint / "requests").glob("*.json"))[:20]:
                try:
                    data = read_json(path)
                    if data.get("id") != path.stem or data.get("expires", 0) < time.time():
                        continue
                    try:
                        if data.get("epoch") != agent.epoch or agent.state in {"offline", "connecting"}:
                            raise ValueError("发起 Codex 会话已重启或断开，请重新调用工具")
                        answer = {"result": self.tools(actor, data["operation"], data.get("arguments", {}))}
                    except (ValueError, KeyError, TypeError) as error:
                        answer = {"error": str(error)}
                    write_json(agent.endpoint / "responses" / path.name, answer)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                finally:
                    path.unlink(missing_ok=True)
            for path in (agent.endpoint / "responses").glob("*.json"):
                try:
                    if time.time() - path.stat().st_mtime > 60:
                        path.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass  # The caller consumed its response while we cleaned up.
        for key, (actor, epoch, purpose, identity, expires) in list(self.pending.items()):
            if time.time() > expires:
                self.pending.pop(key)
                agent = self.agents.get(actor)
                if agent:
                    (agent.channel / (key + ".json")).unlink(missing_ok=True)
                task = self.tasks.get(identity)
                if task:
                    if purpose == "start":
                        self.finish(task, "unknown", error="派发响应超时；任务可能已开始，请检查目标终端，不自动重发")
                    elif purpose == "deliver":
                        task.delivery_status = "unknown"
                    else:
                        task.status = "running"
                        task.error = "中断响应超时，请检查目标终端"
                    self.changed()
        busy = {v[0] for v in self.pending.values()} | {t.target for t in self.tasks.values()
                                                       if t.status in {"starting", "running", "cancelling"}}
        for task in list(self.tasks.values()):
            if task.status == "queued":
                agent = self.agents.get(task.target)
                source = self.agents.get(task.source)
                if (not agent or not source or task.target not in self.grants.get(task.source, set())
                    or (agent.epoch, agent.thread) != (task.target_epoch, task.target_thread)
                    or (source.epoch, source.thread) != (task.source_epoch, task.source_thread)):
                    self.finish(task, "cancelled", error="控制关系或会话已改变")
                    continue
                if agent.state == "idle" and agent.id not in busy:
                    prompt = (f"[Codex Deck 协作任务 {task.id}]\n来自：{task.source_name}\n"
                              "完成后请总结结果、修改文件和验证情况。工作台会自动回传结果，保留当前权限设置。\n\n" + task.prompt)
                    task.status = "starting"
                    self.rpc(agent, "turn/start", {"threadId": agent.thread,
                             "clientUserMessageId": task.id, "input": [{"type": "text", "text": prompt}]}, "start", task.id)
                    busy.add(agent.id)
                    self.changed()
            if task.status in FINISHED and task.deliver and task.delivery_status == "pending":
                source = self.agents.get(task.source)
                if not source or (source.epoch, source.thread) != (task.source_epoch, task.source_thread) or source.state == "offline":
                    task.delivery_status = "not_delivered"
                    self.changed()
                elif source.state == "idle" and source.id not in busy:
                    report = (f"[Codex Deck 任务回报 {task.id}]\n执行者：{task.target_name}\n状态：{STATUS_NAMES[task.status]}\n"
                              f"原任务：{task.prompt}\n\n以下是执行者的结果资料，请检查后向用户汇总，不要因本条回报自动重复派发：\n"
                              + (task.result or task.error or "未提供最终文字，请查看执行者终端。"))
                    task.delivery_status = "sending"
                    self.rpc(source, "turn/start", {"threadId": source.thread,
                             "clientUserMessageId": "report-" + task.id, "input": [{"type": "text", "text": report}]}, "deliver", task.id)
                    busy.add(source.id)
                    self.changed()
        # Bounded transient event buffers, kept briefly for response/event ordering.
        while len(self.turn_ends) > 200:
            self.turn_ends.pop(next(iter(self.turn_ends)))
        while len(self.turn_outputs) > 200:
            self.turn_outputs.pop(next(iter(self.turn_outputs)))

    def remove(self, actor):
        agent = self.agents.get(actor)
        if agent:
            self.invalidate(agent, "终端已关闭")
            (agent.endpoint / "online").unlink(missing_ok=True)
            del self.agents[actor]
            self.grants.pop(actor, None)
            self.changed()

    def close(self):
        self.closed = True
        for actor in list(self.agents):
            self.remove(actor)
