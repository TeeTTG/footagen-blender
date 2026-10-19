# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs the add-on tests inside Blender without a window.

    blender --background --factory-startup --online-mode --python-exit-code 1 \
        --python tests/run.py -- [--api http://127.0.0.1:8801 --app http://localhost:3101] [--report out.json]

Without --api the tests use tests/mock_relay.py. With --api they use a real
local Footagen API started with NODE_ENV=development and ALLOW_DEV_AUTH=true
(the browser side is played with the development user header).

The add-on must be installed as an extension first (see scripts/ci_test.py),
so the tests exercise the same package a user installs."""

import argparse
import importlib
import json
import os
import sys
import tempfile
import time
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MODULE = "bl_ext.user_default.footagen"


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="", help="A real local Footagen API instead of the mock relay")
    parser.add_argument("--app", default="", help="The local Footagen app serving connect-config.json (with --api)")
    parser.add_argument("--user", default="blender-addon-test-user")
    parser.add_argument("--report", default="")
    parser.add_argument("--only", default="")
    return parser.parse_args(argv)


def enable_addon():
    import addon_utils

    addon_utils.enable(MODULE, default_set=True, persistent=True)
    module = sys.modules.get(MODULE)
    if module is None:
        raise RuntimeError(f"{MODULE} is not installed. Run scripts/ci_test.py, which installs the built zip.")
    return module


def main():
    args = parse_args()
    if not bpy.app.online_access:
        raise RuntimeError("Run Blender with --online-mode for these tests.")
    enable_addon()
    import harness

    harness.configure(args)
    modules = [importlib.import_module(name) for name in ("test_package", "test_export", "test_move", "test_relay")]
    results = []
    for module in modules:
        for name in sorted(dir(module)):
            if not name.startswith("test_") or (args.only and args.only not in f"{module.__name__}.{name}"):
                continue
            harness.reset_scene()
            started = time.time()
            try:
                getattr(module, name)()
                results.append({"test": f"{module.__name__}.{name}", "ok": True, "seconds": round(time.time() - started, 2)})
                print(f"PASS {module.__name__}.{name}")
            except Exception:  # noqa: BLE001 - every failure is reported
                results.append({"test": f"{module.__name__}.{name}", "ok": False, "error": traceback.format_exc()})
                print(f"FAIL {module.__name__}.{name}\n{traceback.format_exc()}")
    harness.shutdown()
    failed = [r for r in results if not r["ok"]]
    summary = {
        "blender": bpy.app.version_string,
        "platform": sys.platform,
        "relay": "footagen" if args.api else "mock",
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
    }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
    print(f"RESULT {summary['passed']} passed, {summary['failed']} failed on Blender {bpy.app.version_string} ({summary['relay']} relay)")
    if failed or not results:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
