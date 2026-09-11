"""Hit-testing a millisecond that carries two timing points.

A converter can deliberately leave a green line stacked on top of one that
was already there (`_convert_to_anti_barline`'s own comment: "the green line
... has to survive even where the map's own timing supplied [one]"), rather
than dedupe it away -- osu! resolves the tie by file order, honouring the
*last* one written. `sorted_by_time` is a stable sort and `InsertTimingPoints`
appends before sorting, so the last of an exact-millisecond tie in any of
these lists is always the one actually in force.

The nearest-point hit tests used to break the tie by picking whichever came
*first* (a strict `<` on the distance comparison never lets a later, equally
close point win), so a click, drag or double-click on such a millisecond
grabbed and displayed the superseded point while the effective one sat
untouched underneath -- "the SV shown is the old one, not the one in force."
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gui
from osu_io.timing import TimingPoint

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class _Document:
    def __init__(self, timing_points, hit_objects=()):
        self.timing_points = timing_points
        self.hit_objects = list(hit_objects)
        self.slider_multiplier = 1.4


class SVEditorViewTieBreakTests(unittest.TestCase):
    def _view(self, old, new):
        view = gui.SVEditorView()
        view.window_ms = 4000.0
        view.current_time = 0.0
        # Insertion order matters, not list order handed in: sorted_by_time
        # is stable, so whichever of these is listed last stays last among
        # the tie once set_timing_points sorts by time alone.
        view.load_document(_Document([old, new]))
        return view

    def test_nearest_inherited_prefers_the_last_of_an_exact_tie(self):
        old = TimingPoint.inherited_at(1000, 1.5)
        new = TimingPoint.inherited_at(1000, 2.5)
        view = self._view(old, new)
        x = view.x_for_time(1000.0)
        found = view._nearest_inherited(x)
        self.assertIsNotNone(found)
        self.assertEqual(found.uid, new.uid, "the second-inserted line is the one osu! honours")

    def test_nearest_point_prefers_the_last_of_an_exact_tie(self):
        old = TimingPoint.inherited_at(1000, 1.5)
        new = TimingPoint.inherited_at(1000, 2.5)
        view = self._view(old, new)
        x = view.x_for_time(1000.0)
        found = view._nearest_point(x)
        self.assertIsNotNone(found)
        self.assertEqual(found.uid, new.uid)

    def test_a_genuinely_closer_point_still_wins_over_an_exact_tie_elsewhere(self):
        """The tie-break only changes behaviour when distances are equal --
        not a way for a farther point to win."""
        far_old = TimingPoint.inherited_at(1000, 1.5)
        near_new = TimingPoint.inherited_at(1002, 2.5)
        view = self._view(far_old, near_new)
        x = view.x_for_time(1002.0)
        found = view._nearest_point(x)
        self.assertEqual(found.uid, near_new.uid)


class TimelineGameplayTieBreakTests(unittest.TestCase):
    def _view(self, old, new, gimmick_times=None):
        view = gui.TimelineGameplay()
        view.window_ms = 4000.0
        view.current_time = 0.0
        view.load_document(_Document([old, new]))
        if gimmick_times is not None:
            view.gimmick_times = gimmick_times
        return view

    def test_timing_point_near_x_prefers_the_last_of_an_exact_tie(self):
        old = TimingPoint.uninherited_at(1000, 120.0)
        new = TimingPoint.uninherited_at(1000, 180.0)
        view = self._view(old, new)
        x = view.x_for_time(1000.0)
        found = view._timing_point_near_x(x)
        self.assertIsNotNone(found)
        self.assertEqual(found.uid, new.uid)

    def test_gimmick_centre_near_x_prefers_the_last_of_an_exact_tie(self):
        old = TimingPoint.uninherited_at(1000, 120.0)
        new = TimingPoint.uninherited_at(1000, 180.0)
        view = self._view(old, new, gimmick_times={1000})
        x = view.x_for_time(1000.0)
        found = view._gimmick_centre_near_x(x)
        self.assertIsNotNone(found)
        self.assertEqual(found.uid, new.uid)


if __name__ == "__main__":
    unittest.main()
