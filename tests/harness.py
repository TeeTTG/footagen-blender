# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test setup: the relay (mock or real), the browser side of Footagen,
and known Blender scenes."""

import json
import math
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bpy
import requests
from mathutils import Euler, Vector

import mock_relay

ADDON = "bl_ext.user_default.footagen"
_server = None
_config_server = None
relay = None  # the mock's in-memory state, None against a real API
app_url = ""
api_url = ""
user = ""
storage_dir = tempfile.mkdtemp(prefix="footagen-test-token-")


def addon(name):
    return sys.modules[f"{ADDON}.{name}"]


def _serve_config(api):
    """A stand-in for the Footagen app's connect-config.json."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = json.dumps({"format": "footagen-connect/1", "apiBaseUrl": api}).encode()
            self.send_response(200 if self.path == "/connect-config.json" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def configure(args):
    global _server, _config_server, relay, app_url, api_url, user
    user = args.user
    if args.api:
        api_url = args.api.rstrip("/")
        if args.app:
            app_url = args.app.rstrip("/")
        else:
            _config_server, app_url = _serve_config(api_url)
    else:
        _server, relay, api_url = mock_relay.start()
        app_url = api_url
    addon("auth").set_storage_dir(storage_dir)
    addon("jobs").set_inline(True)
    prefs = bpy.context.preferences.addons[ADDON].preferences
    prefs.app_url = app_url


def shutdown():
    for server in (_server, _config_server):
        if server:
            server.shutdown()


# ---- The browser side of Footagen ----

def _headers():
    # x-test-user: mock relay. x-user-id: a real development API (dev auth).
    return {"x-test-user": user, "x-user-id": user}


def web(method, path, **kwargs):
    headers = {**_headers(), **kwargs.pop("headers", {})}
    response = requests.request(method, f"{api_url}{path}", headers=headers, timeout=60, **kwargs)
    return response


def approve(user_code, approve=True, name="Blender test device"):
    response = web("POST", "/v1/connect/pair/approve", json={"userCode": user_code, "approve": approve, "deviceName": name})
    assert response.status_code == 200, response.text
    return response.json()


def web_inbox():
    response = web("GET", "/v1/connect/web/messages")
    assert response.status_code == 200, response.text
    return response.json()["messages"]


def web_collect(message_id, claim="test-tab"):
    claimed = web("POST", f"/v1/connect/web/messages/{message_id}/claim", json={"claimId": claim})
    assert claimed.status_code == 200, claimed.text
    content = web("GET", f"/v1/connect/web/messages/{message_id}/content", params={"claimId": claim})
    assert content.status_code == 200, content.text
    acked = web("POST", f"/v1/connect/web/messages/{message_id}/ack", json={"claimId": claim})
    assert acked.status_code == 200 and acked.json()["delivered"] is True, acked.text
    return content.content


def web_send(device_id, kind, data):
    response = web("POST", "/v1/connect/web/messages", params={"deviceId": device_id, "kind": kind},
                   data=data, headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 201, response.text
    return response.json()["message"]


def web_sent():
    response = web("GET", "/v1/connect/web/sent")
    assert response.status_code == 200, response.text
    return response.json()["messages"]


def revoke(device_id):
    response = web("DELETE", f"/v1/connect/devices/{device_id}")
    assert response.status_code == 200, response.text
    return response.json()


def connect():
    """Pairs the add-on through the real flow: start, approve in 'the browser', poll."""
    bridge = addon("bridge")
    bridge.state.reset()
    addon("auth").forget()
    bridge.begin_connect(open_url=False)
    assert bridge.state.pairing, bridge.state.message
    approve(bridge.state.pairing["userCode"])
    bridge.poll_pairing_once()
    assert bridge.state.connection, bridge.state.message
    return bridge.state.connection


# ---- Known scenes ----

def reset_scene():
    """Empties the file without reloading it (a reload would also reset the
    preferences and disable the add-on)."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for store in (bpy.data.meshes, bpy.data.cameras, bpy.data.materials, bpy.data.images, bpy.data.curves, bpy.data.lights):
        for block in list(store):
            if block.users == 0:
                store.remove(block)
    scene = bpy.context.scene
    if "footagen_target" in scene:
        del scene["footagen_target"]
    scene.camera = None
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.frame_start, scene.frame_end, scene.frame_current = 1, 48, 1
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.resolution_percentage = 100
    scene.render.fps, scene.render.fps_base = 24, 1.0


def _add(kind, name, location, rotation=(0, 0, 0), scale=(1, 1, 1)):
    ops = {
        "cube": bpy.ops.mesh.primitive_cube_add,
        "sphere": bpy.ops.mesh.primitive_uv_sphere_add,
        "cylinder": bpy.ops.mesh.primitive_cylinder_add,
        "plane": bpy.ops.mesh.primitive_plane_add,
        "cone": bpy.ops.mesh.primitive_cone_add,
    }
    ops[kind](location=location, rotation=rotation)
    obj = bpy.context.active_object
    obj.scale = scale
    obj.name = name
    obj.data.name = name
    return obj


def build_known_scene():
    """Five named objects and a 35 mm camera, as in the plan's acceptance test."""
    objects = {
        "Floor": _add("plane", "Floor", (0, 0, 0), scale=(4, 4, 1)),
        "Table": _add("cube", "Table", (1.0, 0.5, 0.4), rotation=(0, 0, math.radians(30)), scale=(0.8, 0.5, 0.4)),
        "Anna": _add("cylinder", "Anna", (-0.6, 0.2, 0.85), scale=(0.25, 0.25, 0.85)),
        "Lamp": _add("cone", "Lamp", (1.4, 1.3, 1.0), rotation=(math.radians(10), 0, 0), scale=(0.2, 0.2, 0.3)),
        "Ball": _add("sphere", "Ball", (0.3, -0.8, 0.15), scale=(0.15, 0.15, 0.15)),
    }
    objects["Anna"]["footagen_kind"] = "character"
    objects["Anna"]["footagen_asset"] = "Anna"
    objects["Lamp"]["footagen_kind"] = "prop"
    camera_data = bpy.data.cameras.new("Cam_A")
    camera_data.lens = 35.0
    camera_data.sensor_width = 36.0
    camera_data.sensor_fit = "AUTO"
    camera = bpy.data.objects.new("Cam_A", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = Vector((0.0, -5.0, 1.6))
    camera.rotation_euler = Euler((math.radians(88.0), 0.0, math.radians(4.0)))
    bpy.context.scene.camera = camera
    bpy.context.view_layer.update()
    return objects, camera
