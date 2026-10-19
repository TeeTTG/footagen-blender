# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender scene -> package -> Blender: names, positions, camera and roles."""

import json
import math
import os
import tempfile

import bpy
from mathutils import Quaternion, Vector

import harness
from harness import addon

POSITION_TOLERANCE_M = 0.001
ANGLE_TOLERANCE_DEG = 0.1
NAMES = {"Floor", "Table", "Anna", "Lamp", "Ball"}


def to_gltf(vector):
    return Vector((vector.x, vector.z, -vector.y))


def angle_between(a, b):
    return math.degrees(a.normalized().angle(b.normalized(), 0.0))


def import_glb(data):
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "scene.glb")
    with open(path, "wb") as handle:
        handle.write(data)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    os.remove(path)
    return [obj for obj in bpy.data.objects if obj not in before]


def world_vertices(obj):
    return [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]


def test_scene_package_has_named_objects_roles_and_raw_camera():
    objects, camera = harness.build_known_scene()
    manifest, parts, warnings = addon("scene_export").collect_scene(bpy.context, "SCENE")
    assert warnings == [], warnings
    assert {entry["node"] for entry in manifest["objects"]} == NAMES
    by_name = {entry["node"]: entry for entry in manifest["objects"]}
    for name, obj in objects.items():
        assert obj["footagen_id"].startswith("blender:"), name
        assert by_name[name]["footagenId"] == obj["footagen_id"]
    assert by_name["Anna"]["roleHint"] == "character"
    assert by_name["Anna"]["entity"] == {"kind": "character", "name": "Anna"}
    assert by_name["Lamp"]["roleHint"] == "prop" and by_name["Lamp"]["entity"] is None
    assert by_name["Table"]["roleHint"] is None
    assert by_name["Ball"]["triangles"] > 0
    floor_box = by_name["Floor"]["bbox"]
    assert abs(floor_box["max"][0] - 4.0) < 1e-6 and abs(floor_box["min"][1]) < 1e-6, floor_box

    cam = manifest["camera"]
    assert cam["name"] == "Cam_A" and cam["lensMm"] == 35.0
    assert cam["sensor"] == {"widthMm": 36.0, "heightMm": 24.0, "fit": "AUTO"}
    assert cam["resolution"] == [1920, 1080]
    assert all(abs(a - b) < 1e-6 for a, b in zip(cam["position"], (0.0, 1.6, 5.0))), cam["position"]
    x, y, z, w = cam["quaternion"]
    rotation = Quaternion((w, x, y, z))
    blender_rotation = camera.matrix_world.to_quaternion()
    for local in (Vector((0, 0, -1)), Vector((0, 1, 0))):  # view direction and up
        expected = to_gltf(blender_rotation @ local)
        assert angle_between(rotation @ local, expected) < 1e-4, local
    assert manifest["units"] == {"metersPerUnit": 1.0, "upAxis": "Y"}
    assert manifest["frame"]["fps"] == 24.0
    assert parts["scene.glb"][:4] == b"glTF"


def test_blender_scene_survives_the_glb_round_trip():
    objects, camera = harness.build_known_scene()
    originals = {name: world_vertices(obj) for name, obj in objects.items()}
    rotations = {name: obj.matrix_world.to_quaternion() for name, obj in objects.items()}
    ids = {name: None for name in objects}
    camera_matrix = camera.matrix_world.copy()
    manifest, parts, _ = addon("scene_export").collect_scene(bpy.context, "SCENE")
    for name, obj in objects.items():
        ids[name] = obj["footagen_id"]
    harness.reset_scene()
    imported = import_glb(parts["scene.glb"])
    meshes = {obj.name: obj for obj in imported if obj.type == "MESH"}
    assert set(meshes) == NAMES, set(meshes)
    for name, obj in meshes.items():
        assert obj.get("footagen_id") == ids[name], "the stable id travels in the glTF extras"
        error = max(min((a - b).length for b in originals[name]) for a in world_vertices(obj))
        assert error < POSITION_TOLERANCE_M, (name, error)
        angle = math.degrees(obj.matrix_world.to_quaternion().rotation_difference(rotations[name]).angle)
        assert min(angle, 360 - angle) < ANGLE_TOLERANCE_DEG, (name, angle)
    assert meshes["Anna"].get("footagen_kind") == "character"
    cameras = [obj for obj in imported if obj.type == "CAMERA"]
    assert len(cameras) == 1
    assert (cameras[0].matrix_world.translation - camera_matrix.translation).length < POSITION_TOLERANCE_M
    angle = math.degrees(cameras[0].matrix_world.to_quaternion().rotation_difference(camera_matrix.to_quaternion()).angle)
    assert min(angle, 360 - angle) < ANGLE_TOLERANCE_DEG, angle


