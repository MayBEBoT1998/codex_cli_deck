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
        "foreground": "#243345", "background": "#f8fafc",
        "cursor": "#186653", "selection": "#c9e5de",
        "palette": ["#243345", "#a62a37", "#246437", "#775006", "#245aa6", "#794499", "#116675", "#596777",
                    "#677487", "#b52f3d", "#2e7040", "#865b08", "#2865b5", "#8b449c", "#0a6c7f", "#364658"],
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
