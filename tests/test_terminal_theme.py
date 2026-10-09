"""Readability when a live TUI retains its own dark RGB composer background."""
import unittest

from codex_deck.appearance import THEMES


def contrast(first, second):
    def luminance(color):
        rgb = [int(color[n:n+2], 16) / 255 for n in (1, 3, 5)]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))
    a, b = sorted((luminance(first), luminance(second)))
    return (b + .05) / (a + .05)


class TerminalThemeTests(unittest.TestCase):
    def test_day_text_stays_readable_on_codex_rgb_input_background(self):
        day = THEMES["day"]
        for background in (day["background"], "#373b40", "#303438"):
            self.assertGreater(contrast(day["foreground"], background), 7)
        for color in day["palette"][1:]:
            self.assertGreater(contrast(color, day["background"]), 4.5)

    def test_switching_theme_does_not_invert_cached_terminal_colors(self):
        for foreground in (THEMES["night"]["foreground"], THEMES["day"]["foreground"]):
            for background in (THEMES["night"]["background"], THEMES["day"]["background"]):
                self.assertGreater(contrast(foreground, background), 7)


if __name__ == "__main__":
    unittest.main()
