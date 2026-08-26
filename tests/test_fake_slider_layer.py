"""Layer 2 (fake sliders) fixes: the two rows are separate objects, its red
lines are configurable, and its two tools have a big variant.

Its own module rather than more of `test_gimmick_editor.py`, which builds a
window per test and takes minutes to run whole -- the same reason
`test_gimmick_round6.py` exists.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

import gui
from tests.test_gimmick_editor import _GimmickFixture, _StubTimingLineDialog

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class _Layer2(_GimmickFixture):
    """A gimmick session with a fake slider and a shiny on the same snap.

    One snap, two structures a millisecond apart -- the layer's two rows exist
    precisely for this, and it is where every one of these bugs showed up.
    """

    SNAP = 10000

    def setUp(self) -> None:
        super().setUp()
        self._enter(gui.GimmickEntryDialog.USE_CURRENT)
        self.state = self.window._states[self.window._gimmick_pairing.target]
        self.document = self.state.document
        self.config = self.window._gimmick_config("fake_slider")
        self.layer = self.window._gimmick_views[1].chart_view
        self.layer.resize(800, self.window.GIMMICK_LAYER_HEIGHT)

    def _place_pair(self) -> None:
        self.window._place_gimmick("fake_slider", "regular", self.SNAP)
        self.window._place_gimmick("fake_slider", "shiny", self.SNAP)
        self.layer.current_time = float(self.SNAP)

    @property
    def slider_at(self) -> int:
        return self.SNAP + self.config.fake_slider_offset_ms

    @property
    def shiny_at(self) -> int:
        return self.SNAP + self.config.shiny_offset_ms

    def _rows(self):
        """(upper row y, lower row y) -- fake sliders above, shinies below."""
        baseline = self.layer._baseline_y()
        return self.layer._row_y(baseline, False), self.layer._row_y(baseline, True)

    def _press(self, x: float, y: float) -> QMouseEvent:
        return QMouseEvent(
            QEvent.MouseButtonPress, QPointF(x, y), QPointF(x, y),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        )

    def _sliders_at(self, time_ms):
        return [
            note for note in self.document.hit_objects
            if gui.MainWindow.is_fake_slider(note) and round(note.time) == time_ms
        ]


class RowAwareGrabTests(_Layer2, unittest.TestCase):
    """A grab must resolve to the one object under the cursor.

    The two structures are a millisecond apart, which at this layer's zoom is
    well inside one 20px hit radius, so before the click's y was consulted the
    scan returned whichever came first in the document.
    """

    def test_the_two_structures_are_the_same_pixel_column(self) -> None:
        """The premise: if they were not, none of this would have mattered."""
        self._place_pair()
        distance = abs(
            self.layer.x_for_time(self.slider_at) - self.layer.x_for_time(self.shiny_at)
        )
        self.assertLess(distance, 20.0)

    def test_the_row_outranks_the_nearer_millisecond(self) -> None:
        """Aimed at the shiny's own column, but on the fake slider's row.

        Nearest-x alone answers "the shiny" here, which is the bug: at this
        zoom the two columns are a third of a pixel apart and the row is the
        only thing the cursor can actually aim at.
        """
        self._place_pair()
        upper, lower = self._rows()
        x = self.layer.x_for_time(self.shiny_at)
        self.assertEqual(round(self.layer._note_near_x(x).time), self.shiny_at)
        self.assertEqual(round(self.layer._note_near_x(x, y=upper).time), self.slider_at)
        self.assertEqual(round(self.layer._note_near_x(x, y=lower).time), self.shiny_at)

    def test_a_drag_on_the_fake_slider_leaves_the_shiny_alone(self) -> None:
        self._place_pair()
        upper, _lower = self._rows()
        self.layer.set_tool("select")
        # The shiny's column, the fake slider's row -- see above.
        self.layer.mousePressEvent(self._press(self.layer.x_for_time(self.shiny_at), upper))
        grabbed = set(self.layer._move_note_uids)
        self.layer.releaseMouse()
        shiny_uids = {note.uid for note in self._sliders_at(self.shiny_at)}
        slider_uids = {note.uid for note in self._sliders_at(self.slider_at)}
        self.assertEqual(grabbed, slider_uids)
        self.assertFalse(grabbed & shiny_uids, "the shiny came along for the ride")

    def test_a_drag_on_the_shiny_leaves_the_fake_slider_alone(self) -> None:
        self._place_pair()
        _upper, lower = self._rows()
        self.layer.set_tool("select")
        self.layer.mousePressEvent(self._press(self.layer.x_for_time(self.slider_at), lower))
        grabbed = set(self.layer._move_note_uids)
        self.layer.releaseMouse()
        slider_uids = {note.uid for note in self._sliders_at(self.slider_at)}
        self.assertTrue(grabbed)
        self.assertFalse(grabbed & slider_uids, "the fake slider came along for the ride")

    def test_a_right_click_deletes_the_row_it_landed_on(self) -> None:
        """Right click resolves the same way -- deleting the wrong one of a
        pair is the same bug with a worse ending."""
        self._place_pair()
        upper, _lower = self._rows()
        x = self.layer.x_for_time(self.shiny_at)
        self.layer.mousePressEvent(QMouseEvent(
            QEvent.MouseButtonPress, QPointF(x, upper), QPointF(x, upper),
            Qt.RightButton, Qt.RightButton, Qt.NoModifier,
        ))
        self.assertEqual(self._sliders_at(self.slider_at), [])
        self.assertEqual(len(self._sliders_at(self.shiny_at)), self.config.shiny_count)

    def test_a_view_without_rows_still_ignores_y(self) -> None:
        """Every other view draws one row, so a y anywhere in it must hit."""
        chart = self.window._gimmick_views[0].chart_view
        chart.resize(800, self.window.GIMMICK_LAYER_HEIGHT)
        note = chart.notes[0]
        chart.current_time = float(note.time)
        self.assertIs(chart._note_near_x(chart.x_for_time(note.time), y=1.0), note)


class RedLineDialogTests(_Layer2, unittest.TestCase):
    """Double click a red line in layer 2 to retype it, as in layer 3."""

    def test_the_line_opens_but_is_not_otherwise_editable_here(self) -> None:
        self.assertTrue(self.layer.timing_dialog_enabled)
        self.assertFalse(
            self.layer.timing_edit_enabled,
            "dragging or deleting the line alone would dismantle the structure",
        )

    def _double_click(self, at_ms) -> list:
        seen = []
        self.layer.timing_line_edit_requested.connect(seen.append)
        x = self.layer.x_for_time(at_ms)
        # The window is already connected to this signal and opens the real,
        # modal dialog on it -- see _StubTimingLineDialog.
        _StubTimingLineDialog.accepted = False
        with patch.object(gui, "TimingLineDialog", _StubTimingLineDialog):
            self.layer.mouseDoubleClickEvent(QMouseEvent(
                QEvent.MouseButtonDblClick, QPointF(x, 20.0), QPointF(x, 20.0),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
            ))
        return seen

    def test_double_clicking_a_fake_sliders_line_asks_for_its_bpm(self) -> None:
        self.window._place_gimmick("fake_slider", "regular", self.SNAP)
        self.layer.current_time = float(self.SNAP)
        point = next(
            p for p in self.document.timing_points
            if p.uninherited and round(p.time) == self.slider_at
        )
        self.assertEqual(self._double_click(self.slider_at), [point.uid])

    def test_a_line_this_layer_does_not_draw_is_not_offered(self) -> None:
        """The fixture's own timing at 0 belongs to no fake slider, so it is
        not drawn here -- and a dialog for an invisible line is worse than no
        dialog at all."""
        self.layer.current_time = 0.0
        self.assertEqual(self._double_click(0), [])

    def test_the_edit_is_one_undo_step(self) -> None:
        self.window._place_gimmick("fake_slider", "regular", self.SNAP)
        point = next(
            p for p in self.document.timing_points
            if p.uninherited and round(p.time) == self.slider_at
        )
        _StubTimingLineDialog.accepted = True
        _StubTimingLineDialog.values = {"beat_length": (point.beat_length, 300.0)}
        before = len(self.state.history.undo_stack)
        with patch.object(gui, "TimingLineDialog", _StubTimingLineDialog):
            self.window._edit_timing_line(self.state.source_path, point.uid)
        self.assertEqual(len(self.state.history.undo_stack), before + 1)
        self.assertAlmostEqual(point.bpm, 200.0)


class BigVariantTests(_Layer2, unittest.TestCase):
    """Shift places the big variant, the same bit a Shift-clicked Don gets."""

    def test_a_plain_fake_slider_is_small_without_shift(self) -> None:
        self.window._place_gimmick("fake_slider", "regular", self.SNAP)
        self.assertFalse(self._sliders_at(self.slider_at)[0].is_finisher)

    def test_shift_makes_the_fake_slider_big(self) -> None:
        self.window._place_gimmick("fake_slider", "regular", self.SNAP, True)
        self.assertTrue(self._sliders_at(self.slider_at)[0].is_finisher)

    def test_shift_makes_every_slider_in_a_shiny_big(self) -> None:
        self.window._place_gimmick("fake_slider", "shiny", self.SNAP, True)
        stack = self._sliders_at(self.shiny_at)
        self.assertEqual(len(stack), self.config.shiny_count)
        self.assertTrue(all(note.is_finisher for note in stack))

    def test_shift_does_not_resize_a_don(self) -> None:
        """Don and Kat already spend the finisher bit saying which they are."""
        self.window._place_gimmick("fake_slider", "don", self.SNAP, True)
        self.assertFalse(self._sliders_at(self.slider_at)[0].is_finisher)

    def test_the_views_shift_flag_reaches_the_placement(self) -> None:
        """The layer's signal used to drop `big` on the floor."""
        self.layer.note_place_requested.emit("regular", float(self.SNAP), False, True)
        self.assertTrue(self._sliders_at(self.slider_at)[0].is_finisher)

    def test_the_ghost_previews_the_big_size(self) -> None:
        self.layer.set_tool("regular")
        self.layer.current_time = float(self.SNAP)
        self.layer._hover_time = float(self.SNAP)
        with patch.object(
            gui.QApplication, "keyboardModifiers", staticmethod(lambda: Qt.ShiftModifier)
        ):
            big = self._ghost_radius()
        with patch.object(
            gui.QApplication, "keyboardModifiers", staticmethod(lambda: Qt.NoModifier)
        ):
            small = self._ghost_radius()
        self.assertGreater(big, small)

    def _ghost_radius(self) -> float:
        """The radius `_draw_placement_ghost` hands the layer ghost."""
        seen = []
        with patch.object(
            gui.TimelineGameplay, "_draw_layer_ghost",
            lambda self, painter, x, baseline_y, radius: seen.append(radius),
        ):
            self.layer._draw_placement_ghost(None, 60, 10.0, 20.0)
        return seen[0]


if __name__ == "__main__":
    unittest.main()
