# SPDX-License-Identifier: GPL-3.0-or-later
"""The connection to Footagen: pairing, sending, the inbox and its timers.
Operators call these; tests call them directly in background mode, where
jobs run inline."""

import sys
import time
import urllib.parse
from contextlib import contextmanager

import bpy

from . import auth, camera_move, jobs, package, render, scene_export, scene_import
from .client import Client, ConnectionEnded, FootagenError, check_app_url, discover_api
from .version import ADDON_VERSION

DEFAULT_APP_URL = "https://app.footagen.ai"
INBOX_INTERVAL_SEC = 10.0
SENT_HISTORY = 8

KIND_LABELS = {"scene": "Scene", "frame": "Frame", "camera_move": "Camera move"}
STATUS_LABELS = {
    "waiting": "Waiting for Footagen",
    "delivered": "Delivered",
    "expired": "Expired",
    "discarded": "Discarded in Footagen",
    "uploading": "Sending…",
}


class State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.connection = None  # {token, apiBase, appUrl, deviceId, deviceName}
        self.pairing = None  # {userCode, deviceCode, interval, deadline, url, apiBase, appUrl}
        self.message = ""
        self.level = "INFO"
        self.sent = []  # [{id, kind, status, createdAt}]
        self.inbox = []  # [{id, kind, summary, sizeBytes, createdAt}]
        self.warnings = []
        self.last_inbox_check = 0.0

    def info(self, text):
        self.message, self.level = text, "INFO"

    def error(self, text):
        self.message, self.level = text, "ERROR"


state = State()


def preferences():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def app_url():
    prefs = preferences()
    custom = (getattr(prefs, "app_url", "") or "").strip() if prefs else ""
    return check_app_url(custom) if custom else DEFAULT_APP_URL


def online():
    return bool(bpy.app.online_access)


SYSTEM_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}


def client_info():
    # The computer's network name can be an address or a person's name, so the
    # suggested name uses the operating system; the user renames it on approval.
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    return {
        "deviceName": f"Blender {bpy.app.version_string.split(' ')[0]} on {SYSTEM_LABELS[system]}",
        "appVersion": ADDON_VERSION,
        "hostVersion": bpy.app.version_string.split(" ")[0],
        "platform": system,
    }


def load_connection():
    state.connection = auth.load()


def _client():
    if not state.connection:
        raise FootagenError("Connect Blender to Footagen first.", "NotConnected")
    return Client(state.connection["apiBase"], state.connection["token"], f"FootagenForBlender/{ADDON_VERSION}")


def _require_online():
    if not online():
        raise FootagenError(
            "Online access is off in Blender. Turn on Allow Online Access in Preferences → System → Network, "
            "or use Export for Footagen (.glb).",
            "Offline",
        )


def _handle_error(error, action):
    if isinstance(error, ConnectionEnded):
        auth.forget()
        state.connection = None
        state.inbox = []
        state.error(f"{error} ({action})")
    elif isinstance(error, FootagenError):
        state.error(str(error))
    elif isinstance(error, (ValueError, RuntimeError)):
        state.error(str(error))
    else:
        state.error(f"{action} failed: {error}")


# ---- Pairing ----

def _same_origin(url, app):
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(app)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def open_browser(url):
    if bpy.app.background:
        return
    try:
        bpy.ops.wm.url_open(url=url)
    except RuntimeError:
        import webbrowser

        webbrowser.open(url)


def begin_connect(open_url=True):
    """Starts a pairing. Returns False if one is already starting."""
    _require_online()
    app = app_url()
    info = client_info()

    def work():
        api = discover_api(app)
        return api, Client(api).start_pairing(info)

    def done(result, error):
        if error:
            _handle_error(error, "Connecting")
            return
        api, pairing = result
        url = str(pairing.get("verificationUriComplete") or "")
        if not _same_origin(url, app):
            state.error("Footagen sent an unexpected approval address. Nothing was opened.")
            return
        state.pairing = {
            "userCode": pairing["userCode"],
            "deviceCode": pairing["deviceCode"],
            "interval": max(2, int(pairing.get("interval") or 5)),
            "deadline": time.monotonic() + int(pairing.get("expiresIn") or 600),
            "url": url,
            "apiBase": api,
            "appUrl": app,
        }
        state.info(f"Approve code {pairing['userCode']} in the browser.")
        if open_url:
            open_browser(url)
        if not bpy.app.background and not bpy.app.timers.is_registered(_pairing_timer):
            bpy.app.timers.register(_pairing_timer, first_interval=state.pairing["interval"])

    return jobs.run("connect", work, done)


def cancel_connect():
    state.pairing = None
    state.info("Connection cancelled.")


def poll_pairing_once():
    pairing = state.pairing
    if not pairing:
        return
    if time.monotonic() > pairing["deadline"]:
        state.pairing = None
        state.error("The code expired. Select Connect to Footagen to try again.")
        return

    def work():
        return Client(pairing["apiBase"]).poll_pairing(pairing["deviceCode"])

    def done(result, error):
        if state.pairing is not pairing:
            return
        if error:
            _handle_error(error, "Connecting")
            return
        status = result.get("status")
        if status == "approved" and str(result.get("token", "")).startswith("fgb_"):
            device = result.get("device") or {}
            state.connection = {
                "token": result["token"],
                "apiBase": pairing["apiBase"],
                "appUrl": pairing["appUrl"],
                "deviceId": device.get("id"),
                "deviceName": device.get("name") or "Blender",
            }
            auth.save(state.connection)
            state.pairing = None
            state.info(f"Connected to Footagen as {state.connection['deviceName']}.")
            ensure_inbox_timer()
        elif status == "denied":
            state.pairing = None
            state.error("The connection was declined in Footagen.")
        elif status == "expired":
            state.pairing = None
            state.error("The code expired. Select Connect to Footagen to try again.")

    jobs.run("pair-poll", work, done)


