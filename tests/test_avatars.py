from pathlib import Path
import json
import tempfile
import unittest

from codex_deck.avatars import AvatarLibrary, GdkPixbuf
from codex_deck.core import Settings


class AvatarPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sources = self.root / "source images"
        self.sources.mkdir()
        self.library = AvatarLibrary(self.root / "state")

    def tearDown(self):
        self.temp.cleanup()

    def image(self, name, color):
        path = self.sources / name
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 40, 80)
        image.fill(color)
        image.savev(str(path), "png", [], [])
        return path

    def test_imported_pack_survives_removal_of_originals_and_reload(self):
        first = self.image("初音 一.png", 0x53b9aaff)
        second = self.image("初音 二.png", 0xfaabc8ff)
        group, count, errors = self.library.import_files([first, second], "我的头像集")
        self.assertEqual(count, 2)
        self.assertEqual(errors, [])
        first.unlink()
        second.unlink()
        loaded = AvatarLibrary(self.root / "state")
        avatars = loaded.for_pack(group)
        self.assertEqual(len(avatars), 2)
        for avatar in avatars:
            self.assertTrue(avatar.path.is_relative_to(self.root / "state"))
            picture = GdkPixbuf.Pixbuf.new_from_file(str(avatar.path))
            self.assertEqual((picture.get_width(), picture.get_height()), (256, 256))
        self.assertNotEqual(avatars[0].path.read_bytes(), avatars[1].path.read_bytes())

    def test_duplicate_images_and_invalid_files_do_not_break_valid_imports(self):
        first = self.image("one.png", 0x53b9aaff)
        duplicate = self.image("same.png", 0x53b9aaff)
        bad = self.sources / "broken.png"
        bad.write_text("not an image")
        group, count, errors = self.library.import_files([first, duplicate, bad], "mixed")
        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(self.library.for_pack(group)), 1)

    def test_appearance_and_selected_pack_persist_with_sessions(self):
        path = self.root / "state" / "settings.json"
        settings = Settings(path)
        settings.values.update(theme="day", motion=False, avatar_pack="custom-example")
        settings.save([])
        loaded = Settings(path)
        self.assertEqual(loaded.values["theme"], "day")
        self.assertFalse(loaded.values["motion"])
        self.assertEqual(loaded.values["avatar_pack"], "custom-example")

    def test_unknown_pack_and_missing_avatar_have_a_local_fallback(self):
        avatars = self.library.for_pack("deleted-pack")
        self.assertTrue(avatars)
        self.assertTrue(all(item.path.exists() for item in avatars))
        self.assertTrue(self.library.get("deleted-avatar").path.exists())

    def test_only_new_portraits_are_bundled(self):
        self.assertEqual(self.library.packs, {"portraits": "御姐头像 · 10 款"})
        avatars = self.library.for_pack()
        self.assertEqual([a.id for a in avatars], [f"portrait-{i:02}" for i in range(1, 11)])
        for avatar in avatars:
            image = GdkPixbuf.Pixbuf.new_from_file(str(avatar.path))
            self.assertEqual((image.get_width(), image.get_height()), (256, 256))

    def test_saved_legacy_avatar_ids_migrate_without_changing_project_or_custom_choice(self):
        path = self.root / "settings.json"
        path.write_text(json.dumps({"avatar_pack": "miku", "sessions": [
            {"name": "one", "cwd": "/tmp/project-a", "avatar": "miku-07"},
            {"name": "two", "cwd": "/tmp/project-b", "avatar": "luna"},
            {"name": "three", "cwd": "/tmp/project-c", "avatar": "custom-personal-001"},
        ]}))
        settings = Settings(path)
        self.assertEqual(settings.values["avatar_pack"], "portraits")
        self.assertEqual([s["avatar"] for s in settings.values["sessions"]],
                         ["portrait-07", "portrait-01", "custom-personal-001"])
        self.assertEqual([s["cwd"] for s in settings.values["sessions"]],
                         ["/tmp/project-a", "/tmp/project-b", "/tmp/project-c"])


if __name__ == "__main__":
    unittest.main()
