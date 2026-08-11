"""Third owner feedback round, after trying the SV editor in practice:

- the status/log line moved out from between Settings and the page tabs to
  the left side of the global header
- the difficulty name is shown once, at the right of each view's chrome,
  instead of once there and once in a group header on the left
- SV editor: Delete/Backspace removes selected green lines, Ctrl+Z/Ctrl+Y
  actually refresh the views, green-line mode drags an existing point
  instead of stacking on it, and a snapped ghost line previews the click
- uninherited lines render red, or yellow where SV shares the millisecond
- one hit object and one SV point per millisecond, spinners excepted
- Ctrl+C / Ctrl+V for both notes and SV points
- function mode generates on note positions (default) or every N snaps,
  with a -5ms default position offset
- SV views follow the playback clock, and zoom is shared per difficulty
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel

import gui
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


def _press(view, x, y=100.0, button=Qt.MouseButton.LeftButton):
    view.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(x, y), button, button,
        Qt.KeyboardModifier.NoModifier,
    ))


def _move(view, x, y=100.0, buttons=Qt.MouseButton.LeftButton):
    view.mouseMoveEvent(QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(x, y), Qt.MouseButton.NoButton, buttons,
        Qt.KeyboardModifier.NoModifier,
    ))


def _release(view, x, y=100.0):
    view.mouseReleaseEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, QPointF(x, y), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    ))


def _key(view, key):
    view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


class WindowTestCase(unittest.TestCase):
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

    def _chart_frame(self) -> gui.EditorViewFrame:
        return next(f for f in self.window._editor_views if f.view_type == "chart")

    def _sv_frame(self) -> gui.EditorViewFrame:
        return next(f for f in self.window._editor_views if f.view_type == "sv")

    def _chart_view(self) -> gui.TimelineGameplay:
        return self._chart_frame().chart_view

    def _sv_view(self) -> gui.SVEditorView:
        return self._sv_frame().sv_view


# -- header / chrome layout -------------------------------------------------


class HeaderStatusPositionTests(WindowTestCase):
    def test_status_sits_left_of_settings_and_the_page_tabs(self):
        header = self.window.status.parentWidget().layout()
        # The header is the layout that holds all four; find their order in it.
        order = {}
        layout = self.window.settings_button.parentWidget().layout()
        self.assertIsNotNone(layout)
        header_layout = None
        for i in range(layout.count()):
            item = layout.itemAt(i)
            sub = item.layout()
            if sub is None:
                continue
            widgets = {sub.itemAt(j).widget() for j in range(sub.count())}
            if self.window.settings_button in widgets:
                header_layout = sub
                break
        self.assertIsNotNone(header_layout, "global header row not found")
        for i in range(header_layout.count()):
            widget = header_layout.itemAt(i).widget()
            if widget is not None:
                order[widget] = i
        self.assertLess(order[self.window.status], order[self.window.settings_button])
        self.assertLess(order[self.window.status], order[self.window.editor_page_button])
        self.assertLess(order[self.window.status], order[self.window.fancy_arranger_page_button])
        del header


class DifficultyLabelTests(WindowTestCase):
    def test_every_view_frame_labels_its_difficulty_on_the_right(self):
        for frame in self.window._editor_views:
            chrome = frame.layout().itemAt(0).layout()
            widgets = [chrome.itemAt(i).widget() for i in range(chrome.count())]
            self.assertIs(widgets[-1], frame.difficulty_name_label)
            self.assertEqual(frame.difficulty_name_label.text(), self.state.document.version)

    def test_difficulty_group_has_no_second_label_on_the_left(self):
        group_layout = self.window._editor_view_groups[self.path]
        stray = [
            group_layout.itemAt(i).widget()
            for i in range(group_layout.count())
            if isinstance(group_layout.itemAt(i).widget(), QLabel)
        ]
        self.assertEqual(stray, [], "the difficulty name must appear only at the right of each view")


# -- SV editor: selection, delete, undo/redo --------------------------------


class SVSelectionAndDeleteTests(WindowTestCase):
    def _select_first_inherited(self):
        view = self._sv_view()
        view.resize(800, 200)
        view.window_ms = 4000.0
        point = next(p for p in view.timing_points if not p.uninherited)
        view.current_time = point.time
        view.tool = "select"
        _press(view, view.x_for_time(point.time))
        _release(view, view.x_for_time(point.time))
        return view, point

    def test_clicking_a_green_line_selects_it(self):
        view, point = self._select_first_inherited()
        self.assertEqual(view.selected_uids, {point.uid})

    def test_delete_key_removes_the_selected_green_line(self):
        view, point = self._select_first_inherited()
        before = len(self.state.document.timing_points)
        _key(view, Qt.Key_Delete)
        self.assertEqual(len(self.state.document.timing_points), before - 1)
        self.assertNotIn(point.uid, {p.uid for p in self.state.document.timing_points})

    def test_backspace_removes_the_selected_green_line(self):
        view, point = self._select_first_inherited()
        before = len(self.state.document.timing_points)
        _key(view, Qt.Key_Backspace)
        self.assertEqual(len(self.state.document.timing_points), before - 1)

    def test_deleting_a_selection_is_one_undo_step(self):
        view = self._sv_view()
        view.selected_uids = {p.uid for p in view.timing_points if not p.uninherited}
        before = len(self.state.document.timing_points)
        steps = len(self.state.history.undo_stack)
        _key(view, Qt.Key_Delete)
        self.assertEqual(len(self.state.history.undo_stack), steps + 1)
        self.window.undo()
        self.assertEqual(len(self.state.document.timing_points), before)

    def test_delete_never_removes_an_uninherited_point(self):
        view = self._sv_view()
        uninherited = next(p for p in view.timing_points if p.uninherited)
        view.selected_uids = {uninherited.uid}
        before = len(self.state.document.timing_points)
        _key(view, Qt.Key_Delete)
        self.assertEqual(len(self.state.document.timing_points), before)

    def test_escape_clears_the_selection(self):
        view, _point = self._select_first_inherited()
        _key(view, Qt.Key_Escape)
        self.assertEqual(view.selected_uids, set())


class HistoryRefreshTests(WindowTestCase):
    def test_undo_of_an_sv_add_refreshes_the_sv_views(self):
        """Undo used to refresh only the transform canvas, so the Editor page
        kept painting the pre-undo document -- which reads as "Ctrl+Z does
        nothing" even though the model had already rolled back."""
        view = self._sv_view()
        view.point_add_requested.emit(15000.0, 1.75)
        self.assertTrue(any(p.time == 15000 for p in view.timing_points))

        self.window.undo()
        self.assertFalse(any(p.time == 15000 for p in view.timing_points))

        self.window.redo()
        self.assertTrue(any(p.time == 15000 for p in view.timing_points))

    def test_undo_of_a_note_placement_refreshes_the_chart_views(self):
        view = self._chart_view()
        view.note_place_requested.emit("don", 12345.0, False, False)
        self.assertTrue(any(n.time == 12345 for n in view.notes))

        self.window.undo()
        self.assertFalse(any(n.time == 12345 for n in view.notes))

        self.window.redo()
        self.assertTrue(any(n.time == 12345 for n in view.notes))

    def test_undo_targets_the_focused_editor_views_difficulty(self):
        sv_view = self._sv_view()
        self.window._editor_view_focus_changed(None, sv_view)
        self.assertIs(self.window._last_focused_editor_view, sv_view)
        self.assertIs(self.window._history_target(), self.state)

    def test_closing_the_focused_view_falls_back_to_the_active_difficulty(self):
        frame = self._sv_frame()
        self.window._editor_view_focus_changed(None, frame.sv_view)
        self.window._close_editor_view(frame)
        self.assertIsNone(self.window._last_focused_editor_view)
        self.assertIs(self.window._history_target(), self.state)


