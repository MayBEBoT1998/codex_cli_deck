"""Transparent, click-through GTK overlay for the window pets."""
import math

import cairo
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .pets import PET_SIZE, PetScene, draw_cat
from .pet_sprites import SpriteBank


class PetLayer(Gtk.DrawingArea):
    def __init__(self, layout, theme, count=3, style="anime", character="all"):
        super().__init__()
        self.scene = PetScene(count)
        self.layout = layout
        self.theme = theme
        self.blocked = []
        self.sprites = SpriteBank()
        self.style = style
        self.character = character
        self.route_heights = []
        self.tick_id = None
        self.last_frame = None
        self.enabled = False
        self.set_has_window(False)
        self.set_can_focus(False)
        self.set_no_show_all(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.connect("draw", self.draw)
        self.connect("map", lambda *_: self.start())
        self.connect("unmap", lambda *_: self.stop())
        self.connect("destroy", lambda *_: self.stop())

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.set_visible(self.enabled)
        if self.enabled:
            self.start()
        else:
            self.stop()
        self.queue_draw()

    def set_count(self, count):
        self.scene.set_count(count)
        self.queue_draw()

    @property
    def uses_sprites(self):
        return self.style == "anime" and bool(self.sprites.characters)

    def set_style(self, style):
        self.style = style
        self.scene.set_routes([])
        self.queue_draw()

    def start(self):
        if self.enabled and self.get_mapped() and self.tick_id is None:
            self.tick_id = self.add_tick_callback(self.tick, None)

    def stop(self):
        if self.tick_id is not None:
            self.remove_tick_callback(self.tick_id)
            self.tick_id = None
        self.last_frame = None

    def update_layout(self):
        routes, self.blocked, self.route_heights = self.layout()
        self.scene.set_routes(routes)

    def damage(self, poses):
        radius = math.ceil(max(self.route_heights, default=PET_SIZE) * 1.2) if self.uses_sprites else math.ceil(PET_SIZE * .8)
        for _, x, y, _ in poses:
            self.queue_draw_area(int(x) - radius, int(y) - radius, radius * 2, radius * 2)

    def tick(self, _widget, frame_clock, _data):
        now = frame_clock.get_frame_time()
        if self.last_frame is None:
            self.last_frame = now
        elapsed = (now - self.last_frame) / 1_000_000
        if elapsed < 1 / 30:
            return True
        previous = self.scene.poses()
        self.last_frame = now
        self.update_layout()
        if Gtk.Settings.get_default().get_property("gtk-enable-animations"):
            self.scene.advance(elapsed)
        self.damage(previous)
        self.damage(self.scene.poses())
        return True

    def draw(self, widget, cr):
        if not self.enabled:
            return False
        self.update_layout()
        cr.save()
        # Subtract each protected region separately: overlapping exclusions must
        # remain excluded, rather than toggling an even-odd intersection back on.
        for x, y, width, height in self.blocked:
            cr.new_path()
            cr.rectangle(0, 0, widget.get_allocated_width(), widget.get_allocated_height())
            cr.rectangle(x, y, width, height)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.clip()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        for pet, x, y, angle in self.scene.poses():
            if self.uses_sprites:
                index = pet.route % len(self.scene.routes)
                self.sprites.draw(cr, pet, x, y, angle, self.scene.time,
                                  self.route_heights[index], self.scene.routes[index], self.character)
            else:
                draw_cat(cr, x, y, angle, self.scene.time, pet.palette,
                         resting=pet.pause > 0, night=self.theme() == "night")
        cr.restore()
        return False
