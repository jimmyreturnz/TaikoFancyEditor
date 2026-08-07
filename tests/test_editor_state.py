"""Tests for the per-difficulty state, command and history model.

Deliberately Qt-free: these run without an offscreen QApplication.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from model.commands import (
    CompositeCommand,
    EditTimingPoint,
    InsertHitObjects,
    InsertTimingPoints,
    MoveNotes,
    RemoveHitObjects,
    RemoveTimingPoints,
    SetNoteFields,
    SetNotePositions,
)
from model.editor_state import DifficultyState
from model.history import History
from model.hit_object import HitObject
from osu_io.parser import parse_osu
from osu_io.timing import TimingPoint
from tests.osu_fixtures import write_fixture


def load_state(directory: Path, name: str = "full_v14") -> DifficultyState:
    source = write_fixture(directory, name)
    return DifficultyState.from_document(parse_osu(source), source)


class StateTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.state = load_state(Path(self._temp.name))

    def tearDown(self):
        self._temp.cleanup()

    def test_positions_start_matching_the_document(self):
        self.assertEqual(len(self.state.original_positions), 8)
        self.assertEqual(self.state.applied_positions, self.state.original_positions)
        self.assertFalse(self.state.is_modified())

    def test_is_modified_tracks_applied_positions(self):
        self.state.applied_positions[0] = (1, 2)
        self.assertTrue(self.state.is_modified())

    def test_notes_in_time_order_survives_insertion(self):
        """R11: sorted(selected) is only time-ordered by accident today."""
        document = self.state.document
        late = HitObject.from_line("256,192,9000,1,0", original_index=99)
        early = HitObject.from_line("256,192,1,1,0", original_index=100)
        document.hit_objects.extend([late, early])
        self.state.selected = {0, 99, 100}

        self.assertEqual(self.state.notes_in_time_order(), [100, 0, 99])
        self.assertNotEqual(
            self.state.notes_in_time_order(),
            sorted(self.state.selected),
            "raw key order must not be mistaken for time order",
        )

    def test_state_carries_its_own_history(self):
        other = load_state(Path(self._temp.name), "colours_between")
        self.assertIsNot(self.state.history, other.history)


class MoveNotesTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.state = load_state(Path(self._temp.name))
        self.history = self.state.history

    def tearDown(self):
        self._temp.cleanup()

    def test_apply_and_revert(self):
        before = dict(self.state.applied_positions)
        command = MoveNotes({0: (before[0], (10, 20))})
        self.history.push(command, self.state)
        self.assertEqual(self.state.applied_positions[0], (10, 20))

        self.history.undo(self.state)
        self.assertEqual(self.state.applied_positions, before)

        self.history.redo(self.state)
        self.assertEqual(self.state.applied_positions[0], (10, 20))

    def test_drag_stream_merges_into_one_step(self):
        start = self.state.applied_positions[0]
        for step in range(1, 26):
            self.history.push(
                MoveNotes({0: (self.state.applied_positions[0], (step, step))}),
                self.state,
                allow_merge=True,
            )
        self.assertEqual(len(self.history.undo_stack), 1, "a drag is one undo step")
        self.assertEqual(self.state.applied_positions[0], (25, 25))

        self.history.undo(self.state)
        self.assertEqual(self.state.applied_positions[0], start, "undo rewinds the whole drag")

    def test_merge_refuses_a_different_selection(self):
        self.history.push(MoveNotes({0: ((0, 0), (1, 1))}), self.state, allow_merge=True)
        self.history.push(MoveNotes({1: ((0, 0), (2, 2))}), self.state, allow_merge=True)
        self.assertEqual(len(self.history.undo_stack), 2)

    def test_reset_positions_round_trips(self):
        original = dict(self.state.original_positions)
        self.history.push(MoveNotes({0: (original[0], (5, 5))}), self.state)
        self.history.push(
            SetNotePositions(dict(self.state.applied_positions), dict(original)), self.state
        )
        self.assertEqual(self.state.applied_positions, original)

        self.history.undo(self.state)
        self.assertEqual(self.state.applied_positions[0], (5, 5))


class NoteEditingTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.state = load_state(Path(self._temp.name))
        self.history = self.state.history

    def tearDown(self):
        self._temp.cleanup()

    def test_insert_and_remove_notes(self):
        before = len(self.state.document.hit_objects)
        fake = HitObject.from_line("256,192,8000,2,12,L|624:192,643,-0.0001")
        self.history.push(InsertHitObjects([fake]), self.state)
        self.assertEqual(len(self.state.document.hit_objects), before + 1)

        self.history.undo(self.state)
        self.assertEqual(len(self.state.document.hit_objects), before)
        self.assertNotIn(fake.uid, {n.uid for n in self.state.document.hit_objects})

    def test_removed_notes_come_back_identical(self):
        victim = self.state.document.hit_objects[-1]
        line = victim.to_line("")
        self.history.push(RemoveHitObjects([victim]), self.state)
        self.assertEqual(len(self.state.document.hit_objects), 7)

        self.history.undo(self.state)
        restored = next(n for n in self.state.document.hit_objects if n.uid == victim.uid)
        self.assertEqual(restored.to_line(""), line)

    def test_set_note_fields(self):
        note = self.state.document.hit_objects[0]
        self.history.push(
            SetNoteFields(note.uid, {"hit_sound": (note.hit_sound, 8)}), self.state
        )
        self.assertEqual(note.note_kind, "kat")
        self.history.undo(self.state)
        self.assertEqual(note.note_kind, "don")

    def test_inserted_notes_stay_time_ordered(self):
        self.history.push(
            InsertHitObjects([HitObject.from_line("256,192,1,1,0")]), self.state
        )
        times = [n.time for n in self.state.document.hit_objects]
        self.assertEqual(times, sorted(times))


class TimingCommandTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.state = load_state(Path(self._temp.name))
        self.history = self.state.history

    def tearDown(self):
        self._temp.cleanup()

    def test_sv_sweep_is_one_undo_step(self):
        """A generated sweep must not need 400 Ctrl+Z presses."""
        before = len(self.state.document.timing_points)
        points = [TimingPoint.inherited_at(10000 + i * 25, 1.0 + i * 0.01) for i in range(400)]
        self.history.push(
            CompositeCommand([InsertTimingPoints(points)], "generate_sv"), self.state
        )
        self.assertEqual(len(self.state.document.timing_points), before + 400)
        self.assertEqual(len(self.history.undo_stack), 1)

        self.history.undo(self.state)
        self.assertEqual(len(self.state.document.timing_points), before)

    def test_edit_timing_point_and_merge(self):
        point = next(p for p in self.state.document.timing_points if not p.uninherited)
        original = point.beat_length
        for sv in (1.5, 2.0, 2.5):
            self.history.push(
                EditTimingPoint(point.uid, {"beat_length": (point.beat_length, -100.0 / sv)}),
                self.state,
                allow_merge=True,
            )
        self.assertEqual(len(self.history.undo_stack), 1, "dragging a green line is one step")
        self.assertAlmostEqual(point.sv_multiplier, 2.5)

        self.history.undo(self.state)
        self.assertAlmostEqual(point.beat_length, original)

    def test_remove_timing_points_restores_them(self):
        inherited = [p for p in self.state.document.timing_points if not p.uninherited]
        self.history.push(RemoveTimingPoints(inherited), self.state)
        self.assertTrue(all(p.uninherited for p in self.state.document.timing_points))

        self.history.undo(self.state)
        self.assertEqual(len(self.state.document.timing_points), 4)

    def test_composite_reverts_in_reverse_order(self):
        order = []

        class Recorder(MoveNotes):
            def __init__(self, tag):
                super().__init__({})
                self.tag = tag

            def apply(self, target):
                order.append(("apply", self.tag))

            def revert(self, target):
                order.append(("revert", self.tag))

        composite = CompositeCommand([Recorder("a"), Recorder("b"), Recorder("c")])
        composite.apply(self.state)
        composite.revert(self.state)
        self.assertEqual(
            order,
            [("apply", "a"), ("apply", "b"), ("apply", "c"),
             ("revert", "c"), ("revert", "b"), ("revert", "a")],
        )

    def test_barline_pass_is_one_step(self):
        notes = [n for n in self.state.document.hit_objects if n.note_kind in ("don", "kat", "big_don", "big_kat")]
        points = []
        for note in notes:
            for offset in ((-6, -4, -2, 2, 4, 6) if note.is_kat else (-2, 2)):
                points.append(TimingPoint.uninherited_at(note.time + offset, 120.0, omit_first_barline=True))
        self.history.push(CompositeCommand([InsertTimingPoints(points)], "barline_gimmick"), self.state)
        self.assertEqual(len(self.history.undo_stack), 1)

        self.history.undo(self.state)
        self.assertEqual(len(self.state.document.timing_points), 4)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.state = load_state(Path(self._temp.name))

    def tearDown(self):
        self._temp.cleanup()

    def test_revision_changes_on_every_operation(self):
        history = self.state.history
        seen = {history.revision}
        history.push(MoveNotes({0: ((0, 0), (1, 1))}), self.state)
        seen.add(history.revision)
        history.undo(self.state)
        seen.add(history.revision)
        history.redo(self.state)
        seen.add(history.revision)
        self.assertEqual(len(seen), 4, "the preview cache key must go stale each time")

    def test_push_clears_redo(self):
        history = self.state.history
        history.push(MoveNotes({0: ((0, 0), (1, 1))}), self.state)
        history.undo(self.state)
        self.assertTrue(history.can_redo())
        history.push(MoveNotes({0: ((0, 0), (2, 2))}), self.state)
        self.assertFalse(history.can_redo())

    def test_stack_is_bounded(self):
        history = History(limit=10)
        for step in range(25):
            history.push(MoveNotes({step: ((0, 0), (step, step))}), self.state)
        self.assertEqual(len(history.undo_stack), 10)

    def test_undo_on_empty_history_is_safe(self):
        history = History()
        self.assertIsNone(history.undo(self.state))
        self.assertIsNone(history.redo(self.state))

    def test_dirty_tracking(self):
        history = self.state.history
        self.assertFalse(history.dirty)
        history.push(MoveNotes({0: ((0, 0), (1, 1))}), self.state)
        self.assertTrue(history.dirty)
        history.mark_saved()
        self.assertFalse(history.dirty)


class MultipleDifficultyTests(unittest.TestCase):
    def test_histories_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            first = load_state(Path(directory), "full_v14")
            second = load_state(Path(directory), "colours_between")

            first.history.push(MoveNotes({0: (first.applied_positions[0], (9, 9))}), first)
            self.assertEqual(first.applied_positions[0], (9, 9))
            self.assertFalse(second.history.can_undo(), "editing one map must not touch another")

            second.history.push(MoveNotes({0: (second.applied_positions[0], (4, 4))}), second)
            first.history.undo(first)
            self.assertNotEqual(first.applied_positions[0], (9, 9))
            self.assertEqual(second.applied_positions[0], (4, 4), "the other map keeps its edit")


if __name__ == "__main__":
    unittest.main()
