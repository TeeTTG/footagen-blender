# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the device token lives: a file in the add-on's own user folder,
readable by the owner only. Never in Blender's preferences or in the .blend
file, so sharing a .blend or a preferences file never shares the connection."""

import json
import os
import stat

import bpy

TOKEN_FILE = "connection.json"
_override_dir = None


def set_storage_dir(path):
    """Tests use a temporary folder instead of the real user folder."""
    global _override_dir
    _override_dir = path


def _path():
    folder = _override_dir or bpy.utils.extension_path_user(__package__, create=True)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, TOKEN_FILE)


def load():
    """Returns {token, apiBase, appUrl, deviceId, deviceName} or None."""
    try:
        with open(_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("token", "")).startswith("fgb_"):
        return None
    return data


def save(data):
    path = _path()
    temporary = f"{path}.tmp"
    # Create with owner-only permissions before any secret is written.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(temporary, path)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def forget():
    try:
        os.remove(_path())
    except FileNotFoundError:
        pass
