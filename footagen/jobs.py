# SPDX-License-Identifier: GPL-3.0-or-later
"""Network work runs on a worker thread that never touches bpy; its result is
handed back to Blender's main thread through a timer. In background mode
(command line, tests) there is no event loop, so work runs inline."""

import queue
import threading
import traceback

import bpy

_results = queue.Queue()
_busy = set()
_inline = False


def set_inline(value):
    global _inline
    _inline = bool(value)


def is_busy(label):
    return label in _busy


def _redraw():
    wm = getattr(bpy.context, "window_manager", None)
    for window in getattr(wm, "windows", []) or []:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "PROPERTIES"}:
                area.tag_redraw()


def run(label, work, done):
    """`work()` runs off the main thread; `done(result, error)` runs on it."""
    if label in _busy:
        return False
    _busy.add(label)
    if _inline or bpy.app.background:
        try:
            result, error = work(), None
        except Exception as caught:  # noqa: BLE001 - reported to the user below
            result, error = None, caught
        _busy.discard(label)
        done(result, error)
        return True

    def target():
        try:
            _results.put((label, done, work(), None))
        except Exception as caught:  # noqa: BLE001
            traceback.print_exc()
            _results.put((label, done, None, caught))

    threading.Thread(target=target, name=f"footagen-{label}", daemon=True).start()
    return True


def pump():
    """Timer callback: delivers finished work on the main thread."""
    while True:
        try:
            label, done, result, error = _results.get_nowait()
        except queue.Empty:
            break
        _busy.discard(label)
        try:
            done(result, error)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _redraw()
    return 0.2


def register():
    if not bpy.app.timers.is_registered(pump):
        bpy.app.timers.register(pump, first_interval=0.2, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(pump):
        bpy.app.timers.unregister(pump)
    _busy.clear()
