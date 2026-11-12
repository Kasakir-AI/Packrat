# PyInstaller recipe for Packrat:  pyinstaller --noconfirm packrat.spec
# Windows: dist/Packrat/Packrat.exe (window, no console) + packrat-cli.exe (commands), sharing one _internal folder.
# macOS:   dist/Packrat.app (the window; Contents/MacOS/packrat-cli runs the commands).
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

here = os.path.abspath(os.getcwd())
datas = [(os.path.join(here, "packrat", "sources"), os.path.join("packrat", "sources")),
         (os.path.join(here, "packrat", "assets"), os.path.join("packrat", "assets"))]
datas += collect_data_files("dukpy")        # its JavaScript runtime files (the site sources run on it)
hidden = collect_submodules("keyring.backends") + collect_submodules("packrat") + ["dukpy"]
if sys.platform == "win32":
    hidden.append("win32timezone")
excludes = ["PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
            "PySide6.QtQuickControls2", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtSerialPort",
            "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
            "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtTest", "PySide6.QtSql",
            "PySide6.QtPdf", "PySide6.QtPdfWidgets", "pytest"]
a = Analysis(["packrat_entry.py"], pathex=[here], datas=datas, hiddenimports=hidden, excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
version_file = os.path.join(here, "tools", "version_info.txt")
if sys.platform == "darwin":
    # the Mac icon (.icns) is made from the 256 px picture at build time
    from PIL import Image
    icon = os.path.join(workpath, "packrat.icns")
    os.makedirs(workpath, exist_ok=True)
    Image.open(os.path.join(here, "packrat", "assets", "packrat.png")).save(icon)
else:
    icon = os.path.join(here, "packrat", "assets", "packrat.ico")
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Packrat", console=False, icon=icon,
          version=version_file if sys.platform == "win32" and os.path.exists(version_file) else None)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="packrat-cli", console=True, icon=icon)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="Packrat")
if sys.platform == "darwin":
    import re
    ver = re.search(r'__version__ = "(.+)"', open(os.path.join(here, "packrat", "__init__.py")).read()).group(1)
    app = BUNDLE(coll, name="Packrat.app", icon=icon, bundle_identifier="ai.kasakir.packrat", version=ver,
                 info_plist={"CFBundleName": "Packrat", "CFBundleDisplayName": "Packrat", "CFBundleShortVersionString": ver,
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0",
                             "NSHumanReadableCopyright": "Packrat by Kasakir-AI"})
