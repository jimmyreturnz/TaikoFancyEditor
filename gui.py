from __future__ import annotations

import random
import csv
import shutil
import sys
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImageReader, QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSlider, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget, QDialog, QDialogButtonBox, QFontComboBox, QSizePolicy
)

from gui_draft import PARAMETERS
from osu_io.parser import parse_osu
from osu_io.writer import write_osu
from transformer import available_transformations, transform, transform_groups

PLAYFIELD_WIDTH = 512
PLAYFIELD_HEIGHT = 384


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
PARAMETERS.setdefault("drawn_path", [{"key":"chunk_size","label":"Notes per Drawing","type":"int","min":2,"max":4096,"default":256},{"key":"reverse","label":"Direction","type":"choice","choices":[("Top to Bottom / Left to Right",False),("Top to Bottom / Right to Left",True)],"default":False}])
if not any(item.get("key")=="font_family" for item in PARAMETERS.get("text",[])):
    text_parameters=PARAMETERS.setdefault("text",[])
    insert_at=next((index+1 for index,item in enumerate(text_parameters) if item.get("key")=="text"),0)
    text_parameters.insert(insert_at,{"key":"font_family","label":"Font","type":"font","default":"Segoe UI"})
if not any(item.get("key")=="reverse" for item in PARAMETERS.get("text",[])):
    PARAMETERS["text"].append({"key":"reverse","label":"Direction","type":"choice","choices":[("Top to Bottom / Left to Right",False),("Top to Bottom / Right to Left",True)],"default":False})
GUI_TRANSFORMATIONS=[name for name in ("text","drawn_path","equation") if name in PARAMETERS]+[name for name in available_transformations() if name in PARAMETERS and name not in {"text","drawn_path","equation"}]


@dataclass(frozen=True, slots=True)
class TimingPoint:
    time: float
    beat_length: float


def extract_timing_points(document) -> list[TimingPoint]:
    section = ""
    output: list[TimingPoint] = []

    for line in document.lines:
        text = line.rstrip("\r\n").strip()

        if text.startswith("[") and text.endswith("]"):
            section = text[1:-1]
            continue

        if section != "TimingPoints" or not text or text.startswith("//"):
            continue

        fields = text.split(",")

        try:
            if (
                len(fields) >= 7
                and int(fields[6]) == 1
                and float(fields[1]) > 0
            ):
                output.append(
                    TimingPoint(
                        time=float(fields[0]),
                        beat_length=float(fields[1]),
                    )
                )
        except ValueError:
            continue

    output.sort(key=lambda item: item.time)
    return output


def active_timing(
    timing_points: list[TimingPoint],
    time_ms: float,
) -> TimingPoint:
    if not timing_points:
        return TimingPoint(0.0, 500.0)

    active = timing_points[0]

    for timing_point in timing_points:
        if timing_point.time <= time_ms:
            active = timing_point
        else:
            break

    return active


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


def format_time(time_ms: int) -> str:
    value = max(0, int(time_ms))
    return f"{value // 60000:02d}.{(value % 60000) // 1000:02d}.{value % 1000:03d}"


def editor_timeline_metadata(document) -> tuple[list[int], int | None]:
    section=""; bookmarks=[]; preview_time=None
    for line in document.lines:
        raw=line.rstrip("\r\n"); stripped=raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section=stripped[1:-1]; continue
        if section!="Editor": continue
        if raw.startswith("Bookmarks:"):
            for value in raw.split(":",1)[1].split(","):
                try: bookmarks.append(max(0,round(float(value.strip()))))
                except ValueError: pass
        elif raw.startswith("PreviewTime:"):
            try:
                value=round(float(raw.split(":",1)[1].strip()))
                preview_time=value if value>=0 else None
            except ValueError: pass
    return sorted(set(bookmarks)),preview_time

def timing_point_lines(document) -> list[str]:
    section = ""; result = []
    for line in document.lines:
        content = line.rstrip("\r\n"); stripped = content.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]; continue
        if section == "TimingPoints" and stripped and not stripped.startswith("//"):
            result.append(content)
    return result


def kiai_ranges(document, duration_ms: int) -> list[tuple[int, int]]:
    changes = []
    for line in timing_point_lines(document):
        fields = line.split(",")
        if len(fields) >= 8:
            try: changes.append((int(float(fields[0])), bool(int(fields[7]))))
            except ValueError: pass
    changes.sort()
    output = []; start = None
    for time_ms, enabled in changes:
        if enabled and start is None: start = time_ms
        elif not enabled and start is not None:
            output.append((start, time_ms)); start = None
    if start is not None: output.append((start, duration_ms))
    return output


