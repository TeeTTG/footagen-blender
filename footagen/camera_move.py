# SPDX-License-Identifier: GPL-3.0-or-later
"""Send camera move: the camera as Blender shows it on every frame, after
constraints, rigs, drivers, parents and F-curve modifiers, plus the root
motion of objects marked as characters or props. Main thread only."""

import math
import os
import tempfile

import bpy
from mathutils import Vector

from .scene_export import (
    base_manifest,
    camera_manifest,
    gltf_pose,
    meters_per_unit,
    role_of,
    TO_GLTF,
)

MAX_MOVE_SECONDS = 60.0
MAX_MOVE_RENDER_HEIGHT = 720


def frame_range(scene, use_preview=False):
    if use_preview and scene.use_preview_range:
        return scene.frame_preview_start, scene.frame_preview_end
    return scene.frame_start, scene.frame_end


def scene_fps(scene):
    return scene.render.fps / (scene.render.fps_base or 1.0)


class _FrameGuard:
    """Steps through frames and puts the current frame back afterwards."""

    def __init__(self, scene):
        self.scene = scene

    def __enter__(self):
        self.saved = (self.scene.frame_current, self.scene.frame_subframe)
        return self

    def __exit__(self, *_):
        self.scene.frame_set(self.saved[0], subframe=self.saved[1])


def _focus_distance(camera_eval, meters):
    dof = camera_eval.data.dof
    if not dof.use_dof:
        return None
    if dof.focus_object is not None:
        return (dof.focus_object.matrix_world.translation - camera_eval.matrix_world.translation).length * meters
    return dof.focus_distance * meters


def _yaw_deg(matrix_world):
    """Facing direction in glTF space: Blender's front (-Y) becomes +Z; yaw 0 faces +Z."""
    forward = TO_GLTF.to_3x3() @ (matrix_world.to_3x3() @ Vector((0.0, -1.0, 0.0)))
    return math.degrees(math.atan2(forward.x, forward.z))


def sample_move(context, camera_object, start, end, subjects=()):
    """Returns (cameraTrack, subjectTracks) for frames start..end inclusive."""
    scene = context.scene
    fps = scene_fps(scene)
    meters = meters_per_unit(scene)
    if end <= start:
        raise ValueError("The frame range needs at least two frames.")
    if (end - start) / fps > MAX_MOVE_SECONDS:
        raise ValueError(f"Camera moves are limited to {int(MAX_MOVE_SECONDS)} seconds. Shorten the frame range.")
    camera_samples = []
    subject_samples = {obj.name: [] for obj in subjects}
    with _FrameGuard(scene):
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            depsgraph = context.evaluated_depsgraph_get()
            camera_eval = camera_object.evaluated_get(depsgraph)
            position, quaternion = gltf_pose(camera_eval.matrix_world.normalized(), meters)
            sample = {
                "t": round((frame - start) / fps, 6),
                "position": position,
                "quaternion": quaternion,
                "lensMm": camera_eval.data.lens,
            }
            focus = _focus_distance(camera_eval, meters)
            if focus is not None:
                sample["focusDistanceM"] = focus
            camera_samples.append(sample)
            for obj in subjects:
                evaluated = obj.evaluated_get(depsgraph)
                location = TO_GLTF @ evaluated.matrix_world.translation
                subject_samples[obj.name].append({
                    "t": sample["t"],
                    "position": [v * meters for v in location],
                    "yawDeg": _yaw_deg(evaluated.matrix_world),
                })
    track = {"fps": fps, "startFrame": start, "endFrame": end, "samples": camera_samples}
    subject_tracks = [
        {"node": obj.name, "footagenId": obj.get("footagen_id"), "fps": fps, "samples": subject_samples[obj.name]}
        for obj in subjects
        if _moves(subject_samples[obj.name])
    ]
    return track, subject_tracks


def _moves(samples):
    if len(samples) < 2:
        return False
    first = samples[0]
    return any(
        max(abs(a - b) for a, b in zip(sample["position"], first["position"])) > 1e-4
        or abs(sample["yawDeg"] - first["yawDeg"]) > 0.01
        for sample in samples[1:]
    )


def linked_subjects(context):
    """Objects marked as characters or props in the Footagen tab."""
    return [obj for obj in context.scene.objects if role_of(obj) in ("character", "prop")]