# -- SV editor: green-line mode, ghost, colours -----------------------------


class GreenLineModeTests(WindowTestCase):
    def test_green_line_mode_drags_an_existing_point_instead_of_stacking(self):
        view = self._sv_view()
        view.resize(800, 200)
        view.window_ms = 4000.0
        point = next(p for p in view.timing_points if not p.uninherited)
        view.current_time = point.time
        view.tool = "green_line"

        before = len(self.state.document.timing_points)
        x = view.x_for_time(point.time)
        _press(view, x, y=40.0)
        self.assertIs(view._drag_point, point)
        _move(view, x, y=40.0)
        _release(view, x, y=40.0)

        self.assertEqual(len(self.state.document.timing_points), before, "must adjust, not add")
        self.assertEqual(view.selected_uids, {point.uid})

    def test_green_line_mode_still_adds_on_empty_space(self):
        view = self._sv_view()
        view.resize(800, 200)
        view.window_ms = 4000.0
        view.current_time = 30000.0
        view.tool = "green_line"

        before = len(self.state.document.timing_points)
        _press(view, view.x_for_time(30000.0), y=60.0)
        self.assertEqual(len(self.state.document.timing_points), before + 1)

    def test_placement_snaps_to_the_beat_grid(self):
        view = self._sv_view()
        view.resize(800, 200)
        view.window_ms = 4000.0
        view.current_time = 30000.0
        view.tool = "green_line"
        view.set_snap_divisor(4)

        added: list[float] = []
        view.point_add_requested.connect(lambda time_ms, _sv: added.append(time_ms))
        # 30040ms is off-grid; the fixture's 120 BPM section snaps 1/4 to 125ms.
        _press(view, view.x_for_time(30040.0), y=60.0)
        self.assertEqual(added, [30000.0])

    def test_hover_sets_the_ghost_time_and_paints_without_raising(self):
        view = self._sv_view()
        view.resize(800, 200)
        view.tool = "green_line"
        _move(view, 400.0, y=70.0, buttons=Qt.MouseButton.NoButton)
        self.assertIsNotNone(view._hover_time)
        self.assertIsNotNone(view._hover_sv)
        view.grab()  # must not raise

    def test_no_ghost_in_select_mode(self):
        view = self._sv_view()
        view.resize(800, 200)
        view.tool = "select"
        view._hover_time = 5000.0
        view.grab()  # still must not raise


