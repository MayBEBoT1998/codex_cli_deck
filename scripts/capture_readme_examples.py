#!/usr/bin/python3
"""Capture three real GTK examples and check relationship controls; no model calls."""
import argparse
import faulthandler
import json
from pathlib import Path
import signal
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.app import Deck, GLib, Gtk
from codex_deck.core import ROOT
from codex_deck.relationships import drag_payload, relation_edges
from codex_deck.screenshot import save_widget_png


ARTIFACT = ROOT / "artifacts/readme-examples-check.json"


def write_result(result):
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    pending = ARTIFACT.with_suffix(".tmp")
    pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(ARTIFACT)


class ExamplesDeck(Deck):
    def __init__(self, work, output):
        self.demo_work, self.output = work, output
        self.output.mkdir(parents=True, exist_ok=True)
        self.checks, self.screenshots = [], []
        self.passed = False
        self.progress("starting")
        self.deadline = time.monotonic() + 30
        self.phase = 0
        for folder in ("planner", "web", "api", "tests"):
            (work / "projects" / folder).mkdir(parents=True)
        args = SimpleNamespace(cwd=str(work / "projects/planner"), restore=False, shell=True,
                               demo=True, smoke_test=None, state_dir=str(work / "state"))
        super().__init__(args)
        self.resize(1320, 870)
        self.set_title("Codex Deck · 使用示例")

    def open_initial_sessions(self):
        for index, (folder, name) in enumerate((("planner", "项目协调"), ("web", "前端开发"),
                                                ("api", "后端接口"), ("tests", "测试复核")), 1):
            self.new_session(name=name, cwd=str(self.demo_work / "projects" / folder),
                             avatar=f"portrait-{index:02}", activate=False, persist=False)
        self.activate(self.sessions[0].id)
        return False

    def check(self, name, condition):
        self.checks.append({"name": name, "passed": bool(condition)})
        self.progress(name)
        if not condition:
            raise AssertionError(name)

    def setup_demo(self):
        if self.closing:
            return False
        if len(self.pids) < 4:
            if time.monotonic() > self.deadline:
                self.finish("示例终端启动超时")
                return False
            return True
        try:
            for session in self.sessions:
                folder = Path(session.cwd).name
                self.views[session.id].feed_child(f"PS1='❯ projects/{folder} $ '; clear\r".encode())
                for event in ({"type": "agent-starting"},
                              {"type": "agent-bound", "thread_id": "example-" + session.id,
                               "status": {"type": "idle"}}, {"type": "agent-tools-ready"}):
                    self.coordinator.handle_event(session.id, dict(event, epoch="example"))
            self.open_coordination(self.sessions[0].id)
            self.coordination_panel.resize(920, 610)
            GLib.timeout_add(700, self.step)
        except Exception as error:
            self.finish(str(error))
        return False

    def step(self):
        if self.closing:
            return False
        try:
            panel = self.coordination_panel
            a, b, c, d = [s.id for s in self.sessions]
            if self.phase == 0:
                self.check("empty_relationship_state", panel.relation_stack.get_visible_child_name() == "empty")
                self.check("task_form_and_table_removed", not any(hasattr(panel, key) for key in ("prompt", "tree", "store")))
                panel.source.set_active_id(a)
                panel.target.set_active_id(b)
                panel.connect_button.clicked()
                self.check("selector_creates_directed_relationship", relation_edges(self.coordinator) == [(a, b)])
                self.check("agent_cards_are_registered_drag_targets", all(card.drag_source_get_target_list() and card.drag_dest_get_target_list()
                                                                         for card in panel.cards.values()))
                # Exercise the production drop receiver without moving the user's mouse.
                payload = drag_payload(self.coordinator, a, panel.panel_id)
                self.check("drop_receiver_connects_another_agent", panel.receive_drop(payload, c))
                self.check("drop_does_not_add_reverse_grant", a not in self.coordinator.grants[c])
                self.check("duplicate_drop_is_idempotent", panel.receive_drop(payload, c) and len(relation_edges(self.coordinator)) == 2)
                self.check("self_drop_is_rejected", not panel.receive_drop(payload, a))
                panel.relationship_rows[a, c].get_children()[-1].clicked()
                self.check("remove_button_revokes_only_one_relation", relation_edges(self.coordinator) == [(a, b)])
                panel.receive_drop(payload, c)
                panel.receive_drop(drag_payload(self.coordinator, c, panel.panel_id), d)
                self.check("another_agent_can_initiate", d in self.coordinator.grants[c])
                panel.source.set_active_id(a)
                panel.feedback.set_text("示例关系：项目协调 → 前端 / 后端；后端接口 → 测试复核。")
                self.phase = 1
            elif self.phase == 1:
                self.capture(panel, "collaboration-night.png")
                self.set_preference("theme", "day")
                self.check("day_theme_applied", self.settings.values["theme"] == "day")
                panel.source.set_active_id(c)
                panel.feedback.set_text("任意 Agent 都可发起；点击关系右侧 × 解除，点击头像打开终端。")
                self.phase = 2
            elif self.phase == 2:
                self.capture(panel, "collaboration-day.png")
                panel.hide()
                self.show_day_workspace()
                self.present()
                self.phase = 3
            else:
                self.capture(self, "workspace-day.png")
                self.check("three_readme_screenshots_saved", len(self.screenshots) == 3)
                self.finish()
                return False
        except Exception as error:
            self.finish(str(error))
            return False
        return True

    def show_day_workspace(self):
        terminal = self.views[self.sessions[0].id]
        terminal.reset(True, True)
        terminal.feed((
            "\r\n  \x1b[1;38;5;115mCODEX DECK\x1b[0m  /  协作使用示例\r\n"
            "\r\n  日间界面 · 清晰终端 · 多 Agent 协作\r\n"
            "\r\n  \x1b[38;5;111m协同关系\x1b[0m\r\n"
            "\r\n    项目协调 ──→ 前端开发\r\n"
            "\r\n    项目协调 ──→ 后端接口 ──→ 测试复核\r\n"
            "\r\n  \x1b[38;5;150m使用方式\x1b[0m\r\n"
            "\r\n    1. 在「Agent 协作」窗口拖动卡片，建立关系\r\n"
            "\r\n    2. 回到发起者终端，用自然语言描述工作\r\n"
            "\r\n    3. 执行者完成后，结果自动回传\r\n"
            "\r\n  \x1b[2m这里是界面演示，未调用模型。\x1b[0m\r\n"
            "\r\n  \x1b[48;2;55;59;64m ❯ 让前端 Agent 介绍界面结构，再请测试 Agent 做检查。     \x1b[0m\r\n"
            "\r\n  \x1b[38;5;111mprojects/planner\x1b[0m  ·  输入框与状态文字保持清晰\r\n"
        ).encode())
        self.sessions[1].apply({"type": "agent-turn-complete", "turn-id": "example-notification",
                                "last-assistant-message": "示例：前端已完成，点击查看。"})
        self.refresh()
        self.path_label.set_text("⌁  projects/planner  ·  示例工作区")
        terminal.grab_focus()

    def capture(self, widget, name):
        self.progress("saving " + name)
        width, height = save_widget_png(widget, self.output / name)
        self.screenshots.append({"file": name, "width": width, "height": height})
        self.progress("saved " + name)
        print("已保存 " + name, flush=True)

    def progress(self, stage, error=None):
        result = {"passed": self.passed, "checks": self.checks, "screenshots": self.screenshots,
                  "stage": stage, "model_calls": 0, "error": error,
                  "drag_validation": "GTK targets plus production drop receiver; physical pointer gesture not automated."}
        write_result(result)
        return result

    def finish(self, error=None):
        self.passed = error is None
        result = self.progress("failed" if error else "completed", error)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        self.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/images")
    args = parser.parse_args()
    write_result({"passed": False, "stage": "initializing", "checks": [], "screenshots": [], "model_calls": 0})
    with ARTIFACT.with_suffix(".log").open("w", encoding="utf-8") as diagnostic:
        faulthandler.enable(file=diagnostic)
        try:
            if not Gtk.init_check()[0]:
                raise RuntimeError("需要图形桌面：请在本机运行 /usr/bin/python3 scripts/capture_readme_examples.py")
            with tempfile.TemporaryDirectory(prefix="codex-deck-examples-") as directory:
                deck = ExamplesDeck(Path(directory), args.output.resolve())
                for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    signal.signal(signum, lambda *_: GLib.idle_add(deck.shutdown))
                Gtk.main()
                return 0 if deck.passed else 1
        except Exception as error:
            result = json.loads(ARTIFACT.read_text(encoding="utf-8"))
            result.update(passed=False, stage="failed", error=str(error))
            write_result(result)
            traceback.print_exc(file=diagnostic)
            print("截图失败：" + str(error), file=sys.stderr)
            return 1
        finally:
            faulthandler.disable()


if __name__ == "__main__":
    sys.exit(main())
