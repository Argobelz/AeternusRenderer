bl_info = {
    "name": "Aeternus Renderer — Send Job",
    "author": "Aeternus",
    "version": (0, 6),
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
ADDON_VERSION = "2.5"          # FIX #1: was "2.4" in addon, "2.3" in app — unified to 2.5
MIN_APP_VERSION = "0.6"        # FIX #6: app must be at least this version
RENDER_ROOT   = "J:\\Aeternus\\Render\\Img Seq"
FILE_PREFIXES = ("PNT", "TXT", "VID")

# ---------------------------------------------------------------------------
# Marker selection property (PNT/TXT only)
# ---------------------------------------------------------------------------

class AeternusMarkerItem(bpy.types.PropertyGroup):
    name    : bpy.props.StringProperty()
    enabled : bpy.props.BoolProperty(default=True)


def _sync_marker_list(scene):
    """Keep scene.aeternus_markers in sync with camera-bound markers."""
    try:
        existing = {item.name for item in scene.aeternus_markers}
        current  = {m.camera.name for m in scene.timeline_markers if m.camera}
        for name in current - existing:
            item = scene.aeternus_markers.add()
            item.name = name; item.enabled = True
        for i in range(len(scene.aeternus_markers) - 1, -1, -1):
            if scene.aeternus_markers[i].name not in current:
                scene.aeternus_markers.remove(i)
    except Exception as e:
        print(f"[Aeternus] _sync_marker_list error: {e}")


def _enabled_marker_names(scene):
    _sync_marker_list(scene)
    return {item.name for item in scene.aeternus_markers if item.enabled}


# ---------------------------------------------------------------------------
# Select All / Deselect All operators — markers
# ---------------------------------------------------------------------------

class AETERNUS_OT_markers_select_all(bpy.types.Operator):
    bl_idname  = "aeternus.markers_select_all"
    bl_label   = "Select All Markers"
    bl_options = {"REGISTER", "UNDO"}
    def execute(self, context):
        for item in context.scene.aeternus_markers:
            item.enabled = True
        return {"FINISHED"}

class AETERNUS_OT_markers_deselect_all(bpy.types.Operator):
    bl_idname  = "aeternus.markers_deselect_all"
    bl_label   = "Deselect All Markers"
    bl_options = {"REGISTER", "UNDO"}
    def execute(self, context):
        for item in context.scene.aeternus_markers:
            item.enabled = False
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Select All / Deselect All operators — view layers
# ---------------------------------------------------------------------------

class AETERNUS_OT_layers_select_all(bpy.types.Operator):
    bl_idname  = "aeternus.layers_select_all"
    bl_label   = "Select All Layers"
    bl_options = {"REGISTER", "UNDO"}
    def execute(self, context):
        for vl in context.scene.view_layers:
            vl.use = True
        return {"FINISHED"}

class AETERNUS_OT_layers_deselect_all(bpy.types.Operator):
    bl_idname  = "aeternus.layers_deselect_all"
    bl_label   = "Deselect All Layers"
    bl_options = {"REGISTER", "UNDO"}
    def execute(self, context):
        for vl in context.scene.view_layers:
            vl.use = False
        return {"FINISHED"}


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

# FIX #6: version comparison helper
def _version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0,)

# ---------------------------------------------------------------------------
# Camera-bound marker reading
# ---------------------------------------------------------------------------

def get_camera_ranges(scene):
    """
    Returns list of {camera, name, start, end} sorted by start frame.
    End = frame before next marker, or scene.frame_end for last.
    """
    try:
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
    except Exception as e:
        print(f"[Aeternus] get_camera_ranges error: {e}")
        return []

# ---------------------------------------------------------------------------
# Job builders
# ---------------------------------------------------------------------------

def build_jobs_pnt_txt(scene, blend_path, phase, prefix, enabled_markers=None):
    """
    PNT / TXT: multiple cameras each bound to a timeline marker.
    One job per camera × view layer.
    Fallback to scene frame range if no markers.
    enabled_markers: set of camera names to include (None = all).
    """
    ranges = get_camera_ranges(scene)
    if enabled_markers is not None:
        ranges = [r for r in ranges if r["name"] in enabled_markers]
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
        # No markers — single shot from filename, use scene frame range
        stem     = os.path.splitext(os.path.basename(blend_path))[0]
        shot_raw = stem[len(prefix) + 1:]
        parsed   = parse_shot(shot_raw)
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
# Shared send helper — FIX #6: version gate
# ---------------------------------------------------------------------------

def _send_payload(operator, url, payload_dict):
    """
    POST payload to the app. Returns the parsed JSON response or None on failure.
    Applies minimum app version check.
    """
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result  = json.loads(resp.read().decode())
            app_ver = result.get("app_version", "0")
            # FIX #6: reject incompatible app versions
            if _version_tuple(app_ver) < _version_tuple(MIN_APP_VERSION):
                operator.report(
                    {"ERROR"},
                    f"App v{app_ver} is too old (need >= v{MIN_APP_VERSION}). "
                    f"Please update Aeternus Renderer."
                )
                return None
            return result
    except urllib.error.URLError:
        operator.report({"ERROR"}, "Cannot connect to Aeternus Renderer. Is the app running?")
        return None

