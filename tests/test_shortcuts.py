"""macOS Command shortcuts must not consume terminal Ctrl+C or Linux Meta keys."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codex_deck.app import Deck, Gdk, Vte


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.terminal = Mock()
        self.deck = SimpleNamespace(
            views={"one": self.terminal}, active_id="one", font_size=12,
            sessions=[SimpleNamespace(id="one"), SimpleNamespace(id="two")],
            choose_directory=Mock(), close_session=Mock(), activate=Mock(), set_font=Mock(),
        )

    def press(self, key, modifiers, platform):
        event = SimpleNamespace(keyval=Gdk.keyval_from_name(key), state=modifiers)
        with patch("codex_deck.app.sys.platform", platform):
            return Deck.on_key(self.deck, None, event)

    def test_mac_command_shortcuts(self):
        command = Gdk.ModifierType.MOD2_MASK
        for key in ("t", "c", "v", "2", "bracketleft", "plus", "w"):
            self.assertTrue(self.press(key, command, "darwin"))
        self.deck.choose_directory.assert_called_once()
        self.terminal.copy_clipboard_format.assert_called_once_with(Vte.Format.TEXT)
        self.terminal.paste_clipboard.assert_called_once()
        self.deck.activate.assert_any_call("two")
        self.deck.activate.assert_any_call("two", -1)
        self.deck.set_font.assert_called_once_with(13)
        self.deck.close_session.assert_called_once_with("one")

    def test_ctrl_c_remains_a_terminal_interrupt_on_both_platforms(self):
        for platform in ("linux", "darwin"):
            self.assertFalse(self.press("c", Gdk.ModifierType.CONTROL_MASK, platform))
        self.terminal.copy_clipboard_format.assert_not_called()

    def test_linux_shortcuts_stay_unchanged(self):
        self.assertFalse(self.press("t", Gdk.ModifierType.MOD2_MASK, "linux"))
        self.assertFalse(self.press("t", Gdk.ModifierType.META_MASK, "linux"))
        self.assertTrue(self.press("t", Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, "linux"))
        self.deck.choose_directory.assert_called_once()
