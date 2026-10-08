"""Terminal process inspection; keep Linux /proc and isolate macOS dependencies."""
import os
from pathlib import Path
import signal
import sys

if sys.platform == "darwin":
    import psutil


def process_cwd(pid):
    """Return the shell's current directory, or None when it is unavailable."""
    if sys.platform == "darwin":
        try:
            return psutil.Process(pid).cwd()
        except psutil.Error:
            return None
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def process_running(pid):
    """Exclude zombies when checking that a closed terminal was cleaned up."""
    if sys.platform == "darwin":
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def owned_processes(root_pid):
    """Snapshot descendants, including jobs in separate process groups."""
    if not root_pid or root_pid <= 1 or root_pid == os.getpid():
        return []
    if sys.platform == "darwin":
        try:
            children = psutil.Process(root_pid).children(recursive=True)
            return sorted({root_pid, *(p.pid for p in children)} - {os.getpid()}, reverse=True)
        except psutil.Error:
            return [root_pid]
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
    if not root_pid or root_pid <= 1 or root_pid == os.getpid():
        return []
    victims = []
    for pid in owned_processes(root_pid):
        if sys.platform == "darwin":
            try:
                process = psutil.Process(pid)
                victims.append((pid, process.create_time()))
                process.send_signal(signal.SIGHUP)
            except psutil.Error:
                pass
        else:
            # Preserve Linux start-time checks against PID reuse.
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
        if sys.platform == "darwin":
            try:
                process = psutil.Process(pid)
                if process.create_time() == identity:
                    process.kill()
            except psutil.Error:
                pass
        else:
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
                if stat[stat.rfind(")") + 2:].split()[19] == identity:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, IndexError):
                pass
