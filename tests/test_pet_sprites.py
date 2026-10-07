import hashlib
import json
import math
from pathlib import Path
import unittest

import cairo
from PIL import Image

from codex_deck.core import ROOT
from codex_deck.pet_sprites import SpriteBank, build_sprite_routes
from codex_deck.pets import Pet, sample_route, route_length


class AnimeSpriteTests(unittest.TestCase):
    def setUp(self):
        self.bank = SpriteBank()

    def test_three_complete_transparent_character_packs(self):
        self.assertEqual(self.bank.errors, [])
        self.assertEqual(len(self.bank.characters), 3)
        directory = ROOT / "assets/pets/anime"
        for character in self.bank.characters:
            self.assertEqual([len(character.frames[a]) for a in ("idle", "walk", "climb")], [2, 4, 2])
            sizes = set()
            bottoms = []
            for file in sorted((directory / character.id).glob("frame-*.png")):
                with Image.open(file) as frame:
                    self.assertEqual(frame.mode, "RGBA")
                    alpha = frame.getchannel("A")
                    self.assertEqual(alpha.getpixel((0, 0)), 0)
                    bounds = alpha.getbbox()
                    self.assertIsNotNone(bounds)
                    self.assertGreater(bounds[0], 0)
                    self.assertGreater(bounds[1], 0)
                    self.assertLess(bounds[2], frame.width)
                    self.assertLess(bounds[3], frame.height)
                    bottoms.append(bounds[3])
                    sizes.add(frame.size)
            self.assertEqual(len(sizes), 1)
            self.assertLessEqual(max(bottoms) - min(bottoms), 2)

    def test_cross_grid_fingers_retained(self):
        root = ROOT / "assets/pets/anime"
        for key in ("oneesan-black", "oneesan-silver", "oneesan-wine"):
            data = json.loads((root / key / "sprite.json").read_text())
            self.assertLess(data["source_bounds"][6][1], 512, "Climbing fingers above the row boundary were lost")
            self.assertFalse(data["rotate_whole_character"])

    def test_originals_unchanged_when_local_sources_are_available(self):
        root = ROOT / "assets/pets/anime"
        sources = [("oneesan-black", "黑西装御姐八格动作表-1.png"),
                   ("oneesan-silver", "银发女伴八姿精灵图-2.png"),
                   ("oneesan-wine", "酒红发成年女伴八姿态精灵图-3.png")]
        if not all((root / filename).is_file() for _, filename in sources):
            self.skipTest("Original artwork is local-only; distributed runtime frames are checked separately")
        for key, filename in sources:
            data = json.loads((root / key / "sprite.json").read_text())
            self.assertEqual(data["source_sha256"], hashlib.sha256((root / filename).read_bytes()).hexdigest())

    def test_idle_walk_climb_and_freezing_on_wall(self):
        pet = Pet(0, 0, 25, 0, 10)
        _, action, first = self.bank.frame(pet, 0, 0)
        self.assertEqual(action, "walk")
        pet.distance = 10
        self.assertIsNot(self.bank.frame(pet, 0, .4)[2], first)
        pet.pause = 2
        self.assertEqual(self.bank.frame(pet, 0, 1)[1], "idle")
        self.assertEqual(self.bank.frame(pet, math.pi / 2, 1)[1], "climb")
        self.assertIs(self.bank.frame(pet, math.pi / 2, 1)[2], self.bank.frame(pet, math.pi / 2, 2)[2])

    def test_whole_sprite_fits_free_lanes_across_window_sizes(self):
        for width, height in ((820, 520), (1180, 780), (1280, 880), (1920, 1080)):
            panel = (290, 210, width - 316, height - 270)
            blocked = [(291, 266, width - 318, height - 350), (290, 112, min(580, width - 310), 18),
                       (290, 60, 250, 28), (width - 170, 45, 135, 36), (0, height - 70, 264, 70)]
            routes, sizes = build_sprite_routes(width, height, panel, (0, 230, 264, height - 300),
                                                430, blocked, 144, self.bank.aspect_ratio)
            for route, size in zip(routes, sizes):
                half = size * self.bank.aspect_ratio / 2
                for i in range(101):
                    x, y, _ = sample_route(route, route_length(route) * i / 100)
                    self.assertGreaterEqual(x - half, 0)
                    self.assertLessEqual(x + half, width)
                    self.assertGreaterEqual(y - size, 0)
                    self.assertLessEqual(y + 4, height)
                    for bx, by, bw, bh in blocked:
                        intersects = x - half < bx + bw and x + half > bx and y - size < by + bh and y + 4 > by
                        self.assertFalse(intersects, (width, height, x, y, size, (bx, by, bw, bh)))

    def test_renderer_preserves_upright_full_body_for_left_and_right(self):
        for direction in (0, math.pi, math.pi / 2, -math.pi / 2):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 240)
            context = cairo.Context(surface)
            self.bank.draw(context, Pet(0, 10, 25, 0, 10), 150, 210, direction, 1, 144,
                           [(50, 210), (250, 210)], "all")
            surface.flush()
            image = Image.frombuffer("RGBA", (300, 240), bytes(surface.get_data()), "raw", "BGRA", surface.get_stride(), 1)
            left, top, right, bottom = image.getchannel("A").getbbox()
            self.assertGreater(bottom - top, right - left, "Character was rotated sideways")


if __name__ == "__main__":
    unittest.main()