# ---------------------------------------------------------------------------
# Operator — Send Jobs
# ---------------------------------------------------------------------------

class AETERNUS_OT_send_job(bpy.types.Operator):
    bl_idname      = "aeternus.send_job"
    bl_label       = "Send to Aeternus Renderer"
    bl_description = "Sends selected camera shots and view layers to the Aeternus Renderer app"

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
            enabled = _enabled_marker_names(scene) if ranges else None
            filtered = [r for r in ranges if r["name"] in enabled] if enabled else ranges
            marker_info = (f"{len(filtered)} of {len(ranges)} marker(s) selected"
                           if ranges else "NO camera-bound markers — used scene range")
            jobs, error = build_jobs_pnt_txt(scene, blend_path, phase, prefix, enabled)
        else:
            active = [v for v in scene.view_layers if v.use]
            marker_info = f"VID mode — {len(active)} layer(s) selected"
            jobs, error = build_jobs_vid(scene, blend_path, phase)

        if not jobs:
            self.report({"ERROR"}, error or "No jobs could be built.")
            return {"CANCELLED"}

        if error:
            self.report({"WARNING"}, error)

        result = _send_payload(self, APP_URL, {"addon_version": ADDON_VERSION, "jobs": jobs})
        if result is None:
            return {"CANCELLED"}

        app_ver  = result.get("app_version", "?")
        mismatch = f" [app v{app_ver}]" if app_ver != ADDON_VERSION else ""
        self.report({"INFO"},
            f"Sent {result.get('added', 0)} job(s) — Phase: {phase}, "
            f"Type: {prefix} — {marker_info}{mismatch}")
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
            self.report({"ERROR"}, "Filename must start with PNT, TXT, or VID.")
            return {"CANCELLED"}

        phase = detect_phase(blend_path)

        if prefix in ("PNT", "TXT"):
            ranges = get_camera_ranges(scene)
            enabled = _enabled_marker_names(scene) if ranges else None
            filtered = [r for r in ranges if r["name"] in enabled] if enabled else ranges
            marker_info = (f"{len(filtered)} of {len(ranges)} marker(s) selected"
                           if ranges else "NO markers — scene range")
            jobs, error = build_jobs_pnt_txt(scene, blend_path, phase, prefix, enabled)
        else:
            active = [v for v in scene.view_layers if v.use]
            marker_info = f"VID mode — {len(active)} layer(s) selected"
            jobs, error = build_jobs_vid(scene, blend_path, phase)

        if not jobs:
            self.report({"ERROR"}, error or "No jobs could be built.")
            return {"CANCELLED"}

        if error:
            self.report({"WARNING"}, error)

        result = _send_payload(self, UPDATE_URL, {
            "addon_version": ADDON_VERSION,
            "blend_path"   : blend_path,
            "jobs"         : jobs,
        })
        if result is None:
            return {"CANCELLED"}

        self.report({"INFO"},
            f"Refreshed: replaced {result.get('replaced', 0)}, "
            f"added {result.get('added', 0)} job(s) — {marker_info}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Panel  — FIX: wrapped in try/except to surface draw() errors visibly
#           instead of silently truncating the UI
# ---------------------------------------------------------------------------

class AETERNUS_PT_panel(bpy.types.Panel):
    bl_label       = "Aeternus Renderer"
    bl_idname      = "AETERNUS_PT_panel"
    bl_space_type  = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context     = "output"

    def draw(self, context):
        try:
            self._draw_inner(context)
        except Exception as e:
            self.layout.label(text=f"Panel error: {e}", icon="ERROR")
            print(f"[Aeternus] Panel draw error: {e}")
            import traceback; traceback.print_exc()

    def _draw_inner(self, context):
        layout     = self.layout
        scene      = context.scene
        blend_path = bpy.data.filepath

        # ── File info box ──────────────────────────────────────────────────
        box = layout.box()
        if not blend_path:
            box.label(text="Blend not saved yet.", icon="ERROR")
            return

        prefix = get_blend_prefix(blend_path)
        phase  = detect_phase(blend_path)
        stem   = os.path.splitext(os.path.basename(blend_path))[0]
        box.label(text=f"File: {stem}",               icon="BLENDER")
        box.label(text=f"Type: {prefix or 'Unknown'}", icon="FILE_BLEND")
        box.label(text=f"Phase: {phase}",              icon="RENDERLAYERS")

        if not prefix:
            box.label(text="Filename must start with PNT, TXT, or VID.", icon="ERROR")
            return

        layout.separator()

        ranges = get_camera_ranges(scene)

        # ── PNT / TXT ─────────────────────────────────────────────────────
        if prefix in ("PNT", "TXT"):
            if ranges:
                # Marker-based: one checkbox per camera/shot
                _sync_marker_list(scene)
                enabled_set    = {item.name for item in scene.aeternus_markers if item.enabled}
                active_vl      = [v for v in scene.view_layers if v.use]
                selected_shots = sum(1 for r in ranges if r["name"] in enabled_set)
                total_jobs     = selected_shots * len(active_vl)

                box = layout.box()
                # Header row with Select All / Deselect All
                hdr = box.row(align=True)
                hdr.label(
                    text=f"Shots — {selected_shots} of {len(ranges)} selected:",
                    icon="CAMERA_DATA")
                hdr.operator("aeternus.markers_select_all",   text="All",  icon="CHECKBOX_HLT")
                hdr.operator("aeternus.markers_deselect_all", text="None", icon="CHECKBOX_DEHLT")

                # Draw each marker row — iterate aeternus_markers by index to
                # avoid referencing a PropertyGroup item directly in prop(), which
                # can crash in some Blender contexts.
                for idx in range(len(scene.aeternus_markers)):
                    item    = scene.aeternus_markers[idx]
                    r_match = next((r for r in ranges if r["name"] == item.name), None)
                    if not r_match:
                        continue
                    row = box.row(align=True)
                    row.prop(scene.aeternus_markers, f"[{idx}].enabled", text="")
                    sub = row.row()
                    sub.enabled = item.enabled
                    sub.label(text=f"{item.name}  [{r_match['start']} – {r_match['end']}]")

                box.separator()

                # View layers sub-section
                vl_hdr = box.row(align=True)
                vl_hdr.label(text="View layers:", icon="RENDERLAYERS")
                vl_hdr.operator("aeternus.layers_select_all",   text="All",  icon="CHECKBOX_HLT")
                vl_hdr.operator("aeternus.layers_deselect_all", text="None", icon="CHECKBOX_DEHLT")
                for vl in scene.view_layers:
                    row = box.row(align=True)
                    row.prop(vl, "use", text="")
                    sub = row.row()
                    sub.enabled = vl.use
                    sub.label(text=vl.name)

                box.separator()
                box.label(text=f"Total jobs to send: {total_jobs}")

            else:
                # No markers — single shot from filename
                shot_raw = stem[len(prefix) + 1:]
                parsed   = parse_shot(shot_raw)

                active_vl  = [v for v in scene.view_layers if v.use]
                total_jobs = len(active_vl)

                box = layout.box()
                if parsed:
                    box.label(
                        text=f"Shot: {parsed['shot_id']}  [{scene.frame_start} – {scene.frame_end}]",
                        icon="CAMERA_DATA")
                else:
                    box.label(text=f"Range: [{scene.frame_start} – {scene.frame_end}]",
                              icon="INFO")
                    box.label(text="Filename has no valid shot ID — check naming.", icon="ERROR")

                vl_hdr = box.row(align=True)
                vl_hdr.label(text=f"View layers — {len(active_vl)} selected:",
                             icon="RENDERLAYERS")
                vl_hdr.operator("aeternus.layers_select_all",   text="All",  icon="CHECKBOX_HLT")
                vl_hdr.operator("aeternus.layers_deselect_all", text="None", icon="CHECKBOX_DEHLT")
                for vl in scene.view_layers:
                    row = box.row(align=True)
                    row.prop(vl, "use", text="")
                    sub = row.row()
                    sub.enabled = vl.use
                    sub.label(text=vl.name)

                box.separator()
                box.label(text=f"Total jobs to send: {total_jobs}")

        # ── VID ───────────────────────────────────────────────────────────
        elif prefix == "VID":
            all_layers = list(scene.view_layers)
            active     = [v for v in all_layers if v.use]

            box = layout.box()
            hdr = box.row(align=True)
            hdr.label(text=f"VID layers — {len(active)} of {len(all_layers)} selected:",
                      icon="RENDERLAYERS")
            hdr.operator("aeternus.layers_select_all",   text="All",  icon="CHECKBOX_HLT")
            hdr.operator("aeternus.layers_deselect_all", text="None", icon="CHECKBOX_DEHLT")

            for vl in all_layers:
                row = box.row(align=True)
                row.prop(vl, "use", text="")
                sub = row.row()
                sub.enabled = vl.use
                sub.label(text=f"{vl.name}  [{scene.frame_start} – {scene.frame_end}]")

        layout.separator()
        row = layout.row(align=True)
        row.operator("aeternus.send_job",    icon="RENDER_ANIMATION", text="Send Jobs")
        row.operator("aeternus.update_jobs", icon="FILE_REFRESH",     text="Update Jobs")

# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

classes = (
    AeternusMarkerItem,
    AETERNUS_OT_markers_select_all,
    AETERNUS_OT_markers_deselect_all,
    AETERNUS_OT_layers_select_all,
    AETERNUS_OT_layers_deselect_all,
    AETERNUS_OT_send_job,
    AETERNUS_OT_update_jobs,
    AETERNUS_PT_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.aeternus_markers = bpy.props.CollectionProperty(type=AeternusMarkerItem)

def unregister():
    del bpy.types.Scene.aeternus_markers
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
