# PyInstaller build definition. Build with:  pyinstaller DeviceInventory.spec
# Produces a single double-click program in dist/.
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["launcher.py"],
    datas=[("templates", "templates"), ("static", "static"), ("schema.sql", ".")],
    hiddenimports=collect_submodules("waitress"),
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="DeviceInventory",
    console=True,  # the window shows the address and closing it stops the app
    upx=False,
)
