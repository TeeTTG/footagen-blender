# SPDX-License-Identifier: GPL-3.0-or-later
"""An in-memory stand-in for the Footagen relay, following PROTOCOL.md. CI
runs the add-on against it on Windows, Linux and macOS; the same test suite
also runs against a real local Footagen server (tests/run.py --api)."""

import io
import json
import secrets
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ALPHABET = "BCDFGHJKMNPQRSTVWXZ23456789"
MAX_PACKAGE = 50 * 1024 * 1024
PARTS = {"manifest.json", "scene.glb", "frame.png", "move.mp4"}


class Relay:
    def __init__(self):
        self.lock = threading.Lock()
        self.pairings = {}  # device_code -> dict
        self.devices = {}  # token -> dict
        self.messages = {}  # id -> dict
        self.requests = []  # (method, path) for assertions

    def summary(self, data, kind):
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = [info.filename for info in archive.infolist()]
        if any(name not in PARTS for name in names) or "manifest.json" not in names:
            raise ValueError("InvalidPackage")
        if any(info.compress_type != zipfile.ZIP_STORED for info in archive.infolist()):
            raise ValueError("InvalidPackage")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "footagen-scene/1":
            raise ValueError("UnsupportedFormat")
        if kind == "scene" and "scene.glb" not in names:
            raise ValueError("InvalidPackage")
        if kind == "frame" and "frame.png" not in names:
            raise ValueError("InvalidPackage")
        if "scene.glb" in names and archive.read("scene.glb")[:4] != b"glTF":
            raise ValueError("InvalidPackage")
        if "frame.png" in names and archive.read("frame.png")[:4] != b"\x89PNG":
            raise ValueError("InvalidPackage")
        samples = (manifest.get("cameraTrack") or {}).get("samples") or []
        if kind == "camera_move" and len(samples) < 2:
            raise ValueError("InvalidPackage")
        return {
            "objectCount": len(manifest.get("objects") or []),
            "target": manifest.get("target"),
            "hasScene": "scene.glb" in names,
            "hasFrame": "frame.png" in names,
            "hasCameraTrack": bool(samples),
            "hasMoveRender": "move.mp4" in names,
        }


def public(message):
    return {key: message[key] for key in ("id", "deviceId", "direction", "kind", "sizeBytes", "status", "summary", "createdAt")}


