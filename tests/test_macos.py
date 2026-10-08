"""Platform isolation and frozen helper command regressions."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from codex_deck import core, macos


class MacStartupTests(unittest.TestCase):
    def test_linux_keeps_its_input_method_environment(self):
        with patch.object(sys, "platform", "linux"), \
                patch.dict(os.environ, {"GTK_IM_MODULE": "ibus", "GTK_IM_MODULE_FILE": "/linux/cache"}):
            macos.configure_input_method()
            self.assertEqual(os.environ["GTK_IM_MODULE"], "ibus")
            self.assertEqual(os.environ["GTK_IM_MODULE_FILE"], "/linux/cache")
            self.assertEqual(core.terminal_shell(), "/bin/bash")

    def test_homebrew_cache_is_explicit_on_intel_and_apple_silicon(self):
        for prefix in ("/opt/homebrew", "/usr/local"):
            with patch.object(sys, "platform", "darwin"), \
                    patch.object(macos.shutil, "which", return_value=prefix + "/bin/brew"), \
                    patch.object(Path, "is_file", return_value=True), patch.dict(os.environ):
                macos.configure_input_method()
                self.assertEqual(os.environ["GTK_IM_MODULE"], "quartz")
                self.assertEqual(os.environ["GTK_IM_MODULE_FILE"], prefix + "/lib/gtk-3.0/3.0.0/immodules.cache")

    def test_frozen_callbacks_reenter_headless_helper(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable", "/Applications/Codex Deck.app/Contents/MacOS/Codex Deck"):
            self.assertEqual(core.helper_command("run_codex"), [sys.executable, "--internal-run-codex"])
            self.assertEqual(core.helper_command("notify"), [sys.executable, "--internal-notify"])

    def test_source_callbacks_use_the_active_python(self):
        self.assertEqual(core.helper_command("notify"), [sys.executable, str(core.ROOT / "scripts/notify.py")])
