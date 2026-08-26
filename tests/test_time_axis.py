"""The shared time axis: the snap list, and what a seek does to the playhead."""
from __future__ import annotations

import unittest

from osu_io.timing import TimingPoint
from time_axis import SNAP_DIVISORS, TimeAxisMixin, snap_time


class _Axis(TimeAxisMixin):
    """The smallest host `set_time` needs: it only repaints and reads a clock."""

    def __init__(self, current_time: float = 0.0) -> None:
        self.current_time = current_time
        self.last_rendered_time = -1.0
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


class SnapDivisorTests(unittest.TestCase):
    def test_the_list_is_sorted_so_alt_wheel_steps_one_way(self):
        self.assertEqual(list(SNAP_DIVISORS), sorted(SNAP_DIVISORS))

    def test_one_sixty_fourth_is_offered(self):
        self.assertIn(64, SNAP_DIVISORS)

    def test_a_sixty_fourth_of_a_beat_is_where_it_should_be(self):
        points = [TimingPoint.uninherited_at(0, 180.0)]
        # 180 BPM is a 333.33ms beat, so 1/64 of one is 5.208ms.
        self.assertAlmostEqual(snap_time(points, 5.2, 64), 60000.0 / 180.0 / 64)


class PlayheadPrecisionTests(unittest.TestCase):
    """A snap is rarely a whole millisecond. The playhead is carried as a float
    from the view that moved it out to every other view, so a view adopts what
    it is handed rather than keeping a fraction of its own -- keeping its own
    was what left the six gimmick layers disagreeing about where "now" is, each
    by a different fraction, which at deep zoom is a visible offset per band."""

    def test_a_fractional_position_is_kept(self):
        axis = _Axis(10000.0)
        axis.set_time(10000.4)
        self.assertEqual(axis.current_time, 10000.4)

    def test_a_view_does_not_keep_a_fraction_of_its_own(self):
        axis = _Axis(10000.4)
        axis.set_time(10000.0)
        self.assertEqual(axis.current_time, 10000.0)

    def test_a_real_move_still_lands(self):
        axis = _Axis(10000.4)
        axis.set_time(10333)
        self.assertEqual(axis.current_time, 10333.0)

    def test_playback_still_advances_the_playhead(self):
        axis = _Axis(0.0)
        for position in range(0, 100, 7):
            axis.set_time(position)
        self.assertEqual(axis.current_time, 98.0)

    def test_a_negative_position_is_still_clamped(self):
        axis = _Axis(500.0)
        axis.set_time(-20)
        self.assertEqual(axis.current_time, 0.0)


if __name__ == "__main__":
    unittest.main()
