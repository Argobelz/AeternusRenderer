bl_info = {
    "name": "Aeternus Renderer — Send Job",
    "author": "Aeternus",
    "version": (0, 3),
    "blender": (5, 0, 0),
    "location": "Properties > Output > Aeternus Renderer",
    "description": "Sends render jobs to the Aeternus Renderer app",
    "category": "Render",
}

import bpy
import json
import re
import os
import urllib.request
import urllib.error

APP_PORT      = 47821
APP_URL       = f"http://localhost:{APP_PORT}/add_jobs"
UPDATE_URL    = f"http://localhost:{APP_PORT}/update_jobs"
ADDON_VERSION = "2.3"
RENDER_ROOT   = "J:\\Aeternus\\Render\\Img Seq"
FILE_PREFIXES = ("PNT", "TXT", "VID")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def detect_phase(blend_path):
    for p in ("Phase 3", "Phase 2", "Phase 1"):
        if p.lower() in blend_path.lower():
            return p
    return "Phase 1"

def get_blend_prefix(blend_path):
    stem = os.path.splitext(os.path.basename(blend_path))[0].upper()
    for pfx in FILE_PREFIXES:
        if stem.startswith(pfx):
            return pfx
    return None

def parse_shot(name):
    m = re.match(r"(EPS(\d+))_(SQ\d+)_(SH[0-9A-Z]+)", name.strip(), re.IGNORECASE)
    if not m:
        return None
    eps_raw = m.group(1).upper()
    eps_num = m.group(2)
    sq      = m.group(3).upper()
    sh      = m.group(4).upper()
    return {
        "eps_raw"    : eps_raw,
        "eps_folder" : "EPS" + eps_num.zfill(3),
        "sq"         : sq,
        "sh"         : sh,
        "shot_id"    : f"{eps_raw}_{sq}_{sh}",
    }

def output_path(phase, parsed, layer_name):
    folder = os.path.join(RENDER_ROOT, phase,
                          parsed["eps_folder"], parsed["sq"], parsed["sh"])
    return os.path.join(folder, f"{layer_name}_{parsed['shot_id']}_")

# ---------------------------------------------------------------------------
# Camera-bound marker reading
# ---------------------------------------------------------------------------

def get_camera_ranges(scene):
    """
    Returns list of {camera, name, start, end} sorted by start frame.
    End = frame before next marker, or scene.frame_end for last.
    """
    bound = [m for m in scene.timeline_markers if m.camera is not None]
    if not bound:
        return []
    bound.sort(key=lambda m: m.frame)
    result = []
    for i, m in enumerate(bound):
        start = m.frame
        end   = bound[i + 1].frame - 1 if i + 1 < len(bound) else scene.frame_end
        result.append({
            "camera": m.camera,
            "name"  : m.camera.name,
            "start" : start,
            "end"   : end,
        })
    return result

# ---------------------------------------------------------------------------
# Job builders
# ---------------------------------------------------------------------------

def build_jobs_pnt_txt(scene, blend_path, phase, prefix):
    """
    PNT / TXT: multiple cameras each bound to a timeline marker.
    One job per camera × view layer.
    Fallback to scene frame range if no markers.
    """
    ranges = get_camera_ranges(scene)
    jobs   = []
    errors = []

    if ranges:
        for cam_range in ranges:
            parsed = parse_shot(cam_range["name"])
            if not parsed:
                errors.append(f"Camera '{cam_range['name']}' doesn't match EPSxx_SQxx_SHxxx — skipped.")
                continue
            for vl in scene.view_layers:
                if not vl.use:
                    continue
                jobs.append({
                    "blend_path"  : blend_path,
                    "prefix"      : prefix,
                    "episode"     : parsed["eps_folder"],
                    "sequence"    : parsed["sq"],
                    "shot"        : parsed["sh"],
                    "shot_id"     : parsed["shot_id"],
                    "view_layer"  : vl.name,
                    "frame_start" : cam_range["start"],
                    "frame_end"   : cam_range["end"],
                    "output_path" : output_path(phase, parsed, vl.name),
                    "phase"       : phase,
                    "camera_name" : cam_range["name"],
                })
    else:
        # No markers — single camera, use scene frame range
        stem    = os.path.splitext(os.path.basename(blend_path))[0]
        shot_raw = stem[len(prefix) + 1:]
        parsed  = parse_shot(shot_raw)
        if not parsed:
            return None, f"No markers and filename '{stem}' has no valid shot ID."
        for vl in scene.view_layers:
            if not vl.use:
                continue
            jobs.append({
                "blend_path"  : blend_path,
                "prefix"      : prefix,
                "episode"     : parsed["eps_folder"],
                "sequence"    : parsed["sq"],
                "shot"        : parsed["sh"],
                "shot_id"     : parsed["shot_id"],
                "view_layer"  : vl.name,
                "frame_start" : scene.frame_start,
                "frame_end"   : scene.frame_end,
                "output_path" : output_path(phase, parsed, vl.name),
                "phase"       : phase,
                "camera_name" : scene.camera.name if scene.camera else "",
            })

    return (jobs or None), ("\n".join(errors) if errors else None)


