# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks every value that arrives from Footagen before Blender uses it.
Messages are data: a fixed set of fields with bounded numbers and text.
No bpy here."""

import math

from .client import FootagenError

MAX_TEXT = 200
MAX_COORD = 100000.0
MAX_SAMPLES = 60 * 240


def _bad(field):
    raise FootagenError(f"The package from Footagen has an invalid {field}.", "InvalidPackage")


def text(value, field, max_length=MAX_TEXT, required=False):
    if value is None:
        if required:
            _bad(field)
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        _bad(field)
    value = "".join(ch for ch in str(value) if ch >= " " and ch != "\x7f").strip()
    if required and not value:
        _bad(field)
    return value[:max_length] or None


def number(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _bad(field)
    if value < low or value > high:
        _bad(field)
    return float(value)


def vector(value, field, size=3, limit=MAX_COORD):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        _bad(field)
    return [number(v, field, -limit, limit) for v in value]


def quaternion(value, field):
    q = vector(value, field, 4, 2.0)
    length = math.sqrt(sum(v * v for v in q))
    if length < 0.5 or length > 1.5:
        _bad(field)
    return [v / length for v in q]


def target(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        _bad("target")
    return {
        "projectId": text(value.get("projectId"), "target project", 128),
        "sceneNumber": text(value.get("sceneNumber"), "target scene", 16),
        "shotId": text(value.get("shotId"), "target shot", 128),
        "projectName": text(value.get("projectName"), "project name", 120),
        "shotLabel": text(value.get("shotLabel"), "shot label", 120),
    }


def camera(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        _bad("camera")
    sensor = value.get("sensor") or {}
    if not isinstance(sensor, dict):
        _bad("camera sensor")
    fit = sensor.get("fit", "AUTO")
    if fit not in ("AUTO", "HORIZONTAL", "VERTICAL"):
        _bad("camera sensor fit")
    resolution = value.get("resolution")
    return {
        "name": text(value.get("name"), "camera name", 63) or "Footagen camera",
        "position": vector(value.get("position"), "camera position"),
        "quaternion": quaternion(value.get("quaternion"), "camera rotation"),
        "lensMm": number(value.get("lensMm"), "lens", 1.0, 5000.0),
        "sensor": {
            "widthMm": number(sensor.get("widthMm", 36.0), "sensor width", 1.0, 200.0),
            "heightMm": number(sensor.get("heightMm", 24.0), "sensor height", 1.0, 200.0),
            "fit": fit,
        },
        "resolution": None if resolution is None else [int(number(v, "resolution", 16, 16384)) for v in vector(resolution, "resolution", 2, 16384)],
    }


def manifest_from_footagen(manifest):
    """The parts of an incoming manifest the add-on uses, checked."""
    objects = manifest.get("objects") or []
    if not isinstance(objects, list) or len(objects) > 10000:
        _bad("object list")
    cleaned_objects = []
    for item in objects:
        if not isinstance(item, dict):
            _bad("object")
        kind = item.get("roleHint")
        cleaned_objects.append({
            "node": text(item.get("node"), "object name", 63, required=True),
            "footagenId": text(item.get("footagenId"), "object id", 200),
            "roleHint": kind if kind in ("character", "prop", "setPiece") else None,
        })
    source = manifest.get("source") or {}
    if not isinstance(source, dict):
        _bad("source")
    return {
        "target": target(manifest.get("target")),
        "camera": camera(manifest.get("camera")),
        "objects": cleaned_objects,
        "sourceApp": text(source.get("app"), "source", 32),
        "units": number((manifest.get("units") or {}).get("metersPerUnit", 1.0), "units", 0.0001, 10000.0),
    }
