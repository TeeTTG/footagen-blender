# SPDX-License-Identifier: GPL-3.0-or-later
"""Every network action is a button the user presses."""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import bridge, scene_export
from .client import FootagenError


def _report(operator, error):
    operator.report({"ERROR"}, str(error))
    bridge.state.error(str(error))
    return {"CANCELLED"}


class FOOTAGEN_OT_connect(bpy.types.Operator):
    bl_idname = "footagen.connect"
    bl_label = "Connect to Footagen"
    bl_description = "Pair Blender with your Footagen account. A browser opens for approval"

    def execute(self, _context):
        try:
            bridge.begin_connect()
        except FootagenError as error:
            return _report(self, error)
        return {"FINISHED"}


class FOOTAGEN_OT_open_approval(bpy.types.Operator):
    bl_idname = "footagen.open_approval"
    bl_label = "Open approval page"
    bl_description = "Open the Footagen page where you approve this code"

    def execute(self, _context):
        if bridge.state.pairing:
            bridge.open_browser(bridge.state.pairing["url"])
        return {"FINISHED"}


class FOOTAGEN_OT_cancel_connect(bpy.types.Operator):
    bl_idname = "footagen.cancel_connect"
    bl_label = "Cancel"
    bl_description = "Stop waiting for approval"

    def execute(self, _context):
        bridge.cancel_connect()
        return {"FINISHED"}


class FOOTAGEN_OT_disconnect(bpy.types.Operator):
    bl_idname = "footagen.disconnect"
    bl_label = "Disconnect"
    bl_description = "End this connection. Footagen stops accepting this Blender at once"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, _context):
        bridge.disconnect()
        return {"FINISHED"}


class FOOTAGEN_OT_send(bpy.types.Operator):
    bl_idname = "footagen.send"
    bl_label = "Send to Footagen"
    bl_description = "Send the scene, the selection or the camera to your open Footagen tabs"

    def execute(self, context):
        settings = context.scene.footagen
        try:
            bridge.send_scene(context, settings.send_mode, settings.include_materials)
        except (FootagenError, ValueError, RuntimeError) as error:
            return _report(self, error)
        for warning in bridge.state.warnings:
            self.report({"WARNING"}, warning)
        return {"FINISHED"}


class FOOTAGEN_OT_send_frame(bpy.types.Operator):
    bl_idname = "footagen.send_frame"
    bl_label = "Send frame"
    bl_description = "Render the current frame through the scene camera and send it as the shot's composition"

    def execute(self, context):
        try:
            bridge.send_frame(context)
        except (FootagenError, ValueError, RuntimeError) as error:
            return _report(self, error)
        return {"FINISHED"}


class FOOTAGEN_OT_check_inbox(bpy.types.Operator):
    bl_idname = "footagen.check_inbox"
    bl_label = "Check now"
    bl_description = "Look for scenes sent from Footagen"

    def execute(self, _context):
        if not bridge.check_inbox():
            self.report({"INFO"}, "Already checking, or not connected.")
        return {"FINISHED"}


class FOOTAGEN_OT_import_transfer(bpy.types.Operator):
    bl_idname = "footagen.import_transfer"
    bl_label = "Import"
    bl_description = "Import this scene into its own Footagen collection. A newer version replaces the old one"

    message_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        try:
            bridge.import_transfer(self.message_id, context.scene.footagen.use_footagen_camera)
        except FootagenError as error:
            return _report(self, error)
        return {"FINISHED"}


class FOOTAGEN_OT_set_role(bpy.types.Operator):
    bl_idname = "footagen.set_role"
    bl_label = "Set Footagen role"
    bl_description = "How Footagen treats this object"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(items=(
        ("AUTO", "Auto", "Footagen suggests the role from the name"),
        ("SET_PIECE", "Set piece", "Part of the set"),
        ("CHARACTER", "Character", "A character in the script"),
        ("PROP", "Prop", "A prop in the script"),
    ))

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def execute(self, context):
        for obj in context.selected_objects:
            if self.role == "AUTO":
                for key in ("footagen_kind", "footagen_asset"):
                    if key in obj:
                        del obj[key]
            else:
                obj["footagen_kind"] = scene_export.ROLE_KINDS[self.role]
                if self.role in ("CHARACTER", "PROP") and "footagen_asset" not in obj:
                    obj["footagen_asset"] = obj.name
                if self.role == "SET_PIECE" and "footagen_asset" in obj:
                    del obj["footagen_asset"]
        return {"FINISHED"}


class FOOTAGEN_OT_export_file(bpy.types.Operator, ExportHelper):
    bl_idname = "footagen.export_file"
    bl_label = "Export for Footagen (.glb)"
    bl_description = "Save a GLB that the 3D Blocker imports with Import 3D scene. Works without a connection"

    filename_ext = ".glb"
    filter_glob: StringProperty(default="*.glb", options={"HIDDEN"})

    def execute(self, context):
        settings = context.scene.footagen
        try:
            _manifest, parts, warnings = scene_export.collect_scene(context, settings.send_mode, settings.include_materials)
        except (ValueError, RuntimeError) as error:
            return _report(self, error)
        with open(self.filepath, "wb") as handle:
            handle.write(parts["scene.glb"])
        for warning in warnings:
            self.report({"WARNING"}, warning)
        self.report({"INFO"}, f"Saved {self.filepath}")
        return {"FINISHED"}


class FOOTAGEN_OT_open_footagen(bpy.types.Operator):
    bl_idname = "footagen.open_footagen"
    bl_label = "Open Footagen"
    bl_description = "Open Footagen in the browser"

    def execute(self, _context):
        try:
            bridge.open_browser(bridge.app_url())
        except FootagenError as error:
            return _report(self, error)
        return {"FINISHED"}


CLASSES = (
    FOOTAGEN_OT_connect,
    FOOTAGEN_OT_open_approval,
    FOOTAGEN_OT_cancel_connect,
    FOOTAGEN_OT_disconnect,
    FOOTAGEN_OT_send,
    FOOTAGEN_OT_send_frame,
    FOOTAGEN_OT_check_inbox,
    FOOTAGEN_OT_import_transfer,
    FOOTAGEN_OT_set_role,
    FOOTAGEN_OT_export_file,
    FOOTAGEN_OT_open_footagen,
)
