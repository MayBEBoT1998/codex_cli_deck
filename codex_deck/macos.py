"""macOS startup configuration; imported before GTK initializes."""
import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile

_im_cache = None


def configure_input_method():
    global _im_cache
    if sys.platform != "darwin":
        return
    if getattr(sys, "frozen", False):
        if _im_cache is None:
            module = Path(sys._MEIPASS) / "lib/gtk-3.0/3.0.0/immodules/im-quartz.so"
            if not module.is_file():
                raise RuntimeError("应用缺少 macOS 输入法模块，请重新打包。")
            # GTK's cache embeds absolute paths. Generate it outside the signed
            # bundle so moving the app to Applications does not break the IME.
            handle = tempfile.NamedTemporaryFile(mode="w", prefix="codex-deck-im-", suffix=".cache", delete=False)
            _im_cache = Path(handle.name)
            escaped = str(module).replace("\\", "\\\\").replace('"', '\\"')
            with handle:
                handle.write(f'"{escaped}"\n"quartz" "Mac OS X Quartz" "gtk30" "" "ja:ko:zh:*"\n')
            atexit.register(lambda: _im_cache.unlink(missing_ok=True))
        cache = _im_cache
    else:
        brew = shutil.which("brew")
        prefix = Path(brew).parent.parent if brew else Path("/opt/homebrew" if Path("/opt/homebrew").is_dir() else "/usr/local")
        cache = prefix / "lib/gtk-3.0/3.0.0/immodules.cache"
        if not cache.is_file():
            raise RuntimeError("未找到 macOS 输入法缓存，请重新执行 bash scripts/setup_macos.sh。")
    os.environ["GTK_IM_MODULE_FILE"] = str(cache)
    os.environ["GTK_IM_MODULE"] = "quartz"


def configure_bundle():
    """Finder has a minimal PATH; preserve the build machine's CLI discovery."""
    if not getattr(sys, "frozen", False):
        return
    import json
    root = Path(sys._MEIPASS)
    paths = [str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    try:
        paths.extend(json.loads((root / "macos-path.json").read_text()))
    except (OSError, ValueError):
        pass
    paths.extend(os.environ.get("PATH", os.defpath).split(os.pathsep))
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(p for p in paths if isinstance(p, str) and p))
