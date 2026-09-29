"""
Build the Windows app and its installer, from the repository root:

    python desktop/build.py                 ->  desktop/dist/Doosra/Doosra.exe and desktop/dist/DoosraSetup-<version>.exe
    python desktop/build.py --no-installer  (just the app)

Steps: the frontend (served by the app itself, so built with an empty API base), the pinned
llama.cpp builds, PyInstaller, the app's self-test, then Inno Setup. Needs Node, the backend's
requirements plus pyinstaller and pywebview, and Inno Setup 6 (ISCC) for the installer.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
DIST = DESKTOP / "dist"
VERSION = re.search(r'VERSION = "([^"]+)"', (ROOT / "backend" / "version.py").read_text()).group(1)
ISCC = ["ISCC.exe", r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe")]


def run(args: list[str], **kw) -> None:
    print("\n>", " ".join(map(str, args)), flush=True)
    subprocess.run(args, check=True, **kw)


def iscc() -> str:
    for c in ISCC:
        if found := shutil.which(c) or (Path(c).exists() and c):
            return found
    sys.exit("Inno Setup 6 not found (https://jrsoftware.org/isinfo.php, or: winget install JRSoftware.InnoSetup)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-installer", action="store_true")
    parser.add_argument("--out", type=Path, default=DIST, help="where the app folder and installer go")
    parser.add_argument("--skip-frontend", action="store_true", help="use the frontend/dist already built")
    args = parser.parse_args()
    out = args.out.resolve()
    print(f"Doosra {VERSION} -> {out}")

    if not args.skip_frontend:
        npm = shutil.which("npm") or sys.exit("npm not found")
        run([npm, "ci"], cwd=ROOT / "frontend")
        run([npm, "run", "build"], cwd=ROOT / "frontend", env={**os.environ, "VITE_API_BASE": ""})
    run([sys.executable, str(DESKTOP / "fetch_engine.py")])
    run([sys.executable, "-m", "PyInstaller", str(DESKTOP / "doosra.spec"), "--noconfirm",
         "--distpath", str(out), "--workpath", str(DESKTOP / "build" / "pyinstaller")], cwd=ROOT)
    exe = out / "Doosra" / "Doosra.exe"
    home = DESKTOP / "build" / "selftest-home"
    shutil.rmtree(home, ignore_errors=True)
    try:
        run([str(exe), "--self-test"], env={**os.environ, "DOOSRA_HOME": str(home)})
    finally:                                   # a windowed exe has no console: its report is in the log folder
        report = home / "logs" / "console.log"
        print(report.read_text(encoding="utf-8") if report.exists() else "(no self-test report)")
    if not args.no_installer:
        run([iscc(), f"/DAppVersion={VERSION}", f"/DSourceDir={out / 'Doosra'}", f"/O{out}",
             str(DESKTOP / "installer.iss")])
        print(f"\nInstaller: {out / f'DoosraSetup-{VERSION}.exe'}")


if __name__ == "__main__":
    main()
