#!/usr/bin/python3
"""Desktop verification using two real Codex TUIs and a brief read-only task.

This calls the signed-in model. It asks Agent A to delegate to B through Deck's
MCP tools, then checks the task result and return message on the original A.
No global Codex configuration is changed. Test logs stay under artifacts/.
"""
import argparse
import json
from pathlib import Path
import signal
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.app import Deck, Gdk, GLib, Gtk
from codex_deck.core import ROOT


class CheckDeck(Deck):
    def __init__(self, state, output):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.passed = False
        self.stage = 0
        self.deadline = time.monotonic() + 240
        self.last_phase = ""
        args = SimpleNamespace(cwd=str(ROOT), shell=True, demo=False, smoke_test=None,
                               restore=False, state_dir=str(state))
        super().__init__(args)
        self.set_title("Codex Deck · 真实协作验证（只读）")
        self.resize(1360, 900)
        GLib.timeout_add(300, self.check_step)

    def open_initial_sessions(self):
        self.a = self.new_session(name="协作测试 A · 发起者", cwd=str(ROOT), activate=False)
        self.b = self.new_session(name="协作测试 B · 执行者", cwd=str(ROOT), activate=False)
        self.activate(self.a.id)
        return False

    def phase(self, message):
        if self.last_phase != message:
            print(message, flush=True)
            self.last_phase = message

    def check(self, name, condition):
        self.checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    def check_step(self):
        if self.closing:
            return False
        try:
            if time.monotonic() > self.deadline:
                raise TimeoutError("真实协作验证超时；请检查两个终端是否停在登录或目录信任界面")
            if self.stage == 0:
                if len(self.pids) < 2:
                    return True
                for session in (self.a, self.b):
                    self.views[session.id].feed_child(b"codex --no-alt-screen --sandbox read-only\r")
                self.stage = 1
                self.phase("正在启动两个真实 Codex。若出现登录/目录信任提示，请在对应终端完成。")
            elif self.stage == 1:
                agents = [self.coordinator.agents[s.id] for s in (self.a, self.b)]
                failed = [a for a in agents if a.error]
                if failed:
                    raise RuntimeError("; ".join(a.error for a in failed))
                if not all(a.thread and a.tools_ready and a.state == "idle" for a in agents):
                    return True
                self.check("two_native_tuis_bound_to_distinct_codex_threads", agents[0].thread != agents[1].thread)
                self.check("deck_mcp_tools_loaded_in_both_agents", all(a.tools_ready for a in agents))
                self.coordinator.set_grants(self.a.id, [self.b.id])
                self.coordinator.set_grants(self.b.id, [self.a.id])
                self.check("either_agent_can_be_an_initiator", len(self.coordinator.workers(self.b.id)) == 1)
                self.activate(self.a.id)
                message = ("这是 Codex Deck 的只读协作集成检查，不修改文件，不执行 shell 命令。"
                           "请先调用 deck 的 list_workers 工具，再用 dispatch_task 向唯一可用的 Agent 派发一次任务："
                           "不要使用工具，只回复 DECK_WORKER_OK。request_id 使用 deck-integration-check。"
                           "派发后结束本轮，等待工作台自动回传，不要轮询。收到带 DECK_WORKER_OK 的任务回报后，只回复 DECK_TEAM_OK。")
                self.views[self.a.id].feed_child(b"\x1b[200~" + message.encode() + b"\x1b[201~\r")
                self.stage = 2
                self.phase("已向 A 提交测试请求，等待 A 通过 MCP 派给 B。仅产生简短模型调用。")
            elif self.stage == 2:
                tasks = [t for t in self.coordinator.tasks.values() if t.source == self.a.id and t.target == self.b.id]
                if not tasks:
                    return True
                task = tasks[0]
                if task.status in {"failed", "cancelled", "disconnected", "unknown"}:
                    raise RuntimeError(task.error or task.status)
                if task.status != "completed":
                    return True
                self.check("agent_a_dispatched_through_mcp", task.request_key == "deck-integration-check")
                self.check("agent_b_returned_real_model_result", "DECK_WORKER_OK" in task.result)
                self.test_task = task
                self.stage = 3
                self.phase("B 已完成，正在验证自动回传和 A 的汇总。")
            elif self.stage == 3:
                if self.test_task.delivery_status.startswith("failed"):
                    raise RuntimeError(self.test_task.delivery_status)
                if self.test_task.delivery_status != "delivered":
                    return True
                outputs = [text for key, text in self.coordinator.turn_outputs.items() if key[0] == self.a.id]
                if not any("DECK_TEAM_OK" in text for text in outputs):
                    return True
                self.check("result_delivered_to_original_initiator", True)
                self.check("initiator_continued_and_summarized", True)
                self.open_coordination(self.a.id)
                self.stage = 4
            elif self.stage == 4:
                panel = self.coordination_panel
                window = panel.get_window()
                image = Gdk.pixbuf_get_from_window(window, 0, 0, window.get_width(), window.get_height())
                image.savev(str(self.output / "coordination-panel.png"), "png", [], [])
                self.finish()
                return False
        except Exception as error:
            self.finish(error)
            return False
        return True

    def finish(self, error=None):
        self.passed = error is None
        result = {"passed": self.passed, "checks": self.checks, "error": str(error) if error else None,
                  "validation": "Real native Codex TUI, MCP delegation, worker model response, and origin continuation."}
        if error:
            result["terminals"] = {s.name: (self.views[s.id].get_text(None, None)[0] or "")[-8000:] for s in self.sessions}
            for s in self.sessions:
                log = self.runtime / s.id / "app-server.log"
                if log.exists():
                    result.setdefault("server_logs", {})[s.name] = log.read_text(errors="replace")[-4000:]
        (self.output / "coordination-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        self.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts")
    args = parser.parse_args()
    if not Gtk.init_check()[0]:
        parser.exit(1, "需要图形桌面：请在本机终端运行 /usr/bin/python3 scripts/check_coordination.py\n")
    with tempfile.TemporaryDirectory(prefix="deck-coordination-check-") as directory:
        window = CheckDeck(Path(directory), args.output.resolve())
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, lambda *_: GLib.idle_add(window.shutdown))
        Gtk.main()
        return 0 if window.passed else 1


if __name__ == "__main__":
    sys.exit(main())
