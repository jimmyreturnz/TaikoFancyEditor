from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QKeySequenceEdit,
)

from settings import SettingsManager, ShortcutRegistry, duplicate_shortcuts, normalize_sequence


class SettingsDialog(QDialog):
    """Plain native Settings dialog for Phase 1 infrastructure."""

    def __init__(self, settings: SettingsManager, shortcuts: ShortcutRegistry, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.shortcuts = shortcuts
        self.setWindowTitle(self.tr("Settings"))
        self.resize(760, 500)
        self.setStyleSheet(
            """
            QDialog, QWidget { background: #ffffff; color: #000000; }
            QPushButton { background: #f0f0f0; color: #000000; border: 1px solid #9a9a9a; border-radius: 3px; padding: 5px 10px; }
            QPushButton:hover { background: #e6e6e6; }
            QComboBox, QLineEdit, QKeySequenceEdit { background: #ffffff; color: #000000; border: 1px solid #9a9a9a; padding: 3px; }
            QListWidget, QTableWidget { background: #ffffff; color: #000000; border: 1px solid #b8b8b8; }
            """
        )
        self._shortcut_editors: dict[str, QKeySequenceEdit] = {}
        self._build_ui()
        self._load_current_values()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        body = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setFixedWidth(160)
        self.pages = QStackedWidget()
        for title, page in (
            (self.tr("General"), self._general_page()),
            (self.tr("Language"), self._language_page()),
            (self.tr("Shortcuts"), self._shortcuts_page()),
            (self.tr("Advanced"), self._advanced_page()),
        ):
            self.nav.addItem(QListWidgetItem(title))
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        self.restore_button = QPushButton(self.tr("Restore Defaults"))
        self.restore_button.clicked.connect(self._restore_current_page_defaults)
        footer.addWidget(self.restore_button)
        footer.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Apply | QDialogButtonBox.Ok)
        self.buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        root.addLayout(footer)

    def _general_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.confirm_overwrite = QCheckBox(self.tr("Confirm before overwriting original beatmap"))
        layout.addWidget(self.confirm_overwrite)
        layout.addStretch(1)
        return page

    def _language_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("日本語", "ja")
        form.addRow(self.tr("Language"), self.language_combo)
        note = QLabel(self.tr("Restart Taiko Fancy Arranger to apply the interface language."))
        note.setWordWrap(True)
        form.addRow("", note)
        return page

    def _shortcuts_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.shortcuts_table = QTableWidget(0, 3)
        self.shortcuts_table.setHorizontalHeaderLabels([self.tr("Action"), self.tr("Category"), self.tr("Shortcut")])
        self.shortcuts_table.horizontalHeader().setStretchLastSection(True)
        for row, definition in enumerate(self.shortcuts.definitions.values()):
            self.shortcuts_table.insertRow(row)
            self.shortcuts_table.setItem(row, 0, QTableWidgetItem(self.tr(definition.label)))
            self.shortcuts_table.setItem(row, 1, QTableWidgetItem(self.tr(definition.category)))
            editor = QKeySequenceEdit()
            editor.setProperty("action_id", definition.action_id)
            self.shortcuts_table.setCellWidget(row, 2, editor)
            self._shortcut_editors[definition.action_id] = editor
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #b00020;")
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.shortcuts_table)
        layout.addWidget(self.validation_label)
        return page

    def _advanced_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        storage_label = QLabel(self.tr("Settings storage location:"))
        self.storage_value = QLabel(self.settings.storage_name())
        self.storage_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reset_all_button = QPushButton(self.tr("Reset all settings"))
        self.reset_all_button.clicked.connect(self._reset_all_settings)
        layout.addWidget(storage_label)
        layout.addWidget(self.storage_value)
        layout.addSpacing(12)
        layout.addWidget(self.reset_all_button, 0, Qt.AlignLeft)
        layout.addStretch(1)
        return page

    def _load_current_values(self) -> None:
        self.confirm_overwrite.setChecked(self.settings.bool_value("general/confirm_overwrite", True))
        language = self.settings.string_value("language/current", "en")
        index = self.language_combo.findData(language if language in {"en", "ja"} else "en")
        self.language_combo.setCurrentIndex(max(0, index))
        for action_id, editor in self._shortcut_editors.items():
            editor.setKeySequence(QKeySequence(self.shortcuts.sequence(action_id)))

    def _restore_current_page_defaults(self) -> None:
        page = self.pages.currentIndex()
        if page == 0:
            self.confirm_overwrite.setChecked(True)
        elif page == 1:
            self.language_combo.setCurrentIndex(self.language_combo.findData("en"))
        elif page == 2:
            for action_id, editor in self._shortcut_editors.items():
                editor.setKeySequence(QKeySequence(self.shortcuts.default_sequence(action_id)))
        elif page == 3:
            QMessageBox.information(self, self.tr("Settings"), self.tr("Use Reset all settings to clear every saved setting."))

    def _reset_all_settings(self) -> None:
        if QMessageBox.question(self, self.tr("Reset all settings"), self.tr("Clear every saved setting and restore defaults?")) != QMessageBox.Yes:
            return
        self.settings.clear_all()
        self._load_current_values()

    def _shortcut_values(self) -> dict[str, str]:
        return {action_id: normalize_sequence(editor.keySequence()) for action_id, editor in self._shortcut_editors.items()}

    def _validate(self) -> bool:
        duplicates = duplicate_shortcuts(self._shortcut_values())
        if duplicates:
            sequence, first, second = duplicates[0]
            first_label = self.shortcuts.definitions[first].label
            second_label = self.shortcuts.definitions[second].label
            self.validation_label.setText(self.tr("Shortcut conflict: {sequence} is assigned to {first} and {second}.").format(sequence=sequence, first=first_label, second=second_label))
            return False
        self.validation_label.setText("")
        return True

    def apply(self) -> bool:
        if not self._validate():
            return False
        previous_language = self.settings.string_value("language/current", "en")
        selected_language = str(self.language_combo.currentData())
        self.settings.set_value("general/confirm_overwrite", self.confirm_overwrite.isChecked())
        self.settings.set_value("language/current", selected_language)
        for action_id, sequence in self._shortcut_values().items():
            self.shortcuts.set_sequence(action_id, sequence)
        self.settings.sync()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_reload_shortcuts"):
            parent._reload_shortcuts()
        if selected_language != previous_language:
            QMessageBox.information(
                self,
                self.tr("Restart required"),
                self.tr("Please restart Taiko Fancy Arranger to apply the language change."),
                QMessageBox.Ok,
            )
        return True

    def _ok(self) -> None:
        if self.apply():
            self.accept()