def _pairing_timer():
    if not state.pairing:
        return None
    poll_pairing_once()
    return state.pairing["interval"] if state.pairing else None


def disconnect():
    connection = state.connection
    if not connection:
        return

    def work():
        if online():
            try:
                Client(connection["apiBase"], connection["token"]).disconnect()
            except ConnectionEnded:
                pass
        return True

    def done(_result, error):
        auth.forget()
        state.connection = None
        state.inbox = []
        if error and not isinstance(error, ConnectionEnded):
            state.info("Disconnected in Blender. Remove the device in Footagen Settings → Connected apps as well.")
        else:
            state.info("Disconnected from Footagen.")

    jobs.run("disconnect", work, done)


# ---- Sending ----

def _upload(kind, data, label):
    client = _client()
    entry = {"id": None, "kind": kind, "status": "uploading", "createdAt": time.time(), "label": label}
    state.sent.insert(0, entry)
    del state.sent[SENT_HISTORY:]

    def work():
        return client.send(kind, data)

    def done(result, error):
        if error:
            if entry in state.sent:
                state.sent.remove(entry)
            _handle_error(error, "Sending")
            return
        entry.update(id=result["id"], status=result.get("status", "waiting"))
        state.info(f"{label} sent. Footagen shows it in every open tab of your account.")

    return jobs.run(f"send-{kind}", work, done)


def send_scene(context, mode="SCENE", include_materials=False):
    _require_online()
    _client()
    manifest, parts, warnings = scene_export.collect_scene(context, mode, include_materials)
    state.warnings = warnings
    data = package.build(manifest, parts)
    label = {"SCENE": "Scene", "SELECTION": "Selection", "CAMERA": "Camera"}[mode]
    return _upload("scene", data, label)


def send_frame(context):
    _require_online()
    _client()
    scene = context.scene
    png = render.render_frame_png(context)
    manifest = scene_export.base_manifest(context)
    if scene.camera is not None and scene.camera.type == "CAMERA" and scene.camera.data.type == "PERSP":
        manifest["camera"] = scene_export.camera_manifest(scene, scene.camera)
    manifest["objects"] = []
    data = package.build(manifest, {"frame.png": png})
    return _upload("frame", data, f"Frame {scene.frame_current}")


def send_camera_move(context, use_preview=False, include_subjects=True, include_render=False):
    _require_online()
    _client()
    manifest, parts, warnings = camera_move.collect_move(context, use_preview, include_subjects, include_render)
    state.warnings = warnings
    data = package.build(manifest, parts)
    track = manifest["cameraTrack"]
    seconds = track["samples"][-1]["t"]
    return _upload("camera_move", data, f"Camera move ({seconds:.1f} s)")


# ---- Inbox and transfer status ----

def check_inbox():
    if not state.connection or not online():
        return False
    client = _client()
    waiting = [entry["id"] for entry in state.sent if entry.get("id") and entry["status"] == "waiting"]

    def work():
        inbox = client.inbox()
        statuses = client.sent_status(waiting) if waiting else []
        return inbox, statuses

    def done(result, error):
        state.last_inbox_check = time.monotonic()
        if error:
            if isinstance(error, ConnectionEnded):
                _handle_error(error, "Checking for transfers")
            return  # A failed background check stays quiet until the next one.
        inbox, statuses = result
        state.inbox = [item for item in inbox if item.get("kind") == "scene"]
        by_id = {item["id"]: item["status"] for item in statuses}
        for entry in state.sent:
            if entry.get("id") in by_id:
                entry["status"] = by_id[entry["id"]]

    return jobs.run("inbox", work, done)


def _inbox_timer():
    if not state.connection:
        return None
    if online() and not jobs.is_busy("inbox"):
        check_inbox()
    return INBOX_INTERVAL_SEC


def ensure_inbox_timer():
    if state.connection and not bpy.app.background and not bpy.app.timers.is_registered(_inbox_timer):
        bpy.app.timers.register(_inbox_timer, first_interval=1.0)


@contextmanager
def _window_context():
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm and wm.windows else None)
    if window is None:
        yield
        return
    with bpy.context.temp_override(window=window):
        yield


def import_transfer(message_id, use_camera=True):
    _require_online()
    client = _client()

    def work():
        return client.download(message_id)

    def done(data, error):
        if error:
            _handle_error(error, "Importing")
            return
        try:
            manifest, parts = package.read(data)
            with _window_context():
                summary = scene_import.import_package(bpy.context, manifest, parts, use_camera=use_camera)
        except Exception as caught:  # noqa: BLE001 - shown to the user
            _handle_error(caught, "Importing")
            return
        state.inbox = [item for item in state.inbox if item["id"] != message_id]
        state.info(f"Imported {summary['objects']} objects into “{summary['collection']}”.")

        def acknowledge():
            return client.ack(message_id)

        jobs.run(f"ack-{message_id}", acknowledge, lambda _r, e: e and _handle_error(e, "Confirming the transfer"))

    return jobs.run(f"import-{message_id}", work, done)


def register():
    state.reset()
    load_connection()
    ensure_inbox_timer()


def unregister():
    for timer in (_pairing_timer, _inbox_timer):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    state.reset()
