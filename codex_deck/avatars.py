"""Local avatar packs, copied into the workspace and normalized to PNG."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import uuid

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from .core import DEFAULT_AVATAR, ROOT

SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}


@dataclass(frozen=True)
class Avatar:
    id: str
    name: str
    path: Path
    pack: str


def normalize_image(source, destination):
    source = Path(source)
    if source.stat().st_size > 12 * 1024 * 1024:
        raise ValueError("图片超过 12 MB")
    info, width, height = GdkPixbuf.Pixbuf.get_file_info(str(source))
    if not info or width < 1 or height < 1:
        raise ValueError("无法识别的图片格式")
    if width > 8192 or height > 8192 or width * height > 32000000:
        raise ValueError("图片尺寸过大")
    image = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(source), 256, 256, True)
    square = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 256, 256)
    square.fill(0x00000000)
    image.copy_area(0, 0, image.get_width(), image.get_height(), square,
                    (256 - image.get_width()) // 2, (256 - image.get_height()) // 2)
    square.savev(str(destination), "png", [], [])
    return hashlib.sha256(bytes(square.get_pixels())).hexdigest()


class AvatarLibrary:
    def __init__(self, state_dir):
        self.directory = Path(state_dir) / "avatars"
        self.reload()

    def reload(self):
        portraits = ROOT / "assets" / "avatars" / "portraits"
        bundled = json.loads((portraits / "catalog.json").read_text())
        self.packs = {"portraits": bundled["name"]}
        self.items = {item["id"]: Avatar(item["id"], item["name"], portraits / item["file"], "portraits")
                      for item in bundled["items"]}
        self.catalog = []
        try:
            data = json.loads((self.directory / "catalog.json").read_text())
            if isinstance(data, list):
                self.catalog = data
        except (OSError, ValueError):
            pass
        for group in self.catalog:
            if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not isinstance(group.get("items"), list):
                continue
            for item in group["items"]:
                if not isinstance(item, dict) or not isinstance(item.get("file"), str) or not isinstance(item.get("id"), str):
                    continue
                path = (self.directory / item["file"]).resolve()
                if not path.is_relative_to(self.directory.resolve()) or not path.is_file():
                    continue
                self.packs[group["id"]] = str(group.get("name", "我的头像"))
                self.items[item["id"]] = Avatar(item["id"], str(item.get("name", "头像")), path, group["id"])

    def get(self, key):
        return self.items.get(key, self.items[DEFAULT_AVATAR])

    def for_pack(self, pack="portraits"):
        if pack == "auto" or pack not in self.packs:
            pack = "portraits"
        return [item for item in self.items.values() if item.pack == pack]

    def import_files(self, paths, name):
        paths = [Path(p) for p in paths]
        if not paths or len(paths) > 100:
            raise ValueError("一次请选择 1–100 张图片")
        group_id = "custom-" + uuid.uuid4().hex[:12]
        folder = self.directory / group_id
        folder.mkdir(parents=True, mode=0o700)
        items, errors, hashes = [], [], set()
        for index, path in enumerate(paths):
            key = f"{group_id}-{index:03}"
            output = folder / f"{key}.png"
            try:
                digest = normalize_image(path, output)
                if digest in hashes:
                    output.unlink()
                    continue
                hashes.add(digest)
                items.append({"id": key, "name": path.stem[:40], "file": str(output.relative_to(self.directory))})
            except Exception as error:
                output.unlink(missing_ok=True)
                errors.append(f"{path.name}: {error}")
        if not items:
            folder.rmdir()
            raise ValueError("没有可导入的图片。\n" + "\n".join(errors[:5]))
        self.catalog.append({"id": group_id, "name": name[:60], "items": items})
        temp = self.directory / "catalog.tmp"
        temp.write_text(json.dumps(self.catalog, ensure_ascii=False, indent=2))
        temp.replace(self.directory / "catalog.json")
        self.reload()
        return group_id, len(items), errors
