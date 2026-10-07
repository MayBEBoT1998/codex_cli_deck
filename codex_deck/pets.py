"""Small vector cats walking around the workspace; independent of GTK."""
from dataclasses import dataclass
import math
import random


PET_SIZE = 36
PALETTES = (
    ("#8dddc9", "#dffaf0", "#347d73"),
    ("#f3ca8b", "#fff1d6", "#987044"),
    ("#c2a9ea", "#f0e6ff", "#786197"),
    ("#a3ceee", "#e5f4ff", "#4b7f9e"),
    ("#efaeba", "#ffe7ed", "#a7687a"),
)


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def route_length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(points, points[1:] + points[:1]))


def sample_route(points, distance):
    total = route_length(points)
    if not points or total == 0:
        return 0, 0, 0
    distance %= total
    for a, b in zip(points, points[1:] + points[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length > 0 and distance <= length:
            ratio = distance / length
            return a[0] + dx * ratio, a[1] + dy * ratio, math.atan2(dy, dx)
        distance -= length
    return points[0][0], points[0][1], 0


def build_routes(width, height, panel, sidebar, sidebar_free_y):
    """Use allocated widget edges, with every pet center inside the window."""
    if width < 2 * PET_SIZE or height < 2 * PET_SIZE:
        return []
    radius = PET_SIZE / 2 + 2
    clamp_x = lambda x: max(radius, min(width - radius, x))
    clamp_y = lambda y: max(radius, min(height - radius, y))
    x, y, w, h = panel
    left, right = clamp_x(x - 13), clamp_x(x + w + 13)
    top, bottom = clamp_y(y - 15), clamp_y(y + h + 15)
    perimeter = [(left, top), (right, top), (right, bottom), (left, bottom)]
    sx, sy, sw, sh = sidebar
    left, right = clamp_x(sx + 30), clamp_x(sx + sw - 30)
    top = clamp_y(max(sy + 24, sidebar_free_y + radius))
    bottom = clamp_y(sy + sh - 24)
    # If the session list fills the sidebar, use the outer frame as the second route.
    if bottom - top < PET_SIZE * 2:
        top = clamp_y(sy + 80)
        left = right = clamp_x(sx + 16)
    sidebar_route = [(left, top), (right, top), (right, bottom), (left, bottom)]
    return [perimeter, sidebar_route]


@dataclass
class Pet:
    route: int
    distance: float
    speed: float
    palette: int
    rest_in: float
    pause: float = 0


class PetScene:
    def __init__(self, count=3):
        self.routes = []
        self.time = 0
        self.random = random.Random(39)
        self.set_count(count)

    def set_count(self, count):
        self.pets = [Pet(index % 2, (0.12, 0.32, 0.54, 0.76, 0.90)[index],
                         23 + index * 3, index, 8 + index * 4)
                     for index in range(max(1, min(5, count)))]
        for pet in self.pets:
            if self.routes:
                pet.distance *= route_length(self.routes[pet.route % len(self.routes)])

    def set_routes(self, routes):
        if routes == self.routes:
            return
        for pet in self.pets:
            previous = route_length(self.routes[pet.route % len(self.routes)]) if self.routes else 1
            progress = pet.distance / previous if previous else 0
            length = route_length(routes[pet.route % len(routes)]) if routes else 1
            pet.distance = progress * length
        self.routes = routes

    def advance(self, dt):
        dt = max(0, min(.1, dt))  # No jumping after a hidden/minimized window resumes.
        self.time += dt
        for pet in self.pets:
            if pet.pause > 0:
                pet.pause = max(0, pet.pause - dt)
            else:
                pet.distance += pet.speed * dt
                pet.rest_in -= dt
                if pet.rest_in <= 0:
                    pet.pause = self.random.uniform(1.2, 2.8)
                    pet.rest_in = self.random.uniform(10, 20)

    def poses(self):
        if not self.routes:
            return []
        return [(pet, *sample_route(self.routes[pet.route % len(self.routes)], pet.distance))
                for pet in self.pets]


def draw_cat(cr, x, y, angle=0, phase=0, palette=0, resting=False, night=True):
    """Original cel-shaded cat, with walking paws, wagging tail and blinking eyes."""
    coat, cream, accent = PALETTES[palette % len(PALETTES)]
    outline = "#253944"
    cr.save()
    cr.translate(x, y)
    cr.rotate(angle)
    cr.set_line_join(1)
    cr.set_line_cap(1)
    cr.set_line_width(1.3)

    def fill(color, stroke=True):
        cr.set_source_rgb(*rgb(color))
        cr.fill_preserve()
        if stroke:
            cr.set_source_rgb(*rgb(outline))
            cr.stroke()
        else:
            cr.new_path()

    def oval(cx, cy, rx, ry, color, stroke=True):
        cr.save()
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        cr.arc(0, 0, 1, 0, math.tau)
        cr.restore()
        fill(color, stroke)

    wave = math.sin(phase * 4)
    stride = 0 if resting else math.sin(phase * 9) * 2
    # Tail and paws move independently, so climbing is visible even at small sizes.
    cr.move_to(-7, 10)
    cr.curve_to(-18, 12, -17, 1 + wave * 2, -14, 1 + wave * 2)
    cr.set_source_rgb(*rgb(outline)); cr.set_line_width(5); cr.stroke_preserve()
    cr.set_source_rgb(*rgb(coat)); cr.set_line_width(3); cr.stroke()
    cr.set_line_width(1.3)
    oval(0, 9, 9, 7, coat)
    oval(1, 11, 5, 4, cream, False)
    oval(-5, 15 + stride * .6, 4, 2.6, cream)
    oval(7, 15 - stride * .6, 4, 2.6, cream)
    oval(-10, 5 - stride, 2.7, 3.3, coat)
    oval(10, 5 + stride, 2.7, 3.3, coat)
    # Ears are part of the silhouette, not an avatar pasted onto a moving rectangle.
    cr.move_to(-12, -6); cr.line_to(-12, -18); cr.line_to(-3, -12); cr.close_path(); fill(coat)
    cr.move_to(4, -12); cr.line_to(13, -18); cr.line_to(13, -5); cr.close_path(); fill(coat)
    cr.move_to(-10, -9); cr.line_to(-10, -15); cr.line_to(-5, -11); cr.close_path(); fill("#eaa6b3", False)
    cr.move_to(6, -11); cr.line_to(11, -15); cr.line_to(11, -9); cr.close_path(); fill("#eaa6b3", False)
    oval(0, -3, 14, 11.5, coat)
    oval(0, 2, 10, 6, cream, False)
    blink = (phase + palette * .7) % 5.3 > 5.05
    if blink:
        cr.set_source_rgb(*rgb(outline)); cr.set_line_width(1.6)
        for ex in (-5, 6):
            cr.move_to(ex - 2, -3); cr.line_to(ex + 2, -3)
        cr.stroke()
    else:
        for ex in (-5, 6):
            oval(ex, -3, 2.1, 3.1, outline, False)
            oval(ex + .5, -4, .7, 1, "#ffffff", False)
    oval(-10, 1, 2.2, 1.1, "#e799ac", False)
    oval(10, 1, 2.2, 1.1, "#e799ac", False)
    cr.move_to(-1, 1); cr.line_to(2, 1); cr.line_to(.5, 3); cr.close_path(); fill(accent, False)
    cr.set_source_rgb(*rgb(outline)); cr.set_line_width(1)
    cr.move_to(.5, 3); cr.curve_to(0, 6, -3, 5, -3, 4)
    cr.move_to(.5, 3); cr.curve_to(1, 6, 4, 5, 4, 4); cr.stroke()
    oval(1, 9, 1.7, 1.7, "#f8d577")
    if resting:
        cr.set_source_rgba(*rgb("#d4e8f5" if night else "#52697d"), .8)
        cr.set_font_size(7); cr.move_to(11, -16); cr.show_text("z")
    cr.restore()
