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
- dropping a background on the Fancy Arranger canvas marks the difficulty
  dirty, so Ctrl+S actually writes the new reference instead of silently
  skipping it ("Nothing to save.")
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

    def test_the_overview_bars_emit_one_seek_per_click(self):
        """The click-stutter report. Both bars used to emit on press *and* on
        release at the same pixel; the audio engine coalesces a burst, so the
        duplicate was not dropped but parked, landing 50ms later as a second
        sink teardown and rebuild -- one hiccup just after every click.
        """
        for bar in (gui.TimingOverviewBar(), gui.DensityOverview()):
            with self.subTest(bar=type(bar).__name__):
                bar.resize(400, bar.height())
                bar.duration_ms = 60000
                seeks: list[int] = []
                bar.seek_requested.connect(seeks.append)

                _click(bar, 200.0)
                self.assertEqual(len(seeks), 1, "press and release are one click")

                # A second click on the same pixel is a real request: playback
                # has moved on since, so it must not be swallowed as a repeat.
                _click(bar, 200.0)
                self.assertEqual(len(seeks), 2)

                # A drag that actually moves still seeks per new millisecond.
                bar.mousePressEvent(QMouseEvent(
                    QMouseEvent.Type.MouseButtonPress, QPointF(100.0, 10.0),
                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier))
                before = len(seeks)
                for x in (120.0, 140.0, 160.0):
                    bar.mouseMoveEvent(QMouseEvent(
                        QMouseEvent.Type.MouseMove, QPointF(x, 10.0),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier))
                self.assertEqual(len(seeks) - before, 3, "a real drag still seeks")


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

    def test_no_button_is_narrower_than_the_label_it_draws(self):
        """The pink-square bug, in one assertion.

        A QPushButton fixed narrower than the window stylesheet's 32px of
        horizontal padding has negative room for its text, so Qt paints the
        body over the whole label and the button reads as blank -- which is
        how the Fancy Arranger's +/- buttons went "missing" at a typed 30px
        and 28px. Compared against `button_text_width`, which is glyphs *plus*
        that padding: comparing against the glyphs alone would have let both
        of those through.
        """
        from PySide6.QtWidgets import QPushButton

        clipped = [
            (button.text(), button.width(), gui.button_text_width(button))
            for button in self.window.findChildren(QPushButton)
            if button.text() and button.width() < gui.button_text_width(button)
        ]
        self.assertEqual(clipped, [], f"buttons narrower than their label: {clipped}")

    def test_a_checkable_button_is_measured_in_its_checked_chrome(self):
        """`QPushButton:checked` is `border: 1px solid` against the base rule's
        `border: 0`, so selecting a button costs it two pixels that were never
        in the width it was sized to -- and every tool row's selected button
        clipped the last letter of its own label. Font-independent, unlike the
        weight half of the same rule, so this holds on the offscreen platform
        where every glyph measures the same."""
        from PySide6.QtWidgets import QPushButton

        checkable = QPushButton("Select", self.window)
        checkable.setCheckable(True)
        plain = QPushButton("Select", self.window)
        try:
            self.assertGreater(
                gui.button_chrome_width(checkable), gui.button_chrome_width(plain),
            )
        finally:
            checkable.deleteLater()
            plain.deleteLater()

    def test_a_ragged_row_still_fits_every_label(self):
        """`widths=False` used to leave the width to sizeHint, which Qt takes in
        the font the button *reports* -- 600 even while a checked one is painted
        at 700."""
        from PySide6.QtWidgets import QPushButton

        buttons = [QPushButton(text, self.window) for text in ("Select", "Multiple Fake Slider")]
        for button in buttons:
            button.setCheckable(True)
        try:
            gui.equalize_button_widths(buttons, heights=True, widths=False)
            self.assertNotEqual(
                buttons[0].minimumWidth(), buttons[1].minimumWidth(),
                "widths=False must stay ragged",
            )
            for button in buttons:
                with self.subTest(text=button.text()):
                    self.assertGreaterEqual(
                        button.minimumWidth(), gui.button_text_width(button),
                    )
        finally:
            for button in buttons:
                button.deleteLater()

    def test_all_three_playback_rows_are_sized(self):
        """The gimmick page's row was left out of showEvent and kept its
        build-time hint -- unpadded, and five pixels short of "100%"."""
        rows = (
            [self.window.editor_play_button, *self.window.editor_playback_speed_buttons],
            [self.window.timeline_play_button, *self.window.playback_speed_buttons],
            [self.window.gimmick_play_button, *self.window.gimmick_playback_speed_buttons],
        )
        for row in rows:
            with self.subTest(first=row[1].text()):
                self.assertEqual(len({button.width() for button in row}), 1)
                for button in row:
                    self.assertGreaterEqual(
                        button.width(), gui.button_text_width(button),
                    )

    def test_the_fancy_arranger_step_buttons_show_their_glyphs(self):
        """`ParameterControl` and `DifficultyValueControl` build the +/- pair
        themselves, and both used to type a width smaller than the padding."""
        from PySide6.QtWidgets import QPushButton

        controls = [
            gui.ParameterControl(
                {"key": "x", "label": "X", "type": "int", "min": 0, "max": 10, "default": 1}
            ),
            gui.ParameterControl(
                {"key": "seed", "label": "Seed", "type": "int", "min": 0, "max": 99, "default": 0}
            ),
            gui.DifficultyValueControl("HP", 5.0, "tip"),
        ]
        for control in controls:
            control.setParent(self.window)
            control.show()
        steps = [
            button
            for control in controls
            for button in control.findChildren(QPushButton)
            if button.text() in {"+", "-"}
        ]
        self.assertEqual(len(steps), 6)
        for button in steps:
            with self.subTest(text=button.text()):
                self.assertGreaterEqual(button.width(), gui.button_text_width(button))
        for control in controls:
            control.deleteLater()


class BackgroundDropSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")
        self.image = directory / "external" / "bg.png"
        self.image.parent.mkdir()
        self.image.write_bytes(b"\x89PNG\r\n")

        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()

    def test_drop_marks_the_difficulty_dirty(self):
        self.assertFalse(self.window.state.history.dirty)
        self.window._background_dropped(str(self.image))
        self.assertTrue(self.window.state.history.dirty)

    def test_ctrl_s_writes_the_new_background_reference(self):
        from osu_io.parser import parse_osu

        self.window._background_dropped(str(self.image))
        self.window.save_all_states()

        reparsed = parse_osu(self.path)
        self.assertEqual(gui.extract_background_filename(reparsed), "bg.png")


if __name__ == "__main__":
    unittest.main()
