"""Build a single-file Windows executable named <app>-v<version>.exe in ./dist.

Usage:  python build.py
The version comes from jira_workflows/__init__.py, so bump it there before building a release.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from jira_workflows import APP_NAME, RELEASE_DATE, __version__  # pylint: disable=wrong-import-position

EXE_NAME = f"{APP_NAME.replace(' ', '')}-v{__version__}"


def version_file() -> Path:
    """Windows file-properties metadata (shown in Explorer > Properties > Details)."""
    parts = [int(x) for x in re.findall(r"\d+", __version__)][:4]
    parts += [0] * (4 - len(parts))
    tup = tuple(parts)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tup}, prodvers={tup}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', ''),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{EXE_NAME}'),
      StringStruct('OriginalFilename', '{EXE_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__} ({RELEASE_DATE})')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ])
"""
    f = Path(tempfile.mkdtemp()) / "version_info.txt"
    f.write_text(text, encoding="utf-8")
    return f


def make_icon() -> Path:
    """Render the app icon with Qt and write a multi-size .ico for the executable."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QGuiApplication
    from PIL import Image
    import io
    _app = QGuiApplication.instance() or QGuiApplication([])   # the icon needs a Qt application to exist
    from jira_workflows.ui import app_icon_pixmap
    buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly)
    app_icon_pixmap(256).save(buf, "PNG")
    out = Path(tempfile.mkdtemp()) / "app.ico"
    Image.open(io.BytesIO(bytes(buf.data()))).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return out


def main():
    for d in ("build", "dist"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", EXE_NAME, "--version-file", str(version_file()), "--icon", str(make_icon()),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'README.md'}{os.pathsep}.",   # the Help page reads the user guide
        "--add-data", f"{ROOT / 'jira_workflows'}{os.pathsep}jira_workflows",   # sources the generated job script bundles (jobscript.py)
        "--exclude-module", "tkinter", "--exclude-module", "qfluentwidgets",
        "--exclude-module", "PyQt6.QtWebEngineCore", "--exclude-module", "PyQt6.QtQml", "--exclude-module", "PyQt6.QtQuick",
        str(ROOT / "main.py"),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = ROOT / "dist" / f"{EXE_NAME}.exe"
    print(f"\nBuilt {exe} ({exe.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
