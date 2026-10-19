# SPDX-License-Identifier: GPL-3.0-or-later
"""The `footagen-scene/1` package: a ZIP with stored (uncompressed) entries.
No bpy here."""

import io
import json
import zipfile

from .client import FootagenError

FORMAT = "footagen-scene/1"
MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
PARTS = ("manifest.json", "scene.glb", "frame.png", "move.mp4")
_MAGIC = {
    "scene.glb": lambda data: data[:4] == b"glTF",
    "frame.png": lambda data: data[:8] == b"\x89PNG\r\n\x1a\n",
    "move.mp4": lambda data: data[4:8] == b"ftyp",
}


def build(manifest, parts=None):
    """Returns the package bytes. `parts` maps entry name -> bytes."""
    parts = parts or {}
    unknown = set(parts) - set(PARTS)
    if unknown:
        raise ValueError(f"Unknown package parts: {sorted(unknown)}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":"), allow_nan=False))
        for name in PARTS[1:]:
            if name in parts:
                archive.writestr(name, parts[name])
    data = buffer.getvalue()
    if len(data) > MAX_PACKAGE_BYTES:
        raise FootagenError(
            "The package is larger than 50 MB. Send the selection only, leave out materials "
            "or the move render, or simplify heavy objects.",
            "PackageTooLarge",
        )
    return data


def read(data):
    """Opens a received package and returns (manifest, parts). Only data is
    read: nothing in a package is ever executed."""
    if len(data) > MAX_PACKAGE_BYTES:
        raise FootagenError("The received package is too large.", "PackageTooLarge")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise FootagenError("The received package is damaged.", "InvalidPackage") from error
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(set(names)) != len(names) or any(name not in PARTS for name in names):
            raise FootagenError("The received package contains unexpected files.", "InvalidPackage")
        total = sum(info.file_size for info in infos)
        if total > MAX_PACKAGE_BYTES or any(info.compress_type != zipfile.ZIP_STORED for info in infos):
            raise FootagenError("The received package is not a Footagen package.", "InvalidPackage")
        parts = {name: archive.read(name) for name in names}
    raw_manifest = parts.pop("manifest.json", None)
    if raw_manifest is None or len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise FootagenError("The received package has no manifest.", "InvalidPackage")
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise FootagenError("The received package manifest is damaged.", "InvalidPackage") from error
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise FootagenError("This package needs a newer add-on. Update Footagen for Blender.", "UnsupportedFormat")
    for name, check in _MAGIC.items():
        if name in parts and not check(parts[name]):
            raise FootagenError(f"{name} in the received package is not the expected file type.", "InvalidPackage")
    return manifest, parts
