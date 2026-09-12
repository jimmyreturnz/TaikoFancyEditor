from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QKeySequence

from skin import available_skins, skins_root
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QKeySequenceEdit,
)

import updater
from settings import (
    NOTE_OPACITY_DEFAULT_PERCENT,
    NOTE_OPACITY_MIN_PERCENT,
    SettingsManager,
    ShortcutRegistry,
    duplicate_shortcuts,
    normalize_sequence,
)


class SettingsDialog(QDialog):
    """Plain native Settings dialog for Phase 1 infrastructure."""

    def __init__(self, settings: SettingsManager, shortcuts: ShortcutRegistry, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.shortcuts = shortcuts
        self.setWindowTitle(self.tr("Settings"))
        self.resize(760, 500)
        # Small enough to fit a short laptop screen once the pages scroll.
        self.setMinimumSize(560, 360)
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
        self._update_check: object | None = None
        self._build_ui()
        self._load_current_values()
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Never open taller or wider than the screen it opens on.

        The size above is a preference, not a promise: pages grow as settings
        are added, and a laptop's work area is smaller than the desktop the
        number was picked on. Clamped to the *available* geometry, so a taskbar
        or a dock is already discounted -- and the pages scroll, so clamping
        hides nothing.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.resize(
            min(self.width(), int(available.width() * 0.9)),
            min(max(self.height(), self.sizeHint().height()),
                int(available.height() * 0.9)),
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        body = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setFixedWidth(160)
        self.pages = QStackedWidget()
        self._page_ids: list[str] = []
        for page_id, title, page in (
            ("general", self.tr("General"), self._general_page()),
            ("audio", self.tr("Audio"), self._audio_page()),
            ("skin", self.tr("Skin"), self._skin_page()),
            ("language", self.tr("Language"), self._language_page()),
            ("shortcuts", self.tr("Shortcuts"), self._shortcuts_page()),
            ("advanced", self.tr("Advanced"), self._advanced_page()),
        ):
            self.nav.addItem(QListWidgetItem(title))
            self.pages.addWidget(page)
            self._page_ids.append(page_id)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        # The pages scroll rather than forcing the dialog to be as tall as the
        # tallest of them. Without this the Audio page alone -- offset,
        # calibration, skin, and the paragraphs explaining each -- decides the
        # window height for every page, and on a short screen the buttons at
        # the bottom go off the edge where they cannot be reached.
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setWidget(self.pages)
        body.addWidget(scroller, 1)
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
        self.check_updates_on_startup = QCheckBox(self.tr("Check for updates on startup"))
        layout.addWidget(self.check_updates_on_startup)
        self.check_updates_button = QPushButton(self.tr("Check for updates now"))
        self.check_updates_button.clicked.connect(self._check_for_updates)
        layout.addWidget(self.check_updates_button, 0, Qt.AlignLeft)
        layout.addStretch(1)
        return page

    def _check_for_updates(self) -> None:
        # Held so the worker is not garbage collected while it is running.
        self._update_check = updater.check_now(self, self.settings)

    def _audio_page(self) -> QWidget:
        # Pink +/- spin buttons are applied to every QSpinBox from gui.py
        # after construction (see ImageTraceDialog) -- nothing to do here.
        page = QWidget()
        form = QFormLayout(page)
        self.hitsounds_enabled = QCheckBox(self.tr("Hitsounds"))
        form.addRow(self.hitsounds_enabled)
        self.hitsound_volume = QSpinBox()
        self.hitsound_volume.setRange(0, 100)
        self.hitsound_volume.setSuffix("%")
        form.addRow(self.tr("Hitsound volume"), self.hitsound_volume)
        self.hitsound_offset_ms = QSpinBox()
        # A trim against the music, not a latency any more. The notes are mixed
        # into the music stream (`audio_engine.HitsoundMixer`), so they already
        # carry exactly the device latency the music does and Music offset
        # below covers both. Kept, and kept negative-capable, because this is
        # the physical world and somebody will want to nudge it.
        self.hitsound_offset_ms.setRange(-500, 500)
        form.addRow(self.tr("Hitsound offset (ms)"), self.hitsound_offset_ms)
        offset_note = QLabel(self.tr(
            "Nudge hitsounds earlier or later against the music. Your device's "
            "latency is already covered by Music offset below, because the "
            "notes leave through the same output as the song."
        ))
        offset_note.setWordWrap(True)
        form.addRow("", offset_note)
        self.music_volume = QSpinBox()
        self.music_volume.setRange(0, 100)
        self.music_volume.setSuffix("%")
        form.addRow(self.tr("Music volume"), self.music_volume)
        # **This** is the device latency, and now the only setting that is.
        # Both the music and the notes leave through one sink, so one number
        # covers both -- the hitsound offset above is a trim between them
        # rather than a second latency. Ships at zero: the right value is a
        # property of the user's device, not something to guess, which is what
        # the Calibrate button is for.
        self.output_offset_ms = QSpinBox()
        self.output_offset_ms.setRange(-500, 500)
        offset_row = QHBoxLayout()
        offset_row.addWidget(self.output_offset_ms)
        self.calibrate_button = QPushButton(self.tr("Calibrate…"))
        self.calibrate_button.clicked.connect(self._calibrate_offset)
        offset_row.addWidget(self.calibrate_button)
        offset_row.addStretch(1)
        form.addRow(self.tr("Music offset (ms)"), offset_row)
        output_note = QLabel(self.tr(
            "Shift the playhead to match when the music actually reaches your "
            "ears. Raise it if the notes look early against what you hear. "
            "Measured in real time, so one value stays correct at every "
            "playback speed."
        ))
        output_note.setWordWrap(True)
        form.addRow("", output_note)
        return page

    def _skin_page(self) -> QWidget:
        """Skin selection. Its own page rather than a row on the Audio one:
        nothing here is about sound."""
        page = QWidget()
        form = QFormLayout(page)
        # Skins live beside the songs folder the user already chose, so this
        # asks for nothing new. Only folders carrying taiko note art are
        # offered -- an osu! install collects dozens of skins for other modes,
        # and listing those would be a menu of choices that change nothing.
        self.skin_combo = QComboBox()
        self.skin_combo.addItem(self.tr("Built-in"), "")
        root = skins_root(self.settings.string_value("library/songs_folder", ""))
        for name in available_skins(root):
            self.skin_combo.addItem(name, name)
        form.addRow(self.tr("Gameplay skin"), self.skin_combo)
        skin_note = QLabel(self.tr(
            "Uses the note, drumroll and hit-explosion art from one of your "
            "osu! skins in the gameplay preview. Anything a skin does not "
            "provide falls back to the built-in drawing."
        ))
        skin_note.setWordWrap(True)
        form.addRow("", skin_note)

        # A slider, because this is a look rather than a number: nobody knows
        # they want 62%, they want it a bit fainter than it is. The readout
        # beside it is what makes the position mean something.
        self.note_opacity = QSlider(Qt.Horizontal)
        self.note_opacity.setRange(NOTE_OPACITY_MIN_PERCENT, 100)
        self.note_opacity.setSingleStep(1)
        self.note_opacity.setPageStep(10)
        self.note_opacity.setTickInterval(10)
        self.note_opacity.setTickPosition(QSlider.TicksBelow)
        self.note_opacity_value = QLabel()
        self.note_opacity_value.setMinimumWidth(44)
        self.note_opacity.valueChanged.connect(
            lambda percent: self.note_opacity_value.setText(f"{percent} %"))
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.note_opacity, 1)
        opacity_row.addWidget(self.note_opacity_value)
        form.addRow(self.tr("Note opacity"), opacity_row)
        opacity_note = QLabel(self.tr(
            "How solid notes are drawn in the editor timeline layers. Lower "
            "leaves the snap grid and the lines behind them easier to read "
            "through a dense section; 100% draws them opaque. The gameplay "
            "preview is unaffected."
        ))
        opacity_note.setWordWrap(True)
        form.addRow("", opacity_note)
        return page

    def _calibrate_offset(self) -> None:
        """Tap along to a click track and take the number it settles on.

        Only ever writes the spin box above, which is the app-wide music
        offset. No beatmap's own offset is read or written by any of this --
        the value being measured belongs to the sound card, not to a chart.
        """
        from offset_calibration import OffsetCalibrationDialog

        dialog = OffsetCalibrationDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.result_offset is not None:
            self.output_offset_ms.setValue(dialog.result_offset)

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
        self.shortcuts_table = QTableWidget(0, 2)
        self.shortcuts_table.setHorizontalHeaderLabels([self.tr("Action"), self.tr("Shortcut")])
        header = self.shortcuts_table.horizontalHeader()
        # Action sizes to its longest label -- which is a translation, so no
        # fixed width can be right in both languages -- and the editor column
        # takes what is left.
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        # The category is a spanned header row rather than a column: with a
        # tool set per gimmick layer the list is long enough that repeating
        # "Tools -- Fake sliders" on nine consecutive rows says less than one
        # heading above them does. The table scrolls, so the page stays the
        # same height however many layers exist.
        self.shortcuts_table.verticalHeader().setVisible(False)
        current_category = None
        for definition in self.shortcuts.definitions.values():
            if definition.category != current_category:
                current_category = definition.category
                row = self.shortcuts_table.rowCount()
                self.shortcuts_table.insertRow(row)
                heading = QTableWidgetItem(self.tr(definition.category))
                font = heading.font()
                font.setBold(True)
                heading.setFont(font)
                # A heading is not a row anyone edits or picks.
                heading.setFlags(Qt.NoItemFlags)
                self.shortcuts_table.setItem(row, 0, heading)
                self.shortcuts_table.setSpan(row, 0, 1, 2)
            row = self.shortcuts_table.rowCount()
            self.shortcuts_table.insertRow(row)
            self.shortcuts_table.setItem(row, 0, QTableWidgetItem(self.tr(definition.label)))
            editor = QKeySequenceEdit()
            editor.setProperty("action_id", definition.action_id)
            self.shortcuts_table.setCellWidget(row, 1, editor)
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
        self.reset_all_button = QPushButton(self.tr("Reset All Settings"))
        self.reset_all_button.clicked.connect(self._reset_all_settings)
        layout.addWidget(storage_label)
        layout.addWidget(self.storage_value)
        layout.addSpacing(12)
        layout.addWidget(self.reset_all_button, 0, Qt.AlignLeft)
        layout.addStretch(1)
        return page

    def _load_current_values(self) -> None:
        self.confirm_overwrite.setChecked(self.settings.bool_value("general/confirm_overwrite", True))
        self.check_updates_on_startup.setChecked(
            self.settings.bool_value(updater.SETTING_CHECK_ON_STARTUP, True)
        )
        self.hitsounds_enabled.setChecked(self.settings.bool_value("audio/hitsounds_enabled", True))
        self.hitsound_volume.setValue(self.settings.int_value("audio/hitsound_volume", 70))
        self.hitsound_offset_ms.setValue(self.settings.int_value("audio/hitsound_offset_ms", 0))
        self.music_volume.setValue(self.settings.int_value("audio/music_volume", 65))
        self.output_offset_ms.setValue(self.settings.int_value("audio/output_offset_ms", 0))
        self.note_opacity.setValue(self.settings.int_value(
            "appearance/note_opacity", NOTE_OPACITY_DEFAULT_PERCENT))
        chosen = self.settings.string_value("appearance/skin", "")
        self.skin_combo.setCurrentIndex(max(0, self.skin_combo.findData(chosen)))
        language = self.settings.string_value("language/current", "en")
        index = self.language_combo.findData(language if language in {"en", "ja"} else "en")
        self.language_combo.setCurrentIndex(max(0, index))
        for action_id, editor in self._shortcut_editors.items():
            editor.setKeySequence(QKeySequence(self.shortcuts.sequence(action_id)))

    def _restore_current_page_defaults(self) -> None:
        """Reset whichever page is showing.

        Dispatched on the page's own identifier rather than its position:
        adding the Skin page between Audio and Language silently shifted every
        index below it, so Restore Defaults on Language reset the shortcuts and
        the last page pointed past the end.
        """
        page = self._page_ids[self.pages.currentIndex()]
        if page == "general":
            self.confirm_overwrite.setChecked(True)
            self.check_updates_on_startup.setChecked(True)
        elif page == "audio":
            self.hitsounds_enabled.setChecked(True)
            self.hitsound_volume.setValue(70)
            self.hitsound_offset_ms.setValue(0)
            self.output_offset_ms.setValue(0)
            self.music_volume.setValue(65)
        elif page == "skin":
            self.skin_combo.setCurrentIndex(0)
            self.note_opacity.setValue(NOTE_OPACITY_DEFAULT_PERCENT)
        elif page == "language":
            self.language_combo.setCurrentIndex(self.language_combo.findData("en"))
        elif page == "shortcuts":
            for action_id, editor in self._shortcut_editors.items():
                editor.setKeySequence(QKeySequence(self.shortcuts.default_sequence(action_id)))
        elif page == "advanced":
            QMessageBox.information(self, self.tr("Settings"), self.tr("Use Reset All Settings to clear every saved setting."))

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
        self.settings.set_value(
            updater.SETTING_CHECK_ON_STARTUP, self.check_updates_on_startup.isChecked()
        )
        self.settings.set_value("audio/hitsounds_enabled", self.hitsounds_enabled.isChecked())
        self.settings.set_value("audio/hitsound_volume", self.hitsound_volume.value())
        self.settings.set_value("audio/hitsound_offset_ms", self.hitsound_offset_ms.value())
        self.settings.set_value("audio/music_volume", self.music_volume.value())
        self.settings.set_value("audio/output_offset_ms", self.output_offset_ms.value())
        self.settings.set_value("appearance/skin", str(self.skin_combo.currentData()))
        self.settings.set_value("appearance/note_opacity", self.note_opacity.value())
        self.settings.set_value("language/current", selected_language)
        for action_id, sequence in self._shortcut_values().items():
            self.shortcuts.set_sequence(action_id, sequence)
        self.settings.sync()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_reload_shortcuts"):
            parent._reload_shortcuts()
        if selected_language != previous_language:
            self._prompt_language_restart()
        return True

    def _prompt_language_restart(self) -> None:
        """Ask to restart, in the language just chosen -- not the old one.

        Qt does not retranslate widgets that already exist, so switching the
        catalog here leaves the rest of the running UI in the previous
        language; that is exactly what the restart is for. But the prompt
        itself is built *after* the switch, so someone who has just picked
        Japanese is asked in Japanese rather than in the language they were
        moving away from.
        """
        import i18n

        i18n.install_translator(QApplication.instance(), self.settings)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(self.tr("Restart required"))
        box.setText(self.tr("Please restart Taiko Fancy Arranger to apply the language change."))
        restart_button = box.addButton(self.tr("Restart Now"), QMessageBox.AcceptRole)
        box.addButton(self.tr("Restart Later"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is restart_button:
            self._restart_application()

    def _restart_application(self) -> None:
        """Relaunch this program and quit the current instance.

        startDetached before quit, so the new process is not a child of one
        that is about to exit. A frozen build is its own executable and takes
        only the original arguments; a source run needs the interpreter in
        front of them.
        """
        if getattr(sys, "frozen", False):
            program, arguments = sys.executable, sys.argv[1:]
        else:
            program, arguments = sys.executable, sys.argv
        if not QProcess.startDetached(program, arguments, str(Path.cwd())):
            QMessageBox.warning(
                self,
                self.tr("Restart required"),
                self.tr("Could not restart automatically. Please close and reopen the program."),
            )
            return
        QApplication.quit()

    def _ok(self) -> None:
        if self.apply():
            self.accept()