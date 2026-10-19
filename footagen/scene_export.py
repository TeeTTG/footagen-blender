# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads the Blender scene into a `footagen-scene/1` package: Blender's own
glTF export for the objects plus raw camera values. Main thread only."""

import os
import tempfile
import uuid
from datetime import datetime, timezone

import bpy
from mathutils import Matrix, Vector

from .version import ADDON_VERSION

# Blender is Z up; glTF and Footagen are Y up: (x, y, z) -> (x, z, -y).
TO_GLTF = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
ROLE_KINDS = {"SET_PIECE": "setPiece", "CHARACTER": "character", "PROP": "prop"}
KIND_ROLES = {value: key for key, value in ROLE_KINDS.items()}
TRIANGLE_WARNING = 1_000_000




def meters_per_unit(scene):
    return float(scene.unit_settings.scale_length or 1.0)


def gltf_pose(matrix_world, meters=1.0):
    """World matrix -> (position [x, y, z], quaternion [x, y, z, w]) in glTF space."""
    location, rotation, _scale = (TO_GLTF @ matrix_world).decompose()
    return [v * meters for v in location], [rotation.x, rotation.y, rotation.z, rotation.w]


def render_resolution(scene):
    render = scene.render
    scale = render.resolution_percentage / 100.0
    return [max(1, round(render.resolution_x * scale)), max(1, round(render.resolution_y * scale))]


def focus_distance(camera_object):
    dof = camera_object.data.dof
    if dof.focus_object is not None:
        return (dof.focus_object.matrix_world.translation - camera_object.matrix_world.translation).length
    return dof.focus_distance


def camera_manifest(scene, camera_object):
    """Blender's raw camera values; Footagen converts them (one rule, one place)."""
    data = camera_object.data
    meters = meters_per_unit(scene)
    position, quaternion = gltf_pose(camera_object.matrix_world.normalized(), meters)
    return {
        "name": camera_object.name,
        "type": data.type,
        "position": position,
        "quaternion": quaternion,
        "lensMm": data.lens,
        "sensor": {"widthMm": data.sensor_width, "heightMm": data.sensor_height, "fit": data.sensor_fit},
        "resolution": render_resolution(scene),
        "pixelAspect": [scene.render.pixel_aspect_x, scene.render.pixel_aspect_y],
        "shift": [data.shift_x, data.shift_y],
        "clip": [data.clip_start * meters, data.clip_end * meters],
        "focus": {
            "enabled": bool(data.dof.use_dof),
            "distanceM": focus_distance(camera_object) * meters,
            "fStop": data.dof.aperture_fstop,
        },
    }


def role_of(obj):
    kind = obj.get("footagen_kind")
    return kind if kind in KIND_ROLES else None


def ensure_identity(obj):
    """A stable id, so a later send updates the same Footagen object."""
    if not isinstance(obj.get("footagen_id"), str) or not obj["footagen_id"]:
        obj["footagen_id"] = f"blender:{uuid.uuid4()}"
    return obj["footagen_id"]


def _triangles(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    try:
        mesh = evaluated.to_mesh()
    except RuntimeError:
        return 0
    try:
        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)
    finally:
        evaluated.to_mesh_clear()


def _bbox(obj, meters):
    corners = [TO_GLTF @ (obj.matrix_world @ Vector(corner)) for corner in obj.bound_box]
    low = [min(c[i] for c in corners) * meters for i in range(3)]
    high = [max(c[i] for c in corners) * meters for i in range(3)]
    return {"min": low, "max": high}


def exported_objects(context, mode):
    scene = context.scene
    if mode == "CAMERA":
        return []
    view_layer = context.view_layer
    candidates = context.selected_objects if mode == "SELECTION" else scene.objects
    return [
        obj for obj in candidates
        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}
        and obj.visible_get(view_layer=view_layer)
    ]


def object_entries(context, objects):
    depsgraph = context.evaluated_depsgraph_get()
    meters = meters_per_unit(context.scene)
    entries = []
    for obj in objects:
        kind = role_of(obj)
        name = obj.get("footagen_asset") if isinstance(obj.get("footagen_asset"), str) else None
        entries.append({
            "node": obj.name,
            "footagenId": obj.get("footagen_id"),
            "roleHint": kind,
            "entity": {"kind": kind, "name": name} if kind in ("character", "prop") and name else None,
            "bbox": _bbox(obj, meters),
            "triangles": _triangles(obj, depsgraph),
        })
    return entries


