# Footagen for Blender

Send scenes, cameras and rendered frames between Blender and
[Footagen](https://app.footagen.ai), and bring Footagen's 3D Blocker layouts
back into Blender.

The add-on is deliberately thin. It moves Blender's own data: objects through
Blender's glTF exporter, the camera's raw lens and sensor values, and frames
rendered with your own render settings. All of Footagen's work (the script,
Shot List, characters, colour coding, AI composition and prompts) stays in
Footagen.

## What it does

- **Connect to Footagen.** Pairs Blender with your Footagen account with a
  short code, like signing in on a TV. No password is typed into Blender.
- **Send to Footagen.** The whole scene, the selection or only the camera.
  Footagen shows *Scene from Blender arrived* in your open tabs and imports it
  into the 3D Blocker, with each object's role (set piece, character, prop).
- **Send frame.** Renders the current frame through the scene camera and sends
  it as the shot's composition image.
- **Send camera move.** Sends the camera exactly as Blender plays it on every
  frame of the scene or preview range — after constraints, rigs, drivers and
  F-curve modifiers such as noise — including lens changes. Footagen makes it the
  shot's camera move. **Include subject motion** adds how characters and props
  move (position and facing); **Include move render** adds Blender's own render
  of the move (at most 720 p) as a motion reference. Up to 60 seconds.
- **From Footagen.** Scenes sent with *Send to Blender* in Footagen's 3D Blocker
  arrive in the Footagen tab; *Import* puts them into their own collection and
  can make the Footagen camera the scene camera. A newer version replaces the
  older one in that collection only.
- **Export for Footagen (.glb).** Works without a connection or an account:
  import the file in the 3D Blocker with **Import 3D scene…**.

Footagen's 3D Blocker matches Blender's camera on a 36 mm full-frame sensor, so
a 35 mm lens in Blender is a 35 mm lens in Footagen.

## Install

Requires Blender 4.5 LTS or newer.

1. Download `footagen-<version>.zip` from the
   [releases](https://github.com/TeeTTG/footagen-blender/releases).
2. In Blender: **Edit → Preferences → Get Extensions**, open the menu (⌄) at the
   top right and choose **Install from Disk…**, then pick the zip.
3. Make sure **Allow Online Access** is on (**Preferences → System → Network**).
   Without it the add-on never connects; **Export for Footagen** still works.
4. In the 3D Viewport press **N** and open the **Footagen** tab.

## Connecting

1. Select **Connect to Footagen**. Your browser opens Footagen and Blender shows
   a code such as `KQ4D-7ZPX`.
2. In Footagen, check that the code matches and select **Connect**.
3. Blender shows **Connected**. The connection lasts 90 days after its last use.

To end it, select **Disconnect** in Blender, or **Disconnect** next to the
device in Footagen under **Settings → Connected apps**. Either takes effect at
once.

## Privacy and security

- Transfers wait on Footagen's servers in the EU only until your Footagen app or
  Blender collects them, and for at most 24 hours. They are not used to train
  AI models.
- The connection token is stored in the add-on's own user folder, readable only
  by you. It is never written into a `.blend` file or Blender's preferences, so
  sharing those never shares your connection. The token works only for
  transfers: it cannot generate, spend credits or read your projects.
- The add-on never runs code it receives. Everything from Footagen is checked
  as plain data (see [PROTOCOL.md](PROTOCOL.md)).
- It sends nothing on its own: every transfer is a button you press. It has no
  updater of its own; updates come through Blender's extension system.

Security software that inspects HTTPS traffic can block the connection. If
Blender reports that a secure connection could not be made, allow Blender to
connect directly.

## Development

```
python3 scripts/ci_test.py --blender /path/to/blender
```

builds the extension with `blender --command extension build`, validates it,
installs it into a temporary Blender user folder (your own preferences are not
touched) and runs `tests/run.py` without a window against
`tests/mock_relay.py`. To test against a local Footagen started with
`NODE_ENV=development ALLOW_DEV_AUTH=true`:

```
python3 scripts/ci_test.py --blender /path/to/blender --api http://127.0.0.1:8801 --app http://localhost:3101
```

GitHub Actions runs the suite on Windows, Linux and macOS with Blender 4.5 LTS
and the latest LTS. The protocol is in [PROTOCOL.md](PROTOCOL.md).

To point the add-on at a local Footagen while developing, open the add-on's
preferences, turn on **Developer settings** and set the app address
(`localhost` or `127.0.0.1` only).

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE). Footagen itself is a separate
service and is not covered by this licence.
