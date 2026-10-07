"""Prepared anime sprites and full-body-safe play areas for the workspace."""
from dataclasses import dataclass
import json
import math
from pathlib import Path

import cairo

from .core import ROOT


@dataclass
class SpriteCharacter:
    id: str
    name: str
    frames: dict
    fps: dict
    anchor: tuple


class SpriteBank:
    def __init__(self, directory=None):
        directory = Path(directory or ROOT / "assets" / "pets" / "anime")
        self.characters = []
        self.errors = []
        for path in sorted(directory.glob("oneesan-*/sprite.json")):
            try:
                data = json.loads(path.read_text())
                frames = {}
                for action in ("idle", "walk", "climb"):
                    frames[action] = []
                    for name in data[action]:
                        source = (path.parent / name).resolve()
                        if not source.is_relative_to(path.parent.resolve()):
                            raise ValueError("Sprite path outside character directory")
                        frames[action].append(cairo.ImageSurface.create_from_png(str(source)))
                    if not frames[action]:
                        raise ValueError("Empty animation")
                self.characters.append(SpriteCharacter(data["id"], data.get("name", data["id"]),
                                                       frames, data["fps"], tuple(data.get("anchor", (.5, .98)))))
            except (OSError, ValueError, KeyError, TypeError, cairo.Error) as error:
                self.errors.append(f"{path.parent.name}: {error}")

    @property
    def aspect_ratio(self):
        return max((frame.get_width() / frame.get_height() for character in self.characters
                    for frames in character.frames.values() for frame in frames), default=.65)

    def character(self, index, selected="all"):
        return next((c for c in self.characters if c.id == selected),
                    self.characters[index % len(self.characters)])

    def frame(self, pet, angle, time, selected="all"):
        character = self.character(pet.palette, selected)
        climbing = abs(math.sin(angle)) > .5
        action = "climb" if climbing else "idle" if pet.pause > 0 else "walk"
        frames = character.frames[action]
        if action == "idle":
            index = 1 if (time + pet.palette * .8) % 4.6 > 4.43 else 0
        else:
            # Distance stops when the pet rests, freezing its climbing pose in place.
            index = int(pet.distance / max(pet.speed, 1) * character.fps[action])
        return character, action, frames[index % len(frames)]

    def draw(self, cr, pet, x, feet_y, angle, time, height, route, selected="all"):
        character, action, frame = self.frame(pet, angle, time, selected)
        if action == "climb":
            left, right = min(p[0] for p in route), max(p[0] for p in route)
            mirror = x < (left + right) / 2
        else:
            mirror = math.cos(angle) < 0
        scale = height / frame.get_height()
        cr.save()
        cr.translate(x, feet_y)
        cr.scale(-scale if mirror else scale, scale)
        cr.translate(-frame.get_width() * character.anchor[0], -frame.get_height() * character.anchor[1])
        cr.set_source_surface(frame, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BEST)
        cr.paint()
        cr.restore()


def subtract_intervals(intervals, left, right):
    remaining = []
    for a, b in intervals:
        if right <= a or left >= b:
            remaining.append((a, b))
        else:
            if a < left:
                remaining.append((a, left))
            if right < b:
                remaining.append((right, b))
    return remaining


def build_sprite_routes(width, height, panel, sidebar, free_y, blocked, desired_height=144, aspect=.65):
    """Route coordinates anchor the feet. Find lanes where the entire sprite fits."""
    routes, heights = [], []
    px, py, pw, ph = panel
    sx, sy, sw, sh = sidebar
    sizes = sorted({desired_height, min(desired_height, 128), min(desired_height, 112),
                    min(desired_height, 96), min(desired_height, 80)}, reverse=True)
    # The topmost usable shelf can move above a completion banner or a long path.
    for size in sizes:
        half = size * aspect / 2 + 6
        found = None
        for feet_y in range(int(min(py - 3, height - 8)), int(size + 5), -8):
            intervals = [(max(half + 6, px + half + 10), min(width - half - 6, px + pw - half - 10))]
            for bx, by, bw, bh in blocked:
                if by < feet_y + 4 and by + bh > feet_y - size:
                    intervals = subtract_intervals(intervals, bx - half - 5, bx + bw + half + 5)
            good = [(a, b) for a, b in intervals if b - a >= 45]
            if good:
                found = (*max(good, key=lambda pair: pair[1] - pair[0]), feet_y)
                break
        if found:
            a, b, y = found
            routes.append([(a, y), (b, y)])
            heights.append(size)
            break
    # A rectangle in the unused sidebar gives real vertical climbing segments.
    for size in sizes:
        half = size * aspect / 2 + 6
        left, right = sx + half + 8, sx + sw - half - 8
        top, bottom = max(sy, free_y) + size + 6, min(height - 8, sy + sh - 8)
        if right - left >= 30 and bottom >= top:
            route = [(left, top), (right, top), (right, bottom), (left, bottom)]
            routes.append(route)
            heights.append(size)
            break
    return routes, heights
