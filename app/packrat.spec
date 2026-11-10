# PyInstaller recipe for Packrat on Windows:  pyinstaller --noconfirm packrat.spec
# Result: dist/Packrat/Packrat.exe (window, no console) + packrat-cli.exe (commands), sharing one _internal folder.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

here = os.path.abspath(os.getcwd())
datas = [(os.path.join(here, "packrat", "sources"), os.path.join("packrat", "sources")),
         (os.path.join(here, "packrat", "assets"), os.path.join("packrat", "assets"))]
datas += collect_data_files("dukpy")        # its JavaScript runtime files (the site sources run on it)
hidden = collect_submodules("keyring.backends") + collect_submodules("packrat") + ["dukpy", "win32timezone"]
excludes = ["PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
            "PySide6.QtQuickControls2", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtSerialPort",
            "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
            "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtTest", "PySide6.QtSql",
            "PySide6.QtPdf", "PySide6.QtPdfWidgets", "pytest"]
a = Analysis(["packrat_entry.py"], pathex=[here], datas=datas, hiddenimports=hidden, excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
icon = os.path.join(here, "packrat", "assets", "packrat.ico")
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Packrat", console=False, icon=icon,
          version=os.path.join(here, "tools", "version_info.txt") if os.path.exists(os.path.join(here, "tools", "version_info.txt")) else None)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="packrat-cli", console=True, icon=icon)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="Packrat")