def test_selection_and_camera_only_send_less():
    objects, _camera = harness.build_known_scene()
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    objects["Table"].select_set(True)
    objects["Anna"].select_set(True)
    manifest, parts, _ = addon("scene_export").collect_scene(bpy.context, "SELECTION")
    assert {entry["node"] for entry in manifest["objects"]} == {"Table", "Anna"}
    assert objects["Table"].select_get() and not objects["Ball"].select_get(), "the user's selection is restored"
    harness.reset_scene()
    imported = import_glb(parts["scene.glb"])
    assert sorted(obj.type for obj in imported) == ["CAMERA", "MESH", "MESH"]

    harness.reset_scene()
    harness.build_known_scene()
    manifest, parts, _ = addon("scene_export").collect_scene(bpy.context, "CAMERA")
    assert manifest["objects"] == [] and manifest["camera"]["name"] == "Cam_A"
    harness.reset_scene()
    assert [obj.type for obj in import_glb(parts["scene.glb"])] == ["CAMERA"]


def test_send_frame_renders_through_the_scene_camera_and_restores_settings():
    harness.build_known_scene()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.cycles.device = "CPU"
    scene.render.resolution_x, scene.render.resolution_y = 64, 36
    scene.render.filepath = "//user-output/"
    scene.render.image_settings.file_format = "JPEG"
    png = addon("render").render_frame_png(bpy.context)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert scene.render.filepath == "//user-output/"
    assert scene.render.image_settings.file_format == "JPEG"


def footagen_package(target=None):
    """A package as Footagen sends it: Blocker GLB, target shot and camera."""
    harness.build_known_scene()
    manifest, parts, _ = addon("scene_export").collect_scene(bpy.context, "SCENE")
    manifest["target"] = target or {"projectId": "project-1", "sceneNumber": 3, "shotId": "shot-4a", "shotLabel": "4A", "projectName": "Kitchen"}
    manifest["source"] = {"app": "footagen", "addonVersion": None, "blenderVersion": None, "file": None}
    manifest["camera"].update({"lensMm": 50.0, "resolution": [1080, 1920]})
    return addon("package").build(manifest, parts)


def test_import_from_footagen_replaces_its_own_collection_only():
    data = footagen_package()
    harness.reset_scene()
    mine = bpy.data.objects.new("My own empty", None)
    bpy.context.scene.collection.objects.link(mine)
    manifest, parts = addon("package").read(data)
    for _ in range(2):  # a second version replaces the first
        summary = addon("scene_import").import_package(bpy.context, manifest, parts, use_camera=True)
    collection = bpy.data.collections[summary["collection"]]
    assert summary["collection"] == "Footagen scene 3 shot 4A"
    assert sorted(obj.name for obj in collection.objects if obj.type == "MESH") == sorted(NAMES)
    assert "My own empty" in bpy.data.objects
    scene = bpy.context.scene
    assert scene.camera.name == "Footagen camera" and scene.camera.name in collection.objects
    assert scene.camera.data.lens == 50.0 and scene.camera.data.sensor_width == 36.0
    assert (scene.render.resolution_x, scene.render.resolution_y) == (1080, 1920)
    assert (scene.camera.matrix_world.translation - Vector((0.0, -5.0, 1.6))).length < POSITION_TOLERANCE_M
    target = json.loads(scene["footagen_target"])
    assert target["shotId"] == "shot-4a" and target["sceneNumber"] == "3"
    # The next send goes back to the same shot.
    assert addon("scene_export").base_manifest(bpy.context)["target"]["shotId"] == "shot-4a"
