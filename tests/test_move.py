# SPDX-License-Identifier: GPL-3.0-or-later
"""Send camera move: per-frame camera after constraints and F-curve modifiers,
animated lens, subject root motion and the optional move render."""

import math

import bpy
from mathutils import Quaternion, Vector

import harness
from harness import addon


def to_gltf(vector):
    return Vector((vector.x, vector.z, -vector.y))


def animated_scene():
    """A dolly-in that aims at Anna with Track To, a 35 → 50 mm zoom, handheld
    noise, and Anna walking across the set while turning."""
    objects, camera = harness.build_known_scene()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 48
    aim = bpy.data.objects.new("Aim", None)
    scene.collection.objects.link(aim)
    aim.location = (-0.6, 0.2, 1.4)
    camera.rotation_euler = (0, 0, 0)
    constraint = camera.constraints.new("TRACK_TO")
    constraint.target = aim
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"
    camera.location = (0.0, -6.0, 1.6)
    camera.keyframe_insert("location", frame=1)
    camera.location = (0.0, -3.0, 1.6)
    camera.keyframe_insert("location", frame=48)
    camera.data.lens = 35.0
    camera.data.keyframe_insert("lens", frame=1)
    camera.data.lens = 50.0
    camera.data.keyframe_insert("lens", frame=48)
    for curve in _fcurves(camera):
        if curve.data_path == "location" and curve.array_index == 0:
            noise = curve.modifiers.new("NOISE")
            noise.scale, noise.strength = 2.0, 0.04
    anna = objects["Anna"]
    anna.location = (-0.6, 0.2, 0.85)
    anna.rotation_euler = (0, 0, 0)
    anna.keyframe_insert("location", frame=1)
    anna.keyframe_insert("rotation_euler", frame=1)
    anna.location = (0.6, 0.2, 0.85)
    anna.rotation_euler = (0, 0, math.radians(90))
    anna.keyframe_insert("location", frame=48)
    anna.keyframe_insert("rotation_euler", frame=48)
    scene.frame_set(10)  # the send must not depend on, or change, the current frame
    return objects, camera, aim


def _fcurves(obj):
    action = obj.animation_data.action
    if hasattr(action, "fcurves"):
        return list(action.fcurves)
    # Blender 5 layered actions: curves live in the slot's channelbag.
    curves = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                curves.extend(bag.fcurves)
    return curves


def test_camera_track_follows_constraints_zoom_and_noise_frame_by_frame():
    _objects, camera, aim = animated_scene()
    scene = bpy.context.scene
    manifest, parts, warnings = addon("camera_move").collect_move(bpy.context, include_subjects=True)
    assert scene.frame_current == 10, "the current frame is restored"
    assert parts == {}
    track = manifest["cameraTrack"]
    assert track["fps"] == 24.0 and track["startFrame"] == 1 and track["endFrame"] == 48
    assert len(track["samples"]) == 48
    assert track["samples"][0]["t"] == 0.0 and abs(track["samples"][-1]["t"] - 47 / 24) < 1e-6
    assert abs(track["samples"][0]["lensMm"] - 35.0) < 1e-4 and abs(track["samples"][-1]["lensMm"] - 50.0) < 1e-4
    assert any("lens changes" in warning for warning in warnings)
    # Independent check of three frames: the same evaluated pose Blender draws.
    for frame in (1, 24, 48):
        scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        matrix = camera.evaluated_get(depsgraph).matrix_world.normalized()
        sample = track["samples"][frame - 1]
        expected = to_gltf(matrix.translation)
        assert (Vector(sample["position"]) - expected).length < 1e-5, (frame, sample["position"], expected)
        x, y, z, w = sample["quaternion"]
        forward = Quaternion((w, x, y, z)) @ Vector((0, 0, -1))
        toward_aim = (to_gltf(aim.matrix_world.translation) - expected).normalized()
        assert forward.angle(toward_aim) < math.radians(0.1), (frame, math.degrees(forward.angle(toward_aim)))
    # Handheld noise is in the samples (x wobbles while the dolly runs along y).
    xs = [sample["position"][0] for sample in track["samples"]]
    assert max(xs) - min(xs) > 0.01
    scene.frame_set(10)


def test_subject_motion_is_root_position_and_facing_of_marked_objects_only():
    animated_scene()
    manifest, _parts, _warnings = addon("camera_move").collect_move(bpy.context, include_subjects=True)
    tracks = {track["node"]: track for track in manifest["subjectTracks"]}
    assert set(tracks) == {"Anna"}, "static marked objects and unmarked objects are left out"
    samples = tracks["Anna"]["samples"]
    assert len(samples) == 48
    assert (Vector(samples[0]["position"]) - Vector((-0.6, 0.85, -0.2))).length < 1e-5
    assert (Vector(samples[-1]["position"]) - Vector((0.6, 0.85, -0.2))).length < 1e-5
    # Blender front (-Y) is glTF +Z: yaw 0 at the start, turned 90° about Blender Z at the end.
    assert abs(samples[0]["yawDeg"]) < 1e-4
    assert abs(abs(samples[-1]["yawDeg"]) - 90.0) < 1e-3
    without = addon("camera_move").collect_move(bpy.context, include_subjects=False)[0]
    assert without["subjectTracks"] == []


def test_preview_range_and_length_limit():
    animated_scene()
    scene = bpy.context.scene
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 5, 12
    manifest = addon("camera_move").collect_move(bpy.context, use_preview=True, include_subjects=False)[0]
    assert len(manifest["cameraTrack"]["samples"]) == 8 and manifest["cameraTrack"]["startFrame"] == 5
    scene.frame_end = 24 * 61
    try:
        addon("camera_move").collect_move(bpy.context, include_subjects=False)
    except ValueError as error:
        assert "60 seconds" in str(error)
    else:
        raise AssertionError("moves longer than 60 s are refused")


def test_move_render_is_an_mp4_and_settings_are_restored():
    animated_scene()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 6
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.cycles.device = "CPU"
    scene.render.resolution_x, scene.render.resolution_y = 64, 36
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = "//frames/"
    manifest, parts, _ = addon("camera_move").collect_move(bpy.context, include_subjects=False, include_render=True)
    video = parts["move.mp4"]
    assert video[4:8] == b"ftyp", video[:16]
    assert scene.render.image_settings.file_format == "PNG" and scene.render.filepath == "//frames/"
    assert (scene.frame_start, scene.frame_end) == (1, 6)
    data = addon("package").build(manifest, parts)
    assert addon("package").read(data)[1]["move.mp4"][4:8] == b"ftyp"
