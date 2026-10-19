# SPDX-License-Identifier: GPL-3.0-or-later
"""Package format, incoming-data checks and connection storage."""

import io
import os
import re
import stat
import sys
import zipfile

from harness import addon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLB = b"glTF" + bytes(16)
PNG = b"\x89PNG\r\n\x1a\n" + bytes(16)


def expect_error(code, fn, *args, **kwargs):
    FootagenError = addon("client").FootagenError
    try:
        fn(*args, **kwargs)
    except FootagenError as error:
        assert error.code == code, (error.code, str(error))
        return error
    raise AssertionError(f"expected {code}")


def test_version_matches_manifest():
    with open(os.path.join(ROOT, "footagen", "blender_manifest.toml"), encoding="utf-8") as handle:
        manifest_version = re.search(r'^version = "([^"]+)"', handle.read(), re.M).group(1)
    assert addon("version").ADDON_VERSION == manifest_version


def test_package_round_trip_uses_stored_entries_only():
    package = addon("package")
    data = package.build({"format": "footagen-scene/1", "objects": []}, {"scene.glb": GLB, "frame.png": PNG})
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert [info.filename for info in archive.infolist()] == ["manifest.json", "scene.glb", "frame.png"]
    assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
    manifest, parts = package.read(data)
    assert manifest["format"] == "footagen-scene/1"
    assert parts == {"scene.glb": GLB, "frame.png": PNG}


def _zip(entries, compression=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_received_packages_are_data_only():
    package = addon("package")
    manifest = b'{"format":"footagen-scene/1"}'
    expect_error("InvalidPackage", package.read, _zip({"manifest.json": manifest, "run.py": b"import os"}))
    expect_error("InvalidPackage", package.read, _zip({"manifest.json": manifest, "scene.glb": GLB}, zipfile.ZIP_DEFLATED))
    expect_error("InvalidPackage", package.read, _zip({"manifest.json": manifest, "scene.glb": b"<html>"}))
    expect_error("UnsupportedFormat", package.read, _zip({"manifest.json": b'{"format":"footagen-scene/2"}'}))
    expect_error("InvalidPackage", package.read, b"not a zip")
    try:
        package.build({"format": "footagen-scene/1"}, {"evil.py": b""})
    except ValueError:
        pass
    else:
        raise AssertionError("unknown parts must be refused")


def test_incoming_values_are_bounded():
    validate = addon("validate")
    good = validate.manifest_from_footagen({
        "format": "footagen-scene/1",
        "target": {"projectId": "p1", "sceneNumber": 3, "shotId": "s1", "shotLabel": "4A\n\x00"},
        "camera": {"name": "Cam", "position": [0, 1.6, 5], "quaternion": [0, 0, 0, 1.25], "lensMm": 35,
                   "sensor": {"widthMm": 36, "heightMm": 24, "fit": "AUTO"}, "resolution": [1920, 1080]},
        "objects": [{"node": "Table", "footagenId": "obj-1", "roleHint": "evil"}],
    })
    assert good["target"]["sceneNumber"] == "3"
    assert good["target"]["shotLabel"] == "4A"
    assert abs(sum(v * v for v in good["camera"]["quaternion"]) - 1) < 1e-9
    assert good["objects"][0]["roleHint"] is None
    for bad in (
        {"camera": {"position": [0, float("nan"), 0], "quaternion": [0, 0, 0, 1], "lensMm": 35}},
        {"camera": {"position": [0, 1e9, 0], "quaternion": [0, 0, 0, 1], "lensMm": 35}},
        {"camera": {"position": [0, 0, 0], "quaternion": [0, 0, 0, 1], "lensMm": "35; import os"}},
        {"camera": {"position": [0, 0, 0], "quaternion": [0, 0, 0, 1], "lensMm": 35, "sensor": {"fit": "EVAL"}}},
        {"objects": "Table"},
        {"target": ["p1"]},
    ):
        expect_error("InvalidPackage", validate.manifest_from_footagen, bad)


def test_app_address_must_be_https_or_local():
    client = addon("client")
    assert client.check_app_url("https://app.footagen.ai/") == "https://app.footagen.ai"
    assert client.check_app_url("http://localhost:3101") == "http://localhost:3101"
    for bad in ("http://app.footagen.ai", "http://192.168.1.10:3000", "file:///etc/passwd", "ftp://localhost"):
        expect_error("BadAppUrl", client.check_app_url, bad)


def test_connection_file_is_private_and_outside_the_blend_file():
    auth = addon("auth")
    auth.save({"token": "fgb_" + "a" * 43, "apiBase": "http://127.0.0.1:1", "deviceName": "Test"})
    path = auth._path()
    assert auth.load()["deviceName"] == "Test"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    import bpy

    assert not any("fgb_" in str(value) for value in bpy.context.scene.values() if isinstance(value, str))
    auth.forget()
    assert auth.load() is None
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"token": "eyJ.not-a-device-token"}')
    assert auth.load() is None, "only fgb_ device tokens are accepted"
    auth.forget()