def make_handler(relay, base_url_holder):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def _send(self, status, body=None, raw=None, content_type="application/json"):
            payload = raw if raw is not None else json.dumps(body or {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _error(self, status, code, message="Mock relay error."):
            self._send(status, {"error": code, "message": message})

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _json(self):
            raw = self._body()
            return json.loads(raw) if raw else {}

        def _device(self):
            header = self.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else ""
            device = relay.devices.get(token)
            if not token.startswith("fgb_") or not device or device["revoked"]:
                self._error(401, "InvalidDeviceToken", "This connection has ended. Connect Blender to Footagen again.")
                return None
            return device

        def _user(self):
            user = self.headers.get("x-test-user")
            if not user:
                self._error(401, "Unauthorized")
            return user

        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")

        def do_DELETE(self):
            self.route("DELETE")

        def route(self, method):
            parts = urlsplit(self.path)
            path, query = parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}
            relay.requests.append((method, path))
            with relay.lock:
                self.dispatch(method, path, query)

        def dispatch(self, method, path, query):
            segments = path.strip("/").split("/")
            if path == "/connect-config.json":
                return self._send(200, {"format": "footagen-connect/1", "apiBaseUrl": base_url_holder[0]})
            if path.startswith("/v1/") and self.headers.get("Authorization", "").startswith("Bearer fgb_") \
                    and not path.startswith("/v1/connect/app/"):
                return self._error(403, "DeviceTokenNotAllowed")
            if (method, path) == ("POST", "/v1/connect/pair/start"):
                body = self._json()
                code = "".join(secrets.choice(ALPHABET) for _ in range(8))
                device_code = secrets.token_urlsafe(32)
                relay.pairings[device_code] = {"code": code, "status": "pending", "info": body, "user": None, "name": None}
                return self._send(200, {
                    "userCode": f"{code[:4]}-{code[4:]}", "deviceCode": device_code,
                    "verificationUri": f"{base_url_holder[0]}/connect",
                    "verificationUriComplete": f"{base_url_holder[0]}/connect?code={code}",
                    "expiresIn": 600, "interval": 5,
                })
            if (method, path) == ("POST", "/v1/connect/pair/poll"):
                pairing = relay.pairings.get(self._json().get("deviceCode"))
                if not pairing or pairing["status"] in ("consumed", "expired"):
                    return self._send(200, {"status": "expired"})
                if pairing["status"] != "approved":
                    return self._send(200, {"status": pairing["status"]})
                token = f"fgb_{secrets.token_urlsafe(32)[:43]}"
                device = {"id": str(uuid.uuid4()), "name": pairing["name"], "user": pairing["user"], "revoked": False}
                relay.devices[token] = device
                pairing["status"] = "consumed"
                return self._send(200, {"status": "approved", "token": token, "device": {"id": device["id"], "name": device["name"]}})
            if (method, path) == ("POST", "/v1/connect/pair/approve"):
                user = self._user()
                if not user:
                    return None
                body = self._json()
                code = body.get("userCode", "").replace("-", "").upper()
                pairing = next((p for p in relay.pairings.values() if p["code"] == code and p["status"] == "pending"), None)
                if not pairing:
                    return self._error(404, "PairingNotFound")
                pairing.update(status="approved" if body.get("approve") else "denied", user=user,
                               name=body.get("deviceName") or pairing["info"].get("deviceName") or "Blender")
                return self._send(200, {"status": pairing["status"], "name": pairing["name"]})
            if path.startswith("/v1/connect/app/"):
                device = self._device()
                if not device:
                    return None
                return self.app_route(method, segments[3:], query, device)
            if path.startswith("/v1/connect/web/") or path.startswith("/v1/connect/devices/"):
                user = self._user()
                if not user:
                    return None
                return self.web_route(method, segments[2:], query, user)
            return self._error(404, "NotFound")

        def _store(self, device, direction, kind, data):
            if kind not in ("scene", "frame", "camera_move"):
                return self._error(400, "InvalidKind")
            if len(data) > MAX_PACKAGE:
                return self._error(413, "PackageTooLarge")
            try:
                summary = relay.summary(data, kind)
            except (ValueError, zipfile.BadZipFile) as error:
                return self._error(400, str(error) if str(error) else "InvalidPackage")
            message = {
                "id": str(uuid.uuid4()), "deviceId": device["id"], "user": device["user"], "direction": direction,
                "kind": kind, "sizeBytes": len(data), "status": "waiting", "summary": summary,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "data": data, "claim": None,
            }
            relay.messages[message["id"]] = message
            return self._send(201, {"message": public(message)})

        def app_route(self, method, rest, query, device):
            if (method, rest) == ("GET", ["session"]):
                return self._send(200, {"device": {"id": device["id"], "name": device["name"]},
                                        "limits": {"maxPackageBytes": MAX_PACKAGE, "messageTtlHours": 24, "pollIntervalSec": 10}})
            if (method, rest) == ("POST", ["disconnect"]):
                device["revoked"] = True
                return self._send(200, {"revoked": True})
            if (method, rest) == ("POST", ["messages"]):
                return self._store(device, "to_web", query.get("kind"), self._body())
            if (method, rest) == ("GET", ["messages"]):
                waiting = [public(m) for m in relay.messages.values()
                           if m["deviceId"] == device["id"] and m["direction"] == "to_app" and m["status"] == "waiting"]
                return self._send(200, {"messages": waiting})
            if (method, rest) == ("GET", ["sent"]):
                ids = [i for i in query.get("ids", "").split(",") if i]
                rows = [public(m) for m in relay.messages.values()
                        if m["deviceId"] == device["id"] and m["direction"] == "to_web" and (not ids or m["id"] in ids)]
                return self._send(200, {"messages": rows})
            if len(rest) == 3 and rest[0] == "messages":
                message = relay.messages.get(rest[1])
                if not message or message["deviceId"] != device["id"] or message["direction"] != "to_app" or message["status"] != "waiting":
                    return self._error(404, "TransferNotFound")
                if (method, rest[2]) == ("GET", "content"):
                    return self._send(200, raw=message["data"], content_type="application/zip")
                if (method, rest[2]) == ("POST", "ack"):
                    message.update(status="delivered", data=None)
                    return self._send(200, {"delivered": True})
            return self._error(404, "NotFound")

        def web_route(self, method, rest, query, user):
            if method == "DELETE" and rest[0] == "devices":
                for device in relay.devices.values():
                    if device["id"] == rest[1] and device["user"] == user:
                        device["revoked"] = True
                        return self._send(200, {"revoked": True})
                return self._send(200, {"revoked": False})
            rest = rest[1:]  # after "web"
            if (method, rest) == ("GET", ["messages"]):
                waiting = [public(m) for m in relay.messages.values()
                           if m["user"] == user and m["direction"] == "to_web" and m["status"] == "waiting" and not m["claim"]]
                return self._send(200, {"enabled": True, "messages": waiting})
            if (method, rest) == ("POST", ["messages"]):
                device = next((d for d in relay.devices.values() if d["id"] == query.get("deviceId") and d["user"] == user and not d["revoked"]), None)
                if not device:
                    return self._error(404, "DeviceNotFound")
                return self._store(device, "to_app", query.get("kind"), self._body())
            if (method, rest) == ("GET", ["sent"]):
                rows = [public(m) for m in relay.messages.values() if m["user"] == user and m["direction"] == "to_app"]
                return self._send(200, {"messages": rows})
            if len(rest) == 3 and rest[0] == "messages":
                message = relay.messages.get(rest[1])
                if not message or message["user"] != user or message["direction"] != "to_web" or message["status"] != "waiting":
                    return self._error(404, "TransferNotFound")
                if (method, rest[2]) == ("POST", "claim"):
                    claim = self._json().get("claimId")
                    if message["claim"] and message["claim"] != claim:
                        return self._error(409, "AlreadyCollected")
                    message["claim"] = claim
                    return self._send(200, {"message": public(message)})
                if (method, rest[2]) == ("GET", "content"):
                    if message["claim"] != query.get("claimId"):
                        return self._error(404, "TransferNotFound")
                    return self._send(200, raw=message["data"], content_type="application/zip")
                if (method, rest[2]) == ("POST", "ack"):
                    if message["claim"] != self._json().get("claimId"):
                        return self._send(200, {"delivered": False})
                    message.update(status="delivered", data=None)
                    return self._send(200, {"delivered": True})
            return self._error(404, "NotFound")

    return Handler


def start():
    """Starts the mock on a free local port. Returns (server, relay, base_url)."""
    relay = Relay()
    holder = [""]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(relay, holder))
    holder[0] = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, relay, holder[0]
