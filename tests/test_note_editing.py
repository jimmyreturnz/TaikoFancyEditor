"""M3 note editing: place/delete via a chart view's tool row.

Covers the TimelineGameplay <-> MainWindow contract (signals in, no
MainWindow reference held by the widget), the four placed note shapes,
multi-view refresh on edit, undo/redo, and a real write-to-disk round trip
so [HitObjects] regeneration (M1) is exercised for genuinely inserted notes,
not just parsed ones.
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
from osu_io.parser import parse_osu
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


def _click(view: gui.TimelineGameplay, x: float, button: Qt.MouseButton) -> None:
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(x, 100),
        button, button, Qt.KeyboardModifier.NoModifier,
    )
    view.mousePressEvent(event)


class NoteEditingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")

        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)
        self.state = self.window.state

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()

    def _chart_view(self) -> gui.TimelineGameplay:
        frame = next(f for f in self.window._editor_views if f.view_type == "chart")
        return frame.chart_view

    # -- default state / isolation from the shared Fancy Arranger timeline --

    def test_fancy_arranger_timeline_has_no_tool_row_and_stays_select(self):
        self.assertEqual(self.window.timeline.tool, "select")
        # Nothing wires note_place_requested for the shared timeline, so
        # emitting it (as a stray keypress/click never could, since its
        # tool never leaves "select") must not mutate the document.
        before = len(self.state.document.hit_objects)
        self.window.timeline.note_place_requested.emit("don", 12345.0, False, False)
        self.assertEqual(len(self.state.document.hit_objects), before)

    def test_editor_activation_gives_chart_view_default_tool(self):
        frame = next(f for f in self.window._editor_views if f.view_type == "chart")
        self.assertEqual(frame.chart_view.tool, "select")

    def test_new_chart_view_becomes_the_global_tool_rows_target(self):
        frame = next(f for f in self.window._editor_views if f.view_type == "chart")
        self.assertIs(self.window._active_chart_view, frame.chart_view)
        self.assertTrue(self.window.global_tool_row.isEnabled())

    def test_global_tool_row_drives_the_active_view(self):
        view = self._chart_view()
        self.assertIs(self.window._active_chart_view, view)
        self.window.tool_buttons["kat"].setChecked(True)
        self.assertEqual(view.tool, "kat")
        self.window.new_combo_button.setChecked(True)
        self.assertTrue(view.new_combo)

    # -- placement, one per shape --------------------------------------------

    def test_place_don(self):
        view = self._chart_view()
        before = len(self.state.document.hit_objects)
        view.note_place_requested.emit("don", 12345.0, False, False)
        self.assertEqual(len(self.state.document.hit_objects), before + 1)
        note = next(n for n in self.state.document.hit_objects if n.time == 12345)
        self.assertEqual(note.note_kind, "don")
        self.assertFalse(note.is_new_combo)

    def test_place_kat_with_new_combo(self):
        view = self._chart_view()
        view.note_place_requested.emit("kat", 20000.0, True, False)
        note = next(n for n in self.state.document.hit_objects if n.time == 20000)
        self.assertEqual(note.note_kind, "kat")
        self.assertTrue(note.is_new_combo)

    def test_place_drumroll_and_denden(self):
        view = self._chart_view()
        view.note_place_with_duration_requested.emit("slider", 30000.0, 30500.0, False, False)
        view.note_place_with_duration_requested.emit("spinner", 40000.0, 41000.0, False, False)
        drumroll = next(n for n in self.state.document.hit_objects if n.time == 30000)
        denden = next(n for n in self.state.document.hit_objects if n.time == 40000)
        self.assertEqual(drumroll.note_kind, "drumroll")
        self.assertEqual(denden.note_kind, "denden")
        self.assertEqual(denden.end_time, 41000)

    def test_placed_notes_default_to_playfield_center(self):
        view = self._chart_view()
        view.note_place_requested.emit("don", 5000.0, False, False)
        note = next(n for n in self.state.document.hit_objects if n.time == 5000)
        self.assertEqual((note.x, note.y), (gui.PLAYFIELD_WIDTH // 2, gui.PLAYFIELD_HEIGHT // 2))

    # -- deletion, undo/redo --------------------------------------------------

    def test_delete_via_signal(self):
        view = self._chart_view()
        view.note_place_requested.emit("don", 12345.0, False, False)
        note = next(n for n in self.state.document.hit_objects if n.time == 12345)
        before = len(self.state.document.hit_objects)
        view.note_delete_requested.emit(note.uid)
        self.assertEqual(len(self.state.document.hit_objects), before - 1)
        self.assertNotIn(note.uid, {n.uid for n in self.state.document.hit_objects})

    def test_place_and_delete_are_undoable(self):
        view = self._chart_view()
        before = len(self.state.document.hit_objects)
        view.note_place_requested.emit("don", 12345.0, False, False)
        self.assertTrue(self.state.history.can_undo())
        self.window.undo()
        self.assertEqual(len(self.state.document.hit_objects), before)
        self.window.redo()
        self.assertEqual(len(self.state.document.hit_objects), before + 1)

    # -- real mouse events, not just direct signal emission -------------------

    def test_real_left_click_places_and_right_click_deletes(self):
        view = self._chart_view()
        view.resize(800, 200)
        view.window_ms = 2000.0
        view.current_time = 12000.0
        view.tool = "don"

        before = len(self.state.document.hit_objects)
        _click(view, view.x_for_time(12000.0), Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.state.document.hit_objects), before + 1)

        placed = max(self.state.document.hit_objects, key=lambda n: n.uid)
        view.tool = "select"
        _click(view, view.x_for_time(placed.time), Qt.MouseButton.RightButton)
        self.assertNotIn(placed.uid, {n.uid for n in self.state.document.hit_objects})

    # -- multi-view refresh -----------------------------------------------------

    def test_edit_refreshes_every_open_view_of_the_difficulty(self):
        view = self._chart_view()
        self.window._add_editor_view("chart", self.path)
        second_view = [f for f in self.window._editor_views if f.view_type == "chart"][-1].chart_view

        view.note_place_requested.emit("don", 12345.0, False, False)

        self.assertTrue(any(n.time == 12345 for n in second_view.notes))
        self.assertTrue(any(n.time == 12345 for n in self.window.timeline.notes))
        # The cursor and selection an edit shouldn't disturb.
        self.assertEqual(second_view.selected, set())

    # -- write-to-disk round trip -----------------------------------------------

    def test_placed_notes_survive_a_write_round_trip(self):
        view = self._chart_view()
        view.note_place_requested.emit("don", 5000.0, False, False)
        view.note_place_with_duration_requested.emit("slider", 30000.0, 30500.0, False, False)
        view.note_place_with_duration_requested.emit("spinner", 40000.0, 41000.0, False, False)

        self.window.save_all_states()

        reparsed = parse_osu(self.path)
        times = {n.time for n in reparsed.hit_objects}
        self.assertTrue({5000, 30000, 40000} <= times)
        drumroll = next(n for n in reparsed.hit_objects if n.time == 30000)
        denden = next(n for n in reparsed.hit_objects if n.time == 40000)
        self.assertEqual(drumroll.note_kind, "drumroll")
        self.assertEqual(denden.note_kind, "denden")
        self.assertEqual(denden.end_time, 41000)

    # -- locking disables editing -------------------------------------------

    def test_locking_the_active_view_disables_content_and_global_tool_row(self):
        frame = next(f for f in self.window._editor_views if f.view_type == "chart")
        self.assertIs(self.window._active_chart_view, frame.chart_view)
        frame.lock_button.setChecked(True)
        self.assertTrue(frame.locked)
        self.assertFalse(frame.content.isEnabled())
        self.assertFalse(self.window.global_tool_row.isEnabled())


if __name__ == "__main__":
    unittest.main()
