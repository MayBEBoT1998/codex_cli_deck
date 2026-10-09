"""Theme colors and stack geometry shared by the live GTK workspace."""
import math


THEMES = {
    "night": {
        "foreground": "#d1dce5", "background": "#11151a",
        "cursor": "#a5dcc6", "selection": "#39544e",
        "palette": ["#18202a", "#df898d", "#9dceab", "#e6c38c", "#91b5e1", "#b7a3df", "#8ecfc5", "#cbd4dd",
                    "#647382", "#ed9ea1", "#b9e7b9", "#f4d4a3", "#b6d1f2", "#d6c4ef", "#b2e7dc", "#f2f6fa"],
        "layer_back": (0.12, 0.15, 0.19), "layer_front": (0.17, 0.21, 0.26),
        "layer_border": (0.32, 0.39, 0.46),
    },
    "day": {
        # Codex paints parts of its TUI with fixed RGB colors and may cache its
        # background detection. Keep VTE dark in light chrome so switching a
        # running session cannot turn its composer into dark-on-dark text.
        "foreground": "#e1eaf3", "background": "#141b24",
        "cursor": "#b5ead5", "selection": "#365b52",
        "palette": ["#273442", "#efa3a8", "#a8deb6", "#eed3a1", "#a5c8f5", "#d3baf0", "#a0ddd1", "#dbe4ed",
                    "#9cabb9", "#ffb6bc", "#c2eac3", "#ffe1ad", "#bfdaff", "#e3cdf9", "#c0f0e5", "#f5f8fc"],
        "layer_back": (0.78, 0.83, 0.88), "layer_front": (0.89, 0.93, 0.96),
        "layer_border": (0.51, 0.60, 0.68),
    },
}


def layer_geometry(session_count, width):
    """One exposed edge per background terminal; the live panel is the last layer."""
    count = max(0, session_count - 1)
    if not count:
        return 0, []
    step = min(10, 96 / count)
    height = math.ceil(count * step + 2)
    inset_step = min(10, min(64, max(0, width / 4)) / count)
    return height, [(inset_step * (count - index), 1 + index * step) for index in range(count)]


def paint_layers(cr, session_count, width, theme):
    height, edges = layer_geometry(session_count, width)
    colors = THEMES[theme]
    for index, (inset, y) in enumerate(edges):
        blend = (index + 1) / len(edges)
        fill = [a + (b - a) * blend for a, b in zip(colors["layer_back"], colors["layer_front"])]
        cr.set_source_rgb(*fill)
        cr.rectangle(inset, y, max(0, width - inset * 2), height - y)
        cr.fill_preserve()
        cr.set_source_rgb(*colors["layer_border"])
        cr.set_line_width(1)
        cr.stroke()


def scroll_target(top, bottom, value, page_size, upper):
    if top < value:
        value = top - 8
    elif bottom > value + page_size:
        value = bottom - page_size + 8
    return max(0, min(value, max(0, upper - page_size)))


def ease_out(progress):
    return 1 - (1 - max(0, min(1, progress))) ** 3
