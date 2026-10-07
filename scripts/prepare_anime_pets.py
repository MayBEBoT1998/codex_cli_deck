#!/usr/bin/python3
"""Extract eight characters by their alpha silhouettes, preserving hands across grid lines.

Usage: /usr/bin/python3 scripts/prepare_anime_pets.py SHEET.png CHARACTER_ID
This only processes an existing generated image; it does not generate artwork.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def prepare(sheet_path, character_id, name=None):
    if not character_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in character_id):
        raise ValueError("CHARACTER_ID 仅能包含小写字母、数字、短横线和下划线")
    destination = ROOT / "assets" / "pets" / "anime" / character_id
    if destination.exists():
        raise ValueError("该角色目录已存在，请使用新的版本名称以保留已有图片")
    with Image.open(sheet_path) as source:
        source.load()
        sheet = source.convert("RGBA")
    if sheet.getchannel("A").getextrema()[0] == 255:
        raise ValueError("图片没有透明背景，请生成带真实透明通道的 PNG")
    pixels = np.asarray(sheet).copy()
    alpha = pixels[:, :, 3]
    count, labels, stats, centers = cv2.connectedComponentsWithStats((alpha > 8).astype("uint8"), 8)
    candidates = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] > sheet.width * sheet.height * .005]
    if len(candidates) != 8:
        raise ValueError(f"检测到 {len(candidates)} 个人物轮廓，预期为 8 个；请检查动作是否互相重叠")
    ordered = sorted(candidates, key=lambda i: centers[i][1])
    ordered = sorted(ordered[:4], key=lambda i: centers[i][0]) + sorted(ordered[4:], key=lambda i: centers[i][0])
    frames, anchors, boxes = [], [], []
    for component in ordered:
        # A small dilation keeps antialiasing at the silhouette without including
        # neighbouring characters or the low-alpha haze elsewhere on the sheet.
        mask = cv2.dilate((labels == component).astype("uint8"), np.ones((5, 5), dtype="uint8"))
        clean = pixels.copy()
        matte = np.where((mask > 0) & (alpha > 3), np.minimum(alpha.astype("float32") * 255 / 253, 255), 0).astype("uint8")
        clean[:, :, 3] = matte
        clean[matte == 0, :3] = 0
        image = Image.fromarray(clean, "RGBA")
        bounds = image.getchannel("A").getbbox()
        image = image.crop(bounds)
        a = np.asarray(image)[:, :, 3]
        # Anchor by the upper torso, not the bounding-box center: an extended arm
        # or forward shoe must not shift the entire character between frames.
        region = a[int(image.height * .22):int(image.height * .46)] > 64
        anchor = float(np.where(region)[1].mean())
        frames.append(image)
        anchors.append(anchor)
        boxes.append(list(bounds))
    height = max(frame.height for frame in frames) + 16
    half_width = int(max(max(anchor, frame.width - anchor) for frame, anchor in zip(frames, anchors))) + 10
    canvas_size = (half_width * 2, height)
    scale = min(1, 320 / height)
    size = (round(canvas_size[0] * scale), round(height * scale))
    destination.mkdir(parents=True)
    for index, (frame, anchor) in enumerate(zip(frames, anchors)):
        canvas = Image.new("RGBA", canvas_size)
        canvas.alpha_composite(frame, (round(half_width - anchor), height - 8 - frame.height))
        canvas = canvas.resize(size, getattr(Image, "Resampling", Image).LANCZOS)
        canvas.save(destination / f"frame-{index:02}.png")
    sheet.save(destination / "source-sheet.png")
    manifest = {
        "id": character_id, "name": name or character_id, "display_height": 144,
        "frame_size": list(size), "anchor": [0.5, (height - 8) / height],
        "source_sha256": hashlib.sha256(Path(sheet_path).read_bytes()).hexdigest(),
        "source_bounds": boxes,
        "idle": ["frame-00.png", "frame-01.png"],
        "walk": [f"frame-{index:02}.png" for index in range(2, 6)],
        "climb": ["frame-06.png", "frame-07.png"],
        "fps": {"idle": 1, "walk": 3, "climb": 2},
        "direction": "right", "rotate_whole_character": False,
    }
    (destination / "sprite.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sheet", type=Path)
    parser.add_argument("character_id")
    parser.add_argument("--name")
    args = parser.parse_args()
    try:
        print(prepare(args.sheet, args.character_id, args.name))
    except (OSError, ValueError) as error:
        parser.error(str(error))
