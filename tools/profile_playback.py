"""Measure the cost of one playback frame, the way stutter is actually caused.

    python tools/profile_playback.py <a real .osu> [frames] [--profile]

The editor renders at 120fps, so every frame has an 8.33ms budget: the clock
advance, the hitsound scan, and a synchronous repaint of every open view. A
frame that overruns is a dropped frame, and dropped frames are the stutter.

Reports the distribution rather than the mean -- stutter is the tail. With
--profile it also prints the cumulative-time hot list for the same run.
"""
from __future__ import annotations

import cProfile
import os
import pstats
import sys
from pathlib import Path
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

import gui

path = Path(sys.argv[1])
frames = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else 400
profile = "--profile" in sys.argv

app = QApplication.instance() or QApplication([])
window = gui.MainWindow()
# Paint cost scales with pixels, and the offscreen platform's default screen
# is far smaller than a real maximized window -- measuring at 758x180 would
# understate every number here by the ratio of the areas.
window.resize(1920, 1080)
window.show()
window._load_map_path(path, refresh_difficulties=True)
app.processEvents()

if "--gimmick" in sys.argv:
    # The heavy layout: the gimmick page opens a band per layer, so the frame
    # cost is multiplied by however many are on screen. Entering it normally
    # asks a question in a dialog; answer it the way the tests do.
    from unittest.mock import patch

    class _Entry:
        CREATE = gui.GimmickEntryDialog.CREATE
        USE_CURRENT = gui.GimmickEntryDialog.USE_CURRENT
        CANCEL = gui.GimmickEntryDialog.CANCEL

        def __init__(self, version, parent=None, references=None): pass
        def exec(self): return 1
        def selected_action(self): return self.USE_CURRENT
        def selected_reference(self): return None
        def deleteLater(self): pass

    with patch.object(gui, "GimmickEntryDialog", _Entry):
        entered = window._enter_gimmick_page()
    if entered:
        window._show_page(gui.PAGE_GIMMICK)
    print(f"     gimmick page entered: {entered}")
    app.processEvents()

state = window.state
print(f"map: {path.name}")
print(f"     {len(state.document.hit_objects)} hit objects, "
      f"{len(state.document.timing_points)} timing points")

views = []
for name in ("_chart_views", "_sv_views", "_gameplay_views", "_density_views", "_timing_bars"):
    group = list(getattr(window, name, ()) or ())
    views.append((name, group))
    print(f"     {name}: {len(group)} ({sum(1 for v in group if v.isVisible())} visible)")


def render_one(position: float) -> None:
    """One frame's worth of work, painted synchronously so it is really done."""
    for view in window._chart_views:
        view.set_time(position, force=True)
    for view in window._sv_views:
        view.set_time(position, force=True)
    for view in window._gameplay_views:
        view.set_time(position, force=True)
    for view in window._density_views:
        view.set_time(position)
        view.set_viewport(position, window.timeline.window_ms)
    for bar in window._timing_bars:
        bar.set_time(position)
        bar.set_viewport(position, window.timeline.window_ms)
    # update() + one processEvents, not repaint() per widget: the app never
    # forces a synchronous per-widget backing-store flush, and measuring that
    # way charges every view its own compositing pass instead of the single
    # coalesced one a real frame gets.
    for _name, group in views:
        for view in group:
            if view.isVisible():
                view.update()
    app.processEvents()


start = float(state.document.hit_objects[0].time)
step = 1000.0 / 120.0

render_one(start)  # warm up caches, JIT-free but Qt has its own first-paint cost

times = []
if profile:
    profiler = cProfile.Profile()
    profiler.enable()
for index in range(frames):
    at = start + index * step
    began = perf_counter()
    render_one(at)
    times.append((perf_counter() - began) * 1000.0)
if profile:
    profiler.disable()

# Per view: which of them actually costs the frame. Same positions, one view
# at a time, so an expensive one cannot hide behind the others.
print()
print("per view, median paint of 120 frames:")
rows = []
for _name, group in views:
    for view in group:
        if not view.isVisible():
            continue
        each = []
        for index in range(120):
            at = start + index * step
            if hasattr(view, "set_viewport"):
                view.set_time(at)
                view.set_viewport(at, window.timeline.window_ms)
            else:
                view.set_time(at, force=True)
            began = perf_counter()
            view.repaint()
            each.append((perf_counter() - began) * 1000.0)
        each.sort()
        rows.append((each[len(each) // 2], type(view).__name__,
                     view.width(), view.height(), getattr(view, "gimmick_layer", "")))
for median, name, w, h, layer in sorted(rows, reverse=True):
    print(f"  {median:6.2f}ms  {name}{'/' + layer if layer else ''}  {w}x{h}")

times.sort()
def pct(p): return times[min(len(times) - 1, int(len(times) * p))]
over = sum(1 for t in times if t > 8.33)
print(f"\n{frames} frames, 8.33ms budget (120fps)")
print(f"  median {pct(0.5):6.2f}ms   p95 {pct(0.95):6.2f}ms   p99 {pct(0.99):6.2f}ms   max {times[-1]:6.2f}ms")
print(f"  over budget: {over}/{frames} ({100.0 * over / frames:.1f}%)")

if profile:
    print()
    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative").print_stats(14)
    stats.sort_stats("tottime").print_stats(16)
    if "--callers" in sys.argv:
        stats.print_callers("timing.py:66|timing.py:320|uninherited_points")
