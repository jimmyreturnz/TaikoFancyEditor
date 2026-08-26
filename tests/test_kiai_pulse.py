"""Kiai flash: notes inside a kiai section brighten on every 1/1 beat.

Two properties beyond "it paints": the flash is gated on the *note's* kiai
state (not the playhead's), and stacked objects amplify it. A fake slider
dropped on top of a note is the normal way to make one note read brighter than
its neighbours, and it works here because each object paints its own overlay --
nothing counts the stack.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gui
from model.hit_object import HitObject
from osu_io.parser import parse_osu
from osu_io.timing import TimingPoint
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


def _brightness(view, half: int = 34) -> int:
    """Summed channel value of the box around the hit position."""
    image = view.grab().toImage()
    centre_x = round(view.width() * gui.GAMEPLAY_HIT_X_RATIO)
    centre_y = view.height() // 2
    total = 0
    for x in range(centre_x - half, centre_x + half):
        for y in range(centre_y - half, centre_y + half):
            pixel = image.pixel(x, y)
            total += (pixel & 0xFF) + ((pixel >> 8) & 0xFF) + ((pixel >> 16) & 0xFF)
    return total


class PulseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.points = [TimingPoint(time=0.0, beat_length=500.0)]

    def test_it_is_full_on_the_beat_and_fades_to_the_next(self):
        self.assertAlmostEqual(gui.beat_pulse(self.points, 2000.0), 1.0, places=9)
        self.assertAlmostEqual(gui.beat_pulse(self.points, 2250.0), 0.5, places=9)
        self.assertAlmostEqual(gui.beat_pulse(self.points, 2499.0), 0.002, places=9)

    def test_a_gimmick_beat_length_does_not_strobe(self):
        """0.0001ms per beat would flash several times per frame."""
        self.assertEqual(gui.beat_pulse([TimingPoint(time=0.0, beat_length=0.0001)], 1234.5), 0.0)

    def test_kiai_reads_the_green_lines_too(self):
        """The fixture switches kiai on with an inherited point at 2000 and off
        with the next one at 4000."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        document = parse_osu(write_fixture(Path(temp.name), "full_v14"))
        points = gui.sorted_by_time(document.timing_points)
        self.assertFalse(gui.in_kiai(points, 1500.0))
        self.assertTrue(gui.in_kiai(points, 2500.0))
        self.assertFalse(gui.in_kiai(points, 4500.0))
        # Uninherited points alone cannot answer this, which is why the flash
        # lives in the viewer -- the only view that keeps the full list.
        self.assertFalse(gui.in_kiai(gui.uninherited_points(points), 2500.0))


class FlashRenderTests(unittest.TestCase):
    """Rendered brightness, since the flash is only ever an overlay."""

    def _viewer(self, kiai: bool, stack: int = 1, *, kiai_from: float = 0.0, playhead: float = 2000.0):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        document = parse_osu(write_fixture(Path(temp.name), "full_v14"))
        document.timing_points = [TimingPoint(time=0.0, beat_length=500.0)]
        if kiai:
            document.timing_points.append(
                TimingPoint(time=kiai_from, beat_length=-100.0, uninherited_flag=0, effects=1)
            )
        document.hit_objects = [
            HitObject(x=256, y=192, time=2000, type=1, hit_sound=0) for _ in range(stack)
        ]
        view = gui.GameplayViewerView()
        view.resize(800, 200)
        view.load_document(document)
        view.current_time = playhead  # on the beat, so the flash is at full
        return view

    def test_a_kiai_note_on_the_beat_is_brighter_than_the_same_note_outside(self):
        self.assertGreater(_brightness(self._viewer(True)), _brightness(self._viewer(False)))

    def test_stacked_objects_amplify_it(self):
        """Three fake sliders on one note: each overlay lands on the last."""
        self.assertGreater(_brightness(self._viewer(True, stack=3)), _brightness(self._viewer(True)))

    def test_it_starts_only_once_the_playhead_reaches_the_section(self):
        """A kiai note already on screen stays plain until the playhead gets
        there -- kiai starts when you arrive at it, not when you can see it."""
        approaching = self._viewer(True, kiai_from=1900.0, playhead=1000.0)
        plain = self._viewer(False, playhead=1000.0)
        self.assertEqual(_brightness(approaching), _brightness(plain))
        # ...and once it arrives, the same note flashes.
        self.assertGreater(
            _brightness(self._viewer(True, kiai_from=1900.0, playhead=2000.0)),
            _brightness(self._viewer(False, playhead=2000.0)),
        )

    def test_a_stack_outside_kiai_does_not_brighten(self):
        """Only kiai amplifies. Stacking translucent note fills does darken the
        spot slightly -- that is the existing note rendering, not the flash."""
        self.assertLessEqual(_brightness(self._viewer(False, stack=3)), _brightness(self._viewer(False)))

    def test_the_chart_editor_does_not_flash(self):
        """Gameplay viewer only: the chart view is for editing, and a pulsing
        note there fights the selection and snap colours.

        It does *tint* -- every layer draws a flat orange band behind a kiai
        section, which is the point: it says which sections are choruses without
        moving. So the test is that the picture does not change with the beat,
        not that kiai is invisible."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        def brightness(kiai: bool, band: bool = True) -> int:
            document = parse_osu(write_fixture(Path(temp.name), "full_v14"))
            document.timing_points = [TimingPoint(time=0.0, beat_length=500.0, effects=1 if kiai else 0)]
            document.hit_objects = [HitObject(x=256, y=192, time=2000, type=1, hit_sound=0)]
            view = gui.TimelineGameplay()
            view.resize(800, 200)
            view.load_document(document)
            view.current_time = 2000.0
            if not band:
                view.kiai_bands = []
            image = view.grab().toImage()
            return sum(
                (image.pixel(x, y) & 0xFF) + ((image.pixel(x, y) >> 8) & 0xFF) + ((image.pixel(x, y) >> 16) & 0xFF)
                for x in range(370, 430)
                for y in range(0, view.height())
            )

        # With the band taken away there is nothing left that kiai changes --
        # no flash on the beat, which is the whole claim.
        self.assertEqual(brightness(True, band=False), brightness(False, band=False))
        # ...and the band itself is the only difference, so it does show.
        self.assertNotEqual(brightness(True), brightness(False))

    def test_the_chart_editor_marks_a_kiai_section(self):
        """The band is background information and is drawn in every layer, so a
        kiai section reads as one wherever you are looking."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        document = parse_osu(write_fixture(Path(temp.name), "full_v14"))
        document.timing_points = [
            TimingPoint(time=0.0, beat_length=500.0, effects=1),
            TimingPoint(time=3000.0, beat_length=-100.0, uninherited_flag=0, effects=0),
        ]
        view = gui.TimelineGameplay()
        view.load_document(document)
        self.assertIn((0, 3000), view.kiai_bands)

        sv_view = gui.SVEditorView()
        sv_view.load_document(document)
        self.assertIn((0, 3000), sv_view.kiai_bands)


if __name__ == "__main__":
    unittest.main()
