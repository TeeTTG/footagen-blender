# SPDX-License-Identifier: GPL-3.0-or-later
"""The whole trip through the relay: pair, Blender -> Footagen, Footagen ->
Blender, frames, offline mode and ending the connection. Runs against the
mock relay in CI and against a real local Footagen API with --api."""

import json

import bpy

import harness
from harness import addon
from test_export import NAMES, footagen_package


def test_pairing_gives_a_device_token_kept_outside_blender():
    connection = harness.connect()
    assert connection["token"].startswith("fgb_")
    assert connection["deviceName"] == "Blender test device"
    assert addon("auth").load()["token"] == connection["token"]
    assert "Connected to Footagen" in addon("bridge").state.message
    # The token reaches the relay and nothing else.
    for path in ("/v1/me", "/v1/projects"):
        response = harness.web("GET", path, headers={"Authorization": f"Bearer {connection['token']}"})
        assert response.status_code == 403, (path, response.status_code, response.text)


def test_denied_pairing_gives_nothing():
    bridge = addon("bridge")
    bridge.state.reset()
    addon("auth").forget()
    bridge.begin_connect(open_url=False)
    harness.approve(bridge.state.pairing["userCode"], approve=False)
    bridge.poll_pairing_once()
    assert bridge.state.connection is None and bridge.state.pairing is None
    assert "declined" in bridge.state.message
    assert addon("auth").load() is None


def test_blender_scene_reaches_footagen_and_status_turns_delivered():
    bridge = addon("bridge")
    harness.connect()
    harness.build_known_scene()
    bridge.send_scene(bpy.context, "SCENE")
    entry = bridge.state.sent[0]
    assert entry["status"] == "waiting", bridge.state.message
    inbox = [m for m in harness.web_inbox() if m["id"] == entry["id"]]
    assert inbox and inbox[0]["kind"] == "scene" and inbox[0]["summary"]["objectCount"] == 5
    manifest, parts = addon("package").read(harness.web_collect(entry["id"]))
    assert {o["node"] for o in manifest["objects"]} == NAMES
    assert manifest["camera"]["lensMm"] == 35.0 and parts["scene.glb"][:4] == b"glTF"
    bridge.check_inbox()
    assert bridge.state.sent[0]["status"] == "delivered"


def test_footagen_scene_reaches_blender_and_is_acknowledged():
    bridge = addon("bridge")
    connection = harness.connect()
    data = footagen_package()
    harness.reset_scene()
    sent = harness.web_send(connection["deviceId"], "scene", data)
    bridge.check_inbox()
    assert [item["id"] for item in bridge.state.inbox] == [sent["id"]]
    assert bridge.state.inbox[0]["summary"]["target"]["shotId"] == "shot-4a"
    bridge.import_transfer(sent["id"])
    assert bridge.state.inbox == [], bridge.state.message
    collection = bpy.data.collections["Footagen scene 3 shot 4A"]
    assert sorted(o.name for o in collection.objects if o.type == "MESH") == sorted(NAMES)
    assert bpy.context.scene.camera.name == "Footagen camera"
    assert json.loads(bpy.context.scene["footagen_target"])["projectId"] == "project-1"
    status = [m for m in harness.web_sent() if m["id"] == sent["id"]]
    assert status and status[0]["status"] == "delivered", status


def test_frame_is_sent_as_png():
    bridge = addon("bridge")
    harness.connect()
    harness.build_known_scene()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.cycles.device = "CPU"
    scene.render.resolution_x, scene.render.resolution_y = 64, 36
    bridge.send_frame(bpy.context)
    entry = bridge.state.sent[0]
    assert entry["kind"] == "frame" and entry["status"] == "waiting", bridge.state.message
    manifest, parts = addon("package").read(harness.web_collect(entry["id"]))
    assert parts["frame.png"][:4] == b"\x89PNG"
    assert manifest["camera"]["name"] == "Cam_A"


def test_offline_blender_makes_no_request():
    bridge = addon("bridge")
    harness.connect()
    harness.build_known_scene()
    count = len(harness.relay.requests) if harness.relay else None
    original = bridge.online
    bridge.online = lambda: False
    try:
        try:
            bridge.send_scene(bpy.context, "SCENE")
        except addon("client").FootagenError as error:
            assert error.code == "Offline"
        else:
            raise AssertionError("sending must refuse while online access is off")
        assert bridge.check_inbox() is False
        try:
            bridge.begin_connect(open_url=False)
        except addon("client").FootagenError as error:
            assert error.code == "Offline"
        else:
            raise AssertionError("connecting must refuse while online access is off")
    finally:
        bridge.online = original
    if count is not None:
        assert len(harness.relay.requests) == count, "no request left Blender"


def test_revoking_in_footagen_ends_the_connection_in_blender():
    bridge = addon("bridge")
    connection = harness.connect()
    assert harness.revoke(connection["deviceId"])["revoked"] is True
    bridge.check_inbox()
    assert bridge.state.connection is None, "a revoked token is forgotten"
    assert addon("auth").load() is None
    assert "Connect Blender to Footagen again" in bridge.state.message


def test_disconnect_in_blender_revokes_the_token():
    bridge = addon("bridge")
    connection = harness.connect()
    bridge.disconnect()
    assert bridge.state.connection is None and addon("auth").load() is None
    response = harness.web("GET", "/v1/connect/app/session", headers={"Authorization": f"Bearer {connection['token']}"})
    assert response.status_code == 401, response.text


def test_camera_move_reaches_footagen_with_its_track():
    from test_move import animated_scene

    bridge = addon("bridge")
    harness.connect()
    animated_scene()
    bridge.send_camera_move(bpy.context, include_subjects=True)
    entry = bridge.state.sent[0]
    assert entry["kind"] == "camera_move" and entry["status"] == "waiting", bridge.state.message
    inbox = [m for m in harness.web_inbox() if m["id"] == entry["id"]]
    assert inbox and inbox[0]["kind"] == "camera_move" and inbox[0]["summary"]["hasCameraTrack"] is True
    manifest, _parts = addon("package").read(harness.web_collect(entry["id"]))
    assert len(manifest["cameraTrack"]["samples"]) == 48
    assert [track["node"] for track in manifest["subjectTracks"]] == ["Anna"]
