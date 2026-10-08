"""Regressions for Linux /proc parsing and PID reuse, runnable on either OS."""
import os
from pathlib import Path
import signal
import unittest
from unittest.mock import patch

from codex_deck import processes


def stat(pid, parent, started="12345", state="S"):
    fields = [state, str(parent)] + ["0"] * 17 + [started]
    return f"{pid} (command with ) spaces) " + " ".join(fields)


class LinuxProcessTests(unittest.TestCase):
    def setUp(self):
        platform = patch.object(processes.sys, "platform", "linux")
        platform.start()
        self.addCleanup(platform.stop)

    def test_descendants_include_background_jobs_but_not_other_sessions(self):
        entries = {
            Path("/proc/42000/stat"): stat(42000, 1),
            Path("/proc/42001/stat"): stat(42001, 42000),
            Path("/proc/42002/stat"): stat(42002, 42001),
            Path("/proc/43000/stat"): stat(43000, 1),
        }
        with patch.object(Path, "glob", return_value=list(entries)), \
                patch.object(Path, "read_text", autospec=True, side_effect=entries.__getitem__):
            self.assertEqual(processes.owned_processes(42000), [42002, 42001, 42000])

    def test_cleanup_checks_start_time_before_killing_a_pid(self):
        with patch.object(processes, "owned_processes", return_value=[42000]), \
                patch.object(Path, "read_text", return_value=stat(42000, 1)), \
                patch.object(os, "kill") as kill:
            victims = processes.stop_processes(42000)
            self.assertEqual(victims, [(42000, "12345")])
            kill.assert_called_once_with(42000, signal.SIGHUP)
        with patch.object(Path, "read_text", return_value=stat(42000, 1, "67890")), \
                patch.object(os, "kill") as kill:
            processes.reap_stubborn(victims)
            kill.assert_not_called()
        with patch.object(Path, "read_text", return_value=stat(42000, 1)), \
                patch.object(os, "kill") as kill:
            processes.reap_stubborn(victims)
            kill.assert_called_once_with(42000, signal.SIGKILL)

    def test_exit_during_cleanup_is_harmless(self):
        with patch.object(Path, "read_text", side_effect=FileNotFoundError), \
                patch.object(os, "kill") as kill:
            processes.reap_stubborn([(42000, "12345")])
            self.assertFalse(processes.process_running(42000))
            kill.assert_not_called()
        with patch.object(os, "readlink", side_effect=FileNotFoundError):
            self.assertIsNone(processes.process_cwd(42000))

    def test_zombie_does_not_count_as_a_surviving_job(self):
        with patch.object(Path, "read_text", return_value=stat(42000, 1, state="Z")):
            self.assertFalse(processes.process_running(42000))

    def test_invalid_roots_never_receive_a_signal(self):
        with patch.object(os, "kill") as kill:
            for pid in (None, -1, 0, 1, os.getpid()):
                self.assertEqual(processes.stop_processes(pid), [])
                self.assertEqual(processes.owned_processes(pid), [])
            kill.assert_not_called()
