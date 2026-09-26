"""The Fancy Arranger page: osu!'s proportions on the canvas, and the page's
boxes as docks that can be moved, floated, closed and brought back.
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
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class CanvasProportionTests(unittest.TestCase):
    """`OsuPlayfieldAdjustmentContainer`: 512x384 is 80% of a 640x480 frame,
    centred, 8 osu!pixels low -- osu!stable's (64, 56). The canvas used to
    stretch the playfield to the full height, 25% larger than osu! draws it."""

    def setUp(self) -> None:
        self.canvas = gui.TransformCanvas()
        self.canvas.resize(1624, 924)  # a 1600x900 render area inside the 12px margin
        self.canvas._update_view_geometry()
        self.render = self.canvas.render_rect
        self.playfield = self.canvas.playfield_rect
        self.scale = self.render.height() / 480.0

    def test_the_render_area_is_16_9(self):
        self.assertAlmostEqual(self.render.width() / self.render.height(), 16 / 9, places=6)

    def test_the_playfield_is_80_percent_of_the_height(self):
        self.assertAlmostEqual(self.playfield.height() / self.render.height(), 0.8, places=6)
        self.assertAlmostEqual(self.playfield.width() / self.playfield.height(), 4 / 3, places=6)

    def test_it_sits_at_osu_stables_64_56(self):
        frame_left = self.render.center().x() - self.render.height() * 4 / 3 / 2
        self.assertAlmostEqual(self.playfield.left() - frame_left, 64 * self.scale, places=4)
        self.assertAlmostEqual(self.playfield.top() - self.render.top(), 56 * self.scale, places=4)

    def test_notes_map_through_the_same_scale(self):
        self.assertAlmostEqual(self.canvas.view_scale, self.scale, places=6)

    def test_circle_size_is_osus(self):
        self.assertAlmostEqual(gui.osu_circle_radius(5.0), 32.0, places=6)
        self.assertAlmostEqual(gui.osu_circle_radius(7.0), 23.04, places=6)


class _Window(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")
        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)
        self.window._show_page(gui.PAGE_FANCY)

    def tearDown(self) -> None:
        self.window._reset_fancy_layout()
        self.window.close()
        self._temp.cleanup()


class DockTests(_Window):
    def test_the_canvas_is_the_centre_and_the_rest_are_docks(self):
        docks = self.window.fancy_docks
        self.assertIs(docks.centralWidget(), self.window.canvas)
        self.assertEqual(docks.dockWidgetArea(self.window.fancy_transform_dock), Qt.RightDockWidgetArea)
        self.assertEqual(docks.dockWidgetArea(self.window.fancy_timeline_dock), Qt.BottomDockWidgetArea)

    def test_the_timeline_dock_holds_the_existing_timeline(self):
        dock = self.window.fancy_timeline_dock
        for widget in (self.window.timeline, self.window.fancy_timing_bar, self.window.fancy_density,
                       self.window.snap_combo):
            self.assertTrue(dock.isAncestorOf(widget))

    def test_the_layout_comes_back_next_time(self):
        docks = self.window.fancy_docks
        docks.addDockWidget(Qt.TopDockWidgetArea, self.window.fancy_timeline_dock)
        self.window.settings.set_value("fancy/dock_state", docks.saveState())
        second = gui.MainWindow()
        try:
            self.assertEqual(
                second.fancy_docks.dockWidgetArea(second.fancy_timeline_dock), Qt.TopDockWidgetArea)
        finally:
            second._reset_fancy_layout()
            second.close()

    def test_reset_puts_everything_back(self):
        docks = self.window.fancy_docks
        docks.addDockWidget(Qt.LeftDockWidgetArea, self.window.fancy_transform_dock)
        self.window.fancy_timeline_dock.hide()
        self.window._reset_fancy_layout()
        self.assertEqual(docks.dockWidgetArea(self.window.fancy_transform_dock), Qt.RightDockWidgetArea)
        self.assertFalse(self.window.fancy_timeline_dock.isHidden())

    def test_a_floating_dock_still_gets_the_windows_wheel_snap(self):
        """The app-wide filter drops events from other top-level windows, and a
        floating dock is one. Its parent chain still reaches this window."""
        self.window.fancy_timeline_dock.setFloating(True)
        timeline = self.window.timeline
        before = timeline.snap_divisor
        centre = QPointF(timeline.width() / 2, timeline.height() / 2)
        QApplication.sendEvent(timeline, QWheelEvent(
            centre, QPointF(timeline.mapToGlobal(centre.toPoint())), QPoint(0, 0), QPoint(0, 120),
            Qt.NoButton, Qt.AltModifier, Qt.NoScrollPhase, False))
        self.assertNotEqual(timeline.snap_divisor, before)

    def test_the_layout_menu_lists_both_docks_and_a_reset(self):
        menu = self.window.fancy_layout_button.menu()
        self.window._fill_fancy_layout_menu(menu)
        texts = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(len(texts), 3)


class GlideTests(unittest.TestCase):
    def test_a_note_is_drawn_between_its_old_place_and_its_target(self):
        """The target is what callers and hit tests read; the drawing eases
        to it. Tested on the arithmetic because the offscreen platform runs
        with animation off."""
        from types import SimpleNamespace
        canvas = gui.TransformCanvas()
        note = SimpleNamespace(original_index=7, x=0, y=0)
        canvas.positions = {7: (100.0, 40.0)}
        canvas._glide_from = {7: (0.0, 0.0)}
        canvas._glide_t = 0.25
        self.assertEqual(canvas._drawn_position(note), (25.0, 10.0))
        canvas._glide_t = 1.0
        self.assertEqual(canvas._drawn_position(note), (100.0, 40.0))
        canvas.deleteLater()


class FancyButtonTests(_Window):
    def test_the_duplicate_save_button_is_gone(self):
        self.assertFalse(hasattr(self.window, "apply_original_button"))

    def test_export_says_what_it_does(self):
        self.assertEqual(self.window.export_button.text(), "Export as new difficulty…")

    def test_the_transform_button_counts_its_notes(self):
        self.window._selection_changed({0, 1, 2})
        self.assertEqual(self.window.apply_button.text(), "Transform 3 notes")
        self.window._selection_changed(set())
        self.assertEqual(self.window.apply_button.text(), "Transform Selected Notes")

    def test_the_mode_pills_drive_the_mode(self):
        self.window.mode_buttons["split"].click()
        self.assertTrue(self.window._is_split_mode())
        self.assertTrue(self.window.mode_buttons["split"].isChecked())
        self.assertFalse(self.window.mode_buttons["all"].isChecked())
        self.window.mode_buttons["all"].click()
        self.assertFalse(self.window._is_split_mode())


if __name__ == "__main__":
    unittest.main()
