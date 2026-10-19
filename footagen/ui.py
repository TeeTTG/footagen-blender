# SPDX-License-Identifier: GPL-3.0-or-later
"""The Footagen tab in the 3D Viewport sidebar (N panel)."""

import time

import bpy

from . import bridge, jobs, scene_export


def _ago(timestamp):
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def _target_label(target):
    if not target:
        return "No shot chosen yet"
    parts = []
    if target.get("projectName"):
        parts.append(target["projectName"])
    if target.get("sceneNumber"):
        parts.append(f"Scene {target['sceneNumber']}")
    if target.get("shotLabel"):
        parts.append(f"Shot {target['shotLabel']}")
    return " · ".join(parts) or "A Footagen shot"


class FootagenPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Footagen"


class FOOTAGEN_PT_main(FootagenPanel, bpy.types.Panel):
    bl_label = "Footagen"

    def draw(self, context):
        layout = self.layout
        state = bridge.state
        if not bridge.online():
            box = layout.box()
            box.label(text="Online access is off", icon="ERROR")
            col = box.column(align=True)
            col.label(text="Turn on Allow Online Access in")
            col.label(text="Preferences → System → Network.")
            col.label(text="Export for Footagen works offline.")
        elif state.connection:
            row = layout.row()
            row.label(text=state.connection.get("deviceName") or "Connected", icon="LINKED")
            layout.operator("footagen.disconnect", icon="UNLINKED")
        elif state.pairing:
            box = layout.box()
            box.label(text="Approve this code in Footagen:")
            code = box.row()
            code.scale_y = 1.6
            code.alignment = "CENTER"
            code.label(text=state.pairing["userCode"])
            row = box.row(align=True)
            row.operator("footagen.open_approval", icon="URL")
            row.operator("footagen.cancel_connect")
        else:
            row = layout.row()
            row.scale_y = 1.3
            row.enabled = not jobs.is_busy("connect")
            row.operator("footagen.connect", icon="LINKED")
        if state.message:
            box = layout.box()
            for index, line in enumerate(_wrap(state.message, 38)):
                box.label(text=line, icon=("ERROR" if state.level == "ERROR" else "INFO") if index == 0 else "BLANK1")


def _wrap(text, width):
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines or [""]


class FOOTAGEN_PT_send(FootagenPanel, bpy.types.Panel):
    bl_label = "Send"
    bl_parent_id = "FOOTAGEN_PT_main"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.footagen
        state = bridge.state
        target = scene_export.scene_target(context.scene)
        layout.label(text=_target_label(target), icon="SEQUENCE")
        layout.prop(settings, "send_mode", expand=True)
        layout.prop(settings, "include_materials")
        col = layout.column(align=True)
        col.enabled = bool(state.connection) and bridge.online()
        col.scale_y = 1.2
        col.operator("footagen.send", icon="EXPORT")
        col.operator("footagen.send_frame", icon="RENDER_STILL")
        move = layout.box()
        move.label(text="Camera move", icon="CAMERA_DATA")
        move.prop(settings, "move_range", expand=True)
        move.prop(settings, "include_subject_motion")
        move.prop(settings, "include_move_render")
        row = move.row()
        row.enabled = bool(state.connection) and bridge.online()
        row.operator("footagen.send_camera_move", icon="ANIM")
        if state.sent:
            box = layout.box()
            for entry in state.sent[:5]:
                status = bridge.STATUS_LABELS.get(entry["status"], entry["status"])
                icon = {"delivered": "CHECKMARK", "expired": "CANCEL", "discarded": "X"}.get(entry["status"], "TIME")
                box.label(text=f"{entry['label']}: {status}", icon=icon)
        layout.operator("footagen.export_file", icon="FILE_3D")


class FOOTAGEN_PT_receive(FootagenPanel, bpy.types.Panel):
    bl_label = "From Footagen"
    bl_parent_id = "FOOTAGEN_PT_main"

    @classmethod
    def poll(cls, _context):
        return bool(bridge.state.connection)

    def draw(self, context):
        layout = self.layout
        state = bridge.state
        if not state.inbox:
            layout.label(text="Nothing waiting")
        for item in state.inbox:
            summary = item.get("summary") or {}
            box = layout.box()
            box.label(text=_target_label(summary.get("target")), icon="SCENE_DATA")
            box.label(text=f"{summary.get('objectCount', 0)} objects · {(item.get('sizeBytes') or 0) / 1e6:.1f} MB")
            row = box.row()
            row.enabled = not jobs.is_busy(f"import-{item['id']}")
            row.operator("footagen.import_transfer", icon="IMPORT").message_id = item["id"]
        layout.prop(context.scene.footagen, "use_footagen_camera")
        row = layout.row()
        row.enabled = not jobs.is_busy("inbox")
        row.operator("footagen.check_inbox", icon="FILE_REFRESH")


class FOOTAGEN_PT_object(FootagenPanel, bpy.types.Panel):
    bl_label = "Object role"
    bl_parent_id = "FOOTAGEN_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        current = scene_export.KIND_ROLES.get(obj.get("footagen_kind"), "AUTO")
        row = layout.row(align=True)
        for role, label in (("AUTO", "Auto"), ("SET_PIECE", "Set"), ("CHARACTER", "Character"), ("PROP", "Prop")):
            row.operator("footagen.set_role", text=label, depress=current == role).role = role
        if "footagen_asset" in obj:
            layout.prop(obj, '["footagen_asset"]', text="Name in Footagen")
        layout.label(text="Footagen suggests matches from the names.", icon="INFO")


CLASSES = (FOOTAGEN_PT_main, FOOTAGEN_PT_send, FOOTAGEN_PT_receive, FOOTAGEN_PT_object)
