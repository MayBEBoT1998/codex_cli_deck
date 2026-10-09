#!/usr/bin/python3
"""Install a self-contained user copy, CLI entry, and Ubuntu application entry."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
APP_ID = "codex-deck"
MARKER = ".installed.json"
DESKTOP_MARKER = "X-Codex-Deck-Installer=1"


def state_home():
    value = os.environ.get("XDG_STATE_HOME", "")
    return (Path(value) if value and Path(value).is_absolute() else Path.home() / ".local/state") / APP_ID


def payload_files(source):
    """Only runtime code and processed artwork; no state, Git, or originals."""
    files = {Path(p) for p in ("launch.sh", "install.sh", "README.md", "INSTALL.md",
                              "assets/icon.svg", "assets/style.css", "assets/day.css")}
    files.update(p.relative_to(source) for p in (source / "codex_deck").glob("*.py"))
    files.update(p.relative_to(source) for p in (source / "scripts").glob("*.py"))
    catalog = Path("assets/avatars/portraits/catalog.json")
    files.add(catalog)
    for item in json.loads((source / catalog).read_text(encoding="utf-8"))["items"]:
        files.add(catalog.parent / item["file"])
    for sprite in (source / "assets/pets/anime").glob("oneesan-*/sprite.json"):
        relative = sprite.relative_to(source)
        files.add(relative)
        data = json.loads(sprite.read_text(encoding="utf-8"))
        for action in ("idle", "walk", "climb"):
            files.update(relative.parent / name for name in data[action])
    for relative in files:
        path = source / relative
        if relative.is_absolute() or ".." in relative.parts or path.is_symlink() or not path.is_file():
            raise ValueError(f"无效或缺失的程序文件：{relative}")
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError(f"程序文件位于源码目录外：{relative}")
    return sorted(files)


def desktop_string(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def desktop_exec(value):
    # Desktop Entry quoting has two layers: string escapes, then Exec escapes.
    quoted = '"' + "".join("\\" + c if c in '\\"`$' else c for c in str(value)) + '"'
    return desktop_string(quoted).replace("%", "%%")


def desktop_entry(command, app_dir):
    return ("[Desktop Entry]\nType=Application\nVersion=1.0\nName=Codex Deck\n"
            "Comment=Multi-terminal workspace for Codex\nComment[zh_CN]=Codex 多终端工作台\n"
            f"Exec=/usr/bin/python3 {desktop_exec(command)}\n"
            f"Icon={desktop_string(app_dir / 'assets/icon.svg')}\n"
            "Terminal=false\nCategories=Development;\n"
            "Keywords=Codex;Terminal;Agent;\nStartupNotify=true\nStartupWMClass=codex-deck\n"
            f"{DESKTOP_MARKER}\n")


def destinations(prefix):
    return (prefix / "share" / APP_ID, prefix / "bin" / APP_ID,
            prefix / "share/applications" / (APP_ID + ".desktop"))


def owned_installation(app_dir, command, desktop):
    metadata = None
    if app_dir.exists() or app_dir.is_symlink():
        if app_dir.is_symlink() or not (app_dir / MARKER).is_file():
            raise ValueError(f"此目录不是安装器创建的，已保留：{app_dir}")
        metadata = json.loads((app_dir / MARKER).read_text(encoding="utf-8"))
        if metadata.get("app") != APP_ID:
            raise ValueError(f"此目录属于其他程序，已保留：{app_dir}")
    if command.exists() or command.is_symlink():
        if not command.is_symlink() or command.resolve() != (app_dir / "scripts/launch_deck.py").resolve():
            raise ValueError(f"同名命令不是本程序创建的，已保留：{command}")
    if desktop.exists() or desktop.is_symlink():
        if desktop.is_symlink() or DESKTOP_MARKER not in desktop.read_text(encoding="utf-8").splitlines():
            raise ValueError(f"同名桌面入口不是本程序创建的，已保留：{desktop}")
    return metadata


def migrate_state(source, destination):
    """First install copies existing preferences and user avatars, never overwrites."""
    if destination.exists():
        return False
    destination.mkdir(parents=True, mode=0o700)
    legacy = source / ".state"
    paths = [legacy / "settings.json", legacy / "coordination.json"]
    paths.extend((legacy / "avatars").rglob("*"))
    copied = False
    for path in paths:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(legacy.resolve()):
            continue
        target = destination / path.relative_to(legacy)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(path, target)
        target.chmod(0o600)
        copied = True
    return copied


def refresh_desktop(prefix):
    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run([updater, str(prefix / "share/applications")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def install(source, prefix, state_dir=None):
    app_dir, command, desktop = destinations(prefix)
    if app_dir == source or source.is_relative_to(app_dir):
        raise ValueError("安装位置不能覆盖正在使用的源码目录")
    metadata = owned_installation(app_dir, command, desktop)
    if state_dir is None:
        state_dir = Path(metadata["state_dir"]) if metadata else state_home()
    state_dir = state_dir.resolve()
    if state_dir == app_dir or state_dir.is_relative_to(app_dir):
        raise ValueError("设置目录必须位于程序安装目录之外，以便更新时保留")
    files = payload_files(source)
    app_dir.parent.mkdir(parents=True, exist_ok=True)
    command.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".codex-deck-install-", dir=app_dir.parent) as temporary:
        staging = Path(temporary) / APP_ID
        staging.mkdir()
        for relative in files:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
            target.chmod(0o755 if relative.suffix == ".sh" or relative == Path("scripts/launch_deck.py") else 0o644)
        (staging / MARKER).write_text(json.dumps({"app": APP_ID, "state_dir": str(state_dir)}, indent=2) + "\n")
        staged_desktop = Path(temporary) / "codex-deck.desktop"
        staged_desktop.write_text(desktop_entry(command, app_dir), encoding="utf-8")
        validator = shutil.which("desktop-file-validate")
        if validator:
            subprocess.run([validator, str(staged_desktop)], check=True)
        migrated = migrate_state(source, state_dir)
        previous = Path(temporary) / "previous"
        if app_dir.exists():
            app_dir.rename(previous)
        try:
            staging.rename(app_dir)
        except OSError:
            if previous.exists():
                previous.rename(app_dir)
            raise
        if not command.is_symlink():
            command.symlink_to(Path("../share") / APP_ID / "scripts/launch_deck.py")
        staged_desktop.replace(desktop)
    refresh_desktop(prefix)
    return {"command": command, "app_dir": app_dir, "state_dir": state_dir, "migrated": migrated}


def uninstall(prefix):
    app_dir, command, desktop = destinations(prefix)
    metadata = owned_installation(app_dir, command, desktop)
    if command.is_symlink():
        command.unlink()
    desktop.unlink(missing_ok=True)
    if metadata:
        shutil.rmtree(app_dir)
    refresh_desktop(prefix)
    return metadata


def main():
    parser = argparse.ArgumentParser(description="安装 Codex Deck 到用户目录，无需 sudo")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local", help="安装前缀（默认 ~/.local）")
    parser.add_argument("--state-dir", type=Path, help="设置目录（默认 ~/.local/state/codex-deck，支持 XDG_STATE_HOME）")
    parser.add_argument("--uninstall", action="store_true", help="卸载程序和入口，保留设置与自定义头像")
    args = parser.parse_args()
    prefix = args.prefix.expanduser().resolve()
    try:
        if args.uninstall:
            uninstall(prefix)
            print("Codex Deck 已卸载；设置和自定义头像已保留。")
            return 0
        result = install(ROOT, prefix, args.state_dir.expanduser() if args.state_dir else None)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"安装操作未完成：{error}", file=sys.stderr)
        return 1
    print("Codex Deck 安装完成，可在应用菜单搜索 Codex Deck，或运行 codex-deck。")
    print(f"程序：{result['app_dir']}\n设置：{result['state_dir']}")
    if result["migrated"]:
        print("已复制原有设置、自定义头像和协作记录。")
    if str(prefix / "bin") not in os.environ.get("PATH", "").split(os.pathsep):
        import shlex
        print("首次使用请重新登录，或在当前终端执行：")
        print("export PATH=" + shlex.quote(str(prefix / "bin")) + ':"$PATH"')
    print("更新：在新版源码目录再次运行 bash install.sh。卸载：codex-deck --uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())
