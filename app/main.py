"""
Aeternus Renderer v2.7
Standalone Windows render manager for Blender.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import json
import os
import re
import subprocess
import time
import uuid
import shutil
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_TITLE     = "Aeternus Renderer"
APP_VERSION   = "2.9"
ADDON_VERSION = "2.3"
APP_PORT      = 47821
CONFIG_DIR    = os.path.join(os.path.expanduser("~"), ".aeternus_renderer")
DATA_FILE     = os.path.join(CONFIG_DIR, "data.json")
LOG_FILE      = os.path.join(CONFIG_DIR, "render.log")
DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
DEFAULT_PLAYER  = r"C:\Program Files\DJV2\bin\djv.exe"
AUTO_RETRY_MAX  = 3

# Update URLs — raw GitHub content
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/Argobelz/AeternusRenderer/main/version.json"
UPDATE_EXE_URL      = "https://raw.githubusercontent.com/Argobelz/AeternusRenderer/main/AeternusRenderer.exe"

STATUS_WAITING   = "Waiting"
STATUS_RENDERING = "Rendering"
STATUS_DONE      = "Done"
STATUS_FAILED    = "Failed"
STATUS_DISABLED  = "Disabled"

# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

THEMES = {
    "Dark": {
        "bg"         : "#1e1e1e",
        "bg2"        : "#252525",
        "bg3"        : "#161616",
        "bg4"        : "#111111",
        "bg5"        : "#141414",
        "fg"         : "#cccccc",
        "fg2"        : "#888888",
        "fg3"        : "#555555",
        "accent"     : "#7ec8e3",
        "accent2"    : "#4caf50",
        "sep"        : "#333333",
        "sel"        : "#1a3a5a",
        "row_wait"   : ("#222222", "#cccccc"),
        "row_render" : ("#0d2a40", "#7ec8e3"),
        "row_done"   : ("#0d2a0d", "#7ec87e"),
        "row_fail"   : ("#2a0d0d", "#e37e7e"),
        "row_dis"    : ("#1a1a1a", "#555555"),
        "grp_sq"     : ("#1a1a2a", "#7ec8e3"),
        "grp_sh"     : ("#161616", "#888888"),
    },
    "Light": {
        "bg"         : "#f0f0f0",
        "bg2"        : "#e0e0e0",
        "bg3"        : "#d8d8d8",
        "bg4"        : "#c8c8c8",
        "bg5"        : "#dcdcdc",
        "fg"         : "#111111",
        "fg2"        : "#444444",
        "fg3"        : "#888888",
        "accent"     : "#1565c0",
        "accent2"    : "#2e7d32",
        "sep"        : "#bbbbbb",
        "sel"        : "#bbdefb",
        "row_wait"   : ("#ffffff", "#111111"),
        "row_render" : ("#e3f2fd", "#1565c0"),
        "row_done"   : ("#e8f5e9", "#2e7d32"),
        "row_fail"   : ("#ffebee", "#c62828"),
        "row_dis"    : ("#f5f5f5", "#999999"),
        "grp_sq"     : ("#e8eaf6", "#3949ab"),
        "grp_sh"     : ("#eeeeee", "#666666"),
    },
    "Claude": {
        "bg"         : "#1a1a2e",
        "bg2"        : "#22223a",
        "bg3"        : "#16213e",
        "bg4"        : "#0f0f23",
        "bg5"        : "#1a1a2e",
        "fg"         : "#e0e0f0",
        "fg2"        : "#9090b0",
        "fg3"        : "#606080",
        "accent"     : "#c084fc",
        "accent2"    : "#34d399",
        "sep"        : "#2a2a4a",
        "sel"        : "#3b2f6b",
        "row_wait"   : ("#1e1e38", "#e0e0f0"),
        "row_render" : ("#1a2a4a", "#c084fc"),
        "row_done"   : ("#1a2e2a", "#34d399"),
        "row_fail"   : ("#2e1a2a", "#f87171"),
        "row_dis"    : ("#181828", "#505070"),
        "grp_sq"     : ("#22203e", "#c084fc"),
        "grp_sh"     : ("#1c1c34", "#7070a0"),
    },
}

T = THEMES["Dark"]   # active theme — replaced at runtime

def apply_theme(name: str):
    global T
    T = THEMES.get(name, THEMES["Dark"])

# ---------------------------------------------------------------------------
# Updater
# ---------------------------------------------------------------------------

class Updater:
    """
    Checks GitHub for a newer version.json, downloads new exe if available,
    replaces the running exe, and restarts.

    version.json format:
        { "version": "2.9", "exe_url": "https://raw.githubusercontent.com/..." }
    """

    def __init__(self, store: DataStore):
        self._store = store

    # ---- public API --------------------------------------------------------

    def check_and_prompt(self, parent_widget, silent=False):
        """
        Call from UI thread. Spawns background check; shows dialog if update found.
        silent=True  → only prompts when update available (used on auto-launch check).
        silent=False → always shows result (used when user clicks Check Now).
        """
        def _run():
            result = self._fetch_manifest()
            parent_widget.after(0, lambda: self._handle_result(parent_widget, result, silent))
        threading.Thread(target=_run, daemon=True).start()

    # ---- internals ---------------------------------------------------------

    def _fetch_manifest(self):
        """Returns (latest_version, exe_url) or raises."""
        import urllib.request
        try:
            with urllib.request.urlopen(UPDATE_MANIFEST_URL, timeout=8) as r:
                data = json.loads(r.read().decode())
            return data["version"], data.get("exe_url", UPDATE_EXE_URL)
        except Exception as e:
            return None, str(e)

    def _handle_result(self, parent, result, silent):
        latest, exe_url_or_err = result

        if latest is None:
            if not silent:
                messagebox.showerror("Update Check Failed",
                    f"Could not reach update server:\n{exe_url_or_err}",
                    parent=parent)
            return

        if self._version_tuple(latest) <= self._version_tuple(APP_VERSION):
            if not silent:
                messagebox.showinfo("Up to Date",
                    f"You're on the latest version ({APP_VERSION}).",
                    parent=parent)
            return

        # Update available
        if not messagebox.askyesno("Update Available",
                f"Version {latest} is available  (you have {APP_VERSION}).\n\n"
                f"Download and install now?",
                parent=parent):
            return

        UpdateProgressDialog(parent, latest, exe_url_or_err)

    @staticmethod
    def _version_tuple(v):
        try:
            return tuple(int(x) for x in str(v).split("."))
        except Exception:
            return (0,)


class UpdateProgressDialog(tk.Toplevel):
    """Downloads the new exe, replaces the running one, and restarts."""

    def __init__(self, parent, version, exe_url):
        super().__init__(parent)
        self.title(f"Updating to v{version}")
        self.configure(bg=T["bg"])
        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # block close during download

        self._version = version
        self._exe_url = exe_url

        tk.Label(self, text=f"Downloading v{version}…",
                 bg=T["bg"], fg=T["fg"], font=("Segoe UI", 10)
                 ).pack(padx=30, pady=(20, 8))

        self._bar = ttk.Progressbar(self, length=340, mode="determinate")
        self._bar.pack(padx=30, pady=4)

        self._status = tk.Label(self, text="Connecting…",
                                bg=T["bg"], fg=T["fg2"], font=("Segoe UI", 8))
        self._status.pack(padx=30, pady=(4, 20))

        self.update_idletasks()
        threading.Thread(target=self._download, daemon=True).start()

    def _set_status(self, text, pct=None):
        def _do():
            self._status.config(text=text)
            if pct is not None:
                self._bar["value"] = pct
        self.after(0, _do)

    def _download(self):
        import urllib.request
        tmp = os.path.join(CONFIG_DIR, "AeternusRenderer_update.exe")
        try:
            self._set_status("Downloading…", 0)
            with urllib.request.urlopen(self._exe_url, timeout=60) as r:
                total = int(r.headers.get("Content-Length", 0))
                downloaded = 0
                chunk = 65536
                with open(tmp, "wb") as f:
                    while True:
                        buf = r.read(chunk)
                        if not buf:
                            break
                        f.write(buf)
                        downloaded += len(buf)
                        if total:
                            pct = int(downloaded / total * 100)
                            self._set_status(
                                f"{downloaded // 1024} / {total // 1024} KB", pct)
            self._set_status("Installing…", 99)
            self.after(0, lambda: self._install(tmp))
        except Exception as e:
            self.after(0, lambda: self._fail(str(e), tmp))

    def _install(self, tmp):
        """
        Replace running exe with downloaded file.
        On Windows the running exe is locked, so we use a helper .bat that:
          1. Waits for this process to exit
          2. Copies the new exe over the old one
          3. Launches the new exe
          4. Deletes itself
        """
        current_exe = sys.executable if getattr(sys, "frozen", False) else None

        if current_exe and current_exe.endswith(".exe"):
            bat = os.path.join(CONFIG_DIR, "update_helper.bat")
            with open(bat, "w") as f:
                f.write(f"""@echo off
