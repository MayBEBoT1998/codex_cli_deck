"""Save the actual GTK widget tree without reading the display's backing surface."""
from pathlib import Path

import cairo


def save_widget_png(widget, destination):
    # Gdk.pixbuf_get_from_window can abort in cairo_surface_mark_dirty on
    # surfaces carrying MIME data (seen on Ubuntu's GTK 3 / Cairo stack).
    # Draw the live, allocated widgets into a fresh image surface instead.
    if not widget.get_realized() or not widget.get_mapped():
        raise RuntimeError("截图窗口尚未显示")
    width, height = widget.get_allocated_width(), widget.get_allocated_height()
    if width <= 1 or height <= 1:
        raise RuntimeError("截图窗口尚未完成布局")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    widget.draw(cairo.Context(surface))
    surface.flush()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(destination))
    return width, height
