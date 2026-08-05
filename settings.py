from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QKeySequenceEdit,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

ORGANIZATION_NAME = "jimmyreturnz"
APPLICATION_NAME = "TaikoFancyArranger"


class SettingsManager:
    """Small typed wrapper around QSettings for Taiko Fancy Arranger."""

    def __init__(self) -> None:
        self._settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)

    def value(self, key: str, default=None):
        return self._settings.value(key, default)

    def set_value(self, key: str, value) -> None:
        self._settings.setValue(key, value)

    def sync(self) -> None:
        self._settings.sync()

    def clear_all(self) -> None:
        self._settings.clear()
        self._settings.sync()

    def bool_value(self, key: str, default: bool = False) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def string_value(self, key: str, default: str = "") -> str:
        value = self._settings.value(key, default)
        return default if value is None else str(value)

    def storage_name(self) -> str:
        return self._settings.fileName()


@dataclass(frozen=True, slots=True)
class ShortcutDefinition:
    action_id: str
    label: str
    category: str
    default: str


SHORTCUT_DEFINITIONS: tuple[ShortcutDefinition, ...] = (
    ShortcutDefinition("play_pause", "Play/Pause", "Playback", "Space"),
    ShortcutDefinition("undo", "Undo", "Editing", "Ctrl+Z"),
    ShortcutDefinition("redo", "Redo", "Editing", "Ctrl+Y"),
)


class ShortcutRegistry:
    """Stores configurable keyboard shortcuts while keeping stable action IDs."""

    def __init__(self, settings: SettingsManager) -> None:
        self.settings = settings
        self.definitions = {item.action_id: item for item in SHORTCUT_DEFINITIONS}

    @staticmethod
    def key(action_id: str) -> str:
        return f"shortcuts/{action_id}"

    def default_sequence(self, action_id: str) -> str:
        return self.definitions[action_id].default

    def sequence(self, action_id: str) -> str:
        default = self.default_sequence(action_id)
        value = self.settings.string_value(self.key(action_id), default).strip()
        return value or default

    def set_sequence(self, action_id: str, sequence: str) -> None:
        normalized = normalize_sequence(sequence)
        self.settings.set_value(self.key(action_id), normalized or self.default_sequence(action_id))

    def restore_default(self, action_id: str) -> None:
        self.settings.set_value(self.key(action_id), self.default_sequence(action_id))

    def all_sequences(self) -> dict[str, str]:
        return {action_id: self.sequence(action_id) for action_id in self.definitions}


def normalize_sequence(value: str | QKeySequence) -> str:
    sequence = value if isinstance(value, QKeySequence) else QKeySequence(str(value))
    return sequence.toString(QKeySequence.PortableText).strip()


def duplicate_shortcuts(values: dict[str, str]) -> list[tuple[str, str, str]]:
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str, str]] = []
    for action_id, raw_sequence in values.items():
        sequence = normalize_sequence(raw_sequence)
        if not sequence:
            continue
        if sequence in seen:
            duplicates.append((sequence, seen[sequence], action_id))
        else:
            seen[sequence] = action_id
    return duplicates


def should_ignore_shortcut_focus(widget: QWidget | None) -> bool:
    """Return True when app-level shortcuts should not override text editing."""
    while widget is not None:
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QKeySequenceEdit)):
            return True
        if isinstance(widget, QAbstractSpinBox):
            return True
        if isinstance(widget, QComboBox) and widget.isEditable():
            return True
        widget = widget.parentWidget()
    return False
