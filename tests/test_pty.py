"""Real VTE PTY integration without needing a desktop display or a model call."""
import os
from pathlib import Path
import select
import shutil
import tempfile
import time
import unittest

import gi
gi.require_version("Vte", "2.91")
from gi.repository import GLib, Vte

from codex_deck.core import Session, drain_events, prepare_session, reap_stubborn, stop_processes, terminal_environment
from codex_deck.processes import process_cwd, process_running


class PtyShell:
    def __init__(self, directory, codex=None):
        self.directory = Path(directory)
        self.session = Session("pty-test", str(directory))
        argv = prepare_session(directory, self.session, codex, clean_shell=True)
        self.pty = Vte.Pty.new_sync(Vte.PtyFlags.DEFAULT, None)
        self.pid = None
        self.error = None
        self.buffer = b""
        loop = GLib.MainLoop()

        def ready(pty, result, _):
            try:
                _, self.pid = pty.spawn_finish(result)
            except Exception as error:
                self.error = error
            loop.quit()

        timeout = GLib.timeout_add_seconds(5, lambda: loop.quit())
        env = [f"{key}={value}" for key, value in terminal_environment().items()]
        self.pty.spawn_async(str(directory), argv, env, GLib.SpawnFlags.DEFAULT,
                             None, None, -1, None, ready, None)
        loop.run()
        GLib.source_remove(timeout)
        if self.error:
            raise self.error
        if not self.pid:
            raise RuntimeError("VTE PTY startup timed out")
        # Darwin rejects TIOCSWINSZ until the slave PTY has been opened.
        self.pty.set_size(24, 100)
        self.read_until(b"$ ")

    def send(self, command):
        os.write(self.pty.get_fd(), command.encode() + b"\r")

    def read_until(self, marker, timeout=5):
        result, self.buffer = self.buffer, b""
        deadline = time.monotonic() + timeout
        while marker not in result and time.monotonic() < deadline:
            if select.select([self.pty.get_fd()], [], [], max(0, deadline - time.monotonic()))[0]:
                try:
                    result += os.read(self.pty.get_fd(), 65536)
                except OSError:
                    break
        if marker not in result:
            raise AssertionError(f"Missing {marker!r} in terminal output {result!r}")
        end = result.index(marker) + len(marker)
        self.buffer = result[end:]
        return result[:end]

    def close(self):
        if self.pid:
            victims = stop_processes(self.pid)
            reap_stubborn(victims)
            # macOS can keep an exiting shell in tty drain until master close.
            self.pty = None
            deadline = time.monotonic() + 3
            while True:
                try:
                    reaped, _ = os.waitpid(self.pid, os.WNOHANG)
                except ChildProcessError:
                    break
                if reaped:
                    break
                if time.monotonic() > deadline:
                    raise AssertionError(f"Shell {self.pid} survived cleanup: {victims!r}")
                time.sleep(.01)
            self.pid = None


class RealPtyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.shells = []

    def shell(self, name, codex=None):
        path = Path(self.temp.name).resolve() / name
        path.mkdir()
        shell = PtyShell(path, codex)
        self.shells.append(shell)
        return shell

    def tearDown(self):
        for shell in self.shells:
            shell.close()
        self.temp.cleanup()

    def test_independent_real_terminals_and_unicode(self):
        first, second = self.shell("one"), self.shell("two")
        first.send("export DECK_PTY_VALUE=one; printf 'VALUE:%s\\n' \"$DECK_PTY_VALUE\"")
        first.read_until(b"VALUE:one")
        second.send("printf 'VALUE:%s\\n' \"${DECK_PTY_VALUE-unset}\"")
        second.read_until(b"VALUE:unset")
        second.send("printf '中文%s\\n' '正常'")
        second.read_until("中文正常".encode())

    def test_terminals_run_commands_in_their_own_project_directories(self):
        first, second = self.shell("project-one"), self.shell("中文 project two")
        for shell in (first, second):
            shell.send("printf 'HERE:%s\\n' \"$PWD\"")
            shell.read_until(("HERE:" + str(shell.directory)).encode())
            shell.read_until(b"$ ")
        second.send("cd /tmp; printf 'MOVED:%s\\n' \"$PWD\"")
        second.read_until(b"MOVED:/tmp")
        self.assertEqual(Path(process_cwd(second.pid)).resolve(), Path("/tmp").resolve())
        self.assertEqual(Path(process_cwd(first.pid)).resolve(), first.directory)
        first.send("printf 'STILL:%s\\n' \"$PWD\"")
        first.read_until(("STILL:" + str(first.directory)).encode())

    def test_resize_ctrl_c_and_process_cleanup(self):
        shell = self.shell("resize")
        shell.pty.set_size(31, 112)
        shell.send("stty size")
        shell.read_until(b"31 112")
        shell.read_until(b"$ ")
        shell.send("sleep 60")
        shell.read_until(b"sleep 60")
        deadline = time.monotonic() + 2
        while os.tcgetpgrp(shell.pty.get_fd()) == os.getpgid(shell.pid):
            if time.monotonic() >= deadline:
                self.fail("sleep did not take over the foreground process group")
            time.sleep(.01)
        os.write(shell.pty.get_fd(), b"\x03")
        shell.read_until(b"$ ")
        shell.send("printf 'INTERRUPT:%s\\n' OK")
        shell.read_until(b"INTERRUPT:OK")
        shell.read_until(b"$ ")
        shell.send("sleep 60 & printf 'CHILD:%s\\n' $!")
        shell.read_until(b"$ ")
        # Closing the tab must kill background jobs as well as the shell.
        from codex_deck.core import owned_processes
        deadline = time.monotonic() + 2
        descendants = []
        while time.monotonic() < deadline:
            descendants = [pid for pid in owned_processes(shell.pid) if pid != shell.pid]
            if descendants:
                break
            time.sleep(.02)
        self.assertTrue(descendants)
        shell.close()
        for pid in descendants:
            # SIGKILL delivery is asynchronous even after the parent has exited.
            deadline = time.monotonic() + 2
            while process_running(pid):
                if time.monotonic() >= deadline:
                    self.fail("Background process survived terminal close")
                time.sleep(.01)

    @unittest.skipUnless(shutil.which("codex"), "Codex CLI not installed")
    def test_installed_codex_through_session_wrapper(self):
        shell = self.shell("real-codex", shutil.which("codex"))
        shell.send("codex --version; printf 'CLI_EXIT:%s\\n' $?")
        output = shell.read_until(b"CLI_EXIT:0")
        self.assertIn(b"codex-cli", output)
        events = drain_events(shell.directory / "events")
        self.assertEqual([event["type"] for event in events], ["codex-start", "codex-exit"])

    def test_cleanup_reaps_jobs_that_ignore_hangup(self):
        from codex_deck.core import owned_processes
        shell = self.shell("stubborn")
        shell.send("trap '' HUP; sleep 60 & printf 'READY:%s\\n' stubborn")
        shell.read_until(b"READY:stubborn")
        pids = owned_processes(shell.pid)
        self.assertGreater(len(pids), 1)
        victims = stop_processes(shell.pid)
        self.assertTrue(process_running(shell.pid))
        reap_stubborn(victims)
        shell.close()
        deadline = time.monotonic() + 2
        while any(process_running(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(any(process_running(pid) for pid in pids))


if __name__ == "__main__":
    unittest.main()
