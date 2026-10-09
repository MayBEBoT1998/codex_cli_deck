"""Exercise the installed command and a full install/update/uninstall lifecycle."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.install import ROOT, desktop_entry, destinations, install, payload_files, uninstall


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prefix = self.root / "应用 install 'quoted' $cash"
        self.state = self.root / "preferences"

    def tearDown(self):
        self.temp.cleanup()

    def test_install_runs_outside_source_updates_and_uninstalls_without_losing_state(self):
        result = install(ROOT, self.prefix, self.state)
        app_dir, command, desktop = destinations(self.prefix)
        self.assertEqual(result["app_dir"], app_dir)
        self.assertTrue(command.is_file())
        self.assertTrue(os.access(command, os.X_OK))
        output = subprocess.run([str(command), "--help"], cwd=self.root,
                                capture_output=True, text=True, timeout=15, check=True)
        self.assertIn("--cwd", output.stdout)
        # The launcher must keep the invocation directory. This child directory
        # does not exist in the source or installed application directories.
        (self.root / "my project").mkdir()
        env = dict(os.environ, DISPLAY="", WAYLAND_DISPLAY="", GDK_BACKEND="x11")
        output = subprocess.run([str(command), "--cwd", "my project", "--shell"], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(output.returncode, 1, output.stderr)
        self.assertIn("无法连接桌面", output.stderr)
        for relative in payload_files(ROOT):
            self.assertTrue((app_dir / relative).is_file(), relative)
        self.assertFalse((app_dir / ".state").exists())
        self.assertFalse((app_dir / ".git").exists())
        self.assertFalse(list(app_dir.rglob("source-sheet.png")))

        settings = self.state / "settings.json"
        settings.write_text('{"theme":"day"}')
        user_avatar = self.state / "avatars/custom/avatar.png"
        user_avatar.parent.mkdir(parents=True, exist_ok=True)
        user_avatar.write_bytes(b"user artwork")
        stale_module = app_dir / "codex_deck/removed.py"
        stale_module.write_text("# old version")
        install(ROOT, self.prefix)
        self.assertFalse(stale_module.exists())
        self.assertEqual(settings.read_text(), '{"theme":"day"}')
        self.assertEqual(user_avatar.read_bytes(), b"user artwork")
        self.assertEqual(json.loads((app_dir / ".installed.json").read_text())["state_dir"], str(self.state))
        # The installed CLI can uninstall even when the original checkout is gone.
        subprocess.run([str(command), "--uninstall"], cwd=self.root, capture_output=True, check=True, timeout=15)
        self.assertFalse(command.is_symlink())
        self.assertFalse(desktop.exists())
        self.assertFalse(app_dir.exists())
        self.assertTrue(user_avatar.exists())
        self.assertTrue(settings.exists())

    def test_first_install_migrates_only_known_state_and_does_not_overwrite(self):
        from scripts.install import migrate_state
        source = self.root / "source"
        legacy = source / ".state"
        (legacy / "avatars/custom").mkdir(parents=True)
        (legacy / "settings.json").write_text('{"font_size":18}')
        (legacy / "coordination.json").write_text('{"tasks":[]}')
        (legacy / "avatars/catalog.json").write_text('[]')
        (legacy / "avatars/custom/avatar.png").write_bytes(b"avatar")
        (legacy / "unrelated.secret").write_text("do not package")
        self.assertTrue(migrate_state(source, self.state))
        self.assertEqual((self.state / "settings.json").read_text(), '{"font_size":18}')
        self.assertTrue((self.state / "avatars/custom/avatar.png").exists())
        self.assertFalse((self.state / "unrelated.secret").exists())
        (legacy / "settings.json").write_text('{}')
        self.assertFalse(migrate_state(source, self.state))
        self.assertEqual((self.state / "settings.json").read_text(), '{"font_size":18}')

    def test_existing_unrelated_command_is_never_overwritten_or_removed(self):
        _, command, _ = destinations(self.prefix)
        command.parent.mkdir(parents=True)
        command.write_text("existing user command")
        with self.assertRaises(ValueError):
            install(ROOT, self.prefix, self.state)
        with self.assertRaises(ValueError):
            uninstall(self.prefix)
        self.assertEqual(command.read_text(), "existing user command")

    def test_desktop_entry_handles_spaces_quotes_and_shell_characters(self):
        from gi.repository import GLib
        command = self.prefix / 'bin/quoted"back\\tick`app'
        path = self.root / "codex-deck.desktop"
        path.write_text(desktop_entry(command, self.prefix / "share/codex-deck"))
        validator = shutil.which("desktop-file-validate")
        if validator:
            subprocess.run([validator, str(path)], capture_output=True, check=True)
        entry = GLib.KeyFile()
        entry.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        _, argv = GLib.shell_parse_argv(entry.get_string("Desktop Entry", "Exec"))
        self.assertEqual(argv, ["/usr/bin/python3", str(command)])
        self.assertFalse(entry.get_boolean("Desktop Entry", "Terminal"))


if __name__ == "__main__":
    unittest.main()
