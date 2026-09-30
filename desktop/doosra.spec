# PyInstaller build of Doosra.exe (onedir: faster start and fewer antivirus false alarms than onefile).
#
#   cd frontend && VITE_API_BASE= npm run build          (bash; the app is served by the API, same origin)
#   python desktop/fetch_engine.py                         (the pinned llama.cpp builds)
#   pyinstaller desktop/doosra.spec --noconfirm            -> desktop/dist/Doosra/Doosra.exe
#
# Run it from the repository root.

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                  VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parent
BACKEND = ROOT / "backend"
DIST = ROOT / "frontend" / "dist"
ENGINE = ROOT / "desktop" / "build" / "engine"
VERSION = re.search(r'VERSION = "([^"]+)"', (BACKEND / "version.py").read_text()).group(1)
NUMS = tuple(int(n) for n in (VERSION.split(".") + ["0"] * 4)[:4])
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMS, prodvers=NUMS, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "Doosra"), StringStruct("FileDescription", "Doosra cricket analytics"),
        StringStruct("FileVersion", VERSION), StringStruct("ProductName", "Doosra"),
        StringStruct("ProductVersion", VERSION), StringStruct("OriginalFilename", "Doosra.exe"),
        StringStruct("LegalCopyright", "Doosra contributors")])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])])])

for need, how in ((DIST / "index.html", "build the frontend with VITE_API_BASE empty"),
                  (ENGINE / "vulkan" / "llama-server.exe", "run desktop/fetch_engine.py")):
    if not need.exists():
        raise SystemExit(f"missing {need}: {how}")

hidden = (collect_submodules("uvicorn") + collect_submodules("webview")
          + collect_submodules("agent") + collect_submodules("analytics") + collect_submodules("api")
          + collect_submodules("ingest") + collect_submodules("mcp_server")
          + ["main", "sqlalchemy.dialects.sqlite", "compression.zstd"])

datas = [
    (str(BACKEND / "models.json"), "."),
    (str(BACKEND / "models"), "models"),                      # the win-probability models (JSON)
    (str(DIST), "frontend/dist"),
    (str(ENGINE), "engine"),
    (str(ROOT / "desktop" / "assets" / "doosra.ico"), "assets"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
] + collect_data_files("webview")

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(BACKEND)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "psycopg", "psycopg_binary",
              "PyQt5", "PyQt6", "PySide2", "PySide6", "gi"],
    noarchive=False,
)
# llama.cpp's DLLs belong next to llama-server.exe only (the Vulkan and CPU builds each have their own
# ggml.dll); PyInstaller would otherwise also copy them into the top folder as dependencies of each other.
a.binaries = [b for b in a.binaries
              if Path(b[0]).parts[0] == "engine" or not Path(b[1]).resolve().is_relative_to(ENGINE.resolve())]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Doosra",
    icon=str(ROOT / "desktop" / "assets" / "doosra.ico"),
    console=False,
    upx=False,
    version=VERSION_INFO,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Doosra", upx=False)