class SVLineColourTests(unittest.TestCase):
    def _view(self, points):
        view = gui.SVEditorView()
        view.set_timing_points(points)
        return view

    def test_uninherited_alone_is_red(self):
        view = self._view([gui.TimingPoint(time=0.0, beat_length=500.0)])
        self.assertEqual(view.line_kinds()[0], "red")

    def test_inherited_alone_is_green(self):
        view = self._view([gui.TimingPoint(time=100.0, beat_length=-100.0, uninherited_flag=0)])
        self.assertEqual(view.line_kinds()[100], "green")

    def test_sv_stacked_on_a_bpm_point_is_yellow(self):
        view = self._view([
            gui.TimingPoint(time=250.0, beat_length=500.0),
            gui.TimingPoint(time=250.0, beat_length=-50.0, uninherited_flag=0),
        ])
        self.assertEqual(view.line_kinds()[250], "yellow")


# -- one object per millisecond ---------------------------------------------


class OnePerMillisecondTests(WindowTestCase):
    def test_placing_a_note_replaces_the_note_already_there(self):
        view = self._chart_view()
        before = len(self.state.document.hit_objects)
        original = next(n for n in self.state.document.hit_objects if n.time == 1000)

        view.note_place_requested.emit("kat", 1000.0, False, False)

        self.assertEqual(len(self.state.document.hit_objects), before, "no stacking")
        at_1000 = [n for n in self.state.document.hit_objects if n.time == 1000]
        self.assertEqual(len(at_1000), 1)
        self.assertEqual(at_1000[0].note_kind, "kat")
        self.assertNotIn(original.uid, {n.uid for n in self.state.document.hit_objects})

    def test_replacement_is_a_single_undo_step(self):
        view = self._chart_view()
        before = [(n.uid, n.time) for n in self.state.document.hit_objects]
        steps = len(self.state.history.undo_stack)
        view.note_place_requested.emit("kat", 1000.0, False, False)
        self.assertEqual(len(self.state.history.undo_stack), steps + 1)
        self.window.undo()
        self.assertEqual([(n.uid, n.time) for n in self.state.document.hit_objects], before)

    def test_a_spinner_may_stack_on_an_existing_note(self):
        view = self._chart_view()
        before = len(self.state.document.hit_objects)
        view.note_place_with_duration_requested.emit("spinner", 1000.0, 2000.0, False, False)
        self.assertEqual(len(self.state.document.hit_objects), before + 1)
        self.assertTrue(any(n.time == 1000 and n.is_spinner for n in self.state.document.hit_objects))
        self.assertTrue(any(n.time == 1000 and n.is_circle for n in self.state.document.hit_objects))

    def test_a_note_may_be_placed_on_top_of_a_spinner(self):
        view = self._chart_view()
        view.note_place_with_duration_requested.emit("spinner", 30000.0, 31000.0, False, False)
        before = len(self.state.document.hit_objects)
        view.note_place_requested.emit("don", 30000.0, False, False)
        self.assertEqual(len(self.state.document.hit_objects), before + 1)

    def test_adding_sv_on_an_existing_green_line_replaces_it(self):
        view = self._sv_view()
        existing = next(p for p in self.state.document.timing_points if not p.uninherited)
        before = len(self.state.document.timing_points)

        view.point_add_requested.emit(float(existing.time), 2.5)

        self.assertEqual(len(self.state.document.timing_points), before)
        inherited_here = [
            p for p in self.state.document.timing_points
            if not p.uninherited and round(p.time) == round(existing.time)
        ]
        self.assertEqual(len(inherited_here), 1)
        self.assertAlmostEqual(inherited_here[0].sv_multiplier, 2.5, places=2)

    def test_adding_sv_never_removes_the_bpm_point_sharing_its_millisecond(self):
        view = self._sv_view()
        uninherited = next(p for p in self.state.document.timing_points if p.uninherited)
        view.point_add_requested.emit(float(uninherited.time), 1.5)
        self.assertIn(uninherited.uid, {p.uid for p in self.state.document.timing_points})

    def test_inserted_notes_get_their_own_applied_position_key(self):
        view = self._chart_view()
        view.note_place_requested.emit("don", 30000.0, False, False)
        placed = next(n for n in self.state.document.hit_objects if n.time == 30000)
        self.assertNotEqual(placed.original_index, 0, "must not alias the map's first note")
        self.assertIn(placed.original_index, self.state.applied_positions)

        self.window.undo()
        self.assertNotIn(placed.original_index, self.state.applied_positions)


