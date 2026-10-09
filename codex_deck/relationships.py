"""Directed collaboration relationships, shared by clicks and drag-and-drop."""
import json


def set_relationship(manager, source, target, enabled=True):
    if source not in manager.agents or target not in manager.agents:
        raise ValueError("这个 Agent 已关闭，请重新选择")
    if source == target:
        raise ValueError("请拖到另一个 Agent 上")
    first, second = manager.agents[source], manager.agents[target]
    if enabled and first.thread and first.thread == second.thread:
        raise ValueError("这两个终端是同一个 Codex 会话，请选择另一个 Agent")
    selected = set(manager.grants.get(source, set()))
    previous = target in selected
    (selected.add if enabled else selected.discard)(target)
    if previous != enabled:
        manager.set_grants(source, selected)
    return previous != enabled


def relation_edges(manager):
    return [(source, target) for source in manager.agents for target in manager.agents
            if target in manager.grants.get(source, set()) and source != target]


def drag_payload(manager, source, panel_id):
    agent = manager.agents[source]
    return json.dumps({"panel": panel_id, "source": source,
                       "epoch": agent.epoch, "thread": agent.thread}).encode()


def connect_drop(manager, payload, target, panel_id):
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("panel") != panel_id:
            raise ValueError("请拖动当前窗口内的 Agent")
        agent = manager.agents.get(data.get("source"))
        if agent is None or (agent.epoch, agent.thread) != (data.get("epoch"), data.get("thread")):
            raise ValueError("拖动期间会话已改变，请重新拖动")
        set_relationship(manager, agent.id, target)
        return agent.id
    except (TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("无法识别拖动的 Agent") from error
