import math
from pathlib import Path
import tempfile
import unittest

from codex_deck.core import Settings
from codex_deck.pets import PET_SIZE, PetScene, build_routes, sample_route


class PetSceneTests(unittest.TestCase):
    def test_routes_stay_in_window_after_resize_and_sidebar_fills(self):
        scene = PetScene(5)
        for width, height, free in ((1180, 780, 450), (820, 520, 800), (1920, 1080, 440)):
            routes = build_routes(width, height, (290, 150, width - 316, height - 210),
                                  (0, 230, 264, height - 300), free)
            scene.set_routes(routes)
            for step in range(3000):
                scene.advance(.1)
                for pet, x, y, angle in scene.poses():
                    self.assertGreaterEqual(x, PET_SIZE / 2)
                    self.assertLessEqual(x, width - PET_SIZE / 2)
                    self.assertGreaterEqual(y, PET_SIZE / 2)
                    self.assertLessEqual(y, height - PET_SIZE / 2)
                    self.assertTrue(math.isfinite(angle))

    def test_walking_climbing_and_rest_resume(self):
        route = [(30, 30), (130, 30), (130, 130), (30, 130)]
        self.assertEqual(sample_route(route, 50), (80, 30, 0))
        self.assertEqual(sample_route(route, 150), (130, 80, math.pi / 2))
        scene = PetScene(1)
        scene.set_routes([route])
        pet = scene.pets[0]
        pet.rest_in = 0
        scene.advance(.1)
        self.assertGreater(pet.pause, 0)
        distance = pet.distance
        scene.advance(.1)
        self.assertEqual(pet.distance, distance)
        for _ in range(40):
            scene.advance(.1)
        self.assertGreater(pet.distance, distance)

    def test_resume_after_long_delay_has_no_large_jump(self):
        scene = PetScene(3)
        scene.set_routes(build_routes(1180, 780, (290, 150, 864, 570), (0, 230, 264, 480), 400))
        before = [pet.distance for pet in scene.pets]
        scene.advance(300)
        for old, pet in zip(before, scene.pets):
            self.assertLessEqual(pet.distance - old, pet.speed * .1 + .001)

    def test_pet_switch_and_count_persist_and_invalid_count_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(path)
            settings.values.update(pets_enabled=False, pet_count=5)
            settings.save([])
            loaded = Settings(path)
            self.assertFalse(loaded.values["pets_enabled"])
            self.assertEqual(loaded.values["pet_count"], 5)
            path.write_text('{"pet_count": -9}')
            self.assertEqual(Settings(path).values["pet_count"], 1)
            path.write_text('{"pet_count": "bad"}')
            self.assertEqual(Settings(path).values["pet_count"], 3)


if __name__ == "__main__":
    unittest.main()