def display_name(name: str) -> str:
    return "Drawing" if name=="drawn_path" else name.replace("_"," ").title()


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
            self.text_input.setPlaceholderText("Enter text, for example 67, 日本, or ภาษาไทย")
            self.text_input.textChanged.connect(self.changed)
            layout.addWidget(self.text_input, 1)
            self.choice = None; self.slider = None; self.spin = None
            return
        if definition["type"] == "choice":
            self.choice_group=QButtonGroup(self); self.choice_group.setExclusive(True); self.choice_buttons={}
            for label,value in definition["choices"]:
                button=QPushButton(label); button.setCheckable(True); button.setProperty("choice_value",value)
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
            random_button = QPushButton("Random Seed")
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
        for button in (decrease,increase):button.setFixedSize(28,28);button.setAutoRepeat(True);button.setFocusPolicy(Qt.NoFocus);button.setToolTip("Adjust by 0.01")
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
        super().__init__(parent);self.accepted_points=[];self.setWindowTitle("Drawing");self.resize(780,560)
        icon=application_icon()
        if not icon.isNull():self.setWindowIcon(icon)
        layout=QVBoxLayout(self);label=QLabel("Draw one or more strokes. Notes are placed top-to-bottom, then horizontally within each row. Ctrl+Z: undo, Ctrl+Y: redo.");label.setWordWrap(True);layout.addWidget(label)
        self.surface=DrawingSurface();layout.addWidget(self.surface,1)
        row=QHBoxLayout();undo_button=QPushButton("Undo");redo_button=QPushButton("Redo");clear_button=QPushButton("Clear");undo_button.clicked.connect(self.surface.undo);redo_button.clicked.connect(self.surface.redo);clear_button.clicked.connect(self.surface.clear);row.addWidget(undo_button);row.addWidget(redo_button);row.addWidget(clear_button);layout.addLayout(row)
        self.undo_shortcut=QShortcut(QKeySequence("Ctrl+Z"),self);self.undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut);self.undo_shortcut.activated.connect(self.surface.undo)
        self.redo_shortcut=QShortcut(QKeySequence("Ctrl+Y"),self);self.redo_shortcut.setContext(Qt.WidgetWithChildrenShortcut);self.redo_shortcut.activated.connect(self.surface.redo)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def accept(self):
        points=self.surface.sampled_points()
        if len(points)<2:
            QMessageBox.information(self,"Drawing","Draw at least one stroke before pressing OK.")
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

    def __init__(self) -> None:
        super().__init__()

        self.notes = []
        self.note_times: list[int] = []
        self.timing_points: list[TimingPoint] = []
        self.selected: set[int] = set()

        self.current_time = 0.0
        self.window_ms = 2000.0
        self.snap_divisor = 4

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
        self.don_brush = QColor(255, 65, 30, 180)
        self.kat_brush = QColor(55, 145, 255, 180)
        self.normal_note_pen = QPen(QColor(255, 255, 255, 220), 2)
        self.selected_note_pen = QPen(QColor(255, 220, 110, 235), 3)
        self.baseline_pen = QPen(QColor("#7a8492"), 2)
        self.cursor_pen = QPen(QColor("#ffffff"), 3)

        self.setMinimumHeight(180)
        self.setFocusPolicy(Qt.StrongFocus)

    def load_document(self, document) -> None:
        self.notes = document.hit_objects
        self.note_times = [note.time for note in self.notes]
        self.timing_points = extract_timing_points(document)
        self.current_time = float(
            self.notes[0].time if self.notes else 0
        )
        self.selected.clear()
        self.update()

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
        values = (1, 2, 3, 4, 5, 6, 7, 8, 12, 16)
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

            for _ in range(abs(steps) * 4):
                timing = active_timing(
                    self.timing_points,
                    self.current_time,
                )
                self.current_time = (
                    snap_time(
                        self.timing_points,
                        self.current_time,
                        divisor,
                    )
                    + seek_direction * timing.beat_length / divisor
                )

            self.current_time = max(0.0, self.current_time)
            self.seek_requested.emit(round(self.current_time))
            self.update()

        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button()==Qt.LeftButton:
            self.drag_start_x=event.position().x(); self.drag_mouse_x=self.drag_start_x
            self.drag_anchor_time=self.time_for_x(self.drag_start_x); self.grabMouse(); self.auto_scroll_timer.start(); self.update()
    def _update_drag_selection(self) -> None:
        if self.drag_anchor_time is None: return
        a,b=sorted((self.drag_anchor_time,self.time_for_x(self.drag_mouse_x)))
        first,last=bisect_left(self.note_times,a),bisect_right(self.note_times,b)
        self.selected={n.original_index for n in self.notes[first:last]}; self.selection_changed.emit(set(self.selected)); self.update()
    def mouseMoveEvent(self,event) -> None:
        if self.drag_anchor_time is not None: self.drag_mouse_x=event.position().x(); self._update_drag_selection()
    def _auto_scroll_selection(self) -> None:
        if self.drag_anchor_time is None: self.auto_scroll_timer.stop(); return
        overflow=self.drag_mouse_x if self.drag_mouse_x<0 else self.drag_mouse_x-self.width() if self.drag_mouse_x>self.width() else 0.0
        if not overflow: return
        amount=min(4.0,abs(overflow)/80.0); self.current_time=max(0.0,self.current_time+(-1 if overflow<0 else 1)*(8.0+22.0*amount*amount))
        self.seek_requested.emit(round(self.current_time)); self._update_drag_selection()
    def mouseReleaseEvent(self,event) -> None:
        if self.drag_anchor_time is None: return
        self.auto_scroll_timer.stop(); self.releaseMouse()
        if abs(event.position().x()-(self.drag_start_x or 0.0))<5:
            self.current_time=max(0.0,snap_time(self.timing_points,self.time_for_x(event.position().x()),self.snap_divisor)); self.seek_requested.emit(round(self.current_time))
        self.drag_start_x=None; self.drag_anchor_time=None; self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.selected.clear()
            self.selection_changed.emit(set())
            self.update()
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

        snap_length = timing.beat_length / divisor

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

            snap_index = round(
                (tick_time - timing.time)
                / snap_length
            )

            position_in_beat = snap_index % divisor

            if position_in_beat == 0:
                # Whole beat.
                color = QColor("#f2f2f2")
                tick_height = 32
                tick_width = 2

            elif (
                divisor % 2 == 0
                and position_in_beat == divisor // 2
            ):
                # Half beat.
                color = QColor("#ff5151")
                tick_height = 24
                tick_width = 2

            elif (
                divisor % 4 == 0
                and position_in_beat % (divisor // 4) == 0
            ):
                # Quarter beat.
                color = QColor("#4b9cff")
                tick_height = 18
                tick_width = 1

            elif (
                divisor % 8 == 0
                and position_in_beat % (divisor // 8) == 0
            ):
                # Eighth beat.
                color = QColor("#f2d34f")
                tick_height = 12
                tick_width = 1

            elif divisor % 3 == 0:
                color = QColor("#b578ff")
                tick_height = 16
                tick_width = 1

            elif divisor % 5 == 0:
                color = QColor("#f2d34f")
                tick_height = 14
                tick_width = 1

            elif divisor % 7 == 0:
                color = QColor("#55d6be")
                tick_height = 12
                tick_width = 1

            else:
                color = QColor("#738098")
                tick_height = 10
                tick_width = 1

            x = round(self.x_for_time(tick_time))

            painter.setPen(QPen(color, tick_width))
            painter.drawLine(
                x,
                baseline_y,
                x,
                baseline_y - tick_height,
            )

            tick_time += snap_length

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#151b24"))

        baseline_y = self.height() // 2 + 34
        normal_note_radius = 31
        finisher_note_radius = 42

        self._draw_snap_grid(painter, baseline_y)

        painter.setPen(self.baseline_pen)
        painter.drawLine(0, baseline_y, self.width(), baseline_y)

        start_time = self.current_time - self.window_ms / 2
        end_time = self.current_time + self.window_ms / 2

        # Only visit notes inside the visible window instead of scanning the
        # complete beatmap on every audio-position update.
        first_visible = bisect_left(self.note_times, start_time)
        after_last_visible = bisect_right(self.note_times, end_time)

        if self.drag_anchor_time is not None:
            anchor_x=self.x_for_time(self.drag_anchor_time); current_x=max(0.0,min(float(self.width()),self.drag_mouse_x))
            left,width=min(anchor_x,current_x),abs(current_x-anchor_x)
            painter.fillRect(QRectF(left,0,width,self.height()),QColor(190,195,205,28))
            painter.setPen(QPen(QColor(220,225,235,70),1)); painter.drawRect(QRectF(left,0,width,self.height()-1))
        painter.setRenderHint(QPainter.Antialiasing, True)

        for note in self.notes[first_visible:after_last_visible]:
            x = self.x_for_time(note.time)
            radius = (
                finisher_note_radius
                if note.is_finisher
                else normal_note_radius
            )
            note_center_y = baseline_y - radius

            # Semi-transparent fill keeps the snap grid visible through notes.
            painter.setBrush(self.kat_brush if note.is_kat else self.don_brush)
            painter.setPen(self.selected_note_pen if note.original_index in self.selected else self.normal_note_pen)
            painter.drawEllipse(
                QPointF(x, note_center_y),
                radius,
                radius,
            )

        cursor_x = self.width() // 2
        painter.setPen(self.cursor_pen)
        painter.drawLine(
            cursor_x,
            baseline_y - 92,
            cursor_x,
            baseline_y + 7,
        )


class TimingOverviewBar(QWidget):
    seek_requested = Signal(int)
    def __init__(self) -> None:
        super().__init__()
        self.duration_ms=1; self.current_time=0; self.viewport_start=0; self.viewport_end=0
        self.kiai=[]; self.timing_markers=[]; self.bookmarks=[]; self.preview_time=None; self.dragging=False
        self.setFixedHeight(28); self.setCursor(Qt.PointingHandCursor)
    def load_document(self,document,duration_ms:int)->None:
        self.duration_ms=max(duration_ms,document.hit_objects[-1].time if document.hit_objects else 1,1)
        self.kiai=kiai_ranges(document,self.duration_ms); self.timing_markers=[]
        self.bookmarks,self.preview_time=editor_timeline_metadata(document)
        for line in timing_point_lines(document):
            fields=line.split(",")
            try:
                if len(fields)>=7:self.timing_markers.append((round(float(fields[0])),int(fields[6])==1))
            except ValueError:pass
        self.update()
    def set_duration(self,value:int)->None:
        if value>0:self.duration_ms=value;self.update()
    def set_time(self,value:int)->None:self.current_time=max(0,min(value,self.duration_ms));self.update()
    def set_viewport(self,center:int,window_ms:float)->None:
        self.viewport_start=max(0,center-window_ms/2);self.viewport_end=min(self.duration_ms,center+window_ms/2);self.update()
    def _seek(self,x:float)->None:self.seek_requested.emit(round(max(0,min(1,x/max(1,self.width())))*self.duration_ms))
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
        groups={}
        for time_ms,uninherited in self.timing_markers:groups.setdefault(time_ms,set()).add(uninherited)
        for time_ms,kinds in groups.items():
            x=round(time_ms/self.duration_ms*self.width())
            color=QColor("#ffd400") if len(kinds)>1 else QColor("#ff4545") if True in kinds else QColor("#45d65a")
            painter.setPen(QPen(color,1));painter.drawLine(x,1,x,center-1)
        painter.setPen(QPen(QColor("#3e9bff"),1))
        for bookmark in self.bookmarks:
            x=round(bookmark/self.duration_ms*self.width());painter.drawLine(x,center+1,x,self.height()-2)
        if self.preview_time is not None:
            x=round(self.preview_time/self.duration_ms*self.width());painter.setPen(QPen(QColor("#ffd400"),1));painter.drawLine(x,center+1,x,self.height()-2)
        if self.viewport_end>self.viewport_start:
            x=self.viewport_start/self.duration_ms*self.width();w=(self.viewport_end-self.viewport_start)/self.duration_ms*self.width()
            painter.setPen(QPen(QColor(255,255,255,65),1));painter.setBrush(Qt.NoBrush);painter.drawRect(QRectF(x,1,w,self.height()-2))
        x=round(self.current_time/self.duration_ms*self.width());painter.setPen(QPen(QColor("#ffffff"),1));painter.drawLine(x,0,x,self.height())


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
        if points:
            for i,point in enumerate(points):
                section_end=points[i+1].time if i+1<len(points) else self.duration_ms
                cursor=point.time
                while cursor<section_end:
                    end=min(section_end,cursor+point.beat_length*4)
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.app_name="Taiko Fancy Arranger";self.setWindowTitle(self.app_name)
        icon=application_icon()
        if not icon.isNull():self.setWindowIcon(icon)
        QApplication.instance().setFont(
            QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        )
        self.resize(1360, 900)

        self.document = None
        self.source_path: Path | None = None
        self.song_difficulties: list[Path] = []
        self.current_background_path: Path | None = None

        self.original_positions: dict[int, tuple[int, int]] = {}
        self.applied_positions: dict[int, tuple[int, int]] = {}
        self.preview_positions: dict[int, tuple[int, int]] = {}
        self.selected: set[int] = set()
        self.preview_offsets = {"all": [0.0, 0.0], "don": [0.0, 0.0], "kat": [0.0, 0.0]}

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

        self.gameplay_frame_clock = QElapsedTimer()
        self.gameplay_frame_clock.start()
        self.gameplay_frame_interval_ns = 8_333_333

        self.gameplay_render_timer = QTimer(self)
        self.gameplay_render_timer.setTimerType(Qt.PreciseTimer)
        self.gameplay_render_timer.setInterval(4)
        self.gameplay_render_timer.timeout.connect(self._render_gameplay_frame)
        self.gameplay_render_timer.start()

        self.info_timer = QTimer(self)
        self.info_timer.setInterval(16)
        self.info_timer.timeout.connect(self._update_timeline_info)
        self.info_timer.start()

        self.play_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.play_shortcut.setContext(Qt.ApplicationShortcut)
        self.play_shortcut.activated.connect(self._toggle_playback_from_shortcut)

        self.undo_stack=[]; self.redo_stack=[]; self.preview_cache={}; self.commit_revision=0
        self.undo_shortcut=QShortcut(QKeySequence("Ctrl+Z"),self); self.undo_shortcut.setContext(Qt.ApplicationShortcut); self.undo_shortcut.activated.connect(self.undo)
        self.redo_shortcut=QShortcut(QKeySequence("Ctrl+Y"),self); self.redo_shortcut.setContext(Qt.ApplicationShortcut); self.redo_shortcut.activated.connect(self.redo)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(70)
        self.preview_timer.timeout.connect(self.update_preview)

        self._build_ui()
        self._rebuild_control_tabs()
        QApplication.instance().installEventFilter(self)

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
                    self.timeline._change_snap_from_wheel(delta)
                event.accept()
                return True
        if event.type()==QEvent.MouseButtonPress and hasattr(self,"timeline") and self.selected:
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
        if self.document is not None:
            self.toggle_playback()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)
        self.setCentralWidget(central)

        toolbar = QHBoxLayout()

        open_button = QPushButton("Open .osu")
        open_button.clicked.connect(self.open_map)
        open_button.setFocusPolicy(Qt.NoFocus)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)
        self.play_button.setFocusPolicy(Qt.NoFocus)

        self.reset_button = QPushButton("Reset applied transforms")
        self.reset_button.clicked.connect(self.reset_applied)
        self.reset_button.setFocusPolicy(Qt.NoFocus)

        self.export_button = QPushButton("Export applied map")
        self.export_button.clicked.connect(self.export_map)
        self.export_button.setFocusPolicy(Qt.NoFocus)

        for button in (
            self.play_button,
            self.reset_button,
            self.export_button,
        ):
            button.setEnabled(False)

        toolbar.addWidget(open_button)
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.setMinimumWidth(260)
        self.difficulty_combo.setEnabled(False)
        self.difficulty_combo.currentIndexChanged.connect(self._difficulty_changed)
        toolbar.addWidget(QLabel("Difficulty"))
        toolbar.addWidget(self.difficulty_combo)

        toolbar.addWidget(self.play_button)
        toolbar.addWidget(self.reset_button)
        toolbar.addWidget(self.export_button)
        self.approach_rate_control=DifficultyValueControl("AR",10.0,"Approach Rate: 0 is slowest, 10 is fastest. Export default is 10.00.")
        self.circle_size_control=DifficultyValueControl("CS",7.0,"Circle Size: 0 is biggest, 10 is smallest. Export default is 7.00.")
        toolbar.addWidget(self.approach_rate_control)
        toolbar.addWidget(self.circle_size_control)
        toolbar.addStretch(1)

        self.status = ElidedLabel("Open a map to begin.")
        self.status.setMinimumWidth(100)
        self.status.setMaximumWidth(360)
        self.status.setAlignment(
            Qt.AlignRight | Qt.AlignVCenter
        )

        toolbar.addWidget(self.status, 0)

        self.status = ElidedLabel("Open a map to begin.")
        self.status.setMaximumWidth(360)
        self.status.setMinimumWidth(100)
        self.status.setAlignment(
            Qt.AlignRight | Qt.AlignVCenter
        )
        toolbar.addWidget(self.status)
        root.addLayout(toolbar)

        # Upper workspace only: transformation preview and its controls.
        # The gameplay timeline is added to root below this splitter so it
        # spans the complete application width beneath both columns.
        workspace_splitter = QSplitter(Qt.Horizontal)

        self.canvas = TransformCanvas()
        self.canvas.background_dropped.connect(self._background_dropped)
        self.canvas.drag_offset_requested.connect(self._canvas_dragged)
        workspace_splitter.addWidget(self.canvas)

        right = QWidget()
        self.transform_controls_panel = right
        right.setMinimumWidth(390)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)

        self.background_opacity_control = ParameterControl(
            {"key": "background_opacity", "label": "Background Opacity", "type": "int", "min": 0, "max": 100, "default": 55}
        )
        self.background_opacity_control.changed.connect(
            lambda: self.canvas.set_background_opacity(int(self.background_opacity_control.value()))
        )
        right_layout.addWidget(QLabel("Background Opacity"))
        right_layout.addWidget(self.background_opacity_control)

        right_layout.addWidget(QLabel("Transformation mode"))

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["All Notes", "Split Don / Kat"])
        self.mode_combo.currentTextChanged.connect(
            self._rebuild_control_tabs
        )
        right_layout.addWidget(self.mode_combo)
        self.swap_don_kat_button = QPushButton("Swap Don ↔ Kat")
        self.swap_don_kat_button.setVisible(False)
        self.swap_don_kat_button.setToolTip("Swap transformation, parameters, and position between Don and Kat")
        self.swap_don_kat_button.clicked.connect(self._swap_don_kat_transformations)
        right_layout.addWidget(self.swap_don_kat_button)
        self.mode_combo.currentTextChanged.connect(
            lambda mode: self.swap_don_kat_button.setVisible(mode == "Split Don / Kat")
        )

        self.control_tabs = QTabWidget()
        right_layout.addWidget(self.control_tabs, 1)

        self.apply_button = QPushButton("Transform selected notes")
        self.apply_button.clicked.connect(self.apply_selection)
        self.apply_button.setFocusPolicy(Qt.NoFocus)
        self.apply_button.setEnabled(False)
        right_layout.addWidget(self.apply_button)
        self.apply_original_button=QPushButton("Apply all changes to original file")
        self.apply_original_button.clicked.connect(self.apply_to_original_file)
        self.apply_original_button.setFocusPolicy(Qt.NoFocus); self.apply_original_button.setEnabled(False)
        right_layout.addWidget(self.apply_original_button)

        workspace_splitter.addWidget(right)
        workspace_splitter.setSizes([960, 400])
        root.addWidget(workspace_splitter, 1)

        timeline_controls = QHBoxLayout()
        timeline_controls.addWidget(QLabel("Beat snap"))

        self.snap_combo = QComboBox()
        for divisor in (1, 2, 3, 4, 5, 6, 7, 8, 12, 16):
            self.snap_combo.addItem(f"1/{divisor}", divisor)
        self.snap_combo.setCurrentText("1/4")
        self.snap_combo.currentIndexChanged.connect(self._snap_changed)
        timeline_controls.addWidget(self.snap_combo)

        self.timeline_info = QLabel(
            "Duration: --   |   Position: --   |   Snap: 1/4   |   "
            "Wheel: seek   |   Shift+wheel: 1 beat   |   Ctrl+wheel: zoom"
        )
        self.timeline_info.setStyleSheet("color: #aeb8c5; padding-left: 10px;")
        timeline_controls.addWidget(self.timeline_info, 1)
        root.addLayout(timeline_controls)

        self.timeline = TimelineGameplay()
        self.timeline.selection_changed.connect(self._selection_changed)
        self.timeline.selection_finalized.connect(self._selection_finalized)
        self.timeline.seek_requested.connect(self.seek_audio)
        self.timeline.snap_changed_by_wheel.connect(self._snap_changed_by_wheel)
        root.addWidget(self.timeline, 0)
        timeline_row=QHBoxLayout();timeline_row.setSpacing(5)
        time_area=QWidget();time_area.setFixedWidth(150)
        time_area_layout=QHBoxLayout(time_area);time_area_layout.setContentsMargins(0,0,0,0);time_area_layout.addStretch()
        self.timeline_time=QLabel("00:00:000");self.timeline_time.setAlignment(Qt.AlignCenter);self.timeline_time.setStyleSheet("font-size:18px;font-weight:700;");time_area_layout.addWidget(self.timeline_time)
        time_area_layout.addStretch();timeline_row.addWidget(time_area)
        self.timing_bar=TimingOverviewBar();self.timing_bar.seek_requested.connect(self.seek_audio);timeline_row.addWidget(self.timing_bar,1)
        self.timeline_play_button=QPushButton("▶");self.timeline_play_button.setFixedWidth(42);self.timeline_play_button.clicked.connect(self.toggle_playback);timeline_row.addWidget(self.timeline_play_button)
        timeline_row.addWidget(QLabel("Playback Rate"))
        self.playback_speed_buttons=[]
        for label,rate in (("25%",.25),("50%",.5),("75%",.75),("100%",1.0)):
            button=QPushButton(label);button.setCheckable(True);button.setFixedWidth(52);button.setProperty("playbackRate",rate)
            button.clicked.connect(lambda checked=False,r=rate:self._change_playback_speed(r));self.playback_speed_buttons.append(button);timeline_row.addWidget(button)
        self._change_playback_speed(1.0)
        root.addLayout(timeline_row)
        self.overview=DensityOverview(); self.overview.seek_requested.connect(self.seek_audio); root.addWidget(self.overview)

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
                padding: 8px 11px;
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
                padding: 8px 13px;
            }
            QTabBar::tab:selected { background: #f3a6bd; color: #17191f; }
            """
        )

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
        if self.mode_combo.currentText() != "Split Don / Kat":
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
        self.status.setText("Swapped Don and Kat transformations.")

    def _control_page(self, group: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        combo = QComboBox()
        combo.addItem("None", "")
        for name in GUI_TRANSFORMATIONS: combo.addItem(display_name(name), name)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        scroll.setWidget(form_widget)

        layout.addWidget(combo)
        layout.addWidget(scroll, 1)
        equation_keyboard_button = QPushButton("⌨")
        equation_keyboard_button.setToolTip("Show or hide equation keyboard")
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
                form.addRow(definition["label"], control)

            if transformation_name=="drawn_path":
                draw_button=QPushButton("Open Drawing Window")
                def open_drawing(active_group=group):
                    self.drawing_dialog_active=True
                    try:
                        dialog=DrawingDialog(self)
                        if dialog.exec()==QDialog.Accepted:
                            points=list(dialog.accepted_points)
                            self.last_drawing_points=points
                            self.drawing_points[active_group]=points
                            for group_name,page in zip(("all","don","kat"),self.group_pages):
                                if str(page.combo.currentData() or "")=="drawn_path":self.drawing_points[group_name]=points
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
                form.addRow(label, position_control)

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

    def _rebuild_control_tabs(self) -> None:
        self.control_tabs.clear()

        if self.mode_combo.currentText() == "All Notes":
            self.control_tabs.addTab(
                self._control_page("all"),
                "All",
            )
        else:
            self.control_tabs.addTab(
                self._control_page("don"),
                "Don",
            )
            self.control_tabs.addTab(
                self._control_page("kat"),
                "Kat",
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
            self, "Open osu! beatmap", "", "osu! beatmaps (*.osu)"
        )
        if filename:
            self._load_map_path(Path(filename).resolve(), refresh_difficulties=True)

    def _load_map_path(self, source_path: Path, refresh_difficulties: bool = False) -> None:
        try:
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
        except Exception as error:
            QMessageBox.critical(self, "Open failed", str(error))
            return

        self.document = document
        self.source_path = source_path
        self.setWindowTitle(f"{source_path.name} - {self.app_name}")
        self.original_positions = {
            note.original_index: (note.x, note.y)
            for note in document.hit_objects
        }
        self.applied_positions = dict(self.original_positions)
        self.preview_positions = dict(self.applied_positions)
        self.selected.clear(); self.undo_stack.clear(); self.redo_stack.clear(); self.commit_revision=0; self.preview_cache.clear()
        if hasattr(self, "preview_offsets"):
            self.preview_offsets = {"all": [0.0, 0.0], "don": [0.0, 0.0], "kat": [0.0, 0.0]}

        self.timeline.load_document(document)
        self.timeline.set_snap_divisor(int(self.snap_combo.currentData()))
        self.player.setSource(QUrl.fromLocalFile(str(audio_path)))
        duration_hint=document.hit_objects[-1].time if document.hit_objects else 1
        self.timing_bar.load_document(document,duration_hint)
        self.overview.load_document(document,duration_hint)

        background_name = extract_background_filename(document)
        background_path = None
        if background_name:
            try:
                candidate = resolve_song_asset(source_path.parent, background_name, {".jpg", ".jpeg", ".png", ".webp", ".bmp"})
                background_path = candidate if candidate.is_file() else None
            except ValueError:
                background_path = None
        self.current_background_path = background_path
        self.canvas.set_background(str(self.current_background_path) if self.current_background_path else None)

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

        for button in (self.play_button, self.apply_button, self.reset_button, self.export_button, self.apply_original_button):
            button.setEnabled(True)
        self.refresh_canvas()
        self.status.setText(f"{source_path.name} | drag notes, preview, then Apply to selection")

    def _difficulty_changed(self, index: int) -> None:
        if index < 0:
            return
        value = self.difficulty_combo.itemData(index)
        if value:
            candidate = Path(value).resolve()
            if candidate != self.source_path:
                self.player.stop()
                self._load_map_path(candidate, refresh_difficulties=False)

    def _background_dropped(self, dropped_path: str) -> None:
        if self.document is None or self.source_path is None:
            QMessageBox.information(self, "Open a map first", "Open a beatmap before adding a background.")
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
                QMessageBox.critical(self, "Background copy failed", str(error))
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
        if any(self._spec(i, group)[0] for i,group in enumerate(("all",) if self.mode_combo.currentText()=="All Notes" else ("don","kat"))): self.schedule_preview()

    def schedule_preview(self) -> None:
        if self.document is not None:
            self.preview_timer.start()

    def _calculate_selected_transform(
        self,
    ) -> dict[int, tuple[int, int]]:
        indexes = sorted(self.selected)

        if not indexes:
            return {}

        if self.mode_combo.currentText() == "All Notes":
            transformation_name, params = self._spec(
                0,
                "all",
            )

            if not transformation_name: return {}
            if transformation_name in {"taiko","vertical_taiko"}:
                params.update({"note_times":{n.original_index:n.time for n in self.document.hit_objects},"timing_points":timing_point_lines(self.document),"timing_mode":"filtered","anchor_mode":"selection_start"})
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
            if name in {"taiko","vertical_taiko"}: params.update({"note_times":{n.original_index:n.time for n in self.document.hit_objects},"timing_points":timing_point_lines(self.document),"timing_mode":"filtered","anchor_mode":"selection_start"})

        groups={}
        if don_name: groups["don"]={"transformation_name":don_name,"selected_note_indexes":don_indexes,"params":don_params}
        if kat_name: groups["kat"]={"transformation_name":kat_name,"selected_note_indexes":kat_indexes,"params":kat_params}
        return transform_groups(groups)


    def _indices_for_drag_group(self, clicked_group: str) -> set[int]:
        if self.mode_combo.currentText() == "All Notes":
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
            offset_key = "all" if self.mode_combo.currentText() == "All Notes" else group
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
        groups = ("all",) if self.mode_combo.currentText() != "Split Don / Kat" else (offset_key,)
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
        offset_key = "all" if self.mode_combo.currentText() == "All Notes" else clicked_group
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
            if self.mode_combo.currentText()=="All Notes": specs=[self._spec(0,"all")]
            else: specs=[self._spec(0,"don"),self._spec(1,"kat")]
            if any(name=="drawn_path" and len(params.get("points",[]))<2 for name,params in specs):
                self.preview_positions=dict(self.applied_positions);self.refresh_canvas();self.status_label.setText("Open Drawing Window and draw a shape to preview.");return
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
                "Select notes in the bottom timeline first."
            )
            return

        try:
            changed_positions = self._apply_preview_offsets(
                self._calculate_selected_transform()
            )
        except Exception as error:
            QMessageBox.warning(
                self,
                "Apply failed",
                str(error),
            )
            return

        self.undo_stack.append(dict(self.applied_positions)); self.redo_stack.clear(); self.commit_revision+=1; self.preview_cache.clear()
        self.applied_positions.update(changed_positions)
        self.preview_offsets = {"all": [0.0, 0.0], "don": [0.0, 0.0], "kat": [0.0, 0.0]}
        self.preview_positions = dict(self.applied_positions)
        self.refresh_canvas()

        self.status.setText(
            f"Applied transformation to "
            f"{len(changed_positions)} notes."
        )

    def reset_applied(self) -> None:
        self.undo_stack.append(dict(self.applied_positions)); self.redo_stack.clear(); self.commit_revision+=1; self.preview_cache.clear()
        self.applied_positions = dict(self.original_positions)
        self.preview_positions = dict(self.applied_positions)
        self.refresh_canvas()

        self.status.setText(
            "All applied transformations reset."
        )

    def undo(self)->None:
        if not self.undo_stack:return
        self.redo_stack.append(dict(self.applied_positions));self.applied_positions=self.undo_stack.pop();self.preview_positions=dict(self.applied_positions);self.commit_revision+=1;self.preview_cache.clear();self.refresh_canvas()
    def redo(self)->None:
        if not self.redo_stack:return
        self.undo_stack.append(dict(self.applied_positions));self.applied_positions=self.redo_stack.pop();self.preview_positions=dict(self.applied_positions);self.commit_revision+=1;self.preview_cache.clear();self.refresh_canvas()

    def _change_playback_speed(self,rate:float)->None:
        rate=float(rate);self.player.setPlaybackRate(rate)
        self.audio_anchor_position=self.player.position();self.audio_anchor_clock.restart()
        for button in getattr(self,"playback_speed_buttons",[]):
            active=abs(float(button.property("playbackRate"))-rate)<0.0001
            button.blockSignals(True);button.setChecked(active);button.blockSignals(False)

    def toggle_playback(self) -> None:
        if (
            self.player.playbackState()
            == QMediaPlayer.PlayingState
        ):
            self.player.pause()
            self.timeline.is_playing=False
            self.play_button.setText("Play")
            if hasattr(self,"timeline_play_button"):self.timeline_play_button.setText("▶")
        else:
            self.audio_anchor_position=self.player.position(); self.audio_anchor_clock.restart()
            self.timeline.is_playing=True
            self.player.play()
            self.play_button.setText("Pause")
            if hasattr(self,"timeline_play_button"):self.timeline_play_button.setText("❚❚")

    def seek_audio(self, position: int) -> None:
        position=max(0,position); self.audio_anchor_position=position; self.latest_audio_position=position; self.audio_anchor_clock.restart()
        self.player.setPosition(position); self.timeline.set_time(position,force=True); self.timing_bar.set_time(position); self.overview.set_time(position)

    def _update_timeline_info(self) -> None:
        if not hasattr(self, "timeline_info"):
            return
        duration = max(0, self.player.duration())
        position = max(0, round(self.timeline.current_time) if hasattr(self,"timeline") else self.player.position())
        if hasattr(self,"overview"): self.overview.set_duration(duration)
        if hasattr(self,"timing_bar"): self.timing_bar.set_duration(duration)
        if hasattr(self,"timeline_time"):
            value=max(0,int(position));self.timeline_time.setText(f"{value//60000:02d}:{(value%60000)//1000:02d}:{value%1000:03d}")
        self.timeline_info.setText(
            f"Duration: {format_time(duration)}   |   "
            f"Position: {format_time(position)}   |   "
            f"Snap: 1/{int(self.snap_combo.currentData())}   |   "
            "Wheel: 4 snaps   |   Shift+wheel: 4 beats   |   Ctrl+wheel: zoom"
        )

    def _player_position_changed(self, position: int) -> None:
        self.latest_audio_position=position; self.audio_anchor_position=position; self.audio_anchor_clock.restart()
    def _render_gameplay_frame(self) -> None:
        if self.gameplay_frame_clock.nsecsElapsed() < self.gameplay_frame_interval_ns:
            return
        self.gameplay_frame_clock.restart()
        if self.document is None:return
        position=self.audio_anchor_position
        if self.player.playbackState()==QMediaPlayer.PlayingState:
            position+=round(self.audio_anchor_clock.elapsed()*self.player.playbackRate())
        duration=self.player.duration()
        if duration>0:position=min(position,duration)
        self.timeline.set_time(position,force=True)
        self.timing_bar.set_time(position)
        self.timing_bar.set_viewport(position,self.timeline.window_ms)
        self.overview.set_time(position)
        self.overview.set_viewport(position,self.timeline.window_ms)

    def apply_to_original_file(self)->None:
        if self.document is None or self.source_path is None:return
        answer=QMessageBox.question(self,"Overwrite original beatmap?","This writes every committed transformation to the original .osu file. Continue?",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        try:
            for note in self.document.hit_objects:note.x,note.y=self.applied_positions[note.original_index]
            write_osu(self.document,self.source_path,self.document.version,allow_overwrite_source=True,create_backup=True,force_ar=self.approach_rate_control.value(),force_cs=self.circle_size_control.value())
        except Exception as error:QMessageBox.critical(self,"Write failed",str(error));return
        QMessageBox.information(self,"Original updated","Applied all committed changes to:\n"+str(self.source_path))

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
            "Export applied map",
            str(candidate),
            "osu! beatmaps (*.osu)",
        )

        if not destination:
            return

        destination_path = Path(destination).resolve()

        if destination_path == self.source_path:
            QMessageBox.warning(
                self,
                "Source protected",
                "Choose a different filename.",
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
                "Export failed",
                str(error),
            )
            return

        QMessageBox.information(
            self,
            "Export complete",
            f"Created:\n{destination_path}",
        )


def main() -> None:
    app=QApplication(sys.argv)
    icon=application_icon()
    if not icon.isNull():app.setWindowIcon(icon)
    window = MainWindow()
    window.showMaximized()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()