#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds the extension zip, installs it into a throw-away Blender user folder
and runs tests/run.py in that Blender without a window.

    python3 scripts/ci_test.py --blender /path/to/blender [--api http://127.0.0.1:8801] [--report out.json]

Your own Blender preferences and add-ons are never touched: the user folder is
temporary (BLENDER_USER_RESOURCES)."""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(command, env):
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", required=True)
    parser.add_argument("--api", default="")
    parser.add_argument("--app", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--only", default="")
    parser.add_argument("--keep", action="store_true", help="Keep the temporary user folder")
    args = parser.parse_args()

    user_resources = tempfile.mkdtemp(prefix="footagen-blender-user-")
    dist = os.path.join(ROOT, "dist")
    env = {**os.environ, "BLENDER_USER_RESOURCES": user_resources}
    try:
        for old in glob.glob(os.path.join(dist, "footagen-*.zip")):
            os.remove(old)
        run([args.blender, "--factory-startup", "--command", "extension", "build",
             "--source-dir", os.path.join(ROOT, "footagen"), "--output-dir", dist], env)
        package = glob.glob(os.path.join(dist, "footagen-*.zip"))[0]
        run([args.blender, "--factory-startup", "--command", "extension", "validate", package], env)
        run([args.blender, "--factory-startup", "--command", "extension", "install-file",
             "-r", "user_default", package], env)
        command = [args.blender, "--background", "--factory-startup", "--online-mode",
                   "--python-exit-code", "1", "--python", os.path.join(ROOT, "tests", "run.py"), "--"]
        if args.api:
            command += ["--api", args.api]
        if args.app:
            command += ["--app", args.app]
        if args.report:
            command += ["--report", os.path.abspath(args.report)]
        if args.only:
            command += ["--only", args.only]
        run(command, env)
        print(f"Package: {package}")
    finally:
        if args.keep:
            print(f"User folder kept: {user_resources}")
        else:
            shutil.rmtree(user_resources, ignore_errors=True)


if __name__ == "__main__":
    main()