class TransformOrderingTests(WindowTestCase):
    def test_transform_order_follows_note_time_not_insertion_order(self):
        """R11. An inserted note gets the next free original_index, which for
        a note placed *earlier* than existing ones is a higher key -- so key
        order and time order disagree the moment insertion exists."""
        view = self._chart_view()
        view.note_place_requested.emit("don", 500.0, False, False)  # before every fixture note
        early = next(n for n in self.state.document.hit_objects if n.time == 500)
        self.assertGreater(early.original_index, 0)

        self.window.selected = {
            n.original_index for n in self.state.document.hit_objects if n.time <= 1500
        }
        ordered = self.state.notes_in_time_order()
        by_index = {n.original_index: n for n in self.state.document.hit_objects}
        times = [by_index[key].time for key in ordered]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[0], 500)
        self.assertNotEqual(ordered, sorted(ordered), "key order must not match time order here")


class ChartDeleteKeyTests(WindowTestCase):
    def test_delete_removes_the_chart_selection_in_one_step(self):
        view = self._chart_view()
        victims = self.state.document.hit_objects[:2]
        view.selected = {n.original_index for n in victims}
        before = len(self.state.document.hit_objects)
        steps = len(self.state.history.undo_stack)

        _key(view, Qt.Key_Delete)

        self.assertEqual(len(self.state.document.hit_objects), before - 2)
        self.assertEqual(len(self.state.history.undo_stack), steps + 1)
        self.window.undo()
        self.assertEqual(len(self.state.document.hit_objects), before)

    def test_the_fancy_arranger_timeline_ignores_delete(self):
        """It is a transform-selection surface, not an editing one."""
        timeline = self.window.timeline
        timeline.selected = {n.original_index for n in self.state.document.hit_objects[:2]}
        before = len(self.state.document.hit_objects)
        _key(timeline, Qt.Key_Delete)
        self.assertEqual(len(self.state.document.hit_objects), before)


