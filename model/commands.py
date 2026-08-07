"""Undo/redo commands.

Replaces full snapshots of `applied_positions`, which could only express a
coordinate change, scaled as O(notes) per undo entry, and had no way to
represent a timing-point edit or a note being added.

Nothing here imports Qt, so every command is unit-testable without an offscreen
QApplication.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EditTarget(Protocol):
    """What a command is allowed to touch.

    DifficultyState satisfies this. Keeping it structural means commands never
    import editor_state, so the dependency runs one way only.
    """

    document: Any
    applied_positions: dict[int, tuple[int, int]]


class Command:
    """Base class. `label_id` is a stable identifier, translated at display time."""

    label_id = "command"

    def apply(self, target: EditTarget) -> None:
        raise NotImplementedError

    def revert(self, target: EditTarget) -> None:
        raise NotImplementedError

    def merge_with(self, later: "Command") -> "Command | None":
        """Return a combined command, or None if the two cannot merge."""
        return None


@dataclass
class MoveNotes(Command):
    """Coordinate changes, keyed by note identity.

    `changes` maps key -> (old_xy, new_xy). The key is whatever
    applied_positions is keyed by, which is original_index today and becomes uid
    once insertion exists.
    """

    changes: dict[int, tuple[tuple[int, int], tuple[int, int]]]
    label_id: str = "move_notes"

    def apply(self, target: EditTarget) -> None:
        for key, (_old, new) in self.changes.items():
            target.applied_positions[key] = new

    def revert(self, target: EditTarget) -> None:
        for key, (old, _new) in self.changes.items():
            target.applied_positions[key] = old

    def merge_with(self, later: "Command") -> "Command | None":
        """Coalesce consecutive drags of the same notes.

        The canvas drag handler fires per mouse-move. Without merging, a single
        drag would push dozens of entries and undo would rewind it one pixel at
        a time.
        """
        if not isinstance(later, MoveNotes) or later.changes.keys() != self.changes.keys():
            return None
        merged = {
            key: (self.changes[key][0], later.changes[key][1])
            for key in self.changes
        }
        return MoveNotes(merged, self.label_id)


@dataclass
class SetNotePositions(Command):
    """Replace the whole position map, for Reset applied transforms."""

    before: dict[int, tuple[int, int]]
    after: dict[int, tuple[int, int]]
    label_id: str = "set_positions"

    def apply(self, target: EditTarget) -> None:
        target.applied_positions.clear()
        target.applied_positions.update(self.after)

    def revert(self, target: EditTarget) -> None:
        target.applied_positions.clear()
        target.applied_positions.update(self.before)


@dataclass
class SetNoteFields(Command):
    """Field changes on one hit object: don<->kat, big toggle, retime, type."""

    uid: int
    changes: dict[str, tuple[Any, Any]]
    label_id: str = "set_note_fields"

    def _note(self, target: EditTarget):
        for note in target.document.hit_objects:
            if note.uid == self.uid:
                return note
        raise KeyError(f"No hit object with uid {self.uid}")

    def apply(self, target: EditTarget) -> None:
        note = self._note(target)
        for name, (_old, new) in self.changes.items():
            setattr(note, name, new)

    def revert(self, target: EditTarget) -> None:
        note = self._note(target)
        for name, (old, _new) in self.changes.items():
            setattr(note, name, old)


@dataclass
class InsertHitObjects(Command):
    """Add hit objects. Stores them so revert can remove exactly these."""

    notes: list[Any]
    label_id: str = "insert_notes"

    def apply(self, target: EditTarget) -> None:
        target.document.hit_objects.extend(self.notes)
        target.document.hit_objects.sort(key=lambda note: note.time)

    def revert(self, target: EditTarget) -> None:
        removing = {note.uid for note in self.notes}
        target.document.hit_objects[:] = [
            note for note in target.document.hit_objects if note.uid not in removing
        ]


@dataclass
class RemoveHitObjects(Command):
    """Delete hit objects, keeping the objects themselves for revert."""

    notes: list[Any]
    label_id: str = "remove_notes"

    def apply(self, target: EditTarget) -> None:
        removing = {note.uid for note in self.notes}
        target.document.hit_objects[:] = [
            note for note in target.document.hit_objects if note.uid not in removing
        ]

    def revert(self, target: EditTarget) -> None:
        target.document.hit_objects.extend(self.notes)
        target.document.hit_objects.sort(key=lambda note: note.time)


@dataclass
class InsertTimingPoints(Command):
    """Add timing points. One SV sweep or barline pass is one of these."""

    points: list[Any]
    label_id: str = "insert_timing_points"

    def apply(self, target: EditTarget) -> None:
        target.document.timing_points.extend(self.points)
        target.document.timing_points.sort(key=lambda point: point.time)

    def revert(self, target: EditTarget) -> None:
        removing = {point.uid for point in self.points}
        target.document.timing_points[:] = [
            point for point in target.document.timing_points if point.uid not in removing
        ]


@dataclass
class RemoveTimingPoints(Command):
    points: list[Any]
    label_id: str = "remove_timing_points"

    def apply(self, target: EditTarget) -> None:
        removing = {point.uid for point in self.points}
        target.document.timing_points[:] = [
            point for point in target.document.timing_points if point.uid not in removing
        ]

    def revert(self, target: EditTarget) -> None:
        target.document.timing_points.extend(self.points)
        target.document.timing_points.sort(key=lambda point: point.time)


@dataclass
class EditTimingPoint(Command):
    """Field changes on one timing point, e.g. dragging a green line's SV."""

    uid: int
    changes: dict[str, tuple[Any, Any]]
    label_id: str = "edit_timing_point"

    def _point(self, target: EditTarget):
        for point in target.document.timing_points:
            if point.uid == self.uid:
                return point
        raise KeyError(f"No timing point with uid {self.uid}")

    def apply(self, target: EditTarget) -> None:
        point = self._point(target)
        for name, (_old, new) in self.changes.items():
            setattr(point, name, new)

    def revert(self, target: EditTarget) -> None:
        point = self._point(target)
        for name, (old, _new) in self.changes.items():
            setattr(point, name, old)

    def merge_with(self, later: "Command") -> "Command | None":
        """Coalesce a continuous drag on the same point and fields."""
        if not isinstance(later, EditTimingPoint):
            return None
        if later.uid != self.uid or later.changes.keys() != self.changes.keys():
            return None
        merged = {
            name: (self.changes[name][0], later.changes[name][1])
            for name in self.changes
        }
        return EditTimingPoint(self.uid, merged, self.label_id)


@dataclass
class CompositeCommand(Command):
    """Several commands as one undo step.

    This is what makes a 400-point eased SV sweep, or a full barline pass over a
    selection, a single Ctrl+Z rather than 400 of them.
    """

    commands: list[Command] = field(default_factory=list)
    label_id: str = "composite"

    def apply(self, target: EditTarget) -> None:
        for command in self.commands:
            command.apply(target)

    def revert(self, target: EditTarget) -> None:
        for command in reversed(self.commands):
            command.revert(target)
