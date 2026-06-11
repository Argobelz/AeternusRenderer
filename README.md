# Aeternus Renderer

Custom Blender render manager for the Aeternus production pipeline.

## Installation

1. Download `AeternusRenderer.exe`
2. Run it — no installation needed
3. Install the Blender addon from `addon/aeternus_send_job.py`

## Updating

Open the app → Settings → **Check Now** to download and install the latest version automatically.

Or enable **Check automatically on launch** to be notified on startup.

## For the developer (Rez)

To release a new version:
1. Bump `APP_VERSION` in `main.py`
2. Build: `python build.py`
3. Update `version.json` with the new version number
4. Push both `AeternusRenderer.exe` and `version.json` to this repo
5. All installed copies will pick it up on next launch

## Addon version

Current: `2.3` — must match the app. Both are updated together.
