#!/usr/bin/env python3
"""Build a relocatable macOS app with its own Python, GTK/VTE and Bash."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    if sys.platform != "darwin":
        raise SystemExit("请在 macOS 上打包；Ubuntu 继续使用 launch.sh。")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    # Only PATH is recorded, never credentials or the rest of the environment.
    (build / "macos-path.json").write_text(json.dumps(os.environ.get("PATH", os.defpath).split(os.pathsep)))
    icons = build / "CodexDeck.iconset"
    icons.mkdir(exist_ok=True)
    prefix = Path(subprocess.check_output(["brew", "--prefix"], text=True).strip())
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            subprocess.run([str(prefix / "bin/rsvg-convert"), "-w", str(size * scale),
                            "-h", str(size * scale), "-o", str(icons / name), str(ROOT / "assets/icon.svg")], check=True)
    subprocess.run(["iconutil", "-c", "icns", str(icons), "-o", str(build / "CodexDeck.icns")], check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    str(ROOT / "macos/CodexDeck.spec")], cwd=ROOT, check=True)
    print(f"已生成：{ROOT / 'dist/Codex Deck.app'}")


if __name__ == "__main__":
    main()
