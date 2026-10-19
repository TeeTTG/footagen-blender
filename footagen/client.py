# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP client for the Footagen relay (PROTOCOL.md). No bpy here: these
functions run on a worker thread, and Blender's data must not be touched
outside the main thread."""

import json
import urllib.parse

import requests

TIMEOUT = (10, 60)
UPLOAD_TIMEOUT = (10, 300)
CONNECT_FORMAT = "footagen-connect/1"
LOCAL_HOSTS = ("localhost", "127.0.0.1")


class FootagenError(Exception):
    """An error with a message meant for the user."""

    def __init__(self, message, code="Error", status=0):
        super().__init__(message)
        self.code = code
        self.status = status


class ConnectionEnded(FootagenError):
    """The device token no longer works; the add-on forgets it."""


def _is_local(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in LOCAL_HOSTS


def check_app_url(url):
    """Only the Footagen app over HTTPS, or a local development app."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme == "https" and parts.hostname:
        return f"https://{parts.netloc}"
    if parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
        return f"http://{parts.netloc}"
    raise FootagenError("The Footagen address must be https, or localhost for development.", "BadAppUrl")


def _raise_for(response):
    if response.status_code < 400:
        return
    try:
        body = response.json()
    except ValueError:
        body = {}
    message = body.get("message") or f"Footagen answered with HTTP {response.status_code}."
    code = body.get("error") or "HttpError"
    if response.status_code == 401 and code == "InvalidDeviceToken":
        raise ConnectionEnded(message, code, response.status_code)
    raise FootagenError(message, code, response.status_code)


def _request(method, url, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        response = requests.request(method, url, **kwargs)
    except requests.exceptions.SSLError as error:
        raise FootagenError(
            "A secure connection to Footagen could not be made. Security software that inspects "
            "HTTPS traffic can cause this; allow Blender to connect directly.",
            "TlsError",
        ) from error
    except requests.exceptions.RequestException as error:
        raise FootagenError("Footagen could not be reached. Check the internet connection.", "NetworkError") from error
    _raise_for(response)
    return response


def discover_api(app_url):
    """Reads <app>/connect-config.json and returns the API base URL."""
    app = check_app_url(app_url)
    response = _request("GET", f"{app}/connect-config.json", headers={"Accept": "application/json"})
    try:
        config = response.json()
    except ValueError as error:
        raise FootagenError("This Footagen address does not offer connected apps.", "NoConnectConfig") from error
    api = str(config.get("apiBaseUrl") or "").rstrip("/")
    if config.get("format") != CONNECT_FORMAT or not api:
        raise FootagenError("This Footagen version does not support this add-on. Update the add-on.", "UnsupportedVersion")
    parts = urllib.parse.urlsplit(api)
    if _is_local(app):
        if parts.scheme not in ("http", "https") or parts.hostname not in LOCAL_HOSTS:
            raise FootagenError("A local Footagen app must use a local API.", "BadApiUrl")
    elif parts.scheme != "https" or not parts.hostname:
        raise FootagenError("Footagen announced an insecure API address.", "BadApiUrl")
    return api


class Client:
    """One Footagen API base; `token` is the device token once paired."""

    def __init__(self, api_base, token=None, user_agent="FootagenForBlender"):
        self.api = api_base.rstrip("/")
        self.token = token
        self.user_agent = user_agent

    def _headers(self, extra=None, auth=True):
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def _json(self, method, path, body=None, auth=True):
        response = _request(
            method,
            f"{self.api}{path}",
            data=None if body is None else json.dumps(body),
            headers=self._headers({"Content-Type": "application/json"} if body is not None else None, auth),
        )
        try:
            return response.json()
        except ValueError as error:
            raise FootagenError("Footagen sent an unexpected answer.", "BadResponse") from error

    # Pairing
    def start_pairing(self, client_info):
        return self._json("POST", "/v1/connect/pair/start", {"app": "blender", **client_info}, auth=False)

    def poll_pairing(self, device_code):
        return self._json("POST", "/v1/connect/pair/poll", {"deviceCode": device_code}, auth=False)

    # Device
    def session(self):
        return self._json("GET", "/v1/connect/app/session")

    def disconnect(self):
        return self._json("POST", "/v1/connect/app/disconnect", {})

    def send(self, kind, package_bytes):
        response = _request(
            "POST",
            f"{self.api}/v1/connect/app/messages?kind={urllib.parse.quote(kind)}",
            data=package_bytes,
            headers=self._headers({"Content-Type": "application/octet-stream"}),
            timeout=UPLOAD_TIMEOUT,
        )
        return response.json()["message"]

    def sent_status(self, ids):
        query = urllib.parse.quote(",".join(ids))
        return self._json("GET", f"/v1/connect/app/sent?ids={query}")["messages"]

    def inbox(self):
        return self._json("GET", "/v1/connect/app/messages")["messages"]

    def download(self, message_id):
        response = _request(
            "GET",
            f"{self.api}/v1/connect/app/messages/{urllib.parse.quote(message_id)}/content",
            headers=self._headers({"Accept": "application/zip"}),
            timeout=UPLOAD_TIMEOUT,
        )
        return response.content

    def ack(self, message_id):
        return self._json("POST", f"/v1/connect/app/messages/{urllib.parse.quote(message_id)}/ack", {})