# -- clipboard ---------------------------------------------------------------


class ClipboardTests(WindowTestCase):
    def test_copy_and_paste_notes_at_the_playhead(self):
        view = self._chart_view()
        self.window._editor_view_focus_changed(None, view)
        sources = [n for n in self.state.document.hit_objects if n.time in (1000, 1500)]
        view.selected = {n.original_index for n in sources}

        self.window.copy_selection()
        self.assertEqual(len(self.window._note_clipboard), 2)

        view.current_time = 30000.0
        before = len(self.state.document.hit_objects)
        self.window.paste_clipboard()

        self.assertEqual(len(self.state.document.hit_objects), before + 2)
        times = {n.time for n in self.state.document.hit_objects}
        self.assertIn(30000, times)
        self.assertIn(30500, times, "relative spacing must be preserved")

    def test_pasting_notes_is_one_undo_step(self):
        view = self._chart_view()
        self.window._editor_view_focus_changed(None, view)
        view.selected = {n.original_index for n in self.state.document.hit_objects[:3]}
        self.window.copy_selection()
        view.current_time = 30000.0
        before = len(self.state.document.hit_objects)
        steps = len(self.state.history.undo_stack)

        self.window.paste_clipboard()
        self.assertEqual(len(self.state.history.undo_stack), steps + 1)
        self.window.undo()
        self.assertEqual(len(self.state.document.hit_objects), before)

    def test_copy_and_paste_sv_points(self):
        sv_view = self._sv_view()
        self.window._editor_view_focus_changed(None, sv_view)
        sources = [p for p in sv_view.timing_points if not p.uninherited]
        sv_view.selected_uids = {p.uid for p in sources}

        self.window.copy_selection()
        self.assertEqual(len(self.window._sv_clipboard), len(sources))

        sv_view.current_time = 30000.0
        before = len(self.state.document.timing_points)
        self.window.paste_clipboard()

        self.assertEqual(len(self.state.document.timing_points), before + len(sources))
        pasted = [p for p in self.state.document.timing_points if p.time == 30000]
        self.assertTrue(pasted)
        self.assertFalse(pasted[0].uninherited)

    def test_clipboard_shortcuts_are_scoped_to_the_editor_page(self):
        """Application context would consume Ctrl+C everywhere, breaking copy
        in every line edit and spin box in the app."""
        for shortcut in (self.window.copy_shortcut, self.window.paste_shortcut):
            self.assertIs(shortcut.parent(), self.window.editor_page)
            self.assertEqual(shortcut.context(), Qt.WidgetWithChildrenShortcut)

    def test_copy_routes_by_which_tool_row_is_visible(self):
        chart_view = self._chart_view()
        self.window._editor_view_focus_changed(None, chart_view)
        self.assertEqual(self.window._clipboard_target(), "chart")
        self.window._editor_view_focus_changed(None, self._sv_view())
        self.assertEqual(self.window._clipboard_target(), "sv")

    def test_pasted_notes_do_not_stack_on_existing_ones(self):
        view = self._chart_view()
        self.window._editor_view_focus_changed(None, view)
        view.selected = {next(n.original_index for n in self.state.document.hit_objects if n.time == 1000)}
        self.window.copy_selection()

        view.current_time = 2000.0  # already occupied by a note
        before = len(self.state.document.hit_objects)
        self.window.paste_clipboard()

        self.assertEqual(len(self.state.document.hit_objects), before)
        self.assertEqual(len([n for n in self.state.document.hit_objects if n.time == 2000]), 1)


# -- function mode -----------------------------------------------------------


