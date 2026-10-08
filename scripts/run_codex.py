#!/usr/bin/python3
"""Wrap only this terminal's Codex invocation with a completion callback."""
import json
from pathlib import Path
import signal
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.core import emit_event


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    executable = config["codex"]
    if not executable:
        print("未找到 Codex CLI。安装并登录后，重新打开 Codex Deck。", file=sys.stderr)
        return 127
    callback = ["/usr/bin/python3", config["notify"], config["events"]]
    args = [executable, "-c", "notify=" + json.dumps(callback, ensure_ascii=False), *sys.argv[2:]]
    emit_event(config["events"], {"type": "codex-start"})
    try:
        if config.get("coordination") and "--no-daemon" not in sys.argv[2:]:
            try:
                from codex_deck.codex_relay import run_managed, supports_managed
            except (ImportError, SyntaxError) as error:
                print(f"协作模块未就绪，将以普通模式启动 Codex：{error}", file=sys.stderr)
                emit_event(config["events"], {"type": "agent-unavailable", "message": str(error)})
            else:
                if supports_managed(sys.argv[2:]):
                    signal.signal(signal.SIGINT, lambda *_: None)
                    try:
                        return run_managed(config, sys.argv[2:], callback)
                    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                        print(f"协作连接失败：{error}\n可运行 codex --no-daemon 使用普通终端模式。", file=sys.stderr)
                        emit_event(config["events"], {"type": "agent-unavailable", "message": str(error)})
                        return 1
        child = subprocess.Popen(args)
        # The terminal sends Ctrl+C to the whole foreground group. Let Codex handle it.
        signal.signal(signal.SIGINT, lambda *_: None)
        code = child.wait()
        return code if code >= 0 else 128 - code
    except OSError as error:
        print(f"无法启动 Codex：{error}", file=sys.stderr)
        return 127
    finally:
        emit_event(config["events"], {"type": "codex-exit"})


if __name__ == "__main__":
    sys.exit(main())
