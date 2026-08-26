"""M6 gameplay viewer: an osu!taiko-like preview where every object approaches
the hit position at the speed in force at *its own* time.

That per-object rule is the whole feature. A chart view shows notes on a time
axis, where an SV change is invisible by construction; integrating one shared
scroll position instead would be osu!mania's model, in which objects can never
pass each other. So most of these tests pin positions against the full_v14
fixture, whose timing points were written for exactly this kind of arithmetic:

    0     500ms/beat (120 BPM), uninherited
    2000  SV 0.75, inherited, kiai
    4000  SV 2.00, inherited
    6000  400ms/beat (150 BPM), uninherited, omit-first-barline
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

import gui
from osu_io.parser import parse_osu
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


def _wheel(view, delta: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    event = QWheelEvent(
        QPointF(100.0, 50.0), QPointF(100.0, 50.0), QPoint(0, 0), QPoint(0, delta),
        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
    )
    view.wheelEvent(event)


def _viewer(document) -> gui.GameplayViewerView:
    view = gui.GameplayViewerView()
    view.resize(800, 200)
    view.load_document(document)
    return view


class ScrollVelocityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        path = write_fixture(Path(self._temp.name), "full_v14")
        self.view = _viewer(parse_osu(path))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_velocity_is_one_beat_per_beat_length_at_1x(self):
        """Scroll speed is BPM x SV, so one beat covers the same distance at
        every BPM -- that is what makes px-per-beat a constant."""
        self.assertAlmostEqual(self.view.velocity_at(1000.0), 1 / 500.0, places=9)
        # 6000 onward is 150 BPM (400ms/beat), and the red line resets SV.
        self.assertAlmostEqual(self.view.velocity_at(6400.0), 1 / 400.0, places=9)

    def test_sv_scales_the_velocity_of_the_section_it_governs(self):
        self.assertAlmostEqual(self.view.velocity_at(2500.0), 0.75 / 500.0, places=9)
        self.assertAlmostEqual(self.view.velocity_at(4500.0), 2.0 / 500.0, places=9)

    def test_each_object_uses_the_speed_at_its_own_time(self):
        """The osu!taiko rule, and the reason this is not an integral: a note
        in a 2.0x section is twice as far out as one the same time away in a
        1.0x section, whatever lies between them and the playhead."""
        self.view.current_time = 0.0
        hit_x = self.view.width() * gui.GAMEPLAY_HIT_X_RATIO
        slow = self.view.x_for_time(1000.0) - hit_x  # 1.0x
        fast = self.view.x_for_time(4500.0) - hit_x  # 2.0x
        self.assertAlmostEqual(slow, 1000.0 / 500.0 * self.view.px_per_beat, places=6)
        self.assertAlmostEqual(fast, 4500.0 * 2.0 / 500.0 * self.view.px_per_beat, places=6)

    def test_a_green_line_moves_the_note_sitting_on_it(self):
        """'Every note to the right follows this speed, including the current
        note the green line is on.'"""
        self.view.current_time = 0.0
        before = self.view.x_for_time(4000.0)
        point = next(p for p in self.view.timing_points if p.time == 4000.0)
        point.set_sv(point.sv_multiplier * 2)
        self.view._rebuild_velocities()
        self.assertAlmostEqual(self.view.x_for_time(4000.0) - self.view._hit_x(),
                               (before - self.view._hit_x()) * 2, places=6)

    def test_a_green_line_leaves_earlier_notes_alone(self):
        self.view.current_time = 0.0
        before = self.view.x_for_time(1000.0)
        point = next(p for p in self.view.timing_points if p.time == 4000.0)
        point.set_sv(point.sv_multiplier * 2)
        self.view._rebuild_velocities()
        self.assertAlmostEqual(self.view.x_for_time(1000.0), before, places=6)

    def test_the_current_time_sits_on_the_hit_position(self):
        self.view.current_time = 3000.0
        self.assertAlmostEqual(
            self.view.x_for_time(3000.0), self.view.width() * gui.GAMEPLAY_HIT_X_RATIO, places=6
        )

    def test_scroll_speed_only_scales_the_picture(self):
        """Ctrl+wheel must not change *where* in the map the view is, only how
        much of it fits -- velocity is stored in beats for this reason."""
        self.view.current_time = 3000.0
        before = self.view.velocity_at(5000.0)
        self.view.px_per_beat *= 2.0
        self.assertAlmostEqual(self.view.velocity_at(5000.0), before, places=9)
        hit_x = self.view.width() * gui.GAMEPLAY_HIT_X_RATIO
        self.assertAlmostEqual(self.view.x_for_time(3000.0), hit_x, places=6)

    def test_a_zero_beat_length_does_not_divide_by_zero(self):
        """The invisible-note gimmick authors beat_length near zero, and a
        hand-edited map can carry a literal 0."""
        self.view.timing_points[0].beat_length = 0.0
        self.view._rebuild_velocities()
        self.assertTrue(all(velocity > 0 for velocity in self.view._velocities))


