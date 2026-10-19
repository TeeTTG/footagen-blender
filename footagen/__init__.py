# SPDX-License-Identifier: GPL-3.0-or-later
# Footagen for Blender. Copyright (C) 2026 Footagen.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version. It is distributed WITHOUT ANY WARRANTY; see the LICENSE file.

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty

from . import bridge, jobs, operators, ui


class FootagenSceneSettings(bpy.types.PropertyGroup):
    send_mode: EnumProperty(
        name="Send",
        items=(
            ("SCENE", "Whole scene", "Every visible object and the scene camera"),
            ("SELECTION", "Selection", "Selected objects and the scene camera"),
            ("CAMERA", "Camera only", "Only the scene camera"),
        ),
        default="SCENE",
    )
    include_materials: BoolProperty(
        name="Include materials",
        description="Send materials and textures. Off sends only base colours, which keeps transfers small",
        default=False,
    )
    use_footagen_camera: BoolProperty(
        name="Use Footagen camera",
        description="Make the imported camera the scene camera and match the frame size to the shot",
        default=True,
    )


class FootagenPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    show_developer: BoolProperty(name="Developer settings", default=False)
    app_url: StringProperty(
        name="Footagen app address",
        description="Leave empty for footagen.ai. Only localhost or 127.0.0.1 addresses are accepted for development",
        default="",
    )

    def draw(self, _context):
        layout = self.layout
        layout.label(text="Connect from the Footagen tab in the 3D Viewport sidebar (N).")
        layout.prop(self, "show_developer")
        if self.show_developer:
            layout.prop(self, "app_url")


CLASSES = (FootagenSceneSettings, FootagenPreferences, *operators.CLASSES, *ui.CLASSES)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.footagen = PointerProperty(type=FootagenSceneSettings)
    jobs.register()
    bridge.register()


def unregister():
    bridge.unregister()
    jobs.unregister()
    del bpy.types.Scene.footagen
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