class _Selection:
    """Selects exactly `objects` for the exporter and restores the user's selection."""

    def __init__(self, context, objects):
        self.context = context
        self.objects = objects

    def __enter__(self):
        view_layer = self.context.view_layer
        self.previous = [obj for obj in view_layer.objects if obj.select_get()]
        self.active = view_layer.objects.active
        for obj in self.previous:
            obj.select_set(False)
        for obj in self.objects:
            obj.select_set(True)
        return self

    def __exit__(self, *_):
        view_layer = self.context.view_layer
        for obj in view_layer.objects:
            obj.select_set(False)
        for obj in self.previous:
            if obj.name in view_layer.objects:
                obj.select_set(True)
        view_layer.objects.active = self.active


def export_glb(context, objects, camera_object, include_materials=False):
    """Blender's own glTF exporter, with the options Footagen reads."""
    selection = list(objects) + ([camera_object] if camera_object else [])
    folder = tempfile.mkdtemp(prefix="footagen-")
    path = os.path.join(folder, "scene.glb")
    try:
        with _Selection(context, selection):
            bpy.ops.export_scene.gltf(
                filepath=path,
                export_format="GLB",
                use_selection=True,
                use_visible=False,
                export_cameras=True,
                export_extras=True,
                export_yup=True,
                export_apply=True,
                export_lights=False,
                export_animations=False,
                export_materials="EXPORT" if include_materials else "VIEWPORT",
            )
        with open(path, "rb") as handle:
            return handle.read()
    finally:
        try:
            os.remove(path)
            os.rmdir(folder)
        except OSError:
            pass


def frame_info(scene):
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    return {"current": scene.frame_current, "fps": fps, "start": scene.frame_start, "end": scene.frame_end}


def scene_target(scene):
    """Where this scene came from in Footagen, if it ever did."""
    raw = scene.get("footagen_target")
    if not isinstance(raw, str):
        return None
    try:
        import json

        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def base_manifest(context):
    scene = context.scene
    return {
        "format": "footagen-scene/1",
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "app": "blender",
            "blenderVersion": bpy.app.version_string,
            "addonVersion": ADDON_VERSION,
            "file": os.path.basename(bpy.data.filepath) or None,
        },
        "target": scene_target(scene),
        "units": {"metersPerUnit": meters_per_unit(scene), "upAxis": "Y"},
        "frame": frame_info(scene),
    }


def collect_scene(context, mode="SCENE", include_materials=False, camera_object=None):
    """Returns (manifest, parts, warnings) for Send to Footagen.

    mode: SCENE (every visible object), SELECTION or CAMERA (camera only)."""
    scene = context.scene
    camera_object = camera_object or scene.camera
    warnings = []
    if camera_object is not None and camera_object.type != "CAMERA":
        camera_object = None
    if camera_object is not None and camera_object.data.type != "PERSP":
        warnings.append("Only perspective cameras are sent; this camera was left out.")
        camera_object = None
    if mode == "CAMERA" and camera_object is None:
        raise ValueError("The scene has no perspective camera to send.")
    objects = exported_objects(context, mode)
    if mode != "CAMERA" and not objects:
        raise ValueError("Nothing to send. Select objects or choose Whole scene.")
    for obj in objects:
        ensure_identity(obj)
    manifest = base_manifest(context)
    if camera_object is not None:
        manifest["camera"] = camera_manifest(scene, camera_object)
        if abs(camera_object.data.shift_x) > 1e-6 or abs(camera_object.data.shift_y) > 1e-6:
            warnings.append("Footagen does not use lens shift; the frame will be centred.")
    manifest["objects"] = object_entries(context, objects)
    triangles = sum(entry["triangles"] for entry in manifest["objects"])
    if triangles > TRIANGLE_WARNING:
        warnings.append(f"{triangles:,} triangles may slow Footagen's 3D Blocker. Consider Selection only.")
    if abs(meters_per_unit(scene) - 1.0) > 1e-6:
        warnings.append("The scene unit scale is not 1.0. Footagen reads Blender units as metres.")
    glb = export_glb(context, objects, camera_object, include_materials)
    return manifest, {"scene.glb": glb}, warnings
