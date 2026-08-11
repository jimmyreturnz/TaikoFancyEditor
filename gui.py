from __future__ import annotations

import math
import random
import csv
import shutil
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path
from time import perf_counter
from typing import Any

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, QSize, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImageReader, QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea,
    QSlider, QSpacerItem, QSpinBox, QSplitter, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget, QDialog, QDialogButtonBox, QFontComboBox, QSizePolicy
)

from gui_draft import PARAMETERS
from i18n import install_translator, tr
from image_trace_dialog import ImageTraceDialog
from model.commands import (
    CompositeCommand, EditTimingPoint, InsertHitObjects, InsertTimingPoints,
    MoveNotes, RemoveHitObjects, RemoveTimingPoints, SetNoteFields, SetNotePositions,
)
from model.editor_state import DifficultyState
from model.hit_object import HITSOUND_CLAP, HITSOUND_FINISH, HitObject, TYPE_CIRCLE, TYPE_NEW_COMBO, TYPE_SLIDER, TYPE_SPINNER
from settings import (
    APPLICATION_NAME, ORGANIZATION_NAME, SettingsManager, ShortcutRegistry, should_ignore_shortcut_focus,
)
from settings_dialog import SettingsDialog
from osu_io.parser import parse_osu
from osu_io.timing import (
    TimingPoint,
    active_point_at,
    active_uninherited_at,
    kiai_spans,
    sorted_by_time,
    sv_at,
    uninherited_points,
)
from osu_io.writer import write_osu
from song_library import group_by_song, load_cache, save_cache, scan, songs_from_cache
from transformer import available_transformations, transform, transform_groups

PLAYFIELD_WIDTH = 512
PLAYFIELD_HEIGHT = 384

# Shared by the toolbar snap combo and every TimelineGameplay's wheel-cycle.
SNAP_DIVISORS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 27, 32, 36, 48)

# See _player_position_changed. Below the hard threshold, discrepancies are
# blended in gradually (fraction corrected per report) rather than ignored
# or snapped, so both one-off jitter and small steady-state bias converge
# without a visible jump. At or above it, something discontinuous happened
# (stall, external seek) and snapping immediately is the correct response.
POSITION_CORRECTION_FACTOR = 0.3
POSITION_HARD_RESYNC_THRESHOLD_MS = 200.0

# The song-folder scan runs on the UI thread, sliced by time rather than by a
# file count: one frame's worth of work per timer tick, so a folder with a few
# fast files and one with thousands of slow ones both stay responsive.
SCAN_SLICE_SECONDS = 0.008

# page_stack / page_button_group indices.
PAGE_LIBRARY, PAGE_EDITOR, PAGE_FANCY = 0, 1, 2

# The app's accent, used by the global button style and anywhere a widget has
# to reproduce it in code rather than in a stylesheet.
ACCENT_PINK = "#f3a6bd"

# Points added to the system UI font. Applied to the QApplication font so it
# reaches dialogs and honours DPI scaling, unlike a stylesheet pixel size.
UI_FONT_POINT_BOOST = 2.0


def resource_roots() -> list[Path]:
    """Return only trusted application roots, never the process working directory."""
    roots=[]
    if hasattr(sys,"_MEIPASS"):
        roots.append(Path(sys._MEIPASS).resolve())
    roots.append(Path(__file__).resolve().parent)
    unique=[]
    for root in roots:
        if root not in unique:unique.append(root)
    return unique


def resolve_song_asset(song_folder: Path, raw_name: str, allowed_suffixes: set[str]) -> Path:
    """Resolve a beatmap asset without allowing absolute paths or .. traversal."""
    name=raw_name.strip().strip('"')
    if not name:
        raise ValueError("Beatmap asset name is empty")
    relative=Path(name)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise ValueError(f"Unsafe beatmap asset path: {raw_name}")
    candidate=(song_folder.resolve()/relative).resolve()
    try:
        candidate.relative_to(song_folder.resolve())
    except ValueError as error:
        raise ValueError(f"Beatmap asset escapes the song folder: {raw_name}") from error
    if candidate.suffix.lower() not in allowed_suffixes:
        raise ValueError(f"Unsupported beatmap asset type: {candidate.suffix}")
    return candidate


def application_icon() -> QIcon:
    relative_candidates=(
        Path("assets/icons/FancyTaikoEditor_Logo.ico"),
        Path("assets/icons/FancyTaikoEditor_Logo.png"),
        Path("assets/FancyTaikoEditor_Logo.ico"),
        Path("FancyTaikoEditor_Logo.ico"),
        Path("icon.ico"),
    )
    for root in resource_roots():
        for relative in relative_candidates:
            candidate=root/relative
            if candidate.is_file():
                icon=QIcon(str(candidate))
                if not icon.isNull():return icon
    return QIcon()


_SPINNER_PIXMAP: QPixmap | None = None


def spinner_pixmap() -> QPixmap:
    """assets/skins/spinner.png, loaded once and cached.

    Constructing a QPixmap requires a live QApplication, so this is loaded
    lazily on first paint rather than at import time.
    """
    global _SPINNER_PIXMAP
    if _SPINNER_PIXMAP is None:
        _SPINNER_PIXMAP = QPixmap()
        for root in resource_roots():
            candidate = root / "assets" / "skins" / "spinner.png"
            if candidate.is_file():
                loaded = QPixmap(str(candidate))
                if not loaded.isNull():
                    _SPINNER_PIXMAP = loaded
                    break
    return _SPINNER_PIXMAP


# osu!'s own [Difficulty] SliderMultiplier default. This app parses neither
# [Difficulty] nor any per-map override of it, so drumroll length<->duration
# conversion (placing/resizing by dragging a time range) uses this constant
# rather than the map's real value. A documented approximation, not a bug:
# getting slider length exactly right would need parsing a section nothing
# else in this codebase touches yet.
SLIDER_MULTIPLIER_ASSUMED = 1.4


def slider_length_for_duration(duration_ms: float, beat_length: float, sv: float) -> float:
    """osu!px length that plays for `duration_ms` at the given beat length/SV."""
    if beat_length <= 0 or sv <= 0:
        return 1.0
    return max(1.0, duration_ms * SLIDER_MULTIPLIER_ASSUMED * 100.0 * sv / beat_length)


def duration_for_slider_length(length: float, beat_length: float, sv: float) -> float:
    """Inverse of slider_length_for_duration, for hit-testing an existing slider's end."""
    if beat_length <= 0 or sv <= 0:
        return 0.0
    return length / (SLIDER_MULTIPLIER_ASSUMED * 100.0 * sv) * beat_length


PARAMETERS.setdefault("drawn_path", [{"key":"chunk_size","label":"Notes per Drawing","type":"int","min":2,"max":4096,"default":256},{"key":"reverse","label":"Direction","type":"choice","choices":[("Top to Bottom / Left to Right",False),("Top to Bottom / Right to Left",True)],"default":False}])
if not any(item.get("key")=="font_family" for item in PARAMETERS.get("text",[])):
    text_parameters=PARAMETERS.setdefault("text",[])
    insert_at=next((index+1 for index,item in enumerate(text_parameters) if item.get("key")=="text"),0)
    text_parameters.insert(insert_at,{"key":"font_family","label":"Font","type":"font","default":"Segoe UI"})
if not any(item.get("key")=="reverse" for item in PARAMETERS.get("text",[])):
    PARAMETERS["text"].append({"key":"reverse","label":"Direction","type":"choice","choices":[("Top to Bottom / Left to Right",False),("Top to Bottom / Right to Left",True)],"default":False})
GUI_TRANSFORMATIONS=[name for name in ("text","drawn_path","equation") if name in PARAMETERS]+[name for name in available_transformations() if name in PARAMETERS and name not in {"text","drawn_path","equation"}]


def equalize_button_widths(buttons) -> None:
    """Give every button the width of the widest one.

    Polished first: before the style has been applied a button reports the
    unpadded hint, and the widest label is exactly the one that then no longer
    fits.
    """
    for button in buttons:
        button.ensurePolished()
    width = max((button.sizeHint().width() for button in buttons), default=0)
    for button in buttons:
        button.setFixedWidth(width)


def extract_timing_points(document) -> list[TimingPoint]:
    """Uninherited (BPM) points only, sorted by time.

    The snap grid and every beat calculation work in whole beats, so inherited
    SV points are deliberately excluded here. Use document.timing_points when
    SV matters.
    """
    return uninherited_points(sorted_by_time(document.timing_points))


def active_timing(
    timing_points: list[TimingPoint],
    time_ms: float,
) -> TimingPoint:
    return active_uninherited_at(timing_points, time_ms)


# Kiai flash. Notes in a kiai section brighten on every 1/1 beat and fade out
# across it. Stacked objects -- a fake slider dropped on top of a note -- each
# paint their own overlay, so a stack reads brighter than a lone note without
# anything having to count them. Drumroll yellow rather than white: the shine
# mappers stack fake sliders for is that yellow showing through.
KIAI_PULSE_COLOR = (255, 206, 92)
KIAI_PULSE_ALPHA = 80
# Under this a "beat" is a gimmick, not a pulse: an invisible-note section at
# 0.0001ms per beat would strobe once per frame.
KIAI_PULSE_MIN_BEAT_MS = 50.0


def beat_pulse(timing_points: list[TimingPoint], time_ms: float) -> float:
    """Flash strength at `time_ms`: 1.0 on the beat, fading to 0 by the next."""
    point = active_uninherited_at(timing_points, time_ms)
    if point.beat_length < KIAI_PULSE_MIN_BEAT_MS:
        return 0.0
    return 1.0 - ((time_ms - point.time) / point.beat_length) % 1.0


def in_kiai(timing_points: list[TimingPoint], time_ms: float) -> bool:
    """Whether `time_ms` sits in a kiai section.

    Needs the *full* point list, inherited ones included: kiai is normally
    switched on by a green line.
    """
    point = active_point_at(timing_points, time_ms)
    return point is not None and point.kiai


_NOTE_SPRITES: dict[tuple, QPixmap] = {}


def draw_note_sprite(
    painter: QPainter, fill: QColor, outline: QPen, x: float, y: float, radius: float
) -> None:
    """Draw a note circle from a cached pixmap instead of stroking an ellipse.

    A filled, stroked, antialiased ellipse costs the raster engine ~70us; the
    same circle blitted costs ~2us. A screenful of a dense map is 3ms a frame
    against 0.3ms, which is the difference between stuttering and not. Notes are
    identical circles in a handful of colours and two sizes, so there is nothing
    to redraw per frame in the first place.
    """
    ratio = painter.device().devicePixelRatioF()
    radius_px = round(radius)
    key = (fill.rgba(), outline.color().rgba(), round(outline.widthF() * 2), radius_px, ratio)
    sprite = _NOTE_SPRITES.get(key)
    if sprite is None:
        size = radius_px * 2 + 4
        sprite = QPixmap(round(size * ratio), round(size * ratio))
        sprite.setDevicePixelRatio(ratio)
        sprite.fill(Qt.transparent)
        sprite_painter = QPainter(sprite)
        sprite_painter.setRenderHint(QPainter.Antialiasing, True)
        sprite_painter.setPen(outline)
        sprite_painter.setBrush(fill)
        sprite_painter.drawEllipse(QPointF(size / 2, size / 2), radius_px, radius_px)
        sprite_painter.end()
        # ponytail: cleared wholesale rather than evicted by age. Keys are
        # (colour, size, dpr) combinations, so this only grows while the window
        # is being resized; an LRU would be more code than the problem.
        if len(_NOTE_SPRITES) > 64:
            _NOTE_SPRITES.clear()
        _NOTE_SPRITES[key] = sprite
    half = sprite.width() / (2 * ratio)
    painter.drawPixmap(QPointF(x - half, y - half), sprite)


def draw_kiai_flash(painter: QPainter, x: float, y: float, radius: float, pulse: float) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(*KIAI_PULSE_COLOR, round(KIAI_PULSE_ALPHA * pulse)))
    painter.drawEllipse(QPointF(x, y), radius, radius)


def snap_time(
    timing_points: list[TimingPoint],
    time_ms: float,
    divisor: int,
) -> float:
    timing = active_timing(timing_points, time_ms)
    snap_length = timing.beat_length / divisor

    return (
        timing.time
        + round((time_ms - timing.time) / snap_length) * snap_length
    )


def wheel_seek_time(
    timing_points: list[TimingPoint],
    current_time: float,
    direction: int,
    divisor: int,
) -> float:
    """One snap division either side of `current_time` (one beat at divisor 1).

    Shared by every scrolling view so a wheel notch means the same thing in all
    of them. Each widget keeps its own accumulator: that part is about the
    hardware's delta, not about what one step means.
    """
    timing = active_timing(timing_points, current_time)
    return max(
        0.0,
        snap_time(timing_points, current_time, divisor)
        + direction * timing.beat_length / divisor,
    )


def format_time(time_ms: int) -> str:
    value = max(0, int(time_ms))
    return f"{value // 60000:02d}.{(value % 60000) // 1000:02d}.{value % 1000:03d}"


def editor_timeline_metadata(document) -> tuple[list[int], int | None]:
    """Bookmarks and PreviewTime for the timing overview bar.

    Bookmarks live in [Editor], but PreviewTime lives in [General]. Reading both
    from [Editor] meant preview_time was always None, so the yellow PreviewTime
    marker never appeared for any real beatmap. [Editor] is still accepted for
    PreviewTime in case some editor writes it there.
    """
    section=""; bookmarks=[]; preview_time=None
    for line in document.lines:
        raw=line.rstrip("\r\n"); stripped=raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section=stripped[1:-1]; continue
        if section=="Editor" and raw.startswith("Bookmarks:"):
            for value in raw.split(":",1)[1].split(","):
                try: bookmarks.append(max(0,round(float(value.strip()))))
                except ValueError: pass
        elif section in ("General","Editor") and raw.startswith("PreviewTime:"):
            try:
                value=round(float(raw.split(":",1)[1].strip()))
                preview_time=value if value>=0 else None
            except ValueError: pass
    return sorted(set(bookmarks)),preview_time

def kiai_ranges(document, duration_ms: int) -> list[tuple[int, int]]:
    return kiai_spans(document.timing_points, duration_ms)


# Visible transformation labels, keyed by the stable internal transformation ID.
# The IDs are what get stored and compared; only the values are ever shown or
# translated. Adding a transformation without an entry here still works and
# falls back to a titled form of its ID.
TRANSFORMATION_LABELS = {
    "text": "Text",
    "drawn_path": "Drawing",
    "equation": "Equation",
    "pinwheel": "Pinwheel",
    "horizontal": "Horizontal",
    "vertical": "Vertical",
    "taiko": "Taiko",
    "vertical_taiko": "Vertical Taiko",
    "dvd_bouncing": "DVD Bouncing",
    "circle": "Circle",
    "ellipse": "Ellipse",
    "square": "Square",
    "triangle": "Triangle",
    "diamond": "Diamond",
    "infinity": "Infinity",
    "star": "Star",
    "spiral": "Spiral",
    "arc": "Arc",
    "straight_line": "Straight Line",
    "polyline": "Polyline",
    "bezier": "Bézier Path",
    "wave": "Wave",
    "zigzag": "Zigzag",
    "random_walk": "Random Walk",
    "random": "Random",
}


def display_name(name: str) -> str:
    label = TRANSFORMATION_LABELS.get(name) or name.replace("_", " ").title()
    return tr("Transformations", label)


def extract_background_filename(document) -> str:
    section = ""
    for line in document.lines:
        content = line.rstrip("\r\n")
        stripped = content.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if section != "Events" or not stripped or stripped.startswith("//"):
            continue
        try:
            fields = next(csv.reader([content], skipinitialspace=True))
        except (csv.Error, StopIteration):
            continue
        if len(fields) >= 3 and fields[0].strip() == "0":
            return fields[2].strip().strip('"')
    return ""


def set_document_background(document, filename: str) -> None:
    section = ""
    events_header = None
    for index, line in enumerate(document.lines):
        content = line.rstrip("\r\n")
        stripped = content.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            if section == "Events":
                events_header = index
            continue
        if section != "Events" or not stripped or stripped.startswith("//"):
            continue
        try:
            fields = next(csv.reader([content], skipinitialspace=True))
        except (csv.Error, StopIteration):
            continue
        if len(fields) >= 3 and fields[0].strip() == "0":
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            document.lines[index] = f'0,0,"{filename}",0,0{ending}'
            return
    if events_header is None:
        ending = "\n"
        document.lines.extend([f"{ending}[Events]{ending}", f'0,0,"{filename}",0,0{ending}'])
    else:
        ending = "\r\n" if document.lines[events_header].endswith("\r\n") else "\n"
        document.lines.insert(events_header + 1, f'0,0,"{filename}",0,0{ending}')
        for note in document.hit_objects:
            if note.source_line_index > events_header:
                note.source_line_index += 1


class ParameterControl(QWidget):
    changed = Signal()
    def __init__(self, definition: dict[str, Any]) -> None:
        super().__init__(); self.definition = definition
        layout = QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(8)
        if definition["type"] == "font":
            self.font_combo=QFontComboBox();self.font_combo.setCurrentFont(QFont(str(definition.get("default","Segoe UI"))))
            self.font_combo.currentFontChanged.connect(lambda _font:self.changed.emit());layout.addWidget(self.font_combo,1)
            self.choice=None;self.slider=None;self.spin=None;return
        if definition["type"] == "text":
            self.text_input = QLineEdit(str(definition.get("default", "")))
            self.text_input.setPlaceholderText(tr("Parameters", "Enter text, for example 67, 日本, or ภาษาไทย"))
            self.text_input.textChanged.connect(self.changed)
            layout.addWidget(self.text_input, 1)
            self.choice = None; self.slider = None; self.spin = None
            return
        if definition["type"] == "choice":
            self.choice_group=QButtonGroup(self); self.choice_group.setExclusive(True); self.choice_buttons={}
            for label,value in definition["choices"]:
                button=QPushButton(tr("Parameters", str(label))); button.setCheckable(True); button.setProperty("choice_value",value)
                self.choice_group.addButton(button); self.choice_buttons[value]=button; layout.addWidget(button)
            self.choice_buttons[definition["default"]].setChecked(True)
            self.choice_group.buttonClicked.connect(self.changed)
            self.choice=None; self.slider=None; self.spin=None
            return
        if definition["key"] == "seed" or definition["key"].endswith("_seed"):
            self.scale = 1
            self.slider = None
            self.choice = None
            self.spin = QSpinBox()
            self.spin.setRange(int(definition["min"]), int(definition["max"]))
            self.spin.setValue(int(definition.get("default", 0)))
            self.spin.setMinimumWidth(110)
            self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
            random_button = QPushButton(tr("Parameters", "Random Seed"))
            random_button.clicked.connect(self.randomize)
            increase_button = QPushButton("+")
            decrease_button = QPushButton("-")
            for button in (increase_button, decrease_button):
                button.setFixedWidth(30)
                button.setAutoRepeat(True)
            increase_button.clicked.connect(self.spin.stepUp)
            decrease_button.clicked.connect(self.spin.stepDown)
            layout.addWidget(self.spin)
            layout.addWidget(random_button, 1)
            layout.addWidget(increase_button)
            layout.addWidget(decrease_button)
            self.spin.valueChanged.connect(lambda _value: self.changed.emit())
            return
        step=float(definition.get("step",1 if definition["type"]=="int" else .1))
        self.scale=1 if definition["type"]=="int" or step>=1 else 10 if step>=.1 else 100
        self.slider=QSlider(Qt.Horizontal); self.slider.setRange(round(float(definition["min"])*self.scale),round(float(definition["max"])*self.scale))
        if definition["type"]=="int":
            self.spin=QSpinBox(); self.spin.setRange(int(definition["min"]),int(definition["max"])); self.spin.setSingleStep(int(definition.get("step",1)))
        else:
            self.spin=QDoubleSpinBox(); self.spin.setRange(float(definition["min"]),float(definition["max"])); self.spin.setSingleStep(step); self.spin.setDecimals(2)
        self.spin.setMinimumWidth(104)
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        decrease_button=QPushButton("-")
        increase_button=QPushButton("+")
        decrease_button.setFixedWidth(30)
        increase_button.setFixedWidth(30)
        decrease_button.setAutoRepeat(True)
        increase_button.setAutoRepeat(True)
        decrease_button.clicked.connect(self.spin.stepDown)
        increase_button.clicked.connect(self.spin.stepUp)
        layout.addWidget(self.slider,1)
        layout.addWidget(self.spin)
        layout.addWidget(increase_button)
        layout.addWidget(decrease_button)
        self.slider.valueChanged.connect(self._from_slider); self.spin.valueChanged.connect(self._from_spin); self.set_value(definition["default"])
    def _from_slider(self,raw):
        self.spin.blockSignals(True); self.spin.setValue(raw/self.scale); self.spin.blockSignals(False); self.changed.emit()
    def _from_spin(self,value):
        self.slider.blockSignals(True); self.slider.setValue(round(float(value)*self.scale)); self.slider.blockSignals(False); self.changed.emit()
    def randomize(self):
        self.set_value(random.SystemRandom().randint(int(self.definition["min"]),int(self.definition["max"]))); self.changed.emit()
    def set_value(self, value):
        if self.definition["type"] == "font":
            self.font_combo.blockSignals(True);self.font_combo.setCurrentFont(QFont(str(value)));self.font_combo.blockSignals(False);return
        if self.definition["type"] == "text":
            self.text_input.blockSignals(True)
            self.text_input.setText(str(value))
            self.text_input.blockSignals(False)
            return

        if self.definition["type"] == "choice":
            for button in self.choice_group.buttons():
                if button.property("choice_value") == value:
                    button.blockSignals(True)
                    button.setChecked(True)
                    button.blockSignals(False)
                    return
            return

        # Seed controls deliberately have slider=None. Numeric restoration must
        # therefore treat the spin box as mandatory and the slider as optional.
        self.spin.blockSignals(True)
        self.spin.setValue(value)

        if self.slider is not None:
            self.slider.blockSignals(True)
            self.slider.setValue(round(float(value) * self.scale))
            self.slider.blockSignals(False)

        self.spin.blockSignals(False)
    def value(self):
        if self.definition["type"]=="font":return self.font_combo.currentFont().family()
        return self.text_input.text() if self.definition["type"]=="text" else self.choice_group.checkedButton().property("choice_value") if self.definition["type"]=="choice" else self.spin.value()


class DifficultyValueControl(QWidget):
    changed = Signal(float)
    def __init__(self, label: str, default: float, tooltip: str) -> None:
        super().__init__()
        self.setToolTip(tooltip)
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(3)
        caption=QLabel(label);caption.setToolTip(tooltip);layout.addWidget(caption)
        self.slider=QSlider(Qt.Horizontal);self.slider.setRange(0,1000);self.slider.setSingleStep(1);self.slider.setPageStep(10);self.slider.setFixedWidth(200);self.slider.setToolTip(tooltip);layout.addWidget(self.slider)
        self.value_box=QDoubleSpinBox();self.value_box.setRange(0.0,10.0);self.value_box.setDecimals(2);self.value_box.setSingleStep(0.01);self.value_box.setFixedWidth(62);self.value_box.setButtonSymbols(QAbstractSpinBox.NoButtons);self.value_box.setToolTip(tooltip);layout.addWidget(self.value_box)
        decrease=QPushButton("-");increase=QPushButton("+")
        for button in (decrease,increase):button.setFixedSize(28,28);button.setAutoRepeat(True);button.setFocusPolicy(Qt.NoFocus);button.setToolTip(tr("MainWindow", "Adjust by 0.01"))
        decrease.clicked.connect(self.value_box.stepDown);increase.clicked.connect(self.value_box.stepUp);layout.addWidget(increase);layout.addWidget(decrease)
        self.slider.valueChanged.connect(self._slider_changed);self.value_box.valueChanged.connect(self._box_changed);self.set_value(default)
    def _slider_changed(self, raw: int) -> None:
        value=raw/100.0;self.value_box.blockSignals(True);self.value_box.setValue(value);self.value_box.blockSignals(False);self.changed.emit(value)
    def _box_changed(self, value: float) -> None:
        self.slider.blockSignals(True);self.slider.setValue(round(float(value)*100));self.slider.blockSignals(False);self.changed.emit(float(value))
    def value(self) -> float:return float(self.value_box.value())
    def set_value(self, value: float) -> None:
        value=max(0.0,min(10.0,float(value)));self.value_box.setValue(value);self.slider.setValue(round(value*100))


