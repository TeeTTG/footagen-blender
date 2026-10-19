# Footagen connected-app protocol (`footagen-connect/1`)

This is the contract between the add-on and Footagen. The add-on keeps no
Footagen logic: it moves Blender's raw scene data, and Footagen does all
conversion, matching and planning.

All requests use HTTPS against the Footagen API. JSON bodies use
`Content-Type: application/json`; packages use `application/octet-stream`.

## 1. Finding the API

The add-on knows one fixed address, the Footagen app (`https://app.footagen.ai`).
It reads `<app>/connect-config.json`:

```json
{ "format": "footagen-connect/1", "apiBaseUrl": "https://…" }
```

`apiBaseUrl` must be `https`. In development the app address may be
`http://localhost:<port>` or `http://127.0.0.1:<port>`, and then the API must be
local as well.

## 2. Pairing (device authorization, RFC 8628)

1. `POST /v1/connect/pair/start` with
   `{ "app": "blender", "deviceName": "Blender 4.5.7 on studio-mac", "appVersion": "1.0.0", "hostVersion": "4.5.7", "platform": "macos" }`
   → `{ "userCode": "KQ4D-7ZPX", "deviceCode": "…", "verificationUri": "<app>/?connect", "verificationUriComplete": "<app>/?connect=KQ4D7ZPX", "expiresIn": 600, "interval": 5 }`.
   Limited per network (HTTP 429 `TooManyPairings`).
   An optional `"appUrl": "<app>"` names the Footagen address the add-on uses.
   The server puts the approval page on that address when it is one of the
   deployment's own addresses; without it (add-on 1.0.0) it uses
   `https://app.footagen.ai` when the deployment answers there. The server never
   names a foreign address.
2. The add-on opens `verificationUriComplete` only if it is on the same origin
   as `<app>`, shows the code and opens it in the browser.
   The signed-in user compares the code and approves (or denies) in Footagen.
3. The add-on polls `POST /v1/connect/pair/poll` with `{ "deviceCode": "…" }` every
   `interval` seconds:
   - `{ "status": "pending" }` — keep waiting;
   - `{ "status": "approved", "token": "fgb_…", "device": { … } }` — returned **once**;
   - `{ "status": "denied" }` or `{ "status": "expired" }` — stop.

The token starts with `fgb_`. It is valid for 90 days after its last use and only
on `/v1/connect/app/*`. Every other Footagen route refuses it with HTTP 403.
The add-on stores it in its own user folder, never in the `.blend` file or in
Blender's preferences.

## 3. Add-on routes (header `Authorization: Bearer fgb_…`)

| Route | Result |
|---|---|
| `GET /v1/connect/app/session` | `{ "device": { "id", "name", … }, "limits": { "maxPackageBytes", "messageTtlHours", "pollIntervalSec" } }` |
| `POST /v1/connect/app/disconnect` | `{ "revoked": true }`; the token stops working at once. |
| `POST /v1/connect/app/messages?kind=scene\|frame\|camera_move` | Body: the package. HTTP 201 `{ "message": { "id", "status": "waiting", … } }` |
| `GET /v1/connect/app/sent?ids=<id>,<id>` | `{ "messages": [{ "id", "status": "waiting\|delivered\|expired\|discarded" }] }` |
| `GET /v1/connect/app/messages` | Packages waiting for this device: `{ "messages": [{ "id", "kind", "sizeBytes", "summary", "createdAt", "expiresAt" }] }` |
| `GET /v1/connect/app/messages/<id>/content` | The package bytes (`application/zip`). |
| `POST /v1/connect/app/messages/<id>/ack` | `{ "delivered": true }`; the server deletes the bytes. |

HTTP 401 `InvalidDeviceToken` means the connection has ended (revoked, expired or
the feature was switched off); the add-on forgets the token. Errors are
`{ "error": "<Code>", "message": "<text for the user>" }`.

Limits: 50 MB per package, 20 waiting packages per device and direction, 60
transfers per hour. A package waits at most 24 hours.

## 4. Package `footagen-scene/1`

A ZIP archive whose entries are **stored without compression**:

| Entry | When |
|---|---|
| `manifest.json` | always |
| `scene.glb` | a scene (`kind=scene`); optional with a camera move |
| `frame.png` | `kind=frame` |
| `move.mp4` | a camera move with a Blender render of the move |

No other entries are allowed. The server checks the archive index, the sizes
and each part's file signature, and does not open the scene.

```json
{
  "format": "footagen-scene/1",
  "createdAt": "2026-10-19T10:12:00Z",
  "source": { "app": "blender", "blenderVersion": "4.5.7", "addonVersion": "1.0.0", "file": "kitchen_v3.blend" },
  "target": { "projectId": "…", "sceneNumber": 3, "shotId": "…" },
  "units": { "metersPerUnit": 1.0, "upAxis": "Y" },
  "frame": { "current": 101, "fps": 24, "start": 101, "end": 245 },
  "camera": {
    "name": "Cam_A",
    "position": [0.0, 1.6, -3.0],
    "quaternion": [0.0, 0.0, 0.0, 1.0],
    "lensMm": 35.0,
    "sensor": { "widthMm": 36.0, "heightMm": 24.0, "fit": "AUTO" },
    "resolution": [1920, 1080],
    "pixelAspect": [1, 1],
    "shift": [0.0, 0.0],
    "clip": [0.1, 1000.0],
    "focus": { "enabled": true, "distanceM": 3.2, "fStop": 2.8 }
  },
  "cameraTrack": {
    "fps": 24,
    "startFrame": 101,
    "samples": [{ "t": 0.0, "position": [0.0, 1.6, -3.0], "quaternion": [0.0, 0.0, 0.0, 1.0], "lensMm": 35.0, "focusDistanceM": 3.2 }]
  },
  "objects": [
    { "node": "Anna_rig", "footagenId": null, "roleHint": "character", "entity": { "kind": "character", "name": "Anna" }, "bbox": { "min": [-0.3, 0.0, -0.2], "max": [0.3, 1.72, 0.2] }, "triangles": 48210 }
  ],
  "subjectTracks": [
    { "node": "Anna_rig", "fps": 24, "samples": [{ "t": 0.0, "position": [0.5, 0.0, 1.0], "yawDeg": 90.0 }] }
  ]
}
```

- Positions and rotations are in glTF space: metres (times `metersPerUnit`),
  Y up, right-handed. Quaternions are `[x, y, z, w]`.
- Lens and sensor values are Blender's raw values; Footagen converts them.
- `target`, `camera`, `cameraTrack`, `objects`, `subjectTracks`, `entity` and
  `footagenId` are optional.
- Objects carry the custom properties `footagen_id`, `footagen_kind`
  (`character`, `prop`, `setPiece`) and `footagen_asset` in the glTF `extras`.
- A package from Footagen to Blender uses the same format; it always has
  `target`, and its objects have `footagenId`.
- New optional fields do not change the version. Removing a field or changing
  its meaning makes it `footagen-scene/2`.

## 5. What the add-on never does

- It never runs code received from the server: messages are data only.
- It never connects when Blender's **Allow Online Access** is off.
- It never sends anything without the user pressing a button.
- It has no updater of its own; updates come through Blender's extension system.
