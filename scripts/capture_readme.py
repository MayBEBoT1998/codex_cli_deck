#!/usr/bin/python3
"""Open a real, isolated demo window and capture a public README screenshot."""
import argparse
import signal
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.app import Deck, GLib, Gtk
from codex_deck.core import ROOT
from codex_deck.screenshot import save_widget_png


class ReadmeDeck(Deck):
    def __init__(self, work, output):
        self.demo_work = work
        self.output = output
        self.captured = False
        self.deadline = time.monotonic() + 20
        for name in ("web", "api", "tests", "ideas"):
            (work / "projects" / name).mkdir(parents=True)
        args = SimpleNamespace(cwd=str(work / "projects/web"), restore=False, shell=True,
                               demo=True, smoke_test=None, state_dir=str(work / "state"))
        super().__init__(args)
        self.resize(1280, 880)

    def open_initial_sessions(self):
        for index, (folder, name) in enumerate((
            ("web", "前端 · 界面工作台"), ("api", "后端 · 接口联调"),
            ("tests", "测试 · 回归验证"), ("ideas", "灵感 · 下一步"),
        ), 1):
            self.new_session(name=name, cwd=str(self.demo_work / "projects" / folder),
                             avatar=f"portrait-{index:02}", activate=False, persist=False)
        self.activate(self.sessions[0].id)
        return False

    def setup_demo(self):
        if self.closing:
            return False
        if time.monotonic() > self.deadline:
            print("截图失败：示例终端启动超时。", file=sys.stderr)
            self.shutdown()
            return False
        if len(self.pids) < 4:
            return True
        # The shell prompt contains only its public demo folder, no host/user names.
        for session in self.sessions:
            folder = Path(session.cwd).name
            self.views[session.id].feed_child(f"PS1='❯ projects/{folder} $ '; clear\r".encode())
        GLib.timeout_add(400, self.show_features)
        return False

    def show_features(self):
        if self.closing:
            return False
        terminal = self.views[self.sessions[0].id]
        terminal.reset(True, True)
        terminal.feed((
            "\r\n  \x1b[1;38;5;115mCODEX DECK\x1b[0m  /  DEMO\r\n"
            "\r\n  每个项目，一个终端。每次完成，一个提醒。\r\n"
            "\r\n  \x1b[38;5;115m＋\x1b[0m  新建终端，选择各自的项目目录\r\n"
            "\r\n  \x1b[38;5;115m↕\x1b[0m  Ctrl + ↑ / ↓ 平滑切换，后台任务继续运行\r\n"
            "\r\n  \x1b[38;5;115m✦\x1b[0m  完成后头像放大闪烁，点击标签确认\r\n"
            "\r\n  \x1b[38;5;115m☀\x1b[0m  日夜主题 · 自定义头像集 · 二次元桌面宠物\r\n"
            "\r\n  \x1b[38;5;245m示例项目与完成提醒用于界面演示。\x1b[0m\r\n"
            "\r\n  ❯ projects/web $ "
        ).encode())
        self.sessions[1].apply({"type": "agent-turn-complete", "turn-id": "readme-demo",
                                "last-assistant-message": "演示：任务完成后，点击此标签查看结果。"})
        self.refresh()
        GLib.timeout_add(900, self.capture)
        return False

    def capture(self):
        if self.closing:
            return False
        try:
            save_widget_png(self, self.output)
            self.captured = True
            print("已保存 README 截图。", flush=True)
        except Exception as error:
            print(f"截图失败：{error}", file=sys.stderr)
        self.shutdown()
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/images/workspace.png")
    args = parser.parse_args()
    if not Gtk.init_check()[0]:
        parser.exit(1, "无法连接桌面，请在图形桌面的终端执行此命令。\n")
    with tempfile.TemporaryDirectory(prefix="codex-deck-preview-", dir="/tmp") as directory:
        window = ReadmeDeck(Path(directory), args.output.resolve())
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, lambda *_: GLib.idle_add(window.shutdown))
        Gtk.main()
        return 0 if window.captured else 1


if __name__ == "__main__":
    sys.exit(main())