class DrawingSurface(QWidget):
    def __init__(self):
        super().__init__();self.strokes=[];self.active=None;self.redo_strokes=[];self.setMinimumSize(700,450);self.setFocusPolicy(Qt.StrongFocus)
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.setFocus();self.active=[event.position()];self.strokes.append(self.active);self.redo_strokes.clear();event.accept();self.update()
    def mouseMoveEvent(self,event):
        if self.active is not None and event.buttons() & Qt.LeftButton:
            point=event.position()
            if (point-self.active[-1]).manhattanLength()>=1:self.active.append(point);self.update()
            event.accept()
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton:
            point=event.position()
            if self.active is not None and (point-self.active[-1]).manhattanLength()>=1:self.active.append(point)
            self.active=None;event.accept();self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor("#11151c"));painter.setRenderHint(QPainter.Antialiasing,True);painter.setPen(QPen(QColor("#f3a6bd"),4,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
        for stroke in self.strokes:
            if len(stroke)==1:painter.drawPoint(stroke[0])
            for a,b in zip(stroke,stroke[1:]):painter.drawLine(a,b)
    def sampled_points(self):
        points=[]
        for stroke in self.strokes:
            for point in stroke:
                points.append((max(0.0,min(512.0,point.x()/max(1,self.width())*512.0)),max(0.0,min(384.0,point.y()/max(1,self.height())*384.0))))
        return points
    def clear(self):
        if self.strokes:self.redo_strokes.extend(reversed(self.strokes));self.strokes.clear();self.active=None;self.update()
    def undo(self):
        self.active=None
        if self.strokes:self.redo_strokes.append(self.strokes.pop());self.update()
    def redo(self):
        if self.redo_strokes:self.strokes.append(self.redo_strokes.pop());self.update()


class DrawingDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.accepted_points=[];self.setWindowTitle(tr("DrawingDialog", "Drawing"));self.resize(780,560)
        icon=application_icon()
        if not icon.isNull():self.setWindowIcon(icon)
        layout=QVBoxLayout(self);label=QLabel(tr("DrawingDialog", "Draw one or more strokes. Notes are placed top-to-bottom, then horizontally within each row. Ctrl+Z: undo, Ctrl+Y: redo."));label.setWordWrap(True);layout.addWidget(label)
        self.surface=DrawingSurface();layout.addWidget(self.surface,1)
        row=QHBoxLayout();undo_button=QPushButton(tr("DrawingDialog", "Undo"));redo_button=QPushButton(tr("DrawingDialog", "Redo"));clear_button=QPushButton(tr("DrawingDialog", "Clear"));undo_button.clicked.connect(self.surface.undo);redo_button.clicked.connect(self.surface.redo);clear_button.clicked.connect(self.surface.clear);row.addWidget(undo_button);row.addWidget(redo_button);row.addWidget(clear_button)
        import_button=QPushButton(tr("DrawingDialog", "Import Image..."));import_button.clicked.connect(self.import_image);row.addWidget(import_button)
        layout.addLayout(row)
        self.undo_shortcut=QShortcut(QKeySequence("Ctrl+Z"),self);self.undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut);self.undo_shortcut.activated.connect(self.surface.undo)
        self.redo_shortcut=QShortcut(QKeySequence("Ctrl+Y"),self);self.redo_shortcut.setContext(Qt.WidgetWithChildrenShortcut);self.redo_shortcut.activated.connect(self.surface.redo)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def import_image(self):
        """Trace a local image and add the result as Drawing strokes.

        trace_image already fits its output to the 512x384 playfield, so the
        only conversion needed is playfield -> surface widget coordinates,
        which is the exact inverse of DrawingSurface.sampled_points().

        Strokes are appended rather than replacing the canvas, so an import
        can never silently destroy hand-drawn work.
        """
        dialog=ImageTraceDialog(self)
        if dialog.exec()!=QDialog.Accepted:
            return
        strokes=[stroke for stroke in dialog.accepted_strokes if len(stroke)>=2]
        if not strokes:
            return
        width=max(1,self.surface.width());height=max(1,self.surface.height())
        for stroke in strokes:
            self.surface.strokes.append([
                QPointF(x/PLAYFIELD_WIDTH*width, y/PLAYFIELD_HEIGHT*height)
                for x,y in stroke
            ])
        self.surface.active=None;self.surface.redo_strokes.clear();self.surface.update()

    def accept(self):
        points=self.surface.sampled_points()
        if len(points)<2:
            QMessageBox.information(self,tr("DrawingDialog", "Drawing"),tr("DrawingDialog", "Draw at least one stroke before pressing OK."))
            return
        self.accepted_points=points
        super().accept()


class TransformCanvas(QWidget):
    background_dropped = Signal(str)
    drag_offset_requested = Signal(object, float, float)
    def __init__(self) -> None:
        super().__init__()

        self.notes = []
        self.positions: dict[int, tuple[int, int]] = {}
        self.selected: set[int] = set()
        self.drag_last_position = None
        self.drag_group = None
        self.view_scale = 1.0
        self.view_offset_x = 0.0
        self.view_offset_y = 0.0
        self.render_rect = QRectF()
        self.playfield_rect = QRectF()

        self.background_pixmap = QPixmap()
        self.background_opacity = 0.55
        self.setAcceptDrops(True)
        self.setMinimumHeight(360)

    def set_state(
        self,
        notes,
        positions: dict[int, tuple[int, int]],
        selected: set[int],
    ) -> None:
        self.notes = notes
        self.positions = positions
        self.selected = set(selected)
        self.update()

    def set_background(self, image_path: str | None) -> None:
        if image_path and Path(image_path).is_file():
            self.background_pixmap = QPixmap(image_path)
        else:
            self.background_pixmap = QPixmap()
        self.update()

    def set_background_opacity(self, value: int) -> None:
        self.background_opacity = max(0.0, min(1.0, value / 100.0))
        self.update()

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if any(Path(url.toLocalFile()).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"} for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            candidate = Path(url.toLocalFile())
            if candidate.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"} and candidate.is_file():
                self.background_dropped.emit(str(candidate))
                event.acceptProposedAction()
                return

    def _update_view_geometry(self) -> None:
        # Fixed 16:9 preview, letterboxed inside the widget.
        available = self.rect().adjusted(12, 12, -12, -12)
        render_width = float(max(1, available.width()))
        render_height = render_width * 9.0 / 16.0
        if render_height > available.height():
            render_height = float(max(1, available.height()))
            render_width = render_height * 16.0 / 9.0
        self.render_rect = QRectF(
            available.center().x() - render_width / 2.0,
            available.center().y() - render_height / 2.0,
            render_width,
            render_height,
        )

        # Fixed 4:3 osu! playfield inside the 16:9 preview.
        playfield_height = self.render_rect.height()
        playfield_width = playfield_height * 4.0 / 3.0
        if playfield_width > self.render_rect.width():
            playfield_width = self.render_rect.width()
            playfield_height = playfield_width * 3.0 / 4.0
        self.playfield_rect = QRectF(
            self.render_rect.center().x() - playfield_width / 2.0,
            self.render_rect.center().y() - playfield_height / 2.0,
            playfield_width,
            playfield_height,
        )

        self.view_scale = self.playfield_rect.width() / PLAYFIELD_WIDTH
        self.view_offset_x = self.playfield_rect.left()
        self.view_offset_y = self.playfield_rect.top()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_view_geometry()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#11151c"))
        if not self.background_pixmap.isNull():
            target = self.render_rect
            source_width = self.background_pixmap.width()
            source_height = self.background_pixmap.height()
            source_ratio = source_width / max(1, source_height)
            if source_ratio > 16 / 9:
                crop_width = source_height * 16 / 9
                source = QRectF((source_width - crop_width) / 2, 0, crop_width, source_height)
            else:
                crop_height = source_width * 9 / 16
                source = QRectF(0, (source_height - crop_height) / 2, source_width, crop_height)
            painter.save()
            painter.setOpacity(self.background_opacity)
            painter.drawPixmap(target, self.background_pixmap, source)
            painter.restore()
        painter.setPen(QPen(QColor("#465164"), 1))
        painter.drawRect(
            self.playfield_rect
        )

        self._update_view_geometry()

        for note in self.notes:
            position = self.positions.get(
                note.original_index,
                (note.x, note.y),
            )

            x = self.view_offset_x + position[0] * self.view_scale
            y = self.view_offset_y + position[1] * self.view_scale
            radius = 30 if note.is_finisher else 12

            painter.setBrush(
                QColor("#4aa3ff")
                if note.is_kat
                else QColor("#ff4f5e")
            )

            painter.setPen(
                QPen(
                    QColor("#ffd166")
                    if note.original_index in self.selected
                    else QColor("#f4f7fb"),
                    2,
                )
            )

            painter.drawEllipse(
                QPointF(x, y),
                radius,
                radius,
            )


    def _note_at_canvas_position(self, position):
        self._update_view_geometry()
        if self.view_scale <= 0:
            return None
        playfield_x = (position.x() - self.view_offset_x) / self.view_scale
        playfield_y = (position.y() - self.view_offset_y) / self.view_scale
        best_note = None
        best_distance = float("inf")
        for note in self.notes:
            if note.original_index not in self.selected:
                continue
            x, y = self.positions.get(note.original_index, (note.x, note.y))
            distance = (x - playfield_x) ** 2 + (y - playfield_y) ** 2
            if distance < best_distance:
                best_distance = distance
                best_note = note
        hit_radius = 60
        return best_note if best_note is not None and best_distance <= hit_radius ** 2 else None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        note = self._note_at_canvas_position(event.position())
        if note is None:
            return
        self.drag_group = "kat" if note.is_kat else "don"
        self.drag_last_position = event.position()
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self.drag_last_position is None or self.view_scale <= 0:
            return
        delta = event.position() - self.drag_last_position
        self.drag_last_position = event.position()
        self.drag_offset_requested.emit(
            self.drag_group,
            delta.x() / self.view_scale,
            delta.y() / self.view_scale,
        )
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self.drag_last_position is None:
            return
        self.drag_last_position = None
        self.drag_group = None
        self.unsetCursor()
        event.accept()

class TimelineGameplay(QWidget):
    selection_changed = Signal(object)
    selection_finalized = Signal(object)
    seek_requested = Signal(int)
    snap_changed_by_wheel = Signal(int)
    # (note_kind, snapped_time_ms, new_combo, big) -- emitted on left click
    # when self.tool is "don"/"kat"; Shift-click places a big (finisher) note,
    # the same modifier that already marks a slider/spinner big on release.
    # The widget holds no MainWindow reference, so it emits the request rather
    # than mutating the document itself; the owner (MainWindow, per
    # difficulty) builds the HitObject.
    note_place_requested = Signal(str, float, bool, bool)
    # (note_kind, start_ms, end_ms, new_combo, big) -- slider/spinner
    # placement is a click-drag (start position to stop position), not a
    # single click; a plain click without drag arrives here too, with
    # start_ms == end_ms, and the owner falls back to a small default length.
    note_place_with_duration_requested = Signal(str, float, float, bool, bool)
    # (uid, new_end_ms) -- dragging an existing slider/spinner's right edge.
    note_duration_edit_requested = Signal(int, float)
    # uid of the note nearest a right click, within a small pixel radius.
    note_delete_requested = Signal(int)
    # list[uid] -- Delete/Backspace with a selection, Editor-page views only.
    # One signal for the whole selection so the owner can push a single undo
    # step instead of one per note.
    notes_delete_requested = Signal(object)
    # New window_ms after a Ctrl+wheel zoom. Zoom is a per-difficulty
    # property: every view of the same difficulty (chart and SV alike) has to
    # keep showing the same time span or they stop scrolling together.
    zoom_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()

        self.notes = []
        self.note_times: list[int] = []
        self._max_extend_ms = 0.0
        self.timing_points: list[TimingPoint] = []
        self._timing_times: list[float] = []
        self.selected: set[int] = set()

        self.current_time = 0.0
        self.window_ms = 2000.0
        self.snap_divisor = 4
        # Bottom-anchored (today's shared player-deck timeline) by default.
        # Editor-page chart/gimmick views turn this on: snap ticks radiate
        # both above and below the baseline and notes sit centered on it.
        self.symmetric = False
        # "select" preserves today's drag-select-by-time-range behavior
        # (used by the shared Fancy Arranger timeline). Editor-page chart
        # views switch this via their own tool row; nothing else ever
        # changes it, so the shared timeline's behavior is untouched.
        self.tool = "select"
        self.new_combo = False

        self.drag_start_x: float | None = None
        self.drag_anchor_time: float | None = None
        self.drag_mouse_x = 0.0
        self.wheel_accumulator = 0.0
        self.alt_wheel_accumulator = 0.0
        self.last_rendered_time = -1.0
        self.auto_scroll_timer = QTimer(self)
        self.auto_scroll_timer.setInterval(16)
        self.auto_scroll_timer.timeout.connect(self._auto_scroll_selection)
        self.is_playing = False

        # Hover ghost: the note that would be placed if you clicked right
        # now, shown translucent. Needs mouse tracking since plain
        # mouseMoveEvent only fires with a button held otherwise.
        self.setMouseTracking(True)
        self._hover_time: float | None = None

        # Slider/spinner placement is click-drag (start -> stop), not a
        # single click with a fixed length.
        self._placing_tool: str | None = None
        self._placing_start_time: float | None = None
        self._placing_end_time: float | None = None
        # Dragging an existing slider/spinner's right edge to extend it.
        self._resizing_note = None
        self._resize_new_end_time: float | None = None

        self.don_brush = QColor(255, 65, 30, 180)
        self.kat_brush = QColor(55, 145, 255, 180)
        self.slider_brush = QColor(255, 210, 60, 180)
        self.ghost_don_brush = QColor(255, 65, 30, 90)
        self.ghost_kat_brush = QColor(55, 145, 255, 90)
        self.ghost_slider_brush = QColor(255, 210, 60, 90)
        # Spinner extent: grey so it reads as "this span is occupied" rather
        # than competing with don/kat/slider colour coding.
        self.spinner_band_brush = QColor(190, 195, 205, 70)
        self.spinner_edge_pen = QPen(QColor(220, 226, 236, 170), 2)
        self.ghost_pen = QPen(QColor(255, 255, 255, 110), 2)
        self.normal_note_pen = QPen(QColor(255, 255, 255, 220), 2)
        self.selected_note_pen = QPen(QColor(255, 220, 110, 235), 3)
        self.baseline_pen = QPen(QColor("#7a8492"), 2)
        self.cursor_pen = QPen(QColor("#ffffff"), 3)
        # Built once instead of constructing a QPen per tick per frame --
        # at fine snap divisors with many chart views open simultaneously,
        # that allocation churn was a real contributor to paint stutter.
        # (kind -> (QPen, tick_height))
        self._tick_styles = {
            "beat": (QPen(QColor("#f2f2f2"), 2), 32),
            "half": (QPen(QColor("#ff5151"), 2), 24),
            "quarter": (QPen(QColor("#4b9cff"), 1), 18),
            "eighth": (QPen(QColor("#f2d34f"), 1), 12),
            "third": (QPen(QColor("#b578ff"), 1), 16),
            "fifth": (QPen(QColor("#f2d34f"), 1), 14),
            "seventh": (QPen(QColor("#55d6be"), 1), 12),
            "eleventh": (QPen(QColor("#ff9f40"), 1), 12),
            "thirteenth": (QPen(QColor("#8bd1ff"), 1), 12),
            "other": (QPen(QColor("#738098"), 1), 10),
        }

        self.setMinimumHeight(180)
        self.setFocusPolicy(Qt.StrongFocus)

    def load_document(self, document) -> None:
        self.refresh_notes(document)
        self.current_time = float(
            self.notes[0].time if self.notes else 0
        )
        self.selected.clear()
        self.update()

    def refresh_notes(self, document) -> None:
        """Re-read notes/timing after an edit, without resetting cursor or selection.

        Placing or deleting a note must not jump the view back to the first
        note or clear the current selection the way a fresh load_document
        would.
        """
        self.notes = document.hit_objects
        self.note_times = [note.time for note in self.notes]
        self.timing_points = extract_timing_points(document)
        self._timing_times = [point.time for point in self.timing_points]
        # Longest slider/spinner duration, so paintEvent can widen its visible-
        # notes lookup: a long spinner's band must stay drawn until its *end*
        # scrolls out of view, not just its start.
        self._max_extend_ms = 0.0
        for note in self.notes:
            if note.is_slider or note.is_spinner:
                end = self._note_end_time(note)
                if end is not None:
                    self._max_extend_ms = max(self._max_extend_ms, end - note.time)
        self.update()

    def set_tool(self, tool: str) -> None:
        self.tool = tool

    def set_new_combo(self, value: bool) -> None:
        self.new_combo = bool(value)

    def set_snap_divisor(self, divisor: int) -> None:
        self.snap_divisor = divisor
        self.current_time = max(
            0.0,
            snap_time(
                self.timing_points,
                self.current_time,
                divisor,
            ),
        )
        self.seek_requested.emit(round(self.current_time))
        self.update()

    def set_symmetric(self, symmetric: bool) -> None:
        self.symmetric = symmetric
        self.update()

    def set_time(self, time_ms: int, force: bool = False) -> None:
        self.current_time = max(0.0, float(time_ms))
        if force or abs(self.current_time - self.last_rendered_time) >= 16.0:
            self.last_rendered_time = self.current_time
            self.update()

    def time_for_x(self, x: float) -> float:
        start_time = self.current_time - self.window_ms / 2
        return (
            start_time
            + x / max(1, self.width()) * self.window_ms
        )

    def x_for_time(self, time_ms: float) -> float:
        start_time = self.current_time - self.window_ms / 2
        return (
            (time_ms - start_time)
            / self.window_ms
            * self.width()
        )

    def _change_snap_from_wheel(self, delta: int) -> None:
        if not delta:
            return
        values = SNAP_DIVISORS
        try:
            current = values.index(self.snap_divisor)
        except ValueError:
            current = values.index(4)
        # Wheel up moves toward finer snaps; wheel down toward coarser snaps.
        direction = 1 if delta > 0 else -1
        new_index = max(0, min(len(values) - 1, current + direction))
        new_divisor = values[new_index]
        if new_divisor != self.snap_divisor:
            self.snap_divisor = new_divisor
            self.snap_changed_by_wheel.emit(new_divisor)
            self.update()

    def wheelEvent(self, event) -> None:
        angle = event.angleDelta()
        pixel = event.pixelDelta()
        # Windows/Qt can turn Alt+vertical-wheel into a horizontal delta.
        delta = angle.y() or angle.x() or pixel.y() or pixel.x()

        if delta == 0:
            event.accept()
            return

        modifiers = event.modifiers() | QApplication.keyboardModifiers()
        if modifiers & Qt.AltModifier:
            self._change_snap_from_wheel(delta)
            event.accept()
            return

        if modifiers & Qt.ControlModifier:
            zoom_factor = 0.86 if delta > 0 else 1.16
            self.window_ms = max(
                500.0,
                min(60000.0, self.window_ms * zoom_factor),
            )
            self.zoom_changed.emit(self.window_ms)
            self.update()
            event.accept()
            return

        threshold = 40.0 if angle.isNull() == 0 else 120.0
        self.wheel_accumulator += delta
        steps = int(self.wheel_accumulator / threshold)

        if steps:
            self.wheel_accumulator -= steps * threshold
            seek_direction = -1 if steps > 0 else 1
            divisor = (
                1
                if modifiers & Qt.ShiftModifier
                else self.snap_divisor
            )

            # One movement per wheelEvent that crosses the threshold, not one
            # per multiple of it: a notched mouse can report a delta several
            # times a single "line" in one event (Windows' lines-per-notch
            # setting, a high-resolution wheel, ...), and looping abs(steps)
            # times turned one physical click into several snaps/beats.
            self.current_time = wheel_seek_time(
                self.timing_points, self.current_time, seek_direction, divisor
            )
            self.seek_requested.emit(round(self.current_time))
            self.update()

        event.accept()

    def _note_near_x(self, x: float, radius_px: float = 20.0):
        """Nearest note within radius_px of an x position, for right-click delete."""
        best = None; best_distance = None
        for note in self.notes:
            distance = abs(self.x_for_time(note.time) - x)
            if distance <= radius_px and (best_distance is None or distance < best_distance):
                best = note; best_distance = distance
        return best

    def _note_end_time(self, note) -> float | None:
        """End time of a slider or spinner, for edge hit-testing and resize."""
        if note.is_spinner:
            return float(note.end_time) if note.end_time is not None else None
        if note.is_slider and note.length is not None and note.slides is not None:
            timing = active_timing(self.timing_points, note.time)
            sv = sv_at(self.timing_points, note.time)
            duration = duration_for_slider_length(note.length * note.slides, timing.beat_length, sv)
            return note.time + duration if duration > 0 else None
        return None

    def _extendable_note_near_edge(self, x: float, radius_px: float = 16.0):
        """A slider/spinner whose right (end-time) edge is near x, for resize-by-drag."""
        best = None; best_distance = None
        for note in self.notes:
            if not (note.is_slider or note.is_spinner):
                continue
            end = self._note_end_time(note)
            if end is None:
                continue
            distance = abs(self.x_for_time(end) - x)
            if distance <= radius_px and (best_distance is None or distance < best_distance):
                best = note; best_distance = distance
        return best

    def mousePressEvent(self, event) -> None:
        if event.button()==Qt.RightButton:
            note = self._note_near_x(event.position().x())
            if note is not None:
                self.note_delete_requested.emit(note.uid)
            event.accept()
            return
        if event.button()!=Qt.LeftButton:
            return

        if self.tool in ("slider", "spinner"):
            # Extend an already-placed slider/spinner if the click landed
            # near its right edge; otherwise start placing a new one.
            existing = self._extendable_note_near_edge(event.position().x())
            if existing is not None:
                self._resizing_note = existing
                self._resize_new_end_time = self._note_end_time(existing)
                self.grabMouse()
                event.accept()
                return
            snapped = max(0.0, snap_time(self.timing_points, self.time_for_x(event.position().x()), self.snap_divisor))
            self._placing_tool = self.tool
            self._placing_start_time = snapped
            self._placing_end_time = snapped
            self.grabMouse()
            event.accept()
            return

        if self.tool != "select":
            time_ms = max(0.0, snap_time(self.timing_points, self.time_for_x(event.position().x()), self.snap_divisor))
            big = bool((event.modifiers() | QApplication.keyboardModifiers()) & Qt.ShiftModifier)
            self.note_place_requested.emit(self.tool, time_ms, self.new_combo, big)
            event.accept()
            return

        # A left click clears the selection before the drag rebuilds it, so a
        # plain click with no drag deselects rather than silently keeping a
        # selection the user has visibly moved on from.
        if self.selected:
            self.selected.clear()
            self.selection_changed.emit(set())
        self.drag_start_x=event.position().x(); self.drag_mouse_x=self.drag_start_x
        self.drag_anchor_time=self.time_for_x(self.drag_start_x); self.grabMouse(); self.auto_scroll_timer.start(); self.update()
    def _update_drag_selection(self) -> None:
        if self.drag_anchor_time is None: return
        a,b=sorted((self.drag_anchor_time,self.time_for_x(self.drag_mouse_x)))
        first,last=bisect_left(self.note_times,a),bisect_right(self.note_times,b)
        self.selected={n.original_index for n in self.notes[first:last]}; self.selection_changed.emit(set(self.selected)); self.update()
    def mouseMoveEvent(self,event) -> None:
        if self._placing_tool is not None:
            snapped = max(
                self._placing_start_time,
                snap_time(self.timing_points, self.time_for_x(event.position().x()), self.snap_divisor),
            )
            self._placing_end_time = snapped
            self.update()
            return
        if self._resizing_note is not None:
            minimum = self._resizing_note.time + 20.0
            snapped = max(minimum, snap_time(self.timing_points, self.time_for_x(event.position().x()), self.snap_divisor))
            self._resize_new_end_time = snapped
            self.update()
            return
        if self.drag_anchor_time is not None:
            self.drag_mouse_x=event.position().x(); self._update_drag_selection()
            return
        # Hover ghost: only worth repainting for a placement tool -- select
        # mode has nothing to preview.
        self._hover_time = self.time_for_x(event.position().x())
        if self.tool != "select":
            self.update()
    def _auto_scroll_selection(self) -> None:
        if self.drag_anchor_time is None: self.auto_scroll_timer.stop(); return
        overflow=self.drag_mouse_x if self.drag_mouse_x<0 else self.drag_mouse_x-self.width() if self.drag_mouse_x>self.width() else 0.0
        if not overflow: return
        amount=min(4.0,abs(overflow)/80.0); self.current_time=max(0.0,self.current_time+(-1 if overflow<0 else 1)*(8.0+22.0*amount*amount))
        self.seek_requested.emit(round(self.current_time)); self._update_drag_selection()
    def mouseReleaseEvent(self,event) -> None:
        if self._placing_tool is not None:
            self.releaseMouse()
            tool = self._placing_tool
            start = self._placing_start_time
            end = max(start, self._placing_end_time if self._placing_end_time is not None else start)
            # Combine the event's own modifiers with the global state, same
            # as wheelEvent elsewhere in this class: the global state alone
            # can be stale, and (for synthetic/test events especially) the
            # event's own modifiers may be the only place Shift shows up.
            big = bool((event.modifiers() | QApplication.keyboardModifiers()) & Qt.ShiftModifier)
            self._placing_tool = None
            self._placing_start_time = None
            self._placing_end_time = None
            self.note_place_with_duration_requested.emit(tool, start, end, self.new_combo, big)
            self.update()
            return
        if self._resizing_note is not None:
            self.releaseMouse()
            note = self._resizing_note
            new_end = self._resize_new_end_time
            self._resizing_note = None
            self._resize_new_end_time = None
            if new_end is not None:
                self.note_duration_edit_requested.emit(note.uid, new_end)
            self.update()
            return
        if self.drag_anchor_time is None: return
        self.auto_scroll_timer.stop(); self.releaseMouse()
        # Click-to-seek is a Fancy-Arranger-timeline convenience; Editor-page
        # views (symmetric) must not jump the shared playhead just because
        # someone clicked inside them to select/place/inspect a note.
        if not self.symmetric and abs(event.position().x()-(self.drag_start_x or 0.0))<5:
            self.current_time=max(0.0,snap_time(self.timing_points,self.time_for_x(event.position().x()),self.snap_divisor)); self.seek_requested.emit(round(self.current_time))
        self.drag_start_x=None; self.drag_anchor_time=None; self.update()
        # Previewing on every mouse-move would be far too expensive, so the
        # expensive refresh is deferred until the drag actually ends.
        self.selection_finalized.emit(set(self.selected))

    def selected_notes(self) -> list:
        """Selected notes in time order, for copy and for Delete."""
        return sorted(
            (note for note in self.notes if note.original_index in self.selected),
            key=lambda note: (note.time, note.uid),
        )

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            # Only swallow Esc when it actually cleared something. With no
            # selection it belongs to the window, which uses it to leave the
            # editor for the song list.
            if self.selected:
                self.selected.clear()
                self.selection_changed.emit(set())
                self.update()
                event.accept()
                return
            super().keyPressEvent(event)
            return

        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            # Editor-page views only. The shared Fancy Arranger timeline
            # (symmetric=False) is a transform-selection surface, where
            # Delete would destroy notes someone was only trying to arrange.
            if self.symmetric and self.selected:
                self.notes_delete_requested.emit([note.uid for note in self.selected_notes()])
            event.accept()
            return

        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.selected = {
                note.original_index
                for note in self.notes
            }
            self.selection_changed.emit(set(self.selected))
            self.update()
            return

        super().keyPressEvent(event)

    def _next_timing_time_after(self, time_ms: float) -> float | None:
        """Start of the next timing section strictly after `time_ms`."""
        index = bisect_right(self._timing_times, time_ms)
        return self._timing_times[index] if index < len(self._timing_times) else None

    def _draw_snap_grid(
        self,
        painter: QPainter,
        baseline_y: int,
    ) -> None:
        start_time = self.current_time - self.window_ms / 2
        end_time = self.current_time + self.window_ms / 2

        if end_time <= start_time:
            return

        divisor = self.snap_divisor
        timing = active_timing(
            self.timing_points,
            start_time,
        )

        # A gimmick map's "invisible note" points carry beat_length = 0.0001,
        # which puts millions of sub-pixel ticks in one window -- a hard freeze,
        # not a slow frame. Nothing under a pixel is visible anyway, so those
        # sections are skipped whole rather than drawn.
        min_snap_length = self.window_ms / max(1.0, float(self.width()))

        # Floored with the same minimum: a million-BPM point makes snap_length
        # small enough that the division below overflows to infinity, and
        # int(inf) raises. Only the *starting* tick is affected, and the loop
        # skips those sections whole anyway.
        snap_length = max(timing.beat_length / divisor, min_snap_length)

        tick_time = (
            timing.time
            + int(
                (start_time - timing.time)
                // snap_length
            )
            * snap_length
        )

        while tick_time <= end_time + snap_length:
            timing = active_timing(
                self.timing_points,
                tick_time + 0.001,
            )

            snap_length = timing.beat_length / divisor
            if snap_length < min_snap_length:
                next_section = self._next_timing_time_after(tick_time)
                if next_section is None or next_section > end_time:
                    return
                tick_time = next_section
                continue

            snap_index = round(
                (tick_time - timing.time)
                / snap_length
            )

            position_in_beat = snap_index % divisor

            if position_in_beat == 0:
                kind = "beat"
            elif divisor % 2 == 0 and position_in_beat == divisor // 2:
                kind = "half"
            elif divisor % 4 == 0 and position_in_beat % (divisor // 4) == 0:
                kind = "quarter"
            elif divisor % 8 == 0 and position_in_beat % (divisor // 8) == 0:
                kind = "eighth"
            elif divisor % 3 == 0:
                kind = "third"
            elif divisor % 5 == 0:
                kind = "fifth"
            elif divisor % 7 == 0:
                kind = "seventh"
            elif divisor % 11 == 0:
                kind = "eleventh"
            elif divisor % 13 == 0:
                kind = "thirteenth"
            else:
                kind = "other"

            pen, tick_height = self._tick_styles[kind]
            # Clamped to just off either edge before it reaches Qt: drawLine
            # takes a C int, and one 0.00001 BPM section puts the tick after
            # this one billions of pixels out. Handing that over raises
            # OverflowError *inside* paintEvent, which aborts the whole frame --
            # grid, notes and all -- and looks like the map vanishing.
            x = round(max(-1.0, min(float(self.width() + 1), self.x_for_time(tick_time))))

            painter.setPen(pen)
            if self.symmetric:
                # Notes alone stay centered on the baseline; ticks anchor to
                # the view's top and bottom edges and grow inward, framing
                # the notes rather than straddling the middle with them.
                painter.drawLine(x, 0, x, tick_height)
                painter.drawLine(x, self.height(), x, self.height() - tick_height)
            else:
                painter.drawLine(x, baseline_y, x, baseline_y - tick_height)

            tick_time += snap_length

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#151b24"))

        baseline_y = self.height() // 2 if self.symmetric else self.height() // 2 + 34
        normal_note_radius = 31
        finisher_note_radius = 42

        self._draw_snap_grid(painter, baseline_y)

        painter.setPen(self.baseline_pen)
        painter.drawLine(0, baseline_y, self.width(), baseline_y)

        start_time = self.current_time - self.window_ms / 2
        end_time = self.current_time + self.window_ms / 2

        # Only visit notes inside the visible window instead of scanning the
        # complete beatmap on every audio-position update. Widened on the left
        # by the longest slider/spinner duration so a long note whose start
        # has scrolled off-screen still gets drawn while its end is visible.
        first_visible = bisect_left(self.note_times, start_time - self._max_extend_ms)
        after_last_visible = bisect_right(self.note_times, end_time)

        if self.drag_anchor_time is not None:
            anchor_x=self.x_for_time(self.drag_anchor_time); current_x=max(0.0,min(float(self.width()),self.drag_mouse_x))
            left,width=min(anchor_x,current_x),abs(current_x-anchor_x)
            painter.fillRect(QRectF(left,0,width,self.height()),QColor(190,195,205,28))
            painter.setPen(QPen(QColor(220,225,235,70),1)); painter.drawRect(QRectF(left,0,width,self.height()-1))
        painter.setRenderHint(QPainter.Antialiasing, True)

        for note in self.notes[first_visible:after_last_visible]:
            x = self.x_for_time(note.time)
            # Spinners always render at the finisher/"big note" size,
            # independent of the finisher hitsound bit -- a plain visual
            # convention for this app, not a gameplay difference.
            radius = (
                finisher_note_radius
                if (note.is_finisher or note.is_spinner)
                else normal_note_radius
            )
            note_center_y = baseline_y if self.symmetric else baseline_y - radius
            note_pen = self.selected_note_pen if note.original_index in self.selected else self.normal_note_pen

            if note.is_spinner:
                # A spinner is a duration, but only its start had any visual
                # weight, so where it *ended* was invisible unless you dragged
                # its edge and watched the ghost. Grey band from start to end,
                # with a cap at each edge.
                spinner_end = self._note_end_time(note)
                if spinner_end is not None and spinner_end > note.time:
                    end_x = self.x_for_time(spinner_end)
                    band = QRectF(x, note_center_y - radius * 0.5, end_x - x, radius)
                    painter.setBrush(self.spinner_band_brush)
                    painter.setPen(Qt.NoPen)
                    painter.drawRoundedRect(band, radius * 0.25, radius * 0.25)
                    painter.setPen(self.spinner_edge_pen)
                    for edge_x in (x, end_x):
                        painter.drawLine(
                            QPointF(edge_x, note_center_y - radius * 0.7),
                            QPointF(edge_x, note_center_y + radius * 0.7),
                        )
                pixmap = spinner_pixmap()
                if not pixmap.isNull():
                    size = radius * 2
                    target = QRectF(x - radius, note_center_y - radius, size, size)
                    painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
                    if note.original_index in self.selected:
                        painter.setBrush(Qt.NoBrush)
                        painter.setPen(note_pen)
                        painter.drawEllipse(QPointF(x, note_center_y), radius, radius)
                    continue

            if note.is_slider:
                end_time = self._note_end_time(note)
                if end_time is not None and end_time > note.time:
                    end_x = self.x_for_time(end_time)
                    if end_x > x:
                        bar = QRectF(x, note_center_y - radius * 0.4, end_x - x, radius * 0.8)
                        painter.setBrush(self.slider_brush)
                        painter.setPen(Qt.NoPen)
                        painter.drawRoundedRect(bar, radius * 0.4, radius * 0.4)

            # Semi-transparent fill keeps the snap grid visible through notes.
            draw_note_sprite(
                painter,
                self.slider_brush if note.is_slider else (self.kat_brush if note.is_kat else self.don_brush),
                note_pen,
                x,
                note_center_y,
                radius,
            )

        self._draw_placement_ghost(painter, baseline_y, normal_note_radius, finisher_note_radius)

        cursor_x = self.width() // 2
        painter.setPen(self.cursor_pen)
        if self.symmetric:
            painter.drawLine(cursor_x, 0, cursor_x, self.height())
        else:
            painter.drawLine(
                cursor_x,
                baseline_y - 92,
                cursor_x,
                baseline_y + 7,
            )

    def _draw_extend_ghost(
        self, painter: QPainter, baseline_y: int, normal_radius: float, finisher_radius: float,
        tool: str, start_time: float, end_time: float, big: bool,
    ) -> None:
        """Translucent preview of a slider/spinner being placed or resized."""
        radius = finisher_radius if (big or tool == "spinner") else normal_radius
        note_center_y = baseline_y if self.symmetric else baseline_y - radius
        x1 = self.x_for_time(start_time)
        x2 = self.x_for_time(max(start_time, end_time))

        if tool == "spinner":
            pixmap = spinner_pixmap()
            if not pixmap.isNull():
                painter.setOpacity(0.45)
                size = radius * 2
                painter.drawPixmap(QRectF(x1 - radius, note_center_y - radius, size, size), pixmap, QRectF(pixmap.rect()))
                painter.setOpacity(1.0)
            if x2 > x1:
                painter.setPen(QPen(QColor(255, 255, 255, 110), 3))
                painter.drawLine(QPointF(x1, note_center_y), QPointF(x2, note_center_y))
            return

        if x2 > x1:
            bar = QRectF(x1, note_center_y - radius * 0.4, x2 - x1, radius * 0.8)
            painter.setBrush(self.ghost_slider_brush)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(bar, radius * 0.4, radius * 0.4)
        painter.setBrush(self.ghost_slider_brush)
        painter.setPen(self.ghost_pen)
        painter.drawEllipse(QPointF(x1, note_center_y), radius, radius)

    def _draw_placement_ghost(self, painter: QPainter, baseline_y: int, normal_radius: float, finisher_radius: float) -> None:
        shift_held = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)

        if self._placing_tool in ("slider", "spinner") and self._placing_start_time is not None:
            self._draw_extend_ghost(
                painter, baseline_y, normal_radius, finisher_radius, self._placing_tool,
                self._placing_start_time, self._placing_end_time or self._placing_start_time, shift_held,
            )
            return
        if self._resizing_note is not None and self._resize_new_end_time is not None:
            note = self._resizing_note
            tool = "spinner" if note.is_spinner else "slider"
            self._draw_extend_ghost(
                painter, baseline_y, normal_radius, finisher_radius, tool,
                note.time, self._resize_new_end_time, note.is_finisher,
            )
            return
        if self.tool not in ("don", "kat", "slider", "spinner") or self._hover_time is None:
            return

        snapped = max(0.0, snap_time(self.timing_points, self._hover_time, self.snap_divisor))
        x = self.x_for_time(snapped)
        if x < -60 or x > self.width() + 60:
            return

        if self.tool == "slider":
            self._draw_extend_ghost(painter, baseline_y, normal_radius, finisher_radius, "slider", snapped, snapped, shift_held)
            return

        radius = finisher_radius if (self.tool == "spinner" or shift_held) else normal_radius
        note_center_y = baseline_y if self.symmetric else baseline_y - radius
        if self.tool == "spinner":
            pixmap = spinner_pixmap()
            if not pixmap.isNull():
                painter.setOpacity(0.45)
                size = radius * 2
                painter.drawPixmap(QRectF(x - radius, note_center_y - radius, size, size), pixmap, QRectF(pixmap.rect()))
                painter.setOpacity(1.0)
                return
            brush = QColor(255, 255, 255, 70)
        else:
            brush = self.ghost_kat_brush if self.tool == "kat" else self.ghost_don_brush
        painter.setBrush(brush)
        painter.setPen(self.ghost_pen)
        painter.drawEllipse(QPointF(x, note_center_y), radius, radius)


# Range for mapping SV multiplier to vertical position in the SV graph;
# log-scaled since SV is inherently multiplicative (0.5x and 2x are equally
# "far" from 1x). Doesn't affect stored values, only where the line is drawn.
# The floor is fixed: anything slower than SV_VISUAL_MIN is simply drawn along
# the bottom of the graph rather than dragging the whole axis down with it.
# Only the ceiling autoscales, and only upward past SV_VISUAL_MAX -- see
# SVEditorView.update_scale.
SV_VISUAL_MIN = 0.1
SV_VISUAL_MAX = 2.5

# Absolute limits on the axis, whatever the map contains.
SV_SCALE_FLOOR = 0.01
SV_SCALE_CEILING = 100.0

# The axis only ever sits on "round" values -- 0.5x, 1.5x, 2.0x -- rather than
# whatever min/max the visible points happen to have. An axis labelled 0.43x to
# 3.17x is arithmetically correct and useless to read against.
SV_BOUND_STEPS = ((0.5, 0.5), (0.1, 0.1), (0.0, 0.01))
# A bound is pushed outward once a value gets within this ratio of it, rather
# than only once it crosses -- a line drawn hard against the edge of the graph
# is already unreadable.
SV_BOUND_NEAR = 1.05
# ...and pulled back in only once the visible range fits this much inside the
# current bound. Without the gap, scrolling across a section that straddles a
# ladder step would retune the axis on alternate frames.
SV_BOUND_SLACK = 1.3

# Minimum horizontal gap between two SV value labels on the graph. A generated
# sweep can put hundreds of points in one screen; labelling every one is both
# unreadable and a drawText per point per frame.
SV_LABEL_MIN_SPACING_PX = 46.0

# A click on a green line within this many pixels of its actual value dot
# adjusts the SV (vertical); farther away on the same line -- the vertical
# guide line is drawn full-height, so a click anywhere along it lands "on" the
# line -- retimes it (horizontal) instead. Smaller than the line's own hit
# radius, so the dot is a precise target inside a more forgiving line.
SV_DOT_HIT_RADIUS_PX = 10.0


def _sv_bound_step(value: float) -> float:
    """Ladder spacing at `value`: coarse for normal SV, finer near zero."""
    for threshold, step in SV_BOUND_STEPS:
        if value >= threshold:
            return step
    return SV_BOUND_STEPS[-1][1]


def sv_bound_below(value: float) -> float:
    """Largest ladder value strictly below `value`.

    Strictly below, so the line is never drawn along the very edge of the
    graph. Falls to a finer step when the coarse one would land on (or under)
    zero, which a logarithmic axis cannot represent.
    """
    for _threshold, step in SV_BOUND_STEPS:
        candidate = (math.ceil(value / step - 1e-9) - 1) * step
        if candidate >= SV_SCALE_FLOOR:
            return min(candidate, SV_SCALE_CEILING)
    return SV_SCALE_FLOOR


def sv_bound_above(value: float) -> float:
    """Smallest ladder value strictly above `value`."""
    step = _sv_bound_step(value)
    candidate = (math.floor(value / step + 1e-9) + 1) * step
    return max(SV_SCALE_FLOOR * 2, min(candidate, SV_SCALE_CEILING))


class SVEditorView(QWidget):
    """M4: view and edit [TimingPoints]' inherited (SV) points on a time axis.

    Vertical-line colour follows the same rule as the timing overview bar,
    resolved per timestamp rather than per point: **red** where only an
    uninherited (BPM) point sits, **green** where only inherited (SV) points
    sit, **yellow** where both share the millisecond. Resolving per point
    instead drew the two lines on top of each other and whichever came last
    in file order won, which is why a BPM point with SV stacked on it used to
    render as a plain green line.

    A line connects the effective SV over time; dragging a point in select
    *or* green-line mode moves it, either vertically (SV) or horizontally
    (retime), decided by how close the click landed to the point's own value
    dot -- see _drag_axis_for_click. Green-line mode also shows a snapped
    ghost line under the cursor before placing, mirroring the chart view's
    placement ghost. Holds no MainWindow reference -- like TimelineGameplay,
    it emits requests and the owner applies them.
    """

    seek_requested = Signal(int)
    snap_changed_by_wheel = Signal(int)
    # (start_ms, end_ms) -- drag-select in function mode.
    function_range_requested = Signal(float, float)
    # (snapped_time_ms, sv) -- click in green-line mode.
    point_add_requested = Signal(float, float)
    # (uid, new_time_ms) -- horizontal drag of a line (green or red) in select
    # mode, snapped to the grid. Retiming only, no SV change.
    point_time_edit_requested = Signal(int, float)
    # (uid, new_sv) -- vertical drag of a green line's own value dot. An
    # uninherited point has no SV, so this never fires for one. Kept as its
    # own signal (rather than folded into point_time_edit_requested) because
    # _drag_axis_for_click makes a drag either-or, never both, so each drag
    # only ever emits one of the two -- letting a continuous drag merge into a
    # single undo step instead of interleaving two command shapes that can't
    # merge with each other.
    point_sv_edit_requested = Signal(int, float)
    # uid -- right click near an inherited point.
    point_delete_requested = Signal(int)
    # list[uid] -- Delete/Backspace with green lines selected. One signal for
    # the whole selection so the owner can push a single undo step.
    points_delete_requested = Signal(object)
    # New window_ms after a Ctrl+wheel zoom -- see TimelineGameplay.zoom_changed.
    zoom_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()

        self.timing_points: list[TimingPoint] = []
        self.current_time = 0.0
        self.window_ms = 4000.0
        self.snap_divisor = 4
        # "select" mirrors TimelineGameplay's default; changed only by this
        # view's own tool row entry (global, per the Editor page's tool
        # rows), never anything else.
        self.tool = "select"

        self.drag_start_x: float | None = None
        self.drag_anchor_time: float | None = None
        self.drag_mouse_x = 0.0
        self.wheel_accumulator = 0.0
        self.last_rendered_time = -1.0
        self._drag_point: TimingPoint | None = None
        self._drag_axis: str = "time"  # "time" or "value", set by _begin_point_drag
        # Paint caches, rebuilt per edit rather than per frame -- see
        # _rebuild_caches for why that distinction is load-bearing.
        self._series: list[tuple[float, float]] = []
        self._series_times: list[float] = []
        self._line_kinds: dict[int, str] = {}
        self._line_kind_times: list[int] = []
        self._bpm_at: dict[int, float] = {}
        self._beat_points: list[TimingPoint] = []
        # Vertical scale, refitted to the visible SV on every paint. Starts at
        # the historical fixed window so an empty view still looks sensible.
        self.autoscale = True
        self.scale_min = SV_VISUAL_MIN
        self.scale_max = SV_VISUAL_MAX
        # Rubber-band selection over a time range (select tool, empty space).
        # Kept separate from drag_anchor_time, which is function mode's range.
        self.select_anchor_time: float | None = None
        self.select_mouse_x = 0.0
        self.auto_scroll_timer = QTimer(self)
        self.auto_scroll_timer.setInterval(16)
        self.auto_scroll_timer.timeout.connect(self._auto_scroll_selection)
        # uids of inherited points picked in select mode, so Delete/Backspace
        # and Ctrl+C have something to act on.
        self.selected_uids: set[int] = set()

        # Ghost preview of the green line a click would place: needs mouse
        # tracking, since plain mouseMoveEvent only fires with a button held.
        self.setMouseTracking(True)
        self._hover_time: float | None = None
        self._hover_sv: float | None = None

        self.red_pen = QPen(QColor(255, 90, 90, 230), 2)
        self.green_pen = QPen(QColor(90, 225, 130, 230), 2)
        # Both kinds share this millisecond -- same convention the timing
        # overview bar already uses for its markers.
        self.yellow_pen = QPen(QColor(255, 212, 0, 235), 2)
        self.selected_pen = QPen(QColor(255, 255, 255, 240), 3)
        self.ghost_pen = QPen(QColor(140, 255, 185, 130), 2, Qt.DashLine)
        self.graph_pen = QPen(QColor(140, 255, 185, 210), 2)
        self.range_brush = QColor(190, 195, 205, 40)
        self.label_pen = QPen(QColor(225, 230, 240, 235), 1)
        self.cursor_pen = QPen(QColor("#ffffff"), 2)

        self.setMinimumHeight(180)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- data ----------------------------------------------------------------

    def load_document(self, document) -> None:
        self.refresh_points(document)
        self.current_time = float(self.timing_points[0].time if self.timing_points else 0)
        self.update()

    def refresh_points(self, document) -> None:
        """Re-read timing points after an edit, without resetting the cursor."""
        self.set_timing_points(document.timing_points)
        # Points deleted elsewhere must not stay "selected" forever.
        live = {point.uid for point in self.timing_points}
        self.selected_uids &= live
        self.update()

    def set_timing_points(self, points) -> None:
        """Adopt a point list (any order) and rebuild the paint caches."""
        self.timing_points = sorted_by_time(points)
        self._rebuild_caches()

    def _rebuild_caches(self) -> None:
        """Per-timestamp series and line colours, computed once per edit.

        These used to be rebuilt from every point on every paint -- three full
        passes per frame, per open view, at the playback frame rate. On an
        ordinary map that is invisible; on a gimmick map carrying tens of
        thousands of timing points it is the difference between scrolling and
        a frozen window.
        """
        by_time: dict[float, float] = {}
        kinds: dict[float, set[bool]] = {}
        for point in self.timing_points:
            kinds.setdefault(point.time, set()).add(point.uninherited)
            if point.uninherited and point.time in by_time:
                # An inherited point already claimed this millisecond; a red
                # line stacked with a green one does not reset it back to 1.0x.
                continue
            by_time[point.time] = point.sv_multiplier
        self._series = sorted(by_time.items())
        self._series_times = [time_ms for time_ms, _sv in self._series]
        self._line_kinds = {
            round(time_ms): "yellow" if len(flags) > 1 else "red" if True in flags else "green"
            for time_ms, flags in kinds.items()
        }
        self._line_kind_times = sorted(self._line_kinds)
        self._bpm_at = {
            round(point.time): point.bpm
            for point in self.timing_points
            if point.uninherited and point.bpm
        }
        # The red lines on their own. active_uninherited_at steps back over
        # inherited points one at a time, and this view's whole subject matter
        # is maps with thousands of them between two red lines -- asking it
        # against the full list, once per curve segment per frame, was the SV
        # editor's entire paint cost.
        self._beat_points = uninherited_points(self.timing_points)
        if self.autoscale:
            # The ceiling follows the whole document, not the visible window: a
            # bound refitted per frame moved every time a fast point crossed
            # the view edge, which read as the graph flickering while scrolling.
            highest = max((sv for _time_ms, sv in self._series), default=1.0)
            self.scale_max = max(SV_VISUAL_MAX, sv_bound_above(highest))

    def set_snap_divisor(self, divisor: int) -> None:
        self.snap_divisor = divisor
        self.update()

    def set_tool(self, tool: str) -> None:
        self.tool = tool

    def set_time(self, time_ms: int, force: bool = False) -> None:
        """Follow the shared playhead, throttled exactly like TimelineGameplay.

        This is what makes the SV editor scroll along with the chart views
        during playback: MainWindow's frame clock broadcasts to both.
        """
        self.current_time = max(0.0, float(time_ms))
        if force or abs(self.current_time - self.last_rendered_time) >= 16.0:
            self.last_rendered_time = self.current_time
            self.update()

    def line_kinds(self) -> dict[int, str]:
        """{millisecond: "red" | "green" | "yellow"} for the vertical lines, cached per edit.

        One entry per *timestamp*, not per point. Painting one line per point
        meant an uninherited and an inherited point sharing a millisecond drew
        over each other and file order picked the winner, so a BPM line with
        SV stacked on it rendered green -- indistinguishable from a plain SV
        point. Same convention as TimingOverviewBar's markers: yellow means
        both kinds are here.
        """
        return self._line_kinds

    def selected_points(self) -> list[TimingPoint]:
        """Selected inherited points in time order, for copy and for Delete."""
        return sorted(
            (point for point in self.timing_points if point.uid in self.selected_uids),
            key=lambda point: (point.time, point.uid),
        )

    # -- time axis, mirrors TimelineGameplay ----------------------------------

    def time_for_x(self, x: float) -> float:
        start_time = self.current_time - self.window_ms / 2
        return start_time + x / max(1, self.width()) * self.window_ms

    def x_for_time(self, time_ms: float) -> float:
        start_time = self.current_time - self.window_ms / 2
        return (time_ms - start_time) / self.window_ms * self.width()

    def _change_snap_from_wheel(self, delta: int) -> None:
        if not delta:
            return
        values = SNAP_DIVISORS
        try:
            current = values.index(self.snap_divisor)
        except ValueError:
            current = values.index(4)
        direction = 1 if delta > 0 else -1
        new_index = max(0, min(len(values) - 1, current + direction))
        new_divisor = values[new_index]
        if new_divisor != self.snap_divisor:
            self.snap_divisor = new_divisor
            self.snap_changed_by_wheel.emit(new_divisor)
            self.update()

    def wheelEvent(self, event) -> None:
        angle = event.angleDelta()
        pixel = event.pixelDelta()
        delta = angle.y() or angle.x() or pixel.y() or pixel.x()
        if delta == 0:
            event.accept()
            return

        modifiers = event.modifiers() | QApplication.keyboardModifiers()
        if modifiers & Qt.AltModifier:
            self._change_snap_from_wheel(delta)
            event.accept()
            return
        if modifiers & Qt.ControlModifier:
            zoom_factor = 0.86 if delta > 0 else 1.16
            self.window_ms = max(500.0, min(120000.0, self.window_ms * zoom_factor))
            self.zoom_changed.emit(self.window_ms)
            self.update()
            event.accept()
            return

        threshold = 40.0 if angle.isNull() == 0 else 120.0
        self.wheel_accumulator += delta
        steps = int(self.wheel_accumulator / threshold)
        if steps:
            self.wheel_accumulator -= steps * threshold
            seek_direction = -1 if steps > 0 else 1
            divisor = 1 if modifiers & Qt.ShiftModifier else self.snap_divisor
            # One movement per wheelEvent that crosses the threshold -- see
            # TimelineGameplay.wheelEvent for why looping on steps overshoots.
            self.current_time = wheel_seek_time(
                self.timing_points, self.current_time, seek_direction, divisor
            )
            self.seek_requested.emit(round(self.current_time))
            self.update()
        event.accept()

    # -- SV <-> vertical position ----------------------------------------------

    def _graph_top(self) -> float:
        return 20.0

    def _graph_bottom(self) -> float:
        return max(60.0, self.height() - 36.0)

    def _sv_to_y(self, sv: float, top: float, bottom: float) -> float:
        low, high = self.scale_min, self.scale_max
        clamped = max(low, min(high, sv))
        ratio = (math.log(clamped) - math.log(low)) / (math.log(high) - math.log(low))
        return bottom - ratio * (bottom - top)

    def _y_to_sv(self, y: float, top: float, bottom: float) -> float:
        low, high = self.scale_min, self.scale_max
        ratio = max(0.0, min(1.0, (bottom - y) / max(1.0, bottom - top)))
        return math.exp(math.log(low) + ratio * (math.log(high) - math.log(low)))

    def update_scale(self) -> tuple[float, float]:
        """The current axis. Both bounds are decided per *document*, not per frame.

        * The floor is fixed at SV_VISUAL_MIN. Slow sections are read against
          the same baseline everywhere, and one 0.05x stop no longer squashes a
          whole map's worth of ordinary SV into the top of the graph -- it just
          sits on the ground (_sv_to_y clamps).
        * The ceiling is SV_VISUAL_MAX unless the map exceeds it, in which case
          it rises to the enclosing round value (`sv_bound_above`), set in
          `_rebuild_caches`. Fitting it to the *visible window* instead meant it
          moved every time a fast point crossed the view edge -- the graph
          visibly flickering while scrolling, and the sticky-bound machinery
          that used to damp that was only ever treating the symptom.

        Kept as a method (rather than reading the attribute) because
        `autoscale = False` still has to freeze whatever a caller set by hand.
        """
        return self.scale_min, self.scale_max

    def sv_series(self) -> list[tuple[float, float]]:
        """[(time, effective SV)], one entry per timestamp, cached per edit.

        A red (uninherited) line resets SV to 1.0x, but only when nothing else
        sits on that millisecond: mappers routinely stack a green line on a red
        one precisely to set the speed the new BPM section starts at, and osu!
        gives the later line in file order the final say. Emitting one entry
        per *point* meant the red line contributed a 1.0x sample the green line
        immediately overwrote, which showed up as a spike down to 1.0x at every
        BPM change that had SV on it.
        """
        return self._series

    def _nearest_inherited(self, x: float, radius_px: float = 20.0) -> TimingPoint | None:
        best = None
        best_distance = None
        for point in self.timing_points:
            if point.uninherited:
                continue
            distance = abs(self.x_for_time(point.time) - x)
            if distance <= radius_px and (best_distance is None or distance < best_distance):
                best = point
                best_distance = distance
        return best

    def _nearest_point(self, x: float, radius_px: float = 20.0) -> TimingPoint | None:
        """Nearest point of either kind, for select-mode drag-to-retime."""
        best = None
        best_distance = None
        for point in self.timing_points:
            distance = abs(self.x_for_time(point.time) - x)
            if distance <= radius_px and (best_distance is None or distance < best_distance):
                best = point
                best_distance = distance
        return best

    def _dot_position(self, point: TimingPoint) -> QPointF:
        """Screen position of a green point's value dot, for the click-radius
        test that decides whether a drag adjusts SV or retimes the line."""
        return QPointF(
            self.x_for_time(point.time),
            self._sv_to_y(point.sv_multiplier, self._graph_top(), self._graph_bottom()),
        )

    def _drag_axis_for_click(self, point: TimingPoint, pos: QPointF) -> str:
        """"value" (adjust SV) if the click landed on the point's dot, else
        "time" (retime). An uninherited point has no SV, so it is always
        "time" -- see SV_DOT_HIT_RADIUS_PX for the threshold."""
        if point.uninherited:
            return "time"
        dot = self._dot_position(point)
        distance = math.hypot(pos.x() - dot.x(), pos.y() - dot.y())
        return "value" if distance <= SV_DOT_HIT_RADIUS_PX else "time"

    # -- mouse -----------------------------------------------------------------

    def _begin_point_drag(self, point: TimingPoint, axis: str) -> None:
        self._drag_point = point
        self._drag_axis = axis
        self.selected_uids = {point.uid}
        self.grabMouse()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            point = self._nearest_inherited(event.position().x())
            if point is not None:
                self.point_delete_requested.emit(point.uid)
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            return

        if self.tool == "function":
            self.drag_start_x = event.position().x()
            self.drag_mouse_x = self.drag_start_x
            self.drag_anchor_time = self.time_for_x(self.drag_start_x)
            self.grabMouse()
            self.update()
            event.accept()
            return

        if self.tool == "green_line":
            # Landing on an existing green line adjusts it (drag its SV) the
            # same way select mode does, instead of stacking a second point
            # on top of it -- one inherited point per millisecond is the rule
            # the owner enforces anyway, so placing here could only ever have
            # replaced what was already there.
            existing = self._nearest_inherited(event.position().x())
            if existing is not None:
                self._begin_point_drag(existing, self._drag_axis_for_click(existing, event.position()))
                event.accept()
                return
            time_ms = max(0.0, snap_time(self.timing_points, self.time_for_x(event.position().x()), self.snap_divisor))
            sv = self._y_to_sv(event.position().y(), self._graph_top(), self._graph_bottom())
            self.point_add_requested.emit(time_ms, sv)
            event.accept()
            return

        # select: click a line (green or red) to select it and drag it. A
        # click on a green line's own value dot adjusts its SV (vertical);
        # anywhere else along the line retimes it (horizontal) instead -- see
        # _drag_axis_for_click. On empty space, start a rubber-band selection
        # over a time range instead -- and clear what was selected, so a
        # plain click deselects.
        point = self._nearest_point(event.position().x())
        if point is not None:
            self._begin_point_drag(point, self._drag_axis_for_click(point, event.position()))
            event.accept()
            return
        self.selected_uids.clear()
        self.select_anchor_time = self.time_for_x(event.position().x())
        self.select_mouse_x = event.position().x()
        self.grabMouse()
        self.auto_scroll_timer.start()
        self.update()
        event.accept()

    def _update_range_selection(self) -> None:
        if self.select_anchor_time is None:
            return
        a, b = sorted((self.select_anchor_time, self.time_for_x(self.select_mouse_x)))
        self.selected_uids = {
            point.uid
            for point in self.timing_points
            if not point.uninherited and a <= point.time <= b
        }
        self.update()

    def _auto_scroll_selection(self) -> None:
        """Scroll the view when a rubber-band drag runs past its edges.

        Same acceleration curve as TimelineGameplay's: without it, a selection
        can never be larger than one screenful, since the view stands still
        however far the cursor goes.
        """
        if self.select_anchor_time is None:
            self.auto_scroll_timer.stop()
            return
        overflow = (
            self.select_mouse_x if self.select_mouse_x < 0
            else self.select_mouse_x - self.width() if self.select_mouse_x > self.width()
            else 0.0
        )
        if not overflow:
            return
        amount = min(4.0, abs(overflow) / 80.0)
        self.current_time = max(0.0, self.current_time + (-1 if overflow < 0 else 1) * (8.0 + 22.0 * amount * amount))
        self.seek_requested.emit(round(self.current_time))
        self._update_range_selection()

    def mouseMoveEvent(self, event) -> None:
        if self.drag_anchor_time is not None:
            self.drag_mouse_x = event.position().x()
            self.update()
            return
        if self.select_anchor_time is not None:
            self.select_mouse_x = event.position().x()
            self._update_range_selection()
            return
        if self._drag_point is not None:
            if self._drag_axis == "value":
                sv = self._y_to_sv(event.position().y(), self._graph_top(), self._graph_bottom())
                self.point_sv_edit_requested.emit(self._drag_point.uid, sv)
            else:
                new_time = max(
                    0.0,
                    snap_time(self.timing_points, self.time_for_x(event.position().x()), self.snap_divisor),
                )
                self.point_time_edit_requested.emit(self._drag_point.uid, new_time)
            self.update()
            return
        # Ghost preview: only green-line mode has something to preview.
        self._hover_time = self.time_for_x(event.position().x())
        self._hover_sv = self._y_to_sv(event.position().y(), self._graph_top(), self._graph_bottom())
        if self.tool == "green_line":
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.drag_anchor_time is not None:
            self.releaseMouse()
            a, b = sorted((self.drag_anchor_time, self.time_for_x(self.drag_mouse_x)))
            self.drag_start_x = None
            self.drag_anchor_time = None
            self.update()
            if b - a >= 1.0:
                self.function_range_requested.emit(max(0.0, a), max(0.0, b))
            return
        if self.select_anchor_time is not None:
            self.auto_scroll_timer.stop()
            self.releaseMouse()
            self.select_anchor_time = None
            self.update()
            return
        if self._drag_point is not None:
            self.releaseMouse()
            self._drag_point = None

    # -- keyboard --------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            # See TimelineGameplay.keyPressEvent: an Esc with nothing selected
            # is the window's, for leaving the editor.
            if self.selected_uids:
                self.selected_uids.clear()
                self.update()
                event.accept()
                return
            super().keyPressEvent(event)
            return

        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.selected_uids:
                self.points_delete_requested.emit([point.uid for point in self.selected_points()])
            event.accept()
            return

        if event.matches(QKeySequence.StandardKey.SelectAll):
            start_time = self.current_time - self.window_ms / 2
            end_time = self.current_time + self.window_ms / 2
            self.selected_uids = {
                point.uid
                for point in self.timing_points
                if not point.uninherited and start_time <= point.time <= end_time
            }
            self.update()
            event.accept()
            return

        super().keyPressEvent(event)

    # -- paint -------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#151b24"))
        painter.setRenderHint(QPainter.Antialiasing, True)

        top = self._graph_top()
        bottom = self._graph_bottom()
        start_time = self.current_time - self.window_ms / 2
        end_time = self.current_time + self.window_ms / 2
        self.update_scale()

        for anchor, mouse_x in (
            (self.drag_anchor_time, self.drag_mouse_x),
            (self.select_anchor_time, self.select_mouse_x),
        ):
            if anchor is None:
                continue
            anchor_x = self.x_for_time(anchor)
            current_x = max(0.0, min(float(self.width()), mouse_x))
            left, width = min(anchor_x, current_x), abs(current_x - anchor_x)
            painter.fillRect(QRectF(left, 0, width, self.height()), self.range_brush)

        painter.setPen(QPen(QColor("#3a4554"), 1))
        painter.drawLine(0, int(bottom), self.width(), int(bottom))
        self._draw_scale_guides(painter, top, bottom)

        pens = {"red": self.red_pen, "green": self.green_pen, "yellow": self.yellow_pen}
        # Only scanned when something is actually selected: during playback --
        # the case that has to stay cheap on a map with thousands of points --
        # nothing is.
        selected_times = (
            {round(point.time) for point in self.timing_points if point.uid in self.selected_uids}
            if self.selected_uids
            else frozenset()
        )

        # Slice to the window rather than testing every timestamp for
        # visibility: a gimmick map has tens of thousands, and this runs at the
        # playback frame rate in every open SV view.
        first = bisect_left(self._line_kind_times, start_time - self.window_ms * 0.1)
        after_last = bisect_right(self._line_kind_times, end_time + self.window_ms * 0.1)
        # A barline gimmick stacks thousands of lines within one window, most of
        # them on the same pixel column: skip a repeat of the same kind at the
        # same x, and space the BPM labels like the SV ones already are.
        last_drawn: tuple[int, str] | None = None
        previous_x = -1e9
        for time_ms in self._line_kind_times[first:after_last]:
            kind = self._line_kinds[time_ms]
            x = self.x_for_time(time_ms)
            if x < -80 or x > self.width() + 80:
                continue
            selected = time_ms in selected_times
            clear_of_previous = x - previous_x >= SV_LABEL_MIN_SPACING_PX
            previous_x = x
            if not selected and last_drawn == (round(x), kind):
                continue
            last_drawn = (round(x), kind)
            if selected:
                painter.setPen(self.selected_pen)
                painter.drawLine(round(x), 0, round(x), self.height())
            painter.setPen(pens[kind])
            painter.drawLine(round(x), 0, round(x), self.height())
            bpm = self._bpm_at.get(time_ms)
            if bpm and clear_of_previous:
                painter.setPen(self.label_pen)
                painter.drawText(QPointF(x + 4, self.height() - 6), f"{bpm:.0f} BPM")

        self._draw_sv_curve(painter, top, bottom, start_time, end_time)
        self._draw_placement_ghost(painter, top, bottom)

        cursor_x = self.width() // 2
        painter.setPen(self.cursor_pen)
        painter.drawLine(cursor_x, 0, cursor_x, self.height())

    def _draw_scale_guides(self, painter: QPainter, top: float, bottom: float) -> None:
        """Label the autoscaled range, so "high" on the graph means something.

        Without these the curve is unreadable after autoscaling: the same
        shape can be a 0.9x-1.1x wobble or a 1x-8x sweep.
        """
        painter.setPen(QPen(QColor(120, 132, 150, 90), 1, Qt.DotLine))
        painter.drawLine(0, int(top), self.width(), int(top))
        painter.setPen(self.label_pen)
        painter.drawText(QPointF(4, top + 12), f"{self.scale_max:.2f}x")
        painter.drawText(QPointF(4, bottom - 4), f"{self.scale_min:.2f}x")

    def _draw_sv_curve(
        self, painter: QPainter, top: float, bottom: float, start_time: float, end_time: float,
    ) -> None:
        """The SV line: straight segments from each point to the next.

        SV is physically a step function -- it holds a value until the next
        point -- and drawing that literally, as horizontal runs joined by
        vertical risers, made every generated sweep look like a staircase
        whatever easing produced it. Connecting the points instead lets the
        generating function show in its own output: linear reads straight, sin
        reads as an S, exponential reads as a hockey stick.

        Straight segments, not a smoothed curve. A spline through the points
        invents curvature between them that no stored value justifies, and near
        a sharp SV change it overshoots past values the document does not
        contain. Dots mark the real points, so the line is never mistaken for a
        claim that SV ramps continuously between them.
        """
        series = self._series
        if not series:
            return

        # Sliced by bisect, not by filtering the whole series three times: this
        # runs per frame per view, and a gimmick map carries tens of thousands
        # of points.
        margin = self.window_ms * 0.1
        first = bisect_left(self._series_times, start_time - margin)
        after_last = bisect_right(self._series_times, end_time + margin)
        # Anchor to the neighbours just outside the window, or the curve would
        # start and end abruptly at the view edges as it scrolls.
        visible = series[max(0, first - 1):min(len(series), after_last + 1)]
        if not visible:
            return

        points = [QPointF(self.x_for_time(t), self._sv_to_y(sv, top, bottom)) for t, sv in visible]

        painter.setPen(self.graph_pen)
        if len(points) == 1:
            painter.drawLine(QPointF(0.0, points[0].y()), QPointF(float(self.width()), points[0].y()))
        else:
            # Beyond the outermost points there is nothing to interpolate
            # toward, so the line runs flat out to the view edges.
            painter.drawLine(QPointF(0.0, points[0].y()), points[0])
            painter.drawLine(points[-1], QPointF(float(self.width()), points[-1].y()))
            # A gap of at least two whole beats between consecutive points
            # isn't a ramp the document defined -- SV physically holds its
            # value until the next point, so draw that literally (flat, then
            # a riser) instead of a diagonal implying a gradual change.
            # Closer points keep the diagonal, which is what makes a
            # generated sweep's easing shape (linear/sin/exponential/...)
            # readable in the first place.
            for i in range(1, len(points)):
                prev_point, point = points[i - 1], points[i]
                prev_time, time = visible[i - 1][0], visible[i][0]
                beat_length = active_timing(self._beat_points, prev_time).beat_length
                if time - prev_time >= 2 * beat_length:
                    painter.drawLine(prev_point, QPointF(point.x(), prev_point.y()))
                    painter.drawLine(QPointF(point.x(), prev_point.y()), point)
                else:
                    painter.drawLine(prev_point, point)

        painter.setBrush(QColor(140, 255, 185, 200))
        painter.setPen(Qt.NoPen)
        for point in points:
            if -20 <= point.x() <= self.width() + 20:
                painter.drawEllipse(point, 2.5, 2.5)

        # One label per point is unreadable once a sweep is dense -- the text
        # overlaps into a smear and costs a drawText per point per frame. Label
        # only a point that stands clear of its *predecessor*, which is a
        # property of the pair: chaining from the last label instead made which
        # points got labelled depend on where the window happened to start, so
        # they blinked on and off as the view scrolled.
        painter.setPen(self.label_pen)
        for i, (point, (_time_ms, sv)) in enumerate(zip(points, visible)):
            if not 0 <= point.x() <= self.width():
                continue
            if i and point.x() - points[i - 1].x() < SV_LABEL_MIN_SPACING_PX:
                continue
            painter.drawText(QPointF(point.x() + 3, point.y() - 4), f"{sv:.2f}x")

    def _draw_placement_ghost(self, painter: QPainter, top: float, bottom: float) -> None:
        """Dashed preview of the green line a click would place, at the snapped
        time and the SV the cursor's height maps to -- the SV editor's
        equivalent of the chart view's translucent note ghost.
        """
        if self.tool != "green_line" or self._hover_time is None:
            return
        snapped = max(0.0, snap_time(self.timing_points, self._hover_time, self.snap_divisor))
        x = self.x_for_time(snapped)
        if x < -40 or x > self.width() + 40:
            return
        painter.setPen(self.ghost_pen)
        painter.drawLine(round(x), 0, round(x), self.height())
        if self._hover_sv is not None:
            y = self._sv_to_y(self._hover_sv, top, bottom)
            painter.setBrush(QColor(140, 255, 185, 130))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(x, y), 4, 4)
            painter.setPen(self.label_pen)
            painter.drawText(QPointF(x + 6, y - 6), f"{self._hover_sv:.2f}x")


# Screen distance one whole beat covers at 1.0x SV. osu!taiko's scroll speed is
# proportional to BPM x SV, so a beat occupies the same distance at every BPM --
# which is what makes this a constant rather than a function of beat length.
GAMEPLAY_PX_PER_BEAT = 200.0
GAMEPLAY_PX_PER_BEAT_MIN = 40.0
GAMEPLAY_PX_PER_BEAT_MAX = 900.0
# Where the hit position sits, as a fraction of the view width; notes scroll
# right to left onto it.
GAMEPLAY_HIT_X_RATIO = 0.16
# Cap on how far the visible-note lookup will search either way. A near-zero SV
# section makes one screen cover an unbounded amount of *time*, and scanning a
# whole map for it would cost a frame.
GAMEPLAY_MAX_LOOKAHEAD_MS = 30000.0


class GameplayViewerView(QWidget):
    """M6: read-only preview that scrolls the way osu!taiko does.

    Chart and SV views plot on a *time* axis, where an SV change is invisible
    by construction -- spacing there only ever reflects the clock. Here x is a
    distance integrated from the scroll velocity (`px_per_beat * SV /
    beat_length`), so a 2.0x section really is twice as spread out and a
    generated sweep can be judged without exporting and opening osu!.

    Read-only: no tools, no selection, no editing signals. The wheel seeks the
    shared playhead like every other view, and Ctrl+wheel changes the base
    scroll speed -- a property of this preview alone, never of the map.
    """

    seek_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.notes = []
        self.note_times: list[int] = []
        # Both kinds, sorted: SV needs the inherited points, the beat grid and
        # barlines need the uninherited ones.
        self.timing_points: list[TimingPoint] = []
        self.beat_points: list[TimingPoint] = []
        self._beat_times: list[float] = []
        # Scroll velocity in beats per millisecond, one entry per timing-point
        # timestamp, so velocity_at is a binary search instead of a walk.
        self._velocity_times: list[float] = []
        self._velocities: list[float] = []
        self._slowest_velocity = 1.0 / 500.0
        self._max_extend_ms = 0.0
        # Slider/spinner end time by note uid, rebuilt per edit -- see
        # refresh_notes. Circles are simply absent.
        self._end_times: dict[int, float] = {}

        self.current_time = 0.0
        self.snap_divisor = 4
        self.px_per_beat = GAMEPLAY_PX_PER_BEAT
        self.wheel_accumulator = 0.0
        self.last_rendered_time = -1.0

        self.don_brush = QColor(240, 60, 45)
        self.kat_brush = QColor(50, 135, 240)
        self.slider_brush = QColor(255, 200, 60)
        self.spinner_band_brush = QColor(190, 195, 205, 90)
        self.note_pen = QPen(QColor(15, 18, 24, 220), 2)
        self.barline_pen = QPen(QColor(225, 230, 240, 90), 1)
        self.hit_pen = QPen(QColor(255, 255, 255, 120), 2)
        self.lane_brush = QColor(28, 33, 43)

        self.setMinimumHeight(170)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- document ------------------------------------------------------------

    def load_document(self, document) -> None:
        self.refresh_notes(document)
        self.current_time = float(self.notes[0].time if self.notes else 0)
        self.update()

    def refresh_notes(self, document) -> None:
        self.notes = document.hit_objects
        self.note_times = [note.time for note in self.notes]
        self.timing_points = sorted_by_time(document.timing_points)
        self.beat_points = uninherited_points(self.timing_points)
        self._beat_times = [point.time for point in self.beat_points]
        self._rebuild_velocities()
        # End times once per edit, not per note per frame. Every visible note
        # asked for one while painting, and each answer cost two binary searches
        # over the full point list.
        self._max_extend_ms = 0.0
        self._end_times = {}
        for note in self.notes:
            end = self._compute_end_time(note)
            if end is not None:
                self._end_times[note.uid] = end
                self._max_extend_ms = max(self._max_extend_ms, end - note.time)
        self.update()

    # Timing points move the notes here (that is the whole point of the view),
    # so an SV edit refreshes exactly like a note edit does.
    refresh_points = refresh_notes

    def set_snap_divisor(self, divisor: int) -> None:
        self.snap_divisor = divisor

    def set_time(self, time_ms: int, force: bool = False) -> None:
        self.current_time = max(0.0, float(time_ms))
        if force or abs(self.current_time - self.last_rendered_time) >= 16.0:
            self.last_rendered_time = self.current_time
            self.update()

    def _note_end_time(self, note) -> float | None:
        """End time of a slider or spinner; None for a circle."""
        return self._end_times.get(note.uid)

    def _compute_end_time(self, note) -> float | None:
        if note.is_spinner:
            return float(note.end_time) if note.end_time is not None else None
        if note.is_slider and note.length is not None and note.slides is not None:
            # beat_points, not timing_points: active_uninherited_at steps back
            # over inherited points one at a time, and an SV-heavy map puts
            # thousands of them between two red lines.
            timing = active_uninherited_at(self.beat_points, note.time)
            duration = duration_for_slider_length(
                note.length * note.slides, timing.beat_length, sv_at(self.timing_points, note.time)
            )
            return note.time + duration if duration > 0 else None
        return None

    # -- scroll velocity -----------------------------------------------------

    def _rebuild_velocities(self) -> None:
        """Scroll velocity in beats per millisecond, per timing-point timestamp.

        Velocities, not an integrated distance: in osu!taiko each object
        approaches the hit position at the speed in force **at its own time**,
        so a green line moves everything after it (including a note sitting on
        it) and objects can pass each other. Integrating one shared scroll
        position across the whole chart is osu!mania's model, not this one.

        Stored in beats rather than pixels so the base scroll speed stays a
        pure render-time scale: Ctrl+wheel costs a repaint, not a rebuild.
        """
        points = self.timing_points
        beat_length = active_uninherited_at(points, points[0].time if points else 0.0).beat_length
        sv = 1.0
        times: list[float] = []
        velocities: list[float] = []
        index = 0
        while index < len(points):
            time_ms = points[index].time
            group_end = index
            while group_end < len(points) and points[group_end].time == time_ms:
                group_end += 1
            group = points[index:group_end]
            index = group_end
            for point in group:
                if point.uninherited:
                    beat_length = point.beat_length
            # An inherited point sharing the timestamp wins over the 1.0x reset
            # an uninherited one would apply -- the same rule as sv_series, for
            # the same reason: a green line stacked on a red one is how mappers
            # set the speed a new BPM section starts at.
            inherited = [point for point in group if point.inherited]
            if inherited:
                sv = inherited[-1].sv_multiplier
            else:
                sv = 1.0
            times.append(time_ms)
            velocities.append(self._beats_per_ms(beat_length, sv))
        if not times:
            times, velocities = [0.0], [self._beats_per_ms(beat_length, 1.0)]
        self._velocity_times = times
        self._velocities = velocities
        # The slowest section decides how far ahead a full screen can reach,
        # and so how many notes the visible-range lookup has to consider.
        self._slowest_velocity = min(velocities)

    @staticmethod
    def _beats_per_ms(beat_length: float, sv: float) -> float:
        # Guarded rather than trusted: the invisible-note gimmick authors
        # beat_length = 0.0001, and a hand-edited map can carry a zero.
        return max(0.01, sv) / max(1e-4, beat_length)

    def velocity_at(self, time_ms: float) -> float:
        """Scroll velocity, in beats per millisecond, in force at `time_ms`."""
        index = max(0, bisect_right(self._velocity_times, time_ms) - 1)
        return self._velocities[index]

    def slowest_velocity_in(self, start_ms: float, end_ms: float) -> float:
        """Slowest velocity in force anywhere in `[start_ms, end_ms)`.

        Barline spacing has to be judged over a whole timing section, and the
        velocity at its red line does not describe it: a green line partway
        through moves every measure line after it, `x_for_time` resolves each
        one at its *own* time, and taking the red line's speed for the whole
        section made the two disagree. Slowest is the safe direction for both
        things this feeds -- the widest on-screen window and the largest line
        count -- so an SV change inside a section can never drop a line.
        """
        if not self._velocities:
            return self._slowest_velocity
        first = max(0, bisect_right(self._velocity_times, start_ms) - 1)
        last = bisect_left(self._velocity_times, end_ms)
        return min(self._velocities[first:max(last, first + 1)])

    def x_for_time(self, time_ms: float) -> float:
        """Where an object at `time_ms` currently sits.

        Its own velocity, not the playhead's: that is what makes a green line
        move every object after it while leaving the ones before it alone.
        """
        return self._hit_x() + (time_ms - self.current_time) * self.velocity_at(time_ms) * self.px_per_beat

    def _hit_x(self) -> float:
        return self.width() * GAMEPLAY_HIT_X_RATIO

    def visible_time_range(self) -> tuple[float, float]:
        """Time bounds wide enough to contain everything on screen.

        Bounded by the *slowest* velocity in the map, so it never misses an
        object, and clamped: a near-stopped section would otherwise put an
        unbounded amount of time on one screen and turn this into a full scan.
        """
        reach = self.width() / max(1e-6, self._slowest_velocity * self.px_per_beat)
        reach = min(reach, GAMEPLAY_MAX_LOOKAHEAD_MS)
        return self.current_time - reach, self.current_time + reach

    def barline_times(self, start_ms: float, end_ms: float) -> list[float]:
        """Barlines on screen for the frame at `current_time`, within the range:
        one at every uninherited point, plus one each `meter` beats (4 by
        default) after it until the next one.

        A point with omit-first-barline set contributes its later measure lines
        but not the one on the point itself -- that is the flag's whole purpose,
        and barline gimmicks set it in bulk.

        Each section is walked only across the slice of time it is actually on
        screen for, at the slowest speed in force anywhere in it. The passed
        range is sized for the map's slowest section, and a section past 60000
        BPM crosses the whole of it in a fraction of a millisecond: walking that
        one from `start_ms` spent the entire per-frame line budget far off the
        left edge and drew nothing, which is what made those barlines -- the
        only thing still visible in a gimmick section, since its notes travel
        too fast to see -- go missing.

        There is deliberately no cap on how many lines come back. A dense
        section can legitimately want a great many, and the count that used to
        be capped was spent in time order, so slow sections ahead of the
        playhead exhausted it before the walk reached the section the playhead
        was actually in. The only real ceiling is the screen: two barlines that
        round to the same pixel column are one line, so a column already claimed
        this frame is skipped. That bounds what comes back by the width of the
        widget without a number to pick, and it cannot starve a later section --
        every line that survives is one the user can see.
        """
        times: list[float] = []
        claimed_columns: set[int] = set()
        width = self.width()
        hit_x = self._hit_x()
        first_point = bisect_left(self._beat_times, start_ms) - 1
        for index in range(max(0, first_point), len(self.beat_points)):
            point = self.beat_points[index]
            if point.time > end_ms:
                break
            section_end = (
                self.beat_points[index + 1].time
                if index + 1 < len(self.beat_points)
                else float("inf")
            )
            if section_end < start_ms:
                continue
            if point.beat_length <= 0:
                continue
            # Same floor _beats_per_ms puts on beat length. An invisible-note
            # gimmick authors 1e-9 here, and pairing an unclamped measure with a
            # clamped velocity would ask the walk below for millions of lines.
            measure = max(1e-4, point.beat_length) * max(1, point.meter)
            velocity = self.slowest_velocity_in(point.time, min(section_end, end_ms))
            # How much time this section fits on screen. Generous by a factor of
            # two: the hit position is not centered, so both sides need less
            # than a full width, and over-reaching only ever costs a little walk.
            reach = width / max(1e-9, velocity * self.px_per_beat)
            window_start = max(start_ms, self.current_time - reach)
            window_end = min(end_ms, self.current_time + reach)
            if window_end < point.time or window_end < window_start or section_end <= window_start:
                continue
            # Not a budget -- exactly the measure lines this window holds, which
            # is what stops a minutes-long section from being walked to its end.
            # Measured against the window rather than the widget: `reach` is
            # applied to both sides, so the walk spans two screens' worth of
            # time and a width's worth of lines ran out halfway across it.
            remaining = int((window_end - window_start) / measure) + 2
            # Jump straight to the first measure line on screen: a section is
            # routinely minutes long and walking it from the point is not.
            step = 0 if point.time >= window_start else int((window_start - point.time) // measure)
            while remaining > 0:
                remaining -= 1
                time_ms = point.time + step * measure
                if time_ms > window_end or time_ms >= section_end:
                    break
                if time_ms >= window_start and not (step == 0 and point.omit_first_barline):
                    # Each line's own velocity, matching x_for_time -- a green
                    # line inside the section moves this one but not the ones
                    # before it.
                    column = round(
                        hit_x
                        + (time_ms - self.current_time)
                        * self.velocity_at(time_ms)
                        * self.px_per_beat
                    )
                    if column not in claimed_columns:
                        claimed_columns.add(column)
                        times.append(time_ms)
                step += 1
        return times

    # -- input ---------------------------------------------------------------

    def wheelEvent(self, event) -> None:
        angle = event.angleDelta()
        pixel = event.pixelDelta()
        delta = angle.y() or angle.x() or pixel.y() or pixel.x()
        if delta == 0:
            event.accept()
            return

        modifiers = event.modifiers() | QApplication.keyboardModifiers()
        if modifiers & Qt.ControlModifier:
            # Scroll speed, not zoom: this view has no window_ms to share, and
            # how fast the chart moves is a preference about the preview.
            factor = 1.16 if delta > 0 else 0.86
            self.px_per_beat = max(
                GAMEPLAY_PX_PER_BEAT_MIN,
                min(GAMEPLAY_PX_PER_BEAT_MAX, self.px_per_beat * factor),
            )
            self.update()
            event.accept()
            return

        threshold = 40.0 if angle.isNull() == 0 else 120.0
        self.wheel_accumulator += delta
        steps = int(self.wheel_accumulator / threshold)
        if steps:
            self.wheel_accumulator -= steps * threshold
            divisor = 1 if modifiers & Qt.ShiftModifier else self.snap_divisor
            self.current_time = wheel_seek_time(
                self.beat_points, self.current_time, -1 if steps > 0 else 1, divisor
            )
            self.seek_requested.emit(round(self.current_time))
            self.update()
        event.accept()

    # -- paint ---------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#12161d"))

        center_y = self.height() / 2
        normal_radius = min(30.0, self.height() * 0.19)
        big_radius = normal_radius * 1.4
        painter.fillRect(
            QRectF(0, center_y - big_radius * 1.2, self.width(), big_radius * 2.4),
            self.lane_brush,
        )

        start_time, end_time = self.visible_time_range()
        hit_x = self._hit_x()

        first = bisect_left(self.note_times, start_time - self._max_extend_ms)
        after_last = bisect_right(self.note_times, end_time)

        # Real drumroll bodies go under everything, barlines included. One is a
        # band tens of seconds wide, and drawn in with the notes it covered every
        # barline and every fake slider stacked on top of it. A fake slider has
        # no body (its length is zero or negative), so it stays with the notes.
        painter.setRenderHint(QPainter.Antialiasing, True)
        bodies, foreground = [], []
        for note in self.notes[first:after_last]:
            has_body = note.is_slider and self._note_end_time(note) is not None
            (bodies if has_body else foreground).append(note)
        for note in reversed(bodies):
            self._draw_note(painter, note, center_y, normal_radius, big_radius)

        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(self.barline_pen)
        top, bottom = round(center_y - big_radius * 1.2), round(center_y + big_radius * 1.2)
        for time_ms in self.barline_times(start_time, end_time):
            x = self.x_for_time(time_ms)
            if -1.0 <= x <= self.width() + 1.0:
                painter.drawLine(round(x), top, round(x), bottom)

        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(self.hit_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(hit_x, center_y), normal_radius, normal_radius)
        # Back to front: the note nearest the hit position is the one being
        # played, so it belongs on top -- osu!taiko draws them the same way.
        # Bodies are already down, under the barlines.
        for note in reversed(foreground):
            self._draw_note(painter, note, center_y, normal_radius, big_radius)

        # Second pass, so stacked objects compound: a note drawn on top of an
        # earlier note's flash would wipe it, and stacking a fake slider on a
        # note is exactly how a mapper makes that note read brighter.
        #
        # Gated on the *playhead* being in kiai as well as the note: kiai starts
        # when you arrive at it. Notes of a section still approaching are drawn
        # plain, however far into the screen they already are.
        pulse = (
            beat_pulse(self.beat_points, self.current_time)
            if in_kiai(self.timing_points, self.current_time)
            else 0.0
        )
        if pulse > 0.0:
            for note in self.notes[first:after_last]:
                if not in_kiai(self.timing_points, note.time):
                    continue
                x = self.x_for_time(note.time)
                radius = big_radius if (note.is_finisher or note.is_spinner) else normal_radius
                if -radius <= x <= self.width() + radius:
                    draw_kiai_flash(painter, x, center_y, radius, pulse)

    def _draw_note(self, painter, note, center_y, normal_radius, big_radius) -> None:
        x = self.x_for_time(note.time)
        radius = big_radius if (note.is_finisher or note.is_spinner) else normal_radius
        end_time = self._note_end_time(note)
        # A slider or spinner body travels with its *head*: it is one object at
        # one speed, not a series of points each reading its own timing.
        end_x = (
            x + (end_time - note.time) * self.velocity_at(note.time) * self.px_per_beat
            if end_time is not None and end_time > note.time
            else x
        )
        # The time-range slice is bounded by the slowest section in the map, so
        # a fast one lands objects well off either edge; drop those here.
        if end_x + radius < 0 or x - radius > self.width():
            return

        if note.is_spinner:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.spinner_band_brush)
            painter.drawRoundedRect(
                QRectF(x, center_y - radius, max(1.0, end_x - x), radius * 2), radius, radius
            )
            pixmap = spinner_pixmap()
            if not pixmap.isNull():
                size = radius * 2
                painter.drawPixmap(
                    QRectF(x - radius, center_y - radius, size, size), pixmap, QRectF(pixmap.rect())
                )
            return

        if note.is_slider:
            painter.setPen(self.note_pen)
            painter.setBrush(self.slider_brush)
            painter.drawRoundedRect(
                QRectF(x - radius, center_y - radius, max(2 * radius, end_x - x + 2 * radius), radius * 2),
                radius,
                radius,
            )
            return

        draw_note_sprite(
            painter, self.kat_brush if note.is_kat else self.don_brush, self.note_pen,
            x, center_y, radius,
        )


class TimingOverviewBar(QWidget):
    seek_requested = Signal(int)
    def __init__(self) -> None:
        super().__init__()
        self.duration_ms=1; self.current_time=0; self.viewport_start=0; self.viewport_end=0
        self.kiai=[]; self.timing_markers=[]; self._marker_kinds=[]; self.bookmarks=[]; self.preview_time=None; self.dragging=False
        self._marker_cache_key=None; self._marker_lines=[]
        self.setFixedHeight(28); self.setCursor(Qt.PointingHandCursor)
    def load_document(self,document,duration_ms:int)->None:
        self.duration_ms=max(duration_ms,document.hit_objects[-1].time if document.hit_objects else 1,1)
        self.kiai=kiai_ranges(document,self.duration_ms); self.timing_markers=[]
        self.bookmarks,self.preview_time=editor_timeline_metadata(document)
        self.timing_markers=[(round(point.time),point.uninherited) for point in document.timing_points]
        # Grouped once per edit, not per frame: a gimmick map carries tens of
        # thousands of points and this widget repaints on every clock tick.
        groups={}
        for time_ms,uninherited in self.timing_markers:groups.setdefault(time_ms,set()).add(uninherited)
        self._marker_kinds=sorted(
            (time_ms,"yellow" if len(kinds)>1 else "red" if True in kinds else "green")
            for time_ms,kinds in groups.items()
        )
        self._marker_cache_key=None
        self.update()
    def set_duration(self,value:int)->None:
        if value>0:self.duration_ms=value;self.update()
    def set_time(self,value:int)->None:self.current_time=max(0,min(value,self.duration_ms));self.update()
    def set_viewport(self,center:int,window_ms:float)->None:
        self.viewport_start=max(0,center-window_ms/2);self.viewport_end=min(self.duration_ms,center+window_ms/2);self.update()
    def _seek(self,x:float)->None:self.seek_requested.emit(round(max(0,min(1,x/max(1,self.width())))*self.duration_ms))
    def _marker_line_positions(self):
        """(x, kind) per pixel column, cached until the bar or the map changes.

        The bar is a few hundred pixels wide, so a gimmick map's thousands of
        markers collapse onto the same handful of columns -- recomputing that
        every clock tick was the widget's whole paint cost.
        """
        key=(self.width(),self.duration_ms,len(self._marker_kinds))
        if key!=self._marker_cache_key:
            seen=set();lines=[]
            for time_ms,kind in self._marker_kinds:
                x=round(time_ms/self.duration_ms*self.width())
                if (x,kind) in seen:continue
                seen.add((x,kind));lines.append((x,kind))
            self._marker_cache_key=key;self._marker_lines=lines
        return self._marker_lines
    def mousePressEvent(self,event)->None:
        if event.button()==Qt.LeftButton:self.dragging=True;self._seek(event.position().x())
    def mouseMoveEvent(self,event)->None:
        if self.dragging:self._seek(event.position().x())
    def mouseReleaseEvent(self,event)->None:
        if self.dragging:self._seek(event.position().x())
        self.dragging=False
    def paintEvent(self,event)->None:
        painter=QPainter(self);painter.fillRect(self.rect(),QColor("#0d1219"));painter.setRenderHint(QPainter.Antialiasing,False)
        center=self.height()//2
        kiai_height=max(3,self.height()//3);kiai_top=center-kiai_height//2
        painter.setPen(Qt.NoPen)
        for start,end in self.kiai:
            x=start/self.duration_ms*self.width();w=max(1,(end-start)/self.duration_ms*self.width())
            painter.fillRect(QRectF(x,kiai_top,w,kiai_height),QColor(255,170,0,82))
        painter.setPen(QPen(QColor(255,255,255,190),1));painter.drawLine(0,center,self.width(),center)
        colors={"yellow":QColor("#ffd400"),"red":QColor("#ff4545"),"green":QColor("#45d65a")}
        for x,kind in self._marker_line_positions():
            painter.setPen(QPen(colors[kind],1));painter.drawLine(x,1,x,center-1)
        painter.setPen(QPen(QColor("#3e9bff"),1))
        for bookmark in self.bookmarks:
            x=round(bookmark/self.duration_ms*self.width());painter.drawLine(x,center+1,x,self.height()-2)
        if self.preview_time is not None:
            x=round(self.preview_time/self.duration_ms*self.width());painter.setPen(QPen(QColor("#ffd400"),1));painter.drawLine(x,center+1,x,self.height()-2)
        if self.viewport_end>self.viewport_start:
            x=self.viewport_start/self.duration_ms*self.width();w=(self.viewport_end-self.viewport_start)/self.duration_ms*self.width()
            painter.setPen(QPen(QColor(255,255,255,65),1));painter.setBrush(Qt.NoBrush);painter.drawRect(QRectF(x,1,w,self.height()-2))
        x=round(self.current_time/self.duration_ms*self.width());painter.setPen(QPen(QColor("#ffffff"),1));painter.drawLine(x,0,x,self.height())


# Upper bound on the density heatmap's aggregation windows. The widget is a few
# hundred pixels wide, so anything finer is invisible -- and unbounded on a map
# whose timing points carry a near-zero beat length.
DENSITY_MAX_WINDOWS = 2000


class DensityOverview(QWidget):
    seek_requested = Signal(int)
    def __init__(self) -> None:
        super().__init__(); self.duration_ms=1; self.current_time=0; self.viewport_start=0; self.viewport_end=0; self.windows=[]; self.dragging=False
        self.static_layer = QPixmap()
        self.static_layer_dirty = True
        self.setFixedHeight(58); self.setCursor(Qt.PointingHandCursor)
    def load_document(self,document,duration_ms:int)->None:
        self.duration_ms=max(duration_ms,document.hit_objects[-1].time if document.hit_objects else 1,1)
        points=extract_timing_points(document); times=[n.time for n in document.hit_objects]
        self.windows=[]
        # One bar per 4 beats, but never finer than the widget can show: a
        # gimmick map's beat_length = 0.0001 points would otherwise ask for
        # hundreds of millions of sub-pixel windows and hang the load.
        min_window_ms=self.duration_ms/DENSITY_MAX_WINDOWS
        if points:
            for i,point in enumerate(points):
                section_end=points[i+1].time if i+1<len(points) else self.duration_ms
                cursor=point.time
                step=max(point.beat_length*4,min_window_ms)
                while cursor<section_end:
                    end=min(section_end,cursor+step)
                    count=bisect_left(times,end)-bisect_left(times,cursor)
                    self.windows.append((max(0,cursor),end,count)); cursor=end
        self.static_layer_dirty=True; self.update()
    def set_duration(self,value:int)->None:
        if value>0 and value!=self.duration_ms:
            self.duration_ms=value; self.static_layer_dirty=True; self.update()
    def set_time(self,value:int)->None:self.current_time=max(0,min(value,self.duration_ms));self.update()
    def set_viewport(self,center:int,window_ms:float)->None:
        self.viewport_start=max(0,center-window_ms/2); self.viewport_end=min(self.duration_ms,center+window_ms/2); self.update()
    def _seek(self,x:float)->None:self.seek_requested.emit(round(max(0,min(1,x/max(1,self.width())))*self.duration_ms))
    def mousePressEvent(self,event)->None:
        if event.button()==Qt.LeftButton:self.dragging=True;self._seek(event.position().x())
    def mouseMoveEvent(self,event)->None:
        if self.dragging:self._seek(event.position().x())
    def mouseReleaseEvent(self,event)->None:
        if self.dragging:self._seek(event.position().x())
        self.dragging=False
    def resizeEvent(self,event)->None:
        self.static_layer_dirty=True
        super().resizeEvent(event)
    def _rebuild_static_layer(self)->None:
        if self.width()<=0 or self.height()<=0:return
        layer=QPixmap(self.size()); layer.fill(QColor("#000000")); painter=QPainter(layer); painter.setPen(Qt.NoPen)
        maximum=max((c for _,_,c in self.windows),default=1) or 1
        for start,end,count in self.windows:
            if not count:continue
            x=start/self.duration_ms*self.width();w=max(1.0,(end-start)/self.duration_ms*self.width());h=max(1.0,(count/maximum)**.55*(self.height()-8))
            intensity=min(1.0,count/16.0); green=round(255+(216-255)*intensity); blue=round(255+(77-255)*intensity)
            painter.fillRect(QRectF(x,self.height()-h,w,h),QColor(255,green,blue))
        painter.end(); self.static_layer=layer; self.static_layer_dirty=False
    def paintEvent(self,event)->None:
        if self.static_layer_dirty or self.static_layer.size()!=self.size(): self._rebuild_static_layer()
        painter=QPainter(self); painter.drawPixmap(0,0,self.static_layer)
        if self.viewport_end>self.viewport_start:
            vx=self.viewport_start/self.duration_ms*self.width(); vw=(self.viewport_end-self.viewport_start)/self.duration_ms*self.width()
            painter.setPen(QPen(QColor(255,255,255,85),1)); painter.setBrush(Qt.NoBrush); painter.drawRect(QRectF(vx,1,vw,self.height()-2))
        x=self.current_time/self.duration_ms*self.width();painter.setPen(QPen(QColor("#ffffff"),2));painter.drawLine(round(x),0,round(x),self.height())


def freeze_preview_value(value):
    """Convert nested preview parameters into stable, hashable cache data."""
    if isinstance(value, dict):
        return tuple(sorted((str(key), freeze_preview_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_preview_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(freeze_preview_value(item) for item in value))
    return value

class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)

        self._full_text = text
        self.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Preferred,
        )
        self.setMinimumWidth(0)
        self.setToolTip(text)

    def setText(self, text: str) -> None:
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._update_elided_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elided_text()

    def _update_elided_text(self) -> None:
        available_width = max(0, self.width() - 4)

        elided = self.fontMetrics().elidedText(
            self._full_text,
            Qt.ElideRight,
            available_width,
        )

        super().setText(elided)


class EditorViewFrame(QWidget):
    """Chrome around one Editor-page view: close, lock, and a difficulty label.

    The difficulty name is shown exactly once per view, at the **right** of
    this chrome row. It used to appear twice for a chart view -- once here and
    once in the group header above the whole difficulty group -- which is what
    made every difficulty read as if it were labelled on both sides. The group
    header no longer carries a label, so this is the only place it appears,
    and it is shown for every view type (not just chart) since nothing else
    identifies an SV/density/gimmick view's difficulty anymore.

    Holds no MainWindow reference; the owner wires content in via set_content
    and listens for `closed`.
    """

    closed = Signal(object)

    def __init__(self, view_type: str, difficulty_label: str) -> None:
        super().__init__()
        self.view_type = view_type
        self.locked = False
        self.content: QWidget | None = None

        self.setStyleSheet("background: #1b212b; border: 1px solid #303947; border-radius: 6px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # EditorViewFrame's own bare (selector-less) stylesheet above breaks
        # the app-wide pink QPushButton cascade for its descendants, so the
        # chrome buttons render small and low-contrast unless styled here
        # explicitly.
        chrome_button_style = (
            "QPushButton { background: #f3a6bd; color: #17191f; border: 0;"
            " border-radius: 6px; font-weight: 600; }"
            "QPushButton:hover { background: #f7bfd0; }"
            "QPushButton:checked { background-color: #ff66aa; color: #ffffff;"
            " border: 1px solid #ff9dcc; font-weight: 700; }"
        )

        chrome = QHBoxLayout()
        self.close_button = QPushButton("✕")
        self.close_button.setFixedWidth(28)
        self.close_button.setToolTip(tr("MainWindow", "Close view"))
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setStyleSheet(chrome_button_style)
        self.close_button.clicked.connect(lambda: self.closed.emit(self))
        chrome.addWidget(self.close_button)

        self.lock_button = QPushButton("🔒")
        self.lock_button.setCheckable(True)
        self.lock_button.setFixedWidth(28)
        self.lock_button.setToolTip(tr("MainWindow", "Lock view (read-only)"))
        self.lock_button.setFocusPolicy(Qt.NoFocus)
        self.lock_button.setStyleSheet(chrome_button_style)
        self.lock_button.toggled.connect(self._set_locked)
        chrome.addWidget(self.lock_button)

        chrome.addStretch(1)
        self.difficulty_name_label = QLabel(difficulty_label)
        self.difficulty_name_label.setStyleSheet("color:#f3a6bd;font-weight:700;border:0;")
        chrome.addWidget(self.difficulty_name_label)

        layout.addLayout(chrome)

        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._content_layout, 1)

    def set_content(self, widget: QWidget) -> None:
        self.content = widget
        self._content_layout.addWidget(widget)

    def _set_locked(self, locked: bool) -> None:
        self.locked = locked
        if self.content is not None:
            self.content.setEnabled(not locked)


class AddViewDialog(QDialog):
    """Choose a view type and difficulty for a new Editor-page view."""

    def __init__(self, difficulties: list[tuple[str, Path]], parent=None, current: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("MainWindow", "Add view"))
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        layout = QFormLayout(self)

        self.type_combo = QComboBox()
        # Literal tr() calls, one per type: the i18n coverage gate statically
        # scans for tr("context", "constant") pairs and would silently miss a
        # dynamic VIEW_TYPE_LABELS[key] lookup here.
        self.type_combo.addItem(tr("MainWindow", "Chart"), "chart")
        self.type_combo.addItem(tr("MainWindow", "SV Editor"), "sv")
        self.type_combo.addItem(tr("MainWindow", "Gimmick Editor"), "gimmick")
        self.type_combo.addItem(tr("MainWindow", "Gameplay Viewer"), "gameplay")
        self.type_combo.addItem(tr("MainWindow", "Density"), "density")
        layout.addRow(tr("MainWindow", "View type"), self.type_combo)

        self.difficulty_combo = QComboBox()
        for label, path in difficulties:
            self.difficulty_combo.addItem(label, str(path))
        # The difficulty being edited, not whichever one sorts first: adding a
        # view is nearly always about the one already open.
        found = self.difficulty_combo.findData(str(current)) if current is not None else -1
        if found >= 0:
            self.difficulty_combo.setCurrentIndex(found)
        layout.addRow(tr("MainWindow", "Difficulty"), self.difficulty_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def selected_view_type(self) -> str:
        return str(self.type_combo.currentData())

    def selected_difficulty_path(self) -> Path:
        return Path(self.difficulty_combo.currentData())


def sv_ease(function_id: str, t: float) -> float:
    """0..1 progress -> 0..1 eased position. Shared by the preview square and
    the actual generated points, so the preview is an honest picture of what
    Generate produces, not just a decoration.
    """
    t = max(0.0, min(1.0, t))
    if function_id == "sin_in":
        return 1 - math.cos(t * math.pi / 2)
    if function_id == "sin_out":
        return math.sin(t * math.pi / 2)
    if function_id == "exp1.3":
        return t ** 1.3
    if function_id == "exp1.6":
        return t ** 1.6
    if function_id == "true_exp":
        k = 3.0
        return (math.exp(k * t) - 1) / (math.exp(k) - 1)
    if function_id == "sin":
        return -(math.cos(math.pi * t) - 1) / 2
    return t  # "linear" and any unrecognized id


class SVFunctionPreview(QWidget):
    """20 dots showing what Generate would actually produce.

    Plots the real rate at each step (initial -> final through `sv_ease`), not
    a normalised 0..1 curve, so a 1.10x -> 0.90x sweep is drawn *descending*.
    The old version always rose left-to-right whatever the rates were, which
    made it a picture of the easing function rather than of the result -- the
    one case where the preview mattered most (did I get the direction right?)
    was the case it could not show.
    """

    def __init__(self, width: int = 190, height: int = 130, *, labels: bool = True) -> None:
        super().__init__()
        self.function_id = "linear"
        self.initial_rate = 1.0
        self.final_rate = 1.0
        # The dialog renders one of these per function button, at button size
        # and without the endpoint labels -- there is no room for them there,
        # and the same two numbers on all seven tiles say nothing anyway.
        self.labels = labels
        self.setFixedSize(width, height)

    def set_function(self, function_id: str) -> None:
        self.function_id = function_id
        self.update()

    def set_range(self, initial_rate: float, final_rate: float) -> None:
        self.initial_rate = float(initial_rate)
        self.final_rate = float(final_rate)
        self.update()

    def rate_at(self, t: float) -> float:
        return self.initial_rate + (self.final_rate - self.initial_rate) * sv_ease(self.function_id, t)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#151b24"))
        painter.setPen(QPen(QColor("#3a4554"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        margin = 22.0 if self.labels else 10.0
        width = self.width() - margin * 2
        height = self.height() - margin * 2

        low = min(self.initial_rate, self.final_rate)
        high = max(self.initial_rate, self.final_rate)
        span = high - low

        dots = []
        for i in range(20):
            t = i / 19
            rate = self.rate_at(t)
            # A flat sweep has no span to normalise against; draw it down the
            # middle rather than dividing by zero or pinning it to an edge.
            ratio = 0.5 if span < 1e-9 else (rate - low) / span
            dots.append(QPointF(margin + t * width, margin + (1 - ratio) * height))

        painter.setPen(QPen(QColor(243, 166, 189, 110), 1))
        painter.drawPolyline(QPolygonF(dots))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#f3a6bd"))
        radius = 3 if self.labels else 2
        for dot in dots:
            painter.drawEllipse(dot, radius, radius)

        if not self.labels:
            return

        painter.setPen(QPen(QColor(225, 230, 240, 220), 1))
        painter.drawText(QPointF(4, dots[0].y() - 6), f"{self.initial_rate:.2f}x")
        final_text = f"{self.final_rate:.2f}x"
        text_width = painter.fontMetrics().horizontalAdvance(final_text)
        painter.drawText(QPointF(self.width() - text_width - 4, dots[-1].y() - 6), final_text)


class SVFunctionDialog(QDialog):
    """M4 function mode: drag-select a range, then generate an eased SV sweep
    across it.

    Laid out in two columns: every configurable field on the **left**, and the
    function chooser plus its preview on the **right**. The functions are a
    grid of square toggle buttons rather than a drop-down -- there are only
    seven, choosing one is the decision the preview exists to inform, and a
    combo box hid six of them behind a click.

    "Generate at" decides the point positions:

    * **Each note** (default) -- one SV point per hit object inside the
      selected range, and nothing between them. This is what an SV sweep is
      usually for: the scroll speed only has to be right where a note is.
    * **Every snap** -- one point per beat subdivision of the chosen divisor,
      for sweeps that must move continuously rather than note by note.

    Position offset defaults to -5ms: an SV point has to take effect
    *slightly before* the note it governs, or that note is still drawn at the
    previous speed.
    """

    DEFAULT_POSITION_OFFSET_MS = -5
    # Square tile, with its graph above the name. The tile *is* the preview now,
    # so it is worth the room: at the old 78px there was nothing to read.
    TILE_SIZE = 144
    TILE_GRAPH_SIZE = (TILE_SIZE - 16, TILE_SIZE - 34)

    def __init__(
        self, start_ms: float, end_ms: float, parent=None, snap_divisor: int = 4,
        initial_rate: float = 1.0, final_rate: float = 1.0,
    ) -> None:
        super().__init__(parent)
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.setWindowTitle(tr("MainWindow", "Generate SV"))
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        root = QVBoxLayout(self)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        root.addLayout(columns, 1)

        # Left column: everything configurable.
        layout = QFormLayout()
        columns.addLayout(layout)

        self.initial_rate_spin = QDoubleSpinBox()
        self.initial_rate_spin.setRange(0.1, 10.0)
        self.initial_rate_spin.setSingleStep(0.05)
        self.initial_rate_spin.setDecimals(2)
        self.initial_rate_spin.setValue(round(float(initial_rate), 2))
        self.initial_rate_spin.valueChanged.connect(self._update_preview)
        layout.addRow(tr("MainWindow", "Initial rate"), self.initial_rate_spin)

        self.final_rate_spin = QDoubleSpinBox()
        self.final_rate_spin.setRange(0.1, 10.0)
        self.final_rate_spin.setSingleStep(0.05)
        self.final_rate_spin.setDecimals(2)
        self.final_rate_spin.setValue(round(float(final_rate), 2))
        self.final_rate_spin.valueChanged.connect(self._update_preview)
        layout.addRow(tr("MainWindow", "Final rate"), self.final_rate_spin)

        self.placement_combo = QComboBox()
        # Literal tr() calls, one per option -- see AddViewDialog's note on why
        # a dict lookup here would silently escape the i18n coverage gate.
        self.placement_combo.addItem(tr("MainWindow", "Each note"), "notes")
        self.placement_combo.addItem(tr("MainWindow", "Every snap"), "snaps")
        self.placement_combo.currentIndexChanged.connect(self._update_placement_controls)
        layout.addRow(tr("MainWindow", "Generate at"), self.placement_combo)

        self.snap_combo = QComboBox()
        for divisor in SNAP_DIVISORS:
            self.snap_combo.addItem(f"1/{divisor}", divisor)
        found = self.snap_combo.findData(int(snap_divisor))
        self.snap_combo.setCurrentIndex(found if found >= 0 else self.snap_combo.findData(4))
        self.snap_label = QLabel(tr("MainWindow", "Snap"))
        layout.addRow(self.snap_label, self.snap_combo)
        self._form_layout = layout

        self.position_offset_spin = QSpinBox()
        self.position_offset_spin.setRange(-5000, 5000)
        # An SV point governs what comes after it, so it has to land slightly
        # before the note it is meant to affect.
        self.position_offset_spin.setValue(self.DEFAULT_POSITION_OFFSET_MS)
        layout.addRow(tr("MainWindow", "Position offset (ms)"), self.position_offset_spin)

        self.omit_barline_check = QCheckBox()
        self.omit_barline_check.setChecked(False)
        layout.addRow(tr("MainWindow", "Omit barline"), self.omit_barline_check)

        self.relative_to_final_bpm_check = QCheckBox()
        self.relative_to_final_bpm_check.setChecked(True)
        layout.addRow(tr("MainWindow", "Relative to final BPM"), self.relative_to_final_bpm_check)

        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # Right column: function choice and what it produces.
        right = QVBoxLayout()
        right.setSpacing(8)
        columns.addLayout(right)
        right.addWidget(QLabel(tr("MainWindow", "Function")))

        # Literal tr() calls, one per function -- see AddViewDialog's note on
        # why a dynamic lookup here would silently escape the i18n gate.
        functions = (
            ("linear", tr("MainWindow", "Linear")),
            ("sin_in", tr("MainWindow", "Sin In")),
            ("sin_out", tr("MainWindow", "Sin Out")),
            ("exp1.3", tr("MainWindow", "Exp 1.3")),
            ("exp1.6", tr("MainWindow", "Exp 1.6")),
            ("true_exp", tr("MainWindow", "True Exp")),
            ("sin", tr("MainWindow", "Sin")),
        )
        self.function_buttons: dict[str, QToolButton] = {}
        self._function_group = QButtonGroup(self)
        self._function_group.setExclusive(True)
        grid = QGridLayout()
        grid.setSpacing(6)
        for index, (function_id, label) in enumerate(functions):
            button = QToolButton()
            button.setText(label)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(*self.TILE_GRAPH_SIZE))
            button.setCheckable(True)
            button.setFixedSize(self.TILE_SIZE, self.TILE_SIZE)
            button.setFocusPolicy(Qt.NoFocus)
            button.setChecked(function_id == "linear")
            button.toggled.connect(
                lambda checked, f=function_id: self._update_preview() if checked else None
            )
            self._function_group.addButton(button)
            self.function_buttons[function_id] = button
            grid.addWidget(button, index // 3, index % 3)
        right.addLayout(grid)
        right.addStretch(1)

        # Not in the layout: each tile carries its own graph now, so this only
        # renders them. One widget re-grabbed per function beats seven live
        # ones -- and it keeps the plotting in a single paintEvent.
        self.preview = SVFunctionPreview(*self.TILE_GRAPH_SIZE, labels=False)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("MainWindow", "Generate"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._update_preview()
        self._update_placement_controls()

    def selected_function(self) -> str:
        for function_id, button in self.function_buttons.items():
            if button.isChecked():
                return function_id
        return "linear"

    def set_selected_function(self, function_id: str) -> None:
        button = self.function_buttons.get(function_id)
        if button is not None:
            button.setChecked(True)

    def _update_preview(self) -> None:
        """Re-plot every tile against the current rates.

        All seven, not just the selected one: the tiles *are* the preview, and
        their whole job is to be compared against each other for the range you
        actually typed -- a 1.10x -> 0.90x sweep has to show all seven curves
        descending.
        """
        self.preview.set_range(self.initial_rate_spin.value(), self.final_rate_spin.value())
        for function_id, button in self.function_buttons.items():
            self.preview.set_function(function_id)
            button.setIcon(QIcon(self.preview.grab()))
        self.preview.set_function(self.selected_function())

    def _update_placement_controls(self) -> None:
        """The snap divisor only means anything in "Every snap" mode.

        setRowVisible collapses the whole form row; hiding the two widgets
        individually would leave the row's blank space behind.
        """
        every_snap = str(self.placement_combo.currentData()) == "snaps"
        self._form_layout.setRowVisible(self.snap_combo, every_snap)

    def parameters(self) -> dict:
        return {
            "initial_rate": self.initial_rate_spin.value(),
            "final_rate": self.final_rate_spin.value(),
            "placement": str(self.placement_combo.currentData()),
            "snap_divisor": int(self.snap_combo.currentData()),
            "position_offset": self.position_offset_spin.value(),
            "omit_barline": self.omit_barline_check.isChecked(),
            "relative_to_final_bpm": self.relative_to_final_bpm_check.isChecked(),
            "function": self.selected_function(),
        }


class LanguageDialog(QDialog):
    """First-start language choice, shown before anything else is built.

    Deliberately not translated: it is the one screen that cannot assume which
    language the reader wants, so both options are written in their own
    language and there is nothing else on it to translate.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Language / 言語")
        self.language = "en"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        heading = QLabel("Choose your language\n言語を選んでください")
        heading.setAlignment(Qt.AlignCenter)
        heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(heading)
        for code, label in (("en", "English"), ("ja", "日本語")):
            button = QPushButton(label)
            button.setMinimumHeight(44)
            button.clicked.connect(lambda _checked=False, value=code: self._choose(value))
            layout.addWidget(button)
        self.setStyleSheet(
            """
            QDialog { background: #191f29; }
            QLabel { color: #e8edf3; }
            QPushButton { background: #f3a6bd; color: #17191f; border: 0; border-radius: 6px;
                          padding: 10px 18px; font-weight: 600; font-size: 14px; }
            QPushButton:hover { background: #f7bfd0; }
            """
        )

    def _choose(self, code: str) -> None:
        self.language = code
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.app_name="Taiko Fancy Arranger";self.setWindowTitle(self.app_name)
        icon=application_icon()
        if not icon.isNull():self.setWindowIcon(icon)
        # System font, two points up: the default reads small on the dense
        # editor pages, and raising the application font (rather than a
        # stylesheet px size) keeps every dialog and DPI scaling in step.
        base_font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        if base_font.pointSizeF() > 0:
            base_font.setPointSizeF(base_font.pointSizeF() + UI_FONT_POINT_BOOST)
        elif base_font.pixelSize() > 0:
            # A pixel-defined system font reports pointSizeF() as -1; adding to
            # that would set a 1pt font instead of a slightly larger one.
            base_font.setPixelSize(base_font.pixelSize() + 3)
        QApplication.instance().setFont(base_font)
        self.resize(1360, 900)

        self.settings = SettingsManager()
        self.shortcuts = ShortcutRegistry(self.settings)
        # One DifficultyState per source path visited this session, so
        # switching difficulty and back preserves edits, selection, undo
        # history and cursor position instead of discarding them.
        self._states: dict[Path, DifficultyState] = {}
        self.state: DifficultyState | None = None
        self.song_difficulties: list[Path] = []
        # Every TimelineGameplay currently on screen: the shared player-deck
        # timeline plus any Editor-page chart views. _render_gameplay_frame
        # broadcasts the clock to all of them instead of just self.timeline.
        self._chart_views: list[TimelineGameplay] = []
        # Subset of _chart_views, plus every open SV editor view: everything
        # Editor-page-added that has its own snap_divisor (not self.timeline).
        # Alt+wheel on the Editor page and its own snap combo drive every
        # entry here together, regardless of whether it's a chart or SV view.
        self._editor_snap_views: list[TimelineGameplay | SVEditorView | GameplayViewerView] = []
        # Every open SV editor view. Broadcast the clock to these alongside
        # _chart_views so the SV editor scrolls with the chart during
        # playback instead of standing still.
        self._sv_views: list[SVEditorView] = []
        # Every open gameplay viewer. On the same clock, but not in
        # _editor_snap_views' zoom family: it scrolls by distance, not by a
        # shared time window.
        self._gameplay_views: list[GameplayViewerView] = []
        # Zoom (window_ms) is per difficulty, shared by every view of that
        # difficulty: a chart and an SV editor showing different time spans
        # cannot stay visually aligned while the playhead moves.
        self._difficulty_zoom: dict[Path, float] = {}
        # Ctrl+C / Ctrl+V payloads, kept as plain data (time offset + the
        # fields needed to rebuild) rather than live objects, so a paste
        # always creates fresh identities and pasting twice is two objects.
        self._note_clipboard: list[dict] = []
        self._sv_clipboard: list[dict] = []
        # Every open Editor-page density view, broadcast separately since
        # DensityOverview.set_time has a different signature than
        # TimelineGameplay.set_time (no `force` kwarg). self.fancy_density
        # (Fancy Arranger's own, always showing the active difficulty) is
        # appended here too once built, for the same broadcast.
        self._density_views: list[DensityOverview] = []
        # self.timing_bar (Editor page) + self.fancy_timing_bar (Fancy
        # Arranger page): two instances of the same widget type -- a
        # TimingOverviewBar can only live in one layout at a time -- kept in
        # sync wherever the active difficulty's timing info changes.
        self._timing_bars: list[TimingOverviewBar] = []
        # Whichever Editor-page chart/SV view last had keyboard focus; the
        # matching global tool row (below the view stack) acts on it, and
        # the other row hides. Two separate trackers since a chart view and
        # an SV view can't both be "the" active one at the same time.
        self._active_chart_view: TimelineGameplay | None = None
        self._active_sv_view: SVEditorView | None = None
        # Whichever of the two was touched most recently, regardless of type.
        # _active_chart_view is not cleared when an SV view takes focus (each
        # tracker keeps its own last target for its own tool row), so it
        # cannot answer "what was the user just editing?" on its own -- which
        # is what Ctrl+Z needs when several difficulties are open at once.
        self._last_focused_editor_view: TimelineGameplay | SVEditorView | None = None
        QApplication.instance().focusChanged.connect(self._editor_view_focus_changed)

        # Song library: {song folder: [TaikoDifficulty, ...]} for every mode=1
        # chart found under the configured osu! Songs folder. Filled by the
        # sliced scan (_scan_step), persisted between runs in _library_cache.
        self._library_songs: dict[Path, list] = {}
        self._library_cache: dict[str, list] = {}
        self._scan_songs: dict[Path, list] = {}
        self._listed_paths: set[Path] = set()
        self._scan_iterator = None
        self._scan_files = 0
        self._scan_since_refresh = 0
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(0)
        self.scan_timer.timeout.connect(self._scan_step)

        self.position_controls: dict[str, dict[str, ParameterControl]] = {}
        self.drawing_points={"all":[],"don":[],"kat":[]};self.last_drawing_points=[];self.drawing_dialog_active=False
        self.controls: dict[
            str,
            dict[str, ParameterControl],
        ] = {
            "all": {},
            "don": {},
            "kat": {},
        }

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.65)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(
            self._player_position_changed
        )
        self.player.durationChanged.connect(
            lambda _duration: self._update_timeline_info()
        )
        self.audio_anchor_position = 0
        self.audio_anchor_clock = QElapsedTimer()
        self.audio_anchor_clock.start()
        self.latest_audio_position = 0

        # Never restarted after this; _render_gameplay_frame schedules off a
        # fixed cadence (self._next_frame_due_ns) measured against it instead
        # of resetting to "now" every tick, which is what kept turning a
        # slightly-late timer wakeup into a permanently shifted, uneven frame
        # pace -- the main source of visible stutter in the old scheme.
        self.gameplay_frame_clock = QElapsedTimer()
        self.gameplay_frame_clock.start()
        self.gameplay_frame_interval_ns = 8_333_333  # ~120fps
        self._next_frame_due_ns = self.gameplay_frame_interval_ns
        # Last position handed to the views, so a paused playhead stops costing
        # a full repaint of every open view per frame.
        self._last_broadcast_position: float | None = None

        self.gameplay_render_timer = QTimer(self)
        self.gameplay_render_timer.setTimerType(Qt.PreciseTimer)
        self.gameplay_render_timer.setInterval(4)
        self.gameplay_render_timer.timeout.connect(self._render_gameplay_frame)
        self.gameplay_render_timer.start()

        self.info_timer = QTimer(self)
        self.info_timer.setInterval(16)
        self.info_timer.timeout.connect(self._update_timeline_info)
        self.info_timer.start()

        self.play_shortcut = QShortcut(QKeySequence(self.shortcuts.sequence("play_pause")), self)
        self.play_shortcut.setContext(Qt.ApplicationShortcut)
        self.play_shortcut.activated.connect(self._toggle_playback_from_shortcut)

        self.undo_shortcut=QShortcut(QKeySequence(self.shortcuts.sequence("undo")),self); self.undo_shortcut.setContext(Qt.ApplicationShortcut); self.undo_shortcut.activated.connect(self.undo)
        self.redo_shortcut=QShortcut(QKeySequence(self.shortcuts.sequence("redo")),self); self.redo_shortcut.setContext(Qt.ApplicationShortcut); self.redo_shortcut.activated.connect(self.redo)
        self.save_shortcut = QShortcut(QKeySequence(self.shortcuts.sequence("save_all")), self)
        self.save_shortcut.setContext(Qt.ApplicationShortcut)
        self.save_shortcut.activated.connect(self._save_from_shortcut)
        self._build_tool_shortcuts()
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(70)
        self.preview_timer.timeout.connect(self.update_preview)

        self._build_ui()
        # After _build_ui: these are scoped to self.editor_page, which does
        # not exist until the pages are built.
        self._build_clipboard_shortcuts()
        self._rebuild_control_tabs()
        QApplication.instance().installEventFilter(self)

    # -- DifficultyState forwarding -----------------------------------------
    #
    # These keep every existing call site (self.applied_positions[...], etc.)
    # working unchanged while the underlying storage moves to self.state, one
    # DifficultyState per open difficulty. Getters must tolerate self.state
    # being None: eventFilter reads self.selected on every application click,
    # before any map is open.

    @property
    def document(self):
        return self.state.document if self.state else None

    @property
    def source_path(self) -> Path | None:
        return self.state.source_path if self.state else None

    @property
    def current_background_path(self) -> Path | None:
        return self.state.background_path if self.state else None

    @current_background_path.setter
    def current_background_path(self, value) -> None:
        if self.state is not None:
            self.state.background_path = value

    @property
    def original_positions(self) -> dict[int, tuple[int, int]]:
        return self.state.original_positions if self.state else {}

    @property
    def applied_positions(self) -> dict[int, tuple[int, int]]:
        return self.state.applied_positions if self.state else {}

    @applied_positions.setter
    def applied_positions(self, value) -> None:
        if self.state is not None:
            self.state.applied_positions = value

    @property
    def preview_positions(self) -> dict[int, tuple[int, int]]:
        return self.state.preview_positions if self.state else {}

    @preview_positions.setter
    def preview_positions(self, value) -> None:
        if self.state is not None:
            self.state.preview_positions = value

    @property
    def selected(self) -> set[int]:
        return self.state.selected if self.state else set()

    @selected.setter
    def selected(self, value) -> None:
        if self.state is not None:
            self.state.selected = value

    @property
    def preview_offsets(self) -> dict[str, list[float]]:
        if self.state is None:
            return {"all": [0.0, 0.0], "don": [0.0, 0.0], "kat": [0.0, 0.0]}
        return self.state.preview_offsets

    @preview_offsets.setter
    def preview_offsets(self, value) -> None:
        if self.state is not None:
            self.state.preview_offsets = value

    @property
    def preview_cache(self) -> dict:
        return self.state.preview_cache if self.state else {}

    @preview_cache.setter
    def preview_cache(self, value) -> None:
        if self.state is not None:
            self.state.preview_cache = value

    @property
    def commit_revision(self) -> int:
        return self.state.history.revision if self.state else 0

    @staticmethod
    def _is_descendant(widget, ancestor) -> bool:
        while widget is not None:
            if widget is ancestor: return True
            widget=widget.parentWidget()
        return False
    def eventFilter(self,watched,event)->bool:
        if self.drawing_dialog_active and event.type()==QEvent.MouseButtonPress:return super().eventFilter(watched,event)
        if event.type()==QEvent.Wheel and hasattr(self,"timeline"):
            modifiers=event.modifiers() | QApplication.keyboardModifiers()
            alt_down=bool(modifiers & Qt.AltModifier)
            if alt_down:
                angle=event.angleDelta();pixel=event.pixelDelta()
                delta=angle.y() or angle.x() or pixel.y() or pixel.x()
                if delta:
                    # Snap is global per page: on the Editor page every open
                    # chart view moves together; on Fancy Arranger it's just
                    # self.timeline, as before.
                    on_editor_page = hasattr(self,"page_stack") and self.page_stack.currentWidget() is self.editor_page
                    if on_editor_page and hasattr(self,"editor_snap_combo"):
                        self._editor_change_snap_from_wheel(delta)
                    else:
                        self.timeline._change_snap_from_wheel(delta)
                event.accept()
                return True
        # Fancy-Arranger-only: this selection belongs to the transform
        # workflow on that page, so a click elsewhere (including anywhere on
        # the Editor page) must not clear it just because it landed outside
        # these three widgets.
        on_fancy_arranger_page = hasattr(self, "page_stack") and self.page_stack.currentWidget() is self.fancy_arranger_page
        if event.type()==QEvent.MouseButtonPress and on_fancy_arranger_page and hasattr(self,"timeline") and self.selected:
            clicked=QApplication.widgetAt(event.globalPosition().toPoint())
            inside_timeline = self._is_descendant(clicked, self.timeline)
            inside_controls = self._is_descendant(clicked, self.transform_controls_panel)
            inside_canvas = self._is_descendant(clicked, self.canvas)
            if not inside_timeline and not inside_controls and not inside_canvas:
                self.clear_transform_selection()
        return super().eventFilter(watched,event)
    def clear_transform_selection(self)->None:
        self.selected.clear(); self.timeline.selected.clear(); self.preview_positions=dict(self.applied_positions); self.refresh_canvas()

    def _toggle_playback_from_shortcut(self) -> None:
        if should_ignore_shortcut_focus(QApplication.focusWidget()):
            return
        if self.document is not None:
            self.toggle_playback()

    def _save_from_shortcut(self) -> None:
        if not should_ignore_shortcut_focus(QApplication.focusWidget()) and self.state is not None:
            self.save_all_states()

    def _reload_shortcuts(self) -> None:
        """Re-read every registry-driven binding after Settings applies.

        `back_to_songs` needs nothing here: keyPressEvent compares against the
        stored sequence each time rather than holding a QShortcut.
        """
        for action_id, shortcut in (
            ("play_pause", self.play_shortcut),
            ("undo", self.undo_shortcut),
            ("redo", self.redo_shortcut),
            ("save_all", self.save_shortcut),
            ("copy", self.copy_shortcut),
            ("paste", self.paste_shortcut),
            *self.tool_shortcuts.items(),
        ):
            shortcut.setKey(QKeySequence(self.shortcuts.sequence(action_id)))

    def open_settings(self) -> None:
        SettingsDialog(self.settings, self.shortcuts, self).exec()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)
        self.setCentralWidget(central)

        # Global header, shown on both pages: file/session actions that make
        # sense regardless of which page you're on, plus the page tabs
        # themselves (top-right). Difficulty/Play/Reset/AR/CS and the old
        # file-dialog export are Fancy-Arranger-specific and live on that
        # page instead -- see _build_fancy_arranger_page.
        header = QHBoxLayout()

        open_button = QPushButton(tr("MainWindow", "Open .osu"))
        open_button.clicked.connect(self.open_map)
        open_button.setFocusPolicy(Qt.NoFocus)
        header.addWidget(open_button)

        self.undo_button = QPushButton(tr("MainWindow", "Undo"))
        self.undo_button.clicked.connect(self.undo)
        self.undo_button.setFocusPolicy(Qt.NoFocus)
        self.undo_button.setEnabled(False)
        header.addWidget(self.undo_button)

        self.redo_button = QPushButton(tr("MainWindow", "Redo"))
        self.redo_button.clicked.connect(self.redo)
        self.redo_button.setFocusPolicy(Qt.NoFocus)
        self.redo_button.setEnabled(False)
        header.addWidget(self.redo_button)

        self.save_all_button = QPushButton(tr("MainWindow", "Save"))
        self.save_all_button.setToolTip(tr("MainWindow", "Save every changed difficulty"))
        self.save_all_button.clicked.connect(self.save_all_states)
        self.save_all_button.setFocusPolicy(Qt.NoFocus)
        self.save_all_button.setEnabled(False)
        header.addWidget(self.save_all_button)

        self.export_new_difficulty_button = QPushButton(tr("MainWindow", "Export"))
        self.export_new_difficulty_button.setToolTip(tr("MainWindow", "Export as a new difficulty in the same folder"))
        self.export_new_difficulty_button.clicked.connect(self.export_new_difficulty)
        self.export_new_difficulty_button.setFocusPolicy(Qt.NoFocus)
        self.export_new_difficulty_button.setEnabled(False)
        header.addWidget(self.export_new_difficulty_button)

        # Status/log line sits with the action buttons on the left, not wedged
        # between them and Settings / the page tabs on the right: a long
        # message there read as a caption belonging to those buttons.
        # Takes the header's leftover width instead of a fixed 460px, which
        # elided ordinary messages to "..." while empty space sat to its
        # right. It still elides when the header genuinely runs out of room,
        # and ElidedLabel keeps the full text as its tooltip either way.
        self.status = ElidedLabel(tr("MainWindow", "Open a map to begin."))
        self.status.setMinimumWidth(100)
        self.status.setAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )
        header.addWidget(self.status, 1)

        self.settings_button = QPushButton(f"⚙ {tr('SettingsDialog', 'Settings')}")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setToolTip(tr("MainWindow", "Open application settings"))
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.setMinimumWidth(100)
        self.settings_button.setFocusPolicy(Qt.NoFocus)
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)

        # Songs (the library browser you start on and Esc back to), then
        # Editor, then Fancy Arranger. Tabs sit rightmost in the global header.
        self.library_page_button = QPushButton(tr("MainWindow", "Songs"))
        self.editor_page_button = QPushButton(tr("MainWindow", "Editor"))
        self.fancy_arranger_page_button = QPushButton(tr("MainWindow", "Fancy Arranger"))
        for button in (self.library_page_button, self.editor_page_button, self.fancy_arranger_page_button):
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
        self.page_button_group = QButtonGroup(self)
        self.page_button_group.setExclusive(True)
        self.page_button_group.addButton(self.library_page_button, PAGE_LIBRARY)
        self.page_button_group.addButton(self.editor_page_button, PAGE_EDITOR)
        self.page_button_group.addButton(self.fancy_arranger_page_button, PAGE_FANCY)
        self.library_page_button.setChecked(True)
        self.page_button_group.idClicked.connect(self._switch_page)
        header.addWidget(self.library_page_button)
        header.addWidget(self.editor_page_button)
        header.addWidget(self.fancy_arranger_page_button)

        root.addLayout(header)

        self.page_stack = QStackedWidget()
        self.library_page = self._build_library_page()
        self.page_stack.addWidget(self.library_page)
        self.editor_page = self._build_editor_page()
        self.page_stack.addWidget(self.editor_page)
        self.fancy_arranger_page = self._build_fancy_arranger_page()
        self.page_stack.addWidget(self.fancy_arranger_page)
        self.page_stack.setCurrentIndex(PAGE_LIBRARY)
        root.addWidget(self.page_stack, 1)

        # Both pages' playback-speed button sets exist now; sync them once.
        self._change_playback_speed(1.0)

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #191f29;
                color: #e8edf3;
            }
            QPushButton {
                background: #f3a6bd; color: #17191f;
                border: 0;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover { background: #f7bfd0; }
            QPushButton:disabled {
                background: #39414d;
                color: #7d8794;
            }
            QComboBox, QSpinBox, QDoubleSpinBox {
                background: #252d39;
                border: 1px solid #3a4554;
                border-radius: 5px;
                padding: 5px;
            }
            QTabWidget::pane { border: 1px solid #303947; }
            QTabBar::tab {
                background: #222a36;
                padding: 8px 18px;
            }
            QTabBar::tab:selected { background: #f3a6bd; color: #17191f; }
            """
        )

    def _build_fancy_arranger_page(self) -> QWidget:
        """Everything specific to the transform-and-preview workflow: its own
        sub-toolbar (Difficulty/Play/Reset/AR/CS/old file-dialog export), the
        canvas + transform controls, and the big note-scrolling timeline with
        its own snap combo and playback row.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        sub_toolbar = QHBoxLayout()

        self.play_button = QPushButton(tr("MainWindow", "Play"))
        self.play_button.clicked.connect(self.toggle_playback)
        self.play_button.setFocusPolicy(Qt.NoFocus)

        self.reset_button = QPushButton(tr("MainWindow", "Reset applied transforms"))
        self.reset_button.clicked.connect(self.reset_applied)
        self.reset_button.setFocusPolicy(Qt.NoFocus)

        self.export_button = QPushButton(tr("MainWindow", "Export applied map"))
        self.export_button.clicked.connect(self.export_map)
        self.export_button.setFocusPolicy(Qt.NoFocus)

        for button in (
            self.play_button,
            self.reset_button,
            self.export_button,
        ):
            button.setEnabled(False)

        self.difficulty_combo = QComboBox()
        self.difficulty_combo.setMinimumWidth(260)
        self.difficulty_combo.setEnabled(False)
        self.difficulty_combo.currentIndexChanged.connect(self._difficulty_changed)
        sub_toolbar.addWidget(QLabel(tr("MainWindow", "Difficulty")))
        sub_toolbar.addWidget(self.difficulty_combo)

        sub_toolbar.addWidget(self.play_button)
        sub_toolbar.addWidget(self.reset_button)
        sub_toolbar.addWidget(self.export_button)
        self.approach_rate_control=DifficultyValueControl("AR",10.0,"Approach Rate: 0 is slowest, 10 is fastest. Export default is 10.00.")
        self.circle_size_control=DifficultyValueControl("CS",7.0,"Circle Size: 0 is biggest, 10 is smallest. Export default is 7.00.")
        sub_toolbar.addWidget(self.approach_rate_control)
        sub_toolbar.addWidget(self.circle_size_control)
        sub_toolbar.addStretch(1)
        layout.addLayout(sub_toolbar)

        # Transformation preview and its controls. The gameplay timeline is
        # added below this splitter so it spans the full page width.
        workspace_splitter = QSplitter(Qt.Horizontal)

        self.canvas = TransformCanvas()
        self.canvas.background_dropped.connect(self._background_dropped)
        self.canvas.drag_offset_requested.connect(self._canvas_dragged)
        workspace_splitter.addWidget(self.canvas)

        right = QWidget()
        self.transform_controls_panel = right
        right.setMinimumWidth(460)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)

        self.background_opacity_control = ParameterControl(
            {"key": "background_opacity", "label": "Background Opacity", "type": "int", "min": 0, "max": 100, "default": 55}
        )
        self.background_opacity_control.changed.connect(
            lambda: self.canvas.set_background_opacity(int(self.background_opacity_control.value()))
        )
        right_layout.addWidget(QLabel(tr("MainWindow", "Background Opacity")))
        right_layout.addWidget(self.background_opacity_control)

        right_layout.addWidget(QLabel(tr("MainWindow", "Transformation mode")))

        self.mode_combo = QComboBox()
        # Item data is the stable mode identifier. The display text is localized,
        # so mode logic reads currentData() and never currentText().
        self.mode_combo.addItem(tr("MainWindow", "All Notes"), "all")
        self.mode_combo.addItem(tr("MainWindow", "Split Don / Kat"), "split")
        self.mode_combo.currentIndexChanged.connect(
            self._rebuild_control_tabs
        )
        right_layout.addWidget(self.mode_combo)
        self.swap_don_kat_button = QPushButton(tr("MainWindow", "Swap Don ↔ Kat"))
        self.swap_don_kat_button.setVisible(False)
        self.swap_don_kat_button.setToolTip(tr("MainWindow", "Swap transformation, parameters, and position between Don and Kat"))
        self.swap_don_kat_button.clicked.connect(self._swap_don_kat_transformations)
        right_layout.addWidget(self.swap_don_kat_button)
        self.mode_combo.currentIndexChanged.connect(
            lambda _index: self.swap_don_kat_button.setVisible(self._is_split_mode())
        )

        self.control_tabs = QTabWidget()
        right_layout.addWidget(self.control_tabs, 1)

        self.apply_button = QPushButton(tr("MainWindow", "Transform selected notes"))
        self.apply_button.clicked.connect(self.apply_selection)
        self.apply_button.setFocusPolicy(Qt.NoFocus)
        self.apply_button.setEnabled(False)
        right_layout.addWidget(self.apply_button)
        self.apply_original_button=QPushButton(tr("MainWindow", "Apply all changes to original file"))
        self.apply_original_button.clicked.connect(self.apply_to_original_file)
        self.apply_original_button.setFocusPolicy(Qt.NoFocus); self.apply_original_button.setEnabled(False)
        right_layout.addWidget(self.apply_original_button)

        workspace_splitter.addWidget(right)
        workspace_splitter.setSizes([900, 460])
        layout.addWidget(workspace_splitter, 1)

        timeline_controls = QHBoxLayout()
        timeline_controls.addWidget(QLabel(tr("MainWindow", "Beat snap")))

        self.snap_combo = QComboBox()
        for divisor in SNAP_DIVISORS:
            self.snap_combo.addItem(f"1/{divisor}", divisor)
        self.snap_combo.setCurrentText("1/4")
        self.snap_combo.currentIndexChanged.connect(self._snap_changed)
        timeline_controls.addWidget(self.snap_combo)

        self.timeline_info = QLabel(self._timeline_info_text("--", "--", "1/4"))
        self.timeline_info.setStyleSheet("color: #aeb8c5; padding-left: 10px;")
        timeline_controls.addWidget(self.timeline_info, 1)
        layout.addLayout(timeline_controls)

        self.timeline = TimelineGameplay()
        self.timeline.selection_changed.connect(self._selection_changed)
        self.timeline.selection_finalized.connect(self._selection_finalized)
        self.timeline.seek_requested.connect(self.seek_audio)
        self.timeline.snap_changed_by_wheel.connect(self._snap_changed_by_wheel)
        self._chart_views.append(self.timeline)
        layout.addWidget(self.timeline, 0)
        # Time+percentage, the kiai/bookmark bar, and playback controls all
        # in one row -- matches the Editor page's own strip layout, per
        # owner feedback that this row previously had the timing bar split
        # out on its own and was missing the percentage the Editor strip has.
        timeline_row=QHBoxLayout();timeline_row.setSpacing(5)
        time_area=QWidget()
        time_area_layout=QHBoxLayout(time_area);time_area_layout.setContentsMargins(0,0,0,0);time_area_layout.addStretch()
        self.timeline_time=QLabel("00:00:000   0.0%");self.timeline_time.setAlignment(Qt.AlignCenter);self.timeline_time.setStyleSheet("font-size:18px;font-weight:700;");time_area_layout.addWidget(self.timeline_time)
        # Wide enough for the longest readout this label ever shows, measured
        # in its own font rather than assumed in pixels. Polished first, or the
        # measurement uses the inherited font instead of the 18px one above.
        self.timeline_time.ensurePolished()
        time_area.setMinimumWidth(self.timeline_time.fontMetrics().horizontalAdvance("00:00:000   100.0%") + 24)
        time_area_layout.addStretch();timeline_row.addWidget(time_area)

        # Same widget type as the Editor page's timing bar (TimingOverviewBar
        # can only live in one layout at a time, so this is a second
        # instance, kept in sync wherever self.timing_bar is updated -- see
        # self._timing_bars).
        self.fancy_timing_bar = TimingOverviewBar()
        self.fancy_timing_bar.seek_requested.connect(self.seek_audio)
        timeline_row.addWidget(self.fancy_timing_bar, 1)
        self._timing_bars.append(self.fancy_timing_bar)

        self.timeline_play_button=QPushButton("▶");self.timeline_play_button.clicked.connect(self.toggle_playback);timeline_row.addWidget(self.timeline_play_button)
        timeline_row.addWidget(QLabel(tr("MainWindow", "Playback Rate")))
        self.playback_speed_buttons=[]
        for label,rate in (("25%",.25),("50%",.5),("75%",.75),("100%",1.0)):
            button=QPushButton(label);button.setCheckable(True);button.setProperty("playbackRate",rate)
            button.clicked.connect(lambda checked=False,r=rate:self._change_playback_speed(r));self.playback_speed_buttons.append(button);timeline_row.addWidget(button)
        layout.addLayout(timeline_row)

        self.fancy_density = DensityOverview()
        self.fancy_density.seek_requested.connect(self.seek_audio)
        layout.addWidget(self.fancy_density)
        self._density_views.append(self.fancy_density)

        return page

    def _switch_page(self, index: int) -> None:
        current = self.page_stack.currentIndex()
        # Leaving the editor for the song list is the same door Esc uses, so
        # it asks the same question and tears down the same views.
        if index == PAGE_LIBRARY and current != PAGE_LIBRARY and not self._leave_editor():
            self._show_page(current)
            return
        self._show_page(index)

    def _show_page(self, index: int) -> None:
        """Switch page and keep the header tab that owns it checked."""
        self.page_stack.setCurrentIndex(index)
        button = self.page_button_group.button(index)
        if button is not None:
            button.setChecked(True)
        if index == PAGE_LIBRARY and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.toggle_playback()

    def keyPressEvent(self, event) -> None:
        """Esc anywhere outside the song list goes back to the song list.

        It only arrives here when the focused view did not want it: both
        editor views consume Esc to clear their own selection first, and pass
        it up untouched when they have nothing selected.
        """
        back = QKeySequence(self.shortcuts.sequence("back_to_songs"))
        if (
            self.page_stack.currentIndex() != PAGE_LIBRARY
            and not back.isEmpty()
            and QKeySequence(event.keyCombination()) == back
        ):
            self._back_to_library()
            event.accept()
            return
        super().keyPressEvent(event)

    def _back_to_library(self) -> None:
        if self._leave_editor():
            self._show_page(PAGE_LIBRARY)

    def _leave_editor(self) -> bool:
        """Confirm, then tear the open views down. False means "stay"."""
        if not self._confirm_leaving_editor():
            return False
        self._close_all_editor_views()
        return True

    def _close_all_editor_views(self) -> None:
        """Going back to the song list ends the session's views.

        Coming back to a difficulty then reopens the default chart + SV pair
        (_activate_state keys that off _editor_view_groups), so the editor
        starts clean instead of accumulating every view of every song visited
        this session.
        """
        for frame in list(self._editor_views):
            self._close_editor_view(frame)

    def _confirm_leaving_editor(self) -> bool:
        """Ask about unsaved difficulties. False means "stay where you are"."""
        dirty = [state for state in self._states.values() if state.history.dirty]
        if not dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("MainWindow", "Unsaved changes"))
        box.setText(tr("MainWindow", "Save your changes first?"))
        box.setInformativeText(
            tr("MainWindow", "These difficulties have edits that are not written to disk:")
            + "\n\n"
            + "\n".join(f"• {state.source_path.name}" for state in dirty)
        )
        save_button = box.addButton(tr("MainWindow", "Save"), QMessageBox.AcceptRole)
        leave_button = box.addButton(tr("MainWindow", "Continue without saving"), QMessageBox.DestructiveRole)
        box.addButton(tr("MainWindow", "Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            self.save_all_states()
            return True
        return clicked is leave_button

    def closeEvent(self, event) -> None:
        # Same guard as Esc: quitting is the other way to walk away from
        # unwritten edits, and it is the one that cannot be undone.
        #
        # spontaneous() gates it: True only when the close came from the
        # window manager (the X button, Alt+F4) -- someone quitting. A
        # programmatic close() is code tidying up, including every test's
        # teardown, and a modal prompt there has nobody to answer it.
        if not event.spontaneous() or self._confirm_leaving_editor():
            event.accept()
        else:
            event.ignore()

    # -- Song library page --------------------------------------------------

    def _build_library_page(self) -> QWidget:
        """Browse every taiko chart under the osu! Songs folder: song, then difficulty."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        top = QHBoxLayout()
        heading = QLabel(tr("MainWindow", "Taiko songs"))
        heading.setStyleSheet("font-size: 17px; font-weight: 700;")
        top.addWidget(heading)

        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText(
            tr("MainWindow", "Search artist, title, difficulty, mapper or tags")
        )
        self.library_search.setClearButtonEnabled(True)
        self.library_search.setMinimumWidth(240)
        self.library_search.textChanged.connect(lambda _text: self._rebuild_song_list())
        top.addWidget(self.library_search, 1)

        self.library_folder_label = ElidedLabel("")
        self.library_folder_label.setMaximumWidth(360)
        self.library_folder_label.setStyleSheet("color: #97a3b4;")
        top.addWidget(self.library_folder_label)

        change_folder_button = QPushButton(tr("MainWindow", "Change folder"))
        change_folder_button.setFocusPolicy(Qt.NoFocus)
        change_folder_button.clicked.connect(self._choose_songs_folder)
        top.addWidget(change_folder_button)

        rescan_button = QPushButton(tr("MainWindow", "Rescan"))
        rescan_button.setToolTip(tr("MainWindow", "Look for songs added or changed since the last scan"))
        rescan_button.setFocusPolicy(Qt.NoFocus)
        rescan_button.clicked.connect(self._start_scan)
        top.addWidget(rescan_button)
        layout.addLayout(top)

        # Second row: how the list is organised. All three re-sort in place,
        # with no rescan -- they only change how the same index is displayed.
        arrange = QHBoxLayout()
        arrange.addWidget(QLabel(tr("MainWindow", "Group by")))
        self.library_group_combo = QComboBox()
        self.library_group_combo.addItem(tr("MainWindow", "Nothing"), "none")
        self.library_group_combo.addItem(tr("MainWindow", "Mapper"), "mapper")
        self.library_group_combo.addItem(tr("MainWindow", "Artist"), "artist")
        self.library_group_combo.currentIndexChanged.connect(lambda _index: self._rebuild_song_list())
        arrange.addWidget(self.library_group_combo)

        arrange.addWidget(QLabel(tr("MainWindow", "Sort")))
        self.library_sort_combo = QComboBox()
        self.library_sort_combo.addItem(tr("MainWindow", "A to Z"), "az")
        self.library_sort_combo.addItem(tr("MainWindow", "Z to A"), "za")
        self.library_sort_combo.currentIndexChanged.connect(lambda _index: self._rebuild_song_list())
        arrange.addWidget(self.library_sort_combo)

        self.original_metadata_check = QCheckBox(tr("MainWindow", "Original language metadata"))
        self.original_metadata_check.setToolTip(
            tr("MainWindow", "Show titles and artists in the song's own script instead of the romanized fields")
        )
        self.original_metadata_check.setChecked(self.settings.bool_value("library/original_metadata", False))
        self.original_metadata_check.toggled.connect(self._original_metadata_toggled)
        arrange.addWidget(self.original_metadata_check)
        arrange.addStretch(1)
        layout.addLayout(arrange)

        split = QSplitter(Qt.Horizontal)

        self.song_list = QListWidget()
        self.song_list.currentRowChanged.connect(self._song_selected)
        self.song_list.itemActivated.connect(lambda _item: self.difficulty_list.setFocus())
        split.addWidget(self.song_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(QLabel(tr("MainWindow", "Difficulties")))
        self.difficulty_list = QListWidget()
        self.difficulty_list.itemActivated.connect(lambda _item: self._open_selected_difficulty())
        right_layout.addWidget(self.difficulty_list, 1)
        self.open_difficulty_button = QPushButton(tr("MainWindow", "Edit this difficulty"))
        self.open_difficulty_button.clicked.connect(self._open_selected_difficulty)
        right_layout.addWidget(self.open_difficulty_button)
        split.addWidget(right)
        split.setSizes([820, 420])
        layout.addWidget(split, 1)

        footer = QHBoxLayout()
        self.library_status = QLabel(tr("MainWindow", "No songs folder selected yet."))
        footer.addWidget(self.library_status, 1)
        self.scan_progress = QProgressBar()
        # Indeterminate: the walk discovers files as it goes, so there is no
        # honest total to count towards until it has already finished.
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setFixedWidth(160)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setVisible(False)
        footer.addWidget(self.scan_progress)
        layout.addLayout(footer)

        page.setStyleSheet(
            """
            QListWidget { background: #222a36; border: 1px solid #303947; border-radius: 6px; padding: 4px; }
            QListWidget::item { padding: 7px 8px; border-radius: 4px; }
            QListWidget::item:selected { background: #f3a6bd; color: #17191f; }
            QLineEdit { background: #252d39; border: 1px solid #3a4554; border-radius: 5px; padding: 6px; }
            QProgressBar { background: #252d39; border: 1px solid #3a4554; border-radius: 5px; }
            QProgressBar::chunk { background: #f3a6bd; }
            """
        )
        return page

    # -- Song library: folder, scan, cache ----------------------------------

    def _library_cache_path(self) -> Path:
        """Where the song index lives: <AppData>/jimmyreturnz/TaikoFancyArranger.

        main() sets the organization and application names, without which
        AppDataLocation is a folder shared with every other app run by the
        same Python.
        """
        root = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        return Path(root or str(Path.home())) / "song_index.json"

    def start_library(self) -> None:
        """First thing after the window is shown: get a folder, then scan it.

        Called from main() rather than __init__ so constructing a MainWindow
        (tests, and the Fancy Arranger's own file-dialog flow) never triggers a
        multi-thousand-file walk of someone's disk.
        """
        folder = self.settings.string_value("library/songs_folder", "")
        if folder and Path(folder).is_dir() and self.settings.bool_value("setup/completed", False):
            self._start_scan()
            return
        # First start: say what the folder picker about to open is for, in the
        # language just chosen, before dropping someone into a bare dialog.
        QMessageBox.information(
            self,
            tr("MainWindow", "Select your osu! Songs folder"),
            tr(
                "MainWindow",
                "Please select your osu! Songs folder.\n\n"
                "Every beatmap in it is checked once for taiko difficulties, and the result is "
                "remembered, so this only takes a while the first time.",
            ),
        )
        self._choose_songs_folder()

    def _default_songs_folder(self) -> str:
        candidate = Path(
            QStandardPaths.writableLocation(QStandardPaths.HomeLocation) or str(Path.home())
        ) / "AppData" / "Local" / "osu!" / "Songs"
        return str(candidate) if candidate.is_dir() else ""

    def _choose_songs_folder(self) -> None:
        current = self.settings.string_value("library/songs_folder", "") or self._default_songs_folder()
        folder = QFileDialog.getExistingDirectory(
            self, tr("MainWindow", "Select your osu! Songs folder"), current
        )
        if not folder:
            return
        self.settings.set_value("library/songs_folder", folder)
        # Only now is first-time setup really done; cancelling the picker
        # leaves the flag unset so the next launch offers it again.
        self.settings.set_value("setup/completed", True)
        self.settings.sync()
        self._start_scan()

    def _start_scan(self) -> None:
        folder = self.settings.string_value("library/songs_folder", "")
        self.library_folder_label.setText(folder)
        if not folder or not Path(folder).is_dir():
            self.library_status.setText(tr("MainWindow", "No songs folder selected yet."))
            return
        self.scan_timer.stop()
        self._library_cache = load_cache(self._library_cache_path())
        # Show the saved index first, then verify it: on every start after the
        # first, the list is complete before a single file has been reopened.
        self._library_songs = group_by_song(songs_from_cache(self._library_cache, Path(folder)))
        self._listed_paths = {
            difficulty.path for group in self._library_songs.values() for difficulty in group
        }
        self._rebuild_song_list()
        # The walk fills a separate dict that becomes the authoritative one at
        # the end, so difficulties deleted since the last run disappear -- while
        # anything genuinely new is added to the visible list as it is found.
        self._scan_songs = {}
        self._scan_files = 0
        self._scan_since_refresh = 0
        self._scan_iterator = scan(Path(folder), self._library_cache)
        self.scan_progress.setVisible(True)
        self._update_scan_status(
            tr("MainWindow", "{songs} taiko songs from the saved index. Checking for new maps...")
            if self._library_songs
            else tr("MainWindow", "Scanning {files} files, {songs} taiko songs found")
        )
        self.scan_timer.start()

    def _scan_step(self) -> None:
        """Consume one frame's worth of the scan, then hand the UI back."""
        deadline = perf_counter() + SCAN_SLICE_SECONDS
        while perf_counter() < deadline:
            try:
                found = next(self._scan_iterator)
            except StopIteration:
                self._finish_scan()
                return
            self._scan_files += 1
            if found is not None:
                self._scan_songs.setdefault(found.folder, []).append(found)
                if found.path not in self._listed_paths:
                    self._listed_paths.add(found.path)
                    self._library_songs.setdefault(found.folder, []).append(found)
                    self._scan_since_refresh += 1
        # Redrawing a list of thousands of rows costs far more than the scan
        # slice itself, so it happens on found songs, not on every tick.
        if self._scan_since_refresh >= 200:
            self._scan_since_refresh = 0
            self._rebuild_song_list()
        self._update_scan_status(tr("MainWindow", "Scanning {files} files, {songs} taiko songs found"))

    def _finish_scan(self) -> None:
        self.scan_timer.stop()
        self._scan_iterator = None
        self.scan_progress.setVisible(False)
        # What the walk actually found wins: this is where songs deleted since
        # the last run leave the list.
        self._library_songs = self._scan_songs
        try:
            save_cache(self._library_cache_path(), self._library_cache)
        except OSError:
            pass  # A browsable list now matters more than a fast start next time.
        self._rebuild_song_list()
        self._update_scan_status(tr("MainWindow", "{songs} taiko songs, {files} files scanned"))

    def _update_scan_status(self, template: str) -> None:
        self.library_status.setText(
            template.format(files=self._scan_files, songs=len(self._library_songs))
        )

    # -- Song library: the two lists ----------------------------------------

    def _original_metadata_toggled(self, checked: bool) -> None:
        self.settings.set_value("library/original_metadata", checked)
        self.settings.sync()
        self._rebuild_song_list()

    def _song_entries(self) -> list[tuple[str, str, str, Path]]:
        """(group, sort label, row text, folder) for the songs the filters keep.

        Sorted by group first so the list can be walked once and broken into
        headers; Z-to-A reverses the groups too, which is what "sort by
        artist, Z to A" has to mean once songs are grouped by artist.
        """
        original = self.original_metadata_check.isChecked()
        grouping = str(self.library_group_combo.currentData())
        needle = self.library_search.text().strip().lower()
        entries: list[tuple[str, str, str, Path]] = []
        for folder, difficulties in self._library_songs.items():
            if needle and not any(needle in difficulty.search_text() for difficulty in difficulties):
                continue
            first = difficulties[0]
            label = first.song_label(original)
            parts = [label]
            if first.creator:
                parts.append(first.creator)
            parts.append(str(len(difficulties)))
            group = ""
            if grouping == "mapper":
                group = first.creator or tr("MainWindow", "Unknown mapper")
            elif grouping == "artist":
                group = first.display_artist(original) or tr("MainWindow", "Unknown artist")
            entries.append((group, label, "   ·   ".join(parts), folder))
        entries.sort(key=lambda entry: (entry[0].lower(), entry[1].lower()))
        if str(self.library_sort_combo.currentData()) == "za":
            entries.reverse()
        return entries

    def _rebuild_song_list(self) -> None:
        previous = self.song_list.currentItem()
        previous_folder = previous.data(Qt.UserRole) if previous else None
        self.song_list.blockSignals(True)
        self.song_list.clear()
        current_group = None
        for group, _label, text, folder in self._song_entries():
            if group and group != current_group:
                current_group = group
                header = QListWidgetItem(group)
                # Enabled but not selectable: a divider you cannot land on,
                # while still painting in the enabled palette -- NoItemFlags
                # would grey the text out and lose the accent colour.
                header.setFlags(Qt.ItemIsEnabled)
                font = header.font()
                font.setBold(True)
                header.setFont(font)
                header.setForeground(QColor(ACCENT_PINK))
                self.song_list.addItem(header)
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, str(folder))
            item.setToolTip(str(folder))
            self.song_list.addItem(item)
        self.song_list.blockSignals(False)
        for row in range(self.song_list.count()):
            if self.song_list.item(row).data(Qt.UserRole) == previous_folder:
                self.song_list.setCurrentRow(row)
                return
        self.difficulty_list.clear()

    def _song_selected(self, row: int) -> None:
        self.difficulty_list.clear()
        item = self.song_list.item(row)
        if item is None or item.data(Qt.UserRole) is None:
            return  # a group header
        difficulties = self._library_songs.get(Path(item.data(Qt.UserRole)), [])
        for difficulty in sorted(difficulties, key=lambda entry: entry.version.lower()):
            entry_item = QListWidgetItem(difficulty.version or difficulty.path.stem)
            entry_item.setData(Qt.UserRole, str(difficulty.path))
            entry_item.setToolTip(difficulty.path.name)
            self.difficulty_list.addItem(entry_item)
        if self.difficulty_list.count():
            self.difficulty_list.setCurrentRow(0)

    def _open_selected_difficulty(self) -> None:
        item = self.difficulty_list.currentItem()
        if item is None:
            return
        # _load_map_path reports its own failure, and moves to the Editor page
        # itself once the difficulty really loaded.
        self._load_map_path(Path(item.data(Qt.UserRole)).resolve(), refresh_difficulties=True)

    # -- Editor page: page switcher + multi-difficulty view stacking --------

    def _build_editor_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Editor page's own viewing-controls strip, left to right: beat-snap
        # (drives every open chart view together, not just one), merged
        # time+percentage, the timing bar (kiai/bookmarks/SV/BPM -- relocated
        # here from the old shared player deck, not rebuilt), play + speed,
        # then "open new view".
        strip = QHBoxLayout()
        strip.setSpacing(6)

        self.editor_snap_combo = QComboBox()
        for divisor in SNAP_DIVISORS:
            self.editor_snap_combo.addItem(f"1/{divisor}", divisor)
        self.editor_snap_combo.setCurrentText("1/4")
        self.editor_snap_combo.currentIndexChanged.connect(self._editor_snap_changed)
        strip.addWidget(self.editor_snap_combo)

        self.editor_timeline_strip = QLabel()
        strip.addWidget(self.editor_timeline_strip)

        self.timing_bar = TimingOverviewBar()
        self.timing_bar.seek_requested.connect(self.seek_audio)
        strip.addWidget(self.timing_bar, 1)
        self._timing_bars.append(self.timing_bar)

        self.editor_play_button = QPushButton("▶")
        self.editor_play_button.setFocusPolicy(Qt.NoFocus)
        self.editor_play_button.clicked.connect(self.toggle_playback)
        strip.addWidget(self.editor_play_button)

        self.editor_playback_speed_buttons = []
        for label, rate in (("25%", .25), ("50%", .5), ("75%", .75), ("100%", 1.0)):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("playbackRate", rate)
            button.clicked.connect(lambda checked=False, r=rate: self._change_playback_speed(r))
            self.editor_playback_speed_buttons.append(button)
            strip.addWidget(button)
        # Widths for this row are equalized in showEvent (see there): derived
        # from the polished labels instead of hard-coded pixels, which stopped
        # fitting the moment the UI font grew.

        add_view_button = QPushButton("+")
        add_view_button.setToolTip(tr("MainWindow", "open new view"))
        add_view_button.setFocusPolicy(Qt.NoFocus)
        add_view_button.clicked.connect(self._open_add_view_dialog)
        strip.addWidget(add_view_button)

        layout.addLayout(strip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        views_container = QWidget()
        self.editor_views_layout = QVBoxLayout(views_container)
        self.editor_views_layout.setContentsMargins(4, 4, 4, 4)
        self.editor_views_layout.setSpacing(10)
        self.editor_views_layout.addStretch(1)
        scroll.setWidget(views_container)
        layout.addWidget(scroll, 1)

        # Global, not per-view: applies to whichever view has focus. Only
        # one of these two rows is visible at a time (chart vs SV).
        layout.addWidget(self._build_global_tool_row())
        layout.addWidget(self._build_global_sv_tool_row())

        # Difficulty (source path) -> that group's QVBoxLayout. Views append
        # to the bottom of their difficulty's group and are never movable;
        # new difficulties get a new group appended at the bottom of the stack.
        self._editor_view_groups: dict[Path, QVBoxLayout] = {}
        self._editor_views: list[EditorViewFrame] = []
        return page

    def _editor_snap_changed(self) -> None:
        divisor = int(self.editor_snap_combo.currentData())
        for view in self._editor_snap_views:
            view.set_snap_divisor(divisor)

    def _editor_change_snap_from_wheel(self, delta: int) -> None:
        """Alt+wheel on the Editor page: every open chart view moves together."""
        if not delta:
            return
        values = SNAP_DIVISORS
        current = int(self.editor_snap_combo.currentData())
        try:
            index = values.index(current)
        except ValueError:
            index = values.index(4)
        direction = 1 if delta > 0 else -1
        new_index = max(0, min(len(values) - 1, index + direction))
        new_divisor = values[new_index]
        if new_divisor == current:
            return
        found = self.editor_snap_combo.findData(new_divisor)
        if found >= 0:
            self.editor_snap_combo.blockSignals(True)
            self.editor_snap_combo.setCurrentIndex(found)
            self.editor_snap_combo.blockSignals(False)
        for view in self._editor_snap_views:
            view.set_snap_divisor(new_divisor)

    # -- per-difficulty zoom ---------------------------------------------------

    DEFAULT_EDITOR_WINDOW_MS = 2000.0

    def _difficulty_window_ms(self, difficulty_path: Path) -> float:
        """The time span every view of this difficulty shows.

        A new view adopts whatever zoom that difficulty is already at, so
        opening an SV editor next to a zoomed-in chart doesn't put the two on
        different scales.
        """
        return self._difficulty_zoom.setdefault(difficulty_path, self.DEFAULT_EDITOR_WINDOW_MS)

    def _editor_views_for(self, difficulty_path: Path):
        """Every chart/SV view currently open for one difficulty."""
        for frame in self._editor_views:
            if getattr(frame, "difficulty_path", None) != difficulty_path:
                continue
            for attribute in ("chart_view", "sv_view"):
                view = getattr(frame, attribute, None)
                if view is not None:
                    yield view

    def _zoom_changed(self, difficulty_path: Path, window_ms: float, origin=None) -> None:
        """Ctrl+wheel on one view zooms every view of the same difficulty.

        Zoom is difficulty-level rather than view-level because the SV editor
        has to stay aligned with the chart it annotates -- if one is showing
        2 seconds and the other 4, the same x position means two different
        times and following the playhead together stops meaning anything.
        """
        self._difficulty_zoom[difficulty_path] = window_ms
        for view in self._editor_views_for(difficulty_path):
            if view is origin or abs(view.window_ms - window_ms) < 0.01:
                continue
            view.window_ms = window_ms
            view.update()

    def _difficulty_group_layout(self, source_path: Path, label: str) -> QVBoxLayout:
        existing = self._editor_view_groups.get(source_path)
        if existing is not None:
            return existing

        # No group header label: the difficulty name is shown once per view, at
        # the right of each EditorViewFrame's chrome row. A header here as well
        # is what made the name appear on both the left and the right of every
        # difficulty's views. `label` is still taken so callers keep one
        # signature whether or not the group already exists.
        del label
        group = QWidget()
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(6)

        # Insert before the trailing stretch that keeps groups pinned to the top.
        self.editor_views_layout.insertWidget(self.editor_views_layout.count() - 1, group)
        self._editor_view_groups[source_path] = group_layout
        return group_layout

    def _open_add_view_dialog(self) -> None:
        if not self.song_difficulties:
            QMessageBox.information(
                self,
                tr("MainWindow", "Open a map first"),
                tr("MainWindow", "Open a beatmap before adding a view."),
            )
            return
        entries = [
            (self.difficulty_combo.itemText(i), Path(self.difficulty_combo.itemData(i)))
            for i in range(self.difficulty_combo.count())
        ]
        current = self.difficulty_combo.currentData()
        dialog = AddViewDialog(entries, self, Path(current) if current else None)
        if dialog.exec() != QDialog.Accepted:
            return
        self._add_editor_view(dialog.selected_view_type(), dialog.selected_difficulty_path())

    def _add_editor_view(self, view_type: str, difficulty_path: Path) -> None:
        try:
            state = self._ensure_state(difficulty_path)
        except Exception as error:
            QMessageBox.critical(self, tr("MainWindow", "Open failed"), str(error))
            return

        label = state.document.version or difficulty_path.stem
        frame = EditorViewFrame(view_type, label)
        frame.difficulty_path = difficulty_path
        frame.closed.connect(self._close_editor_view)

        if view_type == "chart":
            view = TimelineGameplay()
            view.set_symmetric(True)
            view.load_document(state.document)
            view.set_snap_divisor(int(self.editor_snap_combo.currentData()))
            view.current_time = state.playhead_ms
            view.window_ms = self._difficulty_window_ms(difficulty_path)
            view.zoom_changed.connect(
                lambda window_ms, dp=difficulty_path, v=view: self._zoom_changed(dp, window_ms, v)
            )
            # Scrubbing any chart view moves the one shared playhead, so every
            # open view -- this one, the player deck, and any others -- stays
            # in sync, matching "chart viewing" rather than independent tabs.
            view.seek_requested.connect(self.seek_audio)
            view.note_place_requested.connect(
                lambda note_kind, time_ms, new_combo, big, dp=difficulty_path: self._place_note(dp, note_kind, time_ms, new_combo, big)
            )
            view.note_place_with_duration_requested.connect(
                lambda note_kind, start_ms, end_ms, new_combo, big, dp=difficulty_path:
                    self._place_note_with_duration(dp, note_kind, start_ms, end_ms, new_combo, big)
            )
            view.note_duration_edit_requested.connect(
                lambda uid, new_end_ms, dp=difficulty_path: self._edit_note_duration(dp, uid, new_end_ms)
            )
            view.note_delete_requested.connect(
                lambda uid, dp=difficulty_path: self._delete_note(dp, uid)
            )
            view.notes_delete_requested.connect(
                lambda uids, dp=difficulty_path: self._delete_notes(dp, uids)
            )
            frame.set_content(view)
            frame.chart_view = view
            self._chart_views.append(view)
            self._editor_snap_views.append(view)
            # Locking/unlocking the currently-active view must re-enable or
            # disable the global tool row, since it can't watch every
            # frame's lock state itself.
            frame.lock_button.toggled.connect(
                lambda _checked, v=view: self._sync_global_tool_row() if v is self._active_chart_view else None
            )
            # Newly-added view becomes the global tool row's target. Set
            # directly rather than relying solely on setFocus() -> Qt's
            # focusChanged: that requires a truly active top-level window,
            # which doesn't hold under every platform plugin (offscreen
            # tests included), so this guarantees the invariant either way.
            # focusChanged still applies when a user later clicks into a
            # different already-open view.
            view.setFocus(Qt.FocusReason.OtherFocusReason)
            self._active_chart_view = view
            self._last_focused_editor_view = view
            self._sync_global_tool_row()
            self.global_tool_row.setVisible(True)
            self.global_sv_tool_row.setVisible(False)
        elif view_type == "sv":
            view = SVEditorView()
            view.load_document(state.document)
            view.set_snap_divisor(int(self.editor_snap_combo.currentData()))
            view.current_time = state.playhead_ms
            view.window_ms = self._difficulty_window_ms(difficulty_path)
            view.zoom_changed.connect(
                lambda window_ms, dp=difficulty_path, v=view: self._zoom_changed(dp, window_ms, v)
            )
            view.seek_requested.connect(self.seek_audio)
            view.function_range_requested.connect(
                lambda start_ms, end_ms, dp=difficulty_path: self._open_sv_function_dialog(dp, start_ms, end_ms)
            )
            view.point_add_requested.connect(
                lambda time_ms, sv, dp=difficulty_path: self._add_sv_point(dp, time_ms, sv)
            )
            view.point_time_edit_requested.connect(
                lambda uid, time_ms, dp=difficulty_path: self._edit_sv_point_time(dp, uid, time_ms)
            )
            view.point_sv_edit_requested.connect(
                lambda uid, sv, dp=difficulty_path: self._edit_sv_point(dp, uid, sv)
            )
            view.point_delete_requested.connect(
                lambda uid, dp=difficulty_path: self._delete_sv_point(dp, uid)
            )
            view.points_delete_requested.connect(
                lambda uids, dp=difficulty_path: self._delete_sv_points(dp, uids)
            )
            frame.set_content(view)
            frame.sv_view = view
            self._sv_views.append(view)
            self._editor_snap_views.append(view)
            frame.lock_button.toggled.connect(
                lambda _checked, v=view: self._sync_global_sv_tool_row() if v is self._active_sv_view else None
            )
            view.setFocus(Qt.FocusReason.OtherFocusReason)
            self._active_sv_view = view
            self._last_focused_editor_view = view
            self._sync_global_sv_tool_row()
            self.global_tool_row.setVisible(False)
            self.global_sv_tool_row.setVisible(True)
        elif view_type == "gameplay":
            view = GameplayViewerView()
            view.load_document(state.document)
            view.set_snap_divisor(int(self.editor_snap_combo.currentData()))
            view.current_time = state.playhead_ms
            view.seek_requested.connect(self.seek_audio)
            frame.set_content(view)
            frame.gameplay_view = view
            self._gameplay_views.append(view)
            self._editor_snap_views.append(view)
        elif view_type == "density":
            view = DensityOverview()
            view.load_document(state.document, state.duration_hint)
            view.seek_requested.connect(self.seek_audio)
            frame.set_content(view)
            frame.density_view = view
            self._density_views.append(view)
        else:
            # Literal tr() calls, one per type -- see the note in
            # AddViewDialog about why a dict lookup here would silently
            # escape the i18n coverage gate.
            placeholder_text = {
                "gimmick": tr("MainWindow", "Gimmick tools arrive in a later milestone."),
            }[view_type]
            placeholder = QLabel(placeholder_text)
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color:#7d8794;padding:24px;border:0;")
            frame.set_content(placeholder)

        self._editor_views.append(frame)
        group_layout = self._difficulty_group_layout(difficulty_path, label)
        self._insert_into_group(group_layout, frame)

    def _insert_into_group(self, group_layout: QVBoxLayout, frame: EditorViewFrame) -> None:
        """Place `frame` in its difficulty group, keeping density bottommost.

        Density stays last in its group regardless of add order: a density
        view added first still ends up below a chart view added afterward.
        """
        if frame.view_type == "density":
            group_layout.addWidget(frame)
            return
        insert_index = group_layout.count()
        for i in range(group_layout.count()):
            widget = group_layout.itemAt(i).widget()
            if isinstance(widget, EditorViewFrame) and widget.view_type == "density":
                insert_index = i
                break
        group_layout.insertWidget(insert_index, frame)

    def _build_global_tool_row(self) -> QWidget:
        """M3 note-editing tools: 1 select, 2 don, 3 kat, 4 slider, 5 spinner,
        6 new combo. Global to the program (not per-view, per user feedback
        after trying the per-frame version): applies to whichever chart view
        last had keyboard focus, tracked in self._active_chart_view via
        QApplication.focusChanged. Also driven by the number keys themselves
        (see _build_tool_shortcuts) since these are meant to be pressed, not
        just clicked.
        """
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(QLabel(tr("MainWindow", "Note tools:")))

        self.tool_buttons: dict[str, QPushButton] = {}
        tool_group = QButtonGroup(row)
        tool_group.setExclusive(True)
        tools = (
            ("1", "select", tr("MainWindow", "1. Select")),
            ("2", "don", tr("MainWindow", "2. Don")),
            ("3", "kat", tr("MainWindow", "3. Kat")),
            # Labels only -- "slider"/"spinner" have been the stable internal
            # tool ids since M3 and are what the .osu type bits mean.
            ("4", "slider", tr("MainWindow", "4. Slider")),
            ("5", "spinner", tr("MainWindow", "5. Spinner")),
        )
        for number, tool_id, label in tools:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setFixedHeight(32)
            button.setFocusPolicy(Qt.NoFocus)
            button.setChecked(tool_id == "select")
            button.toggled.connect(lambda checked, t=tool_id: self._set_active_tool(t) if checked else None)
            tool_group.addButton(button)
            self.tool_buttons[tool_id] = button
            layout.addWidget(button)

        self.new_combo_button = QPushButton(tr("MainWindow", "6. New Combo"))
        self.new_combo_button.setCheckable(True)
        self.new_combo_button.setFixedHeight(32)
        self.new_combo_button.setFocusPolicy(Qt.NoFocus)
        self.new_combo_button.toggled.connect(self._set_active_new_combo)
        layout.addWidget(self.new_combo_button)

        layout.addStretch(1)
        self.global_tool_row = row
        self.global_tool_row.setEnabled(False)
        return row

    def _build_global_sv_tool_row(self) -> QWidget:
        """M4 SV-editing tools: 1 select, 2 green line, 3 function. Global,
        same pattern and rationale as the note tool row above; applies to
        whichever SV view last had focus (self._active_sv_view). Only one of
        this row and the note tool row is visible at a time, switched by
        _editor_view_focus_changed based on which view type gained focus.
        """
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(QLabel(tr("MainWindow", "SV tools:")))

        self.sv_tool_buttons: dict[str, QPushButton] = {}
        tool_group = QButtonGroup(row)
        tool_group.setExclusive(True)
        tools = (
            ("1", "select", tr("MainWindow", "1. Select")),
            ("2", "green_line", tr("MainWindow", "2. Green Line")),
            ("3", "function", tr("MainWindow", "3. Function")),
        )
        for number, tool_id, label in tools:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setFixedHeight(32)
            button.setFocusPolicy(Qt.NoFocus)
            button.setChecked(tool_id == "select")
            button.toggled.connect(lambda checked, t=tool_id: self._set_active_sv_tool(t) if checked else None)
            tool_group.addButton(button)
            self.sv_tool_buttons[tool_id] = button
            layout.addWidget(button)

        layout.addStretch(1)
        self.global_sv_tool_row = row
        self.global_sv_tool_row.setEnabled(False)
        self.global_sv_tool_row.setVisible(False)
        return row

    def _build_tool_shortcuts(self) -> None:
        """Number keys 1-6 as global tool shortcuts, not just clickable buttons.

        ApplicationShortcut context fires regardless of which widget has
        focus -- including self.timeline, the shared Fancy Arranger
        timeline -- but the handler only ever calls set_tool on
        self._active_chart_view / self._active_sv_view, both of which are
        exclusively Editor-page views (see _editor_view_focus_changed), so
        this can't resurrect the contamination risk a per-widget
        keyPressEvent override would have. Routes to whichever of the two
        tool rows is currently visible, since both use keys 1-3 for
        different tools and only one is ever relevant at a time.

        The bound key comes from the shortcut registry (`tool_1` ... `tool_6`)
        so Settings can rebind it; the digit passed to the handler stays the
        logical tool number regardless of which key ends up on it.
        """
        self.tool_shortcuts: dict[str, QShortcut] = {}
        for digit in "123456":
            action_id = f"tool_{digit}"
            shortcut = QShortcut(QKeySequence(self.shortcuts.sequence(action_id)), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(lambda d=digit: self._activate_tool_digit(d))
            self.tool_shortcuts[action_id] = shortcut

    def _set_tool_shortcuts_enabled(self, enabled: bool) -> None:
        # getattr: focusChanged is connected before the shortcuts are built.
        for shortcut in getattr(self, "tool_shortcuts", {}).values():
            shortcut.setEnabled(enabled)

    def _activate_tool_digit(self, digit: str) -> None:
        if should_ignore_shortcut_focus(QApplication.focusWidget()):
            return
        note_tools = {"1": "select", "2": "don", "3": "kat", "4": "slider", "5": "spinner"}
        sv_tools = {"1": "select", "2": "green_line", "3": "function"}
        if self.global_tool_row.isVisible():
            if digit in note_tools:
                self.tool_buttons[note_tools[digit]].setChecked(True)
            elif digit == "6":
                self.new_combo_button.toggle()
        elif self.global_sv_tool_row.isVisible() and digit in sv_tools:
            self.sv_tool_buttons[sv_tools[digit]].setChecked(True)

    def _set_active_tool(self, tool_id: str) -> None:
        if self._active_chart_view is not None:
            self._active_chart_view.set_tool(tool_id)

    def _set_active_new_combo(self, value: bool) -> None:
        if self._active_chart_view is not None:
            self._active_chart_view.set_new_combo(value)

    def _editor_view_focus_changed(self, _old, new) -> None:
        # An ApplicationShortcut *consumes* its key, so the plain digits 1-6
        # would eat characters typed into the library's search box or any spin
        # box -- should_ignore_shortcut_focus can decline to act, but cannot
        # hand the key back. Disabling the shortcuts outright while a text
        # widget has focus is the only way the key reaches it.
        self._set_tool_shortcuts_enabled(not should_ignore_shortcut_focus(new))

        # Only Editor-page views (chart: symmetric; any SVEditorView) can
        # become a tool target; self.timeline (the shared Fancy Arranger
        # timeline) never does, so clicking it can't silently steal either
        # global tool row. Whichever type gains focus shows its own row and
        # hides the other, since only one is ever relevant at a time.
        if isinstance(new, TimelineGameplay) and new.symmetric:
            self._active_chart_view = new
            self._last_focused_editor_view = new
            self._sync_global_tool_row()
            self.global_tool_row.setVisible(True)
            self.global_sv_tool_row.setVisible(False)
        elif isinstance(new, SVEditorView):
            self._active_sv_view = new
            self._last_focused_editor_view = new
            self._sync_global_sv_tool_row()
            self.global_tool_row.setVisible(False)
            self.global_sv_tool_row.setVisible(True)

    def _sync_global_tool_row(self) -> None:
        view = self._active_chart_view
        enabled = view is not None and view.isEnabled()
        self.global_tool_row.setEnabled(enabled)
        if view is None:
            return
        button = self.tool_buttons.get(view.tool)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)
        self.new_combo_button.blockSignals(True)
        self.new_combo_button.setChecked(view.new_combo)
        self.new_combo_button.blockSignals(False)

    def _set_active_sv_tool(self, tool_id: str) -> None:
        if self._active_sv_view is not None:
            self._active_sv_view.set_tool(tool_id)

    def _sync_global_sv_tool_row(self) -> None:
        view = self._active_sv_view
        enabled = view is not None and view.isEnabled()
        self.global_sv_tool_row.setEnabled(enabled)
        if view is None:
            return
        button = self.sv_tool_buttons.get(view.tool)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)

    def _difficulty_for_view(self, view) -> Path | None:
        """Which difficulty an Editor-page chart/SV view belongs to."""
        for frame in self._editor_views:
            if getattr(frame, "chart_view", None) is view or getattr(frame, "sv_view", None) is view:
                return getattr(frame, "difficulty_path", None)
        return None

    # -- clipboard -------------------------------------------------------------

    def _build_clipboard_shortcuts(self) -> None:
        """Ctrl+C / Ctrl+V, routed the same way the tool digits are: to
        whichever of the two tool rows is visible, so copying in a chart view
        copies notes and copying in an SV view copies green lines. Fixed
        sequences rather than ShortcutRegistry entries -- these are the
        platform's own copy/paste keys, not app-specific bindings.

        Scoped to the Editor page (WidgetWithChildrenShortcut on
        self.editor_page) rather than application-wide on purpose: an
        application-context shortcut *consumes* the key press, so a global
        Ctrl+C would silently break copying text out of any line edit or spin
        box elsewhere in the app, and the should_ignore_shortcut_focus guard
        can only decline to act, not hand the key back.

        Sequences come from the registry (defaulting to the platform's own
        Ctrl+C / Ctrl+V) so Settings lists them with everything else.
        """
        self.copy_shortcut = QShortcut(QKeySequence(self.shortcuts.sequence("copy")), self.editor_page)
        self.copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self.copy_selection)
        self.paste_shortcut = QShortcut(QKeySequence(self.shortcuts.sequence("paste")), self.editor_page)
        self.paste_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.paste_shortcut.activated.connect(self.paste_clipboard)

    def _clipboard_target(self) -> str:
        """"sv" or "chart" -- which editor the copy/paste keys act on."""
        if self.global_sv_tool_row.isVisible() and self._active_sv_view is not None:
            return "sv"
        return "chart"

    def copy_selection(self) -> None:
        if should_ignore_shortcut_focus(QApplication.focusWidget()):
            return
        if self._clipboard_target() == "sv":
            self._copy_sv_points()
        else:
            self._copy_notes()

    def paste_clipboard(self) -> None:
        if should_ignore_shortcut_focus(QApplication.focusWidget()):
            return
        if self._clipboard_target() == "sv":
            self._paste_sv_points()
        else:
            self._paste_notes()

    def _copy_notes(self) -> None:
        view = self._active_chart_view
        if view is None:
            return
        notes = view.selected_notes()
        if not notes:
            return
        base = notes[0].time
        # Plain data, not the HitObjects themselves: a paste has to produce
        # new identities, and holding live objects would let a later edit (or
        # an undo) mutate what the clipboard "contains".
        self._note_clipboard = [
            {
                "offset": note.time - base,
                "x": note.x, "y": note.y, "type": note.type, "hit_sound": note.hit_sound,
                "extras": tuple(note.extras), "hit_sample": note.hit_sample,
            }
            for note in notes
        ]
        self.status.setText(tr("MainWindow", "Copied {count} notes.").format(count=len(notes)))

    def _paste_notes(self) -> None:
        view = self._active_chart_view
        if view is None or not self._note_clipboard:
            return
        difficulty_path = self._difficulty_for_view(view)
        state = self._states.get(difficulty_path) if difficulty_path is not None else None
        if state is None:
            return
        # Snap the paste anchor to the grid. The playhead is wherever audio
        # happens to be, which is almost never exactly on a division, so
        # pasting at the raw position landed a copied pattern a few
        # milliseconds off every snap it was built on.
        base = round(max(0.0, snap_time(view.timing_points, view.current_time, view.snap_divisor)))
        next_index = self._next_original_index(state)
        pasted = []
        for entry in self._note_clipboard:
            pasted.append(HitObject(
                x=entry["x"], y=entry["y"], time=base + int(entry["offset"]),
                type=entry["type"], hit_sound=entry["hit_sound"],
                extras=entry["extras"], hit_sample=entry["hit_sample"],
                original_index=next_index,
            ))
            next_index += 1
        self._insert_notes(state, pasted, "paste_notes")
        self._refresh_difficulty_views(difficulty_path)
        self.status.setText(tr("MainWindow", "Pasted {count} notes.").format(count=len(pasted)))

    def _copy_sv_points(self) -> None:
        view = self._active_sv_view
        if view is None:
            return
        points = view.selected_points()
        if not points:
            return
        base = points[0].time
        self._sv_clipboard = [
            {
                "offset": point.time - base,
                "beat_length": point.beat_length, "meter": point.meter,
                "sample_set": point.sample_set, "sample_index": point.sample_index,
                "volume": point.volume, "effects": point.effects,
            }
            for point in points
        ]
        self.status.setText(tr("MainWindow", "Copied {count} SV points.").format(count=len(points)))

    def _paste_sv_points(self) -> None:
        view = self._active_sv_view
        if view is None or not self._sv_clipboard:
            return
        difficulty_path = self._difficulty_for_view(view)
        state = self._states.get(difficulty_path) if difficulty_path is not None else None
        if state is None:
            return
        # Same grid snap as the note paste -- see _paste_notes.
        base = round(max(0.0, snap_time(
            extract_timing_points(state.document), view.current_time, view.snap_divisor,
        )))
        pasted = [
            TimingPoint(
                time=float(base + entry["offset"]), beat_length=entry["beat_length"],
                meter=entry["meter"], sample_set=entry["sample_set"],
                sample_index=entry["sample_index"], volume=entry["volume"],
                uninherited_flag=0, effects=entry["effects"],
            )
            for entry in self._sv_clipboard
        ]
        self._insert_sv_points(state, pasted, "paste_sv")
        self._refresh_difficulty_sv_views(difficulty_path)
        self.status.setText(tr("MainWindow", "Pasted {count} SV points.").format(count=len(pasted)))

    # -- "one object per millisecond" -----------------------------------------

    @staticmethod
    def _next_original_index(state: DifficultyState) -> int:
        """A fresh applied_positions key for an inserted note.

        Inserted notes used to keep HitObject's default original_index of 0,
        so every one of them aliased the map's very first note in
        applied_positions and in a chart view's selection set.
        """
        return 1 + max(
            [note.original_index for note in state.document.hit_objects]
            + list(state.applied_positions)
            + [-1]
        )

    @staticmethod
    def _notes_blocking(document, time_ms: int, placing_spinner: bool) -> list:
        """Existing hit objects a new one at `time_ms` would have to replace.

        Only one object may sit on any given millisecond, with one exception:
        a spinner (denden) may share a timestamp with anything, in both
        directions -- its start and end routinely land on top of notes and
        neither displaces the other.
        """
        if placing_spinner:
            return []
        return [
            note for note in document.hit_objects
            if note.time == time_ms and not note.is_spinner
        ]

    def _insert_notes(self, state: DifficultyState, notes: list, label_id: str) -> None:
        """Insert notes, replacing anything they would stack on, as one undo step."""
        displaced = []
        seen: set[int] = set()
        for note in notes:
            for victim in self._notes_blocking(state.document, note.time, note.is_spinner):
                if id(victim) not in seen:
                    seen.add(id(victim))
                    displaced.append(victim)
        if displaced:
            state.history.push(
                CompositeCommand([RemoveHitObjects(displaced), InsertHitObjects(notes)], label_id),
                state,
            )
        else:
            state.history.push(InsertHitObjects(notes), state)

    def _insert_sv_points(self, state: DifficultyState, points: list, label_id: str) -> None:
        """Insert inherited points, replacing any that share a millisecond.

        Uninherited (BPM) points are never touched: an SV point and a BPM
        point legitimately share a timestamp, which is exactly the case the
        SV editor paints yellow.
        """
        wanted = {round(point.time) for point in points}
        displaced = [
            point for point in state.document.timing_points
            if not point.uninherited and round(point.time) in wanted
        ]
        if displaced:
            state.history.push(
                CompositeCommand([RemoveTimingPoints(displaced), InsertTimingPoints(points)], label_id),
                state,
            )
        else:
            state.history.push(InsertTimingPoints(points), state)

    def _build_hit_object(self, note_kind: str, time_ms: int, new_combo: bool, big: bool = False) -> HitObject:
        """don/kat only. Slider/spinner carry a duration this signature has
        no room for -- see _build_extendable_hit_object.

        Big (finisher) is the same HITSOUND_FINISH bit sliders already place
        with Shift held; kat additionally keeps its clap bit, since a big kat
        is a finisher clap, not a finisher replacing the clap.
        """
        # Taiko gameplay ignores hit-object x/y entirely; center is the
        # convention used elsewhere in this codebase (e.g. the fake-slider
        # gimmick literal).
        x, y = PLAYFIELD_WIDTH // 2, PLAYFIELD_HEIGHT // 2
        combo_bit = TYPE_NEW_COMBO if new_combo else 0
        finisher_bit = HITSOUND_FINISH if big else 0
        if note_kind == "don":
            return HitObject(x=x, y=y, time=time_ms, type=TYPE_CIRCLE | combo_bit, hit_sound=finisher_bit)
        if note_kind == "kat":
            return HitObject(x=x, y=y, time=time_ms, type=TYPE_CIRCLE | combo_bit, hit_sound=HITSOUND_CLAP | finisher_bit)
        raise ValueError(f"Unknown note kind: {note_kind}")

    def _place_note(self, difficulty_path: Path, note_kind: str, time_ms: float, new_combo: bool, big: bool = False) -> None:
        state = self._states.get(difficulty_path)
        if state is None:
            return
        note = self._build_hit_object(note_kind, round(time_ms), new_combo, big)
        note.original_index = self._next_original_index(state)
        self._insert_notes(state, [note], "place_note")
        self._refresh_difficulty_views(difficulty_path)

    def _build_extendable_hit_object(
        self, note_kind: str, start_ms: int, end_ms: int, new_combo: bool, big: bool, timing_points: list,
    ) -> HitObject:
        """Slider/spinner, sized from a requested time duration (start_ms to
        end_ms) rather than a fixed default. A bare click (start == end)
        still gets a short, visible object instead of a zero-length one.
        """
        x, y = PLAYFIELD_WIDTH // 2, PLAYFIELD_HEIGHT // 2
        combo_bit = TYPE_NEW_COMBO if new_combo else 0
        duration = max(20.0, end_ms - start_ms)
        if note_kind == "slider":
            timing = active_uninherited_at(timing_points, start_ms)
            sv = sv_at(timing_points, start_ms)
            length = slider_length_for_duration(duration, timing.beat_length, sv)
            hit_sound = HITSOUND_FINISH if big else 0
            return HitObject(
                x=x, y=y, time=start_ms, type=TYPE_SLIDER | combo_bit, hit_sound=hit_sound,
                extras=(f"L|{x + 80}:{y}", "1", f"{length:.3f}"),
            )
        if note_kind == "spinner":
            return HitObject(
                x=x, y=y, time=start_ms, type=TYPE_SPINNER | combo_bit, hit_sound=0,
                extras=(str(round(start_ms + duration)),),
            )
        raise ValueError(f"Unknown extendable note kind: {note_kind}")

    def _place_note_with_duration(
        self, difficulty_path: Path, note_kind: str, start_ms: float, end_ms: float, new_combo: bool, big: bool,
    ) -> None:
        state = self._states.get(difficulty_path)
        if state is None:
            return
        note = self._build_extendable_hit_object(
            note_kind, round(start_ms), round(end_ms), new_combo, big, state.document.timing_points,
        )
        note.original_index = self._next_original_index(state)
        self._insert_notes(state, [note], "place_note")
        self._refresh_difficulty_views(difficulty_path)

    def _edit_note_duration(self, difficulty_path: Path, uid: int, new_end_ms: float) -> None:
        """Extend/shorten an already-placed slider or spinner by dragging its
        right edge. Committed once on release (see TimelineGameplay), not
        per drag tick, so this doesn't need SetNoteFields to support merging.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        note = next((n for n in state.document.hit_objects if n.uid == uid), None)
        if note is None:
            return
        duration = max(20.0, new_end_ms - note.time)
        if note.is_spinner:
            new_extras = (str(round(note.time + duration)),)
        elif note.is_slider:
            timing = active_uninherited_at(state.document.timing_points, note.time)
            sv = sv_at(state.document.timing_points, note.time)
            length = slider_length_for_duration(duration, timing.beat_length, sv)
            curve = note.extras[0] if note.extras else f"L|{note.x + 80}:{note.y}"
            new_extras = (curve, "1", f"{length:.3f}")
        else:
            return
        state.history.push(SetNoteFields(note.uid, {"extras": (note.extras, new_extras)}), state)
        self._refresh_difficulty_views(difficulty_path)

    def _delete_note(self, difficulty_path: Path, uid: int) -> None:
        state = self._states.get(difficulty_path)
        if state is None:
            return
        victim = next((note for note in state.document.hit_objects if note.uid == uid), None)
        if victim is None:
            return
        state.history.push(RemoveHitObjects([victim]), state)
        self._refresh_difficulty_views(difficulty_path)

    def _delete_notes(self, difficulty_path: Path, uids) -> None:
        """Delete/Backspace on a chart view's whole selection, as one undo step."""
        state = self._states.get(difficulty_path)
        if state is None:
            return
        wanted = set(uids)
        victims = [note for note in state.document.hit_objects if note.uid in wanted]
        if not victims:
            return
        state.history.push(RemoveHitObjects(victims), state)
        for view in self._editor_views_for(difficulty_path):
            selected = getattr(view, "selected", None)
            if isinstance(selected, set):
                selected.clear()
        self._refresh_difficulty_views(difficulty_path)

    def _refresh_difficulty_views(self, difficulty_path: Path) -> None:
        """Re-read hit objects into every open view of this difficulty after an edit.

        Deliberately does not touch self._timing_bars: a note place/delete
        never changes [TimingPoints] or [Editor] bookmarks, so reloading
        them here was pure wasted work -- and a visible one, since
        TimingOverviewBar.load_document forces an immediate repaint outside
        the normal ~120fps broadcast cadence, which read as a stutter/flicker
        every time a note was placed. Only _refresh_difficulty_sv_views
        (timing-point edits) needs to reload the timing bars.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        for frame in self._editor_views:
            if getattr(frame, "difficulty_path", None) != difficulty_path:
                continue
            chart_view = getattr(frame, "chart_view", None)
            if chart_view is not None:
                chart_view.refresh_notes(state.document)
            gameplay_view = getattr(frame, "gameplay_view", None)
            if gameplay_view is not None:
                gameplay_view.refresh_notes(state.document)
        if self.state is state:
            self.timeline.refresh_notes(state.document)
            self.refresh_canvas()

    # -- M4: SV point editing -------------------------------------------------

    def _add_sv_point(self, difficulty_path: Path, time_ms: float, sv: float) -> None:
        state = self._states.get(difficulty_path)
        if state is None:
            return
        ordered = sorted_by_time(state.document.timing_points)
        template = active_point_at(ordered, time_ms)
        point = TimingPoint.inherited_at(
            round(time_ms), max(0.01, sv),
            # Kiai is a property of the active point, so a new SV point that
            # doesn't carry it forward silently ends the kiai section it was
            # dropped into.
            kiai=bool(template.kiai) if template is not None else False,
            template=template,
        )
        # _insert_sv_points enforces one inherited point per millisecond, so
        # clicking on top of an existing green line replaces it instead of
        # quietly stacking a second one behind it.
        self._insert_sv_points(state, [point], "add_sv_point")
        self._refresh_difficulty_sv_views(difficulty_path)

    def _edit_sv_point_time(self, difficulty_path: Path, uid: int, new_time: float) -> None:
        """Horizontal drag of a line (green or red): retime only, no SV
        change. Not inherited-only -- either kind's own grid position depends
        on its time, so this also refreshes chart views, not just the SV
        graph.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        point = next((p for p in state.document.timing_points if p.uid == uid), None)
        if point is None:
            return
        state.history.push(
            EditTimingPoint(uid, {"time": (point.time, new_time)}),
            state, allow_merge=True,
        )
        state.document.timing_points.sort(key=lambda p: p.time)
        self._refresh_difficulty_views(difficulty_path)
        self._refresh_difficulty_sv_views(difficulty_path)

    def _edit_sv_point(self, difficulty_path: Path, uid: int, new_sv: float) -> None:
        """Vertical drag of a green line's own value dot: SV only, no retime."""
        state = self._states.get(difficulty_path)
        if state is None:
            return
        point = next((p for p in state.document.timing_points if p.uid == uid), None)
        if point is None or point.uninherited:
            return
        new_beat_length = -100.0 / max(0.01, new_sv)
        state.history.push(
            EditTimingPoint(uid, {"beat_length": (point.beat_length, new_beat_length)}),
            state, allow_merge=True,
        )
        self._refresh_difficulty_sv_views(difficulty_path)

    def _delete_sv_point(self, difficulty_path: Path, uid: int) -> None:
        state = self._states.get(difficulty_path)
        if state is None:
            return
        # Inherited only: an uninherited point is a BPM section, not SV
        # editor scope, and deleting the last one would leave the map
        # without any timing at all.
        victim = next((p for p in state.document.timing_points if p.uid == uid and not p.uninherited), None)
        if victim is None:
            return
        state.history.push(RemoveTimingPoints([victim]), state)
        self._refresh_difficulty_sv_views(difficulty_path)

    def _delete_sv_points(self, difficulty_path: Path, uids) -> None:
        """Delete/Backspace on an SV view's selection, as one undo step.

        Same inherited-only rule as the single-point delete: an uninherited
        (BPM) point in the selection is skipped rather than removed.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        wanted = set(uids)
        victims = [
            point for point in state.document.timing_points
            if point.uid in wanted and not point.uninherited
        ]
        if not victims:
            return
        state.history.push(RemoveTimingPoints(victims), state)
        self._refresh_difficulty_sv_views(difficulty_path)

    def _open_sv_function_dialog(self, difficulty_path: Path, start_ms: float, end_ms: float) -> None:
        state = self._states.get(difficulty_path)
        points = state.document.timing_points if state is not None else []
        # Prefill with the SV already in force at each end of the selection, so
        # "Generate" with nothing changed is a no-op rather than a jump to some
        # arbitrary 1.0x -> 2.0x ramp.
        dialog = SVFunctionDialog(
            start_ms, end_ms, self, snap_divisor=int(self.editor_snap_combo.currentData()),
            initial_rate=sv_at(points, start_ms), final_rate=sv_at(points, end_ms),
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self._generate_sv(difficulty_path, start_ms, end_ms, dialog.parameters())

    def _sv_generation_times(self, state: DifficultyState, start_ms: float, end_ms: float, params: dict) -> list[float]:
        """Where the generated SV points go, before the position offset.

        "Each note" (the default) puts one point on each hit object inside the
        selected range and nowhere else -- an SV sweep only has to be correct
        where a note actually is, and a point every 20ms between them was
        hundreds of timing lines nothing could see. "Every snap" walks the
        beat grid at the chosen divisor instead, for sweeps that have to move
        continuously.
        """
        placement = params.get("placement", "notes")
        if placement == "snaps":
            divisor = max(1, int(params.get("snap_divisor", 4)))
            timing_points = extract_timing_points(state.document)
            times: list[float] = []
            # snap_time rounds to the *nearest* division, which can land just
            # before the range; step forward once when it does.
            cursor = snap_time(timing_points, start_ms, divisor)
            if cursor < start_ms - 0.001:
                cursor += active_uninherited_at(timing_points, cursor).beat_length / divisor
            # Guard against a pathological beat length producing an endless
            # walk; 500 matches the old generator's own cap.
            while cursor <= end_ms + 0.001 and len(times) < 501:
                times.append(cursor)
                step = active_uninherited_at(timing_points, cursor + 0.001).beat_length / divisor
                if step <= 0:
                    break
                cursor += step
            return times
        return sorted({
            float(note.time)
            for note in state.document.hit_objects
            if start_ms - 0.001 <= note.time <= end_ms + 0.001
        })

    def _generate_sv(self, difficulty_path: Path, start_ms: float, end_ms: float, params: dict) -> None:
        state = self._states.get(difficulty_path)
        if state is None:
            return
        ordered = sorted_by_time(state.document.timing_points)
        template = active_point_at(ordered, start_ms)
        start_bpm = active_uninherited_at(state.document.timing_points, start_ms).bpm or 1.0

        times = self._sv_generation_times(state, start_ms, end_ms, params)
        if not times:
            self.status.setText(tr("MainWindow", "No notes in the selected range to generate SV on."))
            return

        # Progress runs across the points that are actually generated, not
        # across the dragged range. With note placement the last note is
        # usually well before where the drag ended, so normalising by the
        # dragged duration meant the final point never reached the requested
        # final rate -- the one value a user picking "1.0x to 2.5x" cares most
        # about getting exactly.
        span = times[-1] - times[0]
        offset = params.get("position_offset", SVFunctionDialog.DEFAULT_POSITION_OFFSET_MS)
        points = []
        for index, source_time in enumerate(times):
            # Measured from where the point *belongs*, not from where the
            # offset moves it, so a -5ms lead-in doesn't skew the curve.
            t = 0.0 if span <= 0 else (source_time - times[0]) / span
            time_ms = round(source_time + offset)
            eased = sv_ease(params["function"], t)
            rate = params["initial_rate"] + (params["final_rate"] - params["initial_rate"]) * eased
            if params["relative_to_final_bpm"] and start_bpm:
                # Compensate for a BPM change across the range so the
                # perceived scroll speed matches `rate` regardless of where
                # the local BPM lands, not just the raw multiplier.
                local_bpm = active_uninherited_at(state.document.timing_points, time_ms).bpm or start_bpm
                rate = rate * (local_bpm / start_bpm)
            # Kiai belongs to the active point, so a generated sweep that
            # doesn't carry it forward switches kiai off for the whole range
            # it covers. Read per point, not once: a range can cross a kiai
            # boundary.
            active = active_point_at(ordered, source_time)
            points.append(TimingPoint.inherited_at(
                time_ms, max(0.01, rate),
                kiai=bool(active.kiai) if active is not None else False,
                omit_first_barline=(params["omit_barline"] and index == 0),
                template=template,
            ))

        # One undo step regardless of point count, and regardless of how many
        # existing green lines the sweep replaces.
        self._insert_sv_points(state, points, "generate_sv")
        self._refresh_difficulty_sv_views(difficulty_path)

    def _refresh_difficulty_sv_views(self, difficulty_path: Path) -> None:
        """Re-read timing points into every open SV view of this difficulty after an edit."""
        state = self._states.get(difficulty_path)
        if state is None:
            return
        for frame in self._editor_views:
            if getattr(frame, "difficulty_path", None) != difficulty_path:
                continue
            sv_view = getattr(frame, "sv_view", None)
            if sv_view is not None:
                sv_view.refresh_points(state.document)
            # An SV or BPM edit changes where the gameplay viewer puts every
            # note after it -- that is the point of the view -- so it belongs on
            # the timing-point refresh path as well as the note one.
            gameplay_view = getattr(frame, "gameplay_view", None)
            if gameplay_view is not None:
                gameplay_view.refresh_points(state.document)
        if self.state is state:
            for bar in self._timing_bars:
                bar.load_document(state.document, state.duration_hint)

    def _close_editor_view(self, frame: EditorViewFrame) -> None:
        if frame in self._editor_views:
            self._editor_views.remove(frame)
        chart_view = getattr(frame, "chart_view", None)
        if chart_view is not None and chart_view in self._chart_views:
            self._chart_views.remove(chart_view)
        if chart_view is not None and chart_view in self._editor_snap_views:
            self._editor_snap_views.remove(chart_view)
        if chart_view is not None and chart_view is self._active_chart_view:
            self._active_chart_view = None
            self._sync_global_tool_row()
        sv_view = getattr(frame, "sv_view", None)
        if sv_view is not None and sv_view in self._sv_views:
            self._sv_views.remove(sv_view)
        if sv_view is not None and sv_view in self._editor_snap_views:
            self._editor_snap_views.remove(sv_view)
        if sv_view is not None and sv_view is self._active_sv_view:
            self._active_sv_view = None
            self._sync_global_sv_tool_row()
        if self._last_focused_editor_view in (chart_view, sv_view):
            self._last_focused_editor_view = None
        gameplay_view = getattr(frame, "gameplay_view", None)
        if gameplay_view is not None and gameplay_view in self._gameplay_views:
            self._gameplay_views.remove(gameplay_view)
        if gameplay_view is not None and gameplay_view in self._editor_snap_views:
            self._editor_snap_views.remove(gameplay_view)
        density_view = getattr(frame, "density_view", None)
        if density_view is not None and density_view in self._density_views:
            self._density_views.remove(density_view)

        difficulty_path = getattr(frame, "difficulty_path", None)
        group_layout = self._editor_view_groups.get(difficulty_path) if difficulty_path is not None else None
        if group_layout is not None:
            group_layout.removeWidget(frame)
        frame.setParent(None)
        frame.deleteLater()
        if group_layout is not None:
            self._prune_empty_difficulty_group(difficulty_path, group_layout)

    def _prune_empty_difficulty_group(self, difficulty_path: Path, group_layout: QVBoxLayout) -> None:
        """Remove a difficulty's group (header included) once its last view closes."""
        still_has_views = any(
            isinstance(group_layout.itemAt(i).widget(), EditorViewFrame)
            for i in range(group_layout.count())
        )
        if still_has_views:
            return
        group_widget = group_layout.parentWidget()
        self.editor_views_layout.removeWidget(group_widget)
        group_widget.setParent(None)
        group_widget.deleteLater()
        del self._editor_view_groups[difficulty_path]

    def _snap_changed(self) -> None:
        divisor = int(self.snap_combo.currentData())
        self.timeline.set_snap_divisor(divisor)
        self._update_timeline_info()

    def _snap_changed_by_wheel(self,divisor:int)->None:
        index=self.snap_combo.findData(divisor)
        if index>=0:
            self.snap_combo.blockSignals(True); self.snap_combo.setCurrentIndex(index); self.snap_combo.blockSignals(False)
        self.timeline.snap_divisor=divisor; self._update_timeline_info()

    def _update_equation_control_visibility(self, group: str) -> None:
        controls = self.controls.get(group, {})
        mode_control = controls.get("equation_mode")
        if mode_control is None:
            return
        mode = mode_control.value()
        for key, control in controls.items():
            label = control.parentWidget()
            visible = True
            if key in {"x_expression", "y_expression", "t_min", "t_max"}:
                visible = mode == "parametric"
            elif key == "equation":
                visible = mode != "parametric"
            elif key in {"y_min", "y_max"}:
                visible = mode != "parametric"
            control.setVisible(visible)

    def _insert_equation_token(self, group: str, token: str) -> None:
        control = self.controls.get(group, {}).get("equation")
        if not control or not hasattr(control, "text_input"):
            return
        line = control.text_input
        line.insert(token)

    def _create_equation_keyboard(self, group: str) -> QWidget:
        keyboard = QWidget()
        grid = QVBoxLayout(keyboard)
        grid.setContentsMargins(0, 4, 0, 4)
        rows = [
            ["x", "y", "t", "=", "(", ")", "{", "}"],
            ["+", "-", "*", "/", "^", "abs(", "sqrt("],
            ["sin(", "cos(", "tan(", "asin(", "acos(", "atan("],
            ["pi", "e", "exp(", "ln(", "log(", "floor(", "ceil("],
        ]
        for tokens in rows:
            row = QHBoxLayout()
            for token in tokens:
                button = QPushButton(token)
                button.setFocusPolicy(Qt.NoFocus)
                button.clicked.connect(
                    lambda checked=False, active=group, value=token:
                    self._insert_equation_token(active, value)
                )
                row.addWidget(button)
            grid.addLayout(row)
        keyboard.setVisible(False)
        return keyboard

    def _apply_selected_toggle_style(self) -> None:
        if getattr(self, "_selected_toggle_style_applied", False):
            return
        self._selected_toggle_style_applied = True
        self.setStyleSheet(
            self.styleSheet()
            + """
            QPushButton:checked {
                background-color: #ff66aa;
                color: #ffffff;
                border: 1px solid #ff9dcc;
                font-weight: 700;
            }
            """
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_selected_toggle_style()
        # One size for every tool button, across both rows: they swap in and out
        # of the same spot, so per-label widths made the row re-flow on every
        # switch. Here rather than at build time because the toggle style above
        # is the last thing to change how wide a label wants to be -- and
        # measured rather than hard-coded, or the longest translation is exactly
        # the one that clips.
        equalize_button_widths(
            [*self.tool_buttons.values(), self.new_combo_button, *self.sv_tool_buttons.values()]
        )
        # Same reasoning for the two playback rows, and the same reason it
        # cannot happen at build time: the window's stylesheet (padding
        # included) is applied after the pages are built, so a button measured
        # there reports a width its padded self does not fit in.
        equalize_button_widths([self.editor_play_button, *self.editor_playback_speed_buttons])
        equalize_button_widths([self.timeline_play_button, *self.playback_speed_buttons])

    def _snapshot_transform_group(self, group: str):
        ref = self._transform_page_refs[group]
        combo = ref["combo"]
        values = {
            key: control.value()
            for key, control in self.controls.get(group, {}).items()
        }
        return {
            # Store the actual index as the primary identity. currentData() may
            # be empty or duplicated in older combo configurations, which made
            # both sides eventually fall back to the same transformation.
            "index": combo.currentIndex(),
            "text": combo.currentText(),
            "data": combo.currentData(),
            "values": values,
            "offset": tuple(self.preview_offsets.get(group, [0.0, 0.0])),
        }

    def _restore_transform_group(self, group: str, snapshot) -> None:
        ref = self._transform_page_refs[group]
        combo = ref["combo"]

        target_index = int(snapshot["index"])
        if not 0 <= target_index < combo.count():
            target_index = combo.findText(
                str(snapshot["text"]),
                Qt.MatchExactly,
            )
        if target_index < 0 and snapshot["data"] is not None:
            target_index = combo.findData(snapshot["data"])
        if target_index < 0:
            raise RuntimeError(
                f"Could not restore {group} transformation: "
                f"{snapshot['text']}"
            )

        combo.blockSignals(True)
        combo.setCurrentIndex(target_index)
        combo.blockSignals(False)

        # Rebuild this side exactly once. The copied snapshot remains immutable
        # while Don and Kat are restored, so neither side can overwrite the
        # other side's source state during repeated swaps.
        ref["rebuild"]()

        for key, value in snapshot["values"].items():
            control = self.controls.get(group, {}).get(key)
            if control is not None:
                control.set_value(value)

        offset_x, offset_y = snapshot["offset"]
        self.preview_offsets[group] = [float(offset_x), float(offset_y)]
        self._sync_position_controls(group)

    def _swap_don_kat_transformations(self) -> None:
        if not self._is_split_mode():
            return
        if not all(
            group in getattr(self, "_transform_page_refs", {})
            for group in ("don", "kat")
        ):
            return

        # Capture both complete states before changing either UI page.
        snapshots = {
            "don": self._snapshot_transform_group("don"),
            "kat": self._snapshot_transform_group("kat"),
        }

        self.preview_timer.stop()
        self._restore_transform_group("don", snapshots["kat"])
        self._restore_transform_group("kat", snapshots["don"])
        self.preview_cache.clear()
        self.schedule_preview()
        self.status.setText(tr("MainWindow", "Swapped Don and Kat transformations."))

    def _control_page(self, group: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        combo = QComboBox()
        combo.addItem(tr("MainWindow", "None"), "")
        for name in GUI_TRANSFORMATIONS: combo.addItem(display_name(name), name)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        scroll.setWidget(form_widget)

        layout.addWidget(combo)
        layout.addWidget(scroll, 1)
        equation_keyboard_button = QPushButton("⌨")
        equation_keyboard_button.setToolTip(tr("MainWindow", "Show or hide equation keyboard"))
        equation_keyboard_button.setCheckable(True)
        equation_keyboard_button.setFixedWidth(38)
        equation_keyboard_button.setVisible(False)
        equation_keyboard = self._create_equation_keyboard(group)
        equation_keyboard_button.toggled.connect(equation_keyboard.setVisible)
        layout.addWidget(equation_keyboard_button, 0, Qt.AlignLeft)
        layout.addWidget(equation_keyboard)

        self.controls[group] = {}

        def rebuild() -> None:
            while form.rowCount():
                form.removeRow(0)

            self.controls[group] = {}

            transformation_name = combo.currentData()
            equation_mode = transformation_name == "equation"
            equation_keyboard_button.setVisible(equation_mode)
            if not equation_mode:
                equation_keyboard_button.setChecked(False)

            for definition in PARAMETERS.get(transformation_name, []):
                control = ParameterControl(definition)
                control.changed.connect(self.schedule_preview)
                if definition["key"] == "equation_mode":
                    control.changed.connect(lambda active_group=group: self._update_equation_control_visibility(active_group))

                self.controls[group][definition["key"]] = control
                form.addRow(tr("Parameters", str(definition["label"])), control)

            if transformation_name=="drawn_path":
                draw_button=QPushButton(tr("MainWindow", "Open Drawing Window"))
                def open_drawing(active_group=group):
                    self.drawing_dialog_active=True
                    try:
                        dialog=DrawingDialog(self)
                        if dialog.exec()==QDialog.Accepted:
                            points=list(dialog.accepted_points)
                            self.last_drawing_points=points
                            self.drawing_points[active_group]=points
                            # Share the strokes with every other group that is also
                            # set to Drawing, so Split Don/Kat previews both halves.
                            for group_name,refs in self._transform_page_refs.items():
                                if str(refs["combo"].currentData() or "")=="drawn_path":self.drawing_points[group_name]=points
                            self.preview_cache.clear();self.schedule_preview()
                    finally:self.drawing_dialog_active=False
                draw_button.clicked.connect(open_drawing);form.addRow(draw_button)
            self.position_controls[group] = {}
            for axis, label, minimum, maximum in (
                ("x", "Position X", -512, 512),
                ("y", "Position Y", -384, 384),
            ):
                position_control = ParameterControl(
                    {
                        "key": f"position_{axis}",
                        "label": label,
                        "type": "int",
                        "min": minimum,
                        "max": maximum,
                        "default": round(self.preview_offsets[group][0 if axis == "x" else 1]),
                    }
                )
                position_control.changed.connect(
                    lambda active_group=group: self._position_slider_changed(active_group)
                )
                self.position_controls[group][axis] = position_control
                form.addRow(tr("Parameters", label), position_control)

            self.schedule_preview()

        combo.currentTextChanged.connect(rebuild)
        page.combo = combo
        rebuild()

        if not hasattr(self, "_transform_page_refs"):
            self._transform_page_refs = {}
        self._transform_page_refs[group] = {
            "page": page,
            "combo": combo,
            "rebuild": rebuild,
        }
        return page

    def _transformation_mode(self) -> str:
        """Return the stable mode identifier, "all" or "split".

        Reads combo item data rather than the visible label so that mode logic
        is unaffected by localization.
        """
        return str(self.mode_combo.currentData() or "all")

    def _is_split_mode(self) -> bool:
        return self._transformation_mode() == "split"

    def _rebuild_control_tabs(self) -> None:
        self.control_tabs.clear()

        if not self._is_split_mode():
            self.control_tabs.addTab(
                self._control_page("all"),
                tr("MainWindow", "All"),
            )
        else:
            self.control_tabs.addTab(
                self._control_page("don"),
                tr("MainWindow", "Don"),
            )
            self.control_tabs.addTab(
                self._control_page("kat"),
                tr("MainWindow", "Kat"),
            )

        self.schedule_preview()

    def _spec(
        self,
        tab_index: int,
        group: str,
    ) -> tuple[str, dict[str, Any]]:
        page = self.control_tabs.widget(tab_index)

        name=str(page.combo.currentData() or "");params={key:control.value() for key,control in self.controls[group].items()}
        if name=="drawn_path":params["points"]=list(self.drawing_points.get(group) or self.last_drawing_points)
        return name,params

    def open_map(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, tr("MainWindow", "Open osu! beatmap"), "", tr("MainWindow", "osu! beatmaps (*.osu)")
        )
        if filename:
            self._load_map_path(Path(filename).resolve(), refresh_difficulties=True)

    def _ensure_state(self, source_path: Path) -> DifficultyState:
        """Return the DifficultyState for source_path, parsing it once.

        A difficulty that has already been visited this session keeps its
        edits, selection and undo history in self._states rather than being
        re-parsed from disk and losing them.
        """
        cached = self._states.get(source_path)
        if cached is not None:
            return cached

        document = parse_osu(source_path)
        if not document.hit_objects:
            raise ValueError("No hit objects were parsed.")
        audio_path = resolve_song_asset(
            source_path.parent,
            document.audio_filename,
            {".mp3", ".ogg", ".wav", ".flac", ".m4a", ".aac", ".opus"},
        )
        if not audio_path.is_file():
            raise FileNotFoundError(f"Audio file not found: {audio_path.name}")

        background_name = extract_background_filename(document)
        background_path = None
        if background_name:
            try:
                candidate = resolve_song_asset(source_path.parent, background_name, {".jpg", ".jpeg", ".png", ".webp", ".bmp"})
                background_path = candidate if candidate.is_file() else None
            except ValueError:
                background_path = None

        state = DifficultyState.from_document(
            document,
            source_path,
            audio_path=audio_path,
            background_path=background_path,
        )
        # Matches the cursor TimelineGameplay.load_document starts at, so
        # activating a freshly-parsed state doesn't jump the audio to 0:00.
        state.playhead_ms = float(document.hit_objects[0].time)
        self._states[source_path] = state
        return state

    def _activate_state(self, state: DifficultyState) -> None:
        """Make `state` the active difficulty.

        Only touches what actually changed: the audio source is left alone
        when the new difficulty shares the previous one's audio file, which
        is the common case within one song folder, so playback is not reset
        to 0:00 on every switch.
        """
        previous = self.state
        if previous is not None:
            previous.playhead_ms = self.timeline.current_time

        self.state = state
        self.setWindowTitle(f"{state.source_path.name} - {self.app_name}")

        if state.audio_path is not None and (previous is None or previous.audio_path != state.audio_path):
            self.player.setSource(QUrl.fromLocalFile(str(state.audio_path)))

        self.timeline.load_document(state.document)
        self.timeline.set_snap_divisor(int(self.snap_combo.currentData()))
        for bar in self._timing_bars:
            bar.load_document(state.document, state.duration_hint)
        # Fancy Arranger's own density chart always reflects the active
        # difficulty (unlike Editor-page density views, which are
        # per-chosen-difficulty and don't reload on activation).
        self.fancy_density.load_document(state.document, state.duration_hint)

        self.canvas.set_background(str(state.background_path) if state.background_path else None)

        for button in (
            self.play_button, self.apply_button, self.reset_button, self.export_button, self.apply_original_button,
            self.undo_button, self.redo_button, self.save_all_button, self.export_new_difficulty_button,
        ):
            button.setEnabled(True)
        self.refresh_canvas()
        self.seek_audio(round(state.playhead_ms))

        # First time this difficulty is activated, give it a default chart +
        # SV editor view on the Editor page. Once the user closes them,
        # _editor_view_groups drops the entry and they come back the next
        # time this difficulty is (re)activated -- treated as "not set up
        # yet" rather than "user doesn't want any views."
        if state.source_path not in self._editor_view_groups:
            self._add_editor_view("chart", state.source_path)
            self._add_editor_view("sv", state.source_path)

        self.status.setText(f"{state.source_path.name} | drag notes, preview, then Apply to selection")

    def open_map(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, tr("MainWindow", "Open osu! beatmap"), "", tr("MainWindow", "osu! beatmaps (*.osu)")
        )
        if filename:
            self._load_map_path(Path(filename).resolve(), refresh_difficulties=True)

    def _load_map_path(self, source_path: Path, refresh_difficulties: bool = False) -> None:
        try:
            state = self._ensure_state(source_path)
        except Exception as error:
            QMessageBox.critical(self, tr("MainWindow", "Open failed"), str(error))
            return

        self._activate_state(state)

        # Opening a map from the song list (or the header's Open .osu) means
        # you want to edit it, so follow it to the Editor. Switching
        # difficulty while already on a working page leaves you there.
        if self.page_stack.currentIndex() == PAGE_LIBRARY:
            self._show_page(PAGE_EDITOR)

        if refresh_difficulties:
            self.song_difficulties = sorted(source_path.parent.glob("*.osu"), key=lambda item: item.name.lower())
            self.difficulty_combo.blockSignals(True)
            self.difficulty_combo.clear()
            for difficulty_path in self.song_difficulties:
                try:
                    difficulty_document = parse_osu(difficulty_path)
                    label = difficulty_document.version or difficulty_path.stem
                except Exception:
                    label = difficulty_path.stem
                self.difficulty_combo.addItem(label, str(difficulty_path))
            selected_index = next((i for i, item in enumerate(self.song_difficulties) if item.resolve() == source_path), 0)
            self.difficulty_combo.setCurrentIndex(selected_index)
            self.difficulty_combo.setEnabled(bool(self.song_difficulties))
            self.difficulty_combo.blockSignals(False)

    def _difficulty_changed(self, index: int) -> None:
        if index < 0:
            return
        value = self.difficulty_combo.itemData(index)
        if value:
            candidate = Path(value).resolve()
            if candidate != self.source_path:
                self._load_map_path(candidate, refresh_difficulties=False)

    def _background_dropped(self, dropped_path: str) -> None:
        if self.document is None or self.source_path is None:
            QMessageBox.information(self, tr("MainWindow", "Open a map first"), tr("MainWindow", "Open a beatmap before adding a background."))
            return
        source = Path(dropped_path).resolve()
        destination = self.source_path.parent / source.name
        if source != destination:
            if destination.exists():
                stem, suffix = source.stem, source.suffix
                number = 2
                while destination.exists():
                    destination = self.source_path.parent / f"{stem}_{number}{suffix}"
                    number += 1
            try:
                shutil.copy2(source, destination)
            except Exception as error:
                QMessageBox.critical(self, tr("MainWindow", "Background copy failed"), str(error))
                return
        set_document_background(self.document, destination.name)
        self.current_background_path = destination
        self.canvas.set_background(str(destination))
        self.status.setText(f"Background set to {destination.name}. Export or apply to original to save the .osu reference.")

    def _selection_changed(self, selected) -> None:
        new_selection = set(selected)
        if new_selection != self.selected:
            self.preview_offsets = {"all": [0.0, 0.0], "don": [0.0, 0.0], "kat": [0.0, 0.0]}
        self.selected=new_selection; self.preview_timer.stop(); self.preview_positions=dict(self.applied_positions); self.refresh_canvas()
    def _selection_finalized(self, selected) -> None:
        self.selected=set(selected)
        if any(self._spec(i, group)[0] for i,group in enumerate(("all",) if not self._is_split_mode() else ("don","kat"))): self.schedule_preview()

    def schedule_preview(self) -> None:
        if self.document is not None:
            self.preview_timer.start()

    def _calculate_selected_transform(
        self,
    ) -> dict[int, tuple[int, int]]:
        # Time order, not key order (R11). sorted(self.selected) was
        # time-ordered only by the accident that original_index followed file
        # order -- which stopped being true once notes could be inserted and
        # pasted, since those get the next free key regardless of when they
        # sit. Every chunked transformation depends on this ordering.
        indexes = self.state.notes_in_time_order() if self.state is not None else []

        if not indexes:
            return {}

        if not self._is_split_mode():
            transformation_name, params = self._spec(
                0,
                "all",
            )

            if not transformation_name: return {}
            if transformation_name in {"taiko","vertical_taiko"}:
                params.update({"note_times":{n.original_index:n.time for n in self.document.hit_objects},"timing_points":self.document.timing_points,"timing_mode":"filtered","anchor_mode":"selection_start"})
            return transform(transformation_name,indexes,params)

        note_by_index = {
            note.original_index: note
            for note in self.document.hit_objects
        }

        don_indexes = [
            index
            for index in indexes
            if not note_by_index[index].is_kat
        ]

        kat_indexes = [
            index
            for index in indexes
            if note_by_index[index].is_kat
        ]

        don_name, don_params = self._spec(0,"don"); kat_name, kat_params = self._spec(1,"kat")
        for name,params in ((don_name,don_params),(kat_name,kat_params)):
            if name in {"taiko","vertical_taiko"}: params.update({"note_times":{n.original_index:n.time for n in self.document.hit_objects},"timing_points":self.document.timing_points,"timing_mode":"filtered","anchor_mode":"selection_start"})

        groups={}
        if don_name: groups["don"]={"transformation_name":don_name,"selected_note_indexes":don_indexes,"params":don_params}
        if kat_name: groups["kat"]={"transformation_name":kat_name,"selected_note_indexes":kat_indexes,"params":kat_params}
        return transform_groups(groups)


    def _indices_for_drag_group(self, clicked_group: str) -> set[int]:
        if not self._is_split_mode():
            return set(self.selected)
        return {
            note.original_index
            for note in self.document.hit_objects
            if note.original_index in self.selected
            and ((clicked_group == "kat" and note.is_kat) or (clicked_group == "don" and not note.is_kat))
        }

    def _apply_preview_offsets(self, positions: dict[int, tuple[int, int]]) -> dict[int, tuple[int, int]]:
        output = dict(positions)
        note_by_index = {note.original_index: note for note in self.document.hit_objects}
        for index in self.selected:
            group = "kat" if note_by_index[index].is_kat else "don"
            offset_key = "all" if not self._is_split_mode() else group
            dx, dy = self.preview_offsets[offset_key]
            if index in output:
                x, y = output[index]
                output[index] = (round(x + dx), round(y + dy))
        return output

    def _position_slider_changed(self, group: str) -> None:
        controls = self.position_controls.get(group)
        if not controls:
            return
        new_x = float(controls["x"].value())
        new_y = float(controls["y"].value())
        old_x, old_y = self.preview_offsets[group]
        requested_dx = new_x - old_x
        requested_dy = new_y - old_y
        clicked_group = group
        if group == "all":
            # All Notes uses either note kind only as a drag anchor. The group
            # selection method returns the whole selection in this mode.
            clicked_group = "don"
        self._canvas_dragged(
            clicked_group,
            requested_dx,
            requested_dy,
            sync_controls=False,
        )

    def _sync_position_controls(self, group: str) -> None:
        controls = self.position_controls.get(group)
        if not controls:
            return
        x, y = self.preview_offsets[group]
        controls["x"].set_value(round(x))
        controls["y"].set_value(round(y))

    def _sync_center_controls_after_drag(self, offset_key: str, dx: float, dy: float) -> None:
        # Position X/Y are always synchronized by the existing drag code.
        # Center X/Y are transformation parameters and should visually track
        # the same translation without triggering a transformation rebuild.
        groups = ("all",) if not self._is_split_mode() else (offset_key,)
        for group in groups:
            controls = self.controls.get(group, {})
            center_x = controls.get("center_x")
            center_y = controls.get("center_y")
            if center_x is not None:
                center_x.set_value(max(0, min(PLAYFIELD_WIDTH, float(center_x.value()) + dx)))
            if center_y is not None:
                center_y.set_value(max(0, min(PLAYFIELD_HEIGHT, float(center_y.value()) + dy)))

    def _canvas_dragged(self, clicked_group: str, requested_dx: float, requested_dy: float, sync_controls: bool = True) -> None:
        if self.document is None or not self.selected:
            return
        target_indices = self._indices_for_drag_group(clicked_group)
        if not target_indices:
            return
        offset_key = "all" if not self._is_split_mode() else clicked_group
        current_positions = {
            index: self.preview_positions.get(index, self.applied_positions[index])
            for index in target_indices
        }
        min_x = min(x for x, _ in current_positions.values())
        max_x = max(x for x, _ in current_positions.values())
        min_y = min(y for _, y in current_positions.values())
        max_y = max(y for _, y in current_positions.values())
        dx = max(-min_x, min(PLAYFIELD_WIDTH - max_x, requested_dx))
        dy = max(-min_y, min(PLAYFIELD_HEIGHT - max_y, requested_dy))
        self.preview_offsets[offset_key][0] += dx
        self.preview_offsets[offset_key][1] += dy
        for index in target_indices:
            x, y = current_positions[index]
            self.preview_positions[index] = (round(x + dx), round(y + dy))
        if sync_controls:
            self._sync_position_controls(offset_key)
        elif dx != requested_dx or dy != requested_dy:
            # A slider request reached a playfield edge. Keep the displayed
            # value aligned with the actual clamped translation.
            self._sync_position_controls(offset_key)
        self._sync_center_controls_after_drag(offset_key, dx, dy)
        self.refresh_canvas()

    def update_preview(self) -> None:
        try:
            self.preview_positions=dict(self.applied_positions)
            specs=[]
            if not self._is_split_mode(): specs=[self._spec(0,"all")]
            else: specs=[self._spec(0,"don"),self._spec(1,"kat")]
            if any(name=="drawn_path" and len(params.get("points",[]))<2 for name,params in specs):
                self.preview_positions=dict(self.applied_positions);self.refresh_canvas();self.status.setText(tr("MainWindow", "Open Drawing Window and draw a shape to preview."));return
            key=(self.commit_revision,tuple(sorted(self.selected)),tuple((name,freeze_preview_value(params)) for name,params in specs))
            result=self.preview_cache.get(key)
            if result is None:
                result=self._calculate_selected_transform(); self.preview_cache={key:result}
            self.preview_positions.update(self._apply_preview_offsets(result))
        except Exception as error:
            self.status.setText(
                f"Preview error: {error}"
            )
            return

        self.refresh_canvas()

        self.status.setText(
            f"Preview only: {len(self.selected)} selected notes. "
            "Click Apply to selection to commit."
        )

    def refresh_canvas(self) -> None:
        if self.document is None:
            return

        visible_notes=[n for n in self.document.hit_objects if not self.selected or n.original_index in self.selected]
        self.canvas.set_state(visible_notes,self.preview_positions,self.selected)

        self.timeline.selected = set(self.selected)
        self.timeline.update()

    def apply_selection(self) -> None:
        if not self.selected:
            self.status.setText(
                tr("MainWindow", "Select notes in the bottom timeline first.")
            )
            return

        try:
            changed_positions = self._apply_preview_offsets(
                self._calculate_selected_transform()
            )
        except Exception as error:
            QMessageBox.warning(
                self,
                tr("MainWindow", "Apply failed"),
                str(error),
            )
            return

        changes = {
            key: (self.applied_positions[key], value)
            for key, value in changed_positions.items()
        }
        self.preview_cache.clear()
        self.state.history.push(MoveNotes(changes), self.state)
        self.preview_offsets = {"all": [0.0, 0.0], "don": [0.0, 0.0], "kat": [0.0, 0.0]}
        self.state.sync_preview_to_applied()
        self.refresh_canvas()

        self.status.setText(
            f"Applied transformation to "
            f"{len(changed_positions)} notes."
        )

    def reset_applied(self) -> None:
        self.preview_cache.clear()
        self.state.history.push(
            SetNotePositions(dict(self.applied_positions), dict(self.original_positions)),
            self.state,
        )
        self.state.sync_preview_to_applied()
        self.refresh_canvas()

        self.status.setText(
            tr("MainWindow", "All applied transformations reset.")
        )

    def _history_target(self) -> DifficultyState | None:
        """The difficulty Ctrl+Z / Ctrl+Y should act on.

        Whichever Editor-page view last had focus, since that is what the
        user was just editing -- an Editor view can be showing a difficulty
        other than the Fancy Arranger page's active one. Falls back to the
        active difficulty when no editor view is focused (Fancy Arranger's
        own transform undo).
        """
        view = self._last_focused_editor_view
        if view is not None:
            path = self._difficulty_for_view(view)
            state = self._states.get(path) if path is not None else None
            if state is not None:
                return state
        return self.state

    def _after_history_change(self, state: DifficultyState) -> None:
        """Re-read the reverted/reapplied document into every view showing it.

        Undo used to refresh only the transform canvas, so undoing a note
        placement or an SV sweep left every Editor-page view still painting
        the old document -- which read as "the undo shortcut does nothing".
        """
        state.preview_cache.clear()
        state.sync_preview_to_applied()
        self._refresh_difficulty_views(state.source_path)
        self._refresh_difficulty_sv_views(state.source_path)
        self.refresh_canvas()

    def undo(self)->None:
        state = self._history_target()
        if state is None or not state.history.can_undo(): return
        state.history.undo(state); self._after_history_change(state)
    def redo(self)->None:
        state = self._history_target()
        if state is None or not state.history.can_redo(): return
        state.history.redo(state); self._after_history_change(state)

    def _change_playback_speed(self,rate:float)->None:
        rate=float(rate);self.player.setPlaybackRate(rate)
        self.audio_anchor_position=self.player.position();self.audio_anchor_clock.restart()
        for button in getattr(self,"playback_speed_buttons",[]) + getattr(self,"editor_playback_speed_buttons",[]):
            active=abs(float(button.property("playbackRate"))-rate)<0.0001
            button.blockSignals(True);button.setChecked(active);button.blockSignals(False)

    def toggle_playback(self) -> None:
        if (
            self.player.playbackState()
            == QMediaPlayer.PlayingState
        ):
            self.player.pause()
            self.timeline.is_playing=False
            self.play_button.setText(tr("MainWindow", "Play"))
            if hasattr(self,"timeline_play_button"):self.timeline_play_button.setText("▶")
            if hasattr(self,"editor_play_button"):self.editor_play_button.setText("▶")
        else:
            self.audio_anchor_position=self.player.position(); self.audio_anchor_clock.restart()
            self.timeline.is_playing=True
            self.player.play()
            self.play_button.setText(tr("MainWindow", "Pause"))
            if hasattr(self,"timeline_play_button"):self.timeline_play_button.setText("❚❚")
            if hasattr(self,"editor_play_button"):self.editor_play_button.setText("❚❚")

    def seek_audio(self, position: int) -> None:
        position=max(0,position); self.audio_anchor_position=position; self.latest_audio_position=position; self.audio_anchor_clock.restart()
        self.player.setPosition(position); self.timeline.set_time(position,force=True)
        for view in self._sv_views:
            view.set_time(position,force=True)
        for view in self._gameplay_views:
            view.set_time(position,force=True)
        for bar in self._timing_bars:
            bar.set_time(position)
        for view in self._density_views:
            view.set_time(position)

    def _timeline_info_text(self, duration_text: str, position_text: str, snap_text: str) -> str:
        """Build the timeline help line from individually translated pieces.

        The wheel hints describe the real step size in wheelEvent, which moves
        one snap division per notch and one whole beat with Shift held.
        """
        return (
            f"{tr('MainWindow', 'Duration')}: {duration_text}   |   "
            f"{tr('MainWindow', 'Now')}: {position_text}   |   "
            f"{tr('MainWindow', 'Snap')}: {snap_text}   |   "
            f"{tr('MainWindow', 'Wheel: 1 snap')}   |   "
            f"{tr('MainWindow', 'Shift+wheel: 1 beat')}   |   "
            f"{tr('MainWindow', 'Ctrl+wheel: zoom')}"
        )

    def _update_timeline_info(self) -> None:
        if not hasattr(self, "timeline_info"):
            return
        duration = max(0, self.player.duration())
        position = max(0, round(self.timeline.current_time) if hasattr(self,"timeline") else self.player.position())
        for bar in getattr(self,"_timing_bars",()): bar.set_duration(duration)
        if hasattr(self,"timeline_time"):
            value=max(0,int(position))
            percent=value/duration*100.0 if duration>0 else 0.0
            self.timeline_time.setText(f"{value//60000:02d}:{(value%60000)//1000:02d}:{value%1000:03d}   {percent:.1f}%")
        self.timeline_info.setText(
            self._timeline_info_text(
                format_time(duration),
                format_time(position),
                f"1/{int(self.snap_combo.currentData())}",
            )
        )

    def _predicted_audio_position(self) -> float:
        """Extrapolate from the last known anchor using nanosecond precision.

        Integer-millisecond elapsed() truncation used to compound visibly at
        higher playback rates (1ms of rounding error becomes `rate` ms of
        position error); nsecsElapsed() keeps that error under a
        microsecond regardless of rate.
        """
        position = float(self.audio_anchor_position)
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            position += self.audio_anchor_clock.nsecsElapsed() / 1_000_000.0 * self.player.playbackRate()
        return position

    def _player_position_changed(self, position: int) -> None:
        self.latest_audio_position = position
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self.audio_anchor_position = position
            self.audio_anchor_clock.restart()
            return

        # Qt's own position reports are noisier than our extrapolation
        # between them (backend rounding/buffering jitter, worse at
        # non-1x rates). Hard-resetting the anchor to `position` on every
        # report turned that jitter into a visible correction jump each
        # time. But *ignoring* every report under a fixed threshold was
        # its own bug: a small, steady-state bias (e.g. consistent
        # decode/resample latency at a slow rate) never gets corrected
        # if no single report ever exceeds the threshold, so displayed
        # position stays quietly wrong for the entire playback. Blend
        # instead: nudge the anchor a fraction of the way toward the
        # reported position every time, so real drift (small or large,
        # one-off or systematic) converges within a few reports without
        # any single correction being large enough to see.
        predicted = self._predicted_audio_position()
        error = position - predicted
        if abs(error) >= POSITION_HARD_RESYNC_THRESHOLD_MS:
            # A real discontinuity (backend stall, external seek) --
            # correcting it gradually would be audibly/visibly wrong for
            # however long convergence took, so snap immediately instead.
            self.audio_anchor_position = position
        else:
            self.audio_anchor_position = predicted + error * POSITION_CORRECTION_FACTOR
        self.audio_anchor_clock.restart()
    def _render_gameplay_frame(self) -> None:
        now_ns = self.gameplay_frame_clock.nsecsElapsed()
        if now_ns < self._next_frame_due_ns:
            return
        # Schedule the next frame from the fixed cadence, not from "now":
        # a timer tick firing a few hundred microseconds late no longer
        # pushes every subsequent frame later too, which is what made the
        # old restart()-every-tick scheme's frame spacing uneven.
        self._next_frame_due_ns += self.gameplay_frame_interval_ns
        if self._next_frame_due_ns < now_ns:
            # Fell behind by more than a full interval (e.g. a stall);
            # resync instead of firing a burst of catch-up frames.
            self._next_frame_due_ns = now_ns + self.gameplay_frame_interval_ns
        if self.document is None:return
        position=self._predicted_audio_position()
        duration=self.player.duration()
        if duration>0:position=min(position,duration)
        # Paused, the playhead does not move: _predicted_audio_position only
        # advances in PlayingState, so every view below would repaint an
        # identical picture 120 times a second. Seeks and edits repaint through
        # their own paths (seek_audio, refresh_notes), never through this one.
        if position==self._last_broadcast_position:return
        self._last_broadcast_position=position
        # Broadcast to every chart view on screen, not just the shared
        # player-deck timeline: Editor-page chart views scroll in lockstep.
        for view in self._chart_views:
            view.set_time(position,force=True)
        # SV editors ride the same clock, so the green lines scroll past at
        # exactly the rate the notes above them do.
        for view in self._sv_views:
            view.set_time(position,force=True)
        for view in self._gameplay_views:
            view.set_time(position,force=True)
        for view in self._density_views:
            view.set_time(position)
            view.set_viewport(position,self.timeline.window_ms)
        for bar in self._timing_bars:
            bar.set_time(position)
            bar.set_viewport(position,self.timeline.window_ms)
        if hasattr(self,"editor_timeline_strip"):
            percent=position/duration*100.0 if duration>0 else 0.0
            self.editor_timeline_strip.setText(f"{format_time(position)}   {percent:.1f}%")

    def apply_to_original_file(self)->None:
        if self.document is None or self.source_path is None:return
        answer=QMessageBox.question(self,tr("MainWindow", "Overwrite original beatmap?"),tr("MainWindow", "This writes every committed transformation to the original .osu file. Continue?"),QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        try:
            for note in self.document.hit_objects:note.x,note.y=self.applied_positions.get(note.original_index,(note.x,note.y))
            write_osu(self.document,self.source_path,self.document.version,allow_overwrite_source=True,create_backup=True,force_ar=self.approach_rate_control.value(),force_cs=self.circle_size_control.value())
        except Exception as error:QMessageBox.critical(self,tr("MainWindow", "Write failed"),str(error));return
        QMessageBox.information(self,tr("MainWindow", "Original updated"),tr("MainWindow", "Applied all committed changes to:")+"\n"+str(self.source_path))

    def save_all_states(self) -> None:
        """Write every changed difficulty's original file at once (global header's Save)."""
        dirty_states = [state for state in self._states.values() if state.history.dirty]
        if not dirty_states:
            self.status.setText(tr("MainWindow", "Nothing to save."))
            return
        errors = []
        for state in dirty_states:
            try:
                for note in state.document.hit_objects:
                    # .get, not []: a note inserted by an older session (or
                    # restored by undoing a delete) may predate its
                    # applied_positions entry, and failing the whole save for
                    # that is far worse than writing the note where it is.
                    note.x, note.y = state.applied_positions.get(note.original_index, (note.x, note.y))
                write_osu(
                    state.document, state.source_path, state.document.version,
                    allow_overwrite_source=True, create_backup=True,
                    force_ar=self.approach_rate_control.value(), force_cs=self.circle_size_control.value(),
                )
                state.history.mark_saved()
            except Exception as error:
                errors.append(f"{state.source_path.name}: {error}")
        if errors:
            QMessageBox.critical(self, tr("MainWindow", "Save failed"), "\n".join(errors))
        else:
            self.status.setText(tr("MainWindow", "Saved all changed difficulties."))

    def export_new_difficulty(self) -> None:
        """Branch the active difficulty off into a new file in the same song folder."""
        if self.state is None:
            QMessageBox.information(
                self,
                tr("MainWindow", "Open a map first"),
                tr("MainWindow", "Open a beatmap before exporting a new difficulty."),
            )
            return
        name, ok = QInputDialog.getText(
            self, tr("MainWindow", "Export new difficulty"), tr("MainWindow", "New difficulty name:")
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        source = self.state.source_path
        candidate = source.with_name(f"{source.stem} [{name}].osu")
        number = 2
        while candidate.exists():
            candidate = source.with_name(f"{source.stem} [{name}] ({number}).osu")
            number += 1
        try:
            for note in self.document.hit_objects:
                note.x, note.y = self.applied_positions[note.original_index]
            write_osu(
                self.document, candidate, name,
                force_ar=self.approach_rate_control.value(), force_cs=self.circle_size_control.value(),
            )
        except Exception as error:
            QMessageBox.critical(self, tr("MainWindow", "Export failed"), str(error))
            return
        QMessageBox.information(self, tr("MainWindow", "Export complete"), tr("MainWindow", "Created:") + "\n" + str(candidate))

    def export_map(self) -> None:
        if self.document is None or self.source_path is None:
            return

        candidate = self.source_path.with_name(
            f"{self.source_path.stem} [arranged].osu"
        )

        number = 2

        while candidate.exists():
            candidate = self.source_path.with_name(
                f"{self.source_path.stem} "
                f"[arranged] ({number}).osu"
            )
            number += 1

        destination, _ = QFileDialog.getSaveFileName(
            self,
            tr("MainWindow", "Export applied map"),
            str(candidate),
            tr("MainWindow", "osu! beatmaps (*.osu)"),
        )

        if not destination:
            return

        destination_path = Path(destination).resolve()

        if destination_path == self.source_path:
            QMessageBox.warning(
                self,
                tr("MainWindow", "Source protected"),
                tr("MainWindow", "Choose a different filename."),
            )
            return

        try:
            for note in self.document.hit_objects:
                note.x, note.y = self.applied_positions[
                    note.original_index
                ]

            write_osu(
                self.document,
                destination_path,
                f"{self.document.version} (arranged)",
                force_ar=self.approach_rate_control.value(),
                force_cs=self.circle_size_control.value(),
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                tr("MainWindow", "Export failed"),
                str(error),
            )
            return

        QMessageBox.information(
            self,
            tr("MainWindow", "Export complete"),
            tr("MainWindow", "Created:") + "\n" + str(destination_path),
        )


def main() -> None:
    app=QApplication(sys.argv)
    # Without these, QStandardPaths.AppDataLocation resolves to
    # AppData/Roaming/python -- shared with every other PySide app run by the
    # same interpreter, and where the song index would have been written.
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationName(APPLICATION_NAME)
    settings = SettingsManager()
    # First start only: pick a language before any UI text is built, since Qt
    # does not retranslate widgets that already exist.
    #
    # Keyed on setup/completed, not on language/current: the Settings dialog
    # writes a language on every Apply, so an existing install already has one
    # stored and would have skipped this screen without ever showing it.
    # setup/completed is only set once a songs folder has actually been
    # chosen, so a cancelled setup is offered again next launch.
    if not settings.bool_value("setup/completed", False):
        dialog = LanguageDialog()
        dialog.exec()
        settings.set_value("language/current", dialog.language)
        settings.sync()
    install_translator(app, settings)
    icon=application_icon()
    if not icon.isNull():app.setWindowIcon(icon)
    window = MainWindow()
    window.showMaximized()
    window.start_library()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()