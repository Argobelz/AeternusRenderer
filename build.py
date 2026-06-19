"""
build.py — Packages Aeternus Renderer for release.

Running this script does three things:
  1. Compiles dist/AeternusRenderer.exe  (pin to taskbar, double-click to run)
  2. Packages dist/AeternusRenderer.zip  (uploaded to GitHub Release for auto-updater)
  3. Updates version.json with the correct release URL

Release workflow after running:
  gh release create v{VERSION} dist/AeternusRenderer.zip --title "v{VERSION}" --notes "..."

The exe is for you to run locally. The zip is what the in-app updater downloads.
Pin the exe to your taskbar — it will update itself in place on next launch.
"""

import subprocess
import sys
import os
import shutil
import re
import zipfile
import json

APP_NAME    = "AeternusRenderer"
ENTRY_POINT = "app/main.py"
ICON        = "app/icon.ico"
DIST_DIR    = "dist"
BUILD_DIR   = "build"

# Files included in the updater zip (not the exe — exe is compiled separately)
ZIP_INCLUDES = [
    "app/main.py",
    "addon/aeternus_send_job.py",
    "version.json",
    "README.md",
]


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


def update_version_json(version):
    vpath = "version.json"
    with open(vpath) as f:
        data = json.load(f)
    data["version"] = version
    data["zip_url"] = (
        f"https://github.com/Argobelz/AeternusRenderer/releases/download/"
        f"v{version}/{APP_NAME}.zip"
    )
    with open(vpath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[build] version.json updated → v{version}")


def build_exe(version):
    """Compile standalone exe with PyInstaller."""
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

    print(f"[build] Compiling exe v{version}…")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\n[build] PyInstaller FAILED — see errors above.")
        sys.exit(1)
    exe = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
    print(f"[build] Exe ready: {exe}")
    return exe


def build_zip(version):
    """Package source files into the updater zip."""
    zip_path = os.path.join(DIST_DIR, f"{APP_NAME}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ZIP_INCLUDES:
            if os.path.exists(path):
                zf.write(path, path)
                print(f"  + {path}")
            else:
                print(f"  ! MISSING: {path} — skipped")
    print(f"[build] Zip ready:  {zip_path}")
    return zip_path


def main():
    version = get_version()
    clean()
    update_version_json(version)

    # Always build exe first so you have something to pin/run
    exe_path = build_exe(version)

    # Then package the updater zip
    zip_path = build_zip(version)

    print(f"""
[release] v{version} ready.

  {exe_path}   ← pin this to your taskbar
  {zip_path}    ← upload this to GitHub Release

Steps:
  1. git add app/main.py version.json
     git commit -m "Release v{version}"
     git push

  2. gh release create v{version} dist/{APP_NAME}.zip --title "v{version}" --notes "..."

All installed copies auto-update on next launch.
""")


if __name__ == "__main__":
    main()
