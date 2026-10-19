# SPDX-License-Identifier: GPL-3.0-or-later
"""Send frame: renders the current frame through the scene camera with the
scene's own render settings and returns PNG bytes. Main thread only."""

import os
import tempfile

import bpy


class _RenderSettings:
    """Renders to a temporary PNG and restores every setting it touched."""

    def __init__(self, scene, path, camera_object):
        self.scene = scene
        self.path = path
        self.camera_object = camera_object

    def __enter__(self):
        render = self.scene.render
        image = render.image_settings
        self.saved = (render.filepath, image.file_format, image.color_mode, image.color_depth, self.scene.camera)
        render.filepath = self.path
        image.file_format = "PNG"
        image.color_mode = "RGB"
        image.color_depth = "8"
        if self.camera_object is not None:
            self.scene.camera = self.camera_object
        return self

    def __exit__(self, *_):
        render = self.scene.render
        image = render.image_settings
        render.filepath, image.file_format, image.color_mode, image.color_depth, self.scene.camera = self.saved


def render_frame_png(context, camera_object=None):
    scene = context.scene
    camera_object = camera_object or scene.camera
    if camera_object is None or camera_object.type != "CAMERA":
        raise ValueError("The scene has no camera to render from.")
    folder = tempfile.mkdtemp(prefix="footagen-")
    path = os.path.join(folder, "frame.png")
    try:
        with _RenderSettings(scene, path, camera_object):
            bpy.ops.render.render(write_still=True, use_viewport=False)
        if not os.path.exists(path):
            raise RuntimeError("Blender did not produce a frame. Check the render settings.")
        with open(path, "rb") as handle:
            return handle.read()
    finally:
        try:
            if os.path.exists(path):
                os.remove(path)
            os.rmdir(folder)
        except OSError:
            pass