class SVFunctionPlacementTests(WindowTestCase):
    def _params(self, **overrides):
        params = {
            "initial_rate": 1.0, "final_rate": 2.0, "placement": "notes", "snap_divisor": 4,
            "position_offset": 0, "omit_barline": False, "relative_to_final_bpm": False,
            "function": "linear",
        }
        params.update(overrides)
        return params

    def test_note_placement_generates_only_on_notes_inside_the_range(self):
        before_ids = {p.uid for p in self.state.document.timing_points}
        self.window._generate_sv(self.path, 1000.0, 3000.0, self._params())

        generated = sorted(
            p.time for p in self.state.document.timing_points if p.uid not in before_ids
        )
        note_times = sorted(
            float(n.time) for n in self.state.document.hit_objects if 1000 <= n.time <= 3000
        )
        self.assertEqual(generated, note_times)

    def test_note_placement_generates_nothing_when_the_range_has_no_notes(self):
        before = len(self.state.document.timing_points)
        self.window._generate_sv(self.path, 40000.0, 41000.0, self._params())
        self.assertEqual(len(self.state.document.timing_points), before)

    def test_snap_placement_walks_the_beat_grid(self):
        before_ids = {p.uid for p in self.state.document.timing_points}
        self.window._generate_sv(
            self.path, 10000.0, 11000.0, self._params(placement="snaps", snap_divisor=4),
        )
        generated = sorted(
            p.time for p in self.state.document.timing_points if p.uid not in before_ids
        )
        # The fixture's last uninherited section starts at 6000 with a 400ms
        # beat, so 1/4 snaps land every 100ms.
        self.assertEqual(generated[0], 10000)
        self.assertEqual(generated[-1], 11000)
        self.assertTrue(all(b - a == 100 for a, b in zip(generated, generated[1:])))

    def test_position_offset_shifts_points_without_skewing_the_curve(self):
        before_ids = {p.uid for p in self.state.document.timing_points}
        self.window._generate_sv(self.path, 1000.0, 3000.0, self._params(position_offset=-5))
        generated = sorted(
            (p for p in self.state.document.timing_points if p.uid not in before_ids),
            key=lambda p: p.time,
        )
        self.assertEqual(generated[0].time, 995)
        # Progress is still measured from the un-offset position, so the
        # endpoints keep the requested rates exactly.
        self.assertAlmostEqual(generated[0].sv_multiplier, 1.0, places=2)
        self.assertAlmostEqual(generated[-1].sv_multiplier, 2.0, places=2)

    def test_generation_replaces_green_lines_it_lands_on(self):
        existing = next(p for p in self.state.document.timing_points if not p.uninherited)
        before = len(self.state.document.timing_points)
        # Offset 0 puts a generated point exactly on the existing one at 2000.
        self.window._generate_sv(self.path, 1000.0, 3000.0, self._params())
        self.assertNotIn(existing.uid, {p.uid for p in self.state.document.timing_points})
        inherited_at_2000 = [
            p for p in self.state.document.timing_points
            if not p.uninherited and p.time == 2000
        ]
        self.assertEqual(len(inherited_at_2000), 1)
        self.assertGreater(len(self.state.document.timing_points), before - 1)

    def test_generation_stays_one_undo_step_even_when_it_replaces(self):
        steps = len(self.state.history.undo_stack)
        before = [(p.uid, p.time) for p in self.state.document.timing_points]
        self.window._generate_sv(self.path, 1000.0, 3000.0, self._params())
        self.assertEqual(len(self.state.history.undo_stack), steps + 1)
        self.window.undo()
        self.assertEqual([(p.uid, p.time) for p in self.state.document.timing_points], before)


class SVFunctionDialogDefaultTests(unittest.TestCase):
    def test_defaults_are_each_note_and_minus_five_ms(self):
        dialog = gui.SVFunctionDialog(1000.0, 2000.0)
        params = dialog.parameters()
        self.assertEqual(params["placement"], "notes")
        self.assertEqual(params["position_offset"], -5)
        dialog.deleteLater()

    def test_the_snap_divisor_follows_the_editor_page(self):
        dialog = gui.SVFunctionDialog(1000.0, 2000.0, snap_divisor=12)
        self.assertEqual(dialog.parameters()["snap_divisor"], 12)
        dialog.deleteLater()

    def test_the_snap_row_only_shows_in_every_snap_mode(self):
        dialog = gui.SVFunctionDialog(1000.0, 2000.0)
        dialog.show()
        self.assertFalse(dialog.snap_combo.isVisible())
        dialog.placement_combo.setCurrentIndex(dialog.placement_combo.findData("snaps"))
        self.assertTrue(dialog.snap_combo.isVisible())
        dialog.close()
        dialog.deleteLater()


