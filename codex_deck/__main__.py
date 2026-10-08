import sys


def main():
    # Helpers run headless and must not initialize GTK or create another window.
    if sys.argv[1:2] in (["--internal-run-codex"], ["--internal-notify"]):
        helper = sys.argv.pop(1)
        if helper == "--internal-run-codex":
            from scripts.run_codex import main as run_helper
        else:
            from scripts.notify import main as run_helper
        return run_helper()
    try:
        from .macos import configure_bundle, configure_input_method
        configure_bundle()
        configure_input_method()
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("Vte", "2.91")
        gi.require_foreign("cairo")
        from gi.repository import Gtk, Vte
        import cairo
        if sys.platform == "darwin":
            import psutil
        from .core import terminal_shell
        terminal_shell()
    except (ImportError, ValueError, RuntimeError) as error:
        remedy = ("bash scripts/setup_macos.sh" if sys.platform == "darwin" else
                  "按 INSTALL.md 安装 Ubuntu 的 python3-gi、python3-gi-cairo、GTK3 和 VTE 依赖")
        print(f"图形或进程管理依赖不可用：{error}\n请执行 {remedy}。", file=sys.stderr)
        return 1
    if sys.argv[1:] == ["--check-deps"]:
        print(f"依赖检查通过：GTK 3 / VTE 2.91 / Cairo；Python：{sys.executable}")
        return 0
    from .app import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
