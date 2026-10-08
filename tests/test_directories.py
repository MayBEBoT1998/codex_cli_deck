"""Directory choices must gate process creation, including startup and cancellation."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from codex_deck.app import Deck, Gtk


class DirectoryFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name).resolve() / "中文 project 'two'"
        self.project.mkdir()
        self.deck = SimpleNamespace(
            closing=False, directory_dialog=None, sessions=[],
            args=SimpleNamespace(restore=False, cwd=None, smoke_test=None, demo=False),
            settings=SimpleNamespace(values={"sessions": [], "default_cwd": self.temp.name}),
            default_cwd=Mock(return_value=self.temp.name), new_session=Mock(),
            message=Mock(), choose_directory=Mock(), activate=Mock(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def choose(self, response, path):
        dialog = Mock()
        dialog.run.return_value = response
        dialog.get_filename.return_value = path
        with patch("codex_deck.app.Gtk.FileChooserDialog", return_value=dialog):
            Deck.choose_directory(self.deck)
        dialog.destroy.assert_called_once()
        self.assertIsNone(self.deck.directory_dialog)

    def test_startup_requires_choice_even_with_saved_sessions(self):
        self.deck.settings.values["sessions"] = [{"name": "old", "cwd": self.temp.name}]
        Deck.open_initial_sessions(self.deck)
        self.deck.choose_directory.assert_called_once()
        self.deck.new_session.assert_not_called()

    def test_explicit_cli_directory_is_used_without_another_prompt(self):
        self.deck.args.cwd = str(self.project)
        self.deck.default_cwd.return_value = str(self.project)
        Deck.open_initial_sessions(self.deck)
        self.deck.choose_directory.assert_not_called()
        self.deck.new_session.assert_called_once_with(cwd=str(self.project), persist=False)

    def test_cancel_does_not_spawn_or_change_directory(self):
        self.choose(Gtk.ResponseType.CANCEL, str(self.project))
        self.deck.new_session.assert_not_called()
        self.assertEqual(self.deck.settings.values["default_cwd"], self.temp.name)

    def test_selected_project_is_forwarded_without_inheriting_existing_cwd(self):
        self.deck.sessions = [SimpleNamespace(name="existing", cwd=self.temp.name)]
        self.choose(Gtk.ResponseType.OK, str(self.project))
        self.deck.new_session.assert_called_once_with(cwd=str(self.project), name=self.project.name)
        self.assertEqual(self.deck.sessions[0].cwd, self.temp.name)
        self.assertEqual(self.deck.settings.values["default_cwd"], str(self.project))

    def test_deleted_selected_directory_never_falls_back(self):
        self.project.rmdir()
        self.choose(Gtk.ResponseType.OK, str(self.project))
        self.deck.new_session.assert_not_called()
        self.deck.message.assert_called_once()

    def test_restore_of_missing_project_requires_a_new_choice(self):
        self.deck.args.restore = True
        self.deck.settings.values["sessions"] = [{"name": "deleted", "cwd": str(self.project / "missing")}]
        Deck.open_initial_sessions(self.deck)
        self.deck.new_session.assert_not_called()
        self.deck.choose_directory.assert_called_once()


if __name__ == "__main__":
    unittest.main()
