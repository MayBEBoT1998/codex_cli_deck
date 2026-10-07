"""State and event handling, independent of GTK and the display server."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import shutil
import signal
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AVATAR = "portrait-01"
# Migrate saved terminal lists written by older running instances on restart.
LEGACY_AVATARS = {
    **{key: f"portrait-{index:02}" for index, key in enumerate(("luna", "miku", "akari", "yuki", "ren", "sora"), 1)},
    **{f"miku-{index:02}": f"portrait-{index:02}" for index in range(1, 11)},
}


@dataclass
class Session:
    name: str
    cwd: str
    avatar: str = DEFAULT_AVATAR
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    codex_running: bool = False
    unread: bool = False
    exited: bool = False
    error: str = ""
    summary: str = ""
    completed_at: float = 0
    turns: int = 0
    seen_turns: list[str] = field(default_factory=list)

    @property
    def status(self):
        if self.error:
            return "启动失败"
        if self.unread:
            return "完成 · 待查看"
        if self.exited:
            return "终端已退出"
        if self.codex_running:
            return "Codex 在线"
        return "终端就绪"

    def apply(self, event):
        """Return True only for a new completed turn, never for terminal output."""
        kind = event.get("type")
        if kind == "codex-start":
            self.codex_running = True
            self.exited = False
        elif kind == "codex-exit":
            self.codex_running = False
        elif kind == "agent-turn-complete":
            turn = str(event.get("turn-id") or event.get("event-id") or "")
            if turn and turn in self.seen_turns:
                return False
            if turn:
                self.seen_turns = (self.seen_turns + [turn])[-128:]
            self.unread = True
            self.completed_at = time.time()
            self.summary = str(event.get("last-assistant-message") or "本轮任务已完成，点击终端查看结果。")[:4000]
            self.turns += 1
            return True
        return False

    def metadata(self):
        return {k: getattr(self, k) for k in ("name", "cwd", "avatar")}


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.values = {"font_size": 12, "auto_codex": True, "animate": True,
                       "theme": "night", "motion": True, "avatar_pack": "portraits",
                       "pets_enabled": True, "pet_count": 3,
                       "pet_style": "anime", "pet_character": "all", "pet_height": 144,
                       "sound": False, "sessions": [], "default_cwd": str(ROOT)}
        try:
            loaded = json.loads(self.path.read_text())
            if isinstance(loaded, dict):
                self.values.update({k: v for k, v in loaded.items() if k in self.values})
        except (OSError, ValueError):
            pass
        for key in ("auto_codex", "animate", "sound", "motion", "pets_enabled"):
            self.values[key] = self.values[key] if isinstance(self.values[key], bool) else False
        size = self.values["font_size"]
        self.values["font_size"] = max(9, min(24, size)) if isinstance(size, int) else 12
        if not isinstance(self.values["sessions"], list):
            self.values["sessions"] = []
        if not isinstance(self.values["default_cwd"], str):
            self.values["default_cwd"] = str(ROOT)
        if self.values["theme"] not in ("day", "night"):
            self.values["theme"] = "night"
        if not isinstance(self.values["avatar_pack"], str) or self.values["avatar_pack"] in ("auto", "builtin", "miku"):
            self.values["avatar_pack"] = "portraits"
        for metadata in self.values["sessions"]:
            if isinstance(metadata, dict) and isinstance(metadata.get("avatar"), str):
                metadata["avatar"] = LEGACY_AVATARS.get(metadata["avatar"], metadata["avatar"])
        count = self.values["pet_count"]
        self.values["pet_count"] = max(1, min(5, count)) if type(count) is int else 3
        if self.values["pet_style"] not in ("anime", "cats"):
            self.values["pet_style"] = "anime"
        if not isinstance(self.values["pet_character"], str):
            self.values["pet_character"] = "all"
        height = self.values["pet_height"]
        self.values["pet_height"] = max(96, min(192, height)) if type(height) is int else 144

    def save(self, sessions):
        self.values["sessions"] = [s.metadata() for s in sessions]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2) + "\n")
        os.chmod(temp, 0o600)
        temp.replace(self.path)


def emit_event(directory, event):
    directory = Path(directory)
    if not directory.is_dir():
        return False  # Window/session was closed before the notification arrived.
    payload = dict(event, **{"event-id": uuid.uuid4().hex})
    name = f"{time.time_ns()}-{uuid.uuid4().hex}"
    temp = directory / (name + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            os.chmod(temp, 0o600)
            json.dump(payload, handle, ensure_ascii=False)
        temp.rename(directory / (name + ".json"))
        return True
    except OSError:
        temp.unlink(missing_ok=True)
        return False


def drain_events(directory):
    events = []
    for path in sorted(Path(directory).glob("*.json"))[:100]:
        try:
            if path.stat().st_size <= 65536:
                event = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(event, dict):
                    events.append(event)
        except (OSError, ValueError):
            pass
        finally:
            path.unlink(missing_ok=True)
    return events


def prepare_session(directory, session, codex, auto_codex=False, clean_shell=False):
    """Install an isolated shell wrapper; never edit the user's shell/Codex config."""
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    events = directory / "events"
    events.mkdir(mode=0o700, exist_ok=True)
    bin_dir = directory / "bin"
    bin_dir.mkdir(mode=0o700, exist_ok=True)
    config = {"events": str(events), "codex": codex,
              "notify": str(ROOT / "scripts" / "notify.py")}
    config_path = directory / "session.json"
    config_path.write_text(json.dumps(config))
    os.chmod(config_path, 0o600)
    wrapper = bin_dir / "codex"
    # repr is Python literal quoting, shlex.quote below is shell quoting.
    wrapper.write_text("#!/usr/bin/python3\nimport runpy, sys\n"
                       f"sys.argv = [{str(ROOT / 'scripts' / 'run_codex.py')!r}, {str(config_path)!r}] + sys.argv[1:]\n"
                       f"runpy.run_path({str(ROOT / 'scripts' / 'run_codex.py')!r}, run_name='__main__')\n")
    wrapper.chmod(0o700)
    import shlex
    rc = directory / "bashrc"
    source = "" if clean_shell else 'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n'
    rc.write_text(source + f"export PATH={shlex.quote(str(bin_dir))}:\"$PATH\"\n"
                  + "unalias codex 2>/dev/null || true\nunset -f codex 2>/dev/null || true\n"
                  + "export TERM=xterm-256color\n"
                  + "PS1='\\[\\e[38;5;115m\\]❯\\[\\e[0m\\] \\w \\$ '\n"
                  + ("codex\n" if auto_codex and codex else ""))
    return ["/bin/bash", "--rcfile", str(rc), "-i"]


def owned_processes(root_pid):
    """Snapshot descendants of a known terminal shell, including job process groups."""
    parents = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            raw = path.read_text()
            fields = raw[raw.rfind(")") + 2:].split()
            parents[int(path.parent.name)] = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
    found = {root_pid}
    while True:
        children = {pid for pid, parent in parents.items() if parent in found}
        new = found | children
        if new == found:
            return sorted(found - {os.getpid()}, reverse=True)
        found = new


def stop_processes(root_pid):
    if not root_pid or root_pid <= 1:
        return []
    # Capture /proc start times to avoid signalling recycled PIDs later.
    victims = []
    for pid in owned_processes(root_pid):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            identity = stat[stat.rfind(")") + 2:].split()[19]
            victims.append((pid, identity))
            os.kill(pid, signal.SIGHUP)
        except (OSError, IndexError):
            pass
    return victims


def reap_stubborn(victims):
    for pid, identity in victims:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            if stat[stat.rfind(")") + 2:].split()[19] == identity:
                os.kill(pid, signal.SIGKILL)
        except (OSError, IndexError):
            pass
