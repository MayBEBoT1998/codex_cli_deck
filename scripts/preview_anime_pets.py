#!/usr/bin/python3
"""Render previews through the same sprite renderer as the live pet overlay."""
import math
from pathlib import Path
import sys

import cairo
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_deck.pet_sprites import SpriteBank
from codex_deck.pets import Pet


def raster(surface):
    surface.flush()
    return Image.frombuffer("RGBA", (surface.get_width(), surface.get_height()), bytes(surface.get_data()),
                            "raw", "BGRA", surface.get_stride(), 1).convert("RGB")


def main():
    bank = SpriteBank()
    output = Path(__file__).resolve().parent.parent / "artifacts"
    output.mkdir(exist_ok=True)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 900, 720)
    cr = cairo.Context(surface)
    for row in range(3):
        for col in range(3):
            cr.set_source_rgb(*((.08, .10, .13) if row != 1 else (.91, .94, .96)))
            cr.rectangle(col * 300, row * 240, 300, 240); cr.fill()
            pet = Pet(0, 7, 25, col, 10, pause=1 if row == 0 else 0)
            x, y = col * 300 + 150, row * 240 + 222
            bank.draw(cr, pet, x, y, math.pi / 2 if row == 2 else 0, 0,
                      200, [(x - 80, y), (x, y)])
            cr.set_source_rgb(*((.65, .75, .83) if row != 1 else (.22, .30, .36)))
            cr.set_font_size(13); cr.move_to(col * 300 + 14, row * 240 + 20)
            cr.show_text(("IDLE", "WALK", "CLIMB")[row] + " / " + ("BLACK", "SILVER", "WINE")[col])
    surface.write_to_png(str(output / "anime-pets-preview.png"))
    animation = []
    for index in range(64):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 900, 260)
        cr = cairo.Context(surface)
        cr.set_source_rgb(.08, .10, .13); cr.paint()
        mode = "idle" if index < 12 else "walk" if index < 40 else "climb"
        for col in range(3):
            pet = Pet(0, index / 12 * 25, 25, col, 10, pause=1 if mode == "idle" else 0)
            x = col * 300 + 150
            bank.draw(cr, pet, x, 242, math.pi / 2 if mode == "climb" else 0,
                      4 + index / 12, 216, [(x - 80, 242), (x, 242)])
        animation.append(raster(surface))
    animation[0].save(output / "anime-pets-animation.gif", save_all=True,
                      append_images=animation[1:], duration=83, loop=0)
    print(output / "anime-pets-preview.png")
    print(output / "anime-pets-animation.gif")


if __name__ == "__main__":
    main()
