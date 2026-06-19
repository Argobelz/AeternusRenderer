"""
build.py — Packages Aeternus Renderer for release.

Two modes:
  python build.py          # package app/ + addon/ into a zip ready for GitHub Release
  python build.py --exe    # additionally compile a standalone .exe with PyInstaller

Release workflow (after running this script):
  gh release create v{VERSION} dist/AeternusRenderer.zip --title "v{VERSION}" --notes "Release notes here"

That makes the zip available at the URL stored in version.json, which the
in-app updater fetches automatically on next launch.
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

# Files and folders to include in the release zip
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


def build_zip(version):
    """Package source files into a release zip."""
    os.makedirs(DIST_DIR, exist_ok=True)
    zip_path = os.path.join(DIST_DIR, f"{APP_NAME}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ZIP_INCLUDES:
            if os.path.exists(path):
                # Preserve the relative path inside the zip so the updater
                # can find app/main.py at the expected location.
                zf.write(path, path)
                print(f"  + {path}")
            else:
                print(f"  ! MISSING: {path} — skipped")
    print(f"\n[build] Zip created: {zip_path}")
    return zip_path


def build_exe(version):
    """Compile a standalone .exe with PyInstaller (optional)."""
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
    print(f"[build] Exe built: {exe}")


def update_version_json(version):
    """Sync version.json zip_url to match the new version tag."""
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


def main():
    build_exe_flag = "--exe" in sys.argv
    version = get_version()
    clean()
    update_version_json(version)
    zip_path = build_zip(version)
    if build_exe_flag:
        build_exe(version)

    print(f"""
[release] v{version} ready.

Next steps:
  1. Commit:   git add app/main.py version.json
               git commit -m "Release v{version}"
               git push

  2. Publish:  gh release create v{version} {zip_path} --title "v{version}" --notes "..."

  All installed copies will auto-update on next launch.
""")


if __name__ == "__main__":
    main()
