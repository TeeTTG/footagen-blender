#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Downloads an official Blender release for CI, checks it against the
release's published SHA-256 list and unpacks it. Prints the executable path.

    python3 scripts/fetch_blender.py 5.2.2 .blender"""

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

BASE = "https://download.blender.org/release"
# download.blender.org refuses Python's default User-Agent.
HEADERS = {"User-Agent": "footagen-blender-ci (+https://github.com/TeeTTG/footagen-blender)"}


def asset_name(version):
    system = sys.platform
    if system == "win32":
        return f"blender-{version}-windows-x64.zip"
    if system == "darwin":
        arch = "arm64" if platform.machine() == "arm64" else "x64"
        return f"blender-{version}-macos-{arch}.dmg"
    return f"blender-{version}-linux-x64.tar.xz"


def executable(dest, version):
    if sys.platform == "win32":
        return os.path.join(dest, f"blender-{version}-windows-x64", "blender.exe")
    if sys.platform == "darwin":
        return os.path.join(dest, "Blender.app", "Contents", "MacOS", "Blender")
    return os.path.join(dest, f"blender-{version}-linux-x64", "blender")


def open_url(url, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def download(url, path):
    with open_url(url, 120) as response, open(path, "wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)


def main():
    version, dest = sys.argv[1], os.path.abspath(sys.argv[2])
    exe = executable(dest, version)
    if os.path.exists(exe):
        print(exe)
        return
    os.makedirs(dest, exist_ok=True)
    series = ".".join(version.split(".")[:2])
    name = asset_name(version)
    with open_url(f"{BASE}/Blender{series}/blender-{version}.sha256", 60) as response:
        sums = dict(line.split()[::-1] for line in response.read().decode().splitlines() if line.strip())
    archive = os.path.join(dest, name)
    download(f"{BASE}/Blender{series}/{name}", archive)
    digest = hashlib.sha256()
    with open(archive, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sums.get(name):
        sys.exit(f"Checksum mismatch for {name}")
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as packed:
            packed.extractall(dest)
    elif name.endswith(".tar.xz"):
        with tarfile.open(archive) as packed:
            packed.extractall(dest)
    else:
        mount = os.path.join(dest, "mnt")
        os.makedirs(mount, exist_ok=True)
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount, archive], check=True)
        try:
            subprocess.run(["ditto", os.path.join(mount, "Blender.app"), os.path.join(dest, "Blender.app")], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", mount], check=False)
    os.remove(archive)
    print(exe)


if __name__ == "__main__":
    main()
