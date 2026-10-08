# Build with scripts/build_macos.py on the target Mac architecture.
from pathlib import Path
import subprocess

root = Path(SPECPATH).parent
prefix = Path(subprocess.check_output(["brew", "--prefix"], text=True).strip())
gtk = prefix / "lib/gtk-3.0/3.0.0/immodules/im-quartz.so"
analysis = Analysis(
    [str(root / "scripts/macos_entry.py")],
    pathex=[str(root)],
    binaries=[(str(prefix / "bin/bash"), "bin"), (str(gtk), "lib/gtk-3.0/3.0.0/immodules")],
    datas=[(str(root / "assets"), "assets"),
           (str(root / "build/macos-path.json"), ".")],
    hiddenimports=["scripts.run_codex", "scripts.notify", "gi._gi_cairo", "gi.repository.Vte"],
    hookspath=[str(root / "macos/hooks")],
    hooksconfig={"gi": {"versions": {"Gtk": "3.0", "Vte": "2.91"},
                         "languages": ["zh_CN", "en_GB"], "icons": ["hicolor"], "themes": []}},
    excludes=["tkinter", "numpy", "cv2", "PIL", "unittest"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
          name="Codex Deck", console=False, argv_emulation=False)
collection = COLLECT(exe, analysis.binaries, analysis.datas, name="Codex Deck")
app = BUNDLE(collection, name="Codex Deck.app",
             icon=str(root / "build/CodexDeck.icns"),
             bundle_identifier="io.github.maybebot1998.codex-deck",
             info_plist={"CFBundleDisplayName": "Codex Deck", "CFBundleShortVersionString": "0.2.0",
                         "CFBundleVersion": "2", "NSHighResolutionCapable": True,
                         "NSPrincipalClass": "NSApplication",
                         "LSApplicationCategoryType": "public.app-category.developer-tools"})
