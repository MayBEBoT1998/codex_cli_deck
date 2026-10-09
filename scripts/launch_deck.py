#!/usr/bin/python3
"""Entry point for source launches and the installed codex-deck command."""
import json
import os
from pathlib import Path
import runpy
import sys


ROOT = Path(__file__).resolve().parent.parent


def main():
    # Resolving __file__ also handles the installed bin/codex-deck symlink.
    # Keep the caller's cwd, so --cwd . works from any project directory.
    installed = ROOT / ".installed.json"
    if installed.is_file():
        metadata = json.loads(installed.read_text(encoding="utf-8"))
        if sys.argv[1:] == ["--uninstall"]:
            sys.argv = [str(ROOT / "scripts/install.py"), "--uninstall",
                        "--prefix", str(ROOT.parent.parent)]
            runpy.run_path(sys.argv[0], run_name="__main__")
            return
        sys.argv[1:1] = ["--state-dir", metadata["state_dir"]]

    # Application-menu launches may not inherit ~/.local/bin from the shell.
    entries = os.environ.get("PATH", "").split(os.pathsep)
    local_bin = str(Path.home() / ".local/bin")
    if local_bin not in entries:
        os.environ["PATH"] = os.pathsep.join([local_bin, *entries])
    sys.path.insert(0, str(ROOT))
    sys.argv[0] = "codex-deck"
    from codex_deck.app import main as run_app
    run_app()


if __name__ == "__main__":
    main()