def build_jobs_vid(scene, blend_path, phase):
    """
    VID: numbered view layers, each with its own camera.
    One job per view layer.
    """
    jobs   = []
    errors = []

    cameras = [o for o in scene.objects if o.type == "CAMERA"]

    for vl in scene.view_layers:
        if not vl.use:
            continue

        # Find camera visible in this view layer
        cam = None
        for c in cameras:
            for col in c.users_collection:
                lc = vl.layer_collection.children.get(col.name)
                if lc and not lc.exclude:
                    cam = c
                    break
            if cam:
                break
        if not cam:
            cam = scene.camera

        if not cam:
            errors.append(f"VID layer '{vl.name}': no camera found — skipped.")
            continue

        parsed = parse_shot(cam.name)
        if not parsed:
            errors.append(f"VID layer '{vl.name}': camera '{cam.name}' doesn't match pattern — skipped.")
            continue

        jobs.append({
            "blend_path"  : blend_path,
            "prefix"      : "VID",
            "episode"     : parsed["eps_folder"],
            "sequence"    : parsed["sq"],
            "shot"        : parsed["sh"],
            "shot_id"     : parsed["shot_id"],
            "view_layer"  : vl.name,
            "frame_start" : scene.frame_start,
            "frame_end"   : scene.frame_end,
            "output_path" : output_path(phase, parsed, vl.name),
            "phase"       : phase,
            "camera_name" : cam.name,
        })

    return (jobs or None), ("\n".join(errors) if errors else None)

# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class AETERNUS_OT_send_job(bpy.types.Operator):
    bl_idname     = "aeternus.send_job"
    bl_label      = "Send to Aeternus Renderer"
    bl_description = "Sends all camera shots and view layers to the Aeternus Renderer app"

    def execute(self, context):
        scene      = context.scene
        blend_path = bpy.data.filepath

        if not blend_path:
            self.report({"ERROR"}, "Save the blend file first.")
            return {"CANCELLED"}

        prefix = get_blend_prefix(blend_path)
        if not prefix:
            self.report({"ERROR"},
                f"Filename must start with PNT, TXT, or VID. Got: {os.path.basename(blend_path)}")
            return {"CANCELLED"}

        phase = detect_phase(blend_path)

        if prefix in ("PNT", "TXT"):
            ranges = get_camera_ranges(scene)
            marker_info = f"{len(ranges)} marker(s)" if ranges else "NO camera-bound markers — used scene range"
            jobs, error = build_jobs_pnt_txt(scene, blend_path, phase, prefix)
        else:
            marker_info = "VID mode"
            jobs, error = build_jobs_vid(scene, blend_path, phase)

        if not jobs:
            self.report({"ERROR"}, error or "No jobs could be built.")
            return {"CANCELLED"}

        if error:
            self.report({"WARNING"}, error)

        payload = json.dumps({"addon_version": ADDON_VERSION, "jobs": jobs}).encode("utf-8")
        req = urllib.request.Request(
            APP_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                result    = json.loads(resp.read().decode())
                app_ver   = result.get("app_version", "?")
                mismatch  = f" [app v{app_ver}]" if app_ver != ADDON_VERSION else ""
                self.report({"INFO"},
                    f"Sent {result.get('added', 0)} job(s) — Phase: {phase}, "
                    f"Type: {prefix} — {marker_info}{mismatch}")
        except urllib.error.URLError:
            self.report({"ERROR"}, "Cannot connect to Aeternus Renderer. Is the app running?")
            return {"CANCELLED"}

        return {"FINISHED"}

# ---------------------------------------------------------------------------
# Operator — Update Jobs (refresh)
# ---------------------------------------------------------------------------

class AETERNUS_OT_update_jobs(bpy.types.Operator):
    bl_idname      = "aeternus.update_jobs"
    bl_label       = "Update Jobs (Refresh)"
    bl_description = (
        "Replaces all Waiting/Failed jobs from this blend file with fresh data. "
        "Rendering or Done jobs are not touched."
    )

    def execute(self, context):
        scene      = context.scene
        blend_path = bpy.data.filepath

        if not blend_path:
            self.report({"ERROR"}, "Save the blend file first.")
            return {"CANCELLED"}

        prefix = get_blend_prefix(blend_path)
        if not prefix:
            self.report({"ERROR"},
                f"Filename must start with PNT, TXT, or VID.")
            return {"CANCELLED"}

        phase = detect_phase(blend_path)

        if prefix in ("PNT", "TXT"):
            ranges = get_camera_ranges(scene)
            marker_info = f"{len(ranges)} marker(s)" if ranges else "NO markers — scene range"
            jobs, error = build_jobs_pnt_txt(scene, blend_path, phase, prefix)
        else:
            marker_info = "VID mode"
            jobs, error = build_jobs_vid(scene, blend_path, phase)

        if not jobs:
            self.report({"ERROR"}, error or "No jobs could be built.")
            return {"CANCELLED"}

        if error:
            self.report({"WARNING"}, error)

        payload = json.dumps({
            "addon_version": ADDON_VERSION,
            "blend_path"   : blend_path,
            "jobs"         : jobs,
        }).encode("utf-8")

        req = urllib.request.Request(
            UPDATE_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode())
                self.report({"INFO"},
                    f"Refreshed: replaced {result.get('replaced', 0)}, "
                    f"added {result.get('added', 0)} job(s) — {marker_info}")
        except urllib.error.URLError:
            self.report({"ERROR"}, "Cannot connect to Aeternus Renderer. Is the app running?")
            return {"CANCELLED"}

        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class AETERNUS_PT_panel(bpy.types.Panel):
    bl_label       = "Aeternus Renderer"
    bl_idname      = "AETERNUS_PT_panel"
    bl_space_type  = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context     = "output"

    def draw(self, context):
        layout     = self.layout
        scene      = context.scene
        blend_path = bpy.data.filepath

        box = layout.box()
        if blend_path:
            prefix = get_blend_prefix(blend_path)
            phase  = detect_phase(blend_path)
            stem   = os.path.splitext(os.path.basename(blend_path))[0]
            box.label(text=f"File: {stem}", icon="BLENDER")
            box.label(text=f"Type: {prefix or 'Unknown'}", icon="FILE_BLEND")
            box.label(text=f"Phase: {phase}", icon="RENDERLAYERS")
        else:
            box.label(text="Blend not saved yet.", icon="ERROR")

        layout.separator()

        if blend_path:
            prefix = get_blend_prefix(blend_path)
            ranges = get_camera_ranges(scene)

            if prefix in ("PNT", "TXT"):
                if ranges:
                    box = layout.box()
                    box.label(text=f"Shots from markers ({len(ranges)}):", icon="CAMERA_DATA")
                    for r in ranges:
                        box.label(text=f"  {r['name']}  [{r['start']} – {r['end']}]")
                    box.label(text=f"  × {len([v for v in scene.view_layers if v.use])} view layers")
                    box.label(text=f"  = {len(ranges) * len([v for v in scene.view_layers if v.use])} total jobs")
                else:
                    box = layout.box()
                    box.label(text="No markers — using scene range:", icon="INFO")
                    box.label(text=f"  [{scene.frame_start} – {scene.frame_end}]")
                    box.label(text=f"  × {len([v for v in scene.view_layers if v.use])} view layers")

            elif prefix == "VID":
                active = [v for v in scene.view_layers if v.use]
                box = layout.box()
                box.label(text=f"VID layers → {len(active)} jobs:", icon="RENDERLAYERS")
                for vl in active:
                    box.label(text=f"  {vl.name}  [{scene.frame_start} – {scene.frame_end}]")

        layout.separator()
        row = layout.row(align=True)
        row.operator("aeternus.send_job",    icon="RENDER_ANIMATION", text="Send Jobs")
        row.operator("aeternus.update_jobs", icon="FILE_REFRESH",     text="Update Jobs")

# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

classes = (AETERNUS_OT_send_job, AETERNUS_OT_update_jobs, AETERNUS_PT_panel)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
