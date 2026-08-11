"""Regression coverage for a round of owner feedback on the Editor page:

- clicking inside an Editor-page (symmetric) chart view must not seek the
  shared playhead, while the Fancy Arranger timeline's click-to-seek stays
- the M3 note tool row is global (applies to whichever chart view has
  focus), not duplicated per view
- slider/spinner get distinct rendering (yellow brush / skin image)
- Fancy Arranger's transform-controls panel is wide enough for its button
  text, and it has its own timing bar + density chart again (a second
  instance of each widget type, since a QWidget can only live in one
  layout at a time -- see self._timing_bars / self._density_views)
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

import gui
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


def _click(view: gui.TimelineGameplay, x: float, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(x, 100), button, button, Qt.KeyboardModifier.NoModifier,
    )
    view.mousePressEvent(press)
    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, QPointF(x, 100), button, button, Qt.KeyboardModifier.NoModifier,
    )
    view.mouseReleaseEvent(release)


class ClickToSeekTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")

        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()

    def test_editor_chart_view_click_does_not_seek(self):
        frame = next(f for f in self.window._editor_views if f.view_type == "chart")
        view = frame.chart_view
        view.resize(800, 200)
        view.window_ms = 2000.0
        view.current_time = 5000.0
        self.assertTrue(view.symmetric)

        seeks: list[int] = []
        view.seek_requested.connect(seeks.append)

        _click(view, view.x_for_time(6000.0))

        self.assertEqual(seeks, [], "Editor-page click must not request a seek")

    def test_fancy_arranger_timeline_click_still_seeks(self):
        timeline = self.window.timeline
        timeline.resize(800, 200)
        timeline.window_ms = 2000.0
        timeline.current_time = 5000.0
        self.assertFalse(timeline.symmetric)

        seeks: list[int] = []
        timeline.seek_requested.connect(seeks.append)

        _click(timeline, timeline.x_for_time(6000.0))

        self.assertEqual(len(seeks), 1, "Fancy Arranger click-to-seek must be unchanged")


class SliderSpinnerRenderingTests(unittest.TestCase):
    def test_slider_brush_is_distinct_from_don_and_kat(self):
        view = gui.TimelineGameplay()
        self.assertNotEqual(view.slider_brush, view.don_brush)
        self.assertNotEqual(view.slider_brush, view.kat_brush)

    def test_spinner_pixmap_loads(self):
        pixmap = gui.spinner_pixmap()
        self.assertFalse(pixmap.isNull(), "assets/skins/spinner.png must load")

    def test_paint_with_slider_and_spinner_does_not_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "audio.mp3").write_bytes(b"\x00")
            path = write_fixture(directory, "full_v14")  # includes a fake slider

            from osu_io.parser import parse_osu
            document = parse_osu(path)

        view = gui.TimelineGameplay()
        view.set_symmetric(True)
        view.resize(400, 200)
        view.load_document(document)
        view.grab()  # forces a paintEvent; must not raise


class FancyArrangerPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")

        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()

    def test_transform_controls_panel_is_wide_enough_for_its_button_text(self):
        self.assertGreaterEqual(self.window.transform_controls_panel.minimumWidth(), 460)

    def test_fancy_arranger_has_its_own_timing_bar_and_density(self):
        self.assertIsInstance(self.window.fancy_timing_bar, gui.TimingOverviewBar)
        self.assertIsInstance(self.window.fancy_density, gui.DensityOverview)
        self.assertIsNot(self.window.fancy_timing_bar, self.window.timing_bar)
        self.assertIn(self.window.fancy_timing_bar, self.window._timing_bars)
        self.assertIn(self.window.fancy_density, self.window._density_views)

    def test_activating_a_difficulty_loads_both_timing_bars_and_fancy_density(self):
        self.assertEqual(self.window.fancy_timing_bar.duration_ms, self.window.timing_bar.duration_ms)
        self.assertGreater(self.window.fancy_density.duration_ms, 1)

    def test_add_view_defaults_to_the_difficulty_being_edited(self):
        entries = [("Muzukashii", Path("m.osu")), ("Oni", Path("o.osu")), ("Inner", Path("i.osu"))]
        dialog = gui.AddViewDialog(entries, self.window, Path("o.osu"))
        self.assertEqual(dialog.selected_difficulty_path(), Path("o.osu"))
        dialog.deleteLater()

    def test_add_view_falls_back_to_the_first_difficulty(self):
        entries = [("Muzukashii", Path("m.osu")), ("Oni", Path("o.osu"))]
        dialog = gui.AddViewDialog(entries, self.window)
        self.assertEqual(dialog.selected_difficulty_path(), Path("m.osu"))
        dialog.deleteLater()

    def test_view_chrome_buttons_are_wide_enough_for_their_glyph(self):
        """The window sheet's 8px 16px button padding is wider than the 28px
        these used to be fixed at, so ✕ and 🔒 were clipped away entirely."""
        frames = self.window.findChildren(gui.EditorViewFrame)
        self.assertTrue(frames)
        for frame in frames:
            for button in (frame.close_button, frame.lock_button):
                self.assertGreaterEqual(button.width(), button.sizeHint().width())

    def test_every_tool_button_is_the_same_width(self):
        """Both rows, not each: they swap into the same spot, so a per-label
        width made the whole row shift on every switch."""
        buttons = [
            *self.window.tool_buttons.values(),
            self.window.new_combo_button,
            *self.window.sv_tool_buttons.values(),
        ]
        widths = {button.width() for button in buttons}
        self.assertEqual(len(widths), 1, f"tool buttons differ in width: {widths}")
        # Wide enough for the longest label, not clipped to the shortest.
        self.assertGreaterEqual(
            widths.pop(), max(button.sizeHint().width() for button in buttons)
        )


if __name__ == "__main__":
    unittest.main()