class _VideoSettings:
    """MP4 (H.264) output for the move render; restores everything it touched."""

    def __init__(self, scene, path, start, end):
        self.scene, self.path, self.start, self.end = scene, path, start, end

    def __enter__(self):
        scene, render = self.scene, self.scene.render
        image = render.image_settings
        self.saved = {
            "filepath": render.filepath,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "percentage": render.resolution_percentage,
            "file_format": image.file_format,
            "media_type": getattr(image, "media_type", None),
            "format": render.ffmpeg.format,
            "codec": render.ffmpeg.codec,
            "crf": render.ffmpeg.constant_rate_factor,
            "audio": render.ffmpeg.audio_codec,
        }
        height = render.resolution_y * render.resolution_percentage / 100.0
        if height > MAX_MOVE_RENDER_HEIGHT:
            render.resolution_percentage = max(1, int(render.resolution_percentage * MAX_MOVE_RENDER_HEIGHT / height))
        if hasattr(image, "media_type"):  # Blender 5.0+
            image.media_type = "VIDEO"
        image.file_format = "FFMPEG"
        render.ffmpeg.format = "MPEG4"
        render.ffmpeg.codec = "H264"
        render.ffmpeg.constant_rate_factor = "MEDIUM"
        render.ffmpeg.audio_codec = "NONE"
        render.filepath = self.path
        scene.frame_start, scene.frame_end = self.start, self.end
        return self

    def __exit__(self, *_):
        scene, render, saved = self.scene, self.scene.render, self.saved
        image = render.image_settings
        render.ffmpeg.format, render.ffmpeg.codec = saved["format"], saved["codec"]
        render.ffmpeg.constant_rate_factor, render.ffmpeg.audio_codec = saved["crf"], saved["audio"]
        if saved["media_type"] is not None:
            image.media_type = saved["media_type"]
        image.file_format = saved["file_format"]
        render.filepath = saved["filepath"]
        render.resolution_percentage = saved["percentage"]
        scene.frame_start, scene.frame_end = saved["frame_start"], saved["frame_end"]


def render_move_mp4(context, start, end):
    """Blender's own render of the move with the scene's render settings, at most 720 p."""
    scene = context.scene
    folder = tempfile.mkdtemp(prefix="footagen-")
    path = os.path.join(folder, "move.mp4")
    try:
        with _FrameGuard(scene), _VideoSettings(scene, path, start, end):
            bpy.ops.render.render(animation=True)
        produced = path if os.path.exists(path) else next(
            (os.path.join(folder, name) for name in os.listdir(folder) if name.endswith(".mp4")), None)
        if not produced:
            raise RuntimeError("Blender did not produce a video. Check the render settings.")
        with open(produced, "rb") as handle:
            return handle.read()
    finally:
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            pass


def collect_move(context, use_preview=False, include_subjects=True, include_render=False):
    """Returns (manifest, parts, warnings) for Send camera move."""
    scene = context.scene
    camera_object = scene.camera
    if camera_object is None or camera_object.type != "CAMERA" or camera_object.data.type != "PERSP":
        raise ValueError("The scene needs a perspective camera to send a camera move.")
    start, end = frame_range(scene, use_preview)
    subjects = linked_subjects(context) if include_subjects else []
    track, subject_tracks = sample_move(context, camera_object, start, end, subjects)
    manifest = base_manifest(context)
    with _FrameGuard(scene):
        scene.frame_set(start)
        manifest["camera"] = camera_manifest(scene, camera_object)
    manifest["cameraTrack"] = track
    manifest["subjectTracks"] = subject_tracks
    manifest["objects"] = []
    warnings = []
    lenses = [sample["lensMm"] for sample in track["samples"]]
    if abs(camera_object.data.shift_x) > 1e-6 or abs(camera_object.data.shift_y) > 1e-6:
        warnings.append("Footagen does not use lens shift; the frame will be centred.")
    parts = {}
    if include_render:
        parts["move.mp4"] = render_move_mp4(context, start, end)
    if max(lenses) - min(lenses) > 1e-3:
        warnings.append("The lens changes during the move; Footagen follows it.")
    return manifest, parts, warnings
