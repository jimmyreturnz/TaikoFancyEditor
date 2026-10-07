from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen

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
    QLineEdit,
)

from config_sheet import DIALOG_STYLE, ConfigSheet, bind_segments, ui_asset

import theme
import updater
from smooth_scroll import smooth
from audio_engine import DEFAULT_HITSOUND_OFFSET_MS
from settings import (
    NOTE_OPACITY_DEFAULT_PERCENT,
    NOTE_OPACITY_MIN_PERCENT,
    SettingsManager,
    ShortcutRegistry,
    duplicate_shortcuts,
    normalize_sequence,
)


class SettingsDialog(QDialog):
    """The app-wide settings, in the window's own theme."""

    def __init__(self, settings: SettingsManager, shortcuts: ShortcutRegistry, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.shortcuts = shortcuts
        self.setWindowTitle(self.tr("Settings"))
        self.resize(880, 620)
        # Small enough to fit a short laptop screen once the pages scroll.
        self.setMinimumSize(560, 360)
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
        """Header, a page list with icons, the page, and a footer.

        Each page is a small config sheet of section cards, so the pages look
        like every other settings surface in the app; the page list stays a
        list (not one long scroll) because the Shortcuts table scrolls on its
        own and a table inside a scrolling column is two scroll bars fighting.
        """
        self.setStyleSheet(DIALOG_STYLE + """
            QListWidget#settingsNav { background: transparent; border: 0; border-right: 1px solid #303947;
                outline: 0; padding: 10px 8px 10px 0; font-size: 13px; font-weight: 600; }
            QListWidget#settingsNav::item { color: #aeb8c5; padding: 7px 10px; border-left: 3px solid transparent; }
            QListWidget#settingsNav::item:hover { background: #2a3341; color: #e8edf3; }
            QListWidget#settingsNav::item:selected { background: #2a2230; color: #ffffff; border-left: 3px solid #ff66aa; }
            QTableWidget { background: #1b212b; border: 1px solid #303947; border-radius: 6px; gridline-color: #262e3a; }
            QHeaderView::section { background: #1e2530; color: #7d8794; border: 0; border-bottom: 1px solid #303947;
                padding: 6px 8px; font-size: 11px; font-weight: 700; }
            QLineEdit#shortcutSearch { background: #252d39; border: 1px solid #3a4554; border-radius: 6px; padding: 6px 10px; }
            QLineEdit#shortcutSearch:focus { border-color: #ff66aa; }
            QKeySequenceEdit QLineEdit { background: transparent; border: 1px dashed transparent; border-radius: 5px; }
            QKeySequenceEdit QLineEdit:hover { border-color: #3a4554; }
            QKeySequenceEdit QLineEdit:focus { border-color: #ff66aa; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QFrame()
        head.setObjectName("sheetHead")
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(16, 12, 16, 12)
        head_layout.setSpacing(1)
        title = QLabel(self.tr("Settings"))
        title.setObjectName("sheetTitle")
        subtitle = QLabel(self.tr("For the whole app. Saved on this computer."))
        subtitle.setObjectName("sheetSub")
        head_layout.addWidget(title)
        head_layout.addWidget(subtitle)
        root.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(196)
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.pages = QStackedWidget()
        self._page_ids: list[str] = []
        for page_id, title_text, page in (
            ("general", self.tr("General"), self._general_page()),
            ("audio", self.tr("Audio"), self._audio_page()),
            ("skin", self.tr("Skin"), self._skin_page()),
            ("language", self.tr("Language"), self._language_page()),
            ("shortcuts", self.tr("Shortcuts"), self._shortcuts_page()),
            ("advanced", self.tr("Advanced"), self._advanced_page()),
        ):
            item = QListWidgetItem(QIcon(ui_asset(f"settings-{page_id}.svg")), title_text)
            self.nav.addItem(item)
            self.pages.addWidget(page)
            self._page_ids.append(page_id)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        # Each page scrolls itself (a ConfigSheet's column), so a short
        # screen never pushes the buttons at the bottom off the edge.
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

        foot = QFrame()
        foot.setObjectName("sheetFoot")
        footer = QHBoxLayout(foot)
        footer.setContentsMargins(16, 10, 16, 10)
        footer.setSpacing(8)
        self.restore_button = QPushButton(self.tr("Restore Defaults"))
        self.restore_button.setProperty("role", "ghost")
        self.restore_button.clicked.connect(self._restore_current_page_defaults)
        footer.addWidget(self.restore_button)
        footer.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Apply | QDialogButtonBox.Ok)
        for kind in (QDialogButtonBox.Cancel, QDialogButtonBox.Apply):
            button = self.buttons.button(kind)
            button.setProperty("role", "ghost")
            button.setStyleSheet("QPushButton { border: 1px solid #3a4554; }")
        self.buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        root.addWidget(foot)

    @staticmethod
    def _sheet() -> ConfigSheet:
        return ConfigSheet(rail=False, explain=False)

    def _volume_row(self, spin: QSpinBox) -> QWidget:
        """A slider for a volume, with its box beside it.

        The box stays the value (apply() and Restore read it); the slider is
        how "a bit quieter" is asked for, which is what a volume is.
        """
        slider = QSlider(Qt.Horizontal)
        slider.setRange(spin.minimum(), spin.maximum())
        slider.setValue(spin.value())
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        spin.setFixedWidth(96)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(slider, 1)
        layout.addWidget(spin)
        return row

    def _general_page(self) -> QWidget:
        sheet = self._sheet()
        saving = sheet.add_section("saving", self.tr("Saving"))
        self.confirm_overwrite = QCheckBox(self.tr("Confirm before overwriting original beatmap"))
        saving.switch(self.confirm_overwrite, self.tr("Asks once per save when the file on disk is the one you opened."))
        updates = sheet.add_section("updates", self.tr("Updates"))
        self.check_updates_on_startup = QCheckBox(self.tr("Check for updates on startup"))
        updates.switch(self.check_updates_on_startup, self.tr("One request to GitHub when the app opens."))
        self.check_updates_button = QPushButton(self.tr("Check for updates now"))
        self.check_updates_button.setProperty("role", "ghost")
        self.check_updates_button.setStyleSheet("QPushButton { border: 1px solid #3a4554; }")
        self.check_updates_button.clicked.connect(self._check_for_updates)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.check_updates_button)
        row_layout.addStretch(1)
        updates.add(row, 2)
        return sheet

    def _check_for_updates(self) -> None:
        # Held so the worker is not garbage collected while it is running.
        # The main window owns the restart: it asks about unsaved work and quits.
        restart = getattr(self.parent(), "restart_into_update", None)
        self._update_check = updater.check_now(self, self.settings, restart)

    def _audio_page(self) -> QWidget:
        sheet = self._sheet()
        hits = sheet.add_section("hitsounds", self.tr("Hitsounds"))
        self.hitsounds_enabled = QCheckBox(self.tr("Hitsounds"))
        hits.switch(self.hitsounds_enabled, self.tr("The skin's own samples when a skin is chosen."))
        self.hitsound_volume = QSpinBox()
        self.hitsound_volume.setRange(0, 100)
        self.hitsound_volume.setSuffix("%")
        hits.field(self.tr("Hitsound volume"), self._volume_row(self.hitsound_volume), span=2,
                   scrub=self.hitsound_volume)
        self.hitsound_offset_ms = QSpinBox()
        # A trim against the music, not a latency any more. The notes are mixed
        # into the music stream (`audio_engine.HitsoundMixer`), so they already
        # carry exactly the device latency the music does and Music offset
        # below covers both. Kept, and kept negative-capable, because this is
        # the physical world and somebody will want to nudge it.
        self.hitsound_offset_ms.setRange(-500, 500)
        self.hitsound_offset_ms.setSuffix(" ms")
        hits.field(self.tr("Hitsound offset (ms)"), self.hitsound_offset_ms)
        offset_note = QLabel(self.tr(
            "Nudge hitsounds earlier or later against the music. Your device's "
            "latency is already covered by Music offset below, because the "
            "notes leave through the same output as the song."
        ))
        offset_note.setObjectName("fieldNote")
        offset_note.setWordWrap(True)
        hits.add(offset_note, 2)

        music = sheet.add_section("music", self.tr("Music"))
        self.music_volume = QSpinBox()
        self.music_volume.setRange(0, 100)
        self.music_volume.setSuffix("%")
        music.field(self.tr("Music volume"), self._volume_row(self.music_volume), span=2,
                    scrub=self.music_volume)
        # **This** is the device latency, and now the only setting that is.
        # Both the music and the notes leave through one sink, so one number
        # covers both -- the hitsound offset above is a trim between them
        # rather than a second latency. Ships at zero: the right value is a
        # property of the user's device, not something to guess, which is what
        # the Calibrate button is for.
        self.output_offset_ms = QSpinBox()
        self.output_offset_ms.setRange(-500, 500)
        self.output_offset_ms.setSuffix(" ms")
        offset_row = QWidget()
        offset_layout = QHBoxLayout(offset_row)
        offset_layout.setContentsMargins(0, 0, 0, 0)
        offset_layout.addWidget(self.output_offset_ms, 1)
        self.calibrate_button = QPushButton(self.tr("Calibrate…"))
        self.calibrate_button.setProperty("role", "ghost")
        self.calibrate_button.setStyleSheet("QPushButton { border: 1px solid #3a4554; }")
        self.calibrate_button.clicked.connect(self._calibrate_offset)
        offset_layout.addWidget(self.calibrate_button)
        music.field(self.tr("Music offset (ms)"), offset_row, scrub=self.output_offset_ms)
        output_note = QLabel(self.tr(
            "Shift the playhead to match when the music actually reaches your "
            "ears. Raise it if the notes look early against what you hear. "
            "Measured in real time, so one value stays correct at every "
            "playback speed."
        ))
        output_note.setObjectName("fieldNote")
        output_note.setWordWrap(True)
        music.add(output_note, 2)
        return sheet

    def _skin_page(self) -> QWidget:
        """Skin selection. Its own page rather than a row on the Audio one:
        nothing here is about sound."""
        sheet = self._sheet()
        look = sheet.add_section("theme", self.tr("App theme"))
        self.theme_combo = QComboBox()
        for name, label in (
            ("osu", self.tr("osu!")),
            ("taiko", self.tr("Taiko")),
            ("lantern", self.tr("Lantern Rite")),
            ("gold", self.tr("Gold")),
            ("monokai", self.tr("Monokai")),
        ):
            self.theme_combo.addItem(label, name)
        look.field(self.tr("Theme"), bind_segments(self.theme_combo), span=2)
        theme_note = QLabel(self.tr(
            "The colours of the window around the chart. Notes, snap ticks and "
            "the SV graph keep osu!'s own. Applies after a restart."
        ))
        theme_note.setObjectName("fieldNote")
        theme_note.setWordWrap(True)
        look.add(theme_note, 2)

        skin = sheet.add_section("skin", self.tr("Gameplay preview"))
        # Skins live beside the songs folder the user already chose, so this
        # asks for nothing new. Only folders carrying taiko note art are
        # offered -- an osu! install collects dozens of skins for other modes,
        # and listing those would be a menu of choices that change nothing.
        self.skin_combo = QComboBox()
        self.skin_combo.addItem(self.tr("Built-in"), "")
        root = skins_root(self.settings.string_value("library/songs_folder", ""))
        for name in available_skins(root):
            self.skin_combo.addItem(name, name)
        skin.field(self.tr("Gameplay skin"), self.skin_combo, span=2)
        skin_note = QLabel(self.tr(
            "Uses the note, drumroll and hit-explosion art from one of your "
            "osu! skins in the gameplay preview. Anything a skin does not "
            "provide falls back to the built-in drawing."
        ))
        skin_note.setObjectName("fieldNote")
        skin_note.setWordWrap(True)
        skin.add(skin_note, 2)

        # A slider, because this is a look rather than a number: nobody knows
        # they want 62%, they want it a bit fainter than it is. The readout
        # beside it is what makes the position mean something, and the notes
        # under it show what the number looks like.
        opacity = sheet.add_section("opacity", self.tr("Editor timeline"))
        self.note_opacity = QSlider(Qt.Horizontal)
        self.note_opacity.setRange(NOTE_OPACITY_MIN_PERCENT, 100)
        self.note_opacity.setSingleStep(1)
        self.note_opacity.setPageStep(10)
        self.note_opacity_value = QLabel()
        self.note_opacity_value.setMinimumWidth(44)
        self.note_opacity.valueChanged.connect(
            lambda percent: self.note_opacity_value.setText(f"{percent} %"))
        opacity_row = QWidget()
        row_layout = QHBoxLayout(opacity_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.note_opacity, 1)
        row_layout.addWidget(self.note_opacity_value)
        opacity.field(self.tr("Note opacity"), opacity_row, span=2)
        self.note_opacity_preview = _NoteOpacityPreview(self.note_opacity, self.skin_combo)
        opacity.add(self.note_opacity_preview, 2)
        opacity_note = QLabel(self.tr(
            "How solid notes are drawn in the editor timeline layers. Lower "
            "leaves the snap grid and the lines behind them easier to read "
            "through a dense section; 100% draws them opaque. The gameplay "
            "preview is unaffected."
        ))
        opacity_note.setObjectName("fieldNote")
        opacity_note.setWordWrap(True)
        opacity.add(opacity_note, 2)

        # The same kind of control for the same reason; MainWindow listens to
        # this one while it moves, so the Editor and Gimmick pages show what
        # the number looks like and cancelling puts the saved value back.
        backdrop = sheet.add_section("backdrop", self.tr("Editor background"))
        self.background_opacity = QSlider(Qt.Horizontal)
        self.background_opacity.setRange(0, 100)
        self.background_opacity.setSingleStep(1)
        self.background_opacity.setPageStep(10)
        self.background_opacity_value = QLabel()
        self.background_opacity_value.setMinimumWidth(44)
        self.background_opacity.valueChanged.connect(
            lambda percent: self.background_opacity_value.setText(f"{percent} %"))
        backdrop_row = QWidget()
        backdrop_layout = QHBoxLayout(backdrop_row)
        backdrop_layout.setContentsMargins(0, 0, 0, 0)
        backdrop_layout.addWidget(self.background_opacity, 1)
        backdrop_layout.addWidget(self.background_opacity_value)
        backdrop.field(self.tr("Background opacity"), backdrop_row, span=2)
        backdrop_note = QLabel(self.tr(
            "How strongly the map's own background shows behind the Editor and "
            "Gimmick pages. 0% leaves them plain."
        ))
        backdrop_note.setObjectName("fieldNote")
        backdrop_note.setWordWrap(True)
        backdrop.add(backdrop_note, 2)
        # Live, like the one above, and for the same reason.
        self.view_opacity = QSlider(Qt.Horizontal)
        self.view_opacity.setRange(0, 100)
        self.view_opacity.setSingleStep(1)
        self.view_opacity.setPageStep(10)
        self.view_opacity_value = QLabel()
        self.view_opacity_value.setMinimumWidth(44)
        self.view_opacity.valueChanged.connect(
            lambda percent: self.view_opacity_value.setText(f"{percent} %"))
        view_row = QWidget()
        view_layout = QHBoxLayout(view_row)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.addWidget(self.view_opacity, 1)
        view_layout.addWidget(self.view_opacity_value)
        backdrop.field(self.tr("View opacity"), view_row, span=2)
        view_note = QLabel(self.tr(
            "How solid the views are. Under 100% the background shows through "
            "them as well as around them."
        ))
        view_note.setObjectName("fieldNote")
        view_note.setWordWrap(True)
        backdrop.add(view_note, 2)

        fancy = sheet.add_section("fancy", self.tr("Fancy Arranger"))
        self.transform_animation = QCheckBox(self.tr("Animate transforms"))
        fancy.switch(self.transform_animation, self.tr(
            "Notes glide to their new places when a transform changes. Off "
            "jumps straight there, which is lighter on a dense map."))
        return sheet

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
        sheet = self._sheet()
        section = sheet.add_section("language", self.tr("Language"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("日本語", "ja")
        section.field(self.tr("Language"), bind_segments(self.language_combo), span=2)
        note = QLabel(self.tr("Restart Taiko Fancy Arranger to apply the interface language."))
        note.setObjectName("fieldNote")
        note.setWordWrap(True)
        section.add(note, 2)
        return sheet

    def _shortcuts_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        # Forty-odd actions across the tool rows of every layer: finding one
        # by name (or finding what Ctrl+R does) beats scrolling for it.
        self.shortcut_search = QLineEdit()
        self.shortcut_search.setObjectName("shortcutSearch")
        self.shortcut_search.setPlaceholderText(self.tr("Find an action or a key"))
        self.shortcut_search.addAction(QIcon(ui_asset("settings-search.svg")), QLineEdit.LeadingPosition)
        self.shortcut_search.setClearButtonEnabled(True)
        self.shortcut_search.textChanged.connect(self._filter_shortcuts)
        layout.addWidget(self.shortcut_search)
        self.shortcuts_table = QTableWidget(0, 2)
        self.shortcuts_table.setHorizontalHeaderLabels([self.tr("Action"), self.tr("Shortcut")])
        self.shortcuts_table.setShowGrid(False)
        smooth(self.shortcuts_table)
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
        self._shortcut_rows: list[tuple[int, int | None, str]] = []
        current_category = None
        heading_row = None
        for definition in self.shortcuts.definitions.values():
            if definition.category != current_category:
                current_category = definition.category
                row = self.shortcuts_table.rowCount()
                self.shortcuts_table.insertRow(row)
                heading = QTableWidgetItem(self.tr(definition.category).upper())
                font = heading.font()
                font.setBold(True)
                font.setPointSizeF(font.pointSizeF() * 0.85)
                heading.setFont(font)
                heading.setForeground(theme.color("#7d8794"))
                # A heading is not a row anyone edits or picks.
                heading.setFlags(Qt.NoItemFlags)
                self.shortcuts_table.setItem(row, 0, heading)
                self.shortcuts_table.setSpan(row, 0, 1, 2)
                heading_row = row
                self._shortcut_rows.append((row, None, self.tr(definition.category)))
            row = self.shortcuts_table.rowCount()
            self.shortcuts_table.insertRow(row)
            self.shortcuts_table.setItem(row, 0, QTableWidgetItem(self.tr(definition.label)))
            editor = QKeySequenceEdit()
            editor.setProperty("action_id", definition.action_id)
            self.shortcuts_table.setCellWidget(row, 1, editor)
            self._shortcut_editors[definition.action_id] = editor
            self._shortcut_rows.append((row, heading_row, self.tr(definition.label)))
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #ffb347;")
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.shortcuts_table, 1)
        layout.addWidget(self.validation_label)
        return page

    def _filter_shortcuts(self, text: str) -> None:
        """Hide actions whose name and keys do not contain `text`; a heading
        stays while any action under it does."""
        wanted = text.strip().lower()
        shown_headings = set()
        for row, heading, label in self._shortcut_rows:
            if heading is None:
                continue
            editor = self.shortcuts_table.cellWidget(row, 1)
            keys = editor.keySequence().toString().lower() if editor is not None else ""
            visible = not wanted or wanted in label.lower() or wanted in keys
            self.shortcuts_table.setRowHidden(row, not visible)
            if visible:
                shown_headings.add(heading)
        for row, heading, label in self._shortcut_rows:
            if heading is None:
                self.shortcuts_table.setRowHidden(row, bool(wanted) and row not in shown_headings)

    def _advanced_page(self) -> QWidget:
        sheet = self._sheet()
        section = sheet.add_section("storage", self.tr("Settings storage location:").rstrip(":"))
        self.storage_value = QLabel(self.settings.storage_name())
        self.storage_value.setObjectName("fieldNote")
        self.storage_value.setWordWrap(True)
        self.storage_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        section.add(self.storage_value, 2)
        self.reset_all_button = QPushButton(self.tr("Reset All Settings"))
        self.reset_all_button.setProperty("role", "ghost")
        self.reset_all_button.setStyleSheet(
            "QPushButton { border: 1px solid #6b3a3a; color: #ff8a8a; }"
            "QPushButton:hover { background: #2a1f24; color: #ffb0b0; }")
        self.reset_all_button.clicked.connect(self._reset_all_settings)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.reset_all_button)
        row_layout.addStretch(1)
        section.add(row, 2)
        return sheet

    def _load_current_values(self) -> None:
        self.confirm_overwrite.setChecked(self.settings.bool_value("general/confirm_overwrite", True))
        self.transform_animation.setChecked(self.settings.bool_value("appearance/transform_animation", True))
        self.check_updates_on_startup.setChecked(
            self.settings.bool_value(updater.SETTING_CHECK_ON_STARTUP, True)
        )
        self.hitsounds_enabled.setChecked(self.settings.bool_value("audio/hitsounds_enabled", True))
        self.hitsound_volume.setValue(self.settings.int_value("audio/hitsound_volume", 70))
        self.hitsound_offset_ms.setValue(self.settings.int_value(
            "audio/hitsound_offset_ms", DEFAULT_HITSOUND_OFFSET_MS))
        self.music_volume.setValue(self.settings.int_value("audio/music_volume", 65))
        self.output_offset_ms.setValue(self.settings.int_value("audio/output_offset_ms", 0))
        self.note_opacity.setValue(self.settings.int_value(
            "appearance/note_opacity", NOTE_OPACITY_DEFAULT_PERCENT))
        self.background_opacity.setValue(self.settings.int_value("appearance/background_opacity", 25))
        self.view_opacity.setValue(self.settings.int_value("appearance/view_opacity", 100))
        chosen_theme = self.settings.string_value(theme.SETTING, theme.DEFAULT)
        chosen_theme = theme.RENAMED.get(chosen_theme, chosen_theme)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(chosen_theme)))
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
            self.transform_animation.setChecked(True)
            self.check_updates_on_startup.setChecked(True)
        elif page == "audio":
            self.hitsounds_enabled.setChecked(True)
            self.hitsound_volume.setValue(70)
            self.hitsound_offset_ms.setValue(DEFAULT_HITSOUND_OFFSET_MS)
            self.output_offset_ms.setValue(0)
            self.music_volume.setValue(65)
        elif page == "skin":
            self.theme_combo.setCurrentIndex(0)
            self.skin_combo.setCurrentIndex(0)
            self.note_opacity.setValue(NOTE_OPACITY_DEFAULT_PERCENT)
            self.background_opacity.setValue(25)
            self.view_opacity.setValue(100)
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
        # Against the theme this process is drawn in, not the stored one: a
        # change saved with "Restart Later" still needs the restart next time.
        selected_theme = str(self.theme_combo.currentData())
        self.settings.set_value(theme.SETTING, selected_theme)
        self.settings.set_value("general/confirm_overwrite", self.confirm_overwrite.isChecked())
        self.settings.set_value("appearance/transform_animation", self.transform_animation.isChecked())
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
        self.settings.set_value("appearance/background_opacity", self.background_opacity.value())
        self.settings.set_value("appearance/view_opacity", self.view_opacity.value())
        self.settings.set_value("language/current", selected_language)
        for action_id, sequence in self._shortcut_values().items():
            self.shortcuts.set_sequence(action_id, sequence)
        self.settings.sync()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_reload_shortcuts"):
            parent._reload_shortcuts()
        if selected_language != previous_language:
            self._prompt_language_restart()
        # The colours-as-written theme is the test suite's and is never
        # offered, so the box cannot show it: it is not a change to restart for.
        elif selected_theme != theme.active() and theme.active() != theme.AS_WRITTEN:
            self._prompt_restart(self.tr("Please restart Taiko Fancy Arranger to apply the theme."))
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
        self._prompt_restart(self.tr("Please restart Taiko Fancy Arranger to apply the language change."))

    def _prompt_restart(self, text: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(self.tr("Restart required"))
        box.setText(text)
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


class _NoteOpacityPreview(QWidget):
    """Three notes at the chosen opacity, over a snap grid, so the percent
    means something before Apply."""

    def __init__(self, slider: QSlider, skin_combo: QComboBox | None = None) -> None:
        super().__init__()
        self.slider = slider
        self.skin_combo = skin_combo
        # Set by MainWindow.open_settings: (painter, rect, skin_name, percent).
        # The notes are drawn by the editor timeline's own code, which lives in
        # gui, and gui imports this module -- so it is handed in rather than
        # imported. None (a dialog built alone) keeps the plain circles below.
        self.paint_notes = None
        self.setFixedHeight(64)
        slider.valueChanged.connect(self.update)
        if skin_combo is not None:
            skin_combo.currentIndexChanged.connect(self.update)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), theme.color("#151b24"))
        painter.setPen(QPen(theme.color("#e8edf3"), 1))
        for x in range(12, self.width(), 18):
            painter.setOpacity(0.35 if (x // 18) % 4 else 0.8)
            painter.drawLine(x, 6, x, self.height() - 6)
        painter.setOpacity(1.0)
        if self.paint_notes is not None:
            name = str(self.skin_combo.currentData() or "") if self.skin_combo is not None else ""
            self.paint_notes(painter, self.rect(), name, self.slider.value())
            return
        painter.setOpacity(self.slider.value() / 100.0)
        y = self.height() / 2
        for index, (colour, radius) in enumerate(((QColor(229, 76, 46), 17), (QColor(67, 141, 171), 17),
                                                  (QColor(229, 76, 46), 25), (QColor(251, 183, 6), 17))):
            painter.setPen(QPen(QColor("#ffffff"), 2.5))
            painter.setBrush(colour)
            painter.drawEllipse(40 + index * 62, y - radius, radius * 2, radius * 2)
