"""
build.py — Packages Aeternus Renderer into a standalone Windows .exe
Run: python build.py
Requires: pip install pyinstaller
"""

import subprocess
import sys
import os
import shutil
import re

APP_NAME    = "AeternusRenderer"
ENTRY_POINT = "app/main.py"
ICON        = "app/icon.ico"  # optional — skip if missing
DIST_DIR    = "dist"
BUILD_DIR   = "build"

def get_version():
    with open(ENTRY_POINT) as f:
        for line in f:
            m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', line)
            if m:
                return m.group(1)
    return "unknown"

def clean():
    for d in (DIST_DIR, BUILD_DIR):
        if os.path.exists(d):
            shutil.rmtree(d)
    spec = f"{APP_NAME}.spec"
    if os.path.exists(spec):
        os.remove(spec)
    print("[build] Cleaned previous build artifacts.")

def build():
    version = get_version()
    clean()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
    ]

    if os.path.exists(ICON):
        cmd += ["--icon", ICON]

    cmd.append(ENTRY_POINT)

    print(f"[build] Building v{version}...")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        exe = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
        print(f"\n[build] SUCCESS — {exe}")
        print(f"\n[release] Next steps to publish v{version}:")
        print(f"  1. Update version.json → set \"version\": \"{version}\"")
        print(f"  2. git add dist/AeternusRenderer.exe version.json")
        print(f"  3. git commit -m \"Release v{version}\"")
        print(f"  4. git push")
        print(f"  → All installed copies will auto-update on next launch.")
    else:
        print("\n[build] FAILED — see errors above.")
        sys.exit(1)

if __name__ == "__main__":
    build()