class BarlineTests(unittest.TestCase):
    """`barline_times` answers for the frame at `current_time`, so each test
    parks the playhead inside the range it asks about. A section is only walked
    across the time it is on screen for at its own velocity -- without that, one
    absurd-BPM section spends the whole per-frame budget off the left edge."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        path = write_fixture(Path(self._temp.name), "full_v14")
        self.view = _viewer(parse_osu(path))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_a_barline_every_meter_beats_from_an_uninherited_point(self):
        # 500ms/beat, meter 4 -> every 2000ms, up to the next red line at 6000.
        self.view.current_time = 2000.0
        self.assertEqual(self.view.barline_times(0.0, 5999.0), [0.0, 2000.0, 4000.0])

    def test_the_range_is_respected_without_walking_the_whole_section(self):
        self.view.current_time = 3500.0
        self.assertEqual(self.view.barline_times(2500.0, 4500.0), [4000.0])

    def test_omit_first_barline_drops_the_line_on_the_point_only(self):
        """The 6000ms red line sets effects bit 3; barline gimmicks set it in
        bulk, so its later measure lines must still appear."""
        self.view.current_time = 7750.0
        times = self.view.barline_times(6000.0, 9500.0)
        self.assertNotIn(6000.0, times)
        # 400ms/beat, meter 4 -> every 1600ms.
        self.assertEqual(times, [7600.0, 9200.0])

    def test_clearing_the_flag_brings_the_points_own_barline_back(self):
        point = next(p for p in self.view.beat_points if p.time == 6000.0)
        point.set_omit_first_barline(False)
        self.view.current_time = 6000.0
        self.assertIn(6000.0, self.view.barline_times(6000.0, 9500.0))

    def test_a_map_with_no_timing_points_draws_no_barlines(self):
        self.view.beat_points = []
        self.assertEqual(self.view.barline_times(0.0, 10000.0), [])

    def _document(self, *points: gui.TimingPoint):
        document = parse_osu(write_fixture(Path(self._temp.name), "full_v14"))
        document.timing_points = list(points)
        return document

    @staticmethod
    def _green(time_ms: float, sv: float) -> gui.TimingPoint:
        """An inherited point. TimingPoint defaults uninherited_flag to 1, so a
        negative beat_length alone still reads as a red line."""
        return gui.TimingPoint(time=time_ms, beat_length=-100.0 / sv, uninherited_flag=0)

    def test_a_green_line_inside_a_section_does_not_cut_its_barlines_short(self):
        """Spacing was judged from the velocity at the *red* line, but a green
        line partway through moves every measure line after it. At 0.25x the
        lines sit four times closer, so four times as many fit on screen -- the
        old reach and line count were both a quarter of what was needed, and the
        right-hand half of the screen lost its barlines."""
        view = _viewer(
            self._document(gui.TimingPoint(time=0.0, beat_length=500.0), self._green(4000.0, 0.25))
        )
        view.current_time = 10000.0
        times = view.barline_times(*view.visible_time_range())

        # 0.25x/500ms-beat -> 0.1 px/ms, so a 2000ms measure spans 200px and the
        # 800px view reaches thousands of ms past where the old window stopped.
        self.assertIn(12000.0, times, "barlines stop short of the right edge")
        self.assertIn(14000.0, times)
        self.assertIn(16000.0, times)
        for time_ms in times:
            self.assertAlmostEqual(time_ms % 2000.0, 0.0, places=6)

    def test_a_crawling_section_gets_every_line_that_fits_not_a_fixed_few(self):
        """The count is geometry, not a budget. At 0.01x a 2000ms measure is 8px,
        so dozens of barlines are genuinely on screen at once -- the old code
        sized the walk from the red line's velocity and handed back three."""
        view = _viewer(
            self._document(gui.TimingPoint(time=0.0, beat_length=500.0), self._green(1000.0, 0.01))
        )
        view.current_time = 30000.0
        times = view.barline_times(*view.visible_time_range())

        self.assertGreater(len(times), 20, "a 0.01x section fits far more than a handful")
        # Both of these sit within the 800px view; the old walk reached neither.
        self.assertIn(2000.0, times)
        self.assertIn(58000.0, times)
        self.assertEqual(len(times), len(set(times)))


class PaintAndInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        path = write_fixture(Path(self._temp.name), "full_v14")
        self.view = _viewer(parse_osu(path))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_painting_every_note_shape_does_not_raise(self):
        for time_ms in (0.0, 1500.0, 3500.0, 5500.0, 54692.0):
            with self.subTest(time_ms=time_ms):
                self.view.current_time = time_ms
                self.view.grab()

    def test_painting_a_near_stopped_section_does_not_hang(self):
        """A 0.01x section puts an unbounded amount of *time* on one screen;
        the visible range is capped rather than scanning the whole map."""
        self.view.timing_points[1].set_sv(0.01)
        self.view._rebuild_velocities()
        self.view.current_time = 2000.0
        start, end = self.view.visible_time_range()
        self.assertLessEqual(end - start, 2 * gui.GAMEPLAY_MAX_LOOKAHEAD_MS)
        self.view.grab()

    def test_a_barline_gimmick_section_does_not_generate_millions_of_lines(self):
        """Absurd-BPM red lines put a measure every 0.0004ms. Past the right
        edge none of them is visible, and asking for the time range's worth
        froze the view.

        There is no line-count cap to lean on -- the bound is the screen, since
        two lines rounding to the same pixel column are one line."""
        gimmick = [gui.TimingPoint(time=0.0, beat_length=500.0)]
        for i in range(200):
            gimmick.append(gui.TimingPoint(time=1000.0 + i * 5.0, beat_length=0.0001))
        self.view.timing_points = gimmick
        self.view.beat_points = list(gimmick)
        self.view._beat_times = [point.time for point in gimmick]
        self.view._rebuild_velocities()
        self.view.current_time = 1000.0
        start, end = self.view.visible_time_range()
        times = self.view.barline_times(start, end)
        self.assertLessEqual(len(times), self.view.width() + 4)
        self.assertEqual(len(times), len(set(times)))
        self.view.grab()

    def test_a_real_drumroll_is_drawn_under_the_barlines(self):
        """Its body is a band seconds wide. Drawn in with the notes it covered
        every barline and every fake slider stacked on top of it, which is the
        whole picture in a gimmick section."""
        from model.hit_object import HitObject

        document = parse_osu(write_fixture(Path(self._temp.name), "full_v14"))
        document.timing_points = [gui.TimingPoint(time=0.0, beat_length=500.0)]
        # 500ms/beat, meter 4 -> a barline every 2000ms; the drumroll covers it.
        document.hit_objects = [
            HitObject(x=256, y=192, time=1000, type=2, hit_sound=0, extras=("L|624:192", "1", "5000"))
        ]
        view = _viewer(document)
        view.current_time = 4000.0
        image = view.grab().toImage()
        row = view.height() // 2
        barline_x = round(view.x_for_time(4000.0))
        self.assertNotEqual(
            image.pixel(barline_x, row),
            image.pixel(barline_x + 6, row),
            "the barline is hidden under the drumroll body",
        )

    def test_notes_are_blitted_from_the_sprite_cache(self):
        """Stroking an antialiased ellipse per note per frame was most of the
        frame; the cache is what makes a dense map scroll."""
        gui._NOTE_SPRITES.clear()
        self.view.grab()
        self.assertTrue(gui._NOTE_SPRITES, "no note sprite was cached")
        cached = len(gui._NOTE_SPRITES)
        for offset in range(5):
            self.view.current_time = 1000.0 + offset * 16.0
            self.view.grab()
        self.assertEqual(len(gui._NOTE_SPRITES), cached, "sprites are being rebuilt per frame")

    def test_a_wheel_notch_moves_one_snap_division(self):
        self.view.snap_divisor = 4
        self.view.current_time = 1000.0
        _wheel(self.view, -120)
        self.assertAlmostEqual(self.view.current_time, 1125.0, places=3)

    def test_shift_wheel_moves_one_whole_beat(self):
        self.view.snap_divisor = 4
        self.view.current_time = 1000.0
        _wheel(self.view, -120, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.view.current_time, 1500.0, places=3)

    def test_ctrl_wheel_does_not_rescale_the_view(self):
        """The viewer shows the chart at the speed the player sees it at, so
        there is nothing for a zoom gesture to mean here."""
        self.view.px_per_beat = gui.GAMEPLAY_PX_PER_BEAT
        for _ in range(5):
            _wheel(self.view, 120, Qt.KeyboardModifier.ControlModifier)
            _wheel(self.view, -120, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.view.px_per_beat, gui.GAMEPLAY_PX_PER_BEAT)

    def test_ctrl_wheel_does_not_seek(self):
        self.view.current_time = 1000.0
        _wheel(self.view, 120, Qt.KeyboardModifier.ControlModifier)
        self.assertAlmostEqual(self.view.current_time, 1000.0, places=6)


class GameplayViewIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")

        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)
        self.state = self.window.state
        self.window._add_editor_view("gameplay", self.path)

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()

    def _frame(self):
        return next(f for f in self.window._editor_views if f.view_type == "gameplay")

    def test_the_gameplay_view_type_is_a_real_widget_not_a_placeholder(self):
        view = self._frame().gameplay_view
        self.assertIsInstance(view, gui.GameplayViewerView)
        self.assertGreater(len(view.notes), 0)
        self.assertIn(view, self.window._gameplay_views)

    def test_it_follows_the_shared_playhead(self):
        view = self._frame().gameplay_view
        self.window.seek_audio(3000)
        self.assertAlmostEqual(view.current_time, 3000.0, places=6)

    def test_placing_a_note_refreshes_it(self):
        view = self._frame().gameplay_view
        before = len(view.notes)
        self.window._place_note(self.path, "don", 1200.0, False, False)
        self.assertEqual(len(view.notes), before + 1)

    def test_an_sv_edit_refreshes_it(self):
        """A green line moves every note after it in this view, so the
        timing-point refresh path has to reach it too."""
        view = self._frame().gameplay_view
        inherited = next(p for p in self.state.document.timing_points if not p.uninherited)
        before = view.velocity_at(inherited.time + 1.0)
        self.window._edit_sv_point(self.path, inherited.uid, inherited.sv_multiplier * 2)
        self.assertAlmostEqual(view.velocity_at(inherited.time + 1.0), before * 2, places=9)

    def test_it_joins_the_editor_pages_global_snap(self):
        view = self._frame().gameplay_view
        self.window._editor_change_snap_from_wheel(1)
        self.assertEqual(view.snap_divisor, int(self.window.editor_snap_combo.currentData()))

    def test_closing_it_unregisters_it_everywhere(self):
        frame = self._frame()
        view = frame.gameplay_view
        self.window._close_editor_view(frame)
        self.assertNotIn(view, self.window._gameplay_views)
        self.assertNotIn(view, self.window._editor_snap_views)


if __name__ == "__main__":
    unittest.main()