# -- playback / zoom ---------------------------------------------------------


class SVFollowsPlaybackTests(WindowTestCase):
    def test_sv_views_are_registered_for_the_clock_broadcast(self):
        self.assertIn(self._sv_view(), self.window._sv_views)

    def test_seeking_moves_the_sv_view_with_the_chart(self):
        sv_view = self._sv_view()
        self.window.seek_audio(4321)
        self.assertAlmostEqual(sv_view.current_time, 4321.0, places=3)

    def test_the_frame_clock_moves_the_sv_view(self):
        sv_view = self._sv_view()
        self.window.audio_anchor_position = 7777
        self.window._next_frame_due_ns = 0
        self.window._render_gameplay_frame()
        self.assertAlmostEqual(sv_view.current_time, 7777.0, places=3)

    def test_closing_an_sv_view_unregisters_it(self):
        frame = self._sv_frame()
        view = frame.sv_view
        self.window._close_editor_view(frame)
        self.assertNotIn(view, self.window._sv_views)


class ZoomIsPerDifficultyTests(WindowTestCase):
    def test_chart_and_sv_open_at_the_same_span(self):
        self.assertEqual(self._chart_view().window_ms, self._sv_view().window_ms)

    def test_zooming_a_chart_view_zooms_the_sv_view_too(self):
        chart = self._chart_view()
        sv = self._sv_view()
        chart.wheelEvent(QWheelEvent(
            QPointF(100.0, 50.0), QPointF(100.0, 50.0), QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        ))
        self.assertLess(chart.window_ms, self.window.DEFAULT_EDITOR_WINDOW_MS)
        self.assertEqual(sv.window_ms, chart.window_ms)

    def test_zooming_an_sv_view_zooms_the_chart_view_too(self):
        chart = self._chart_view()
        sv = self._sv_view()
        self.window._zoom_changed(self.path, 6000.0, sv)
        self.assertEqual(chart.window_ms, 6000.0)

    def test_a_view_opened_later_adopts_the_current_zoom(self):
        self.window._zoom_changed(self.path, 5500.0, None)
        self.window._add_editor_view("chart", self.path)
        newest = [f for f in self.window._editor_views if f.view_type == "chart"][-1]
        self.assertEqual(newest.chart_view.window_ms, 5500.0)


# -- timing bar --------------------------------------------------------------


class TimingBarMarkerTests(WindowTestCase):
    def test_adding_a_green_line_updates_the_timing_bar_markers(self):
        sv_view = self._sv_view()
        sv_view.point_add_requested.emit(15000.0, 1.5)
        for bar in self.window._timing_bars:
            self.assertIn((15000, False), bar.timing_markers)

    def test_generating_sv_updates_the_timing_bar_markers(self):
        params = {
            "initial_rate": 1.0, "final_rate": 2.0, "placement": "notes", "snap_divisor": 4,
            "position_offset": 0, "omit_barline": False, "relative_to_final_bpm": False,
            "function": "linear",
        }
        self.window._generate_sv(self.path, 1000.0, 3000.0, params)
        for bar in self.window._timing_bars:
            self.assertIn((1000, False), bar.timing_markers)
            self.assertIn((3000, False), bar.timing_markers)

    def test_deleting_a_green_line_updates_the_timing_bar_markers(self):
        sv_view = self._sv_view()
        sv_view.point_add_requested.emit(15000.0, 1.5)
        added = next(p for p in self.state.document.timing_points if p.time == 15000)
        sv_view.point_delete_requested.emit(added.uid)
        for bar in self.window._timing_bars:
            self.assertNotIn((15000, False), bar.timing_markers)


if __name__ == "__main__":
    unittest.main()