:wait
tasklist /FI "PID eq {os.getpid()}" 2>NUL | find /I "AeternusRenderer" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >NUL
    goto wait
)
copy /y "{tmp}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
""")
            subprocess.Popen(["cmd", "/c", bat],
                             creationflags=subprocess.CREATE_NO_WINDOW)
            self.after(500, lambda: os.kill(os.getpid(), 9))
        else:
            # Running from source — replace main.py and restart with Python
            target = os.path.abspath(__file__)
            shutil.copy2(target, target + ".bak")
            shutil.copy2(tmp, target)
            try: os.remove(tmp)
            except Exception: pass
            self._set_status("Restarting…", 100)
            self.after(800, lambda: os.execv(sys.executable,
                                             [sys.executable] + sys.argv))

    def _fail(self, err, tmp):
        try: os.remove(tmp)
        except Exception: pass
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        messagebox.showerror("Update Failed", f"Download error:\n{err}", parent=self)
        self.destroy()


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, d):
        self.id               = d.get("id", str(uuid.uuid4())[:8])
        self.blend_path       = d["blend_path"]
        self.prefix           = d.get("prefix", "")
        self.episode          = d.get("episode", "")
        self.sequence         = d.get("sequence", "")
        self.shot             = d.get("shot", "")
        self.shot_id          = d.get("shot_id", "")
        self.view_layer       = d["view_layer"]
        self.frame_start      = int(d.get("frame_start", 1))
        self.frame_end        = int(d.get("frame_end", 1))
        self.orig_frame_start = int(d.get("orig_frame_start", self.frame_start))
        self.orig_frame_end   = int(d.get("orig_frame_end",   self.frame_end))
        self.output_path      = d["output_path"]
        self.phase            = d.get("phase", "Phase 1")
        self.status           = d.get("status", STATUS_WAITING)
        self.progress         = int(d.get("progress", 0))
        self.added_at         = d.get("added_at", datetime.now().isoformat())
        self.queue_name       = d.get("queue_name", self.episode or "Default")
        self.camera_name      = d.get("camera_name", "")
        self.render_time      = d.get("render_time", None)
        self.frames_rendered  = int(d.get("frames_rendered", 0))
        self.retry_count      = int(d.get("retry_count", 0))
        self.auto_retry       = int(d.get("auto_retry", AUTO_RETRY_MAX))
        raw_sf = d.get("specific_frames", None)
        self.specific_frames: list[int] | None = (
            [int(f) for f in raw_sf] if raw_sf else None
        )

    def to_dict(self):
        return self.__dict__.copy()

    @property
    def label(self):
        return f"{self.prefix}  {self.shot_id}  [{self.view_layer}]"

    @property
    def total_frames(self):
        if self.specific_frames:
            return len(self.specific_frames)
        return max(1, self.frame_end - self.frame_start + 1)

    @property
    def frames_str(self):
        if self.specific_frames:
            return ", ".join(str(f) for f in self.specific_frames)
        if self.frame_start == self.frame_end:
            return f"#{self.frame_start}"
        return f"{self.frame_start} – {self.frame_end}"

    @property
    def is_single_frame(self):
        if self.specific_frames:
            return len(self.specific_frames) == 1
        return self.frame_start == self.frame_end

    @property
    def range_is_modified(self):
        if self.specific_frames:
            return True
        return (self.frame_start != self.orig_frame_start or
                self.frame_end   != self.orig_frame_end)

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class DataStore:
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.jobs: list[Job] = []
        self.blender_path = DEFAULT_BLENDER
        self.player_path  = DEFAULT_PLAYER
        self.auto_shutdown = False
        self.auto_retry    = True
        self.theme         = "Dark"
        self.auto_update   = True
        self.load()

    def load(self):
        if not os.path.exists(DATA_FILE):
            return
        try:
            with open(DATA_FILE) as f:
                raw = json.load(f)
            self.blender_path  = raw.get("blender_path",  DEFAULT_BLENDER)
            self.player_path   = raw.get("player_path",   DEFAULT_PLAYER)
            self.auto_shutdown = raw.get("auto_shutdown", False)
            self.auto_retry    = raw.get("auto_retry",    True)
            self.theme         = raw.get("theme",         "Dark")
            self.auto_update   = raw.get("auto_update",   True)
            self.jobs = [Job(j) for j in raw.get("jobs", [])]
            for j in self.jobs:
                if j.status == STATUS_RENDERING:
                    j.status = STATUS_WAITING
        except Exception as e:
            print(f"[Store] Load error: {e}")

    def save(self):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump({
                    "blender_path" : self.blender_path,
                    "player_path"  : self.player_path,
                    "auto_shutdown": self.auto_shutdown,
                    "auto_retry"   : self.auto_retry,
                    "theme"        : self.theme,
                    "auto_update"  : self.auto_update,
                    "jobs"         : [j.to_dict() for j in self.jobs],
                }, f, indent=2)
        except Exception as e:
            print(f"[Store] Save error: {e}")

    def add_jobs(self, jobs):
        self.jobs.extend(jobs)
        self.save()

    def get_queues(self):
        seen = []
        for j in self.jobs:
            if j.queue_name not in seen:
                seen.append(j.queue_name)
        return sorted(seen)

    def jobs_for_queue(self, q):
        return [j for j in self.jobs if j.queue_name == q]

    def job_by_id(self, jid):
        return next((j for j in self.jobs if j.id == jid), None)

    def remove_jobs(self, ids):
        self.jobs = [j for j in self.jobs if j.id not in ids]
        self.save()

    def clear_done(self, q):
        self.jobs = [j for j in self.jobs
                     if not (j.queue_name == q and j.status == STATUS_DONE)]
        self.save()

    def avg_render_time(self):
        samples = [j for j in self.jobs
                   if j.status == STATUS_DONE and j.render_time and j.total_frames > 0]
        if not samples:
            return None
        return sum(j.render_time / j.total_frames for j in samples) / len(samples)

    def queue_eta_seconds(self, queue_name):
        spf = self.avg_render_time()
        if spf is None:
            return None
        waiting = [j for j in self.jobs
                   if j.queue_name == queue_name and j.status == STATUS_WAITING]
        return sum(j.total_frames * spf for j in waiting)

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class Logger:
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._listeners = []

    def add_listener(self, fn):
        self._listeners.append(fn)

    def log(self, msg):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        try:
            with open(LOG_FILE, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass
        for fn in self._listeners:
            try: fn(line)
            except Exception: pass

logger = Logger()

# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class JobHandler(BaseHTTPRequestHandler):
    store    = None
    callback = None

    def do_POST(self):
        if self.path != "/add_jobs":
            self.send_response(404); self.end_headers(); return
        body    = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        payload = json.loads(body.decode())
        addon_ver = payload.get("addon_version", "unknown")
        if addon_ver != ADDON_VERSION:
            logger.log(f"WARNING: Addon v{addon_ver} != app expects v{ADDON_VERSION}.")
        new_jobs = []
        for rj in payload.get("jobs", []):
            rj["id"]              = str(uuid.uuid4())[:8]
            rj["added_at"]        = datetime.now().isoformat()
            rj["status"]          = STATUS_WAITING
            rj["progress"]        = 0
            rj["retry_count"]     = 0
            rj["frames_rendered"] = 0
            rj["queue_name"]      = rj.get("episode", "Default")
            rj["orig_frame_start"] = rj.get("frame_start", 1)
            rj["orig_frame_end"]   = rj.get("frame_end",   1)
            new_jobs.append(Job(rj))
        JobHandler.store.add_jobs(new_jobs)
        logger.log(f"Received {len(new_jobs)} job(s) from Blender (addon v{addon_ver}).")
        if JobHandler.callback:
            JobHandler.callback()
        resp = json.dumps({"added": len(new_jobs), "app_version": APP_VERSION}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *a): pass

def run_http(store, cb):
    JobHandler.store    = store
    JobHandler.callback = cb
    HTTPServer(("localhost", APP_PORT), JobHandler).serve_forever()

# ---------------------------------------------------------------------------
# Render engine
# ---------------------------------------------------------------------------

class RenderEngine:
    def __init__(self, store: DataStore, on_update, on_all_done):
        self.store        = store
        self.on_update    = on_update
        self.on_all_done  = on_all_done
        self.running      = False
        self.paused       = False
        self.current      = None
        self._proc        = None
        self._thread      = None
        self._start_time  = None
        self._priority_id = None

    def start(self):
        if self._thread and self._thread.is_alive():
            self.paused = False; return
        self.running = True
        self.paused  = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def render_now(self, job: Job):
        self._priority_id = job.id
        job.status = STATUS_WAITING; job.progress = 0
        self.store.save(); self.start()

    def stop(self):
        self.running = False; self._priority_id = None
        if self._proc:
            try: self._proc.terminate()
            except Exception: pass

    def pause(self):  self.paused = True
    def resume(self): self.paused = False

    def _next_job(self):
        if self._priority_id:
            job = self.store.job_by_id(self._priority_id)
            self._priority_id = None
            if job and job.status == STATUS_WAITING:
                return job
        return next((j for j in self.store.jobs if j.status == STATUS_WAITING), None)

    def _loop(self):
        while self.running:
            if self.paused:
                time.sleep(0.5); continue
            job = self._next_job()
            if not job:
                active = [j for j in self.store.jobs if j.status != STATUS_DISABLED]
                if active and all(j.status in (STATUS_DONE, STATUS_FAILED) for j in active):
                    logger.log("All jobs complete.")
                    self.on_all_done()
                time.sleep(1); continue

            self.current     = job
            job.status       = STATUS_RENDERING
            job.progress     = 0
            self._start_time = time.time()
            self.store.save(); self.on_update()
            logger.log(f"Started: {job.label}  {job.frames_str}")

            success = self._render(job)

            job.render_time  = int(time.time() - self._start_time)
            self.current     = None
            self._start_time = None

            if success:
                out_dir = os.path.dirname(job.output_path)
                prefix  = os.path.basename(job.output_path)
                written = any(
                    f.startswith(prefix) for f in os.listdir(out_dir)
                ) if os.path.isdir(out_dir) else False
                if not written:
                    logger.log(f"WARNING: {job.label} — no output files found. Marking Failed.")
                    success = False

            if success:
                job.status          = STATUS_DONE
                job.progress        = 100
                job.frames_rendered = job.total_frames
                logger.log(f"Done: {job.label}  {job.total_frames} frame(s)  ({job.render_time}s)")
            else:
                job.retry_count += 1
                if self.store.auto_retry and job.retry_count <= job.auto_retry:
                    logger.log(f"FAILED: {job.label}  retry {job.retry_count}/{job.auto_retry}")
                    job.status = STATUS_WAITING; job.progress = 0
                else:
                    job.status = STATUS_FAILED
                    logger.log(f"FAILED (no more retries): {job.label}  ({job.render_time}s)")

            self.store.save(); self.on_update()
        self.running = False

    def _render(self, job: Job):
        blender = self.store.blender_path
        if not os.path.exists(blender):
            logger.log(f"Blender not found: {blender}"); return False
        os.makedirs(os.path.dirname(job.output_path), exist_ok=True)
        expr = (
            f"import bpy; s=bpy.context.scene; "
            f"vl=s.view_layers.get('{job.view_layer}'); "
            f"bpy.context.window.view_layer = vl if vl else s.view_layers[0]; "
            f"s.render.filepath=r'{job.output_path}'; "
            f"s.frame_start={job.frame_start}; s.frame_end={job.frame_end}; "
            f"s.render.image_settings.file_format='TIFF'"
        )
        cmd = [blender, "--background", job.blend_path, "--python-expr", expr]
        if job.specific_frames:
            cmd += ["--render-frame", ",".join(str(f) for f in job.specific_frames)]
        elif job.is_single_frame:
            cmd += ["--render-frame", str(job.frame_start)]
        else:
            cmd += ["--render-anim"]
        total = job.total_frames
        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, creationflags=flags)
            done = 0
            for line in self._proc.stdout:
                line = line.strip()
                if line: logger.log(line)
                m = re.search(r"Fra:(\d+)", line)
                if m:
                    done         = int(m.group(1)) - job.frame_start + 1
                    job.progress = int(min(done / total * 100, 99))
                    self.on_update()
            self._proc.wait()
            return self._proc.returncode == 0
        except Exception as e:
            logger.log(f"Render error: {e}"); return False
        finally:
            self._proc = None

    def eta_str(self):
        if not self.current or not self._start_time: return ""
        job = self.current; elapsed = time.time() - self._start_time
        p = job.progress / 100.0
        if p <= 0: return "ETA: calculating…"
        return f"ETA: {str(timedelta(seconds=int((elapsed/p)-elapsed)))}"

    def queue_eta_str(self, queue_name):
        secs = self.store.queue_eta_seconds(queue_name)
        if secs is None: return ""
        if self.current and self._start_time and self.current.queue_name == queue_name:
            elapsed = time.time() - self._start_time
            p = self.current.progress / 100.0
            if p > 0: secs += max(0, (elapsed/p) - elapsed)
        return f"Queue ETA: {str(timedelta(seconds=int(secs)))}"

# ---------------------------------------------------------------------------
# Edit Job dialog  (frame start/end + specific frames + view layer + output)
# ---------------------------------------------------------------------------

class EditJobDialog(tk.Toplevel):
    def __init__(self, parent, jobs: list[Job], store: DataStore, on_done):
        super().__init__(parent)
        self.title("Edit Job(s)")
        self.configure(bg=T["bg"])
        self.resizable(False, False)
        self.grab_set()
        self._jobs = jobs; self._store = store; self._on_done = on_done
        multi = len(jobs) > 1
        pad = {"padx": 16, "pady": 6}

        def lbl(text, row):
            tk.Label(self, text=text, bg=T["bg"], fg=T["fg2"],
                     font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", **pad)

        def ent(var, row, w=50):
            e = tk.Entry(self, textvariable=var, bg=T["bg2"], fg=T["fg"],
                         insertbackground=T["fg"], font=("Segoe UI", 9), width=w)
            e.grid(row=row, column=1, **pad); return e

        j0 = jobs[0]
        self._start  = tk.StringVar(value="" if multi else str(j0.frame_start))
        self._end    = tk.StringVar(value="" if multi else str(j0.frame_end))
        self._frames = tk.StringVar(value="" if multi else (
            ", ".join(str(f) for f in j0.specific_frames) if j0.specific_frames else ""))
        self._vl     = tk.StringVar(value="" if multi else j0.view_layer)
        self._out    = tk.StringVar(value="" if multi else j0.output_path)

        lbl("Frame Start:", 0);  ent(self._start, 0, 12)
        lbl("Frame End:",   1);  ent(self._end,   1, 12)
        lbl("Specific Frames:", 2)
        ent(self._frames, 2, 30)
        tk.Label(self, text="e.g. 1,5,25,60  — overrides start/end when set",
                 bg=T["bg"], fg=T["fg3"], font=("Segoe UI", 7)
                 ).grid(row=3, column=1, sticky="w", padx=16)
        lbl("View Layer:",  4);  ent(self._vl,    4)
        lbl("Output Path:", 5)
        out_frame = tk.Frame(self, bg=T["bg"])
        out_frame.grid(row=5, column=1, **pad)
        tk.Entry(out_frame, textvariable=self._out, bg=T["bg2"], fg=T["fg"],
                 insertbackground=T["fg"], font=("Segoe UI", 9), width=44).pack(side="left")
        tk.Button(out_frame, text="…", bg=T["bg3"], fg=T["fg"],
                  relief="flat", command=self._browse).pack(side="left", padx=4)

        if multi:
            tk.Label(self, text=f"Editing {len(jobs)} jobs. Leave blank to keep existing.",
                     bg=T["bg"], fg=T["fg3"], font=("Segoe UI", 8)
                     ).grid(row=6, column=0, columnspan=2, pady=(0,4))

        # Restore original range button
        if any(j.range_is_modified for j in jobs):
            tk.Button(self, text="↩  Restore Original Range",
                      bg=T["bg2"], fg=T["accent"],
                      font=("Segoe UI", 9), relief="flat", padx=12,
                      command=self._restore).grid(row=7, column=0, columnspan=2, pady=(4,0))

        btn_frame = tk.Frame(self, bg=T["bg"])
        btn_frame.grid(row=8, column=0, columnspan=2, pady=12)
        tk.Button(btn_frame, text="Apply", bg="#2a4a6e", fg="#ddd",
                  font=("Segoe UI", 9), relief="flat", padx=20,
                  command=self._apply).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Cancel", bg=T["bg3"], fg=T["fg2"],
                  font=("Segoe UI", 9), relief="flat", padx=12,
                  command=self.destroy).pack(side="left")

    def _browse(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path: self._out.set(path.replace("/", "\\") + "\\")

    def _parse_specific(self, text):
        try:
            frames = sorted(set(int(p.strip()) for p in text.split(",") if p.strip()))
            return frames if frames else None
        except ValueError:
            return None

    def _apply(self):
        sf_text = self._frames.get().strip()
        sf = self._parse_specific(sf_text) if sf_text else None
        for job in self._jobs:
            if sf is not None:
                job.specific_frames = sf
            elif sf_text == "":
                pass  # blank = keep existing
            else:
                job.specific_frames = None
            if self._start.get():
                try: job.frame_start = int(self._start.get())
                except ValueError: pass
            if self._end.get():
                try: job.frame_end = int(self._end.get())
                except ValueError: pass
            if self._vl.get():
                job.view_layer = self._vl.get().strip()
            if self._out.get():
                job.output_path = self._out.get().strip()
            if job.status not in (STATUS_RENDERING, STATUS_DONE):
                job.status = STATUS_WAITING; job.progress = 0
        self._store.save(); self._on_done(); self.destroy()

    def _restore(self):
        for job in self._jobs:
            job.frame_start = job.orig_frame_start
            job.frame_end   = job.orig_frame_end
            job.specific_frames = None
            if job.status not in (STATUS_RENDERING, STATUS_DONE):
                job.status = STATUS_WAITING; job.progress = 0
        self._store.save(); self._on_done(); self.destroy()

# ---------------------------------------------------------------------------
# Set Frame dialog  (quick presets + comma list; separate from Edit for speed)
# ---------------------------------------------------------------------------

class SetFrameDialog(tk.Toplevel):
    FIXED_PRESETS = [1, 5, 10, 25, 50, 60, 100]

    def __init__(self, parent, jobs: list[Job], store: DataStore, on_done):
        super().__init__(parent)
        self.title("Set Frame(s)")
        self.configure(bg=T["bg"])
        self.resizable(False, False)
        self.grab_set()
        self._jobs = jobs; self._store = store; self._on_done = on_done

        tk.Label(self, text=f"Set frame(s) for {len(jobs)} job(s)",
                 bg=T["bg"], fg=T["fg2"], font=("Segoe UI", 9)
                 ).pack(padx=20, pady=(16, 4))

        if len(jobs) == 1:
            j = jobs[0]
            cur = f"Current: {j.frames_str}"
            if j.range_is_modified:
                cur += f"   (original: {j.orig_frame_start} – {j.orig_frame_end})"
            tk.Label(self, text=cur, bg=T["bg"], fg=T["fg3"],
                     font=("Segoe UI", 8)).pack(padx=20, pady=(0,2))

        tk.Label(self,
                 text="Single frame: modify in place.  Multiple frames: stored as frame list on this job.",
                 bg=T["bg"], fg=T["fg3"], font=("Segoe UI", 7)).pack(padx=20, pady=(0,8))

        presets = list(self.FIXED_PRESETS)
        for j in jobs:
            for f in (j.orig_frame_end, j.frame_end):
                if f not in presets: presets.append(f)
        presets = sorted(set(presets))

        btn_frame = tk.Frame(self, bg=T["bg"])
        btn_frame.pack(padx=20, pady=(4,8))
        tk.Label(btn_frame, text="Quick:", bg=T["bg"], fg=T["fg3"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(0,8))
        for f in presets:
            tk.Button(btn_frame, text=str(f), width=4,
                      bg=T["bg3"], fg=T["accent"],
                      font=("Segoe UI", 9, "bold"), relief="flat",
                      command=lambda n=f: self._apply([n])).pack(side="left", padx=2)

        tk.Frame(self, bg=T["sep"], height=1).pack(fill="x", padx=20, pady=4)

        row = tk.Frame(self, bg=T["bg"])
        row.pack(padx=20, pady=(6,4))
        tk.Label(row, text="Frame(s):", bg=T["bg"], fg=T["fg2"],
                 font=("Segoe UI", 9)).pack(side="left", padx=(0,8))
        self._custom = tk.StringVar()
        entry = tk.Entry(row, textvariable=self._custom, bg=T["bg2"], fg=T["fg"],
                         insertbackground=T["fg"], font=("Segoe UI", 9), width=20)
        entry.pack(side="left", padx=(0,6))
        entry.bind("<Return>", lambda e: self._apply_custom())
        tk.Button(row, text="Set", bg="#2a4a6e", fg="#ddd",
                  font=("Segoe UI", 9), relief="flat", padx=10,
                  command=self._apply_custom).pack(side="left")

        tk.Label(self, text="e.g.  5   or   1, 5, 25, 60",
                 bg=T["bg"], fg=T["fg3"], font=("Segoe UI", 7)).pack(pady=(0,4))

        self._err = tk.Label(self, text="", bg=T["bg"], fg="#e37e7e", font=("Segoe UI", 8))
        self._err.pack()

        tk.Frame(self, bg=T["sep"], height=1).pack(fill="x", padx=20, pady=4)

        if any(j.range_is_modified for j in jobs):
            tk.Button(self, text="↩  Restore Original Range",
                      bg=T["bg2"], fg=T["accent"],
                      font=("Segoe UI", 9), relief="flat", padx=12,
                      command=self._restore).pack(pady=(6,2))

        tk.Button(self, text="Cancel", bg=T["bg3"], fg=T["fg2"],
                  font=("Segoe UI", 9), relief="flat", padx=12,
                  command=self.destroy).pack(pady=(4,14))
        entry.focus_set()

    def _parse(self, text):
        try:
            frames = sorted(set(int(p.strip()) for p in text.split(",") if p.strip()))
            return frames if frames else None
        except ValueError:
            return None

    def _apply(self, frames):
        if len(frames) == 1:
            f = frames[0]
            for job in self._jobs:
                job.frame_start = f; job.frame_end = f; job.specific_frames = None
                if job.status not in (STATUS_RENDERING, STATUS_DONE):
                    job.status = STATUS_WAITING; job.progress = 0
        else:
            for job in self._jobs:
                job.specific_frames = frames
                if job.status not in (STATUS_RENDERING, STATUS_DONE):
                    job.status = STATUS_WAITING; job.progress = 0
        self._store.save(); self._on_done(); self.destroy()

    def _apply_custom(self):
        frames = self._parse(self._custom.get())
        if frames is None:
            self._err.config(text="Invalid. Use a number or comma-separated numbers."); return
        self._err.config(text=""); self._apply(frames)

    def _restore(self):
        for job in self._jobs:
            job.frame_start = job.orig_frame_start; job.frame_end = job.orig_frame_end
            job.specific_frames = None
            if job.status not in (STATUS_RENDERING, STATUS_DONE):
                job.status = STATUS_WAITING; job.progress = 0
        self._store.save(); self._on_done(); self.destroy()

# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, store: DataStore, on_done):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=T["bg"])
        self.resizable(False, False)
        self.grab_set()
        self._store = store; self._on_done = on_done
        pad = {"padx": 16, "pady": 8}

        def lbl(text, row):
            tk.Label(self, text=text, bg=T["bg"], fg=T["fg2"],
                     font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", **pad)

        def path_row(var, row, title, filetypes):
            f = tk.Frame(self, bg=T["bg"])
            f.grid(row=row, column=1, **pad)
            tk.Entry(f, textvariable=var, bg=T["bg2"], fg=T["fg"],
                     insertbackground=T["fg"], font=("Segoe UI", 9), width=46).pack(side="left")
            tk.Button(f, text="…", bg=T["bg3"], fg=T["fg"], relief="flat",
                      command=lambda: self._browse(var, title, filetypes)
                      ).pack(side="left", padx=4)

        self._blender = tk.StringVar(value=store.blender_path)
        self._player  = tk.StringVar(value=store.player_path)
        self._theme   = tk.StringVar(value=store.theme)

        lbl("Blender Executable:", 0)
        path_row(self._blender, 0, "Select Blender", [("blender.exe","blender.exe"),("All","*.*")])

        lbl("Media Player:", 1)
        path_row(self._player, 1, "Select Media Player", [("Executable","*.exe"),("All","*.*")])
        tk.Label(self, text="Used for Ctrl+Space / View Render Folder",
                 bg=T["bg"], fg=T["fg3"], font=("Segoe UI", 7)
                 ).grid(row=2, column=1, sticky="w", padx=16)

        lbl("Theme:", 3)
        theme_frame = tk.Frame(self, bg=T["bg"])
        theme_frame.grid(row=3, column=1, **pad)
        for name in THEMES:
            tk.Radiobutton(theme_frame, text=name, variable=self._theme, value=name,
                           bg=T["bg"], fg=T["fg"], selectcolor=T["bg2"],
                           activebackground=T["bg"], activeforeground=T["fg"],
                           font=("Segoe UI", 9)
                           ).pack(side="left", padx=8)

        tk.Label(self, text="Theme change takes effect after restart.",
                 bg=T["bg"], fg=T["fg3"], font=("Segoe UI", 7)
                 ).grid(row=4, column=1, sticky="w", padx=16)

        # ---- Update section ------------------------------------------------
        tk.Frame(self, bg=T["sep"], height=1).grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=8)

        lbl("Updates:", 6)
        upd_frame = tk.Frame(self, bg=T["bg"])
        upd_frame.grid(row=6, column=1, **pad)

        self._auto_update = tk.BooleanVar(value=store.auto_update)
        tk.Checkbutton(upd_frame, text="Check automatically on launch",
                       variable=self._auto_update,
                       bg=T["bg"], fg=T["fg"], selectcolor=T["bg2"],
                       activebackground=T["bg"], activeforeground=T["fg"],
                       font=("Segoe UI", 9)).pack(side="left", padx=(0, 16))

        tk.Button(upd_frame, text="Check Now",
                  bg=T["bg3"], fg=T["fg"],
                  font=("Segoe UI", 9), relief="flat", padx=12,
                  command=self._check_now).pack(side="left")

        tk.Label(self,
                 text=f"Current version: {APP_VERSION}  •  Updates from github.com/Argobelz/AeternusRenderer",
                 bg=T["bg"], fg=T["fg3"], font=("Segoe UI", 7)
                 ).grid(row=7, column=1, sticky="w", padx=16)

        btn_frame = tk.Frame(self, bg=T["bg"])
        btn_frame.grid(row=8, column=0, columnspan=2, pady=12)
        tk.Button(btn_frame, text="Save", bg="#2a4a6e", fg="#ddd",
                  font=("Segoe UI", 9), relief="flat", padx=20,
                  command=self._save).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Cancel", bg=T["bg3"], fg=T["fg2"],
                  font=("Segoe UI", 9), relief="flat", padx=12,
                  command=self.destroy).pack(side="left")

    def _browse(self, var, title, filetypes):
        path = filedialog.askopenfilename(title=title, filetypes=filetypes)
        if path: var.set(path)

    def _save(self):
        self._store.blender_path = self._blender.get()
        self._store.player_path  = self._player.get()
        self._store.theme        = self._theme.get()
        self._store.auto_update  = self._auto_update.get()
        self._store.save()
        apply_theme(self._theme.get())
        self._on_done(); self.destroy()

    def _check_now(self):
        Updater(self._store).check_and_prompt(self, silent=False)

    def _update_app(self):
        """Replace main.py with a new version and restart."""
        path = filedialog.askopenfilename(
            title="Select new main.py",
            filetypes=[("Python file","*.py"),("All","*.*")])
        if not path: return
        target = os.path.abspath(__file__)
        backup = target + ".bak"
        try:
            shutil.copy2(target, backup)
            shutil.copy2(path, target)
        except Exception as e:
            messagebox.showerror("Update Failed", str(e)); return
        if messagebox.askyesno("Update Installed",
                "main.py replaced successfully.\nRestart now?"):
            python = sys.executable
            os.execv(python, [python] + sys.argv)

# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.store = DataStore()
        apply_theme(self.store.theme)

        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("1400x780")
        self.minsize(1000, 550)
        self.configure(bg=T["bg"])

        self.engine = RenderEngine(self.store, self._schedule_refresh, self._on_all_done)

        self._active_queue  = None
        self._log_visible   = False
        self._auto_shutdown = tk.BooleanVar(value=self.store.auto_shutdown)
        self._auto_retry    = tk.BooleanVar(value=self.store.auto_retry)
        self._collapsed     : set[str] = set()

        self._build_ui()
        self._start_services()
        self._tick()
        self._refresh()

        # Auto-update check — runs 3 seconds after launch to not block startup
        if self.store.auto_update:
            self.after(3000, lambda: Updater(self.store).check_and_prompt(self, silent=True))

    # ---------------------------------------------------------------- UI

    def _build_ui(self):
        top = tk.Frame(self, bg=T["bg4"], height=48)
        top.pack(fill="x", side="top"); top.pack_propagate(False)

        tk.Label(top, text=f"  {APP_TITLE}", bg=T["bg4"], fg=T["fg"],
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=8, pady=10)
        tk.Label(top, text=f"v{APP_VERSION}", bg=T["bg4"], fg=T["fg3"],
                 font=("Segoe UI", 9)).pack(side="left", pady=10)

        tk.Button(top, text="⚙  Settings", bg=T["bg2"], fg=T["fg2"],
                  font=("Segoe UI", 9), relief="flat", padx=10,
                  command=self._open_settings).pack(side="right", padx=6, pady=10)

        tk.Checkbutton(top, text="⏻  Auto Shutdown", variable=self._auto_shutdown,
                       bg=T["bg4"], fg=T["fg2"], selectcolor=T["bg4"],
                       activebackground=T["bg4"], activeforeground=T["fg"],
                       font=("Segoe UI", 9),
                       command=self._toggle_shutdown).pack(side="right", padx=6, pady=10)

        tk.Checkbutton(top, text="↺  Auto Retry", variable=self._auto_retry,
                       bg=T["bg4"], fg=T["fg2"], selectcolor=T["bg4"],
                       activebackground=T["bg4"], activeforeground=T["fg"],
                       font=("Segoe UI", 9),
                       command=self._toggle_auto_retry).pack(side="right", padx=6, pady=10)

        tk.Button(top, text="📋  Logs", bg=T["bg2"], fg=T["fg2"],
                  font=("Segoe UI", 9), relief="flat", padx=10,
                  command=self._toggle_log).pack(side="right", padx=6, pady=10)

        self._status_label = tk.Label(top, text="●  Stopped", bg=T["bg4"], fg=T["fg3"],
                                      font=("Segoe UI", 9))
        self._status_label.pack(side="right", padx=16, pady=10)

        self._eta_label = tk.Label(top, text="", bg=T["bg4"], fg=T["accent"],
                                   font=("Segoe UI", 9))
        self._eta_label.pack(side="right", padx=8, pady=10)

        self._main_pane = tk.PanedWindow(self, orient="horizontal",
                                          bg=T["bg"], sashwidth=4, sashrelief="flat")
        self._main_pane.pack(fill="both", expand=True)

        sidebar = tk.Frame(self._main_pane, bg=T["bg3"], width=180)
        self._main_pane.add(sidebar, minsize=140)

        tk.Label(sidebar, text="QUEUES", bg=T["bg3"], fg=T["fg3"],
                 font=("Segoe UI", 8, "bold"), anchor="w", padx=12
                 ).pack(fill="x", pady=(14,4))

        self._queue_frame = tk.Frame(sidebar, bg=T["bg3"])
        self._queue_frame.pack(fill="both", expand=True)

        right = tk.Frame(self._main_pane, bg=T["bg"])
        self._main_pane.add(right, minsize=700)

        # Toolbar
        tb = tk.Frame(right, bg=T["bg2"], height=42)
        tb.pack(fill="x"); tb.pack_propagate(False)

        self._queue_label = tk.Label(tb, text="Select a queue", bg=T["bg2"],
                                      fg=T["fg2"], font=("Segoe UI", 10, "bold"),
                                      anchor="w", padx=12)
        self._queue_label.pack(side="left", fill="y")

        self._queue_eta_label = tk.Label(tb, text="", bg=T["bg2"],
                                          fg=T["fg3"], font=("Segoe UI", 8))
        self._queue_eta_label.pack(side="left", padx=8)

        for txt, cmd, bg in [
            ("▶  Start", self._start, "#1a4a1a"),
            ("⏸  Pause", self._pause, "#3a3a0a"),
            ("⏹  Stop",  self._stop,  "#4a1a1a"),
        ]:
            tk.Button(tb, text=txt, bg=bg, fg="#ddd", font=("Segoe UI", 9),
                      relief="flat", padx=12, command=cmd
                      ).pack(side="right", padx=4, pady=6)

        tk.Button(tb, text="⊞ Expand All", bg=T["bg3"], fg=T["fg2"],
                  font=("Segoe UI", 8), relief="flat", padx=8,
                  command=self._expand_all).pack(side="right", padx=2, pady=6)
        tk.Button(tb, text="⊟ Collapse All", bg=T["bg3"], fg=T["fg2"],
                  font=("Segoe UI", 8), relief="flat", padx=8,
                  command=self._collapse_all).pack(side="right", padx=2, pady=6)

        tk.Button(tb, text="🗑  Clear Done", bg=T["bg3"], fg=T["fg2"],
                  font=("Segoe UI", 9), relief="flat", padx=10,
                  command=self._clear_done).pack(side="right", padx=4, pady=6)

        # Treeview
        cols = ("layer", "shot", "frames", "output", "status", "pct", "total_frames")
        self._tree = ttk.Treeview(right, columns=cols, show="tree headings",
                                   selectmode="extended")
        self._tree.column("#0", width=0, minwidth=0, stretch=False)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=T["bg"], foreground=T["fg"],
                         fieldbackground=T["bg"], rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=T["bg2"],
                         foreground=T["fg2"], font=("Segoe UI", 8, "bold"))
        style.map("Treeview", background=[("selected", T["sel"])])

        for col, hdr, w, anchor in [
            ("layer",        "Layer",    80,  "center"),
            ("shot",         "Shot",     180, "w"),
            ("frames",       "Frames",   120, "center"),
            ("output",       "Output",   340, "w"),
            ("status",       "Status",   88,  "center"),
            ("pct",          "%",        55,  "center"),
            ("total_frames", "Frame(s)", 70,  "center"),
        ]:
            self._tree.heading(col, text=hdr)
            self._tree.column(col, width=w, anchor=anchor, minwidth=30)

        vsb = ttk.Scrollbar(right, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(fill="both", expand=True, side="left")
        vsb.pack(fill="y", side="right")

        # Detail bar
        detail = tk.Frame(self, bg=T["bg5"], height=32)
        detail.pack(fill="x", side="bottom"); detail.pack_propagate(False)
        self._detail = tk.Label(detail, text="", bg=T["bg5"], fg=T["fg3"],
                                font=("Segoe UI", 8), anchor="w", padx=12)
        self._detail.pack(fill="both", expand=True)

        # Log panel
        self._log_frame = tk.Frame(self, bg="#0d0d0d", height=160)
        self._log_text  = tk.Text(self._log_frame, bg="#0d0d0d", fg="#7ec87e",
                                   font=("Consolas", 8), state="disabled",
                                   wrap="word", relief="flat")
        log_vsb = ttk.Scrollbar(self._log_frame, orient="vertical",
                                 command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_vsb.set)
        self._log_text.pack(fill="both", expand=True, side="left")
        log_vsb.pack(fill="y", side="right")
        logger.add_listener(self._append_log)

        # Context menu with shortcuts shown
        self._ctx = tk.Menu(self, tearoff=0, bg=T["bg2"], fg=T["fg"],
                            activebackground=T["bg3"])
        for lbl, cmd, accel in [
            ("Render Now (skip queue)",  self._ctx_render_now,        ""),
            ("Render Selected",          self._ctx_render_selected,   ""),
            ("---", None, ""),
            ("Edit Job(s)",              self._ctx_edit,              "Ctrl+E"),
            ("Set Frame…",               self._ctx_set_frame,         "Ctrl+F"),
            ("Duplicate",                self._ctx_duplicate,         ""),
            ("Duplicate as Frame…",      self._ctx_duplicate_as_frame,""),
            ("---", None, ""),
            ("Disable / Enable",         self._ctx_toggle_disable,    "M"),
            ("Re-render (reset)",        self._ctx_rerender,          "Ctrl+R"),
            ("---", None, ""),
            ("Open Blend",               self._ctx_open_blend,        ""),
            ("Browse Blend Path",        self._ctx_browse_blend,      ""),
            ("Browse Output Path",       self._ctx_browse_output,     ""),
            ("View Render (player)",     self._ctx_view_render,       "Ctrl+Space"),
            ("---", None, ""),
            ("Select All",               self._kb_select_all,         "Ctrl+A"),
            ("Collapse All",             self._collapse_all,          ""),
            ("Expand All",               self._expand_all,            ""),
            ("---", None, ""),
            ("Logs",                     self._ctx_logs,              ""),
            ("---", None, ""),
            ("Remove Job(s)",            self._ctx_remove,            "Del"),
        ]:
            if lbl == "---":
                self._ctx.add_separator()
            else:
                self._ctx.add_command(label=lbl, command=cmd,
                                      accelerator=accel if accel else "")

        # Bindings
        self._tree.bind("<Double-1>",         self._on_double_click)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Button-3>",         self._show_ctx)
        self._tree.bind("<Control-a>",        lambda e: self._kb_select_all())
        self._tree.bind("<Control-A>",        lambda e: self._kb_select_all())
        self._tree.bind("<Control-e>",        lambda e: self._ctx_edit())
        self._tree.bind("<Control-E>",        lambda e: self._ctx_edit())
        self._tree.bind("<Control-f>",        lambda e: self._ctx_set_frame())
        self._tree.bind("<Control-F>",        lambda e: self._ctx_set_frame())
        self._tree.bind("<Delete>",           lambda e: self._ctx_remove())
        self._tree.bind("<Control-space>",    lambda e: self._kb_view_render())
        self._tree.bind("<m>",                lambda e: self._ctx_toggle_disable())
        self._tree.bind("<M>",                lambda e: self._ctx_toggle_disable())
        self._tree.bind("<Control-r>",        lambda e: self._ctx_rerender())
        self._tree.bind("<Control-R>",        lambda e: self._ctx_rerender())
        self._tree.bind("<space>",            lambda e: self._toggle_group_focus())
        self._tree.bind("<<TreeviewOpen>>",   lambda e: self._on_group_open())
        self._tree.bind("<<TreeviewClose>>",  lambda e: self._on_group_close())

    # ---------------------------------------------------------------- Services

    def _start_services(self):
        threading.Thread(target=run_http, args=(self.store, self._schedule_refresh),
                         daemon=True).start()

    def _schedule_refresh(self):
        self.after(0, self._refresh)

    def _tick(self):
        if self.engine.running and self.engine.current:
            self._refresh_status(); self._refresh_jobs()
        self.after(1000, self._tick)

    # ---------------------------------------------------------------- Refresh

    def _refresh(self):
        self._refresh_queues(); self._refresh_jobs(); self._refresh_status()

    def _refresh_queues(self):
        for w in self._queue_frame.winfo_children():
            w.destroy()
        queues = self.store.get_queues()
        if not queues:
            tk.Label(self._queue_frame, text="No queues.\nSend jobs\nfrom Blender.",
                     bg=T["bg3"], fg=T["fg3"], font=("Segoe UI", 8),
                     justify="left", padx=12, pady=8).pack(anchor="w")
            return
        if self._active_queue not in queues:
            self._active_queue = queues[0]
        for q in queues:
            jobs  = self.store.jobs_for_queue(q)
            done  = sum(1 for j in jobs if j.status == STATUS_DONE)
            total = len(jobs)
            act   = q == self._active_queue
            tk.Button(self._queue_frame,
                      text=f"  {q}\n  {done}/{total} done",
                      bg=T["sel"] if act else T["bg3"],
                      fg=T["accent"] if act else T["fg3"],
                      font=("Segoe UI", 8), relief="flat",
                      anchor="w", justify="left", padx=4, pady=5,
                      command=lambda n=q: self._select_queue(n)
                      ).pack(fill="x", pady=1)

    def _refresh_jobs(self):
        sel = set(self._tree.selection())
        for item in self._tree.get_children():
            self._tree.delete(item)
        if not self._active_queue:
            return

        jobs = self.store.jobs_for_queue(self._active_queue)

        self._tree.tag_configure("group_sq", background=T["grp_sq"][0], foreground=T["grp_sq"][1])
        self._tree.tag_configure("group_sh", background=T["grp_sh"][0], foreground=T["grp_sh"][1])

        def insert_job(parent, job):
            prog = (f"{job.progress}%" if job.status == STATUS_RENDERING
                    else "✓" if job.status == STATUS_DONE
                    else f"✗({job.retry_count})" if job.status == STATUS_FAILED
                    else "")
            frames_display = ("~" + job.frames_str if job.range_is_modified else job.frames_str)
            out     = ("…" + job.output_path[-44:] if len(job.output_path) > 47 else job.output_path)
            total_f = str(job.frames_rendered) if job.status == STATUS_DONE else str(job.total_frames)
            tag     = job.status
            bg_key  = f"row_{tag.lower()}"
            self._tree.tag_configure(tag,
                background=T[bg_key][0] if bg_key in T else T["row_wait"][0],
                foreground=T[bg_key][1] if bg_key in T else T["row_wait"][1])
            iid = self._tree.insert(parent, "end", iid=job.id, tags=(tag,),
                                    values=(job.view_layer, job.shot_id,
                                            frames_display, out,
                                            job.status, prog, total_f))
            if job.id in sel:
                self._tree.selection_add(iid)

        # Single job — skip all group rows entirely
        if len(jobs) == 1:
            insert_job("", jobs[0])
        else:
            sq_groups: dict[str, dict[str, list[Job]]] = defaultdict(lambda: defaultdict(list))
            for job in jobs:
                sq_groups[job.sequence][job.shot].append(job)

            for sq in sorted(sq_groups):
                sh_map  = sq_groups[sq]
                sq_jobs = [j for sh in sh_map.values() for j in sh]

                # Single job under this SQ — skip both SQ and SH rows
                if len(sq_jobs) == 1:
                    insert_job("", sq_jobs[0])
                    continue

                sq_done          = sum(1 for j in sq_jobs if j.status == STATUS_DONE)
                sq_fail          = sum(1 for j in sq_jobs if j.status == STATUS_FAILED)
                sq_rend          = sum(1 for j in sq_jobs if j.status == STATUS_RENDERING)
                sq_frames_total  = sum(j.total_frames for j in sq_jobs)
                sq_frames_done   = sum(j.frames_rendered for j in sq_jobs if j.status == STATUS_DONE)
                sq_pct           = f"{sq_rend}▶" if sq_rend else (f"{sq_fail}✗" if sq_fail else "")
                sq_iid           = f"__sq__{sq}"
                self._tree.insert("", "end", iid=sq_iid, tags=("group_sq",),
                                  values=(sq, "", "", "",
                                          f"{sq_done}/{len(sq_jobs)}",
                                          sq_pct,
                                          f"{sq_frames_done}/{sq_frames_total}"))
                if sq_iid not in self._collapsed:
                    self._tree.item(sq_iid, open=True)

                for sh in sorted(sh_map):
                    sh_jobs = sh_map[sh]

                    # Single job under this SH — skip SH row, insert directly under SQ
                    if len(sh_jobs) == 1:
                        insert_job(sq_iid, sh_jobs[0])
                        continue

                    sh_done         = sum(1 for j in sh_jobs if j.status == STATUS_DONE)
                    sh_fail         = sum(1 for j in sh_jobs if j.status == STATUS_FAILED)
                    sh_rend         = sum(1 for j in sh_jobs if j.status == STATUS_RENDERING)
                    sh_frames_total = sum(j.total_frames for j in sh_jobs)
                    sh_frames_done  = sum(j.frames_rendered for j in sh_jobs if j.status == STATUS_DONE)
                    sh_pct          = f"{sh_rend}▶" if sh_rend else (f"{sh_fail}✗" if sh_fail else "")
                    sh_iid          = f"__sh__{sq}__{sh}"
                    self._tree.insert(sq_iid, "end", iid=sh_iid, tags=("group_sh",),
                                      values=("", sh, "", "",
                                              f"{sh_done}/{len(sh_jobs)}",
                                              sh_pct,
                                              f"{sh_frames_done}/{sh_frames_total}"))
                    if sh_iid not in self._collapsed:
                        self._tree.item(sh_iid, open=True)

                    for job in sh_jobs:
                        insert_job(sh_iid, job)

        failed         = sum(1 for j in jobs if j.status == STATUS_FAILED)
        total_rendered = sum(j.frames_rendered for j in jobs)
        label          = f"  {self._active_queue}  —  {len(jobs)} job(s)"
        if failed:         label += f"  •  {failed} failed"
        if total_rendered: label += f"  •  {total_rendered} frames rendered"
        self._queue_label.config(text=label)
        self._queue_eta_label.config(text=self.engine.queue_eta_str(self._active_queue))

    def _refresh_status(self):
        e = self.engine
        if e.running and not e.paused:
            cur = e.current
            if cur:
                retry = f"  retry {cur.retry_count}/{cur.auto_retry}" if cur.retry_count else ""
                self._status_label.config(
                    text=f"●  Rendering: {cur.label}  {cur.progress}%{retry}", fg=T["accent"])
                self._eta_label.config(text=e.eta_str())
            else:
                self._status_label.config(text="●  Running", fg=T["accent2"])
                self._eta_label.config(text="")
        elif e.paused:
            self._status_label.config(text="●  Paused", fg="#e3c87e")
            self._eta_label.config(text="")
        else:
            self._status_label.config(text="●  Stopped", fg=T["fg3"])
            self._eta_label.config(text="")

    # ---------------------------------------------------------------- Log

    def _append_log(self, line):
        def _do():
            self._log_text.config(state="normal")
            self._log_text.insert("end", line + "\n")
            self._log_text.see("end")
            self._log_text.config(state="disabled")
        self.after(0, _do)

    def _toggle_log(self):
        if self._log_visible:
            self._log_frame.pack_forget(); self._log_visible = False
        else:
            self._log_frame.pack(fill="x", side="bottom", before=self._main_pane)
            self._log_visible = True

    # ---------------------------------------------------------------- Actions

    def _select_queue(self, name):
        self._active_queue = name; self._refresh()

    def _start(self):  self.engine.start();  self._refresh_status()
    def _pause(self):
        if self.engine.paused: self.engine.resume()
        else: self.engine.pause()
        self._refresh_status()
    def _stop(self): self.engine.stop(); self._refresh_status()

    def _clear_done(self):
        if self._active_queue:
            self.store.clear_done(self._active_queue); self._refresh()

    def _toggle_shutdown(self):
        self.store.auto_shutdown = self._auto_shutdown.get(); self.store.save()

    def _toggle_auto_retry(self):
        self.store.auto_retry = self._auto_retry.get(); self.store.save()

    def _open_settings(self):
        SettingsDialog(self, self.store, self._refresh)

    def _on_all_done(self):
        if self.store.auto_shutdown:
            logger.log("Auto-shutdown in 60s.")
            self.after(0, lambda: messagebox.showinfo(
                "Render Complete", "All jobs done. Shutting down in 60 seconds."))
            threading.Thread(
                target=lambda: (time.sleep(60), os.system("shutdown /s /t 0")),
                daemon=True).start()
        else:
            self.after(0, self._schedule_refresh)

    def _on_select(self, event):
        sel = self._tree.selection()
        if not sel: return
        job = self.store.job_by_id(sel[-1])
        if job:
            orig  = f"  (orig: {job.orig_frame_start}–{job.orig_frame_end})" if job.range_is_modified else ""
            retry = f"  retries: {job.retry_count}/{job.auto_retry}" if job.retry_count else ""
            self._detail.config(
                text=f"Blend: {job.blend_path}    Layer: {job.view_layer}    "
                     f"Frames: {job.frame_start}–{job.frame_end}{orig}    "
                     f"Output: {job.output_path}{retry}")

    def _on_double_click(self, event):
        sel = self._tree.selection()
        if not sel: return
        jobs = [self.store.job_by_id(j) for j in sel if self.store.job_by_id(j)]
        if jobs: EditJobDialog(self, jobs, self.store, self._refresh)

    def _show_ctx(self, event):
        item = self._tree.identify_row(event.y)
        if not item: return
        if item not in self._tree.selection():
            self._tree.selection_set(item)
        self._ctx.post(event.x_root, event.y_root)

    def _selected_jobs(self):
        return [j for jid in self._tree.selection()
                if not jid.startswith("__")
                if (j := self.store.job_by_id(jid))]

    # ---------------------------------------------------------------- Keyboard

    def _kb_select_all(self):
        iids = []
        for status in (STATUS_WAITING, STATUS_DONE, STATUS_FAILED,
                       STATUS_DISABLED, STATUS_RENDERING):
            iids += [i for i in self._tree.tag_has(status) if not i.startswith("__")]
        if iids: self._tree.selection_set(iids)

    def _kb_view_render(self):
        player = self.store.player_path
        jobs   = self._selected_jobs()
        if not jobs: return
        paths = []
        for job in jobs:
            folder = os.path.dirname(job.output_path)
            if os.path.isdir(folder) and folder not in paths:
                paths.append(folder)
        if not paths:
            messagebox.showinfo("View Render", "No render output folders found yet."); return
        if not os.path.exists(player):
            messagebox.showerror("Player Not Found",
                f"Player not found at:\n{player}\n\nUpdate it in ⚙ Settings."); return
        for p in paths:
            subprocess.Popen([player, p])

    # ---------------------------------------------------------------- Group collapse

    def _on_group_open(self):
        f = self._tree.focus()
        if f.startswith("__"): self._collapsed.discard(f)

    def _on_group_close(self):
        f = self._tree.focus()
        if f.startswith("__"): self._collapsed.add(f)

    def _toggle_group_focus(self):
        f = self._tree.focus()
        if not f.startswith("__"): return
        if self._tree.item(f, "open"):
            self._tree.item(f, open=False); self._collapsed.add(f)
        else:
            self._tree.item(f, open=True); self._collapsed.discard(f)

    def _collapse_all(self):
        for iid in self._tree.get_children():
            self._tree.item(iid, open=False); self._collapsed.add(iid)
            for child in self._tree.get_children(iid):
                self._tree.item(child, open=False); self._collapsed.add(child)

    def _expand_all(self):
        self._collapsed.clear()
        for iid in self._tree.get_children():
            self._tree.item(iid, open=True)
            for child in self._tree.get_children(iid):
                self._tree.item(child, open=True)

    # ---------------------------------------------------------------- Context menu

    def _ctx_render_now(self):
        jobs = self._selected_jobs()
        if not jobs: return
        if len(jobs) > 1:
            messagebox.showinfo("Render Now", "Select a single job."); return
        self.engine.render_now(jobs[0]); self._refresh_status(); self._refresh()

    def _ctx_render_selected(self):
        for j in self._selected_jobs():
            if j.status != STATUS_RENDERING:
                j.status = STATUS_WAITING; j.progress = 0
        self.store.save(); self._refresh(); self.engine.start(); self._refresh_status()

    def _ctx_edit(self):
        jobs = self._selected_jobs()
        if jobs: EditJobDialog(self, jobs, self.store, self._refresh)

    def _ctx_set_frame(self):
        jobs = self._selected_jobs()
        if jobs: SetFrameDialog(self, jobs, self.store, self._refresh)

    def _ctx_duplicate(self):
        new = []
        for j in self._selected_jobs():
            d = j.to_dict().copy()
            d.update(id=str(uuid.uuid4())[:8], status=STATUS_WAITING,
                     progress=0, retry_count=0, added_at=datetime.now().isoformat())
            new.append(Job(d))
        self.store.add_jobs(new); self._refresh()

    def _ctx_duplicate_as_frame(self):
        copies = []
        for j in self._selected_jobs():
            d = j.to_dict().copy()
            d.update(id=str(uuid.uuid4())[:8], status=STATUS_WAITING,
                     progress=0, retry_count=0, specific_frames=None,
                     added_at=datetime.now().isoformat())
            copies.append(Job(d))
        self.store.add_jobs(copies); self._refresh()
        if copies: SetFrameDialog(self, copies, self.store, self._refresh)

    def _ctx_toggle_disable(self):
        for j in self._selected_jobs():
            j.status = STATUS_WAITING if j.status == STATUS_DISABLED else STATUS_DISABLED
        self.store.save(); self._refresh()

    def _ctx_rerender(self):
        for j in self._selected_jobs():
            j.status = STATUS_WAITING; j.progress = 0; j.retry_count = 0
        self.store.save(); self._refresh()

    def _ctx_open_blend(self):
        for j in self._selected_jobs():
            if os.path.exists(j.blend_path): os.startfile(j.blend_path)

    def _ctx_browse_blend(self):
        for j in self._selected_jobs():
            folder = os.path.dirname(j.blend_path)
            if os.path.exists(folder): os.startfile(folder)

    def _ctx_browse_output(self):
        for j in self._selected_jobs():
            folder = os.path.dirname(j.output_path)
            os.makedirs(folder, exist_ok=True); os.startfile(folder)

    def _ctx_view_render(self):
        self._kb_view_render()

    def _ctx_logs(self):
        if not self._log_visible: self._toggle_log()

    def _ctx_remove(self):
        ids = [j.id for j in self._selected_jobs()]
        if not ids: return
        if messagebox.askyesno("Remove", f"Remove {len(ids)} job(s)?"):
            self.store.remove_jobs(ids); self._refresh()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    App().mainloop()
