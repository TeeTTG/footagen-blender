# SPDX-License-Identifier: GPL-3.0-or-later
"""Imports a package from Footagen into its own collection. Only data is read;
the glTF goes through Blender's own importer. Main thread only."""

import json
import os
import re
import tempfile

import bpy

from . import validate
from .scene_export import TO_GLTF

COLLECTION_PREFIX = "Footagen"


def collection_name(target):
    if not target:
        return f"{COLLECTION_PREFIX} import"
    parts = [COLLECTION_PREFIX]
    if target.get("sceneNumber"):
        parts.append(f"scene {target['sceneNumber']}")
    label = target.get("shotLabel") or (target.get("shotId") or "")[:8]
    if label:
        parts.append(f"shot {label}")
    return re.sub(r"\s+", " ", " ".join(parts))[:63]


def _layer_collection(layer, collection):
    if layer.collection == collection:
        return layer
    for child in layer.children:
        found = _layer_collection(child, collection)
        if found:
            return found
    return None


def _managed_collection(scene, name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    collection["footagen_managed"] = True
    return collection


def _clear(collection):
    """A new version replaces the previous one; objects elsewhere are never touched."""
    for obj in list(collection.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            for store in (bpy.data.meshes, bpy.data.cameras, bpy.data.curves):
                if data.name in store and store[data.name] == data:
                    store.remove(data)
                    break


def _apply_camera(scene, camera_object, camera):
    """Footagen's lens on a 36 mm full-frame sensor, and the shot's frame size."""
    data = camera_object.data
    data.type = "PERSP"
    data.lens_unit = "MILLIMETERS"
    data.sensor_fit = camera["sensor"]["fit"]
    data.sensor_width = camera["sensor"]["widthMm"]
    data.sensor_height = camera["sensor"]["heightMm"]
    data.lens = camera["lensMm"]
    data.shift_x = data.shift_y = 0.0
    if camera.get("resolution"):
        scene.render.resolution_x, scene.render.resolution_y = camera["resolution"]
        scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1.0
    from mathutils import Matrix, Quaternion, Vector

    x, y, z, w = camera["quaternion"]
    pose = Matrix.Translation(Vector(camera["position"])) @ Quaternion((w, x, y, z)).to_matrix().to_4x4()
    camera_object.matrix_world = TO_GLTF.inverted() @ pose


def import_package(context, manifest, parts, use_camera=True):
    """Returns a short summary for the user."""
    info = validate.manifest_from_footagen(manifest)
    glb = parts.get("scene.glb")
    if not glb:
        raise ValueError("This transfer has no scene to import.")
    scene = context.scene
    target = info["target"]
    collection = _managed_collection(scene, collection_name(target))
    _clear(collection)

    before = set(bpy.data.objects)
    folder = tempfile.mkdtemp(prefix="footagen-")
    path = os.path.join(folder, "scene.glb")
    previous_active = context.view_layer.active_layer_collection
    try:
        with open(path, "wb") as handle:
            handle.write(glb)
        layer = _layer_collection(context.view_layer.layer_collection, collection)
        if layer:
            context.view_layer.active_layer_collection = layer
        bpy.ops.import_scene.gltf(filepath=path)
    finally:
        context.view_layer.active_layer_collection = previous_active
        try:
            os.remove(path)
            os.rmdir(folder)
        except OSError:
            pass

    imported = [obj for obj in bpy.data.objects if obj not in before]
    for obj in imported:
        for owner in list(obj.users_collection):
            if owner != collection:
                owner.objects.unlink(obj)
        if obj.name not in collection.objects:
            collection.objects.link(obj)

    camera_object = next((obj for obj in imported if obj.type == "CAMERA"), None)
    if camera_object is not None:
        camera_object.name = "Footagen camera"
        if info["camera"]:
            _apply_camera(scene, camera_object, info["camera"])
        if use_camera:
            scene.camera = camera_object
    if target:
        scene["footagen_target"] = json.dumps({k: v for k, v in target.items() if v is not None})
    meshes = [obj for obj in imported if obj.type == "MESH"]
    return {
        "collection": collection.name,
        "objects": len(meshes),
        "camera": camera_object.name if camera_object else None,
        "target": target,
    }
