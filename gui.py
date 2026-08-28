from __future__ import annotations

import math
import os
import random
import csv
import shutil
import sys
from bisect import bisect_left, bisect_right
from contextlib import contextmanager
from itertools import count as count_from
from pathlib import Path
from time import perf_counter
from typing import Any

from PySide6.QtCore import (
    QElapsedTimer, QEvent, QObject, QPointF, QPropertyAnimation, QRect, QRectF, QSize, QStandardPaths, Qt, QTimer,
    QUrl, Signal,
)
from PySide6.QtGui import QImageReader, QColor, QFont, QFontDatabase, QFontMetrics, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea,
    QSlider, QSpacerItem, QSpinBox, QSplitter, QStackedWidget, QStyle, QStyleOptionButton, QTabWidget, QToolButton, QVBoxLayout, QWidget, QDialog, QDialogButtonBox, QFontComboBox, QSizePolicy
)

from parameters import PARAMETERS
from i18n import install_translator, tr
from image_trace_dialog import ImageTraceDialog
from model.commands import (
    CompositeCommand, EditTimingPoint, InsertHitObjects, InsertTimingPoints,
    MoveNotes, RemoveHitObjects, RemoveTimingPoints, SetNoteFields, SetNotePositions,
)
from model.editor_state import DifficultyState
from model.hit_object import HITSOUND_CLAP, HITSOUND_FINISH, HitObject, TYPE_CIRCLE, TYPE_NEW_COMBO, TYPE_SLIDER, TYPE_SPINNER
from settings import (
    APPLICATION_NAME, ORGANIZATION_NAME, SettingsManager, ShortcutDefinition, ShortcutRegistry,
    register_shortcut_definitions, should_ignore_shortcut_focus,
)
from settings_dialog import SettingsDialog
import updater
from osu_io.parser import parse_osu
from osu_io.timing import (
    EFFECT_KIAI,
    EFFECT_OMIT_FIRST_BARLINE,
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
from time_axis import (
    KIAI_OPEN_END_MS, SNAP_DIVISORS, TimeAxisMixin, osu_round, snap_time, wheel_seek_time,
)
from gimmick_session import (
    DEFAULT_RED_LINE_BPM,
    DEFAULT_SHINY_COUNT,
    GimmickConfig,
    GimmickConfigError,
    GimmickPairing,
    barline_note as gimmick_barline_note,
    base_bpm_at,
    fake_slider as gimmick_fake_slider,
    red_line as gimmick_red_line,
    sv_restore_point,
    gimmick_path_for,
    gimmick_version_for,
    index_key,
    difficulty_setting,
    load_index,
    looks_gimmicked,
    oscillating_series,
    carry_active_state,
    save_index,
    shiny_collides,
    snapshot_timing,
    spacing_collides,
)
from transformer import available_transformations, transform, transform_groups

PLAYFIELD_WIDTH = 512
PLAYFIELD_HEIGHT = 384

# Ported from osu!(lazer)'s InterpolatingFramedClock.ProcessFrame: every
# *rendered frame* (not every sparse backend report) the displayed clock is
# nudged 1/8 of the way toward the source clock, or snapped to it outright if
# the two have drifted more than AllowableErrorMilliseconds apart. Blending
# every frame instead of only at report time is what makes the correction
# invisible; osu!'s own numbers for both constants are kept rather than
# re-tuned, since they were already chosen for this exact smoothing problem.
POSITION_INTERPOLATION_DIVISOR = 8.0
POSITION_ALLOWABLE_ERROR_MS = 1000.0 / 60.0 * 2.0

# The song-folder scan runs on the UI thread, sliced by time rather than by a
# file count: one frame's worth of work per timer tick, so a folder with a few
# fast files and one with thousands of slow ones both stay responsive.
SCAN_SLICE_SECONDS = 0.008

# page_stack / page_button_group indices.
PAGE_LIBRARY, PAGE_EDITOR, PAGE_GIMMICK, PAGE_FANCY = 0, 1, 2, 3

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


# osu!'s own [Difficulty] SliderMultiplier default, and nothing more: the
# parser now reads the map's real value (`OsuDocument.slider_multiplier`), so
# this is only the fallback for a file with no SliderMultiplier line at all.
# It is not a stand-in for the real number -- the whole length<->duration
# conversion scales linearly with it, so a map authored at 2.0 had every
# drumroll end drawn at 1.4/2.0 of its true length while this was assumed.
SLIDER_MULTIPLIER_ASSUMED = 1.4


def slider_length_for_duration(
    duration_ms: float, beat_length: float, sv: float,
    slider_multiplier: float = SLIDER_MULTIPLIER_ASSUMED,
) -> float:
    """osu!px length that plays for `duration_ms` at the given beat length/SV."""
    if beat_length <= 0 or sv <= 0 or slider_multiplier <= 0:
        return 1.0
    return max(1.0, duration_ms * slider_multiplier * 100.0 * sv / beat_length)


def duration_for_slider_length(
    length: float, beat_length: float, sv: float,
    slider_multiplier: float = SLIDER_MULTIPLIER_ASSUMED,
) -> float:
    """Inverse of slider_length_for_duration, for hit-testing an existing slider's end.

    `length` is the whole path a drumroll travels -- one slide's length times
    its slide count -- because osu! charges the per-slide duration once per
    slide and the two multiplications are the same one.
    """
    if beat_length <= 0 or sv <= 0 or slider_multiplier <= 0:
        return 0.0
    return length / (slider_multiplier * 100.0 * sv) * beat_length


PARAMETERS.setdefault("drawn_path", [{"key":"chunk_size","label":"Notes per Drawing","type":"int","min":2,"max":4096,"default":256},{"key":"reverse","label":"Direction","type":"choice","choices":[("Top to Bottom / Left to Right",False),("Top to Bottom / Right to Left",True)],"default":False}])
if not any(item.get("key")=="font_family" for item in PARAMETERS.get("text",[])):
    text_parameters=PARAMETERS.setdefault("text",[])
    insert_at=next((index+1 for index,item in enumerate(text_parameters) if item.get("key")=="text"),0)
    text_parameters.insert(insert_at,{"key":"font_family","label":"Font","type":"font","default":"Segoe UI"})
if not any(item.get("key")=="reverse" for item in PARAMETERS.get("text",[])):
    PARAMETERS["text"].append({"key":"reverse","label":"Direction","type":"choice","choices":[("Top to Bottom / Left to Right",False),("Top to Bottom / Right to Left",True)],"default":False})
GUI_TRANSFORMATIONS=[name for name in ("text","drawn_path","equation") if name in PARAMETERS]+[name for name in available_transformations() if name in PARAMETERS and name not in {"text","drawn_path","equation"}]


# The compact +/- pair. Three controls build one by hand (`ParameterControl`
# twice, `DifficultyValueControl`, `pink_spin_buttons`) and they now agree on
# how it looks: the window stylesheet's 8px/16px is far too much padding for
# a single glyph, and overriding only `padding` and `font-size` leaves the
# inherited pink background, hover and disabled states alone -- a leaf
# widget's own QPushButton{} rule overrides just the properties it names.
STEP_BUTTON_STYLE = "QPushButton { padding: 2px 6px; font-weight: 700; font-size: 15px; }"


def button_chrome_width(button: QPushButton) -> int:
    """Pixels `button` spends on padding and border before any text at all.

    Measured, not assumed: the gap between a rect and the contents rect the
    button's *own current style* hands back for it, which is where the window
    stylesheet's `padding: 8px 16px` and any per-button override of it
    actually show up. Measured against a rect widened by the label, because
    the buttons this exists for are exactly the ones currently too narrow to
    hold their own padding, and a style clamps the contents rect at zero
    rather than reporting the overflow.
    """
    button.ensurePolished()
    option = QStyleOptionButton()
    option.initFrom(button)
    text_width = button.fontMetrics().horizontalAdvance(button.text())
    option.rect = QRect(0, 0, option.rect.width() + text_width, max(1, option.rect.height()))
    contents = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
    return max(0, option.rect.width() - contents.width())


def button_text_width(button: QPushButton) -> int:
    """The width `button` needs before its own body starts covering its label.

    This is the fix for the typed widths that used to clip. A 28px "+" under
    the app-wide 16px of padding either side has -4px left for the glyph, so
    Qt drew the pink body straight over both ends of it -- the "buttons eat
    their own text at the left and right" report -- and every attempt to nudge
    the number was a guess at a total the stylesheet already decides. Both
    halves are read back instead: the label from the button's font metrics,
    the padding and border from its style. A longer translation, a bigger font
    or a different padding each widen the button by exactly their cost.

    sizeHint() is still the floor, so an icon, a menu indicator or a style
    minimum is never sized away.

    A second cause layers on top of the first for any checkable button: the
    `QPushButton:checked` rule bumps `font-weight` to 700, but this function
    was measuring with the button's own (unchecked, lighter) font -- so the
    width was pinned before the button ever got heavier, and checking it
    clipped the label again. The advance is now measured with the current
    font AND a bold copy of it, and the wider of the two wins.
    """
    metrics_width = button.fontMetrics().horizontalAdvance(button.text())
    bold_font = QFont(button.font())
    bold_font.setWeight(QFont.Weight.Bold)
    bold_width = QFontMetrics(bold_font).horizontalAdvance(button.text())
    return max(
        button.sizeHint().width(),
        max(metrics_width, bold_width) + button_chrome_width(button),
    )


def fit_button_width(button: QPushButton) -> None:
    """Pin `button` to the width its own label needs. See button_text_width."""
    button.setFixedWidth(button_text_width(button))


def step_button(text: str, on_click, parent: QWidget | None = None) -> QPushButton:
    """A compact, glyph-sized +/- button, wired to `on_click`."""
    button = QPushButton(text, parent)
    button.setStyleSheet(STEP_BUTTON_STYLE)
    button.setAutoRepeat(True)
    button.setFocusPolicy(Qt.NoFocus)
    button.clicked.connect(on_click)
    fit_button_width(button)
    return button


def equalize_button_widths(buttons, heights: bool = False, widths: bool = True) -> None:
    """Give every button the width of the widest one.

    Polished first: before the style has been applied a button reports the
    unpadded hint, and the widest label is exactly the one that then no longer
    fits. The width itself comes from `button_text_width`, which measures the
    label and the real padding separately rather than trusting that hint.

    `heights` does the same for the tallest, and is how the tool rows are sized:
    a typed height clipped descenders once the window stylesheet's padding was
    applied, and the hint is the one number that already accounts for the font,
    the padding and the border together.

    `widths=False` keeps every button at its own natural width and equalizes
    only the height. Uniform width is worth having while a row is a handful of
    short labels, and unaffordable once it is not: the gimmick fake slider row
    reached ten buttons, and giving all of them the width of "Multiple Fake
    Slider" -- across all six layers, since they are sized as one set -- came
    to 2798px, which is wider than the monitor. A ragged row that fits beats a
    tidy one you cannot reach the end of.
    """
    for button in buttons:
        button.ensurePolished()
    hints = [button.sizeHint() for button in buttons]
    # button_text_width rather than the hint: the hint is what let the widest
    # label clip, since it is the one that then has no padding left over.
    width = max((button_text_width(button) for button in buttons), default=0)
    height = max((hint.height() for hint in hints), default=0)
    for button in buttons:
        if widths:
            button.setFixedWidth(width)
        if heights:
            button.setFixedHeight(height)


def pink_spin_buttons(root: QWidget) -> None:
    """Give every spin box under `root` the pink +/- pair, in place of arrows.

    Qt's native spin arrows are two grey triangles a few pixels tall: the
    app-wide pink QPushButton rule does not reach them, they are the hardest
    thing in any of these dialogs to hit, and next to the pink buttons around
    them they read as disabled. Two controls built the pair by hand first
    (`ParameterControl`, `DifficultyValueControl`); this used to claim they
    had "already solved this the same way", which was the one thing they had
    not done -- both typed a width (30px, 28px) under the window stylesheet's
    32px of horizontal padding, so their glyphs had negative room and Qt
    painted blank pink squares. All three now build the pair through
    `step_button`, so the fix cannot go missing from one of them again.

    Called once per dialog, after its layout is built. Spin boxes that already
    carry their own pair say so by having no buttons, and are left alone.
    """
    for spin in root.findChildren(QAbstractSpinBox):
        if spin.buttonSymbols() == QAbstractSpinBox.NoButtons:
            continue
        parent = spin.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is None:
            continue
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        # A row already hidden before the wrap has to stay hidden after it.
        # The container is brand new and therefore visible, and the spin is
        # forced visible below because it has to show *inside* the container
        # -- so between them they used to un-hide any row a dialog had already
        # set_row_visible(..., False) on. Only spin boxes were affected, which
        # is why the Volume dialog hid its checkbox rows and kept showing the
        # position offset.
        was_hidden = spin.isHidden()
        container = QWidget(parent)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        # replaceWidget takes the spin out of the layout without deleting it,
        # so it can be handed straight to the container's own row.
        layout.replaceWidget(spin, container)
        row.addWidget(spin, 1)
        spin.setVisible(True)
        container.setVisible(not was_hidden)
        # The spin is no longer the widget its form row is keyed on, so
        # setRowVisible has to be told where it went -- see `set_row_visible`.
        spin.setProperty("pinkRow", container)
        # A plain hyphen, the same glyph ParameterControl and
        # DifficultyValueControl already use: the typographic minus is not in
        # every font this ships against, and a missing glyph here is exactly
        # the blank button this pair exists to stop being.
        for text, step in (("+", spin.stepUp), ("-", spin.stepDown)):
            # Padding, not a typed width -- the same trap EditorViewFrame's
            # chrome buttons fell into. `step_button` overrides the padding
            # and then measures the glyph against what it actually costs.
            row.addWidget(step_button(text, step, container))


def set_row_visible(layout: QFormLayout, widget: QWidget, visible: bool) -> None:
    """`QFormLayout.setRowVisible` that survives `pink_spin_buttons`.

    A wrapped spin box is no longer the widget its form row holds -- its
    container is -- and setRowVisible on a widget the layout cannot find is a
    silent no-op, which is how "Every n snaps" ended up showing the millisecond
    field as well.
    """
    layout.setRowVisible(widget.property("pinkRow") or widget, visible)


def is_row_visible(layout: QFormLayout, widget: QWidget) -> bool:
    """The read side of `set_row_visible`, for the same reason."""
    return layout.isRowVisible(widget.property("pinkRow") or widget)


# -- hitsounds -------------------------------------------------------------
#
# Which sample a note asks for. **Circles only.** A drumroll -- which is what
# every fake slider and every shiny note is -- a spinner, and a timing point
# are all silent. That single rule is the whole of "play them wherever normal
# chart notes exist, never for fake sliders or barlines", and it needs no
# special case for the gimmick layers: the hittable note a Don/Kat fake slider
# or a barline note writes *is* an ordinary circle in the chart, and the player
# really does hit it, so silencing it would make playback lie about the map.
HITSOUND_SAMPLES = {
    "normal": "taiko-normal-hitnormal.wav",   # don
    "clap": "taiko-normal-hitclap.wav",       # kat
    "finish": "taiko-normal-hitfinish.wav",   # big don
    "whistle": "taiko-normal-hitwhistle.wav", # big kat
}

# One QSoundEffect plays one thing at a time: calling play() again restarts it,
# cutting off the hit still sounding. The samples run from 354ms to 1.5s and a
# 200 BPM 1/4 stream puts a note every 75ms, so around twenty of them overlap
# at once -- hence a pool per sample, played round robin, rather than one
# effect each.
HITSOUND_POOL_SIZE = 8


def hitsound_key(note) -> str | None:
    """Which sample `note` asks for, or None when it is silent."""
    if not note.is_circle:
        return None
    if note.is_finisher:
        return "whistle" if note.is_kat else "finish"
    return "clap" if note.is_kat else "normal"


def hitsound_schedule(hit_objects) -> tuple[list[float], list[str]]:
    """(times, sample keys) for every audible note, in time order.

    Built once per edit and binary-searched per frame. Scanning the hit objects
    on every frame instead would be a full pass over thousands of notes at
    120Hz, which is the cost this file avoids everywhere else it looks
    something up by time.
    """
    scheduled = sorted(
        (float(note.time), key)
        for note, key in ((note, hitsound_key(note)) for note in hit_objects)
        if key is not None
    )
    return [time_ms for time_ms, _key in scheduled], [key for _time, key in scheduled]


class HitsoundPlayer(QObject):
    """Sounds each note as the playhead crosses it, during playback only.

    Deliberately not tied to the audio pipeline: it follows the *song position*
    the frame loop already computes, so the 25/50/75% playback rates work with
    no extra arithmetic and without pitch-shifting the samples.

    `pending()` is kept free of any audio call so the interesting half -- which
    notes a window crosses, and what each one wants -- is testable without a
    media device.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.enabled = True
        # Shifts when a sound fires relative to the playhead; negative fires
        # earlier. Output latency is real and device-dependent, so without this
        # every hitsound sits late and the user has no recourse.
        #
        # **Wall** milliseconds, converted to song milliseconds at the point of
        # use by multiplying by `playback_rate` -- the same distinction
        # POSITION_ALLOWABLE_ERROR_MS draws. What it compensates is the time
        # the sound spends in the output device, which is real time and does
        # not care how fast the song is being read. Held as a song-time
        # constant (as it was) an offset tuned at 100% was four times too large
        # at 25%: the error is offset * (1 - rate), which is exactly zero at
        # 100% and worst at the slowest speed. That is why every rate except
        # the one nobody ever changes away from sounded misaligned.
        self.offset_ms = 0
        # Kept in step by MainWindow._change_playback_speed. Not read from the
        # player: `pending()` is deliberately free of any audio call so the
        # interesting half stays testable without a media device.
        self.playback_rate = 1.0
        self._times: list[float] = []
        self._keys: list[str] = []
        self._cursor = 0.0
        self._pools: dict[str, list[QSoundEffect]] = {}
        self._next: dict[str, int] = {}
        # Applied to each effect as it is built, since the volume is usually
        # set from the settings long before the first sound is ever played.
        self._volume = 1.0
        self._built = False

    def _build_pools(self) -> None:
        """Build every pool, once, the first time a sound is actually wanted.

        **Lazily**, which is not an optimisation but a correctness fix. Four
        samples times `HITSOUND_POOL_SIZE` is 32 QSoundEffect objects, and they
        outlive the window that owns them until Qt gets round to deleting it --
        so building them in the constructor made every MainWindow cost more
        than the last (0.18s at the fifth, 1.37s at the fortieth), which across
        a test suite that builds hundreds of them is quadratic and turned an
        82-minute run into hours. Nothing plays a sound in a test, so nothing
        is built there.

        Loading is asynchronous (Loading -> Ready), so this does not block; a
        note arriving before its sample is ready simply does not play rather
        than stalling the frame loop.
        """
        if self._built:
            return
        self._built = True
        for key, filename in HITSOUND_SAMPLES.items():
            path = next(
                (
                    candidate for candidate in
                    (root / "assets" / "se" / filename for root in resource_roots())
                    if candidate.is_file()
                ),
                None,
            )
            if path is None:
                continue
            pool = []
            for _ in range(HITSOUND_POOL_SIZE):
                effect = QSoundEffect(self)
                effect.setSource(QUrl.fromLocalFile(str(path)))
                effect.setVolume(self._volume)
                pool.append(effect)
            self._pools[key] = pool
            self._next[key] = 0

    def set_volume(self, fraction: float) -> None:
        """Remembered as well as applied: the settings are read at startup,
        long before any pool exists, and `_build_pools` reads it back."""
        self._volume = max(0.0, min(1.0, float(fraction)))
        for pool in self._pools.values():
            for effect in pool:
                effect.setVolume(self._volume)

    def set_schedule(self, hit_objects) -> None:
        self._times, self._keys = hitsound_schedule(hit_objects)

    def reset_to(self, position_ms: float) -> None:
        """Move the firing cursor without sounding anything.

        Every seek comes through here. Without it, jumping forward would fire
        every note between the old position and the new one in a single frame,
        and jumping backwards would replay the section you just left.
        """
        self._cursor = float(position_ms)

    def pending(self, previous_ms: float, current_ms: float) -> list[str]:
        """Sample keys for every note the playhead crossed in this window.

        Half-open on purpose -- `(previous, current]` -- so consecutive frames
        tile the timeline exactly: a note on a window boundary fires in one
        frame and never in both.
        """
        if not self.enabled or current_ms <= previous_ms:
            return []
        # See `offset_ms`: wall milliseconds, so the window it shifts has to be
        # scaled into song time by whatever rate the song is playing at.
        offset = self.offset_ms * abs(self.playback_rate)
        first = bisect_right(self._times, previous_ms - offset)
        last = bisect_right(self._times, current_ms - offset)
        return self._keys[first:last]

    def advance(self, current_ms: float) -> None:
        for key in self.pending(self._cursor, current_ms):
            self._play(key)
        self._cursor = float(current_ms)

    def _play(self, key: str) -> None:
        self._build_pools()
        pool = self._pools.get(key)
        if not pool:
            return
        index = self._next[key]
        self._next[key] = (index + 1) % len(pool)
        effect = pool[index]
        if effect.isLoaded():
            effect.play()


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
# Deliberately far below KIAI_PULSE_ALPHA: this one washes the *entire* view
# rect rather than a single note-sized circle, so the same alpha that reads as
# a glow on a note would read as a floodlight over the whole lane.
PLAYFIELD_PULSE_ALPHA = 16
# A circle's share of that, so a note glows without its colour being replaced.
CIRCLE_KIAI_STRENGTH = 0.55
# Under this a "beat" is a gimmick, not a pulse: an invisible-note section at
# 0.0001ms per beat would strobe once per frame.
KIAI_PULSE_MIN_BEAT_MS = 50.0


def beat_pulse(
    timing_points: list[TimingPoint], time_ms: float, anchor_ms: float | None = None,
) -> float:
    """Flash strength at `time_ms`: 1.0 on the beat, fading to 0 by the next.

    Walks back past any point whose beat_length reads as a gimmick rather than
    a real beat (a 60000 BPM section, a barline run) to the most recent point
    that is one, and pulses against that instead. Without this, the section
    active *right under the playhead* decided whether every note on screen
    flashed at all -- which is why "some shiny notes have no glow" depended on
    where the playhead happened to be sitting, not on the note. Returns 0.0
    only when the map has no real section behind the playhead at all, which
    keeps the original guard against strobing at 0.0001ms per beat.

    `anchor_ms` is the start of the kiai section `time_ms` falls in (the
    caller already has this from its kiai bands, so this doesn't search for
    it). With an anchor, the pulse hits 1.0 there and repeats every `meter`
    beats (a measure) instead of every single beat -- one linear fade across
    the whole measure, so a slower BPM fades slower for free, no separate
    constant needed. Without one, falls back to the old one-beat, point-phased
    behaviour so existing callers are unaffected.
    """
    # Binary search to the last point at or before the playhead, then walk back
    # from there -- not a scan from the end of the list. This runs once per
    # frame at 120Hz and a barline gimmick carries tens of thousands of points,
    # which is the difference between a repaint and a freeze (the same reason
    # `active_uninherited_at` is a binary search).
    index = bisect_right(timing_points, time_ms, key=lambda point: point.time) - 1
    while index >= 0:
        point = timing_points[index]
        if point.beat_length >= KIAI_PULSE_MIN_BEAT_MS:
            if anchor_ms is None:
                return 1.0 - ((time_ms - point.time) / point.beat_length) % 1.0
            meter = point.meter if point.meter > 0 else 4
            period = point.beat_length * meter
            return 1.0 - ((time_ms - anchor_ms) / period) % 1.0
        index -= 1
    return 0.0


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


def kiai_flash_strength(note) -> float:
    """How brightly `note` takes the kiai flash, relative to a drawn object.

    A circle glows *slightly*: it is already a solid, saturated red or blue and
    a full-strength overlay on top of it reads as the note changing colour
    rather than as the chorus lighting up. A drumroll -- every fake slider and
    every shiny -- takes the flash at full strength, because washing that
    yellow out toward white is the effect mappers stack them for in the first
    place. A shiny then compounds on top of that all by itself: it is several
    drumrolls on one millisecond, each painting its own overlay.
    """
    return CIRCLE_KIAI_STRENGTH if note.is_circle else 1.0


def draw_kiai_flash(
    painter: QPainter, x: float, y: float, radius: float, pulse: float, strength: float = 1.0,
) -> None:
    painter.setPen(Qt.NoPen)
    alpha = round(KIAI_PULSE_ALPHA * pulse * strength)
    if alpha <= 0:
        return
    painter.setBrush(QColor(*KIAI_PULSE_COLOR, alpha))
    painter.drawEllipse(QPointF(x, y), radius, radius)


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
            # A typed 30px here left the glyph negative room under the window
            # stylesheet's padding, so the button covered its own "+".
            increase_button = step_button("+", self.spin.stepUp)
            decrease_button = step_button("-", self.spin.stepDown)
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
        decrease_button=step_button("-", self.spin.stepDown)
        increase_button=step_button("+", self.spin.stepUp)
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
        decrease=step_button("-", self.value_box.stepDown);increase=step_button("+", self.value_box.stepUp)
        # Height typed, width measured: 28px was wide enough for the glyph
        # only until the window stylesheet's horizontal padding landed on it.
        for button in (decrease,increase):button.setFixedHeight(28);button.setToolTip(tr("MainWindow", "Adjust by 0.01"))
        layout.addWidget(increase);layout.addWidget(decrease)
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
        # Applied from here rather than inside the dialog: gui imports it, so
        # it cannot import back.
        pink_spin_buttons(dialog)
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

class TimelineGameplay(TimeAxisMixin, QWidget):
    selection_changed = Signal(object)
    selection_finalized = Signal(object)
    seek_requested = Signal(float)
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
    # uid -- double click on a red line, in a view that allows it. Only the
    # barline gimmick layer does; everywhere else the red lines are drawn for
    # reference and do not respond. The owner reads the point's own values.
    timing_line_edit_requested = Signal(int)
    # list[uid] -- right click on a red line, or Delete with red lines selected.
    # One signal for the whole set so a bulk delete is a single undo step.
    timing_lines_delete_requested = Signal(object)
    # (start_ms, end_ms) -- drag-select with the function tool. The chart view's
    # equivalent of SVEditorView.function_range_requested, and the barline
    # layer's route to generating a run of red lines over a range.
    timing_function_range_requested = Signal(float, float)
    # (note uids, timing point uids, delta_ms) -- a select-tool drag that
    # started on an object or an owned red line rather than on empty space. The
    # view reports only what was actually grabbed; growing that into the whole
    # gimmick structure around it is the owner's job, because which lines belong
    # to which structure is its knowledge and not the view's.
    objects_move_requested = Signal(object, object, float)

    # The height every fixed pixel size in paintEvent was measured at, and the
    # smallest a standalone view is allowed to get. The gimmick page's layers
    # drop their minimum and are drawn scaled against this instead.
    DESIGN_HEIGHT = 180

    def __init__(self) -> None:
        super().__init__()

        self.notes = []
        self.note_times: list[int] = []
        self._max_extend_ms = 0.0
        self.timing_points: list[TimingPoint] = []
        self._timing_times: list[float] = []
        # Every point, inherited ones included. `timing_points` above is the
        # uninherited-only list the beat grid needs; a slider's length depends
        # on the SV in force at it, and that only exists on inherited points.
        self._sv_points: list[TimingPoint] = []
        # The map's [Difficulty] SliderMultiplier, the other half of that same
        # conversion. Falls back to osu!'s default until a document is loaded.
        self.slider_multiplier = SLIDER_MULTIPLIER_ASSUMED
        self.selected: set[int] = set()
        # Red lines picked by a drag, in a view that owns them. Kept apart from
        # `selected` (which is note original_index) because the barline layer
        # shows no notes at all and these are what its Delete acts on.
        self.selected_timing_uids: set[int] = set()
        # Predicate deciding which hit objects this view shows; see refresh_notes.
        self.object_filter = None
        # Draw the document's uninherited (BPM) points as vertical red lines.
        # Off for the shared player timeline and the Editor page's chart views,
        # where the snap grid already says where the beats are; on for the
        # gimmick layers, whose whole subject matter is red lines.
        self.show_timing_lines = False
        # Hand this view's red lines to its tools -- right click deletes one,
        # select drags one, a rubber band picks them up. The barline layer
        # only: a red line is that layer's material, and it is view-only
        # elsewhere.
        self.timing_edit_enabled = False
        # ...and let a double click on one open its BPM. Both structure layers,
        # because a fake slider's red line is the half of it that makes the
        # object fake and retiming that line is how a shiny is tuned. Separate
        # from `timing_edit_enabled` on purpose: the fake slider layer must not
        # also let the line be dragged or deleted on its own, since a line
        # pulled out from under its sliders dismantles the structure rather
        # than moving it (only `_expand_move` knows how to move the whole
        # thing, and it recognises a structure by the object, not the line).
        self.timing_dialog_enabled = False
        # Restrict the drawn red lines to these milliseconds; None draws all of
        # them. The fake slider layer sets it to the fake sliders' own times, so
        # the line it shows is the one squashing the object beside it rather
        # than every red line in the map.
        self.timing_line_times: set[int] | None = None
        # Derives the above from the document on every refresh -- see
        # SVEditorView.point_times_for, which solves the same staleness.
        self.timing_line_times_for = None
        # How the hover preview draws a Don/Kat: as the note itself, as the
        # yellow fake slider it will become, or as the bar a barline note draws.
        # The shape a tool writes differs per layer, so the ghost has to as well.
        self.ghost_style = "note"
        # Write the BPM beside each red line this view owns. On in the barline
        # layer, whose whole material is red lines and where the number used to
        # be readable only by dropping down to the SV layer under it.
        self.show_bpm_labels = False
        # Milliseconds whose red line belongs to a *different* layer, drawn
        # dimmed and untouchable here. Supplied by the owner, because which
        # structure owns what is its knowledge, not the view's.
        self.foreign_times: set[int] = set()
        # ...derived from the document on every refresh, like the other two
        # `_for` hooks: the objects it depends on move as the map is edited.
        self.foreign_times_for = None
        # Milliseconds carrying a gimmick line -- the centre of a structure, as
        # opposed to one of the restore lines around it. A drag lands one of
        # these on the snap grid and a right click resolves to it, so which
        # lines they are has to be exact: the BPM that makes a line a gimmick
        # line is configurable per layer, and guessing "anything above 1000" got
        # a 900 BPM gimmick wrong in one direction and a real 1200 BPM section
        # wrong in the other. Supplied by the owner for the same reason
        # `foreign_times` is -- it holds the configs.
        self.gimmick_times: set[int] = set()
        self.gimmick_times_for = None
        # Milliseconds carrying a shiny note -- a stack of fake sliders on one
        # timestamp, which is what reads as white. Derived from the document per
        # refresh like the three sets above, because "is this a shiny" is a
        # question about how many objects share a millisecond and that changes
        # with every placement.
        self.shiny_times: set[int] = set()
        self.shiny_times_for = None
        # Two rows and a centred grid, for the fake slider layer alone: it shows
        # two kinds of drawn object one millisecond apart, and stacked on one
        # baseline at gimmick zoom they are the same blob. Fake sliders take the
        # upper row, shiny notes the lower, and the snap ticks move to the
        # middle so neither row is drawn over them.
        self.split_rows = False

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
        # Whether a select-tool press on an object drags it to a new time
        # instead of starting a rubber-band selection. On for the gimmick
        # layers, where a structure has to be movable and the material is
        # sparse; off elsewhere, because on a normal chart every click lands
        # near a note and selection is what that click is for.
        self.move_enabled = False
        # Select-tool drag of something rather than of empty space -- see
        # objects_move_requested and _begin_move.
        self._move_note_uids: list[int] = []
        self._move_point_uids: list[int] = []
        self._move_origin: float | None = None
        self._move_anchor_time = 0.0
        self._move_delta = 0.0
        self._move_snaps = True
        # What to place instead, if a grab under a placement tool turns out to
        # be a plain click rather than a drag.
        self._move_fallback: tuple | None = None

        self.don_brush = QColor(255, 65, 30, 180)
        self.kat_brush = QColor(55, 145, 255, 180)
        self.slider_brush = QColor(255, 210, 60, 180)
        self.ghost_don_brush = QColor(255, 65, 30, 90)
        self.ghost_kat_brush = QColor(55, 145, 255, 90)
        self.ghost_slider_brush = QColor(255, 210, 60, 90)
        # A shiny note is several translucent drumrolls on one millisecond, and
        # what the stack actually looks like in game is the yellow washing out
        # to white. The layer draws that result directly rather than painting
        # three overlapping yellows, which at this size is just a brighter blob.
        # Deliberately near-white -- a shiny is the stack mappers build to read
        # as "brighter than a note", so its fill is pushed toward pure white
        # rather than toward any particular hue.
        self.shiny_brush = QColor(255, 254, 245, 235)
        self.ghost_shiny_brush = QColor(255, 254, 245, 120)
        # Spinner extent: grey so it reads as "this span is occupied" rather
        # than competing with don/kat/slider colour coding.
        self.spinner_band_brush = QColor(190, 195, 205, 70)
        self.spinner_edge_pen = QPen(QColor(220, 226, 236, 170), 2)
        self.ghost_pen = QPen(QColor(255, 255, 255, 110), 2)
        self.normal_note_pen = QPen(QColor(255, 255, 255, 220), 2)
        self.selected_note_pen = QPen(QColor(255, 220, 110, 235), 3)
        self.baseline_pen = QPen(QColor("#7a8492"), 2)
        self.bpm_label_pen = QPen(QColor(225, 230, 240, 200), 1)
        self.cursor_pen = QPen(QColor("#ffffff"), 3)
        # Two weights of red line: the ones this view owns, and the ones drawn
        # so the owned ones can be read in context.
        self.timing_line_pen = QPen(QColor(255, 90, 90, 235), 2)
        self.timing_line_ref_pen = QPen(QColor(255, 90, 90, 90), 1)
        # Where a line being dragged started. Dashed as well as faded, so it
        # cannot be misread as one of the reference lines above.
        self.timing_line_ghost_pen = QPen(QColor(255, 90, 90, 110), 2, Qt.DashLine)
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

        self.setMinimumHeight(self.DESIGN_HEIGHT)
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
        # The gimmick editor's layers each show one kind of object, so that a
        # fake slider can be selected and dragged without the real chart's notes
        # sitting under the cursor. Everywhere else this is None and every
        # object is shown.
        if self.object_filter is None:
            self.notes = document.hit_objects
        else:
            self.notes = [note for note in document.hit_objects if self.object_filter(note)]
        if self.timing_line_times_for is not None:
            self.timing_line_times = self.timing_line_times_for(document)
        self.note_times = [note.time for note in self.notes]
        if self.foreign_times_for is not None:
            self.foreign_times = self.foreign_times_for(document)
        if self.gimmick_times_for is not None:
            self.gimmick_times = self.gimmick_times_for(document)
        if self.shiny_times_for is not None:
            self.shiny_times = self.shiny_times_for(document)
        if self.kiai_bands_for is not None:
            self.kiai_bands = self.kiai_bands_for(document)
        else:
            self.set_kiai_from(document.timing_points)
        self.timing_points = extract_timing_points(document)
        self._timing_times = [point.time for point in self.timing_points]
        self._sv_points = sorted_by_time(document.timing_points)
        self.slider_multiplier = document.slider_multiplier
        # Lines deleted elsewhere must not stay "selected" forever -- the same
        # rule SVEditorView.refresh_points applies to its own selection.
        self.selected_timing_uids &= {point.uid for point in self.timing_points}
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
        """Change the grid, not the playhead.

        Alt+wheel routes here through `snap_changed_by_wheel` on every open
        view, and this used to re-snap `current_time` onto the new grid and
        broadcast a seek -- so switching 1/4 to 1/3 physically moved every
        view's playhead. Looking at a different grid is not a request to
        move: the playhead self-corrects on the very next wheel notch anyway,
        since `wheel_seek_time` snaps from wherever `current_time` currently
        is before stepping, so sitting off-grid between notches costs
        nothing. Matches `SVEditorView.set_snap_divisor` and
        `GameplayViewerView.set_snap_divisor`, which never snapped here either.
        """
        self.snap_divisor = divisor
        self.update()

    def set_symmetric(self, symmetric: bool) -> None:
        self.symmetric = symmetric
        self.update()

    def _note_near_x(self, x: float, radius_px: float = 20.0, y: float | None = None):
        """Nearest note within radius_px of an x position, for right-click delete.

        `y` matters on a split layer and nowhere else. There the two rows hold
        two different structures a millisecond apart -- the same pixel column
        at any zoom the layer is readable at -- so the row the click landed in
        is the only thing that says which of them was meant. Without it the
        scan returned whichever came first in the document, and grabbing the
        fake slider drawn on the upper row dragged the shiny note under it
        (and the other way round).
        """
        wants_lower = None if y is None or not self.split_rows else y > self._baseline_y()
        best = None; best_distance = None
        for note in self.notes:
            if wants_lower is not None and (round(note.time) in self.shiny_times) != wants_lower:
                continue
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
            # `self.timing_points` is the uninherited-only list (see
            # extract_timing_points), where every point reads as 1.0x -- so the
            # SV has to come off the full list, which is what osu! resolves the
            # slider's speed from at the slider's own start time.
            sv = sv_at(self._sv_points, note.time)
            duration = duration_for_slider_length(
                note.length * note.slides, timing.beat_length, sv, self.slider_multiplier,
            )
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
            # A drag-selection is live and this press landed on the page's
            # timing bar rather than in the view: scrub the playhead there
            # instead of deleting whatever sits under the cursor, so a range
            # longer than one screenful can be built without dragging all the
            # way through it. Only reachable at all because grabMouse() during
            # the drag routes every event here regardless of where the cursor
            # actually is -- see TimeAxisMixin._press_is_on_timing_bar.
            if self.drag_anchor_time is not None and self._press_is_on_timing_bar(
                event.globalPosition().toPoint()
            ):
                self._bar_scrubbing = True
                self.current_time = self._time_on_timing_bar(event.globalPosition().toPoint())
                self.seek_requested.emit(self.current_time)
                self.drag_mouse_x = self.x_for_time(self.current_time)
                self._update_drag_selection()
                event.accept()
                return
            note = self._note_near_x(event.position().x(), y=event.position().y())
            if note is not None:
                self.note_delete_requested.emit(note.uid)
                event.accept()
                return
            # No note under the cursor: in a layer that owns its red lines, the
            # red line is what right click is for. Tried second so a layer
            # showing both never loses the note to the line beneath it.
            if self.timing_edit_enabled:
                point = (
                    self._gimmick_centre_near_x(event.position().x())
                    or self._timing_point_near_x(event.position().x())
                )
                if point is not None:
                    self.timing_lines_delete_requested.emit([point.uid])
            event.accept()
            return
        if event.button()!=Qt.LeftButton:
            return

        if self.tool in ("function", "convert", "kiai"):
            # Same gesture as the SV editor's function tool: drag a range, then
            # answer a dialog about what to fill it with. "convert" and "kiai"
            # share it -- they also act on a range, they just need nothing else
            # asked.
            self.drag_start_x = event.position().x()
            self.drag_mouse_x = self.drag_start_x
            self.drag_anchor_time = self.time_for_x(self.drag_start_x)
            self.grabMouse()
            self.update()
            event.accept()
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
            snapped = self.snap_ms(self.time_for_x(event.position().x()))
            self._placing_tool = self.tool
            self._placing_start_time = snapped
            self._placing_end_time = snapped
            self.grabMouse()
            event.accept()
            return

        if self.tool != "select":
            time_ms = self.snap_ms(self.time_for_x(event.position().x()))
            big = bool((event.modifiers() | QApplication.keyboardModifiers()) & Qt.ShiftModifier)
            # Pressing on something that is already there starts a move, even
            # with a placement tool in hand. Dragging is how a gimmick object
            # gets nudged, and having to switch back to Select first is the
            # reason it read as "objects can't be moved" -- after placing a run
            # of them, the placement tool is what you are still holding.
            #
            # A press that does not turn into a drag still places, so clicking
            # an existing note to retype it (Don over a Kat) is unchanged: the
            # placement is only handed over once the object has actually moved.
            grabbed = (
                self._note_near_x(event.position().x(), y=event.position().y())
                if self.move_enabled else None
            )
            if grabbed is not None:
                self._begin_move(grabbed, None, event.position().x())
                self._move_fallback = (self.tool, time_ms, self.new_combo, big)
                event.accept()
                return
            self.note_place_requested.emit(self.tool, time_ms, self.new_combo, big)
            event.accept()
            return

        # A tail grab takes priority over everything else select does, but
        # only within its own small radius (_extendable_note_near_edge's 16px,
        # tighter than _note_near_x's 20) -- so a click on a slider's head or
        # body still falls through to the move/rubber-band handling below.
        # Independent of move_enabled: a drumroll's tail is draggable on any
        # layer, not only the gimmick ones where whole-object moving is
        # enabled. This is what lets a slider be lengthened without switching
        # back to the tool that created it (see the identical branch above
        # for "slider"/"spinner").
        existing = self._extendable_note_near_edge(event.position().x())
        if existing is not None:
            self._resizing_note = existing
            self._resize_new_end_time = self._note_end_time(existing)
            self.grabMouse()
            event.accept()
            return

        # Select mode, on top of something: drag it to a new time. A note is
        # tried before a red line so a layer showing both never loses the
        # object to the line under it.
        if self.move_enabled:
            grabbed_note = self._note_near_x(event.position().x(), y=event.position().y())
            grabbed_point = (
                self._timing_point_near_x(event.position().x())
                if grabbed_note is None and self.timing_edit_enabled else None
            )
            if grabbed_note is not None or grabbed_point is not None:
                self._begin_move(grabbed_note, grabbed_point, event.position().x())
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
    def _begin_move(self, note, point, x: float) -> None:
        """Start a select-tool drag of whatever the click landed on.

        Grabbing something that is part of the current selection moves the whole
        selection; grabbing something outside it moves that alone and leaves the
        selection untouched -- the same rule every editor uses, and the one that
        makes "select a run of barlines, then shift all of them" possible.
        """
        # Whichever bar of a barline note was grabbed, the structure's centre is
        # what moves -- see _gimmick_centre_near_x.
        if point is not None:
            point = self._gimmick_centre_near_x(x) or point
        in_selection = (
            note is not None and note.original_index in self.selected
            or point is not None and point.uid in self.selected_timing_uids
        )
        if in_selection:
            self._move_note_uids = [n.uid for n in self.selected_notes()]
            self._move_point_uids = sorted(self.selected_timing_uids)
        else:
            self._move_note_uids = [note.uid] if note is not None else []
            self._move_point_uids = [point.uid] if point is not None else []
        self._move_origin = float(note.time if note is not None else point.time)
        # A note belongs on the beat grid, so its drag snaps to it -- and so
        # does a barline note, which is a note drawn out of red lines and is
        # placed on the grid to begin with.
        #
        # A plain red line is not: a barline gimmick's whole vocabulary is
        # millisecond offsets -- its restore lines sit +/-2ms from its centre --
        # and snapping those to a 125ms division meant every nudge smaller than
        # a beat rounded back to where it started, which reads as the drag doing
        # nothing. Only the structure's centre snaps; nudging any other line off
        # the grid by hand still works.
        self._move_snaps = note is not None or (
            point is not None and round(point.time) in self.gimmick_times
        )
        self._move_anchor_time = self.time_for_x(x)
        self._move_delta = 0.0
        self.grabMouse()
        self.update()

    def _update_move(self, x: float) -> None:
        """Land the *grabbed* object on a position and shift everything by that.

        Positioning the grabbed object rather than the cursor: otherwise the
        offset would depend on where inside the object the drag started, and a
        structure could never be landed exactly on a division.

        The grid for a note, whole milliseconds for a red line -- see
        `_begin_move`.
        """
        if self._move_origin is None:
            return
        wanted = self._move_origin + (self.time_for_x(x) - self._move_anchor_time)
        target = self.snap_ms(wanted) if self._move_snaps else round(wanted)
        # Nothing may be dragged to a negative time; clamping the delta rather
        # than each object keeps the structure's own shape intact.
        self._move_delta = max(-self._move_origin, target - self._move_origin)
        self.update()

    def _finish_move(self) -> None:
        self.releaseMouse()
        delta = self._move_delta
        note_uids, point_uids = self._move_note_uids, self._move_point_uids
        fallback = self._move_fallback
        self._move_note_uids = []
        self._move_point_uids = []
        self._move_origin = None
        self._move_delta = 0.0
        self._move_fallback = None
        if delta and (note_uids or point_uids):
            self.objects_move_requested.emit(note_uids, point_uids, delta)
        elif fallback is not None:
            # Never moved: this was a click with a placement tool held, so it
            # means what it always meant.
            self.note_place_requested.emit(*fallback)
        elif note_uids or point_uids:
            # A press on an object that never became a drag is a click, and a
            # click selects it. Without this, clicking a gimmick object did
            # nothing at all -- it could only be selected by dragging a band
            # around it, so Delete had nothing to act on unless you knew that.
            grabbed = set(note_uids)
            self.selected = {
                note.original_index for note in self.notes if note.uid in grabbed
            }
            self.selected_timing_uids = set(point_uids)
            self.selection_changed.emit(set(self.selected))
        self.update()

    def _update_drag_selection(self) -> None:
        if self.drag_anchor_time is None: return
        a,b=sorted((self.drag_anchor_time,self.time_for_x(self.drag_mouse_x)))
        first,last=bisect_left(self.note_times,a),bisect_right(self.note_times,b)
        self.selected={n.original_index for n in self.notes[first:last]}
        # A layer that owns its red lines selects those too, and only the ones
        # it owns: a line sitting on someone else's object is another layer's.
        if self.timing_edit_enabled:
            self.selected_timing_uids = {
                point.uid
                for point in self.timing_points
                if a <= point.time <= b and self._owns_timing_point(point)
            }
        self.selection_changed.emit(set(self.selected)); self.update()
    def mouseMoveEvent(self,event) -> None:
        if self._bar_scrubbing:
            self.current_time = self._time_on_timing_bar(event.globalPosition().toPoint())
            self.seek_requested.emit(self.current_time)
            self.drag_mouse_x = self.x_for_time(self.current_time)
            self._update_drag_selection()
            return
        if self._move_origin is not None:
            self._update_move(event.position().x())
            return
        if self._placing_tool is not None:
            snapped = max(
                self._placing_start_time,
                self.snap_ms(self.time_for_x(event.position().x())),
            )
            self._placing_end_time = snapped
            self.update()
            return
        if self._resizing_note is not None:
            minimum = self._resizing_note.time + 20.0
            snapped = max(minimum, self.snap_ms(self.time_for_x(event.position().x())))
            self._resize_new_end_time = snapped
            self.update()
            return
        if self.drag_anchor_time is not None:
            self.drag_mouse_x=event.position().x(); self._update_drag_selection()
            return
        # Repaint on every move, whatever the tool. The hover *ghost* is only
        # worth drawing for a placement tool, but draw_cursor_position writes
        # the millisecond under the cursor in every one of them -- and with
        # the repaint gated on the tool, that number only refreshed when
        # something else happened to repaint the view, which in select mode
        # meant clicking.
        self._hover_time = self.time_for_x(event.position().x())
        self.update()
    def leaveEvent(self, event) -> None:
        """Hand the readout back to the playhead when the cursor leaves."""
        self._hover_time = None
        super().leaveEvent(event)
        self.update()

    def _auto_scroll_selection(self) -> None:
        if self.drag_anchor_time is None: self.auto_scroll_timer.stop(); return
        if self.auto_scroll_step(self.drag_mouse_x): self._update_drag_selection()
    def mouseReleaseEvent(self,event) -> None:
        if event.button() == Qt.RightButton:
            # A right release only ever matters here as the end of a bar-scrub
            # (see mousePressEvent above) -- it must never fall through to the
            # generic release logic below, which would end whatever drag a
            # left button is still holding open.
            self._bar_scrubbing = False
            event.accept()
            return
        if self._move_origin is not None:
            self._finish_move()
            return
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
        if self.tool in ("function", "convert", "kiai"):
            self.releaseMouse()
            start, end = sorted((self.drag_anchor_time, self.time_for_x(event.position().x())))
            if self.tool == "kiai":
                # Snapped here rather than by the handler -- see
                # SVEditorView.mouseReleaseEvent, which owns the same gesture
                # in the Kiai and Sound Volume layer and for the same reason:
                # only the view knows Ctrl means whole milliseconds.
                start, end = self.snap_ms(start), self.snap_ms(end)
            self.drag_start_x = None
            self.drag_anchor_time = None
            self.update()
            self.timing_function_range_requested.emit(start, end)
            return
        self.auto_scroll_timer.stop(); self.releaseMouse()
        # Click-to-seek is a Fancy-Arranger-timeline convenience; Editor-page
        # views (symmetric) must not jump the shared playhead just because
        # someone clicked inside them to select/place/inspect a note.
        if not self.symmetric and abs(event.position().x()-(self.drag_start_x or 0.0))<5:
            self.current_time=self.snap_ms(self.time_for_x(event.position().x())); self.seek_requested.emit(self.current_time)
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
            if self.selected or self.selected_timing_uids:
                self.selected.clear()
                self.selected_timing_uids.clear()
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
            if self.symmetric and self.selected_timing_uids:
                self.timing_lines_delete_requested.emit(sorted(self.selected_timing_uids))
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
        times = self._snap_times or self._timing_times
        index = bisect_right(times, time_ms)
        return times[index] if index < len(times) else None

    def _draw_snap_grid(
        self,
        painter: QPainter,
        baseline_y: int,
    ) -> None:
        start_time = self.current_time - self.window_ms / 2
        end_time = self.current_time + self.window_ms / 2

        if end_time <= start_time:
            return

        # _tick_styles' heights are pixels at DESIGN_HEIGHT. Sized from the view
        # for the same reason the note radii are: on the gimmick page's short
        # bands a full-size tick growing in from each edge meets the one
        # opposite, and the notes end up drawn on top of a solid grid.
        tick_scale = min(1.0, self.height() / self.DESIGN_HEIGHT)
        divisor = self.snap_divisor
        timing = active_timing(
            self.snap_points,
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
                self.snap_points,
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
            tick_height = round(tick_height * tick_scale)
            # Clamped to just off either edge before it reaches Qt: drawLine
            # takes a C int, and one 0.00001 BPM section puts the tick after
            # this one billions of pixels out. Handing that over raises
            # OverflowError *inside* paintEvent, which aborts the whole frame --
            # grid, notes and all -- and looks like the map vanishing.
            #
            # Drawn at round(tick_time), not the exact fractional beat position:
            # objects snap to a whole millisecond (see `TimeAxisMixin.snap_ms`),
            # so a grid drawn at the unrounded position sits up to half a
            # millisecond off the note it is meant to mark. The walk itself
            # still steps by the exact `snap_length` -- rounding only the
            # drawn position, never accumulating it into `tick_time`, keeps
            # the grid from drifting off its own beats across a long section.
            x = round(max(-1.0, min(float(self.width() + 1), self.x_for_time(osu_round(tick_time)))))

            painter.setPen(pen)
            if self.split_rows:
                # Straddling the middle instead of growing in from the edges:
                # this layer's objects are up against the top and bottom, and
                # ticks there were drawn through them. See `_row_y`.
                painter.drawLine(x, baseline_y - tick_height // 2, x, baseline_y + tick_height // 2)
            elif self.symmetric:
                # Notes alone stay centered on the baseline; ticks anchor to
                # the view's top and bottom edges and grow inward, framing
                # the notes rather than straddling the middle with them.
                painter.drawLine(x, 0, x, tick_height)
                painter.drawLine(x, self.height(), x, self.height() - tick_height)
            else:
                painter.drawLine(x, baseline_y, x, baseline_y - tick_height)

            tick_time += snap_length

    def _owns_timing_point(self, point: TimingPoint) -> bool:
        """Whether this view's tools act on `point`, or merely show it.

        Ownership is decided by which *structure* a line belongs to, not by
        whether some hit object shares its millisecond. It used to be the
        latter, which was true only while a barline note wrote no note of its
        own: once it did, its middle line -- the 60000 BPM line that is the
        whole gimmick -- landed on that note and became untouchable in the one
        layer that exists to touch it.
        """
        return round(point.time) not in self.foreign_times

    def _visible_timing_points(self):
        """The document's red lines inside the window, at most one per pixel.

        A barline gimmick puts thousands of uninherited points in one screen,
        most of them on the same pixel column. Drawing every one is a line per
        point per frame for a result indistinguishable from drawing one.
        """
        if not self.show_timing_lines or not self.timing_points:
            return
        start_time = self.current_time - self.window_ms / 2
        end_time = self.current_time + self.window_ms / 2
        first = bisect_left(self._timing_times, start_time)
        after_last = bisect_right(self._timing_times, end_time)
        previous: tuple[int, bool] | None = None
        for point in self.timing_points[first:after_last]:
            if self.timing_line_times is not None and round(point.time) not in self.timing_line_times:
                continue
            owned = self._owns_timing_point(point)
            x = round(self.x_for_time(point.time))
            if previous == (x, owned):
                continue
            previous = (x, owned)
            yield point, x, owned

    def _draw_timing_lines(self, painter: QPainter) -> None:
        # Same two-places-at-once rule the dragged notes follow: a line being
        # moved is drawn at its destination, and faintly where it came from.
        moving = set(self._move_point_uids) if self._move_origin is not None else frozenset()
        # Labels are spaced the same way the SV editor spaces its own: a
        # barline gimmick stacks thousands of lines in one window, and a number
        # beside each of them is a solid grey block.
        last_label_x = -1e9
        for point, x, owned in self._visible_timing_points():
            if point.uid in moving:
                painter.setPen(self.timing_line_ghost_pen)
                painter.drawLine(x, 0, x, self.height())
                x = round(self.x_for_time(point.time + self._move_delta))
            if point.uid in self.selected_timing_uids:
                painter.setPen(self.selected_note_pen)
            else:
                painter.setPen(self.timing_line_pen if owned else self.timing_line_ref_pen)
            painter.drawLine(x, 0, x, self.height())
            if (
                self.show_bpm_labels and owned and point.bpm
                and x - last_label_x >= SV_LABEL_MIN_SPACING_PX
            ):
                last_label_x = x
                painter.setPen(self.bpm_label_pen)
                painter.drawText(QPointF(x + 4, self.height() - 4), f"{point.bpm:.0f} BPM")

    def _gimmick_centre_near_x(self, x: float, radius_px: float = 12.0):
        """The owned gimmick line in the same pixel neighbourhood as `x`, if any.

        A barline note is up to seven red lines inside twelve milliseconds. At
        any zoom you can read a chart at they are the same pixel column, so
        "the nearest line" picks between its centre and its restores by
        rounding error -- and only the centre is the object. Right click and
        drag both ask for it here, so grabbing a barline note means the same
        thing wherever inside it the cursor was.
        """
        best = None
        best_distance = None
        for point in self.timing_points:
            if round(point.time) not in self.gimmick_times or not self._owns_timing_point(point):
                continue
            distance = abs(self.x_for_time(point.time) - x)
            if distance <= radius_px and (best_distance is None or distance < best_distance):
                best = point
                best_distance = distance
        return best

    def _timing_point_near_x(self, x: float, radius_px: float = 12.0):
        """The owned red line nearest `x`, for the double-click BPM edit.

        Restricted to the lines this view actually draws. A layer that filters
        them (`timing_line_times`, the fake slider layer) would otherwise hand
        back a line that is not on screen at all -- the chart's own timing, or
        a barline gimmick's -- and open a dialog for something invisible.
        """
        best = None
        best_distance = None
        for point in self.timing_points:
            if self.timing_line_times is not None and round(point.time) not in self.timing_line_times:
                continue
            if not self._owns_timing_point(point):
                continue
            distance = abs(self.x_for_time(point.time) - x)
            if distance <= radius_px and (best_distance is None or distance < best_distance):
                best = point
                best_distance = distance
        return best

    def mouseDoubleClickEvent(self, event) -> None:
        """Double click a red line to retype its BPM, where that is allowed.

        Reached before mousePressEvent's tool handling gets a second click, so a
        placement tool being active does not swallow it.
        """
        if not self.timing_dialog_enabled or event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        point = self._timing_point_near_x(event.position().x())
        if point is None or point.bpm is None:
            super().mouseDoubleClickEvent(event)
            return
        self.timing_line_edit_requested.emit(point.uid)
        event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#151b24"))
        self.draw_kiai_bands(painter)

        baseline_y = self._baseline_y()
        # Sized from the view, not typed: the gimmick page shows six layers as
        # short bands, and a fixed 42px finisher there is taller than the band
        # it sits in -- it spills past the edges and stops reading as centred on
        # the baseline. Full-height views are unchanged, since they clamp at the
        # sizes these used to be fixed at. A split layer halves it again: it
        # draws two rows of objects instead of one, and at the full radius the
        # two overlap and the pair reads as a single smear.
        normal_note_radius = min(31.0, self.height() * (0.15 if self.split_rows else 0.22))
        finisher_note_radius = min(42.0, normal_note_radius * 1.35)

        self._draw_snap_grid(painter, baseline_y)
        self._draw_timing_lines(painter)

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

        # Notes being dragged are drawn where they are *going*, with a
        # translucent copy left where they came from -- see _draw_move_ghost.
        # Without the preview, a drag showed the object standing still and only
        # a marker line moving, so there was nothing to line the object up with.
        moving_notes = set(self._move_note_uids) if self._move_origin is not None else frozenset()

        for note in self.notes[first_visible:after_last_visible]:
            x = self.x_for_time(note.time)
            # A dragged note is previewed at its destination, so its body (a
            # slider bar, a spinner band) has to travel with its head.
            shift = 0.0
            if note.uid in moving_notes:
                self._draw_move_ghost(painter, note, x, baseline_y, normal_note_radius, finisher_note_radius)
                shift = self._move_delta
                x = self.x_for_time(note.time + shift)
            # Spinners always render at the finisher/"big note" size,
            # independent of the finisher hitsound bit -- a plain visual
            # convention for this app, not a gameplay difference.
            radius = (
                finisher_note_radius
                if (note.is_finisher or note.is_spinner)
                else normal_note_radius
            )
            shiny = self.split_rows and round(note.time) in self.shiny_times
            note_center_y = (
                self._row_y(baseline_y, shiny) if self.split_rows
                else baseline_y if self.symmetric
                else baseline_y - radius
            )
            note_pen = self.selected_note_pen if note.original_index in self.selected else self.normal_note_pen

            if note.is_spinner:
                # A spinner is a duration, but only its start had any visual
                # weight, so where it *ended* was invisible unless you dragged
                # its edge and watched the ghost. Grey band from start to end,
                # with a cap at each edge.
                spinner_end = self._note_end_time(note)
                if spinner_end is not None and spinner_end > note.time:
                    end_x = self.x_for_time(spinner_end + shift)
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
                    end_x = self.x_for_time(end_time + shift)
                    if end_x > x:
                        bar = QRectF(x, note_center_y - radius * 0.4, end_x - x, radius * 0.8)
                        painter.setBrush(self.slider_brush)
                        painter.setPen(Qt.NoPen)
                        painter.drawRoundedRect(bar, radius * 0.4, radius * 0.4)

            # Semi-transparent fill keeps the snap grid visible through notes.
            draw_note_sprite(
                painter,
                self.shiny_brush if shiny
                else self.slider_brush if note.is_slider
                else (self.kat_brush if note.is_kat else self.don_brush),
                note_pen,
                x,
                note_center_y,
                radius,
            )

        self._draw_placement_ghost(painter, baseline_y, normal_note_radius, finisher_note_radius)
        self.draw_ctrl_precision_line(painter)
        self.draw_cursor_position(painter)

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

    def _baseline_y(self) -> float:
        """The y objects are centred on. Bottom-anchored views draw lower."""
        return self.height() // 2 if self.symmetric else self.height() // 2 + 34

    def _row_y(self, baseline_y: float, lower: bool) -> float:
        """Centre of the upper or lower object row on a split layer.

        A quarter of the band either side of the middle: the snap ticks are
        drawn across the middle, so the rows sit clear of them rather than
        being cut in half by the grid.
        """
        offset = self.height() * 0.24
        return baseline_y + offset if lower else baseline_y - offset

    def _draw_move_ghost(
        self, painter: QPainter, note, x: float, baseline_y: int,
        normal_radius: float, finisher_radius: float,
    ) -> None:
        """Translucent copy of a dragged note, where it started.

        The drag is only committed on release, so during it the object needs to
        say two things at once: where it is going (the solid sprite, drawn at
        the destination) and where it would go back to if the drag were
        abandoned. The same ghost brushes the placement preview uses, so a
        faded object means the same thing throughout the view.
        """
        radius = finisher_radius if (note.is_finisher or note.is_spinner) else normal_radius
        shiny = self.split_rows and round(note.time) in self.shiny_times
        brush = (
            self.ghost_shiny_brush if shiny
            else self.ghost_slider_brush if (note.is_slider or note.is_spinner)
            else self.ghost_kat_brush if note.is_kat
            else self.ghost_don_brush
        )
        draw_note_sprite(
            painter, brush, self.ghost_pen, x,
            self._row_y(baseline_y, shiny) if self.split_rows
            else baseline_y if self.symmetric
            else baseline_y - radius,
            radius,
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
        # "regular" is the fake slider layer's own tool -- it writes a shape
        # rather than a note, so it previews through _draw_layer_ghost below.
        # Without it here the one tool whose whole output is a drawn object was
        # the one tool that showed nothing under the cursor.
        if self.tool not in ("don", "kat", "regular", "shiny", "slider", "spinner") or self._hover_time is None:
            return

        snapped = self.snap_ms(self._hover_time)
        x = self.x_for_time(snapped)
        if x < -60 or x > self.width() + 60:
            return

        if self.tool == "slider":
            self._draw_extend_ghost(painter, baseline_y, normal_radius, finisher_radius, "slider", snapped, snapped, shift_held)
            return

        radius = finisher_radius if (self.tool == "spinner" or shift_held) else normal_radius
        note_center_y = baseline_y if self.symmetric else baseline_y - radius

        if self.ghost_style != "note" and self.tool in ("don", "kat", "regular", "shiny"):
            # Don is the small variant and Kat the big one in both layers, the
            # way the finisher bit decides size in a normal chart -- here the
            # tool decides it, since these structures carry no hitsound of their
            # own until they are written. A plain fake slider and a shiny have
            # no such pair, so Shift picks their size the same way it does for a
            # Don or Kat in a normal chart -- see gimmick_session.fake_slider.
            shift_sizes = shift_held and self.tool in ("regular", "shiny")
            self._draw_layer_ghost(
                painter, x, baseline_y,
                finisher_radius if (self.tool == "kat" or shift_sizes) else normal_radius,
            )
            return
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

    def _draw_layer_ghost(self, painter: QPainter, x: float, baseline_y: float, radius: float) -> None:
        """Don/Kat previewed as what *this layer* writes, not as a note.

        Same translucency as the normal chart's ghost -- what changes is the
        shape, because the structure does:

        * **fake_slider** -- the yellow drumroll body a fake slider is drawn as.
        * **barline** -- a bar, since a run of red lines is what that layer
          writes and a circle would promise a note that is not there. Kat's
          three mirrored pairs make it the wider bar to Don's one.
        """
        # A shiny previews white and on the lower row, which is exactly where it
        # will be drawn once written -- the two tools in this layer place the
        # same shape a millisecond apart, so the row is the only thing under the
        # cursor that says which one is about to happen.
        shiny = self.tool == "shiny"
        painter.setBrush(self.ghost_shiny_brush if shiny else self.ghost_slider_brush)
        painter.setPen(self.ghost_pen)
        if self.ghost_style == "fake_slider":
            y = self._row_y(baseline_y, shiny) if self.split_rows else baseline_y
            painter.drawEllipse(QPointF(x, y), radius, radius)
            return
        half_width = max(2.0, radius * 0.22)
        painter.drawRect(QRectF(x - half_width, baseline_y - radius, half_width * 2, radius * 2))


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


class SVEditorView(TimeAxisMixin, QWidget):
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

    seek_requested = Signal(float)
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
    # uid -- double click on a line of either kind. Same signal and same dialog
    # as TimelineGameplay's: a green line's SV and a red line's BPM are the same
    # edit seen from two views, and both can be retimed there.
    timing_line_edit_requested = Signal(int)
    # list[uid] -- Delete/Backspace with green lines selected. One signal for
    # the whole selection so the owner can push a single undo step.
    points_delete_requested = Signal(object)
    # New window_ms after a Ctrl+wheel zoom -- see TimelineGameplay.zoom_changed.
    zoom_changed = Signal(float)

    # Twice the chart views' limit: an SV sweep is read across a whole section,
    # a note pattern is not. This was already true before the shared axis was
    # extracted -- it is a deliberate difference, not drift.
    MAX_WINDOW_MS = 120000.0

    # See TimelineGameplay.DESIGN_HEIGHT: the height the graph's margins were
    # measured at, and this view's minimum outside the gimmick page.
    DESIGN_HEIGHT = TimelineGameplay.DESIGN_HEIGHT

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
        # Set true only for the Kiai and Sound Volume layer (MainWindow.
        # _add_editor_view). Switches the vertical axis, the connected curve
        # and vertical drags from SV to a plain 0-100 volume percentage; the
        # red/green/yellow line placement itself is unchanged.
        self.volume_mode = False

        self.drag_start_x: float | None = None
        self.drag_anchor_time: float | None = None
        self.drag_mouse_x = 0.0
        self.wheel_accumulator = 0.0
        self.last_rendered_time = -1.0
        self._drag_point: TimingPoint | None = None
        self._drag_axis: str = "time"  # "time" or "value", set by _begin_point_drag
        # Paint caches, rebuilt per edit rather than per frame -- see
        # _rebuild_caches for why that distinction is load-bearing.
        # Milliseconds this view shows SV for, or None to show every point.
        # The gimmick page's three SV layers each own one structure's SV --
        # see MainWindow._sv_layer_times -- and set this to the timestamps of
        # the objects they belong to. Only inherited points survive the filter,
        # which is also why those layers draw green where the plain SV editor
        # draws yellow: the red line sharing the millisecond is not in the view.
        #
        # `point_times_for` derives it from the document on every refresh, which
        # is what keeps it honest: an edit anywhere can add the very object a
        # layer is keyed to, and refreshes arrive from several paths.
        self.point_times: set[int] | None = None
        self.point_times_for = None
        self._visible_points: list[TimingPoint] = []
        # Write the BPM beside each red line. Off in the barline SV layer: the
        # number belongs next to the lines themselves, which is layer 3, and a
        # barline gimmick puts thousands of them in one window.
        self.show_bpm_labels = True
        self._series: list[tuple[float, float]] = []
        self._series_times: list[float] = []
        # Effective volume over time, used only when volume_mode is True. Kept
        # separate from _series/_series_times rather than repurposing them:
        # SV resolution resets to 1.0x at an uninherited point, volume does
        # not (every point, red or green, carries its own .volume), so the
        # two curves are built by different rules from the same points.
        self._volume_series: list[tuple[float, float]] = []
        self._volume_series_times: list[float] = []
        self._line_kinds: dict[float, str] = {}
        self._line_kind_times: list[float] = []
        self._bpm_at: dict[float, float] = {}
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

        self.setMinimumHeight(self.DESIGN_HEIGHT)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- data ----------------------------------------------------------------

    def load_document(self, document) -> None:
        self.refresh_points(document)
        self.current_time = float(self.timing_points[0].time if self.timing_points else 0)
        self.update()

    def refresh_points(self, document) -> None:
        """Re-read timing points after an edit, without resetting the cursor."""
        if self.point_times_for is not None:
            self.point_times = self.point_times_for(document)
        self.set_timing_points(document.timing_points)
        # Points deleted elsewhere must not stay "selected" forever.
        live = {point.uid for point in self.timing_points}
        self.selected_uids &= live
        self.update()

    def set_timing_points(self, points) -> None:
        """Adopt a point list (any order) and rebuild the paint caches."""
        self.timing_points = sorted_by_time(points)
        if self.kiai_bands_for is not None:
            self.kiai_bands = self.kiai_bands_for(self.timing_points)
        else:
            self.set_kiai_from(self.timing_points)
        self._rebuild_caches()

    def _rebuild_caches(self) -> None:
        """Per-timestamp series and line colours, computed once per edit.

        These used to be rebuilt from every point on every paint -- three full
        passes per frame, per open view, at the playback frame rate. On an
        ordinary map that is invisible; on a gimmick map carrying tens of
        thousands of timing points it is the difference between scrolling and
        a frozen window.
        """
        self._visible_points = (
            self.timing_points if self.point_times is None
            else [
                point for point in self.timing_points
                if not point.uninherited and round(point.time) in self.point_times
            ]
        )
        by_time: dict[float, float] = {}
        kinds: dict[float, set[bool]] = {}
        for point in self._visible_points:
            kinds.setdefault(point.time, set()).add(point.uninherited)
            if point.uninherited and point.time in by_time:
                # An inherited point already claimed this millisecond; a red
                # line stacked with a green one does not reset it back to 1.0x.
                continue
            by_time[point.time] = point.sv_multiplier
        self._series = sorted(by_time.items())
        self._series_times = [time_ms for time_ms, _sv in self._series]
        # Effective volume: unlike SV, every point (red or green) carries its
        # own value and none of them reset it, so this is a plain
        # last-in-file-order-wins fold over the same points, iterated in
        # `self.timing_points`' sorted (stable) order.
        by_time_volume: dict[float, float] = {}
        for point in self._visible_points:
            by_time_volume[point.time] = float(point.volume)
        self._volume_series = sorted(by_time_volume.items())
        self._volume_series_times = [time_ms for time_ms, _volume in self._volume_series]
        # Keyed by the point's exact time, not by a rounded millisecond: this
        # is where the line is *drawn*, and a point at 4845.4 rounded to 4845
        # drew half a millisecond away from the note it belongs to -- invisible
        # at chart zoom, a third of the view at the 20ms floor, which is where
        # gimmick work happens. Rounding also merged two points half a
        # millisecond apart into one line.
        self._line_kinds = {
            time_ms: "yellow" if len(flags) > 1 else "red" if True in flags else "green"
            for time_ms, flags in kinds.items()
        }
        self._line_kind_times = sorted(self._line_kinds)
        # Only built where it is read, and reading `.bpm` once per point
        # rather than twice: it is a division behind a property, this runs for
        # every point in every SV view on every refresh, and a gimmick map
        # carries tens of thousands of them. The label pass is the only
        # consumer, so a view that does not draw labels does not pay for it.
        self._bpm_at = {}
        if self.show_bpm_labels:
            for point in self.timing_points:
                if not point.uninherited:
                    continue
                bpm = point.bpm
                if bpm:
                    self._bpm_at[point.time] = bpm
        # The red lines on their own. active_uninherited_at steps back over
        # inherited points one at a time, and this view's whole subject matter
        # is maps with thousands of them between two red lines -- asking it
        # against the full list, once per curve segment per frame, was the SV
        # editor's entire paint cost.
        self._beat_points = uninherited_points(self.timing_points)
        if self.volume_mode:
            # Volume is a plain 0-100% scale, never the log SV ladder -- set
            # here too (not only in update_scale) so a mouse handler that
            # reads scale_min/max between paints never sees a stale SV range.
            self.scale_min, self.scale_max = 0.0, 100.0
        elif self.autoscale:
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

    def line_kinds(self) -> dict[float, str]:
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

    # -- SV <-> vertical position ----------------------------------------------

    def _margin_scale(self) -> float:
        """How much of the designed margins this view's height can afford.

        The margins hold the axis labels and the BPM text, and at DESIGN_HEIGHT
        they cost 56 of 180 pixels. Kept at that on a gimmick band -- a third of
        its height -- they left the curve confined to the middle of the view.
        """
        return min(1.0, self.height() / self.DESIGN_HEIGHT)

    def _graph_top(self) -> float:
        return 20.0 * self._margin_scale()

    def _graph_bottom(self) -> float:
        return self.height() - 36.0 * self._margin_scale()

    def _sv_to_y(self, sv: float, top: float, bottom: float) -> float:
        low, high = self.scale_min, self.scale_max
        clamped = max(low, min(high, sv))
        if self.volume_mode:
            # Linear 0-100%, not log SV -- math.log(0) would crash here, and a
            # log scale has no meaning for a volume percentage anyway.
            ratio = (clamped - low) / (high - low)
        else:
            ratio = (math.log(clamped) - math.log(low)) / (math.log(high) - math.log(low))
        return bottom - ratio * (bottom - top)

    def _y_to_sv(self, y: float, top: float, bottom: float) -> float:
        low, high = self.scale_min, self.scale_max
        ratio = max(0.0, min(1.0, (bottom - y) / max(1.0, bottom - top)))
        if self.volume_mode:
            return low + ratio * (high - low)
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

        In volume_mode the whole SV_BOUND ladder is bypassed: the axis is
        always a fixed 0%-100%, because that is what a volume percentage is.
        """
        if self.volume_mode:
            self.scale_min, self.scale_max = 0.0, 100.0
            return self.scale_min, self.scale_max
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
        # Hit-testing follows what is drawn, or a filtered layer would grab and
        # edit a point it never showed.
        best = None
        best_distance = None
        for point in self._visible_points:
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
        for point in self._visible_points:
            distance = abs(self.x_for_time(point.time) - x)
            if distance <= radius_px and (best_distance is None or distance < best_distance):
                best = point
                best_distance = distance
        return best

    def _placement_time(self, x: float) -> float:
        """Where a green line placed at `x` would go.

        In a layer that owns a fixed set of milliseconds (`point_times`), the
        nearest of those rather than the nearest snap division. A green line
        anywhere else in such a layer is invisible in it -- `_rebuild_caches`
        filters on exactly this set -- and gets reset to 1.0x by the next
        uninherited line anyway, which is why placement used to look like it
        did nothing at all.
        """
        if self.point_times:
            return float(min(self.point_times, key=lambda t: abs(self.x_for_time(t) - x)))
        return self.snap_ms(self.time_for_x(x))

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
        "time" -- see SV_DOT_HIT_RADIUS_PX for the threshold.

        Always "time" in volume_mode too: this layer has nothing to do with
        SV, and a vertical drag there must retime the line, never write one.
        """
        if self.volume_mode:
            return "time"
        if point.uninherited:
            return "time"
        dot = self._dot_position(point)
        distance = math.hypot(pos.x() - dot.x(), pos.y() - dot.y())
        return "value" if distance <= SV_DOT_HIT_RADIUS_PX else "time"

    # -- mouse -----------------------------------------------------------------

    def mouseDoubleClickEvent(self, event) -> None:
        """Double click any line to retype its SV (or BPM) and its time.

        Reaches red lines too: this view draws both kinds, and a BPM line
        double-clicked here is the same edit the chart layers offer.
        """
        if event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        point = self._nearest_point(event.position().x())
        if point is None:
            super().mouseDoubleClickEvent(event)
            return
        self.timing_line_edit_requested.emit(point.uid)
        event.accept()

    def _begin_point_drag(self, point: TimingPoint, axis: str) -> None:
        self._drag_point = point
        self._drag_axis = axis
        self.selected_uids = {point.uid}
        self.grabMouse()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            # A drag-selection (function-range or select-mode rubber-band) is
            # live and this press landed on the page's timing bar rather than
            # in the view: scrub the playhead there instead of deleting a
            # point, the same reasoning as TimelineGameplay.mousePressEvent.
            if (self.drag_anchor_time is not None or self.select_anchor_time is not None) and (
                self._press_is_on_timing_bar(event.globalPosition().toPoint())
            ):
                self._bar_scrubbing = True
                self._scrub_bar_to(event.globalPosition().toPoint())
                event.accept()
                return
            point = self._nearest_inherited(event.position().x())
            if point is not None:
                self.point_delete_requested.emit(point.uid)
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            return

        if self.tool in ("function", "kiai", "volume"):
            # Same gesture for all three: drag a range, then the owner decides
            # what to do with it. Kiai and Volume are the Kiai and Sound Volume
            # layer's tools -- see MainWindow._sv_range_action.
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
            time_ms = self._placement_time(event.position().x())
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
            for point in self._visible_points
            if not point.uninherited and a <= point.time <= b
        }
        self.update()

    def _scrub_bar_to(self, global_pos) -> None:
        """Move the playhead to the timing bar under `global_pos`, and grow
        whichever range drag is currently live to follow it.

        The anchor stays pinned in time -- only `current_time` changes, and
        the view scrolls under it -- so the far edge (`self.time_for_x` of the
        moving side) always reads as the scrub target once the view re-centres
        on it. Only one of `drag_anchor_time` / `select_anchor_time` is ever
        set at a time (function-range mode vs. select mode), so exactly one
        branch here has anything to update.
        """
        self.current_time = self._time_on_timing_bar(global_pos)
        self.seek_requested.emit(self.current_time)
        if self.drag_anchor_time is not None:
            self.drag_mouse_x = self.x_for_time(self.current_time)
            self.update()
        elif self.select_anchor_time is not None:
            self.select_mouse_x = self.x_for_time(self.current_time)
            self._update_range_selection()

    def _auto_scroll_selection(self) -> None:
        """Scroll the view when a rubber-band drag runs past its edges.

        Same acceleration curve as TimelineGameplay's: without it, a selection
        can never be larger than one screenful, since the view stands still
        however far the cursor goes.
        """
        if self.select_anchor_time is None:
            self.auto_scroll_timer.stop()
            return
        if self.auto_scroll_step(self.select_mouse_x):
            self._update_range_selection()

    def mouseMoveEvent(self, event) -> None:
        if self._bar_scrubbing:
            self._scrub_bar_to(event.globalPosition().toPoint())
            return
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
                # Same rule as placement: in a layer keyed to its own objects,
                # a line dragged between them would vanish from the layer that
                # was dragging it.
                self.point_time_edit_requested.emit(
                    self._drag_point.uid, self._placement_time(event.position().x())
                )
            self.update()
            return
        # Ghost preview: only green-line mode has something to preview, but
        # the cursor millisecond is drawn in every mode -- see
        # TimelineGameplay.mouseMoveEvent for why the repaint is unconditional.
        self._hover_time = self.time_for_x(event.position().x())
        self._hover_sv = self._y_to_sv(event.position().y(), self._graph_top(), self._graph_bottom())
        self.update()

    def leaveEvent(self, event) -> None:
        """See TimelineGameplay.leaveEvent."""
        self._hover_time = None
        self._hover_sv = None
        super().leaveEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            # A right release only ever matters here as the end of a
            # bar-scrub (see mousePressEvent above) -- it must never fall
            # through to the generic release logic below, which would end
            # whatever drag a left button is still holding open.
            self._bar_scrubbing = False
            event.accept()
            return
        if self.drag_anchor_time is not None:
            self.releaseMouse()
            a, b = sorted((self.drag_anchor_time, self.time_for_x(self.drag_mouse_x)))
            if self.tool == "kiai":
                # A kiai section starts and ends on the grid the mapper is
                # looking at, not on whatever fraction of a millisecond the
                # cursor happened to be over -- and `snap_ms` is also where
                # Ctrl means "whole milliseconds instead", which is the
                # override this tool needs for sections that dodge a line.
                a, b = self.snap_ms(a), self.snap_ms(b)
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
                for point in self._visible_points
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
        self.draw_kiai_bands(painter)
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
            {point.time for point in self.timing_points if point.uid in self.selected_uids}
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
            bpm = self._bpm_at.get(time_ms) if self.show_bpm_labels else None
            if bpm and clear_of_previous:
                painter.setPen(self.label_pen)
                painter.drawText(QPointF(x + 4, self.height() - 6), f"{bpm:.0f} BPM")

        self._draw_sv_curve(painter, top, bottom, start_time, end_time)
        self._draw_placement_ghost(painter, top, bottom)
        self.draw_ctrl_precision_line(painter)
        self.draw_cursor_position(painter)

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
        if self.volume_mode:
            painter.drawText(QPointF(4, top + 12), f"{self.scale_max:.0f}%")
            painter.drawText(QPointF(4, bottom - 4), f"{self.scale_min:.0f}%")
        else:
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
        # In volume_mode this plots effective volume (carried forward from
        # each point's own .volume, never reset by an uninherited point) --
        # see _rebuild_caches -- rather than effective SV. Same step/diagonal
        # drawing rules either way: a volume sweep generated by the Volume
        # tool (2d) is eased between existing points exactly like an SV sweep,
        # so the same visual makes the easing shape readable.
        series = self._volume_series if self.volume_mode else self._series
        series_times = self._volume_series_times if self.volume_mode else self._series_times
        if not series:
            return

        # Sliced by bisect, not by filtering the whole series three times: this
        # runs per frame per view, and a gimmick map carries tens of thousands
        # of points.
        margin = self.window_ms * 0.1
        first = bisect_left(series_times, start_time - margin)
        after_last = bisect_right(series_times, end_time + margin)
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
            label = f"{sv:.0f}%" if self.volume_mode else f"{sv:.2f}x"
            painter.drawText(QPointF(point.x() + 3, point.y() - 4), label)

    def _draw_placement_ghost(self, painter: QPainter, top: float, bottom: float) -> None:
        """Dashed preview of the green line a click would place, at the snapped
        time and the SV the cursor's height maps to -- the SV editor's
        equivalent of the chart view's translucent note ghost.
        """
        if self.tool != "green_line" or self._hover_time is None:
            return
        snapped = self._placement_time(self.x_for_time(self._hover_time))
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
        # Predicate deciding which hit objects scroll past; see refresh_notes.
        self.object_filter = None
        # Both kinds, sorted: SV needs the inherited points, the beat grid and
        # barlines need the uninherited ones.
        self.timing_points: list[TimingPoint] = []
        self.beat_points: list[TimingPoint] = []
        self._beat_times: list[float] = []
        # (start_ms, end_ms) kiai sections; see refresh_notes and paintEvent's
        # pulse block. kiai_bands_for lets a host share the cached bands the
        # way other views do -- see MainWindow._share_kiai_bands.
        self.kiai_bands: list[tuple[int, int]] = []
        self.kiai_bands_for = None
        # The map's [Difficulty] SliderMultiplier, until a document is loaded.
        self.slider_multiplier = SLIDER_MULTIPLIER_ASSUMED
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
        # Same predicate TimelineGameplay.object_filter is: the gimmick page's
        # per-layer gameplay previews show one layer's objects scrolling, so a
        # fake slider can be judged without the real chart's notes over it.
        # Timing is never filtered -- it is what moves the notes.
        if self.object_filter is None:
            self.notes = document.hit_objects
        else:
            self.notes = [note for note in document.hit_objects if self.object_filter(note)]
        self.note_times = [note.time for note in self.notes]
        self.timing_points = sorted_by_time(document.timing_points)
        if self.kiai_bands_for is not None:
            self.kiai_bands = self.kiai_bands_for(document)
        else:
            self.kiai_bands = kiai_spans(self.timing_points, KIAI_OPEN_END_MS)
        self.slider_multiplier = document.slider_multiplier
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
                note.length * note.slides, timing.beat_length,
                sv_at(self.timing_points, note.time), self.slider_multiplier,
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
            # Swallowed, not acted on. This view exists to show the chart at
            # the speed the *player* sees, which is the map's own scroll
            # velocity -- rescaling it is a picture of something the game will
            # never draw, so there is nothing here for Ctrl+wheel to mean.
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
        # Gated on the *playhead* alone. Arriving at a chorus lights up the
        # whole screen, which is what it does in game -- so every visible
        # object flashes, not only the ones whose own millisecond is inside
        # the section. Notes were previously filtered by their own kiai state
        # as well, which meant the section's first beat lit up a handful of
        # notes and left the rest of the screen dark.
        # Anchor the pulse to the kiai section under the playhead, not to
        # whatever timing point governs it -- see beat_pulse. kiai_bands is
        # sorted by start, so bisect straight to the band the playhead could
        # be in rather than scanning it.
        band_index = bisect_right(self.kiai_bands, self.current_time, key=lambda band: band[0]) - 1
        anchor_ms = None
        if 0 <= band_index < len(self.kiai_bands):
            band_start, band_end = self.kiai_bands[band_index]
            if band_start <= self.current_time < band_end:
                anchor_ms = band_start
        pulse = beat_pulse(self.beat_points, self.current_time, anchor_ms) if anchor_ms is not None else 0.0
        if pulse > 0.0:
            # Subtle white wash over the whole lane, drawn before the per-note
            # flashes below so notes still read brighter than the background.
            playfield_alpha = round(PLAYFIELD_PULSE_ALPHA * pulse)
            if playfield_alpha > 0:
                painter.fillRect(self.rect(), QColor(255, 255, 255, playfield_alpha))
            for note in self.notes[first:after_last]:
                x = self.x_for_time(note.time)
                radius = big_radius if (note.is_finisher or note.is_spinner) else normal_radius
                if -radius <= x <= self.width() + radius:
                    draw_kiai_flash(
                        painter, x, center_y, radius, pulse, kiai_flash_strength(note),
                    )

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


def timeline_bar_data(document, duration_ms: int):
    """Everything a TimingOverviewBar draws, derived once.

    Three bars exist -- one per page -- and they all show the same document, so
    this is computed once per edit and applied to each of them rather than each
    walking the whole map for itself. Returned as a plain tuple so a bar can
    adopt it without knowing where it came from.
    """
    duration = max(duration_ms, document.hit_objects[-1].time if document.hit_objects else 1, 1)
    kiai = kiai_ranges(document, duration)
    bookmarks, preview_time = editor_timeline_metadata(document)
    markers = [(round(point.time), point.uninherited) for point in document.timing_points]
    # Grouped once per edit, not per frame: a gimmick map carries tens of
    # thousands of points and the bar repaints on every clock tick.
    groups: dict[int, set] = {}
    for time_ms, uninherited in markers:
        groups.setdefault(time_ms, set()).add(uninherited)
    kinds = sorted(
        (time_ms, "yellow" if len(flags) > 1 else "red" if True in flags else "green")
        for time_ms, flags in groups.items()
    )
    return duration, kiai, markers, bookmarks, preview_time, kinds


class TimingOverviewBar(QWidget):
    seek_requested = Signal(int)
    def __init__(self) -> None:
        super().__init__()
        self.duration_ms=1; self.current_time=0; self.viewport_start=0; self.viewport_end=0
        self.kiai=[]; self.timing_markers=[]; self._marker_kinds=[]; self.bookmarks=[]; self.preview_time=None; self.dragging=False
        self._marker_cache_key=None; self._marker_lines=[]
        self.setFixedHeight(28); self.setCursor(Qt.PointingHandCursor)
    def load_document(self,document,duration_ms:int)->None:
        self.apply_document_data(timeline_bar_data(document,duration_ms))

    def apply_document_data(self,data)->None:
        """Adopt data already derived by `timeline_bar_data`.

        Split out because three of these bars exist -- one per page -- all
        showing the same document. Each one deriving it for itself walked every
        timing point in the map three times over on every edit.
        """
        self.duration_ms,self.kiai,self.timing_markers,self.bookmarks,self.preview_time,self._marker_kinds=data
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

    def __init__(self, view_type: str, difficulty_label: str, compact: bool = False) -> None:
        super().__init__()
        self.view_type = view_type
        self.locked = False
        self.compact = compact
        self.content: QWidget | None = None

        self.setStyleSheet("background: #1b212b; border: 1px solid #303947; border-radius: 6px;")

        layout = QVBoxLayout(self)
        margin = 3 if compact else 6
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(2 if compact else 4)

        # EditorViewFrame's own bare (selector-less) stylesheet above breaks
        # the app-wide pink QPushButton cascade for its descendants, so the
        # chrome buttons render small and low-contrast unless styled here
        # explicitly.
        #
        # Compact trims the padding for the gimmick page, where six frames of
        # chrome are stacked in one screen and the buttons cost more room than
        # the layers they belong to. Padding rather than a typed size: the glyph
        # still decides how much space it needs.
        chrome_padding = "2px 5px" if compact else "6px 10px"
        chrome_button_style = (
            "QPushButton { background: #f3a6bd; color: #17191f; border: 0;"
            f" border-radius: 6px; font-weight: 600; padding: {chrome_padding}; }}"
            "QPushButton:hover { background: #f7bfd0; }"
            "QPushButton:checked { background-color: #ff66aa; color: #ffffff;"
            " border: 1px solid #ff9dcc; font-weight: 700; }"
        )

        chrome = QHBoxLayout()
        self.close_button = QPushButton("✕")
        self.close_button.setToolTip(tr("MainWindow", "Close view"))
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setStyleSheet(chrome_button_style)
        self.close_button.clicked.connect(lambda: self.closed.emit(self))
        chrome.addWidget(self.close_button)

        self.lock_button = QPushButton("🔒")
        self.lock_button.setCheckable(True)
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

    def showEvent(self, event) -> None:
        # Both glyphs vanished when the window stylesheet's button padding grew
        # to 8px 16px: a typed 28px width left the label negative room, so Qt
        # drew nothing. Measured after polish (and after parenting, which is
        # what makes the app-wide sheet apply), like the playback rows.
        super().showEvent(event)
        equalize_button_widths((self.close_button, self.lock_button))

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
        self.type_combo.addItem(tr("MainWindow", "Regular Chart Only"), "chart_regular")
        self.type_combo.addItem(tr("MainWindow", "Fake Sliders Only"), "chart_fake_slider")
        self.type_combo.addItem(tr("MainWindow", "Barlines Only"), "chart_barline")
        self.type_combo.addItem(tr("MainWindow", "SV Editor"), "sv")
        self.type_combo.addItem(tr("MainWindow", "Kiai and Sound Volume"), "kiai_sound")
        self.type_combo.addItem(tr("MainWindow", "Gameplay Viewer"), "gameplay")
        self.type_combo.addItem(tr("MainWindow", "Gameplay: Regular Chart Only"), "gameplay_regular")
        self.type_combo.addItem(tr("MainWindow", "Gameplay: Fake Sliders Only"), "gameplay_fake_slider")
        self.type_combo.addItem(tr("MainWindow", "Gameplay: Barlines Only"), "gameplay_barline")
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


class GimmickEntryDialog(QDialog):
    """Asked once per difficulty, the first time the Gimmick tab is opened.

    Gimmick editing fills a map with 60000 BPM lines and objects that cannot be
    hit. That is not something to do to the only copy of a chart by accident, so
    the default answer creates a separate difficulty and the dialog says plainly
    what each button does before anything is written.

    The answer is remembered permanently (gimmick_index.json), so this is asked
    once per difficulty rather than once per visit.
    """

    CREATE = "create"
    USE_CURRENT = "use_current"
    CANCEL = "cancel"

    def __init__(self, version: str, parent=None, references: list[tuple[str, Path]] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("MainWindow", "Gimmick editor"))
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self._action = self.CANCEL

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        headline = QLabel(tr("MainWindow", "Edit gimmicks on a new difficulty?"))
        headline.setStyleSheet("font-weight: 600;")
        layout.addWidget(headline)

        body = QLabel(
            tr(
                "MainWindow",
                "Yes creates a new difficulty called \"{0}\" in this song folder, "
                "copied from the one you have open, and edits that.\n\n"
                "Use This One edits the difficulty you already have open, without "
                "making a copy.\n\n"
                "Either way, keep an un-gimmicked version of the chart: gimmick "
                "timing cannot be cleanly undone once the file is saved.",
            ).format(gimmick_version_for(version))
        )
        body.setWordWrap(True)
        layout.addWidget(body)

        # Which difficulty the grid comes from. Asked here rather than
        # discovered later because the answer has to be settled before the first
        # placement writes a 60000 BPM line into whatever would otherwise have
        # been the source of it.
        layout.addWidget(QLabel(tr("MainWindow", "Timing reference")))
        self.reference_combo = QComboBox()
        # Difficulty names are long and the combo sized itself to the shortest
        # one, so the entry it was showing was usually elided into
        # unreadability -- the one control on this dialog whose whole job is to
        # say which file you are choosing.
        self.reference_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.reference_combo.setMinimumWidth(360)
        for label, path in references or ():
            self.reference_combo.addItem(label, str(path))
        note = QLabel()
        note.setWordWrap(True)
        note.setStyleSheet("color:#ffb347;border:0;")
        if self.reference_combo.count():
            note.setText(tr(
                "MainWindow",
                "Snaps, scrolling and the BPM overlay follow this difficulty's timing "
                "for the life of the pairing, so the barlines you place here never move "
                "the grid you place them against.",
            ))
        else:
            self.reference_combo.addItem(
                tr("MainWindow", "This difficulty (no other one exists)"), ""
            )
            note.setText(tr(
                "MainWindow",
                "There is no other difficulty in this song folder to take timing from. "
                "The one you have open will be used, which works until it has gimmicks "
                "of its own -- making a plain, correctly-timed difficulty first is worth "
                "the minute it costs.",
            ))
        layout.addWidget(self.reference_combo)
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        # Explicit roles rather than Yes/No: all three are affirmative answers
        # to different questions, and only Cancel actually leaves.
        create = buttons.addButton(tr("MainWindow", "Yes"), QDialogButtonBox.AcceptRole)
        use_current = buttons.addButton(
            tr("MainWindow", "Use This One"), QDialogButtonBox.ActionRole
        )
        buttons.addButton(tr("MainWindow", "No"), QDialogButtonBox.RejectRole)
        create.clicked.connect(lambda: self._finish(self.CREATE))
        use_current.clicked.connect(lambda: self._finish(self.USE_CURRENT))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        equalize_button_widths((create, use_current))

    def _finish(self, action: str) -> None:
        self._action = action
        self.accept()

    def selected_action(self) -> str:
        """CREATE, USE_CURRENT, or CANCEL if the dialog was dismissed."""
        return self._action

    def selected_reference(self) -> Path | None:
        """The difficulty to take timing from, or None for the open one."""
        data = self.reference_combo.currentData()
        return Path(data) if data else None


class GimmickConfigDialog(QDialog):
    """One layer's toolbox settings.

    Two layers, two configs: the barline layer's red line spacing and the fake
    slider layer's offset are separate numbers that must not agree, so each
    layer opens its own dialog and both show the caution naming the other's
    value. `other` is that other config -- read only, for the caution.
    """

    def __init__(self, config: GimmickConfig, layer_id: str, other: GimmickConfig, parent=None) -> None:
        super().__init__(parent)
        self.layer_id = layer_id
        self._other = other
        sv = layer_id.startswith("sv_")
        if sv:
            title = tr("MainWindow", "SV layer settings")
        elif layer_id == "barline":
            title = tr("MainWindow", "Barline settings")
        else:
            title = tr("MainWindow", "Fake slider settings")
        self.setWindowTitle(title)
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        layout = QFormLayout(self)
        barline = layer_id == "barline"

        self.bpm_spin = QDoubleSpinBox()
        self.bpm_spin.setRange(1.0, 1000000.0)
        self.bpm_spin.setDecimals(0)
        self.bpm_spin.setValue(config.gimmick_bpm)
        if not sv:
            layout.addRow(tr("MainWindow", "Gimmick BPM"), self.bpm_spin)

        self.sv_offset_spin = QSpinBox()
        self.sv_offset_spin.setRange(-5000, 5000)
        self.sv_offset_spin.setValue(config.sv_offset_ms)

        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(1, 1000)
        self.spacing_spin.setValue(config.spacing_ms)
        self.spacing_spin.valueChanged.connect(self._update_caution)

        # Kat's three mirrored pairs, independently configurable -- see the
        # comment above `gimmick_session.DEFAULT_SPACING_MS`. Each feeds the
        # same caution as the Don spacing above: any one of the four landing
        # on the fake slider's own offset is a collision (`spacing_collides`
        # now checks all four), so every spin box here re-runs it.
        self.kat_spacing1_spin = QSpinBox()
        self.kat_spacing1_spin.setRange(1, 1000)
        self.kat_spacing1_spin.setValue(config.kat_spacing1_ms)
        self.kat_spacing1_spin.valueChanged.connect(self._update_caution)

        self.kat_spacing2_spin = QSpinBox()
        self.kat_spacing2_spin.setRange(1, 1000)
        self.kat_spacing2_spin.setValue(config.kat_spacing2_ms)
        self.kat_spacing2_spin.valueChanged.connect(self._update_caution)

        self.kat_spacing3_spin = QSpinBox()
        self.kat_spacing3_spin.setRange(1, 1000)
        self.kat_spacing3_spin.setValue(config.kat_spacing3_ms)
        self.kat_spacing3_spin.valueChanged.connect(self._update_caution)

        self.fake_offset_spin = QSpinBox()
        self.fake_offset_spin.setRange(1, 1000)
        self.fake_offset_spin.setValue(config.fake_slider_offset_ms)
        self.fake_offset_spin.valueChanged.connect(self._update_caution)

        self.length_spin = QDoubleSpinBox()
        # Negative only: a positive length is a real, hittable drumroll.
        self.length_spin.setRange(-100000.0, -0.0001)
        self.length_spin.setDecimals(4)
        self.length_spin.setValue(config.fake_slider_length)

        self.fake_sv_spin = QDoubleSpinBox()
        self.fake_sv_spin.setRange(0.01, 100.0)
        self.fake_sv_spin.setDecimals(2)
        self.fake_sv_spin.setValue(config.fake_slider_sv)

        self.place_notes_check = QCheckBox(
            tr("MainWindow", "Also place the note itself")
        )
        self.place_notes_check.setChecked(config.place_notes)

        # Mirrored reads as one object centred on the note; unticked, the bars
        # all trail it -- a squash on the note and its restores after it, which
        # is the style several hand-made maps are written in and which nothing
        # here could ask for before.
        self.mirror_lines_check = QCheckBox(
            tr("MainWindow", "Mirror bars on both sides")
        )
        self.mirror_lines_check.setChecked(config.mirror_lines)

        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(-1000, 1000)
        self.offset_spin.setValue(config.red_line_offset_ms)

        # An uninherited point's BPM is also its scroll speed, so the plain red
        # line tool is a speed change with no green line involved -- but only
        # when it carries a BPM of its own. Unchecked it writes the chart's own
        # BPM, which is the line that changes nothing but where the bars fall.
        self.red_bpm_check = QCheckBox(tr("MainWindow", "Custom"))
        self.red_bpm_check.setChecked(config.red_line_bpm is not None)
        self.red_bpm_spin = QDoubleSpinBox()
        self.red_bpm_spin.setRange(0.001, 1000000.0)
        self.red_bpm_spin.setDecimals(3)
        self.red_bpm_spin.setValue(
            config.red_line_bpm if config.red_line_bpm is not None
            else DEFAULT_RED_LINE_BPM
        )
        self.red_bpm_spin.setEnabled(self.red_bpm_check.isChecked())
        self.red_bpm_check.toggled.connect(self.red_bpm_spin.setEnabled)
        red_bpm_row = QHBoxLayout()
        red_bpm_row.setContentsMargins(0, 0, 0, 0)
        red_bpm_row.addWidget(self.red_bpm_check)
        red_bpm_row.addWidget(self.red_bpm_spin, 1)
        self.red_bpm_widget = QWidget()
        self.red_bpm_widget.setLayout(red_bpm_row)

        # Bit 3 of the effects field. A fake slider's 60000 BPM line draws a bar
        # nobody asked for; the barline layer's whole output is bars, so it is
        # offered here only.
        self.omit_barline_check = QCheckBox()
        self.omit_barline_check.setChecked(config.omit_barline)

        self.shiny_offset_spin = QSpinBox()
        self.shiny_offset_spin.setRange(1, 1000)
        self.shiny_offset_spin.setValue(config.shiny_offset_ms)
        self.shiny_offset_spin.valueChanged.connect(self._update_caution)

        self.shiny_count_spin = QSpinBox()
        self.shiny_count_spin.setRange(1, 50)
        self.shiny_count_spin.setValue(config.shiny_count)

        self.shiny_bpm_spin = QDoubleSpinBox()
        self.shiny_bpm_spin.setRange(0.001, 1000.0)
        self.shiny_bpm_spin.setDecimals(3)
        self.shiny_bpm_spin.setValue(config.shiny_bpm_multiplier)

        self.fake_slider_bpm_spin = QDoubleSpinBox()
        self.fake_slider_bpm_spin.setRange(0.001, 1000.0)
        self.fake_slider_bpm_spin.setDecimals(3)
        self.fake_slider_bpm_spin.setValue(config.fake_slider_bpm_multiplier)

        # Each layer is shown the numbers it actually uses. The rest are still
        # carried through `config()` untouched, so opening one layer's dialog
        # cannot silently reset the other's structure.
        if sv:
            layout.addRow(tr("MainWindow", "Position offset (ms)"), self.sv_offset_spin)
            hint = QLabel(tr(
                "MainWindow",
                "How far ahead of its object this layer's green lines sit. An SV point "
                "governs what comes after it, so a note needs its line slightly early; a "
                "timing line needs it on the same millisecond, which is why the two "
                "gimmick SV layers default to 0.",
            ))
            hint.setWordWrap(True)
            layout.addRow(hint)
        elif barline:
            layout.addRow(tr("MainWindow", "Don Spacing (ms)"), self.spacing_spin)
            layout.addRow(tr("MainWindow", "Kat Spacing 1 (ms)"), self.kat_spacing1_spin)
            layout.addRow(tr("MainWindow", "Kat Spacing 2 (ms)"), self.kat_spacing2_spin)
            layout.addRow(tr("MainWindow", "Kat Spacing 3 (ms)"), self.kat_spacing3_spin)
            layout.addRow(tr("MainWindow", "Red line offset (ms)"), self.offset_spin)
            layout.addRow(tr("MainWindow", "Redline BPM"), self.red_bpm_widget)
            layout.addRow("", self.place_notes_check)
            layout.addRow("", self.mirror_lines_check)
        else:
            layout.addRow(tr("MainWindow", "Fake slider offset (ms)"), self.fake_offset_spin)
            layout.addRow(tr("MainWindow", "Fake slider length"), self.length_spin)
            layout.addRow(tr("MainWindow", "Don/Kat gimmick SV"), self.fake_sv_spin)
            layout.addRow(tr("MainWindow", "Omit barline"), self.omit_barline_check)
            layout.addRow(tr("MainWindow", "Fake slider redline BPM"), self.fake_slider_bpm_spin)
            layout.addRow(tr("MainWindow", "Shiny offset (ms)"), self.shiny_offset_spin)
            layout.addRow(tr("MainWindow", "Shiny note count"), self.shiny_count_spin)
            layout.addRow(tr("MainWindow", "Shiny redline BPM"), self.shiny_bpm_spin)

        self.caution = QLabel()
        self.caution.setWordWrap(True)
        self.caution.setStyleSheet("color:#ffb347;border:0;")
        layout.addRow(self.caution)

        self.shiny_caution = QLabel(tr(
            "MainWindow",
            "Caution: the shiny offset is the same as the fake slider offset. "
            "Both are drawn on the snap plus their offset, so they would land "
            "on the same millisecond -- and a stack of fake sliders on one "
            "millisecond is what a shiny note is, so the two become "
            "indistinguishable.",
        ))
        self.shiny_caution.setWordWrap(True)
        self.shiny_caution.setStyleSheet("color:#ffb347;border:0;")
        layout.addRow(self.shiny_caution)
        self._update_caution()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        pink_spin_buttons(self)

    def _update_caution(self) -> None:
        """Warn, don't refuse: the two values only actually clash where a
        barline note and a fake slider land on the same snap.

        `config()` can raise while the three Kat spin boxes are mid-edit --
        keyboard tracking fires on every keystroke, so typing a second Kat
        spacing through a value that briefly matches another is a live,
        reachable state, not just a theoretical one. Caught here rather than
        avoided: the cautions simply hold their last picture until the typing
        settles on something constructible again. `accept()` is the actual
        gate against leaving the dialog with a broken combination.
        """
        try:
            edited = self.config()
        except GimmickConfigError:
            return
        self.shiny_caution.setVisible(shiny_collides(edited))
        self.caution.setVisible(spacing_collides(*self._configs_for(edited)))
        self.caution.setText(tr(
            "MainWindow",
            "Caution: one of the barline spacings (Don or Kat) is the same "
            "as the fake slider offset. A fake slider writes 60000 BPM at "
            "its own millisecond and the chart's BPM one offset later, so a "
            "barline note placed on the same snap will fight it for that "
            "line.",
        ))

    def _configs_for(self, edited: GimmickConfig) -> tuple[GimmickConfig, GimmickConfig]:
        """(barline config, fake slider config), whichever this dialog edits."""
        return (edited, self._other) if self.layer_id == "barline" else (self._other, edited)

    def accept(self) -> None:
        # The three Kat spin boxes have no shared range to enforce
        # distinctness the way a single min/max keeps every other field here
        # constructible -- so unlike them, this one combination has to be
        # checked by hand before the dialog is allowed to close, or an
        # invalid GimmickConfig would reach `_open_gimmick_config` and raise
        # there instead, with no field left focused to point at the mistake.
        try:
            self.config()
        except GimmickConfigError as error:
            QMessageBox.warning(self, tr("MainWindow", "Cannot save this"), str(error))
            return
        super().accept()

    def config(self) -> GimmickConfig:
        return GimmickConfig(
            gimmick_bpm=self.bpm_spin.value(),
            spacing_ms=self.spacing_spin.value(),
            kat_spacing1_ms=self.kat_spacing1_spin.value(),
            kat_spacing2_ms=self.kat_spacing2_spin.value(),
            kat_spacing3_ms=self.kat_spacing3_spin.value(),
            fake_slider_length=self.length_spin.value(),
            red_line_offset_ms=self.offset_spin.value(),
            fake_slider_offset_ms=self.fake_offset_spin.value(),
            fake_slider_sv=self.fake_sv_spin.value(),
            place_notes=self.place_notes_check.isChecked(),
            mirror_lines=self.mirror_lines_check.isChecked(),
            sv_offset_ms=self.sv_offset_spin.value(),
            red_line_bpm=(
                self.red_bpm_spin.value() if self.red_bpm_check.isChecked() else None
            ),
            omit_barline=self.omit_barline_check.isChecked(),
            shiny_offset_ms=self.shiny_offset_spin.value(),
            shiny_count=self.shiny_count_spin.value(),
            shiny_bpm_multiplier=self.shiny_bpm_spin.value(),
            fake_slider_bpm_multiplier=self.fake_slider_bpm_spin.value(),
        )


class BarlineFunctionDialog(QDialog):
    """Fill a dragged range with red lines, every `n` ms or every `n` snaps.

    The barline layer's answer to the SV editor's function tool. Two rhythms,
    because a run of barlines is one of two different things:

    * **Every n (ms)** -- scenery. n = 1 is the default because a barline
      gimmick's whole vocabulary is lines packed as tightly as the format
      allows, and coarser runs are what you tune it up to, not down from.
    * **Every n snaps** -- rhythm. Walks the chart's own beat grid at the chosen
      divisor, so the bars land where the music does rather than on a
      millisecond count that drifts against it.

    The snap walk uses the base timing snapshot, not the document: the document
    is the file being filled with 60000 BPM lines, and stepping along *those*
    would collapse the run into the first few milliseconds.
    """

    def __init__(
        self, start_ms: float, end_ms: float, parent=None,
        base_timing: list[TimingPoint] | None = None, snap_divisor: int = 4,
        note_times: set[int] | None = None, current_sv: float = 1.0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("MainWindow", "Generate red lines"))
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.base_timing = base_timing or []
        self.note_times = note_times or set()

        layout = QFormLayout(self)
        layout.addRow(QLabel(
            tr("MainWindow", "Range: {0} to {1}").format(format_time(round(start_ms)), format_time(round(end_ms)))
        ))

        self.mode_combo = QComboBox()
        # Literal tr() calls, one per option -- see AddViewDialog on why a dict
        # lookup here would escape the i18n coverage gate.
        self.mode_combo.addItem(tr("MainWindow", "Every n (ms)"), "ms")
        self.mode_combo.addItem(tr("MainWindow", "Every n snaps"), "snaps")
        layout.addRow(tr("MainWindow", "Generate at"), self.mode_combo)

        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(1, 10000)
        self.spacing_spin.setValue(1)
        layout.addRow(tr("MainWindow", "Every n (ms)"), self.spacing_spin)

        self.snap_count_spin = QSpinBox()
        self.snap_count_spin.setRange(1, 64)
        self.snap_count_spin.setValue(1)
        layout.addRow(tr("MainWindow", "Every n snaps"), self.snap_count_spin)

        self.snap_combo = QComboBox()
        for divisor in SNAP_DIVISORS:
            self.snap_combo.addItem(f"1/{divisor}", divisor)
        found = self.snap_combo.findData(int(snap_divisor))
        self.snap_combo.setCurrentIndex(found if found >= 0 else self.snap_combo.findData(4))
        layout.addRow(tr("MainWindow", "Snap"), self.snap_combo)

        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(-10000, 10000)
        self.offset_spin.setValue(0)
        layout.addRow(tr("MainWindow", "Offset m (ms)"), self.offset_spin)

        # A run of red lines at the chart's own BPM is scenery standing still.
        # Ramping it across the range is what makes the bars accelerate, and the
        # growth functions are the SV generator's own (`sv_ease`), so a barline
        # sweep and an SV sweep shaped "Exp 1.6" mean the same curve.
        # Literal tr() calls, one per option -- see AddViewDialog on why a dict
        # lookup here would escape the i18n coverage gate.
        self.bpm_curve_combo = QComboBox()
        self.bpm_curve_combo.addItem(tr("MainWindow", "Base timing BPM"), "none")
        self.bpm_curve_combo.addItem(tr("MainWindow", "Linear"), "linear")
        self.bpm_curve_combo.addItem(tr("MainWindow", "Exp 1.3"), "exp1.3")
        self.bpm_curve_combo.addItem(tr("MainWindow", "Exp 1.6"), "exp1.6")
        self.bpm_curve_combo.addItem(tr("MainWindow", "True Exp"), "true_exp")
        self.bpm_curve_combo.currentIndexChanged.connect(self._update_mode)
        layout.addRow(tr("MainWindow", "BPM growth"), self.bpm_curve_combo)

        start_bpm = base_bpm_at(self.base_timing, start_ms) if self.base_timing else 120.0
        self.start_bpm_spin = QDoubleSpinBox()
        self.start_bpm_spin.setRange(1.0, 1000000.0)
        self.start_bpm_spin.setDecimals(2)
        self.start_bpm_spin.setValue(start_bpm)
        layout.addRow(tr("MainWindow", "Start BPM"), self.start_bpm_spin)

        self.end_bpm_spin = QDoubleSpinBox()
        self.end_bpm_spin.setRange(1.0, 1000000.0)
        self.end_bpm_spin.setDecimals(2)
        self.end_bpm_spin.setValue(start_bpm * 2)
        layout.addRow(tr("MainWindow", "End BPM"), self.end_bpm_spin)

        # Every uninherited point resets SV to 1.0x, so a run of red lines
        # silently flattens the chart's scroll speed for its whole length
        # unless a green line goes down with each one. "Current speed" writes
        # exactly the SV already in force at each millisecond -- the run then
        # changes where the bars fall and nothing else, which is what you
        # almost always want and why it is the default. A typed value drives
        # the whole run at one speed instead.
        # Literal tr() calls, one per option -- see AddViewDialog on the gate.
        self.sv_mode_combo = QComboBox()
        self.sv_mode_combo.addItem(tr("MainWindow", "Current speed"), "current")
        self.sv_mode_combo.addItem(tr("MainWindow", "Custom"), "custom")
        self.sv_mode_combo.currentIndexChanged.connect(self._update_mode)
        layout.addRow(tr("MainWindow", "SV"), self.sv_mode_combo)

        self.sv_spin = QDoubleSpinBox()
        self.sv_spin.setRange(0.01, 100.0)
        self.sv_spin.setDecimals(2)
        self.sv_spin.setSingleStep(0.05)
        self.sv_spin.setValue(float(current_sv))
        layout.addRow(tr("MainWindow", "SV multiplier"), self.sv_spin)

        # Off by default: a red line landing on a note's own millisecond resets
        # SV to 1.0x for that note and restarts measure counting under it, which
        # is a gimmick in its own right rather than scenery. Available, because
        # doing it deliberately is a real technique.
        self.allow_on_notes_check = QCheckBox()
        self.allow_on_notes_check.setChecked(False)
        self.allow_on_notes_check.toggled.connect(self._update_count)
        layout.addRow(tr("MainWindow", "Allow on existing notes"), self.allow_on_notes_check)

        self.count_label = QLabel()
        self.count_label.setStyleSheet("color:#ffb347;border:0;")
        layout.addRow(self.count_label)
        self._form_layout = layout
        for widget in (self.spacing_spin, self.snap_count_spin, self.offset_spin):
            widget.valueChanged.connect(self._update_count)
        self.snap_combo.currentIndexChanged.connect(self._update_count)
        self.mode_combo.currentIndexChanged.connect(self._update_mode)
        # Before the first _update_mode, not after: wrapping a spin box hands it
        # a new home in the form layout and shows it, so a row hidden first
        # comes back visible.
        pink_spin_buttons(self)
        self._update_mode()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _update_mode(self) -> None:
        """Show only the rhythm the chosen mode actually uses.

        setRowVisible collapses the whole form row; hiding the widget alone
        would leave its label and blank space behind.
        """
        mode = str(self.mode_combo.currentData())
        snaps = mode == "snaps"
        set_row_visible(self._form_layout, self.spacing_spin, mode == "ms")
        set_row_visible(self._form_layout, self.snap_count_spin, snaps)
        set_row_visible(self._form_layout, self.snap_combo, snaps)
        ramping = str(self.bpm_curve_combo.currentData()) != "none"
        set_row_visible(self._form_layout, self.start_bpm_spin, ramping)
        set_row_visible(self._form_layout, self.end_bpm_spin, ramping)
        set_row_visible(
            self._form_layout, self.sv_spin, self.sv_multiplier() is not None
        )
        self._update_count()

    def sv_multiplier(self) -> float | None:
        """The SV every generated line carries, or None for "current speed".

        None rather than a number, because "current" is a different value at
        every millisecond in the run once the chart has SV of its own -- the
        caller reads it per line.
        """
        if str(self.sv_mode_combo.currentData()) == "custom":
            return float(self.sv_spin.value())
        return None

    def _update_count(self) -> None:
        """Say how many lines Ok will write. At n = 1 a careless drag is
        thousands of them, and the number is the only warning worth giving."""
        self.count_label.setText(
            tr("MainWindow", "{0} red lines").format(len(self.times()))
        )

    def times(self) -> list[int]:
        """Every millisecond a line lands on, in order.

        Bounded by the dragged range rather than by a typed cap: how many lines
        a gimmick wants is the mapper's call, and the selection is where they
        already said how far it goes.
        """
        offset = self.offset_spin.value()
        last = round(self.end_ms)
        mode = str(self.mode_combo.currentData())
        if mode == "notes":
            # The chart's own notes inside the range. Offered only where a
            # subclass adds the option (FakeSliderFunctionDialog): a run of red
            # lines has no reason to land on notes, and refuses to by default.
            return sorted(
                at + offset for at in self.note_times
                if round(self.start_ms) <= at <= last
            )
        if mode == "snaps":
            times = [at + offset for at in self._snap_times() if at + offset <= last]
        else:
            first = round(self.start_ms) + offset
            times = list(range(first, last + 1, self.spacing_spin.value())) if first <= last else []
        if self.allow_on_notes_check.isChecked() or not self.note_times:
            return times
        return [at for at in times if at not in self.note_times]

    def bpms(self, times: list[int]) -> list[float]:
        """The BPM each line in `times` carries, in the same order.

        The base timing's own BPM by default -- a line that changes nothing but
        where the bars fall. With a growth function chosen, a ramp from Start to
        End BPM eased across the range instead, which is what makes a run of
        bars accelerate rather than just repeat.

        Progress is measured in *time*, not in line index, so the curve is the
        same shape whether the run is packed every millisecond or spread over
        the beat grid.
        """
        function_id = str(self.bpm_curve_combo.currentData())
        if function_id == "none" or not times:
            return [base_bpm_at(self.base_timing, at) for at in times]
        start_bpm = self.start_bpm_spin.value()
        end_bpm = self.end_bpm_spin.value()
        span = times[-1] - times[0]
        return [
            start_bpm + (end_bpm - start_bpm) * sv_ease(
                function_id, 0.0 if span <= 0 else (at - times[0]) / span
            )
            for at in times
        ]

    def _snap_times(self) -> list[int]:
        """The beat grid across the range, every `n` divisions.

        Stepped one division at a time and then taken every `n`th rather than
        multiplied out, because beat length can change mid-range: a BPM section
        starting inside the selection has to re-anchor the walk, which a single
        multiplication cannot express.
        """
        if not self.base_timing:
            return []
        divisor = int(self.snap_combo.currentData())
        every = self.snap_count_spin.value()
        cursor = snap_time(self.base_timing, self.start_ms, divisor)
        if cursor < self.start_ms - 0.001:
            cursor += active_uninherited_at(self.base_timing, cursor).beat_length / divisor
        times: list[int] = []
        index = 0
        # A pathological beat length would otherwise walk forever; the SV
        # generator caps its own walk at the same order of magnitude.
        while cursor <= self.end_ms + 0.001 and len(times) < 20001:
            if index % every == 0:
                times.append(round(cursor))
            step = active_uninherited_at(self.base_timing, cursor + 0.001).beat_length / divisor
            if step <= 0:
                break
            cursor += step
            index += 1
        return times


class FakeSliderFunctionDialog(BarlineFunctionDialog):
    """Fill a dragged range with fake sliders, or with shiny notes.

    A subclass rather than a second dialog: the two rhythms ("every n ms" for
    scenery, "every n snaps" for something the music can be read against), the
    offset, and the walk along the base timing grid are the same question the
    barline generator already asks, and answering it twice in two places is how
    the two drifted apart last time. What differs is what lands on each
    millisecond, which is the two rows this adds -- and the BPM ramp, which
    belongs to red lines and is hidden.
    """

    def __init__(
        self, start_ms: float, end_ms: float, parent=None,
        base_timing: list[TimingPoint] | None = None, snap_divisor: int = 4,
        shiny_count: int = DEFAULT_SHINY_COUNT,
        shiny_bpm_multiplier: float = 1.0, fake_slider_bpm_multiplier: float = 1.0,
        note_times: set[int] | None = None,
    ) -> None:
        super().__init__(
            start_ms, end_ms, parent, base_timing=base_timing, snap_divisor=snap_divisor,
            note_times=note_times,
        )
        self.setWindowTitle(tr("MainWindow", "Generate fake sliders"))
        # A third rhythm the barline generator has no use for: the chart's own
        # notes. Turning a run of notes into shiny ones is the same generation
        # with the beat grid swapped for where the notes actually are.
        self.mode_combo.addItem(tr("MainWindow", "At each note"), "notes")

        # Each object kind keeps its own remembered multiplier -- switching
        # the combo below is a change of *which* object this run builds, not
        # an edit to either number, so it must not clobber the other kind's
        # setting the way a single shared value would.
        self._kind_multipliers = {
            "shiny": float(shiny_bpm_multiplier), "regular": float(fake_slider_bpm_multiplier),
        }

        # Literal tr() calls, one per option -- see AddViewDialog on why a dict
        # lookup here would escape the i18n coverage gate.
        self.object_combo = QComboBox()
        self.object_combo.addItem(tr("MainWindow", "Fake slider"), "regular")
        self.object_combo.addItem(tr("MainWindow", "Shiny"), "shiny")
        self.object_combo.currentIndexChanged.connect(self._object_kind_changed)

        self.shiny_count_spin = QSpinBox()
        self.shiny_count_spin.setRange(1, 50)
        self.shiny_count_spin.setValue(int(shiny_count))

        # A red line's BPM is also its scroll speed, so retiming a structure's
        # own governing line is how it gets room to move. For a shiny the SV
        # written alongside it divides by the same number, so the note travels
        # at the speed it did before; a fake slider has no such green line to
        # pay it back with (see the hint below), so this is a plain speed
        # change there. The named multiples are the ones this is actually used
        # at; Custom is there because the list can never be complete.
        # Literal tr() calls, one per option -- see AddViewDialog on the gate.
        self.bpm_multiplier_combo = QComboBox()
        self.bpm_multiplier_combo.addItem(tr("MainWindow", "Current BPM"), 1.0)
        self.bpm_multiplier_combo.addItem(tr("MainWindow", "Half"), 0.5)
        self.bpm_multiplier_combo.addItem(tr("MainWindow", "2x"), 2.0)
        self.bpm_multiplier_combo.addItem(tr("MainWindow", "3x"), 3.0)
        self.bpm_multiplier_combo.addItem(tr("MainWindow", "4x"), 4.0)
        self.bpm_multiplier_combo.addItem(tr("MainWindow", "8x"), 8.0)
        self.bpm_multiplier_combo.addItem(tr("MainWindow", "Custom"), None)
        self.bpm_multiplier_combo.currentIndexChanged.connect(self._update_object_mode)

        self.bpm_multiplier_spin = QDoubleSpinBox()
        self.bpm_multiplier_spin.setRange(0.001, 1000.0)
        self.bpm_multiplier_spin.setDecimals(3)

        self.multiplier_hint = QLabel()
        self.multiplier_hint.setWordWrap(True)
        self.multiplier_hint.setStyleSheet("color:#aeb8c5;border:0;")

        row, _role = self._form_layout.getWidgetPosition(self.offset_spin)
        insert_at = row + 1 if row >= 0 else self._form_layout.rowCount()
        for index, (label, widget) in enumerate((
            (tr("MainWindow", "Object"), self.object_combo),
            (tr("MainWindow", "Note count"), self.shiny_count_spin),
            (tr("MainWindow", "Redline BPM"), self.bpm_multiplier_combo),
            (tr("MainWindow", "Custom multiplier"), self.bpm_multiplier_spin),
        )):
            self._form_layout.insertRow(insert_at + index, label, widget)
        self._form_layout.insertRow(insert_at + 4, self.multiplier_hint)

        # The BPM ramp writes a growing BPM onto each red line; here the red
        # lines are a structure's own and their BPM is not the mapper's to bend.
        for widget in (self.bpm_curve_combo, self.start_bpm_spin, self.end_bpm_spin):
            set_row_visible(self._form_layout, widget, False)
        # Snaps, not milliseconds. A barline run wants every millisecond it can
        # get; a run of drawn objects at that density is thousands of sliders
        # nobody can see past.
        snaps = self.mode_combo.findData("snaps")
        if snaps >= 0:
            self.mode_combo.setCurrentIndex(snaps)
        pink_spin_buttons(self)
        self._load_kind_multiplier()
        self._update_object_mode()

    def _object_kind_changed(self) -> None:
        self._load_kind_multiplier()
        self._update_object_mode()

    def _load_kind_multiplier(self) -> None:
        """Show the selected object's own remembered multiplier."""
        value = self._kind_multipliers[self.object_kind()]
        found = self.bpm_multiplier_combo.findData(value)
        self.bpm_multiplier_combo.setCurrentIndex(
            found if found >= 0 else self.bpm_multiplier_combo.findData(None)
        )
        self.bpm_multiplier_spin.setValue(value)

    def _update_object_mode(self) -> None:
        shiny = self.object_kind() == "shiny"
        # The combo itself is shown for both kinds now -- a fake slider's own
        # restore line is retimed the same way a shiny's is. The hint below it
        # stays shiny-only: it explains the SV compensation that pays the
        # retime back, and a fake slider has no green line to do that with.
        custom = self.bpm_multiplier_combo.currentData() is None
        set_row_visible(self._form_layout, self.bpm_multiplier_spin, custom)
        multiplier = self.bpm_multiplier()
        show_hint = shiny and multiplier != 1.0
        self.multiplier_hint.setText(
            tr(
                "MainWindow",
                "Shiny only: the red line is written at {0}x the chart's BPM, "
                "with a green line right after it dividing SV by the same "
                "amount, so the shiny travels at the speed it already did. A "
                "fake slider's restore line has no such green line, so retiming "
                "it there is a plain speed change with nothing to compensate.",
            ).format(f"{multiplier:g}") if show_hint else ""
        )
        self._form_layout.setRowVisible(self.multiplier_hint, show_hint)
        self._update_count()

    def bpm_multiplier(self) -> float:
        """The chosen multiple of the chart's own BPM, named or typed."""
        chosen = self.bpm_multiplier_combo.currentData()
        return float(self.bpm_multiplier_spin.value() if chosen is None else chosen)

    def _update_count(self) -> None:
        # Overrides the barline generator's "N red lines", which is not what
        # this one writes.
        if not hasattr(self, "object_combo"):
            return  # still inside the base __init__
        self.count_label.setText(
            tr("MainWindow", "{0} objects").format(len(self.times()))
        )

    def object_kind(self) -> str:
        return str(self.object_combo.currentData())

    def shiny_count(self) -> int:
        return int(self.shiny_count_spin.value())


class MultiFakeSliderDialog(QDialog):
    """The Multiple Fake Slider tool's config: a run's shape, chosen once.

    Opened by `_set_gimmick_tool` when the tool is *selected*, not on every
    click -- a run's start, end and spacing describe the tool, not one
    placement, so asking on each click would be the same question asked a
    dozen times over a dozen sliders. Start and end locate the run's
    *structures*; each structure's own slider then lands its usual
    `fake_slider_offset_ms` further on, exactly where one click of the plain
    Fake Slider tool would put it -- see `MainWindow._multi_fake_slider_commands`.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("MainWindow", "Multiple fake slider"))
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        layout = QFormLayout(self)

        self.start_spin = QSpinBox()
        # Negative allowed and asymmetric with End on purpose: a run is
        # routinely wanted leading into the click as well as following it.
        self.start_spin.setRange(-10000, 10000)
        self.start_spin.setValue(0)
        layout.addRow(tr("MainWindow", "Start offset (ms)"), self.start_spin)

        self.end_spin = QSpinBox()
        self.end_spin.setRange(-10000, 10000)
        self.end_spin.setValue(16)
        layout.addRow(tr("MainWindow", "End offset (ms)"), self.end_spin)

        self.distance_spin = QSpinBox()
        self.distance_spin.setRange(1, 10000)
        self.distance_spin.setValue(2)
        layout.addRow(tr("MainWindow", "Distance (ms)"), self.distance_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        pink_spin_buttons(self)

    def _accept(self) -> None:
        # Clamped up rather than refused: End below Start is "I want just the
        # one structure at Start", not a mistake to bounce back to the user.
        if self.end_spin.value() < self.start_spin.value():
            self.end_spin.setValue(self.start_spin.value())
        self.accept()

    def parameters(self) -> tuple[int, int, int]:
        """(start, end, distance), all in ms."""
        return (self.start_spin.value(), self.end_spin.value(), self.distance_spin.value())


class TimingLineDialog(QDialog):
    """Retype one timing line: its value, and the millisecond it sits on.

    One dialog for both kinds, because the only difference is what the value
    means -- BPM on a red line, SV multiplier on a green one. Both carry a time,
    and moving a line is the edit that has no other keyboard route: dragging
    retimes it only as far as the snap grid allows.
    """

    def __init__(self, point: TimingPoint, parent=None) -> None:
        super().__init__(parent)
        self.uninherited = point.uninherited
        self.setWindowTitle(
            tr("MainWindow", "Red line") if self.uninherited else tr("MainWindow", "Green line")
        )
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        layout = QFormLayout(self)

        self.value_spin = QDoubleSpinBox()
        if self.uninherited:
            self.value_spin.setRange(0.001, 1000000.0)
            self.value_spin.setDecimals(3)
            self.value_spin.setValue(point.bpm or 120.0)
            layout.addRow(tr("MainWindow", "BPM"), self.value_spin)
        else:
            self.value_spin.setRange(0.01, 100.0)
            self.value_spin.setDecimals(3)
            self.value_spin.setValue(point.sv_multiplier)
            layout.addRow(tr("MainWindow", "SV multiplier"), self.value_spin)

        self.time_spin = QSpinBox()
        self.time_spin.setRange(0, 100000000)
        self.time_spin.setValue(round(point.time))
        layout.addRow(tr("MainWindow", "Time (ms)"), self.time_spin)

        # The two effects bits, editable where the line itself is. Both are
        # things a gimmick has to fix line by line -- a stray bar to hide, a
        # kiai section a generated line switched off -- and until now the only
        # way to reach either was a text editor.
        self.kiai_check = QCheckBox()
        self.kiai_check.setChecked(point.kiai)
        layout.addRow(tr("MainWindow", "Kiai"), self.kiai_check)

        self.omit_barline_check = QCheckBox()
        self.omit_barline_check.setChecked(point.omit_first_barline)
        layout.addRow(tr("MainWindow", "Omit barline"), self.omit_barline_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        pink_spin_buttons(self)

    def effects(self, point: TimingPoint) -> int:
        """`point.effects` with only the two checkboxes' bits rewritten.

        Masked rather than rebuilt: the field carries bits this dialog does not
        offer, and dropping them would be a silent edit.
        """
        effects = point.effects
        for bit, checked in (
            (EFFECT_KIAI, self.kiai_check.isChecked()),
            (EFFECT_OMIT_FIRST_BARLINE, self.omit_barline_check.isChecked()),
        ):
            effects = (effects | bit) if checked else (effects & ~bit)
        return effects

    def changes(self, point: TimingPoint) -> dict:
        """`EditTimingPoint` changes, holding only the fields that moved.

        Every field in one command, so retiming a line, changing its value and
        toggling kiai together is one Ctrl+Z rather than three.
        """
        changes = {}
        time_ms = float(self.time_spin.value())
        if time_ms != point.time:
            changes["time"] = (point.time, time_ms)
        value = self.value_spin.value()
        beat_length = 60000.0 / value if self.uninherited else -100.0 / value
        if beat_length != point.beat_length:
            changes["beat_length"] = (point.beat_length, beat_length)
        effects = self.effects(point)
        if effects != point.effects:
            changes["effects"] = (point.effects, effects)
        return changes


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


# Relative, not absolute, because the BPMs compared here span five orders of
# magnitude: a chart line at 176.9 and a gimmick line at 60000 cannot share one
# millisecond-scale epsilon. A red line's BPM is never stored -- it is
# 60000/beat_length, recomputed from a beat_length that has been through a text
# file with a handful of decimals -- so an authored 60000 and a computed one
# differ in the last few digits and `==` says no. One part in a million is far
# tighter than any two red lines a human would ever want to tell apart, and far
# looser than the round trip's error.
BPM_MATCH_RELATIVE_EPSILON = 1e-6


def bpm_matches(bpm: float | None, wanted: float) -> bool:
    """Is `bpm` the same red-line BPM as `wanted`, allowing for float drift?"""
    if bpm is None:
        return False
    return abs(bpm - wanted) <= BPM_MATCH_RELATIVE_EPSILON * max(abs(bpm), abs(wanted), 1.0)


def bpm_in_range(bpm: float | None, low: float, high: float) -> bool:
    """Is `bpm` within [low, high], inclusive, allowing for the same float
    drift bpm_matches guards against?

    The tolerance is applied per end rather than as one shared absolute
    slop, for the same reason bpm_matches scales it by the value being
    compared: a red line's BPM is recomputed as 60000/beat_length, so an
    authored 500.0 can come back as 499.99999...  and a fixed epsilon would
    be wrong by orders of magnitude between a 60 BPM line and a 60000 BPM
    barline gimmick one.
    """
    if bpm is None:
        return False
    low_tolerance = BPM_MATCH_RELATIVE_EPSILON * max(abs(bpm), abs(low), 1.0)
    high_tolerance = BPM_MATCH_RELATIVE_EPSILON * max(abs(bpm), abs(high), 1.0)
    return bpm >= low - low_tolerance and bpm <= high + high_tolerance


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
        # "", "point" or "pair" -- see SVFunctionDialog's mode combo. Oscillation
        # is the same easing function read as an *amplitude* rather than as a
        # position, so it belongs here rather than in a second preview widget:
        # the curve drawn is still exactly what Generate writes.
        self.oscillate = ""
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

    def set_oscillate(self, mode: str) -> None:
        self.oscillate = mode
        self.update()

    def rate_at(self, t: float) -> float:
        return self.initial_rate + (self.final_rate - self.initial_rate) * sv_ease(self.function_id, t)

    def rates(self, count: int) -> list[float]:
        """The `count` values Generate would write, in order.

        One function for the preview and for `_generate_sv`'s own loop would be
        better still, but that loop also applies the position offset and the
        BPM compensation, neither of which the preview knows about. This is the
        shape; that is the shape with the map's own numbers folded in.
        """
        if self.oscillate:
            return oscillating_series(
                self.initial_rate,
                abs(self.final_rate - self.initial_rate),
                count,
                lambda t: sv_ease(self.function_id, t),
                per_pair=self.oscillate == "pair",
            )
        return [self.rate_at(index / max(1, count - 1)) for index in range(count)]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#151b24"))
        painter.setPen(QPen(QColor("#3a4554"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        margin = 22.0 if self.labels else 10.0
        width = self.width() - margin * 2
        height = self.height() - margin * 2

        rates = self.rates(20)
        low, high = min(rates), max(rates)
        span = high - low

        dots = []
        for i, rate in enumerate(rates):
            t = i / 19
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
        painter.drawText(QPointF(4, dots[0].y() - 6), f"{rates[0]:.2f}x")
        final_text = f"{rates[-1]:.2f}x"
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

    # SV ceiling. Matches TimingLineDialog's own range: a barline gimmick is
    # routinely driven at speeds an ordinary chart never asks for, and there is
    # no reason for the two places that type an SV to disagree about the limit.
    MAX_RATE = 100.0

    def __init__(
        self, start_ms: float, end_ms: float, parent=None, snap_divisor: int = 4,
        initial_rate: float = 1.0, final_rate: float = 1.0,
        gimmick_layer: str | bool | None = False,
        position_offset: int | None = None,
        base_timing: list[TimingPoint] | None = None,
        volume: bool = False,
    ) -> None:
        super().__init__(parent)
        self.start_ms = start_ms
        self.end_ms = end_ms
        # Only for the BPM filter's default, and only layer 6 asks for it. The
        # *base* snapshot rather than the document's own timing for the same
        # reason BarlineFunctionDialog uses it: by the second placement the
        # gimmick difficulty is full of 60000 BPM lines, so its own timing is
        # the worst possible answer to "what BPM are we at".
        self.base_timing = base_timing or []
        # The Kiai and Sound Volume layer's Volume tool is this same generator
        # pointed at TimingPoint.volume: same functions, same modes, same
        # placement. Only the quantity differs -- whole percent in 0..100
        # instead of a scroll rate -- so it is a flag rather than a second
        # dialog with three hundred copied lines behind it.
        self.volume = volume
        # A gimmick SV layer owns exactly one structure's green lines, matched
        # by millisecond (MainWindow._sv_layer_times). A point generated
        # anywhere else is invisible in the layer that made it *and* silently
        # wiped by the next uninherited line, so the two controls that can move
        # a point off its object are taken away rather than merely defaulted.
        self.gimmick_layer = gimmick_layer
        self.setWindowTitle(
            tr("MainWindow", "Generate Volume") if volume else tr("MainWindow", "Generate SV")
        )
        icon = application_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        # The app-wide QPushButton:checked rule does not reach a QToolButton, so
        # the chosen function tile had no selected state at all -- seven
        # identical squares, none of them saying which one Generate would use.
        self.setStyleSheet(
            "QToolButton { background: #252d39; border: 1px solid #3a4554; border-radius: 6px; }"
            "QToolButton:hover { border-color: #f3a6bd; }"
            f"QToolButton:checked {{ background: {ACCENT_PINK}; color: #17191f;"
            " border: 2px solid #ff66aa; font-weight: 700; }"
        )

        root = QVBoxLayout(self)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        root.addLayout(columns, 1)

        # Left column: everything configurable.
        layout = QFormLayout()
        columns.addLayout(layout)

        # Volume is a whole percent in 0..100 (what the .osu field holds and
        # what the writer validates), so the same two spin boxes are told to
        # carry integers rather than a second pair being built beside them.
        low, high = (0.0, 100.0) if volume else (0.01, self.MAX_RATE)
        step, decimals = (1.0, 0) if volume else (0.05, 2)

        self.initial_rate_spin = QDoubleSpinBox()
        self.initial_rate_spin.setRange(low, high)
        self.initial_rate_spin.setSingleStep(step)
        self.initial_rate_spin.setDecimals(decimals)
        self.initial_rate_spin.setValue(round(float(initial_rate), decimals))
        self.initial_rate_spin.valueChanged.connect(self._update_preview)
        layout.addRow(
            tr("MainWindow", "Initial volume") if volume else tr("MainWindow", "Initial rate"),
            self.initial_rate_spin,
        )

        self.final_rate_spin = QDoubleSpinBox()
        self.final_rate_spin.setRange(low, high)
        self.final_rate_spin.setSingleStep(step)
        self.final_rate_spin.setDecimals(decimals)
        self.final_rate_spin.setValue(round(float(final_rate), decimals))
        self.final_rate_spin.valueChanged.connect(self._update_preview)
        layout.addRow(
            tr("MainWindow", "Final volume") if volume else tr("MainWindow", "Final rate"),
            self.final_rate_spin,
        )

        # Sweep or oscillation. The same growth function drives both: a sweep
        # eases the rate from Initial to Final, an oscillation eases the
        # *amplitude* it fans out to either side of Initial, reaching Final at
        # the last point. Per point opens the fan on every step (the two sides
        # peak at different widths); per pair opens it once per up/down pair,
        # so every pair is symmetric about the base.
        # Literal tr() calls, one per option -- see AddViewDialog's note on why
        # a dict lookup here would silently escape the i18n coverage gate.
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("MainWindow", "Sweep"), "")
        self.mode_combo.addItem(tr("MainWindow", "Oscillate (per point)"), "point")
        self.mode_combo.addItem(tr("MainWindow", "Oscillate (per pair)"), "pair")
        self.mode_combo.currentIndexChanged.connect(self._update_preview)
        layout.addRow(tr("MainWindow", "Mode"), self.mode_combo)

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
        # before the note it is meant to affect. In a gimmick layer the value
        # comes from that layer's config, which is also what decides which
        # milliseconds the layer recognises as its own.
        self.position_offset_spin.setValue(
            self.DEFAULT_POSITION_OFFSET_MS if position_offset is None else int(position_offset)
        )
        layout.addRow(tr("MainWindow", "Position offset (ms)"), self.position_offset_spin)
        if volume:
            # Describes where an *inserted* point sits; the Volume tool edits
            # the timing points already in the range instead, so there is
            # nothing here to offset.
            set_row_visible(layout, self.position_offset_spin, False)

        self.omit_barline_check = QCheckBox()
        self.omit_barline_check.setChecked(False)
        layout.addRow(tr("MainWindow", "Omit barline"), self.omit_barline_check)
        if volume:
            # Also describes an inserted point's own flag; nothing is
            # inserted in volume mode.
            set_row_visible(layout, self.omit_barline_check, False)

        self.relative_to_final_bpm_check = QCheckBox()
        # Off in a gimmick layer. It multiplies every generated rate by
        # local_bpm / start_bpm, and a gimmick's local BPM is 60000 against a
        # chart's 180 -- so a "1.0x to 2.0x" sweep came out in the hundreds and
        # neither end landed on the number that was typed.
        self.relative_to_final_bpm_check.setChecked(not gimmick_layer and not volume)
        layout.addRow(tr("MainWindow", "Relative to final BPM"), self.relative_to_final_bpm_check)
        if volume:
            # It scales the generated value by local_bpm / start_bpm, which is
            # a statement about scroll speed. A hitsound is as loud as it is
            # whatever the BPM is doing, so the option is not offered rather
            # than merely defaulted off.
            set_row_visible(layout, self.relative_to_final_bpm_check, False)

        self.include_shiny_check = QCheckBox()
        self.include_shiny_check.setChecked(False)
        if gimmick_layer == "sv_fake_slider":
            layout.addRow(tr("MainWindow", "Include shiny notes"), self.include_shiny_check)
            hint = QLabel(tr(
                "MainWindow",
                "This layer holds the speed of plain fake sliders. Shiny notes "
                "are left out by default -- their gimmick line sits on the note, "
                "so the normal chart SV layer already moves the note and its "
                "shine together -- but this sweep can add them in as well.",
            ))
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#ffb347;border:0;")
            layout.addRow(hint)

        # Layer 6 owns *every* red line -- the chart's own timing, a barline
        # gimmick's 60000 BPM run, hand-placed lines -- and a sweep across a
        # dragged range hits all of them indiscriminately. This narrows it to
        # one BPM, which is how you drive a single gimmick run without
        # disturbing the ordinary timing lines interleaved with it.
        self.bpm_filter_check = QCheckBox()
        self.bpm_filter_check.setChecked(False)
        self.bpm_filter_spin = QDoubleSpinBox()
        self.bpm_filter_spin.setRange(1.0, 1000000.0)
        self.bpm_filter_spin.setDecimals(2)
        # The BPM in force where the drag started -- the run you are looking at
        # is almost always the one you want, so the default costs no typing.
        self.bpm_filter_spin.setValue(
            base_bpm_at(self.base_timing, start_ms) if self.base_timing else 120.0
        )
        # An inclusive range rather than one exact BPM: "500-1000 BPM" means
        # every red line from 500 to 1000, and an exact match is just the
        # degenerate case where the two ends are equal -- so there is no
        # separate exact-value mode to keep in sync with this one.
        self.bpm_filter_max_spin = QDoubleSpinBox()
        self.bpm_filter_max_spin.setRange(1.0, 1000000.0)
        self.bpm_filter_max_spin.setDecimals(2)
        self.bpm_filter_max_spin.setValue(self.bpm_filter_spin.value())
        if gimmick_layer == "sv_barline":
            layout.addRow(tr("MainWindow", "Only red lines at this BPM"), self.bpm_filter_check)
            layout.addRow(tr("MainWindow", "BPM from"), self.bpm_filter_spin)
            layout.addRow(tr("MainWindow", "BPM to"), self.bpm_filter_max_spin)
            self.bpm_filter_check.toggled.connect(self._update_bpm_filter_row)

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
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._update_preview()
        self._update_placement_controls()
        pink_spin_buttons(self)
        # After pink_spin_buttons, not before: the BPM spin is no longer the
        # widget its form row is keyed on once it has been wrapped, and
        # set_row_visible only knows that from the property the wrap sets.
        self._update_bpm_filter_row()

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
        self.preview.set_oscillate(str(self.mode_combo.currentData()))
        for function_id, button in self.function_buttons.items():
            self.preview.set_function(function_id)
            button.setIcon(QIcon(self.preview.grab()))
        self.preview.set_function(self.selected_function())

    def _update_placement_controls(self) -> None:
        """The snap divisor only means anything in "Every snap" mode.

        setRowVisible collapses the whole form row; hiding the two widgets
        individually would leave the row's blank space behind.

        A gimmick layer has no "where" to offer: its points go on its own
        objects or they do not exist. The position offset stays -- it is how far
        ahead of those objects they sit, which is a real choice and the one the
        normal chart layer needs.

        Volume mode has no "where" either, for a different reason: it edits
        the timing points already in the range rather than placing new ones,
        so there is no position to offer a choice about.
        """
        if self.gimmick_layer or self.volume:
            set_row_visible(self._form_layout, self.snap_combo, False)
            set_row_visible(self._form_layout, self.placement_combo, False)
            return
        every_snap = str(self.placement_combo.currentData()) == "snaps"
        set_row_visible(self._form_layout, self.snap_combo, every_snap)

    def _update_bpm_filter_row(self) -> None:
        """The BPM only means anything while the filter is on.

        Hidden rather than disabled, same as the snap divisor above: a greyed
        control still takes a row of an already tall form and still invites a
        click that does nothing.
        """
        if self.gimmick_layer != "sv_barline":
            return
        visible = self.bpm_filter_check.isChecked()
        set_row_visible(self._form_layout, self.bpm_filter_spin, visible)
        set_row_visible(self._form_layout, self.bpm_filter_max_spin, visible)

    def _accept(self) -> None:
        # Clamped up rather than refused, same reasoning as
        # MultiFakeSliderDialog._accept: "to" below "from" is "I only meant
        # the one BPM at from", not a mistake worth bouncing back to the user.
        if self.bpm_filter_max_spin.value() < self.bpm_filter_spin.value():
            self.bpm_filter_max_spin.setValue(self.bpm_filter_spin.value())
        self.accept()

    def parameters(self) -> dict:
        return {
            "initial_rate": self.initial_rate_spin.value(),
            "final_rate": self.final_rate_spin.value(),
            "placement": str(self.placement_combo.currentData()),
            "snap_divisor": int(self.snap_combo.currentData()),
            # Zero in volume mode, not merely hidden: the Volume tool edits
            # the timing points already in the range, so there is nothing to
            # offset and no path may act as though there were.
            "position_offset": 0 if self.volume else self.position_offset_spin.value(),
            "omit_barline": self.omit_barline_check.isChecked(),
            "relative_to_final_bpm": self.relative_to_final_bpm_check.isChecked(),
            "function": self.selected_function(),
            "oscillate": str(self.mode_combo.currentData()),
            "include_shiny": self.include_shiny_check.isChecked(),
            # One key rather than a flag and a value: None *is* "no filter",
            # so no consumer can read the BPM interval without first having
            # checked whether it applies. The value is a (low, high) tuple,
            # inclusive at both ends -- an exact single BPM is just low == high.
            "only_red_line_bpm": (
                (self.bpm_filter_spin.value(), self.bpm_filter_max_spin.value())
                if self.bpm_filter_check.isChecked() else None
            ),
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
        # source_path -> history.revision at the moment the user chose
        # "Continue Without Saving" on it. _confirm_leaving_editor stays
        # quiet about a state whose revision hasn't moved since, so
        # answering the prompt once does not immediately re-ask on the very
        # next page/view change; a fresh edit moves the revision on and the
        # prompt is legitimate again. Cleared once the state is actually
        # saved (save_all_states).
        self._discard_acknowledged: dict[Path, int] = {}
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
        # Non-None only inside `_refresh_cycle`; see `_cached`.
        self._doc_cache: dict | None = None
        # Ctrl+C / Ctrl+V payloads, kept as plain data (time offset + the
        # fields needed to rebuild) rather than live objects, so a paste
        # always creates fresh identities and pasting twice is two objects.
        self._note_clipboard: list[dict] = []
        # Red lines copied alongside the notes -- a gimmick object is both.
        self._timing_clipboard: list[dict] = []
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
        # Which difficulty each gimmick session edits, and the timing snapshot
        # its snaps and scroll are anchored to. Read once, kept for the session.
        self._gimmick_pairings: dict[str, GimmickPairing] = load_index(self._gimmick_index_path())
        self._gimmick_pairing: GimmickPairing | None = None
        # One config per structure-building layer. Sharing a single one meant
        # the barline spacing and the fake slider offset were the same number,
        # which is exactly the pair that must not agree (spacing_collides).
        # The three SV layers get their own too, for `sv_offset_ms`: how far
        # from its object each layer's green lines sit. Only the normal chart
        # wants a lead-in by default -- the other two are keyed to timing lines,
        # where the SV has to be on the line itself.
        self.gimmick_configs = {
            "fake_slider": GimmickConfig(),
            "barline": GimmickConfig(),
            "sv_chart": GimmickConfig(sv_offset_ms=SVFunctionDialog.DEFAULT_POSITION_OFFSET_MS),
            "sv_fake_slider": GimmickConfig(),
            "sv_barline": GimmickConfig(),
        }
        # The Multiple Fake Slider tool's last-chosen run shape (start, end,
        # distance), reused until the tool is reselected and reconfigured --
        # see `_set_gimmick_tool` and `_multi_fake_slider_commands`.
        self._multi_fake_slider_params: tuple[int, int, int] = (0, 16, 2)

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
        self.player.setAudioOutput(self.audio_output)
        self.hitsounds = HitsoundPlayer(self)
        # Music volume was hardcoded here until it had a settings page; both it
        # and the hitsound values are read in one place so the Settings dialog
        # can re-apply them without a restart.
        self._apply_audio_settings()
        self.player.errorOccurred.connect(self._audio_backend_failed)
        self.player.positionChanged.connect(
            self._player_position_changed
        )
        self.player.durationChanged.connect(
            lambda _duration: self._update_timeline_info()
        )
        # The interpolated clock (osu!'s framedClock): the value actually
        # displayed. Advanced and blended toward the source clock once per
        # rendered frame in _advance_interpolated_clock -- never stepped
        # directly from a backend report, and never allowed to move backwards
        # except on an explicit reset (seek_audio, toggle_playback).
        self.audio_anchor_position = 0.0
        # Restarted every time audio_anchor_position is set (per frame, or on
        # a reset) so _predicted_audio_position can extrapolate the small gap
        # since then for callers between rendered frames.
        self.audio_anchor_clock = QElapsedTimer()
        self.audio_anchor_clock.start()
        # The last raw report from QMediaPlayer.positionChanged, and the
        # instant it arrived. osu!'s framedSourceClock is exactly this: a
        # sparse report extrapolated forward by elapsed real time * rate, so
        # there is something to blend toward every frame instead of only at
        # the tens-of-milliseconds-apart instants a report actually lands.
        self.latest_audio_position = 0.0
        self._source_report_clock = QElapsedTimer()
        self._source_report_clock.start()
        # Monotonic floor for _predicted_audio_position's own sub-frame
        # extrapolation -- reset alongside the clocks above so a legitimate
        # backwards seek is never clamped away.
        self._last_predicted_position = 0.0

        # Never restarted after this; _render_gameplay_frame schedules off a
        # fixed cadence (self._next_frame_due_ns) measured against it instead
        # of resetting to "now" every tick, which is what kept turning a
        # slightly-late timer wakeup into a permanently shifted, uneven frame
        # pace -- the main source of visible stutter in the old scheme.
        self.gameplay_frame_clock = QElapsedTimer()
        self.gameplay_frame_clock.start()
        self.gameplay_frame_interval_ns = 8_333_333  # ~120fps
        self._next_frame_due_ns = self.gameplay_frame_interval_ns
        # The real gap between rendered frames drives the interpolated
        # clock's own per-frame advance (_advance_interpolated_clock), rather
        # than the fixed interval above -- a render that fires a little late
        # advances the playhead by how late it actually was.
        self._last_frame_ns = self.gameplay_frame_clock.nsecsElapsed()
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
    def _gimmick_wheel_target(self, watched):
        """Which gimmick layer a wheel event on that page belongs to, or None.

        Every wheel on the gimmick page goes to a layer. Seeking, Ctrl+zoom and
        Alt+snap all mean the same thing wherever the pointer is on the page,
        and none of them are the scroll area's business -- so the pointer no
        longer has to be inside a band for the wheel to do anything.

        This also has to run *before* the Alt+wheel branch below, which was
        catching every Alt+wheel in the program and handing it to the Fancy
        Arranger timeline: on the gimmick page that consumed the event and the
        layer under the pointer never saw it at all.

        A combo box or spin box under the pointer keeps its own wheel; those are
        the one kind of control on the page that reads a wheel as a value.
        """
        stack = getattr(self, "page_stack", None)
        if stack is None or stack.currentIndex() != PAGE_GIMMICK:
            return None
        if not self._gimmick_views or isinstance(watched, (QComboBox, QAbstractSpinBox)):
            return None
        views = [
            getattr(frame, "chart_view", None) or getattr(frame, "sv_view", None)
            for frame in self._gimmick_views
        ]
        views = [view for view in views if view is not None]
        if not views:
            return None
        # The band under the pointer when there is one, so Ctrl+zoom and the
        # snap grid still read as belonging to what is being looked at.
        return watched if watched in views else views[0]

    def eventFilter(self,watched,event)->bool:
        # Shift toggles the placement preview between its normal and finisher
        # size (_draw_placement_ghost reads it fresh on every paint), but a
        # chart/SV view only repaints on mouse *move* -- so holding Shift over
        # a stationary cursor changed nothing on screen until the mouse
        # twitched. Caught here rather than in the views' own keyPressEvent
        # because a view usually does not have keyboard focus (the window
        # does), so it would never see the key at all. Filtered to views the
        # cursor is actually over, not every open view, so this stays a
        # handful of repaints rather than a whole-app one on every keystroke.
        if (
            event.type() in (QEvent.KeyPress, QEvent.KeyRelease)
            and event.key() == Qt.Key_Shift
            and not event.isAutoRepeat()
        ):
            for view in [*self.findChildren(TimelineGameplay), *self.findChildren(SVEditorView)]:
                if view.isVisible() and view.underMouse():
                    view.update()
            # Falls through rather than returning True: nothing else treats
            # Shift as consumed, and eating it here would be a silent change
            # to every other Shift-modified gesture (Shift+click, Shift+drag).
        if self.drawing_dialog_active and event.type()==QEvent.MouseButtonPress:return super().eventFilter(watched,event)
        if event.type()==QEvent.Wheel:
            target = self._gimmick_wheel_target(watched)
            if target is not None:
                target.wheelEvent(event)
                return True
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

    def _apply_audio_settings(self) -> None:
        """Re-read the Audio page and apply it live.

        Called at startup and again after the Settings dialog closes, so a
        changed volume or latency offset takes effect immediately rather than
        at next launch. Reading unconditionally after the dialog is simpler
        than reacting to accept/reject and costs nothing: a rejected dialog
        wrote nothing, so this reads back exactly what was already in force.
        """
        self.audio_output.setVolume(self.settings.int_value("audio/music_volume", 65) / 100.0)
        self.hitsounds.enabled = self.settings.bool_value("audio/hitsounds_enabled", True)
        self.hitsounds.offset_ms = self.settings.int_value("audio/hitsound_offset_ms", 0)
        self.hitsounds.set_volume(self.settings.int_value("audio/hitsound_volume", 70) / 100.0)

    def maybe_check_for_updates(self) -> None:
        """Startup update check, on a worker thread so the window never waits.

        Skipped versions are filtered here rather than in updater.check_now:
        an explicit "Check for updates now" should still report the release the
        user once skipped.
        """
        if not self.settings.bool_value(updater.SETTING_CHECK_ON_STARTUP, True):
            return
        skipped = self.settings.string_value(updater.SETTING_SKIPPED_TAG, "")

        def finished(release: object, _reachable: bool) -> None:
            if release is not None and getattr(release, "tag", "") != skipped:
                updater.present_update(release, self.settings, self)

        # Held on self so Qt does not destroy the thread while it is running.
        self._update_check = updater.CheckThread(self)
        self._update_check.result.connect(finished)
        self._update_check.start()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.shortcuts, self)
        # Applied from here because settings_dialog cannot import gui -- gui
        # imports it. Same route ImageTraceDialog's spin boxes take.
        pink_spin_buttons(dialog)
        dialog.exec()
        self._apply_audio_settings()

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
        self.gimmick_page_button = QPushButton(tr("MainWindow", "Gimmick"))
        self.fancy_arranger_page_button = QPushButton(tr("MainWindow", "Fancy Arranger"))
        page_buttons = (
            self.library_page_button, self.editor_page_button,
            self.gimmick_page_button, self.fancy_arranger_page_button,
        )
        for button in page_buttons:
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
            # Wider than the label needs. These are the app's top-level
            # navigation and they sat as four cramped boxes among the header's
            # other controls; extra padding either side is what makes them read
            # as tabs. Padding rather than a typed width, so a longer
            # translation still fits (see equalize_button_widths).
            button.setStyleSheet("QPushButton { padding-left: 30px; padding-right: 30px; }")
        self.page_button_group = QButtonGroup(self)
        self.page_button_group.setExclusive(True)
        self.page_button_group.addButton(self.library_page_button, PAGE_LIBRARY)
        self.page_button_group.addButton(self.editor_page_button, PAGE_EDITOR)
        self.page_button_group.addButton(self.gimmick_page_button, PAGE_GIMMICK)
        self.page_button_group.addButton(self.fancy_arranger_page_button, PAGE_FANCY)
        self.library_page_button.setChecked(True)
        self.page_button_group.idClicked.connect(self._switch_page)
        for button in page_buttons:
            header.addWidget(button)

        root.addLayout(header)

        self.page_stack = QStackedWidget()
        self.library_page = self._build_library_page()
        self.page_stack.addWidget(self.library_page)
        self.editor_page = self._build_editor_page()
        self.page_stack.addWidget(self.editor_page)
        self.gimmick_page = self._build_gimmick_page()
        self.page_stack.addWidget(self.gimmick_page)
        self.fancy_arranger_page = self._build_fancy_arranger_page()
        self.page_stack.addWidget(self.fancy_arranger_page)
        self.page_stack.setCurrentIndex(PAGE_LIBRARY)
        root.addWidget(self.page_stack, 1)

        # A transient toast for a refusal (see show_toast). Parented to
        # page_stack rather than added through it, so it floats above
        # whichever page is current as an ordinary un-managed child without
        # joining the QStackedLayout -- one instance serves every page.
        self._build_toast()

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

    # -- toast ---------------------------------------------------------------

    def _build_toast(self) -> None:
        """Build the one reusable overlay `show_toast` shows and hides.

        Click-through (`WA_TransparentForMouseEvents`) so a refusal can never
        eat the click that follows it -- "nothing to act on" should not also
        mean "and now the next click goes nowhere". Sized to its own text via
        `adjustSize` in `show_toast`, not a fixed box, so a short refusal does
        not sit inside acres of empty black.
        """
        self._toast = QLabel(self.page_stack)
        self._toast.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._toast.setWordWrap(True)
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.setStyleSheet(
            """
            QLabel {
                background: rgba(0, 0, 0, 179);
                color: #ffffff;
                border-radius: 10px;
                padding: 12px 20px;
                font-size: 14px;
            }
            """
        )
        # Opacity lives on a graphics effect rather than the stylesheet alpha:
        # QPropertyAnimation needs a Qt property to animate, and "opacity" on
        # QGraphicsOpacityEffect fades the label (background, text, border)
        # together instead of requiring the background and text alpha to be
        # animated in step.
        self._toast_opacity = QGraphicsOpacityEffect(self._toast)
        self._toast_opacity.setOpacity(1.0)
        self._toast.setGraphicsEffect(self._toast_opacity)
        self._toast.hide()

        self._toast_hide_timer = QTimer(self)
        self._toast_hide_timer.setSingleShot(True)
        self._toast_hide_timer.timeout.connect(self._start_toast_fade)

        self._toast_fade = QPropertyAnimation(self._toast_opacity, b"opacity", self)
        self._toast_fade.setDuration(400)
        self._toast_fade.setEndValue(0.0)
        self._toast_fade.finished.connect(self._toast.hide)

    def show_toast(self, text: str) -> None:
        """Flash `text` for 3 seconds, then fade it out over ~400ms.

        For refusals -- "nothing in the selected range" and the like -- that
        used to only ever reach the status line. The status line reads as a
        log a mapper checks later rather than an answer to the click they just
        made, so a refusal that produced no dialog and no visible change was
        easy to read as the app doing nothing. A second call while one is
        already showing replaces the text and restarts the 3 seconds rather
        than queuing a second toast behind it.
        """
        self._toast_fade.stop()
        self._toast_opacity.setOpacity(1.0)
        self._toast.setText(text)
        self._toast.adjustSize()
        self._position_toast()
        self._toast.show()
        self._toast.raise_()
        self._toast_hide_timer.start(3000)

    def _start_toast_fade(self) -> None:
        self._toast_fade.stop()
        self._toast_fade.setStartValue(self._toast_opacity.opacity())
        self._toast_fade.start()

    def _position_toast(self) -> None:
        """Centre the toast over `page_stack`, whichever page is current."""
        size = self._toast.sizeHint()
        container = self._toast.parentWidget()
        x = max(0, (container.width() - size.width()) // 2)
        y = max(0, (container.height() - size.height()) // 2)
        self._toast.setGeometry(x, y, size.width(), size.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Guarded: resize events can fire (from the initial self.resize() /
        # setCentralWidget()) before _build_ui has reached _build_toast.
        if hasattr(self, "_toast"):
            self._position_toast()

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

        self.reset_button = QPushButton(tr("MainWindow", "Reset Applied Transforms"))
        self.reset_button.clicked.connect(self.reset_applied)
        self.reset_button.setFocusPolicy(Qt.NoFocus)

        self.export_button = QPushButton(tr("MainWindow", "Export Applied Map"))
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

        self.apply_button = QPushButton(tr("MainWindow", "Transform Selected Notes"))
        self.apply_button.clicked.connect(self.apply_selection)
        self.apply_button.setFocusPolicy(Qt.NoFocus)
        self.apply_button.setEnabled(False)
        right_layout.addWidget(self.apply_button)
        self.apply_original_button=QPushButton(tr("MainWindow", "Apply All Changes to Original File"))
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
        self._share_kiai_bands(self.timeline, sv=False)
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
        # Right-click-scrub target for a live drag-selection in self.timeline
        # -- see TimeAxisMixin._press_is_on_timing_bar. Set here, once
        # fancy_timing_bar exists, rather than at self.timeline's own
        # construction above.
        self.timeline.timing_bar = self.fancy_timing_bar

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
        # "No" in the gimmick entry dialog means stay where you were.
        if index == PAGE_GIMMICK and not self._enter_gimmick_page():
            self._show_page(current)
            return
        self._show_page(index)

    def _reload_timing_bars(self, state, force: bool = False) -> None:
        """Rebuild every kiai/bookmark/marker bar from `state`.

        Every bar, always -- an earlier version of this skipped the ones whose
        page was hidden, which was faster and wrong: it left them stale until
        you switched pages, and it quietly turned an existing "deleting a green
        line clears its marker" test into one that passed because the bar had
        never been updated at all.

        The saving is taken without touching that guarantee. There is a bar on
        each of three pages, all showing the same document, and the derived
        data -- kiai spans, one marker per timing point, bookmarks, the preview
        time -- is identical for all of them. It is computed once here and
        handed to each bar, instead of each bar walking every timing point in
        the map for itself.
        """
        data = self._cached(
            "bar_data",
            lambda: timeline_bar_data(state.document, state.duration_hint),
        )
        for bar in self._timing_bars:
            bar.apply_document_data(data)

    def _show_page(self, index: int) -> None:
        """Switch page and keep the header tab that owns it checked."""
        self.page_stack.setCurrentIndex(index)
        button = self.page_button_group.button(index)
        if button is not None:
            button.setChecked(True)
        # Which toolbox owns the number keys is a property of the page.
        self._refresh_tool_shortcut_scope()
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
        """Ask about unsaved difficulties. False means "stay where you are".

        A state the user already dismissed with "Continue Without Saving" is
        left out as long as nothing has changed on it since: `history.revision`
        bumps on every edit, so a match against the revision recorded at that
        click is exactly "still the same unsaved edits the user already said
        to ignore" -- and a mismatch means a real new edit, which is legitimate
        to ask about again.
        """
        dirty = [
            state for state in self._states.values()
            if state.history.dirty
            and self._discard_acknowledged.get(state.source_path) != state.history.revision
        ]
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
        leave_button = box.addButton(tr("MainWindow", "Continue Without Saving"), QMessageBox.DestructiveRole)
        box.addButton(tr("MainWindow", "Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            self.save_all_states()
            return True
        if clicked is leave_button:
            for state in dirty:
                self._discard_acknowledged[state.source_path] = state.history.revision
            return True
        return False

    def closeEvent(self, event) -> None:
        # Same guard as Esc: quitting is the other way to walk away from
        # unwritten edits, and it is the one that cannot be undone.
        #
        # spontaneous() gates it: True only when the close came from the
        # window manager (the X button, Alt+F4) -- someone quitting. A
        # programmatic close() is code tidying up, including every test's
        # teardown, and a modal prompt there has nobody to answer it.
        if not event.spontaneous() or self._confirm_leaving_editor():
            self._release_application_hooks()
            self._release_audio_file()
            event.accept()
        else:
            event.ignore()

    def _release_application_hooks(self) -> None:
        """Unhook this window from QApplication once it is really closing.

        Two registrations in __init__ are on the *application*, which outlives
        every window: an event filter and a focusChanged slot. Neither is
        undone by close() or by the window being garbage collected, and Qt
        dispatches **every application event through every installed filter**
        -- so a second live registration doubles the per-event Python work, a
        third triples it, and so on.

        One window per run never noticed. The test suite builds one per test
        and noticed badly: twelve windows took 171ms to build the first and
        976ms the twelfth (5.7x), and the whole suite degraded quadratically
        past four hours. Releasing here makes that flat -- 164ms to 154ms over
        the same twelve -- with the same number of objects still alive, which
        is the proof that the cost was the fan-out and not the memory.

        Idempotent: removeEventFilter on an unregistered filter is a no-op, and
        the disconnect is guarded, so a second close (or a close after a
        failed one) is harmless.
        """
        app = QApplication.instance()
        if app is None:
            return
        app.removeEventFilter(self)
        try:
            app.focusChanged.disconnect(self._editor_view_focus_changed)
        except (RuntimeError, TypeError):
            # Already disconnected, or the C++ side is gone.
            pass

    def _audio_backend_failed(self, error, message: str) -> None:
        """Remember to use the compatible decoder after the accurate one refuses a file.

        The accurate backend (Windows Media Foundation) has no Ogg Vorbis
        decoder of its own, and osu! song folders are full of .ogg -- so the
        first song it cannot open is the signal to stop preferring it. The
        switch only takes effect next launch, because Qt reads
        QT_MEDIA_BACKEND once when the multimedia plugin loads; see
        `select_media_backend`.

        Deliberately narrow. Only a decoding failure counts, and only for a
        file that is really there: a missing or renamed audio file raises the
        same kind of error and has nothing to do with the backend, and
        flipping the preference on it would cost accuracy for no reason.
        """
        if error not in (QMediaPlayer.FormatError, QMediaPlayer.ResourceError):
            return
        if os.environ.get("QT_MEDIA_BACKEND", "") != ACCURATE_MEDIA_BACKEND:
            return
        state = self.state
        if state is None or state.audio_path is None or not state.audio_path.is_file():
            return
        if self.settings.string_value(MEDIA_BACKEND_SETTING, "") == COMPATIBLE_MEDIA_BACKEND:
            return
        self.settings.set_value(MEDIA_BACKEND_SETTING, COMPATIBLE_MEDIA_BACKEND)
        self.settings.sync()
        self.show_toast(
            tr("MainWindow", "This song's audio needs the compatible decoder. Restart to play it.")
        )

    def _release_audio_file(self) -> None:
        """Let go of the song file on close.

        Qt's media backend holds the decoded source open until it is cleared,
        which on Windows is a live file lock -- the song cannot be moved,
        renamed, or deleted while a closed-but-not-cleared window still names
        it. The test suite hit the same lock from the other side: tearDown's
        TemporaryDirectory cleanup raced the backend and failed intermittently
        with NotADirectoryError on the fixture's audio.mp3.
        """
        self.player.stop()
        self.player.setSource(QUrl())

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

        change_folder_button = QPushButton(tr("MainWindow", "Change Folder"))
        change_folder_button.setFocusPolicy(Qt.NoFocus)
        change_folder_button.clicked.connect(self._choose_songs_folder)
        top.addWidget(change_folder_button)

        rescan_button = QPushButton(tr("MainWindow", "Rescan"))
        rescan_button.setToolTip(tr("MainWindow", "Look for songs added or changed since the last scan"))
        rescan_button.setFocusPolicy(Qt.NoFocus)
        rescan_button.clicked.connect(self._rescan_library)
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
        self.open_difficulty_button = QPushButton(tr("MainWindow", "Edit This Difficulty"))
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

    def _rescan_library(self) -> None:
        """Rescan means rescan: throw the index away and walk the folder again.

        The ordinary startup scan is incremental -- it reuses the cached entry
        for any file whose size and mtime are unchanged, which is what makes it
        fast. That also means a file the cache is *wrong* about (edited by
        another program, restored from a backup, saved with the same size in the
        same second) stays wrong however many times Rescan is pressed. Deleting
        the file is what makes the button mean what it says.
        """
        try:
            self._library_cache_path().unlink()
        except OSError:
            pass  # Never there, or not ours to delete: the walk still rebuilds it.
        self._library_cache = {}
        self._library_songs = {}
        self._listed_paths = set()
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

    # -- Gimmick page ---------------------------------------------------------

    def _build_gimmick_page(self) -> QWidget:
        """The gimmick editor's six layers, in the Editor page's own stack.

        Empty until a session is opened: which difficulty this page edits is
        decided by GimmickEntryDialog on first entry, not by whatever happened
        to be open.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Same viewing-controls strip the Editor page has, with its own widget
        # instances: a QWidget lives in exactly one layout, so these cannot be
        # the Editor page's own (see self._timing_bars on the same point).
        strip = QHBoxLayout()
        strip.setSpacing(6)

        self.gimmick_snap_combo = QComboBox()
        for divisor in SNAP_DIVISORS:
            self.gimmick_snap_combo.addItem(f"1/{divisor}", divisor)
        self.gimmick_snap_combo.setCurrentText("1/4")
        self.gimmick_snap_combo.currentIndexChanged.connect(self._gimmick_snap_changed)
        strip.addWidget(self.gimmick_snap_combo)

        self.gimmick_timeline_strip = QLabel()
        strip.addWidget(self.gimmick_timeline_strip)

        self.gimmick_timing_bar = TimingOverviewBar()
        self.gimmick_timing_bar.seek_requested.connect(self.seek_audio)
        strip.addWidget(self.gimmick_timing_bar, 1)
        self._timing_bars.append(self.gimmick_timing_bar)

        # The Editor page's own "+", which this strip was missing. It adds to
        # the layer stack below rather than to a difficulty group: the gimmick
        # page edits one difficulty, so there is only one place a view can go.
        self.gimmick_add_view_button = QPushButton("+")
        self.gimmick_add_view_button.setToolTip(tr("MainWindow", "open new view"))
        self.gimmick_add_view_button.setFocusPolicy(Qt.NoFocus)
        self.gimmick_add_view_button.clicked.connect(self._open_gimmick_add_view_dialog)
        strip.addWidget(self.gimmick_add_view_button)

        self.gimmick_play_button = QPushButton("▶")
        self.gimmick_play_button.setFocusPolicy(Qt.NoFocus)
        self.gimmick_play_button.clicked.connect(self.toggle_playback)
        strip.addWidget(self.gimmick_play_button)

        self.gimmick_playback_speed_buttons = []
        for label, rate in (("25%", .25), ("50%", .5), ("75%", .75), ("100%", 1.0)):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("playbackRate", rate)
            button.clicked.connect(lambda checked=False, r=rate: self._change_playback_speed(r))
            self.gimmick_playback_speed_buttons.append(button)
            strip.addWidget(button)

        layout.addLayout(strip)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.gimmick_status = QLabel()
        self.gimmick_status.setWordWrap(True)
        status_row.addWidget(self.gimmick_status, 1)
        # The entry question is asked once and then remembered forever, so
        # without this there was no way to correct the answer -- including the
        # common one of pairing before the properly-timed difficulty existed.
        self.gimmick_reference_button = QPushButton(tr("MainWindow", "Timing Reference..."))
        self.gimmick_reference_button.setFocusPolicy(Qt.NoFocus)
        self.gimmick_reference_button.clicked.connect(self._change_gimmick_reference)
        status_row.addWidget(self.gimmick_reference_button)
        layout.addLayout(status_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Kept, because every visit to this page rebuilds all six layers and a
        # rebuilt layout starts scrolled to the top -- see `_open_gimmick_layers`.
        self.gimmick_scroll = scroll
        views_container = QWidget()
        self.gimmick_views_layout = QVBoxLayout(views_container)
        self.gimmick_views_layout.setContentsMargins(4, 4, 4, 4)
        self.gimmick_views_layout.setSpacing(10)
        self.gimmick_views_layout.addStretch(1)
        scroll.setWidget(views_container)
        layout.addWidget(scroll, 1)

        # One row per layer, only ever one visible: clicking a layer brings up
        # that layer's own toolbox. Six rows and not three shared ones because
        # the same word means a different structure per layer -- Don is a fake
        # slider in layer 2 and a barline note in layer 3 -- and because the
        # two gimmick SV layers carry tools the plain SV layer does not.
        self.gimmick_tool_rows: dict[str, QWidget] = {}
        self.gimmick_tool_buttons: dict[str, dict[str, QPushButton]] = {}
        self._gimmick_row_buttons: list[QPushButton] = []
        for layer_id, _view_type, _label in self.GIMMICK_LAYERS:
            row = self._build_gimmick_row(layer_id)
            self.gimmick_tool_rows[layer_id] = row
            row.setVisible(False)
            layout.addWidget(row)

        self._gimmick_views: list[EditorViewFrame] = []
        return page

    # Per layer: (tool id, label). The digit is the button's position, so 1-N
    # line up with the shortcut keys whatever the layer.
    GIMMICK_TOOLSETS = {
        "chart": (
            ("select", "Select"), ("don", "Don"), ("kat", "Kat"),
            ("slider", "Slider"), ("spinner", "Spinner"), ("new_combo", "New Combo"),
        ),
        "fake_slider": (
            ("select", "Select"), ("regular", "Fake Slider"),
            ("don", "Don"), ("kat", "Kat"), ("shiny", "Shiny"),
            ("multi", "Multiple Fake Slider"),
            ("function", "Function"), ("convert", "Convert Notes"),
        ),
        "barline": (
            ("select", "Select"), ("don", "Don"), ("kat", "Kat"),
            ("red_line", "Red Line"), ("function", "Function"),
            ("convert", "Convert Notes"),
        ),
        "sv_chart": (
            ("select", "Select"), ("green_line", "Green Line"), ("function", "Function"),
        ),
        "sv_fake_slider": (
            ("select", "Select"), ("green_line", "Green Line"), ("function", "Function"),
        ),
        "sv_barline": (
            ("select", "Select"), ("green_line", "Green Line"), ("function", "Function"),
        ),
        # Both tools act on a dragged range rather than on a click: Kiai sets
        # the flag across it, Volume sweeps hitsound volume over it. Kiai used
        # to live in the fake-slider layer, which was never where it belonged --
        # a kiai section is read against every layer at once.
        "kiai_sound": (
            ("select", "Select"), ("kiai", "Kiai"), ("volume", "Volume"),
        ),
    }

    def _build_gimmick_row(self, layer_id: str) -> QWidget:
        """One layer's toolbox: numbered buttons plus its Config button.

        Built to the same shape as the Editor page's own tool rows -- the same
        leading caption, the same TOOL_BUTTON_HEIGHT, and the same equalized
        widths (applied in showEvent, since padding only lands after polish).
        """
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        # Literal tr() calls, one per caption -- see AddViewDialog on why a
        # dict lookup here would escape the i18n coverage gate. The two gimmick
        # object layers say what their tools build: the same "Don" writes a fake
        # slider in one and a run of red lines in the other.
        if layer_id.startswith("sv_"):
            caption = tr("MainWindow", "SV tools:")
        elif layer_id == "fake_slider":
            caption = tr("MainWindow", "Fake Slider Note Tool:")
        elif layer_id == "barline":
            caption = tr("MainWindow", "Barline Note Tool:")
        elif layer_id == "kiai_sound":
            caption = tr("MainWindow", "Kiai / Volume tools:")
        else:
            caption = tr("MainWindow", "Note tools:")
        layout.addWidget(QLabel(caption))

        # Nine tools in the fake-slider layer plus Config, and every layer's
        # row is equalized to one shared set (see the note where
        # _gimmick_row_buttons is extended below) -- so the window stylesheet's
        # 8px/16px button padding, applied at full size, made "Multiple Fake
        # Slider" alone push the assembled row past a monitor's width. Same
        # fix EditorViewFrame's chrome buttons and the pink +/- spin buttons
        # already needed: override padding and font-size directly on the
        # button, which -- like those two -- leaves the inherited pink
        # background/hover/checked look alone, since a leaf widget's own
        # QPushButton{} rule only overrides the properties it names.
        # Smaller than the app-wide 10px/18px so ten tools plus Config fit on
        # one row, but not so small the row stops being clickable -- 10px was
        # legible and unpleasant to aim at. Widths are no longer equalized
        # across layers (see equalize_button_widths), which is what actually
        # bought the room.
        gimmick_button_style = "QPushButton { padding: 4px 10px; font-size: 12px; }"

        buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(row)
        group.setExclusive(True)
        for index, (tool_id, label) in enumerate(self.GIMMICK_TOOLSETS[layer_id], start=1):
            button = QPushButton(f"{index}. {tr('MainWindow', label)}")
            button.setCheckable(True)
            button.setFixedHeight(self.TOOL_BUTTON_HEIGHT)
            button.setStyleSheet(gimmick_button_style)
            button.setFocusPolicy(Qt.NoFocus)
            button.setChecked(tool_id == "select")
            button.toggled.connect(
                lambda checked, t=tool_id, lid=layer_id:
                    self._set_gimmick_tool(lid, t) if checked else None
            )
            group.addButton(button)
            buttons[tool_id] = button
            layout.addWidget(button)

        config_button = QPushButton(tr("MainWindow", "Config"))
        config_button.setFixedHeight(self.TOOL_BUTTON_HEIGHT)
        config_button.setStyleSheet(gimmick_button_style)
        config_button.setFocusPolicy(Qt.NoFocus)
        config_button.clicked.connect(lambda _=False, lid=layer_id: self._open_gimmick_config(lid))
        layout.addWidget(config_button)

        layout.addStretch(1)
        self.gimmick_tool_buttons[layer_id] = buttons
        # Every gimmick row swaps into the same spot, so they are sized as one
        # set -- Config included, or it alone stays narrower than the tools.
        self._gimmick_row_buttons.extend([*buttons.values(), config_button])
        return row

    def _show_gimmick_row_for(self, view) -> None:
        """Bring up the toolbox belonging to the layer that just got focus."""
        layer = getattr(view, "gimmick_layer", None)
        if layer is None:
            return
        for layer_id, row in self.gimmick_tool_rows.items():
            row.setVisible(layer_id == layer)
        self._active_gimmick_layer = layer
        self._refresh_tool_shortcut_scope()

    def _set_gimmick_tool(self, layer_id: str, tool_id: str) -> None:
        """Set a tool on the one layer that owns it.

        Per layer, not globally: the layers have different toolsets, and a tool
        chosen in the barline layer means nothing in the fake-slider one.

        "Multiple Fake Slider" needs a run shape (start, end, distance) before
        it can place anything, so selecting it opens that config here -- once,
        on selection, not on every click a placement tool would otherwise take
        silently. Cancelling leaves nothing configured, so the button falls
        back to Select the same way a moved-away-and-back focus change does
        (see the `moved` branch in `_editor_view_focus_changed`) rather than
        sitting armed with a run nobody chose.
        """
        if tool_id == "multi":
            dialog = MultiFakeSliderDialog(self)
            if dialog.exec() != QDialog.Accepted:
                self.gimmick_tool_buttons[layer_id]["select"].setChecked(True)
                return
            self._multi_fake_slider_params = dialog.parameters()
        for frame in self._gimmick_views:
            if getattr(frame, "gimmick_layer", None) != layer_id:
                continue
            view = getattr(frame, "chart_view", None) or getattr(frame, "sv_view", None)
            if view is None:
                continue
            # Set every time, not only when New Combo is the button pressed:
            # it shares the row's exclusive button group, so the only signal
            # that it has been turned *off* is another tool being turned on.
            # Without this it latched -- pressed once, every note placed in the
            # layer afterwards carried the flag, with the button no longer even
            # highlighted.
            #
            # Chart views only: an SV view has no combo, and calling it there
            # raised before `set_tool` was ever reached -- which is why Green
            # Line and Function in layers 4, 5 and 6 did nothing at all.
            if hasattr(view, "set_new_combo"):
                view.set_new_combo(tool_id == "new_combo")
            if tool_id != "new_combo":
                view.set_tool(tool_id)

    def _open_gimmick_add_view_dialog(self) -> None:
        """The gimmick page's "+": another band under the six fixed layers.

        The difficulty is not asked for -- the page edits the paired one, and a
        band showing a different map next to the six that don't would only be
        confusing. Everything else is the Editor page's own add-view path, so an
        added view is fully wired for editing rather than a read-only copy.
        """
        pairing = self._gimmick_pairing
        if pairing is None:
            QMessageBox.information(
                self,
                tr("MainWindow", "Open a map first"),
                tr("MainWindow", "Open a beatmap before adding a view."),
            )
            return
        label = tr("MainWindow", "Gimmick") + f": {pairing.target.name}"
        dialog = AddViewDialog([(label, pairing.target)], self, pairing.target)
        dialog.difficulty_combo.setEnabled(False)
        if dialog.exec() != QDialog.Accepted:
            return
        self._add_editor_view(
            dialog.selected_view_type(), pairing.target,
            container=self.gimmick_views_layout,
        )

    def _gimmick_config(self, layer_id: str) -> GimmickConfig:
        """The config the layer's tools build with. SV layers borrow the
        barline one, which is the only place they read a gimmick BPM from."""
        return self.gimmick_configs.get(layer_id, self.gimmick_configs["barline"])

    def _open_gimmick_config(self, layer_id: str) -> None:
        if layer_id not in self.gimmick_configs:
            layer_id = "barline"
        # The caution compares the two structure layers' spacing against each
        # other; an SV layer has no part in it, so it is shown the barline
        # config and the caution stays silent (its own spacing is the default).
        other = "fake_slider" if layer_id == "barline" else "barline"
        dialog = GimmickConfigDialog(
            self.gimmick_configs[layer_id], layer_id, self.gimmick_configs[other], self
        )
        if dialog.exec() == QDialog.Accepted:
            self.gimmick_configs[layer_id] = dialog.config()
            # Which lines each layer owns, and which of them are structure
            # centres, are read out of these configs on every refresh -- so a
            # changed BPM or offset only reaches the layers when one happens.
            self._refresh_gimmick_views()

    def _gimmick_index_path(self) -> Path:
        """Beside song_index.json -- see _library_cache_path on the location."""
        root = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        return Path(root or str(Path.home())) / "gimmick_index.json"

    def _enter_gimmick_page(self) -> bool:
        """Resolve which difficulty the gimmick editor edits. False = stay put.

        The pairing is asked once and then remembered permanently, so this is a
        no-op on every visit after the first.
        """
        if self.state is None:
            QMessageBox.information(
                self,
                tr("MainWindow", "Open a map first"),
                tr("MainWindow", "Open a beatmap before using the gimmick editor."),
            )
            return False

        key = index_key(self.state.source_path)
        pairing = self._gimmick_pairings.get(key)
        if pairing is not None and not pairing.target.is_file():
            # The remembered file is gone -- deleted by hand, or while testing.
            # A stale pairing would open a difficulty that no longer exists, so
            # forget it and ask again rather than failing on the way in.
            QMessageBox.information(
                self,
                tr("MainWindow", "Gimmick difficulty is missing"),
                tr("MainWindow", "{0} is no longer in the song folder, so its pairing was forgotten.")
                .format(pairing.target.name),
            )
            self._states.pop(pairing.target, None)
            del self._gimmick_pairings[key]
            save_index(self._gimmick_index_path(), self._gimmick_pairings)
            pairing = None
        if pairing is None:
            pairing = self._create_gimmick_pairing()
            if pairing is None:
                return False
            self._gimmick_pairings[key] = pairing
            save_index(self._gimmick_index_path(), self._gimmick_pairings)

        self._gimmick_pairing = pairing
        self._update_gimmick_status()
        self._open_gimmick_layers()
        return True

    def _update_gimmick_status(self) -> None:
        pairing = self._gimmick_pairing
        if pairing is None:
            return
        status = tr("MainWindow", "Editing: {0}").format(pairing.target.name)
        reference = pairing.reference or pairing.target
        status += "   " + tr("MainWindow", "Timing from: {0}").format(reference.name)
        self.gimmick_status.setText(status)

    def _timing_snapshot_of(self, path: Path) -> list[TimingPoint]:
        """Base timing read from `path`, preferring an open copy of it.

        A difficulty already open this session may carry unsaved edits, and the
        timing on screen is the one the answer is about -- re-reading the file
        would quietly snapshot a different grid than the one being pointed at.
        """
        state = self._states.get(path)
        document = state.document if state is not None else parse_osu(path)
        return snapshot_timing(document.timing_points)

    def _change_gimmick_reference(self) -> None:
        """Re-pick the difficulty the grid comes from, after the fact.

        The difficulty being edited is on the list too. It is the wrong answer
        once it has gimmicks of its own -- which is why the entry dialog leaves
        it out -- but it is the right one for a session that has not written any
        yet, or one deliberately built on its own timing.
        """
        pairing = self._gimmick_pairing
        if pairing is None:
            return
        choices = [
            Path(self.difficulty_combo.itemData(index))
            for index in range(self.difficulty_combo.count())
        ]
        if pairing.target not in choices:
            choices.append(pairing.target)
        # File names, not versions: two difficulties can share a version name,
        # and a list where two rows read the same cannot be chosen between.
        names = [path.name for path in choices]
        current = (pairing.reference or pairing.target).name
        name, accepted = QInputDialog.getItem(
            self,
            tr("MainWindow", "Timing reference"),
            tr("MainWindow", "Take snaps, scrolling and the BPM overlay from:"),
            names,
            names.index(current) if current in names else 0,
            False,
        )
        if not accepted or not name:
            return
        chosen = choices[names.index(name)]
        try:
            base_timing = self._timing_snapshot_of(chosen)
        except Exception as error:
            QMessageBox.warning(
                self,
                tr("MainWindow", "Could not read the timing reference"),
                tr("MainWindow", "{0} could not be read, so this difficulty's own timing is used instead.")
                .format(chosen.name) + f"\n\n{error}",
            )
            return
        if looks_gimmicked(base_timing):
            QMessageBox.warning(
                self,
                tr("MainWindow", "This difficulty already has gimmicks"),
                tr(
                    "MainWindow",
                    "Its timing is being used as the snap and scroll reference, so "
                    "both may behave erratically. Pairing a clean difficulty instead "
                    "avoids this.",
                ),
            )
        pairing.base_timing = base_timing
        pairing.reference = chosen
        save_index(self._gimmick_index_path(), self._gimmick_pairings)
        # Handed to the open layers rather than rebuilding them: the grid is all
        # that changed, and a rebuild would throw the cursor back to the top of
        # every band.
        for frame in self._gimmick_views:
            view = getattr(frame, "chart_view", None) or getattr(frame, "sv_view", None)
            if view is not None:
                view.set_base_timing(base_timing)
        self._update_gimmick_status()

    def _create_gimmick_pairing(self) -> GimmickPairing | None:
        """Ask the entry question, then create or adopt the target difficulty."""
        document = self.state.document
        source = self.state.source_path
        version = document.version or source.stem

        dialog = GimmickEntryDialog(version, self, self._timing_reference_choices(source))
        dialog.exec()
        action = dialog.selected_action()
        reference = dialog.selected_reference()
        dialog.deleteLater()
        if action == GimmickEntryDialog.CANCEL:
            return None

        # The base timing is snapshotted once, here, and drives snaps, scroll
        # and the BPM overlay for the life of the pairing. Taken from the chosen
        # reference difficulty when there is one, so the grid keeps describing
        # the *song* however many 60000 BPM lines land in the file being edited.
        base_timing, reference = self._reference_timing(document, reference)

        if looks_gimmicked(base_timing):
            QMessageBox.warning(
                self,
                tr("MainWindow", "This difficulty already has gimmicks"),
                tr(
                    "MainWindow",
                    "Its timing is being used as the snap and scroll reference, so "
                    "both may behave erratically. Pairing a clean difficulty instead "
                    "avoids this.",
                ),
            )

        if action == GimmickEntryDialog.USE_CURRENT:
            # Editing in place: the difficulty being edited *is* the timing
            # reference unless another was picked, so the pairing records it
            # rather than None. `_reference_timing` returns None when it fell
            # back to the open document, which is the same file -- naming it
            # is what lets the status line say where the grid comes from.
            return GimmickPairing(source, base_timing, reference or source)

        target = gimmick_path_for(source)
        if target.exists():
            QMessageBox.information(
                self,
                tr("MainWindow", "Gimmick difficulty already exists"),
                tr("MainWindow", "Opening the existing file instead of creating a second one:")
                + f"\n{target.name}",
            )
            return GimmickPairing(target, base_timing, reference)

        try:
            write_osu(
                document, target, gimmick_version_for(version),
                # The source's own values, not the export defaults: this is a
                # copy of a difficulty, not a new one.
                force_ar=difficulty_setting(document, "ApproachRate", 5.0),
                force_cs=difficulty_setting(document, "CircleSize", 5.0),
            )
        except Exception as error:
            QMessageBox.critical(self, tr("MainWindow", "Could not create the difficulty"), str(error))
            return None
        return GimmickPairing(target, base_timing, reference)

    def _timing_reference_choices(self, source: Path) -> list[tuple[str, Path]]:
        """Every difficulty in this song, for the timing reference combo.

        The one being entered from is included, and listed **first** so it is
        what the combo shows before the list is ever opened. It used to be
        excluded on the grounds that a difficulty is a poor reference for its
        own gimmicks -- true once the file fills with 60000 BPM lines, but the
        snapshot is taken *before* any of that, so its timing is still the
        song's at the moment it is read. Excluding it meant the common case --
        "this map is already timed, just use it" -- had no answer at all, and
        "Use This One" had to silently pick something else.
        """
        source_resolved = source.resolve()
        choices = [
            (self.difficulty_combo.itemText(i), Path(self.difficulty_combo.itemData(i)))
            for i in range(self.difficulty_combo.count())
        ]
        own = [c for c in choices if c[1].resolve() == source_resolved]
        others = [c for c in choices if c[1].resolve() != source_resolved]
        return own + others

    def _reference_timing(self, document, reference: Path | None):
        """(base timing snapshot, the file it came from or None).

        A reference that will not parse falls back to the open document rather
        than refusing to open the editor -- a missing grid is worth a warning,
        not a locked door.
        """
        if reference is not None:
            try:
                return snapshot_timing(parse_osu(reference).timing_points), reference
            except Exception as error:
                QMessageBox.warning(
                    self,
                    tr("MainWindow", "Could not read the timing reference"),
                    tr("MainWindow", "{0} could not be read, so this difficulty's own timing is used instead.")
                    .format(reference.name) + f"\n\n{error}",
                )
        return snapshot_timing(document.timing_points), None

    # Every layer has to share one screen, so each is a band rather than a
    # full-height view. Tall enough for a note and its snap ticks; anything
    # less and the SV graph has no room to say anything. This is the *view's*
    # height -- its frame adds a chrome row on top -- and the views scale their
    # drawing to it against TimelineGameplay.DESIGN_HEIGHT.
    GIMMICK_LAYER_HEIGHT = 88

    # The layers, top to bottom: the three object layers, an SV layer for each,
    # and last the one that spans them all. (id, view type, label).
    #
    # "kiai_sound" is an SV view with no `point_times` filter, so it draws every
    # timing point in the map -- red, green, and yellow where both share a
    # millisecond. That whole-map picture is the point of it: kiai and hitsound
    # volume are properties of the active timing point, so they are read against
    # every layer at once rather than against one structure's lines.
    GIMMICK_LAYERS = (
        ("chart", "chart", "Normal chart"),
        ("fake_slider", "chart", "Fake sliders"),
        ("barline", "chart", "Barline gimmicks"),
        ("sv_chart", "sv", "SV (normal chart)"),
        ("sv_fake_slider", "sv", "SV (fake sliders)"),
        ("sv_barline", "sv", "SV (barlines)"),
        ("kiai_sound", "sv", "Kiai and Sound Volume"),
    )

    @staticmethod
    def is_fake_slider(note) -> bool:
        """A drumroll of negative length: drawn, never hittable."""
        return note.is_slider and note.length is not None and note.length < 0

    # AddViewDialog's chart-only types, and the gimmick layer each one is.
    # Adding "Barlines Only" from the dialog gets the same view the gimmick
    # page's third layer is, rather than a second implementation of it.
    CHART_ONLY_VIEWS = {
        "chart_regular": "chart",
        "chart_fake_slider": "fake_slider",
        "chart_barline": "barline",
    }
    # The same three layers as a scrolling gameplay preview. Only the object
    # filter carries over: the gameplay viewer has no tools, no red lines of its
    # own and no ghost, so the rest of _apply_chart_layer means nothing here.
    GAMEPLAY_ONLY_VIEWS = {
        "gameplay_regular": "chart",
        "gameplay_fake_slider": "fake_slider",
        "gameplay_barline": "barline",
    }

    def _shiny_times(self, document) -> set[int]:
        """Milliseconds the fake slider layer draws as a shiny note.

        A structure hangs off something: the note it decorates, or -- when
        there is no note -- its own red line. `shiny_offset_ms` after that
        thing is a shiny; `fake_slider_offset_ms` after it is a fake slider.

        **A note outranks a line, and is checked at both distances first.**
        That order is the whole subtlety, and it is what a real map looks
        like::

            87124  note            + 66666 BPM squash
            87125  red line        <- the don's barline, one forward
            87126  fake slider     + its own SV

        The object at 87126 is two milliseconds after its *note*, so it is a
        fake slider -- but one millisecond after the barline that happens to
        sit between them. Reading the nearest anchor of any kind called it a
        shiny. Asking the note first gets it right, and asking the line only
        when no note is in range still covers a structure standing on its own
        (our own tools write one that way when you place onto empty space).

        Position alone decides -- kiai has no say, and neither does whether
        the object has a line of its own. `+1` is a shiny and `+2` is a fake
        slider, everywhere, always.
        """
        return self._cached("shiny", lambda: self._compute_shiny_times(document))

    def _compute_shiny_times(self, document) -> set[int]:
        config = self._gimmick_config("fake_slider")
        shiny_offset = config.shiny_offset_ms
        slider_offset = config.fake_slider_offset_ms
        notes = {
            round(note.time) for note in document.hit_objects
            if not self.is_fake_slider(note)
        }
        reds = {
            round(point.time) for point in document.timing_points if point.uninherited
        }
        shiny = set()
        for at in (round(n.time) for n in document.hit_objects if self.is_fake_slider(n)):
            if at - shiny_offset in notes:
                shiny.add(at)
            elif at - slider_offset in notes:
                continue
            elif at - shiny_offset in reds:
                shiny.add(at)
        return shiny

    def _fake_slider_lines(self, document) -> tuple[set[int], set[int]]:
        return self._cached("fs_lines", lambda: self._compute_fake_slider_lines(document))

    def _compute_fake_slider_lines(self, document) -> tuple[set[int], set[int]]:
        """(head times, every structure-line time) for every fake-slider-layer object.

        A shiny's head is its own red line, `shiny_offset_ms` before the
        object. A fake slider's is its own millisecond -- rule 1 in
        `_shiny_times` is exactly "a fake slider has its own line" -- except a
        Don/Kat, whose object sits on the *restore* line and whose head is the
        squash line `fake_slider_offset_ms` earlier, on the note it hides.
        Read off the gimmick BPM rather than guessed from distance alone: a
        plain fake slider is routinely placed that same distance from an
        unrelated note (see the owner's file in `HandAuthoredShinyTests`), and
        only the BPM says which of the two this is.

        Layer 2 shows only the head per structure -- see
        `_apply_chart_layer`'s `timing_line_times_for`. Layers 1 and 3 need
        every line a structure owns, head and (for a Don/Kat) its restore
        too, so neither mistakes one for barline material.
        """
        config = self._gimmick_config("fake_slider")
        reds_by_time: dict[int, list] = {}
        for point in document.timing_points:
            if point.uninherited:
                reds_by_time.setdefault(round(point.time), []).append(point)
        shiny_ats = self._shiny_times(document)
        fake_slider_ats = {round(n.time) for n in document.hit_objects if self.is_fake_slider(n)}

        heads: set[int] = set()
        all_lines: set[int] = set()
        for at in shiny_ats:
            line = at - config.shiny_offset_ms
            heads.add(line)
            all_lines.add(line)
        for at in fake_slider_ats - shiny_ats:
            squash_at = at - config.fake_slider_offset_ms
            squashed = any(
                point.bpm == config.gimmick_bpm for point in reds_by_time.get(squash_at, ())
            )
            head = squash_at if squashed else at
            heads.add(head)
            all_lines.add(head)
            all_lines.add(at)
        return heads, all_lines

    def _gimmick_bpms(self) -> set[float]:
        """Every BPM that means "this red line is a structure's centre".

        Both object layers, because they hold separate configs and either may
        be retuned: keying the structure walk on the barline layer's number
        alone left a fake slider's own line unrecognised the moment the two
        differed, and deleting one then left its note behind.
        """
        return {
            self._gimmick_config(layer).gimmick_bpm
            for layer in ("barline", "fake_slider")
        }

    def _fake_slider_line_times(self, document) -> set[int]:
        """Every millisecond carrying a fake slider's own red lines."""
        gimmick, restores = self._fake_slider_lines(document)
        return gimmick | restores

    def _gimmick_line_times(self, document) -> set[int]:
        return self._cached("gimmick_lines", lambda: self._compute_gimmick_line_times(document))

    def _compute_gimmick_line_times(self, document) -> set[int]:
        """Milliseconds carrying a structure's own gimmick line."""
        bpms = self._gimmick_bpms()
        return {
            round(point.time) for point in document.timing_points
            if point.uninherited and point.bpm in bpms
        }

    def _layer_object_filter(self, layer_id: str):
        """Which hit objects one layer shows. Shared by both view kinds."""
        if layer_id == "fake_slider":
            return self.is_fake_slider
        if layer_id == "barline":
            # A barline gimmick is red lines; its notes belong to layer 1.
            return lambda note: False
        return lambda note: not self.is_fake_slider(note)

    def _apply_chart_layer(self, view: TimelineGameplay, layer_id: str) -> None:
        """Restrict a chart view to one kind of object, with its red lines.

        The three object layers, in one place because the gimmick page and
        AddViewDialog both build them:

        * **chart** -- the real chart. Fake sliders are excluded: they are drawn
          objects, not notes, and layer 2 owns them. Red lines are shown for
          reference, and are not editable here.
        * **fake_slider** -- fake sliders alone. Red lines are shown here too:
          a fake slider is only fake because of the gimmick line squashing it,
          so the line is the object's other half. Double-clicking one opens its
          BPM, as in layer 3; everything else about it stays view-only, since
          only the object knows how to move the structure it belongs to.
        * **barline** -- no hit objects at all, because a barline gimmick is
          made of red lines. Every red line in the map is drawn; the ones that
          do not sit on an object are this layer's own, and double-clicking one
          opens its BPM.
        """
        view.object_filter = self._layer_object_filter(layer_id)
        view.show_timing_lines = True
        # Which red lines are structure centres, by exact BPM out of the layer
        # configs -- what a drag lands on the grid and what a right click
        # resolves to. See TimelineGameplay.gimmick_times.
        view.gimmick_times_for = self._gimmick_line_times
        # A fake slider's two lines are layer 2's; every other red line in the
        # map -- the chart's own timing, a barline gimmick's, one placed by
        # hand -- is the barline layer's to edit. Set on layer 1 as well so the
        # two draw the same line in the same colour.
        if layer_id in ("chart", "barline"):
            view.foreign_times_for = self._fake_slider_line_times
        if layer_id == "fake_slider":
            # Only the gimmick line each fake slider is squashed by, which for a
            # Don or Kat sits on the note rather than on the slider. Every other
            # red line in the map is another layer's business and just clutters
            # this one.
            view.timing_line_times_for = lambda document: self._fake_slider_lines(document)[0]
            # ...and double-clicking that line opens it, the same dialog and
            # the same one-step EditTimingPoint the barline layer's lines get.
            # Its BPM is what squashes the object beside it, so it is as much
            # this layer's material as the object is -- but only for the
            # dialog: see TimelineGameplay.timing_dialog_enabled.
            view.timing_dialog_enabled = True
            view.ghost_style = "fake_slider"
            # This layer draws only its own structures' lines, and their BPM is
            # the thing being tuned -- a shiny's retimed line especially, where
            # the number *is* the effect. Layer 1 stays unlabelled: it shows
            # every red line in the map for reference, and a number beside each
            # of them is noise on top of a chart you are reading for notes.
            view.show_bpm_labels = True
            # Two rows: fake sliders above, shiny notes below, snap ticks down
            # the middle. The two structures sit a millisecond apart, which at
            # any readable zoom is the same pixel column.
            view.split_rows = True
            view.shiny_times_for = self._shiny_times
        elif layer_id == "barline":
            view.timing_edit_enabled = True
            view.timing_dialog_enabled = True
            view.ghost_style = "barline"
            view.show_bpm_labels = True

    def _expand_move(self, state: DifficultyState, note_uids: set[int], point_uids: set[int]):
        """Grow what a drag grabbed into the whole structure around it.

        A gimmick object is never one thing. A fake slider is a slider plus the
        60000 BPM line squashing it and the restore line after it; a barline
        note is a gimmick line, its mirrored restore lines and (usually) a real
        note on the same millisecond. Dragging any of those and leaving the rest
        behind does not move the gimmick, it dismantles it.

        Membership is read off the layer configs' own offsets rather than
        guessed from proximity, so a structure placed with different settings
        than the ones currently in the toolbox moves only the part that still
        matches -- visibly wrong, rather than quietly dragging a neighbour's
        lines along.
        """
        document = state.document
        notes = {note.uid: note for note in document.hit_objects if note.uid in note_uids}
        points = {p.uid: p for p in document.timing_points if p.uid in point_uids}

        # Indexed once. A selection of a thousand barlines dragged at once would
        # otherwise scan the whole document a thousand times over.
        red_at: dict[int, list[TimingPoint]] = {}
        for point in document.timing_points:
            if point.uninherited:
                red_at.setdefault(round(point.time), []).append(point)
        notes_at: dict[int, list] = {}
        for note in document.hit_objects:
            notes_at.setdefault(round(note.time), []).append(note)

        fake_config = self._gimmick_config("fake_slider")
        barline_config = self._gimmick_config("barline")
        # Every spacing a Don or Kat could actually have written a line at:
        # Don's own (`spacing_ms`) and Kat's three independent pairs. No
        # longer a single formula's n/n+2/n+4 -- see `barline_note` -- so the
        # four values are read straight off the config instead of derived.
        gimmick_spacings = (
            barline_config.spacing_ms,
            barline_config.kat_spacing1_ms,
            barline_config.kat_spacing2_ms,
            barline_config.kat_spacing3_ms,
        )
        gimmick_bpms = self._gimmick_bpms()

        # A fake slider's own line(s) -- see `_shiny_times` and
        # `_fake_slider_lines` for the same classification, done once for a
        # whole layer rather than per selected note here.
        shiny_ats = self._shiny_times(document)
        for note in list(notes.values()):
            if not self.is_fake_slider(note):
                continue
            at_time = round(note.time)
            if at_time in shiny_ats:
                head_times = (at_time - fake_config.shiny_offset_ms,)
            else:
                squash_at = at_time - fake_config.fake_slider_offset_ms
                squashed = any(
                    point.bpm == fake_config.gimmick_bpm for point in red_at.get(squash_at, ())
                )
                # A Don/Kat also has the squash line hiding its note; a plain
                # fake slider has only its own line, which is `at_time` itself.
                head_times = (squash_at, at_time) if squashed else (at_time,)
            for at in head_times:
                for point in red_at.get(at, ()):
                    points.setdefault(point.uid, point)
            # Every sibling on the same millisecond, not only the one that was
            # actually grabbed: a shiny or a stacked fake slider is several
            # hit objects on one timestamp, and a shiny's own red line is
            # never a gimmick-BPM line for the loop below to find them through
            # -- it carries the chart's own BPM (possibly retimed), like every
            # other fake-slider-layer line now does.
            for sibling in notes_at.get(at_time, ()):
                if self.is_fake_slider(sibling):
                    notes.setdefault(sibling.uid, sibling)

        # A real note standing on a gimmick line is that structure's note --
        # both layers write one, and it is what layer 1 shows of them. Without
        # this, nudging or deleting it there left the bars behind at the old
        # time: an invisible section of chart with nothing on screen to explain
        # it. The loop below then grows the rest of the structure from the line.
        for note in list(notes.values()):
            if self.is_fake_slider(note):
                continue
            for point in red_at.get(round(note.time), ()):
                if point.bpm in gimmick_bpms:
                    points.setdefault(point.uid, point)

        for point in list(points.values()):
            if not point.uninherited or point.bpm not in gimmick_bpms:
                continue
            at = round(point.time)
            # Both shapes at once: Don writes +/-`spacing_ms` and Kat writes
            # +/- each of its three independent spacings, all tried here
            # because there is no longer a formula relating one to the
            # others -- and only the offsets that actually carry a line
            # match, so trying every spacing for every gimmick line costs
            # nothing beyond a handful of extra dict misses.
            for value in gimmick_spacings:
                for offset in (-value, value):
                    for neighbour in red_at.get(at + offset, ()):
                        points.setdefault(neighbour.uid, neighbour)
            for note in notes_at.get(at, ()):
                notes.setdefault(note.uid, note)
            # ...and the drawn object one offset later, which is where a fake
            # slider or a shiny sits when the line was grabbed rather than the
            # object. Either offset: the two structures use their own, and only
            # the one that actually has sliders on it matches.
            for offset in (fake_config.fake_slider_offset_ms, fake_config.shiny_offset_ms):
                for note in notes_at.get(at + offset, ()):
                    if self.is_fake_slider(note):
                        notes.setdefault(note.uid, note)

        # Green lines ride along with the red line they are stacked on, or the
        # SV layers would be left pointing at a millisecond the structure has
        # moved off -- which is the same as losing them.
        moved_times = {round(point.time) for point in points.values() if point.uninherited}
        for point in document.timing_points:
            if not point.uninherited and round(point.time) in moved_times:
                points.setdefault(point.uid, point)

        return list(notes.values()), list(points.values())

    def _move_objects(self, difficulty_path: Path, note_uids, point_uids, delta_ms: float) -> None:
        """Apply a select-tool drag: retime what was grabbed, as one undo step."""
        state = self._states.get(difficulty_path)
        if state is None:
            return
        delta = round(delta_ms)
        if not delta:
            return
        notes, points = self._expand_move(state, set(note_uids), set(point_uids))
        commands = [
            SetNoteFields(note.uid, {"time": (note.time, int(note.time) + delta)})
            for note in notes
        ] + [
            EditTimingPoint(point.uid, {"time": (point.time, point.time + delta)})
            for point in points
        ]
        if not commands:
            return
        # Landing on an occupied millisecond replaces what is there, the same
        # as placing on one does. Without this a note dragged onto its
        # neighbour left two hit objects stacked on one timestamp -- one of
        # them permanently invisible, and both of them in the exported map.
        moving = {note.uid for note in notes}
        destinations = {int(note.time) + delta for note in notes if not self.is_fake_slider(note)}
        displaced = [
            note for note in state.document.hit_objects
            if note.uid not in moving
            and round(note.time) in destinations
            and not note.is_spinner
            and not self.is_fake_slider(note)
        ]
        if displaced:
            commands.insert(0, RemoveHitObjects(displaced))
        state.history.push(CompositeCommand(commands, "move_objects"), state)
        with self._refresh_cycle():
            self._refresh_difficulty_views(difficulty_path)
            self._refresh_difficulty_sv_views(difficulty_path)

    def _delete_gimmick_objects(self, difficulty_path: Path, note_uids=(), point_uids=()) -> None:
        """Delete a gimmick object with the structure around it, as one step.

        The same expansion a drag uses (`_expand_move`), for the same reason:
        removing the slider and leaving the 60000 BPM line squashing it does not
        delete the gimmick, it leaves the wreckage of one behind -- an invisible
        section of chart that nothing on screen explains any more. Right click
        and Delete both come through here, so the two agree.

        Green lines are the exception, and deliberately: an SV layer's line
        describes the chart's scroll speed at a millisecond rather than
        belonging to the object that happened to be there, and rebuilding a
        sweep because one note was retyped is worse than a line left behind.
        Red lines go.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        notes, points = self._expand_move(state, set(note_uids), set(point_uids))
        points = [point for point in points if point.uninherited]
        if not notes and not points:
            return
        commands = []
        if notes:
            commands.append(RemoveHitObjects(notes))
        if points:
            commands.append(RemoveTimingPoints(points))
        state.history.push(CompositeCommand(commands, "delete_gimmick"), state)
        # Note selection only: refresh_notes prunes the red-line selection
        # against the document by itself, but `selected` is original_index and
        # survives its notes, so it would keep drawing them as selected.
        gimmick_views = (
            getattr(frame, "chart_view", None) for frame in self._gimmick_views
        )
        for view in (*self._editor_views_for(difficulty_path), *gimmick_views):
            selection = getattr(view, "selected", None)
            if isinstance(selection, set):
                selection.clear()
        with self._refresh_cycle():
            self._refresh_difficulty_views(difficulty_path)
            self._refresh_difficulty_sv_views(difficulty_path)

    def _delete_timing_lines(self, difficulty_path: Path, uids) -> None:
        """Remove red lines, from a right click or a Delete on a selection.

        One command whatever the count, so clearing a generated run of a
        thousand lines is one Ctrl+Z rather than a thousand.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        wanted = set(uids)
        victims = [point for point in state.document.timing_points if point.uid in wanted]
        if not victims:
            return
        state.history.push(RemoveTimingPoints(victims), state)
        with self._refresh_cycle():
            self._refresh_difficulty_views(difficulty_path)
            self._refresh_difficulty_sv_views(difficulty_path)

    def _generate_barlines(self, difficulty_path: Path, start_ms: float, end_ms: float) -> None:
        """The barline layer's function tool: a run of red lines over a range.

        The range's start is snapped the same way a placement is, so a generated
        run begins where a hand-placed line would have.
        """
        pairing = self._gimmick_pairing
        state = self._states.get(difficulty_path)
        if state is None or pairing is None:
            return
        start_ms = snap_time(pairing.base_timing, start_ms, self._gimmick_snap_divisor())
        dialog = BarlineFunctionDialog(
            start_ms, end_ms, self,
            base_timing=pairing.base_timing, snap_divisor=self._gimmick_snap_divisor(),
            # Fake sliders are excluded: they are drawn objects rather than
            # notes, and a red line on one is the structure that makes it fake.
            note_times={
                round(note.time) for note in state.document.hit_objects
                if not self.is_fake_slider(note)
            },
            current_sv=sv_at(sorted_by_time(state.document.timing_points), start_ms),
        )
        accepted = dialog.exec() == QDialog.Accepted
        times = dialog.times() if accepted else []
        # Read before the dialog goes: the growth curve lives on its widgets.
        bpms = dialog.bpms(times) if accepted else []
        sv = dialog.sv_multiplier() if accepted else None
        dialog.deleteLater()
        if not times:
            return
        # Never a second uninherited point on a millisecond that already has
        # one: only the first is meaningful, and the rest are invisible clutter
        # that the layer would then draw and hit-test forever.
        taken = {round(p.time) for p in state.document.timing_points if p.uninherited}
        ordered = sorted_by_time(state.document.timing_points)
        points = []
        for at, bpm in zip(times, bpms):
            if at in taken:
                continue
            # Per point, not once for the range: hitsound volume is carried by
            # whichever timing point is in force, and a run long enough to be
            # worth generating routinely crosses a volume change. Taking the
            # range's opening volume would flatten every later change back to
            # it -- and taking no template at all would reset the whole run to
            # 100%, which is what this used to do.
            template = active_point_at(ordered, at)
            points.append(TimingPoint.uninherited_at(at, bpm, template=template))
            # Straight after its red line, never before: osu! resolves a shared
            # timestamp by file order, and the red one has just reset SV to
            # 1.0x, so the other way round the reset would win and the run
            # would flatten the chart's speed exactly as it did before this
            # existed. `sv_at` is read against the document as it was, not as
            # the run leaves it, so "current" means the speed being replaced.
            points.append(TimingPoint.inherited_at(
                at, sv if sv is not None else sv_at(ordered, at), template=template,
            ))
        if not points:
            return
        # Same reason as _place_gimmick: a thousand generated lines with kiai
        # off is a thousand ways to kill the section they were drawn across.
        carry_active_state(points, state.document.timing_points)
        state.history.push(InsertTimingPoints(points), state)
        with self._refresh_cycle():
            self._refresh_difficulty_views(difficulty_path)
            self._refresh_difficulty_sv_views(difficulty_path)

    def _edit_timing_line(self, difficulty_path: Path, uid: int) -> None:
        """Retype one timing line, from a double click on it.

        Red or green: the dialog reads which from the point itself, so a BPM
        line and an SV line are edited the same way and both can be moved to a
        different millisecond while they are open.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        point = next((p for p in state.document.timing_points if p.uid == uid), None)
        if point is None:
            return
        dialog = TimingLineDialog(point, self)
        accepted = dialog.exec() == QDialog.Accepted
        changes = dialog.changes(point) if accepted else {}
        dialog.deleteLater()
        if not changes:
            return
        state.history.push(EditTimingPoint(uid, changes), state)
        with self._refresh_cycle():
            self._refresh_difficulty_views(difficulty_path)
            self._refresh_difficulty_sv_views(difficulty_path)

    def _sv_layer_times(self, layer_id: str, document) -> set[int] | None:
        """Milliseconds an SV layer owns, or None for a layer that owns them all.

        Its objects' own times, *and* those times shifted by the layer's
        `sv_offset_ms`. The shift is part of ownership rather than something
        applied afterwards: a layer recognises its green lines by exact
        millisecond, so if the offset moved a generated point without moving
        what the layer looks for, the point would vanish from the layer that
        made it. One number, read here and by the Generate dialog both.

        Both, not just the shifted set, because a map arrives with SV already on
        it. Owning only `note - 5` would have hidden every green line a mapper
        had ever put on a note, in the layer whose whole job is to show them.
        """
        times = self._sv_layer_object_times(layer_id, document)
        if times is None:
            return None
        offset = self._gimmick_config(layer_id).sv_offset_ms
        return times | {at + offset for at in times} if offset else times

    def _sv_layer_object_times(self, layer_id: str, document) -> set[int] | None:
        """Where an SV layer's objects actually are, before its offset.

        Each SV layer owns a disjoint set of milliseconds, matched exactly so
        the three of them stop showing each other's green lines:

        * `sv_chart` -- every real (non-fake-slider) hit object's millisecond,
          plus each shiny's red-line millisecond (`shiny_offset_ms` before its
          object). A shiny's line sits on the note it decorates and stays
          visible -- there is no squash to hide -- so its SV is the chart's to
          hold, same as the note beside it. Nothing else: a plain fake
          slider's own line has never been chart SV, which is the bug this
          replaced -- it used to add *every* fake slider's gimmick line, so a
          plain fake slider's SV leaked into the normal-chart layer even where
          no note exists.
        * `sv_fake_slider` -- every fake slider's own millisecond (which is
          now its object's, not a separate restore line). A shiny's
          millisecond is excluded -- its speed is set from the chart layer
          above -- except when it already carries an inherited point, which
          stays visible so a generated or hand-written line there is editable
          rather than invisible.
        * `sv_barline` -- every other uninherited point's millisecond: the
          chart's own timing, barline gimmicks, hand-placed red lines. Minus
          whatever the two layers above own.

        Nothing here is stored: the sets are recomputed from the document on
        every refresh, so a line placed in a layer shows up in it immediately.
        """
        config = self._gimmick_config("fake_slider")
        fake_slider_ats = {round(n.time) for n in document.hit_objects if self.is_fake_slider(n)}
        shiny_ats = self._shiny_times(document)
        plain_ats = fake_slider_ats - shiny_ats
        shiny_lines = {at - config.shiny_offset_ms for at in shiny_ats}

        if layer_id == "sv_chart":
            return {
                round(n.time) for n in document.hit_objects if not self.is_fake_slider(n)
            } | shiny_lines
        if layer_id == "sv_fake_slider":
            green_at_shiny = {
                at for at in shiny_ats
                if any(p.inherited and round(p.time) == at for p in document.timing_points)
            }
            return plain_ats | green_at_shiny
        if layer_id == "sv_barline":
            reds = {round(p.time) for p in document.timing_points if p.uninherited}
            return reds - shiny_lines - plain_ats
        return None

    def _open_gimmick_layers(self) -> None:
        """(Re)build the gimmick layers for the current pairing.

        Every layer shows the same document -- the gimmick editor edits exactly
        one difficulty -- and differs only in which objects it shows and which
        toolbox drives it. They all snap against the base timing snapshot rather
        than the document's own, which the gimmick lines make unusable.
        """
        pairing = self._gimmick_pairing
        if pairing is None:
            return

        # Where the user was looking, across the teardown below. Six layers plus
        # anything added with "+" is taller than the page, so leaving for
        # another tab and coming back landed you at the top every time -- the
        # views themselves keep their playhead, but the *page* forgot which of
        # them you had scrolled down to.
        scrolled_to = self.gimmick_scroll.verticalScrollBar().value()

        for frame in list(self._gimmick_views):
            # Unregister before deleting, or the playhead broadcast keeps
            # calling set_time on a deleted C++ object.
            for attribute, registry in (
                ("chart_view", self._chart_views), ("sv_view", self._sv_views),
            ):
                view = getattr(frame, attribute, None)
                if view is not None:
                    if view in registry:
                        registry.remove(view)
                    if view in self._editor_snap_views:
                        self._editor_snap_views.remove(view)
                    # ...and drop it as the focused view, the same way
                    # _close_editor_view does. A rebuild happens on every visit
                    # to the page, and a stale reference here is not a crash --
                    # PySide6 keeps the Python object alive -- it is copy and
                    # paste silently acting on a layer that is no longer there.
                    for focus in ("_active_chart_view", "_active_sv_view",
                                  "_last_focused_editor_view"):
                        if getattr(self, focus, None) is view:
                            setattr(self, focus, None)
            self.gimmick_views_layout.removeWidget(frame)
            frame.deleteLater()
        self._gimmick_views.clear()

        try:
            state = self._ensure_state(pairing.target)
        except Exception as error:
            QMessageBox.critical(self, tr("MainWindow", "Open failed"), str(error))
            return

        divisor = self._gimmick_snap_divisor()
        for layer_id, view_type, label in self.GIMMICK_LAYERS:
            frame = EditorViewFrame(view_type, label, compact=True)
            frame.difficulty_path = pairing.target
            frame.gimmick_layer = layer_id

            if view_type == "chart":
                view = TimelineGameplay()
                view.set_symmetric(True)
                # A gimmick object is placed by hand and then nudged; the layers
                # show one kind of thing each, so a press that lands on
                # something is unambiguous about what it means to move.
                view.move_enabled = True
                self._apply_chart_layer(view, layer_id)
                view.timing_line_edit_requested.connect(
                    lambda uid, dp=pairing.target: self._edit_timing_line(dp, uid)
                )
                view.load_document(state.document)
            else:
                view = SVEditorView()
                view.point_times_for = (
                    lambda document, lid=layer_id: self._sv_layer_times(lid, document)
                )
                # The BPM belongs beside the lines themselves, which is layer 3.
                view.show_bpm_labels = layer_id != "sv_barline"
                # Layer 7 graphs note volume on a 0-100% axis, not scroll
                # speed. Set here as well as in _add_editor_view because the
                # gimmick page builds its bands itself rather than going
                # through that path -- and the band *is* where this layer
                # normally lives, so missing it here left the curve reading
                # SV in the one place anybody looks at it.
                view.volume_mode = layer_id == "kiai_sound"
                view.timing_line_edit_requested.connect(
                    lambda uid, dp=pairing.target: self._edit_timing_line(dp, uid)
                )
                # The same editing signals an Editor-page SV view gets. Without
                # these the SV layers were read-only: green-line placement,
                # dragging, deletion and Generate all reached nothing.
                # `lid` routes Generate to the layer's own objects, so a sweep
                # in layer 5 lands on fake sliders and nothing else.
                view.function_range_requested.connect(
                    lambda start_ms, end_ms, dp=pairing.target, v=view, lid=layer_id:
                        self._sv_range_action(dp, v.tool, start_ms, end_ms, layer_id=lid)
                )
                view.point_add_requested.connect(
                    lambda time_ms, sv, dp=pairing.target: self._add_sv_point(dp, time_ms, sv)
                )
                view.point_time_edit_requested.connect(
                    lambda uid, time_ms, dp=pairing.target: self._edit_sv_point_time(dp, uid, time_ms)
                )
                view.point_sv_edit_requested.connect(
                    lambda uid, sv, dp=pairing.target: self._edit_sv_point(dp, uid, sv)
                )
                view.point_delete_requested.connect(
                    lambda uid, dp=pairing.target: self._delete_sv_point(dp, uid)
                )
                view.points_delete_requested.connect(
                    lambda uids, dp=pairing.target: self._delete_sv_points(dp, uids)
                )
                view.load_document(state.document)

            view.set_base_timing(pairing.base_timing)
            # None of the six layers set their own zoom otherwise, so a chart
            # band opened at TimelineGameplay's 2000ms default and an SV band
            # at SVEditorView's 4000ms -- two different scales for views of
            # the same document that are supposed to line up.
            view.window_ms = self.DEFAULT_EDITOR_WINDOW_MS
            # Right-click-scrub target (see TimeAxisMixin._press_is_on_timing_bar):
            # every other view gets this, and the six layers were the one
            # place left without it.
            view.timing_bar = self.gimmick_timing_bar
            view.set_snap_divisor(divisor)
            view.current_time = state.playhead_ms
            view.seek_requested.connect(self.seek_audio)
            # Alt+wheel. Without this the view that caught the wheel changed its
            # own divisor and nothing else -- the combo went stale and the other
            # five layers kept the old grid, so which snap a placement used
            # depended on which band the pointer happened to be over.
            view.snap_changed_by_wheel.connect(self._gimmick_snap_changed_by_wheel)
            if view_type == "chart" and layer_id != "chart":
                # The tool carries the shape, the layer carries the kind.
                view.note_place_requested.connect(
                    lambda kind, time_ms, _combo, big, lid=layer_id:
                        self._place_gimmick(lid, kind, time_ms, big)
                )
            elif view_type == "chart":
                # Layer 1 is an ordinary chart, so its tools are the ordinary
                # ones -- and they were connected to nothing at all, which left
                # its whole toolbox (Don, Kat, Slider, Spinner, New Combo)
                # inert. Routed to the same handlers an Editor-page chart view
                # uses, so this stays a normal chart rather than being hijacked
                # into placing gimmick structures.
                view.note_place_requested.connect(
                    lambda kind, time_ms, combo, big, dp=pairing.target:
                        self._place_note(dp, kind, time_ms, combo, big)
                )
                view.note_place_with_duration_requested.connect(
                    lambda kind, start_ms, end_ms, combo, big, dp=pairing.target:
                        self._place_note_with_duration(dp, kind, start_ms, end_ms, combo, big)
                )
                view.note_duration_edit_requested.connect(
                    lambda uid, end_ms, dp=pairing.target: self._edit_note_duration(dp, uid, end_ms)
                )
            if view_type == "chart":
                # Right click and Delete both remove what the layer shows -- a
                # fake slider in layer 2, a barline note in layer 3, a normal
                # note in layer 1 -- together with the structure around it. The
                # view only ever offers objects that passed its own filter, so
                # this cannot reach past the layer into the rest of the chart.
                view.note_delete_requested.connect(
                    lambda uid, dp=pairing.target: self._delete_gimmick_objects(dp, [uid])
                )
                view.notes_delete_requested.connect(
                    lambda uids, dp=pairing.target: self._delete_gimmick_objects(dp, uids)
                )
                view.timing_lines_delete_requested.connect(
                    lambda uids, dp=pairing.target:
                        self._delete_gimmick_objects(dp, (), uids)
                )
                view.objects_move_requested.connect(
                    lambda notes, points, delta, dp=pairing.target:
                        self._move_objects(dp, notes, points, delta)
                )
                # One range signal, two tools: Function fills the range with
                # red lines, Convert rebuilds the notes already in it as this
                # layer's structures. The view says which is active rather than
                # the signal carrying it -- the gesture is identical.
                view.timing_function_range_requested.connect(
                    lambda start_ms, end_ms, dp=pairing.target, v=view, lid=layer_id:
                        self._gimmick_range_action(dp, lid, v.tool, start_ms, end_ms)
                )
            # Zoom is a property of the page, not of one band: the six layers
            # describe the same moment, so reading them together only works
            # while they are all on one scale.
            view.gimmick_layer = layer_id
            view.zoom_changed.connect(self._gimmick_zoom_changed)
            # Every layer on one screen: each gets a slice of the window rather
            # than the Editor page's full-height view, so the whole stack is
            # visible without scrolling between related layers.
            # The band is set on the *view*, not on the frame: a maximum on the
            # frame fought the view's own DESIGN_HEIGHT minimum, and the layout
            # resolved that by clipping -- every layer showed its top half and
            # nothing else. A fixed height on the view leaves nothing to
            # resolve, and the frame sizes itself around it and its chrome. It
            # has to be fixed rather than merely allowed: a bare QWidget has no
            # height of its own to fall back on, so a minimum of zero is what
            # the layout gives it.
            view.setFixedHeight(self.GIMMICK_LAYER_HEIGHT)
            frame.set_content(view)
            setattr(frame, "chart_view" if view_type == "chart" else "sv_view", view)

            # Registered for the playhead broadcast, which is what makes the
            # layers scroll with the song during playback.
            (self._chart_views if view_type == "chart" else self._sv_views).append(view)
            self._share_kiai_bands(view, sv=view_type != "chart")
            self._editor_snap_views.append(view)

            frame.closed.connect(self._close_gimmick_view)
            self.gimmick_views_layout.insertWidget(
                self.gimmick_views_layout.count() - 1, frame
            )
            self._gimmick_views.append(frame)

        # Something has to be showing before the first click lands -- and be
        # what copy, paste and the tool digits act on, or they would all be
        # aimed at nothing until a layer was clicked.
        first = self._gimmick_views[0]
        self._active_chart_view = first.chart_view
        self._show_gimmick_row_for(first.chart_view)

        # Deferred: the scrollbar's range is only recomputed once the rebuilt
        # layout has been laid out, and setting a value against a stale range
        # is silently clamped to whatever it was before.
        QTimer.singleShot(
            0, lambda: self.gimmick_scroll.verticalScrollBar().setValue(scrolled_to)
        )

    def _close_gimmick_view(self, frame: EditorViewFrame) -> None:
        """Close one of the six layers. They come back on the next visit.

        Six bands on one screen is the right default -- a gimmick is read
        across all of them -- but it is a lot of screen for work that is often
        happening in two, so the ✕ has to do something here as well. Nothing
        remembers which were closed: re-entering the tab rebuilds the stack
        (`_open_gimmick_layers`), which is the way back and needs no second
        control to say so.
        """
        if frame in self._gimmick_views:
            self._gimmick_views.remove(frame)
        self.gimmick_views_layout.removeWidget(frame)
        self._close_editor_view(frame)
        # The toolbox belongs to a layer; with that layer gone there is nothing
        # for it to act on, so it goes with it.
        if getattr(frame, "gimmick_layer", None) == getattr(self, "_active_gimmick_layer", None):
            for row in self.gimmick_tool_rows.values():
                row.setVisible(False)
            self._active_gimmick_layer = None
            self._refresh_tool_shortcut_scope()
        # ...and something has to be the focused layer afterwards, or the tool
        # digits, copy and paste are all aimed at nothing -- the same reason
        # _open_gimmick_layers picks one on the way in.
        for remaining in self._gimmick_views:
            view = getattr(remaining, "chart_view", None) or getattr(remaining, "sv_view", None)
            if view is None:
                continue
            if self._active_chart_view is None and hasattr(remaining, "chart_view"):
                self._active_chart_view = remaining.chart_view
            self._show_gimmick_row_for(view)
            break

    def _place_gimmick(self, layer_id: str, kind: str, time_ms: float, big: bool = False) -> None:
        """Place one gimmick object: its lines, its object, and its SV restore.

        Everything a single placement writes is one CompositeCommand, so a fake
        slider and the three or seven timing points that shape it undo together
        rather than one line at a time.

        `big` is Shift held at the click, the same flag Don/Kat placement takes
        -- it reaches the drawn object only where that object has a size of its
        own to give away; see `gimmick_session.fake_slider`.
        """
        pairing = self._gimmick_pairing
        if pairing is None:
            return
        state = self._states.get(pairing.target)
        if state is None:
            return
        time_ms = round(snap_time(pairing.base_timing, time_ms, self._gimmick_snap_divisor()))
        if kind == "multi":
            commands = self._multi_fake_slider_commands(state, pairing, time_ms, big)
        else:
            commands = self._gimmick_commands(
                state, pairing, layer_id, kind, time_ms, count_from(self._next_original_index(state)),
                big=big,
            )
        if not commands:
            return
        state.history.push(CompositeCommand(commands, "place_gimmick"), state)

        # Both refreshes reach the layers themselves now, so a placement needs
        # no third call of its own.
        with self._refresh_cycle():
            self._refresh_difficulty_views(pairing.target)
            self._refresh_difficulty_sv_views(pairing.target)

    def _multi_fake_slider_commands(self, state, pairing, click_ms: int, big: bool = False) -> list:
        """The Multiple Fake Slider tool: N plain fake sliders in one run.

        `click_ms` is where the click snapped to; the configured start/end/
        distance (`_multi_fake_slider_params`) locate the *structures*, one
        per position from `click_ms+start` to `click_ms+end` stepped by
        `distance` -- not their sliders. Each structure's own slider then
        lands `fake_slider_offset_ms` after its position the same way one
        click of the plain Fake Slider tool would place it, which is also why
        this can never write a shiny by accident: every object is at its
        structure's `+fake_slider_offset_ms`, never at the shiny's `+1`.
        Built with `_gimmick_commands(..., kind="regular", ...)` per position
        rather than a second structure builder.

        One counter shared across the whole run, the same reason
        `_generate_fake_sliders` and `_convert_notes_to_gimmick` share theirs:
        `_next_original_index` reads the document, which this batch has not
        written to yet, so asking it once per position would hand every note
        the same key.
        """
        start, end, distance = self._multi_fake_slider_params
        indices = count_from(self._next_original_index(state))
        commands: list = []
        position = click_ms + start
        limit = click_ms + end
        while position <= limit:
            commands.extend(self._gimmick_commands(
                state, pairing, "fake_slider", "regular", position, indices, big=big,
            ))
            position += distance
        return commands

    def _gimmick_commands(
        self, state, pairing, layer_id: str, kind: str, time_ms: int, indices,
        copies: int | None = None, big: bool = False,
    ) -> list:
        """Everything one gimmick structure writes, as commands. [] for a no-op.

        Split out of `_place_gimmick` so the convert tool can build a structure
        per note across a whole selection and push all of them as one undo step.
        `indices` is a counter for `original_index`, shared across a batch --
        `_next_original_index` reads the document, which a batch has not written
        to yet, so asking it once per note would give every one the same key.

        `time_ms` arrives already snapped: the batch path snaps to the notes it
        is converting, not to the grid.
        """
        if kind in ("don", "kat"):
            time_ms = self._note_centre_near(state.document, time_ms)
        config = self._gimmick_config(layer_id)
        # Shiny is a fake slider with its own offset and several sliders on it,
        # so the builder is the same one with a flag -- see gimmick_session.
        shiny = kind == "shiny"
        if shiny:
            kind = "regular"

        # One gimmick structure per millisecond, in every layer. osu! honours
        # only the first uninherited point on a timestamp, so a second structure
        # on one costs a pile of dead lines -- and a duplicated hit object,
        # which is invisible in the editor and a double note in game -- while
        # changing nothing on screen. One accidental double click away, since
        # the time is snapped before it gets here.
        #
        # The fake slider layer is the exception, and deliberately: its two
        # structures are told apart by *where* their object sits, so a fake
        # slider and a shiny on the same snap are two readable objects a
        # millisecond apart sharing one gimmick line -- which is what the
        # layer's two rows exist to show. There, the thing that must not be
        # duplicated is the object's own millisecond, not the line's.
        if layer_id == "fake_slider":
            at = time_ms + (config.shiny_offset_ms if shiny else config.fake_slider_offset_ms)
            if any(
                self.is_fake_slider(note) and round(note.time) == at
                for note in state.document.hit_objects
            ):
                self.show_toast(tr("MainWindow", "There is already a gimmick here."))
                return []
        else:
            line_at = time_ms + config.red_line_offset_ms if kind == "red_line" else time_ms
            occupied = [
                point for point in state.document.timing_points
                if point.uninherited and round(point.time) == line_at
            ]
            # A plain red line duplicating *any* line there is equally
            # meaningless; a structure is refused only by another structure, so
            # one can still be drawn on top of the chart's own timing.
            if any(point.bpm == config.gimmick_bpm for point in occupied) or (
                kind == "red_line" and occupied
            ):
                self.show_toast(tr("MainWindow", "There is already a gimmick here."))
                return []
        try:
            if layer_id == "fake_slider":
                points, notes = gimmick_fake_slider(
                    time_ms, pairing.base_timing, config, kind=kind, shiny=shiny,
                    copies=copies, big=big,
                )
            elif kind == "red_line":
                bpm = base_bpm_at(pairing.base_timing, time_ms)
                points, notes = gimmick_red_line(time_ms, bpm, config)
            else:
                points, notes = gimmick_barline_note(time_ms, pairing.base_timing, config, kind=kind)
        except GimmickConfigError as error:
            QMessageBox.warning(self, tr("MainWindow", "Cannot place this"), str(error))
            return []

        notes = self._without_redundant_note(state.document, notes, kind, time_ms)

        # The gimmick's own lines reset SV to 1.0x. Hand the chart's SV back at
        # the end of the cluster -- and for a structure, write that line even
        # when there was nothing to hand back. An SV layer matches its green
        # lines by exact millisecond, so with none there layers 5 and 6 had
        # nothing to show, drag or generate a sweep from: a 1.0x line agrees
        # with the timing either way and is the handle the layer is made of.
        #
        # The plain red-line tool is one line rather than a structure, and layer
        # 6 already owns its millisecond, so it keeps the old rule -- otherwise
        # every hand-placed line would come with a green one stacked on it.
        #
        # Appended last so it sorts *after* the red line sharing its
        # millisecond: osu! resolves a tie by file order, and the green one has
        # to win. InsertTimingPoints sorts stably on time alone.
        #
        # A shiny's own points are [red@T, green@T], so this handle's
        # millisecond -- the latest point's -- is T, and the builder has
        # already written the green line there. Appending a second one would
        # stack two inherited points on the same millisecond; skipped
        # whenever the cluster already carries an inherited point at the
        # handle's time, which is only ever the shiny case, since every other
        # structure's last point is the uninherited restore line.
        restore_at = max(point.time for point in points)
        already_handled = any(
            not point.uninherited and round(point.time) == round(restore_at) for point in points
        )
        if not already_handled:
            restore = sv_restore_point(state.document.timing_points, time_ms - 1, restore_at)
            if kind != "red_line" or restore.sv_multiplier != 1.0:
                points = [*points, restore]

        # Kiai is a property of the active timing point, so a gimmick line
        # written without it ends the section it lands in for everything after
        # it -- a chorus that stops being a chorus the moment a fake slider is
        # placed in it. Applied to the whole cluster, red and green alike.
        carry_active_state(points, state.document.timing_points)

        # A fake slider and a shiny on one snap share a gimmick line, so the
        # second of them must not write a duplicate: osu! honours only the first
        # uninherited point on a timestamp, and a second identical one is dead
        # weight the structure walk would then have to reason about.
        existing_red = {
            (round(point.time), point.beat_length)
            for point in state.document.timing_points if point.uninherited
        }
        points = [
            point for point in points
            if not point.uninherited
            or (round(point.time), point.beat_length) not in existing_red
        ]
        if not points and not notes:
            return []

        for note in notes:
            note.original_index = next(indices)

        commands = [InsertTimingPoints(points)] if points else []
        if notes:
            # Same "one object per millisecond" rule every other placement path
            # follows (_insert_notes). This path used to skip it, so a Kat
            # placed over an existing Don stacked two hit objects on the same
            # millisecond instead of replacing one -- invisible in the editor,
            # and a double note in game.
            #
            # Fake sliders are outside the rule in both directions: one is
            # decoration drawn beside a note rather than a note competing for
            # its millisecond, so placing one must not delete the chart's note
            # and placing a note must not delete one.
            displaced = [
                victim
                for note in notes
                if not self.is_fake_slider(note)
                for victim in self._notes_blocking(state.document, note.time, note.is_spinner)
                if not self.is_fake_slider(victim)
            ]
            if displaced:
                commands.append(RemoveHitObjects(displaced))
            commands.append(InsertHitObjects(notes))
        return commands

    def _gimmick_range_action(
        self, difficulty_path: Path, layer_id: str, tool: str, start_ms: float, end_ms: float,
    ) -> None:
        """Route a dragged range to whichever range tool the layer is holding.

        One gesture, one signal: Function and Convert are both "drag a range,
        then do something to it", and which of the two it was is a property of
        the view rather than of the drag.
        """
        if tool == "kiai":
            self._set_kiai_range(difficulty_path, start_ms, end_ms)
        elif tool == "convert":
            self._convert_notes_to_gimmick(difficulty_path, layer_id, start_ms, end_ms)
        elif layer_id == "fake_slider":
            self._generate_fake_sliders(difficulty_path, start_ms, end_ms)
        else:
            self._generate_barlines(difficulty_path, start_ms, end_ms)

    def _sv_range_action(
        self, difficulty_path: Path, tool: str, start_ms: float, end_ms: float,
        layer_id: str | None = None,
    ) -> None:
        """The same routing for an SV view's dragged range.

        One signal, three tools: the SV layers' Function, and the Kiai and
        Sound Volume layer's Kiai and Volume. Which one it was is a property of
        the view rather than of the drag -- see _gimmick_range_action.
        """
        if tool == "kiai":
            # The Kiai and Sound Volume layer's Kiai tool never creates a
            # green line (task: "you shouldn't be able to drag them at all"
            # where there is no timing point) -- so a range with nothing in
            # it is refused outright rather than silently inserting edges.
            state = self._states.get(difficulty_path)
            if state is not None and not any(
                round(start_ms) <= round(point.time) <= round(end_ms)
                for point in state.document.timing_points
            ):
                self.show_toast(tr("MainWindow", "No timing point in the selected range."))
                return
            self._set_kiai_range(difficulty_path, start_ms, end_ms)
        elif tool == "volume":
            self._open_volume_function_dialog(difficulty_path, start_ms, end_ms)
        else:
            self._open_sv_function_dialog(difficulty_path, start_ms, end_ms, layer_id=layer_id)

    def _set_kiai_range(
        self, difficulty_path: Path, start_ms: float, end_ms: float,
    ) -> None:
        """Turn a dragged range into one kiai section, across every layer.

        Kiai is a bit on a timing point rather than a span, so a section is
        really "every point from here on says kiai until one says it does not".
        A gimmick fills the range with hundreds of points -- a fake slider's two
        lines, a barline note's seven -- and each of them is a place the section
        can end by accident. Setting them all is the only way to say it once.

        Nothing is ever *created* here. The only toolbox with a Kiai tool is
        the Kiai and Sound Volume layer, and that layer must never write a
        green line -- so an edge with no timing point on it is left alone and
        the section starts (or ends) at the nearest existing point instead.
        This used to invent an inherited point at each edge restating the SV
        already in force, which is a scroll-speed edit made on the user's
        behalf in a layer that has nothing to do with scroll speed. If the
        range holds no timing point at all there is nothing to flag, so the
        caller toasts and does not call this (see _sv_range_action).

        The range arrives already snapped: the view that dragged it owns the
        grid (and the Ctrl override that trades it for whole milliseconds), so
        snapping a second time here would quietly undo that override.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        start, end = round(start_ms), round(end_ms)
        if end <= start:
            return

        ordered = sorted_by_time(state.document.timing_points)
        commands: list = []
        for point in ordered:
            at = round(point.time)
            if start <= at < end:
                wanted = point.effects | EFFECT_KIAI
            elif at == end:
                # The first point of what comes after: it is what ends the
                # section, so it has to say so.
                wanted = point.effects & ~EFFECT_KIAI
            else:
                continue
            if wanted != point.effects:
                commands.append(
                    EditTimingPoint(point.uid, {"effects": (point.effects, wanted)})
                )

        if not commands:
            return
        state.history.push(CompositeCommand(commands, "set_kiai"), state)
        with self._refresh_cycle():
            self._refresh_difficulty_views(difficulty_path)
            self._refresh_difficulty_sv_views(difficulty_path)

    def _generate_fake_sliders(
        self, difficulty_path: Path, start_ms: float, end_ms: float,
    ) -> None:
        """The fake slider layer's function tool: fill a range with objects.

        Fake sliders or shiny notes, on the same two rhythms the barline
        generator offers -- every n milliseconds for scenery, every n snaps for
        something the music can be read against.
        """
        pairing = self._gimmick_pairing
        state = self._states.get(difficulty_path)
        if state is None or pairing is None:
            return
        config = self._gimmick_config("fake_slider")
        start_ms = snap_time(pairing.base_timing, start_ms, self._gimmick_snap_divisor())
        dialog = FakeSliderFunctionDialog(
            start_ms, end_ms, self,
            base_timing=pairing.base_timing, snap_divisor=self._gimmick_snap_divisor(),
            shiny_count=config.shiny_count,
            shiny_bpm_multiplier=config.shiny_bpm_multiplier,
            fake_slider_bpm_multiplier=config.fake_slider_bpm_multiplier,
            # "At each note" turns a run of the chart's own notes into shiny
            # ones, which is what the fake slider layer's structures decorate.
            note_times={
                round(note.time) for note in state.document.hit_objects
                if note.is_circle
            },
        )
        accepted = dialog.exec() == QDialog.Accepted
        times = dialog.times() if accepted else []
        kind = dialog.object_kind() if accepted else "regular"
        count = dialog.shiny_count() if accepted else config.shiny_count
        multiplier = dialog.bpm_multiplier() if accepted else (
            config.shiny_bpm_multiplier if kind == "shiny" else config.fake_slider_bpm_multiplier
        )
        dialog.deleteLater()
        if not times:
            return
        # Written back before generating: the builder reads the multiplier off
        # the config, and the Shiny/Fake Slider tools and the detector have to
        # agree with what this run wrote. Same reason the SV dialog writes its
        # offset back. Which field depends on which object this run built --
        # a fake slider has no compensating green line, so its multiplier is
        # its own setting, never the shiny's.
        if kind == "shiny":
            config.shiny_count = count
            config.shiny_bpm_multiplier = multiplier
        else:
            config.fake_slider_bpm_multiplier = multiplier
        indices = count_from(self._next_original_index(state))
        commands: list = []
        for at in times:
            commands.extend(self._gimmick_commands(
                state, pairing, "fake_slider", kind, at, indices, copies=count,
            ))
        if not commands:
            return
        state.history.push(CompositeCommand(commands, "place_gimmick"), state)
        with self._refresh_cycle():
            self._refresh_difficulty_views(difficulty_path)
            self._refresh_difficulty_sv_views(difficulty_path)

    def _convert_notes_to_gimmick(
        self, difficulty_path: Path, layer_id: str, start_ms: float, end_ms: float,
    ) -> None:
        """Turn every plain note in a dragged range into this layer's structure.

        A gimmick is normally drawn around notes that already exist, and doing
        that by hand meant clicking each one -- so this is the same Don/Kat
        placement the tools make, aimed at the chart's own notes: a don becomes
        a Don structure and a kat a Kat one, on the note's own millisecond
        rather than on the grid, so nothing is retimed by being converted.

        Fake sliders and drumrolls are skipped: they are what the tool *writes*,
        and converting its own output is how a range gets gimmicked twice.
        """
        pairing = self._gimmick_pairing
        state = self._states.get(difficulty_path)
        if state is None or pairing is None:
            return
        indices = count_from(self._next_original_index(state))
        commands: list = []
        for note in sorted(state.document.hit_objects, key=lambda n: n.time):
            if not note.is_circle or not (start_ms <= note.time <= end_ms):
                continue
            commands.extend(self._gimmick_commands(
                state, pairing, layer_id, "kat" if note.is_kat else "don",
                round(note.time), indices,
            ))
        if not commands:
            self.show_toast(tr("MainWindow", "No notes in the selected range to convert."))
            return
        state.history.push(CompositeCommand(commands, "place_gimmick"), state)
        with self._refresh_cycle():
            self._refresh_difficulty_views(pairing.target)
            self._refresh_difficulty_sv_views(pairing.target)

    # How far off its own snap a chart's note is allowed to be and still count
    # as the note this structure is being drawn around. A snap is rarely a whole
    # millisecond, and the millisecond the map itself wrote for it need not be
    # the one round() picks -- osu! rounds a .5 up, Python rounds it to even.
    NOTE_CENTRE_TOLERANCE_MS = 2

    def _note_centre_near(self, document, time_ms: int) -> int:
        """The millisecond a Don/Kat structure should be centred on.

        The chart's own note when one is standing within a millisecond or two,
        its own time exactly; the snap otherwise.

        A barline or fake slider Don is the note plus the structure decorating
        it, and the two only read as one object while they share a millisecond.
        Built on the bare snap instead, a placement one millisecond off the note
        already there wrote a *second* hit object next to it -- the pair a
        millisecond apart that `_without_redundant_note` exists to prevent, which
        it could not see because it matches on the exact millisecond.
        """
        nearest = min(
            (
                note for note in document.hit_objects
                if note.is_circle and abs(note.time - time_ms) <= self.NOTE_CENTRE_TOLERANCE_MS
            ),
            key=lambda note: abs(note.time - time_ms),
            default=None,
        )
        return time_ms if nearest is None else round(nearest.time)

    def _without_redundant_note(self, document, notes: list, kind: str, time_ms: int) -> list:
        """Drop a placement's real note when the chart already has that note.

        Don and Kat in the fake slider and barline layers write a hittable note
        *and* the structure decorating it. When the normal chart already carries
        the same kind of note on that millisecond, that note is the one the
        gimmick is being drawn around: writing a second one only replaces it
        (a placement displaces what it lands on), losing whatever hitsounds,
        finisher bit and new-combo flag the mapper put there. A note of the
        *other* kind is left to be replaced, since swapping a don for a kat is a
        real edit rather than an accident.

        A circle, specifically. A drumroll's hit sound is 0, which reads as a
        don, so a Don placed on one used to write its bars and then drop its own
        note as redundant -- leaving a gimmick with nothing to hit.
        """
        if kind not in ("don", "kat"):
            return notes
        existing = next(
            (
                note for note in document.hit_objects
                if round(note.time) == time_ms and note.is_circle
            ),
            None,
        )
        if existing is None:
            return notes
        if bool(existing.hit_sound & HITSOUND_CLAP) != (kind == "kat"):
            return notes
        return [note for note in notes if self.is_fake_slider(note) or round(note.time) != time_ms]

    def _gimmick_zoom_changed(self, window_ms: float) -> None:
        """Ctrl+wheel on any layer zooms all six.

        The layers describe the same moment from different angles, so reading
        them against each other only works while they share a scale. The
        gameplay viewer is excluded on purpose: its x axis is pixels-per-beat,
        not a time window, and it renders at its own size.
        """
        for frame in self._gimmick_views:
            view = getattr(frame, "chart_view", None) or getattr(frame, "sv_view", None)
            if view is None or isinstance(view, GameplayViewerView):
                continue
            if view.window_ms != window_ms:
                view.window_ms = window_ms
                view.update()

    def _gimmick_snap_divisor(self) -> int:
        return int(self.gimmick_snap_combo.currentData())

    def _gimmick_snap_changed_by_wheel(self, divisor: int) -> None:
        """Alt+wheel in one layer moves the whole page's snap.

        The combo is the single source of truth for all six layers, so the view
        that caught the wheel hands the divisor to it and lets the combo push it
        back out -- to the other five, and harmlessly to the sender, which has
        already set it on itself.
        """
        index = self.gimmick_snap_combo.findData(divisor)
        if index >= 0:
            self.gimmick_snap_combo.setCurrentIndex(index)

    def _gimmick_snap_changed(self) -> None:
        divisor = self._gimmick_snap_divisor()
        for frame in self._gimmick_views:
            view = getattr(frame, "chart_view", None) or getattr(frame, "sv_view", None)
            if view is not None:
                view.set_snap_divisor(divisor)

    def _share_kiai_bands(self, view, sv: bool) -> None:
        """Let `view` take its kiai bands from the shared per-refresh cache.

        Without this every view runs `kiai_spans` itself, which sorts the whole
        timing point list -- six layers doing that on every placement was a
        measurable part of the 162ms it used to take to place one note. A chart
        view is handed the document and an SV view its point list, matching
        what each already passes to `set_kiai_from`.
        """
        if sv:
            view.kiai_bands_for = lambda points: self._kiai_bands(points)
        else:
            view.kiai_bands_for = lambda document: self._kiai_bands(document.timing_points)

    def _cached(self, key: str, compute):
        """Memoize a document-derived set for the length of one refresh cycle.

        A placement refreshes six layers, and each of them asks for the same
        derived sets -- which fake slider is a shiny, which milliseconds carry
        a structure's lines, where the kiai sections are. Every one of those is
        a full pass over the document, and on a gimmick map that is tens of
        thousands of timing points, six times over, twice per placement. It
        measured at 162ms to place one note, and the whole test suite runs on
        placements.

        Scoped to a refresh cycle rather than cached against the document,
        deliberately: nothing mutates the document *during* a refresh, so the
        answer cannot go stale inside the window where it is reused, and there
        is no invalidation to get wrong when an edit lands. Outside a cycle
        (`_doc_cache is None`) every call computes as it always did.
        """
        if self._doc_cache is None:
            return compute()
        if key not in self._doc_cache:
            self._doc_cache[key] = compute()
        return self._doc_cache[key]

    @contextmanager
    def _refresh_cycle(self):
        """Open the `_cached` window, re-entrantly.

        The refresh entry points call each other -- a placement runs both the
        note and the SV refresh, and the gimmick page's own refresh runs inside
        those -- so only the outermost opens and clears the cache.
        """
        if self._doc_cache is not None:
            yield
            return
        self._doc_cache = {}
        try:
            yield
        finally:
            self._doc_cache = None

    def _kiai_bands(self, document_points) -> list[tuple[int, int]]:
        return self._cached("kiai", lambda: kiai_spans(document_points, KIAI_OPEN_END_MS))

    def _refresh_gimmick_views(self) -> None:
        # Once per refresh cycle, not once per caller. An edit runs both
        # `_refresh_difficulty_views` and `_refresh_difficulty_sv_views` and
        # both end up here, so every placement rebuilt all six layers twice
        # over -- the same document, to the same result.
        if self._doc_cache is not None:
            if self._doc_cache.get("gimmick_refreshed"):
                return
            self._doc_cache["gimmick_refreshed"] = True
        with self._refresh_cycle():
            pairing = self._gimmick_pairing
            state = self._states.get(pairing.target) if pairing else None
            if state is None:
                return
            for frame in self._gimmick_views:
                # refresh_*, not load_document: a placement must not throw the
                # cursor back to the first object in all six layers.
                chart = getattr(frame, "chart_view", None)
                if chart is not None:
                    chart.refresh_notes(state.document)
                    continue
                sv = getattr(frame, "sv_view", None)
                if sv is not None:
                    sv.refresh_points(state.document)

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

    def _add_editor_view(
        self, view_type: str, difficulty_path: Path, container: QVBoxLayout | None = None,
    ) -> None:
        """Open one view. `container` overrides where the frame is placed.

        The gimmick page passes its own layer stack, so its "+" reuses this
        wiring rather than growing a second, read-only copy of it. Frames added
        there are bands like the six fixed layers, with the same compact chrome.
        """
        try:
            state = self._ensure_state(difficulty_path)
        except Exception as error:
            QMessageBox.critical(self, tr("MainWindow", "Open failed"), str(error))
            return

        band = container is not None
        label = state.document.version or difficulty_path.stem
        frame = EditorViewFrame(view_type, label, compact=band)
        frame.difficulty_path = difficulty_path
        frame.closed.connect(self._close_editor_view)

        # The layer-restricted types are ordinary chart/gameplay views with one
        # layer's filter on them, so they fall through to everything the plain
        # type already sets up rather than growing a branch each.
        chart_layer = self.CHART_ONLY_VIEWS.get(view_type)
        gameplay_layer = self.GAMEPLAY_ONLY_VIEWS.get(view_type)
        if chart_layer is not None:
            view_type = "chart"
        elif gameplay_layer is not None:
            view_type = "gameplay"
        # ...and so is the Kiai and Sound Volume layer: an unfiltered SV view,
        # differing only in which range tools its drag can be holding, which
        # every SV view now routes through _sv_range_action anyway.
        kiai_sound = view_type == "kiai_sound"
        if kiai_sound:
            view_type = "sv"

        if view_type == "chart":
            view = TimelineGameplay()
            view.set_symmetric(True)
            # Right-click-scrub target for a live drag-selection (see
            # TimeAxisMixin._press_is_on_timing_bar): the gimmick page's own
            # bar for a band added there, the Editor page's otherwise.
            view.timing_bar = self.gimmick_timing_bar if band else self.timing_bar
            if chart_layer is not None:
                self._apply_chart_layer(view, chart_layer)
                view.timing_line_edit_requested.connect(
                    lambda uid, dp=difficulty_path: self._edit_timing_line(dp, uid)
                )
                # _apply_chart_layer hands the barline layer its red lines to
                # edit, so this band offers right click and Delete on them the
                # same way the page's own layer does. Connected only here: a
                # plain chart view shows no red lines to act on.
                view.timing_lines_delete_requested.connect(
                    lambda uids, dp=difficulty_path:
                        self._delete_gimmick_objects(dp, (), uids)
                )
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
            # A layer band deletes the whole structure, the same as the gimmick
            # page's own layers -- right-clicking a fake slider in one and
            # leaving its 60000 BPM line behind is the wreckage
            # _delete_gimmick_objects exists to prevent. It also clears the
            # red-line selection, which _delete_notes does not: without that,
            # one Delete keypress produced two undo steps here.
            if chart_layer is not None:
                view.note_delete_requested.connect(
                    lambda uid, dp=difficulty_path: self._delete_gimmick_objects(dp, [uid])
                )
                view.notes_delete_requested.connect(
                    lambda uids, dp=difficulty_path: self._delete_gimmick_objects(dp, uids)
                )
            else:
                view.note_delete_requested.connect(
                    lambda uid, dp=difficulty_path: self._delete_note(dp, uid)
                )
                view.notes_delete_requested.connect(
                    lambda uids, dp=difficulty_path: self._delete_notes(dp, uids)
                )
            frame.set_content(view)
            frame.chart_view = view
            self._chart_views.append(view)
            self._share_kiai_bands(view, sv=False)
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
            # Marks the Kiai and Sound Volume view for the global SV tool row,
            # which is the one place an unfiltered SV view still has to be told
            # apart from an ordinary one (see _sv_tools_for).
            view.kiai_sound = kiai_sound
            # The Kiai and Sound Volume layer has nothing to do with SV: its
            # axis, curve and drags all read/write note volume instead.
            view.volume_mode = kiai_sound
            view.timing_bar = self.gimmick_timing_bar if band else self.timing_bar
            view.load_document(state.document)
            view.set_snap_divisor(int(self.editor_snap_combo.currentData()))
            view.current_time = state.playhead_ms
            view.window_ms = self._difficulty_window_ms(difficulty_path)
            view.zoom_changed.connect(
                lambda window_ms, dp=difficulty_path, v=view: self._zoom_changed(dp, window_ms, v)
            )
            view.seek_requested.connect(self.seek_audio)
            view.function_range_requested.connect(
                lambda start_ms, end_ms, dp=difficulty_path, v=view:
                    self._sv_range_action(dp, v.tool, start_ms, end_ms)
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
            view.timing_line_edit_requested.connect(
                lambda uid, dp=difficulty_path: self._edit_timing_line(dp, uid)
            )
            frame.set_content(view)
            frame.sv_view = view
            self._sv_views.append(view)
            self._share_kiai_bands(view, sv=True)
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
            if gameplay_layer is not None:
                view.object_filter = self._layer_object_filter(gameplay_layer)
            self._share_kiai_bands(view, sv=False)
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

        self._editor_views.append(frame)
        if container is not None:
            content = frame.content
            if isinstance(content, (TimelineGameplay, SVEditorView)):
                content.setFixedHeight(self.GIMMICK_LAYER_HEIGHT)
                # Same base snapshot and snap the six layers use: a view added
                # here that snapped against the live document would collapse its
                # grid onto the gimmick's 60000 BPM lines.
                if self._gimmick_pairing is not None:
                    content.set_base_timing(self._gimmick_pairing.base_timing)
                content.set_snap_divisor(self._gimmick_snap_divisor())
            # Above the trailing stretch, so added bands land under the layers.
            container.insertWidget(container.count() - 1, frame)
            return
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

    # Floor for every tool button in the program: the Editor page's two rows and
    # the gimmick page's six all sit in the same spot below the views, so a
    # shorter button there reads as a different, smaller control. showEvent
    # raises them all to the tallest polished hint, which is what stops a
    # descender being clipped once the window stylesheet's padding lands.
    TOOL_BUTTON_HEIGHT = 32

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
            button.setFixedHeight(self.TOOL_BUTTON_HEIGHT)
            button.setFocusPolicy(Qt.NoFocus)
            button.setChecked(tool_id == "select")
            button.toggled.connect(lambda checked, t=tool_id: self._set_active_tool(t) if checked else None)
            tool_group.addButton(button)
            self.tool_buttons[tool_id] = button
            layout.addWidget(button)

        self.new_combo_button = QPushButton(tr("MainWindow", "6. New Combo"))
        self.new_combo_button.setCheckable(True)
        self.new_combo_button.setFixedHeight(self.TOOL_BUTTON_HEIGHT)
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
        # Two sets sharing slot 1: an ordinary SV view builds green lines and
        # sweeps, the Kiai and Sound Volume view sets kiai and note volume over
        # the same drag. Both are built here and _sync_global_sv_tool_row shows
        # whichever the focused view wants, so the digits keep meaning the
        # button in the same position -- the rule the gimmick rows already use.
        tools = (
            ("1", "select", tr("MainWindow", "1. Select")),
            ("2", "green_line", tr("MainWindow", "2. Green Line")),
            ("3", "function", tr("MainWindow", "3. Function")),
            ("2", "kiai", tr("MainWindow", "2. Kiai")),
            ("3", "volume", tr("MainWindow", "3. Volume")),
        )
        for number, tool_id, label in tools:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setFixedHeight(self.TOOL_BUTTON_HEIGHT)
            button.setFocusPolicy(Qt.NoFocus)
            button.setChecked(tool_id == "select")
            button.toggled.connect(lambda checked, t=tool_id: self._set_active_sv_tool(t) if checked else None)
            tool_group.addButton(button)
            self.sv_tool_buttons[tool_id] = button
            layout.addWidget(button)

        self.SV_TOOLS_DEFAULT = ("select", "green_line", "function")
        self.SV_TOOLS_KIAI_SOUND = ("select", "kiai", "volume")
        for tool_id in self.SV_TOOLS_KIAI_SOUND[1:]:
            self.sv_tool_buttons[tool_id].setVisible(False)

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

        The bound key comes from the shortcut registry so Settings can rebind
        it; the digit passed to the handler stays the logical tool number
        regardless of which key ends up on it.

        One shortcut per *scoped* definition, not a fixed six: the Editor
        page's toolbox is `tool_1`...`tool_6`, and each gimmick layer has its
        own set derived from its own toolbox (see the registration at the foot
        of this module), because the layers do not agree on what tool 2 is or
        even on how many tools there are.
        """
        self.tool_shortcuts: dict[str, QShortcut] = {}
        self._tool_shortcuts_by_scope: dict[str, list[QShortcut]] = {}
        # Set before any toolbox exists, so the first refresh has an answer.
        self._tool_shortcuts_allowed = True
        for definition in self.shortcuts.definitions.values():
            # A scope is what marks a definition as a toolbox binding; the
            # global actions each have their own QShortcut already.
            if not definition.scope:
                continue
            digit = definition.action_id.rsplit("_", 1)[-1]
            shortcut = QShortcut(QKeySequence(self.shortcuts.sequence(definition.action_id)), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(lambda d=digit: self._activate_tool_digit(d))
            self.tool_shortcuts[definition.action_id] = shortcut
            self._tool_shortcuts_by_scope.setdefault(definition.scope, []).append(shortcut)
        self._refresh_tool_shortcut_scope()

    def _set_tool_shortcuts_enabled(self, enabled: bool) -> None:
        self._tool_shortcuts_allowed = enabled
        self._refresh_tool_shortcut_scope()

    def _refresh_tool_shortcut_scope(self) -> None:
        """Leave only the reachable toolbox's keys enabled.

        Every toolbox numbers its tools from 1, so seven of them want the key
        "1" -- which is correct, since only one toolbox is reachable at a
        time. Qt does not see it that way: two enabled ApplicationShortcuts on
        one key fire `activatedAmbiguously` and *neither* acts, so the digit
        has to belong to exactly one toolbox at any moment. The focused
        gimmick layer's while that page is up, the Editor page's otherwise,
        and none of them while a text widget has focus (see
        `_editor_view_focus_changed`: an ApplicationShortcut consumes its key,
        so the only way a digit reaches a search box is for no tool shortcut
        to be enabled).

        getattr throughout: focusChanged and _show_page both run before the
        shortcuts and the pages exist.
        """
        by_scope = getattr(self, "_tool_shortcuts_by_scope", None)
        if not by_scope:
            return
        stack = getattr(self, "page_stack", None)
        if not self._tool_shortcuts_allowed:
            active = ""
        elif stack is not None and stack.currentIndex() == PAGE_GIMMICK:
            active = getattr(self, "_active_gimmick_layer", None) or ""
        else:
            active = "editor"
        for scope, shortcuts in by_scope.items():
            for shortcut in shortcuts:
                shortcut.setEnabled(scope == active)

    def _activate_tool_digit(self, digit: str) -> None:
        if should_ignore_shortcut_focus(QApplication.focusWidget()):
            return
        note_tools = {"1": "select", "2": "don", "3": "kat", "4": "slider", "5": "spinner"}
        sv_tools = dict(zip("123", self._sv_tools_for(self._active_sv_view)))
        # The gimmick page has its own row and its own meanings for 1-4, and it
        # is checked first: its row is always visible while that page is up,
        # where the Editor page's two rows are only visible on theirs.
        if self.page_stack.currentIndex() == PAGE_GIMMICK:
            # The digit is the button's position in the focused layer's own
            # toolbox, so 2 is a fake slider in one layer and a barline note in
            # the next -- the same relationship the buttons themselves have.
            layer = getattr(self, "_active_gimmick_layer", None)
            tools = self.GIMMICK_TOOLSETS.get(layer or "", ())
            index = int(digit) - 1
            if 0 <= index < len(tools):
                self.gimmick_tool_buttons[layer][tools[index][0]].setChecked(True)
            return
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
        # A gimmick layer owns its own toolbox rather than the Editor page's:
        # the same button means a different structure there.
        # Moving to a *different* view always arrives on Select. A tool is a
        # mode, and carrying one across a view boundary meant the first click in
        # the view you just moved to placed something instead of selecting it --
        # in the gimmick page, a whole barline structure. Same view again (after
        # a modal dialog, say) is not a move, so an active tool survives that.
        moved = new is not self._last_focused_editor_view

        if getattr(new, "gimmick_layer", None) is not None:
            if isinstance(new, SVEditorView):
                self._active_sv_view = new
            else:
                self._active_chart_view = new
            self._show_gimmick_row_for(new)
            if moved:
                self._last_focused_editor_view = new
                self.gimmick_tool_buttons[new.gimmick_layer]["select"].setChecked(True)
            return

        if isinstance(new, TimelineGameplay) and new.symmetric:
            self._active_chart_view = new
            if moved:
                new.set_tool("select")
                new.set_new_combo(False)
            self._last_focused_editor_view = new
            self._sync_global_tool_row()
            self.global_tool_row.setVisible(True)
            self.global_sv_tool_row.setVisible(False)
        elif isinstance(new, SVEditorView):
            self._active_sv_view = new
            if moved:
                new.set_tool("select")
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

    def _sv_tools_for(self, view) -> tuple[str, ...]:
        """Which of the two SV tool sets `view` is driven by."""
        if getattr(view, "kiai_sound", False):
            return self.SV_TOOLS_KIAI_SOUND
        return self.SV_TOOLS_DEFAULT

    def _sync_global_sv_tool_row(self) -> None:
        view = self._active_sv_view
        enabled = view is not None and view.isEnabled()
        self.global_sv_tool_row.setEnabled(enabled)
        if view is None:
            return
        wanted = self._sv_tools_for(view)
        for tool_id, button in self.sv_tool_buttons.items():
            button.setVisible(tool_id in wanted)
        button = self.sv_tool_buttons.get(view.tool)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)

    def _difficulty_for_view(self, view) -> Path | None:
        """Which difficulty a chart/SV view belongs to.

        The gimmick page's six layers are searched too. They were not, which is
        the whole reason paste did nothing there: the lookup returned None, the
        state lookup that follows returned None, and the paste handler bailed
        without a word.
        """
        for frame in [*self._editor_views, *self._gimmick_views]:
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
        # One pair per page that edits. WidgetWithChildrenShortcut only fires
        # for descendants of the widget it is installed on, and the gimmick page
        # is a sibling of the Editor page rather than a child -- so its six
        # layers never saw the copy/paste keys at all.
        self.copy_shortcut = QShortcut(QKeySequence(self.shortcuts.sequence("copy")), self.editor_page)
        self.copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self.copy_selection)
        self.paste_shortcut = QShortcut(QKeySequence(self.shortcuts.sequence("paste")), self.editor_page)
        self.paste_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.paste_shortcut.activated.connect(self.paste_clipboard)
        self.gimmick_copy_shortcut = QShortcut(QKeySequence(self.shortcuts.sequence("copy")), self.gimmick_page)
        self.gimmick_copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.gimmick_copy_shortcut.activated.connect(self.copy_selection)
        self.gimmick_paste_shortcut = QShortcut(QKeySequence(self.shortcuts.sequence("paste")), self.gimmick_page)
        self.gimmick_paste_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.gimmick_paste_shortcut.activated.connect(self.paste_clipboard)

    def _clipboard_target(self) -> str:
        """"sv" or "chart" -- which editor the copy/paste keys act on.

        Neither Editor-page tool row is visible on the gimmick page, so asking
        them there always answered "chart" -- which is why Ctrl+C in one of the
        three SV layers copied notes. There the focused layer says which it is.
        """
        if self.page_stack.currentIndex() == PAGE_GIMMICK:
            layer = getattr(self, "_active_gimmick_layer", "") or ""
            return "sv" if layer.startswith("sv_") else "chart"
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
        """Copy a chart view's selection: its notes *and* the red lines it owns.

        Both, because in the gimmick page they are one thing. A fake slider
        without its 60000 BPM line is an ordinary drumroll, and the barline
        layer has no notes at all -- its selection is nothing but red lines, so
        a notes-only clipboard made Ctrl+C there a no-op.
        """
        view = self._active_chart_view
        if view is None:
            return
        notes = view.selected_notes()
        lines = [
            point for point in view.timing_points
            if point.uid in view.selected_timing_uids
        ]
        if not notes and not lines:
            return
        # Grown into the whole structure first, the same way a drag and a delete
        # are. The fake slider layer never selects red lines -- it does not own
        # them -- so without this its Ctrl+C copied the slider and left the
        # 60000 BPM line that makes it fake behind.
        state = self._states.get(self._difficulty_for_view(view))
        if state is not None and getattr(view, "gimmick_layer", None) is not None:
            notes, lines = self._expand_move(
                state, {note.uid for note in notes}, {point.uid for point in lines},
            )
            # Red lines only, the same rule the delete path follows: a green
            # line is the SV layers' description of the chart's scroll speed at
            # a millisecond, not part of the object standing on it, and pasting
            # copies of it elsewhere is not what copying a slider asked for.
            lines = [point for point in lines if point.uninherited]
        base = min(
            [note.time for note in notes] + [point.time for point in lines]
        )
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
        self._timing_clipboard = [
            {
                "offset": point.time - base,
                "beat_length": point.beat_length, "meter": point.meter,
                "sample_set": point.sample_set, "sample_index": point.sample_index,
                "volume": point.volume, "effects": point.effects,
                "uninherited_flag": 1 if point.uninherited else 0,
            }
            for point in lines
        ]
        self.status.setText(
            tr("MainWindow", "Copied {count} notes.").format(count=len(notes) + len(lines))
        )

    def _paste_notes(self) -> None:
        view = self._active_chart_view
        if view is None or not (self._note_clipboard or self._timing_clipboard):
            return
        difficulty_path = self._difficulty_for_view(view)
        state = self._states.get(difficulty_path) if difficulty_path is not None else None
        if state is None:
            return
        # Snap the paste anchor to the grid. The playhead is wherever audio
        # happens to be, which is almost never exactly on a division, so
        # pasting at the raw position landed a copied pattern a few
        # milliseconds off every snap it was built on. `snap_points` rather
        # than the document's own timing: in a gimmick layer that is the base
        # snapshot, and the document's 60000 BPM lines have no usable grid.
        base = round(max(0.0, snap_time(view.snap_points, view.current_time, view.snap_divisor)))
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
        # Never a second uninherited point on a millisecond that already has
        # one -- the same rule the barline generator follows, for the same
        # reason: only the first is meaningful and the rest are invisible.
        taken = {round(p.time) for p in state.document.timing_points if p.uninherited}
        lines = [
            TimingPoint(
                time=float(base + entry["offset"]), beat_length=entry["beat_length"],
                meter=entry["meter"], sample_set=entry["sample_set"],
                sample_index=entry["sample_index"], volume=entry["volume"],
                uninherited_flag=entry["uninherited_flag"], effects=entry["effects"],
            )
            for entry in self._timing_clipboard
            if not entry["uninherited_flag"] or base + entry["offset"] not in taken
        ]
        commands = []
        if pasted:
            displaced = [
                victim
                for note in pasted
                for victim in self._notes_blocking(state.document, note.time, note.is_spinner)
            ]
            if displaced:
                commands.append(RemoveHitObjects(displaced))
            commands.append(InsertHitObjects(pasted))
        if lines:
            commands.append(InsertTimingPoints(lines))
        if not commands:
            return
        # One undo step for the whole paste, structures included.
        state.history.push(CompositeCommand(commands, "paste_notes"), state)
        with self._refresh_cycle():
            self._refresh_difficulty_views(difficulty_path)
            self._refresh_difficulty_sv_views(difficulty_path)
        self.status.setText(
            tr("MainWindow", "Pasted {count} notes.").format(count=len(pasted) + len(lines))
        )

    def _copy_sv_points(self) -> None:
        view = self._active_sv_view
        if view is None:
            return
        points = view.selected_points()
        if not points:
            return
        base = points[0].time
        # `offset` is what the millisecond-delta paste (the Editor page's own
        # SV view) uses; the list's own order is what the index-mapped paste
        # (a gimmick SV layer) walks instead, since `selected_points` is
        # already sorted by time -- see `_paste_sv_points` on why the two
        # views need different targets and both need the same clipboard.
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
        """Paste the SV clipboard, by millisecond delta or by object index.

        A gimmick SV layer owns a scattered set of milliseconds -- its
        structures' own, not a beat grid (`_sv_layer_times`) -- so a paste
        built from millisecond deltas off a snapped base routinely lands where
        the layer cannot see it: four green lines copied off four fake
        sliders have nowhere near four fake sliders exactly `offset` apart to
        land on. Mapped by index there instead: walk the layer's own owned
        milliseconds forward from the cursor and drop the clipboard values
        onto them in the order they were copied.

        The Editor page's standalone SV view owns every millisecond there is,
        so it keeps the old delta behaviour -- there is nothing to map onto
        that isn't already reachable by offset.
        """
        view = self._active_sv_view
        if view is None or not self._sv_clipboard:
            return
        difficulty_path = self._difficulty_for_view(view)
        state = self._states.get(difficulty_path) if difficulty_path is not None else None
        if state is None:
            return

        layer_id = getattr(view, "gimmick_layer", None)
        if layer_id is not None:
            owned = sorted(self._sv_layer_times(layer_id, state.document) or ())
            targets = [at for at in owned if at >= view.current_time - 0.001]
            if not targets:
                self.show_toast(tr(
                    "MainWindow", "No milliseconds after the cursor in this layer to paste onto.",
                ))
                return
            count = min(len(targets), len(self._sv_clipboard))
            pasted = [
                TimingPoint(
                    time=float(targets[index]), beat_length=entry["beat_length"],
                    meter=entry["meter"], sample_set=entry["sample_set"],
                    sample_index=entry["sample_index"], volume=entry["volume"],
                    uninherited_flag=0, effects=entry["effects"],
                )
                for index, entry in enumerate(self._sv_clipboard[:count])
            ]
            self._insert_sv_points(state, pasted, "paste_sv")
            self._refresh_difficulty_sv_views(difficulty_path)
            if count < len(self._sv_clipboard):
                self.status.setText(tr(
                    "MainWindow",
                    "Pasted {count} of {total} SV points -- this layer had no more "
                    "milliseconds of its own after the cursor.",
                ).format(count=count, total=len(self._sv_clipboard)))
            else:
                self.status.setText(
                    tr("MainWindow", "Pasted {count} SV points.").format(count=count)
                )
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
        slider_multiplier: float = SLIDER_MULTIPLIER_ASSUMED,
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
            length = slider_length_for_duration(
                duration, timing.beat_length, sv, slider_multiplier,
            )
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
            state.document.slider_multiplier,
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
            length = slider_length_for_duration(
                duration, timing.beat_length, sv, state.document.slider_multiplier,
            )
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
        with self._refresh_cycle():
            state = self._states.get(difficulty_path)
            if state is None:
                return
            # Placing or deleting a note must make it audible without reopening the
            # map, but only the difficulty actually being played is scheduled --
            # editing a sibling in another view changes nothing you can hear.
            if self.state is state:
                self.hitsounds.set_schedule(state.document.hit_objects)
            for frame in self._editor_views:
                if getattr(frame, "difficulty_path", None) != difficulty_path:
                    continue
                chart_view = getattr(frame, "chart_view", None)
                if chart_view is not None:
                    chart_view.refresh_notes(state.document)
                gameplay_view = getattr(frame, "gameplay_view", None)
                if gameplay_view is not None:
                    gameplay_view.refresh_notes(state.document)
            # The gimmick layers show a difficulty like any other view does, and
            # every path that edits it -- undo, a right-click delete, a BPM retype --
            # arrives here. Refreshing them anywhere but here left one of those
            # paths behind each time.
            pairing = self._gimmick_pairing
            if pairing is not None and pairing.target == difficulty_path:
                self._refresh_gimmick_views()
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
        with self._refresh_cycle():
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

    def _open_sv_function_dialog(
        self, difficulty_path: Path, start_ms: float, end_ms: float, layer_id: str | None = None,
    ) -> None:
        state = self._states.get(difficulty_path)
        points = state.document.timing_points if state is not None else []
        divisor = (
            self._gimmick_snap_divisor() if layer_id is not None
            else int(self.editor_snap_combo.currentData())
        )
        # Prefill with the SV already in force at each end of the selection, so
        # "Generate" with nothing changed is a no-op rather than a jump to some
        # arbitrary 1.0x -> 2.0x ramp.
        dialog = SVFunctionDialog(
            start_ms, end_ms, self, snap_divisor=divisor,
            initial_rate=sv_at(points, start_ms), final_rate=sv_at(points, end_ms),
            gimmick_layer=layer_id,
            position_offset=(
                self._gimmick_config(layer_id).sv_offset_ms if layer_id is not None else None
            ),
            # Layer 6's BPM filter defaults to the BPM in force where the drag
            # started, which only the base snapshot can answer honestly.
            base_timing=(
                self._gimmick_pairing.base_timing
                if layer_id == "sv_barline" and self._gimmick_pairing is not None else None
            ),
        )
        if dialog.exec() != QDialog.Accepted:
            return
        params = dialog.parameters()
        if layer_id is not None:
            # Written back before generating, because the layer decides which
            # milliseconds it owns from this number -- generate first and the
            # sweep would land where the layer is not yet looking.
            self._gimmick_config(layer_id).sv_offset_ms = params["position_offset"]
        self._generate_sv(difficulty_path, start_ms, end_ms, params, layer_id=layer_id)

    def _open_volume_function_dialog(
        self, difficulty_path: Path, start_ms: float, end_ms: float,
    ) -> None:
        """The Kiai and Sound Volume layer's Volume tool: the SV generator's
        easing curves aimed at hitsound volume, editing existing timing
        points rather than placing new ones (see _generate_volume).

        No `layer_id` is passed on -- there is no note/snap placement mode to
        decide between any more, since nothing is placed. SVFunctionDialog
        hides "Generate at" and the other placement-only rows in volume mode.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        ordered = sorted_by_time(state.document.timing_points)
        # Prefilled with the volume already in force at each end, so Generate
        # with nothing changed is a no-op -- same reasoning as the SV dialog.
        initial = active_point_at(ordered, start_ms)
        final = active_point_at(ordered, end_ms)
        dialog = SVFunctionDialog(
            start_ms, end_ms, self, snap_divisor=self._gimmick_snap_divisor(),
            initial_rate=initial.volume if initial else 100,
            final_rate=final.volume if final else 100,
            volume=True,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self._generate_sv(
            difficulty_path, start_ms, end_ms, dialog.parameters(), volume=True,
        )

    def _sv_generation_times(
        self, state: DifficultyState, start_ms: float, end_ms: float, params: dict,
        layer_id: str | None = None,
    ) -> list[float]:
        """Where the generated SV points go, before the position offset.

        "Each note" (the default) puts one point on each hit object inside the
        selected range and nowhere else -- an SV sweep only has to be correct
        where a note actually is, and a point every 20ms between them was
        hundreds of timing lines nothing could see. "Every snap" walks the
        beat grid at the chosen divisor instead, for sweeps that have to move
        continuously.

        `layer_id` overrides both modes with the layer's own objects -- real
        notes for layer 4, fake sliders for 5, red lines for 6. Checked before
        the placement mode, not after: "every snap" walks the beat grid, which
        lands between a gimmick's objects, and a point there is both invisible
        in the layer that made it and reset to 1.0x by the next uninherited
        line. A gimmick SV layer only has one honest answer to "where", so it
        is not asked (SVFunctionDialog hides the choice).

        `params["include_shiny"]` adds shiny milliseconds to layer 5's targets
        for this one sweep only -- the layer's *ownership* is unchanged
        (`_sv_layer_object_times` still excludes them, and a hand-placed or
        generated shiny line outside a sweep is still layer 4's to show), this
        just lets one Generate run reach both at once instead of two runs.

        `params["only_red_line_bpm"]` (layer 6 only, None when off) is an
        inclusive (low, high) BPM interval; every position whose red line
        falls outside it is dropped, so a sweep can drive one 60000 BPM
        barline run -- or a whole family of them, "500-1000 BPM" -- and leave
        the chart's own timing lines, which share the layer, untouched. An
        exact single BPM is simply low == high.
        """
        if layer_id is not None:
            # The objects themselves, unshifted: `_generate_sv` applies the
            # layer's offset as the position offset, and applying it here too
            # would double it.
            times = set(self._sv_layer_object_times(layer_id, state.document) or ())
            if layer_id == "sv_fake_slider" and params.get("include_shiny"):
                times |= self._shiny_times(state.document)
            selected = sorted(
                float(time_ms) for time_ms in times
                if start_ms - 0.001 <= time_ms <= end_ms + 0.001
            )
            wanted_bpm = params.get("only_red_line_bpm")
            if wanted_bpm is None:
                return selected
            low_bpm, high_bpm = wanted_bpm
            # Layer 6's BPM filter. Keyed on the exact millisecond first: these
            # times *are* red-line milliseconds, rounded, and a line at 1234.6
            # rounds to 1235, where active_uninherited_at would answer with the
            # line before it instead. The walk-back is the fallback for a
            # position that is not itself a red line, where the governing one
            # is the only sensible answer.
            reds = {
                round(point.time): point.bpm
                for point in sorted_by_time(state.document.timing_points)
                if point.uninherited
            }
            return [
                at for at in selected
                if bpm_in_range(
                    reds[round(at)] if round(at) in reds
                    else active_uninherited_at(state.document.timing_points, at).bpm,
                    low_bpm, high_bpm,
                )
            ]
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

    def _generate_sv(
        self, difficulty_path: Path, start_ms: float, end_ms: float, params: dict,
        layer_id: str | None = None, volume: bool = False,
    ) -> None:
        """Fill a range with generated inherited points.

        `volume` no longer reuses this machinery -- see _generate_volume. The
        Kiai and Sound Volume layer's Volume tool edits existing timing
        points' own volume in place; it must never insert a green line, which
        is exactly what this path does. This function is SV-only now.
        """
        state = self._states.get(difficulty_path)
        if state is None:
            return
        if volume:
            self._generate_volume(state, difficulty_path, start_ms, end_ms, params)
            return
        ordered = sorted_by_time(state.document.timing_points)
        start_bpm = active_uninherited_at(state.document.timing_points, start_ms).bpm or 1.0

        times = self._sv_generation_times(state, start_ms, end_ms, params, layer_id)
        if not times:
            self.show_toast(tr("MainWindow", "No notes in the selected range to generate SV on."))
            return

        # Progress runs across the points that are actually generated, not
        # across the dragged range. With note placement the last note is
        # usually well before where the drag ended, so normalising by the
        # dragged duration meant the final point never reached the requested
        # final rate -- the one value a user picking "1.0x to 2.5x" cares most
        # about getting exactly.
        span = times[-1] - times[0]
        offset = params.get("position_offset", SVFunctionDialog.DEFAULT_POSITION_OFFSET_MS)
        # Oscillation is indexed, not timed: it alternates on every *point*, so
        # unlike a sweep its shape cannot be read off elapsed time. Built up
        # front for the whole run, then indexed alongside it.
        oscillate = params.get("oscillate", "")
        wobble = oscillating_series(
            params["initial_rate"],
            abs(params["final_rate"] - params["initial_rate"]),
            len(times),
            lambda t: sv_ease(params["function"], t),
            per_pair=oscillate == "pair",
        ) if oscillate else []
        points = []
        for index, source_time in enumerate(times):
            # Measured from where the point *belongs*, not from where the
            # offset moves it, so a -5ms lead-in doesn't skew the curve.
            t = 0.0 if span <= 0 else (source_time - times[0]) / span
            time_ms = round(source_time + offset)
            if wobble:
                rate = wobble[index]
            else:
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
                # The same point kiai comes from. Sweeping across a volume
                # change used to copy the range's opening volume onto every
                # generated line, silently undoing the change.
                template=active,
            ))

        # One undo step regardless of point count, and regardless of how many
        # existing green lines the sweep replaces.
        self._insert_sv_points(state, points, "generate_sv")
        self._refresh_difficulty_sv_views(difficulty_path)

    def _generate_volume(
        self, state: DifficultyState, difficulty_path: Path, start_ms: float, end_ms: float,
        params: dict,
    ) -> None:
        """The Kiai and Sound Volume layer's Volume tool.

        Edits the volume already on every existing timing point (red and
        green alike) inside the range -- it never inserts one. Position
        offset, "omit first barline", "relative to final BPM" and kiai
        preservation all describe a point being *created*, so none of them
        apply here; SVFunctionDialog hides those rows in volume mode.
        """
        ordered = sorted_by_time(state.document.timing_points)
        targets = [
            point for point in ordered
            if round(start_ms) <= round(point.time) <= round(end_ms)
        ]
        if not targets:
            self.show_toast(tr("MainWindow", "No timing point in the selected range."))
            return

        times = [point.time for point in targets]
        span = times[-1] - times[0]
        oscillate = params.get("oscillate", "")
        wobble = oscillating_series(
            params["initial_rate"],
            abs(params["final_rate"] - params["initial_rate"]),
            len(times),
            lambda t: sv_ease(params["function"], t),
            per_pair=oscillate == "pair",
        ) if oscillate else []

        commands = []
        for index, point in enumerate(targets):
            t = 0.0 if span <= 0 else (point.time - times[0]) / span
            if wobble:
                rate = wobble[index]
            else:
                eased = sv_ease(params["function"], t)
                rate = params["initial_rate"] + (params["final_rate"] - params["initial_rate"]) * eased
            new_volume = max(0, min(100, round(rate)))
            if new_volume == point.volume:
                continue
            commands.append(EditTimingPoint(point.uid, {"volume": (point.volume, new_volume)}))

        if not commands:
            return
        state.history.push(CompositeCommand(commands, "generate_volume"), state)
        self._refresh_difficulty_sv_views(difficulty_path)

    def _refresh_difficulty_sv_views(self, difficulty_path: Path) -> None:
        """Re-read timing points into every open SV view of this difficulty after an edit."""
        with self._refresh_cycle():
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
            # The gimmick layers again -- see _refresh_difficulty_views. A timing
            # edit is the one they care about most: red lines *are* the barline
            # layer's content, and the SV layers are nothing else.
            pairing = self._gimmick_pairing
            if pairing is not None and pairing.target == difficulty_path:
                self._refresh_gimmick_views()
            if self.state is state:
                self._reload_timing_bars(state)

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
        # A gimmick layer lives in the gimmick page's own stack, not in an
        # Editor-page difficulty group -- and the two can name the same
        # difficulty, so looking one up by path would prune a group this frame
        # was never in.
        group_layout = (
            None if getattr(frame, "gimmick_layer", None) is not None
            else self._editor_view_groups.get(difficulty_path) if difficulty_path is not None
            else None
        )
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
        # heights=True: TOOL_BUTTON_HEIGHT is a floor, and the polished hint is
        # what a label actually needs -- typed at 32 the descenders were clipped.
        equalize_button_widths(
            [*self.tool_buttons.values(), self.new_combo_button, *self.sv_tool_buttons.values()],
            heights=True,
        )
        # The gimmick rows are sized as their own set rather than folded into
        # the call above: "2. Fake Slider" is the widest label in the program,
        # and sharing one width would stretch the Editor page's row to match it.
        # Heights only -- see equalize_button_widths on why this row cannot
        # afford a uniform width any more.
        equalize_button_widths(self._gimmick_row_buttons, heights=True, widths=False)
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
        # No typed width. 38px was narrower than the window stylesheet's own
        # 16px of padding either side left room for, so the button covered its
        # keyboard glyph; AlignLeft below already keeps it to its own hint,
        # which is recomputed once that stylesheet lands.
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
        self.hitsounds.set_schedule(state.document.hit_objects)
        self.timeline.set_snap_divisor(int(self.snap_combo.currentData()))
        self._reload_timing_bars(state)
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
        self.seek_audio(state.playhead_ms)

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

        The gimmick page comes first and does not consult focus at all: it edits
        exactly one difficulty, its layers deliberately never become the
        Editor page's focused view (they own their own toolbox), and without
        this Ctrl+Z there undid whatever the Editor page had last touched.
        """
        if self.page_stack.currentIndex() == PAGE_GIMMICK and self._gimmick_pairing is not None:
            gimmick_state = self._states.get(self._gimmick_pairing.target)
            if gimmick_state is not None:
                return gimmick_state

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
        with self._refresh_cycle():
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
        # Re-anchor from *our own* playhead, sampled before the rate changes --
        # never from self.player.position(). That value is whole milliseconds
        # (seek_audio deliberately keeps the real one fractional), it is stale
        # by up to one backend report, it is 0 whenever no media loaded or the
        # backend has not reported yet, and reading it *after* setPlaybackRate
        # means whatever the backend extrapolates into it is computed with the
        # new rate against playback still running at the old one. Every one of
        # those fired on the speed-button click, which is why 25/50/75% looked
        # wrong while 100% -- the startup default nobody ever clicks -- did not.
        # The clock restarts immediately after the sample and before the rate
        # is applied, so the handover is continuous: position up to here at the
        # old rate, everything after it at the new one.
        # The *source* clock needs the same handover, and for the same reason.
        # _source_clock_position extrapolates the last backend report forward
        # by elapsed real time * playbackRate(), so leaving it alone here would
        # replay the interval since that report at the new rate. Reports land
        # tens of milliseconds apart, so at 0.25x that mis-extrapolation is
        # comfortably past POSITION_ALLOWABLE_ERROR_MS -- the interpolated
        # clock would then *snap* to a wrong source on the very next frame,
        # which is the speed-button jump this scheme exists to remove. Rebase
        # it on the old rate first, then let the new one run from there.
        rate=float(rate)
        # Hitsound offset is wall time; the window it shifts is song time.
        self.hitsounds.playback_rate=rate
        self.latest_audio_position=self._source_clock_position()
        self._source_report_clock.restart()
        self.audio_anchor_position=self._predicted_audio_position();self.audio_anchor_clock.restart()
        self._last_predicted_position=self.audio_anchor_position
        self.player.setPlaybackRate(rate)
        for button in (
            getattr(self,"playback_speed_buttons",[])
            + getattr(self,"editor_playback_speed_buttons",[])
            + getattr(self,"gimmick_playback_speed_buttons",[])
        ):
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
            if hasattr(self,"gimmick_play_button"):self.gimmick_play_button.setText("▶")
        else:
            # The anchor is already the playhead, to the fraction of a
            # millisecond seek_audio kept there on purpose; self.player.position()
            # would round it off, or hand back 0 before the backend has ever
            # reported. So: leave it alone, and restart the clock *after*
            # play() rather than before it, because the backend produces no
            # audio while play() is still setting itself up and counting that
            # setup as elapsed song time is what put the playhead ahead of the
            # music for the first second of every resume.
            self.timeline.is_playing=True
            self.player.play()
            self.audio_anchor_clock.restart()
            self._source_report_clock.restart()
            self._last_predicted_position=self.audio_anchor_position
            self.play_button.setText(tr("MainWindow", "Pause"))
            if hasattr(self,"timeline_play_button"):self.timeline_play_button.setText("❚❚")
            if hasattr(self,"editor_play_button"):self.editor_play_button.setText("❚❚")
            if hasattr(self,"gimmick_play_button"):self.gimmick_play_button.setText("❚❚")

    def seek_audio(self, position: float) -> None:
        """Move the shared playhead. `position` is a float on purpose.

        A snap is rarely a whole millisecond, and the audio backend only takes
        whole ones -- so the exact position is kept here and broadcast to the
        views, and only the player is handed the rounded one. Rounding it for
        everybody put every view up to half a millisecond off its own grid,
        which is a third of the screen at the 20ms zoom floor.
        """
        position=max(0.0,float(position)); self.audio_anchor_position=position; self.latest_audio_position=position
        self.audio_anchor_clock.restart(); self._source_report_clock.restart(); self._last_predicted_position=position
        # Before anything else: a jump forward would otherwise fire every note
        # it skipped in one frame, and a jump backwards would replay them.
        self.hitsounds.reset_to(position)
        self.player.setPosition(round(position))
        # Every chart view, not just the shared deck timeline: on the gimmick
        # page the six layers are chart views, and a seek that reached only
        # some of them left them disagreeing about where "now" is until the
        # next frame -- which at deep zoom is a visible jump between bands.
        for view in self._chart_views:
            view.set_time(position,force=True)
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

    def _source_clock_position(self) -> float:
        """osu!'s framedSourceClock: the last backend report, extrapolated
        forward by elapsed real time * rate while playing.

        This is the value _advance_interpolated_clock blends the displayed
        playhead toward every frame, rather than only at the report itself --
        reports land tens of milliseconds apart, which is far too sparse to
        drive a per-frame display directly.
        """
        position = float(self.latest_audio_position)
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            position += self._source_report_clock.nsecsElapsed() / 1_000_000.0 * self.player.playbackRate()
        return position

    def _advance_interpolated_clock(self, frame_elapsed_ms: float) -> None:
        """One frame of osu!(lazer)'s InterpolatingFramedClock.ProcessFrame.

        Extrapolate the displayed clock by real elapsed time, then either
        snap it to the source clock (drifted more than
        POSITION_ALLOWABLE_ERROR_MS, or not currently playing) or blend
        1/POSITION_INTERPOLATION_DIVISOR of the remaining gap in -- every
        frame, not just at the report. The monotonic clamp is mandatory: a
        blended *negative* error would otherwise step the displayed playhead
        backwards, which is the visible bug this whole scheme replaces. An
        explicit reset (seek_audio, toggle_playback) is the only legitimate
        way to move it backwards, and those reset audio_anchor_position
        directly rather than going through this method.
        """
        last_interpolated = self.audio_anchor_position
        running = self.player.playbackState() == QMediaPlayer.PlayingState
        rate = self.player.playbackRate()
        source = self._source_clock_position()

        interpolated = last_interpolated + (frame_elapsed_ms * rate if running else 0.0)
        if not running or abs(interpolated - source) > POSITION_ALLOWABLE_ERROR_MS:
            interpolated = source
        else:
            interpolated += (source - interpolated) / POSITION_INTERPOLATION_DIVISOR
            if rate >= 0:
                interpolated = max(last_interpolated, interpolated)
            else:
                interpolated = min(last_interpolated, interpolated)
        self.audio_anchor_position = interpolated
        self.audio_anchor_clock.restart()

    def _predicted_audio_position(self) -> float:
        """The interpolated clock, plus sub-frame extrapolation for a caller
        that runs between rendered frames.

        Nanosecond precision keeps the extrapolation error under a
        microsecond regardless of rate (integer-millisecond elapsed()
        truncation used to compound into `rate` ms of error). Monotonic for
        the same reason _advance_interpolated_clock is: a caller between
        frames must never see the playhead step backwards either.
        """
        position = float(self.audio_anchor_position)
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            rate = self.player.playbackRate()
            position += self.audio_anchor_clock.nsecsElapsed() / 1_000_000.0 * rate
            position = max(self._last_predicted_position, position) if rate >= 0 else min(
                self._last_predicted_position, position
            )
        self._last_predicted_position = position
        return position

    def _player_position_changed(self, position: int) -> None:
        """Adopt a fresh backend report as the source clock's new base.

        No blending happens here any more -- that is
        _advance_interpolated_clock's job, run once per rendered frame
        against whatever _source_clock_position() (built from what this
        method stores) currently says. This is deliberately the only place
        that touches `latest_audio_position` and its report clock.
        """
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            # Qt reports whole milliseconds, so its echo of a seek we just made
            # would round the fractional playhead away again. Only a real move
            # -- someone dragging the media control, a backend jump -- is worth
            # adopting while paused.
            if abs(position - self.latest_audio_position) >= 1.0:
                self.latest_audio_position = float(position)
                self._source_report_clock.restart()
                self.audio_anchor_position = float(position)
                self.audio_anchor_clock.restart()
                self._last_predicted_position = float(position)
            return
        self.latest_audio_position = float(position)
        self._source_report_clock.restart()

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

        # osu!'s InterpolatingFramedClock advances once per rendered frame,
        # by that frame's own real elapsed time -- not the fixed interval
        # above, so a render that fires a little late advances by how late
        # it actually was rather than by a nominal amount.
        frame_elapsed_ms = max(0.0, (now_ns - self._last_frame_ns) / 1_000_000.0)
        self._last_frame_ns = now_ns
        self._advance_interpolated_clock(frame_elapsed_ms)

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
        # Sound whatever the playhead just crossed. Only while actually
        # playing: scrubbing and editing move the position too, and firing
        # there would machine-gun the whole section under the cursor.
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.hitsounds.advance(position)
        else:
            self.hitsounds.reset_to(position)
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
            text=f"{format_time(position)}   {percent:.1f}%"
            self.editor_timeline_strip.setText(text)
            self.gimmick_timeline_strip.setText(text)

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
                self._discard_acknowledged.pop(state.source_path, None)
            except PermissionError as error:
                # The one failure whose message reads as nonsense on its own:
                # the file is plainly there, and Windows only refuses the
                # replace because something else is holding it open -- osu!
                # with the map loaded, which is exactly the gimmick loop.
                errors.append(
                    f"{state.source_path.name}: {error}\n"
                    + tr("MainWindow", "Close the beatmap in osu! (or any editor holding it) and save again.")
                )
            except Exception as error:
                errors.append(f"{state.source_path.name}: {type(error).__name__}: {error}")
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
                # .get, not []: same reason as save_all_states -- a note
                # restored by undoing a delete has lost its applied_positions
                # entry, and failing the whole export for that is far worse
                # than writing the note where it already is.
                note.x, note.y = self.applied_positions.get(
                    note.original_index, (note.x, note.y)
                )
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
            tr("MainWindow", "Export Applied Map"),
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
                note.x, note.y = self.applied_positions.get(
                    note.original_index, (note.x, note.y)
                )

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


# Every gimmick layer's toolbox gets its own numbered keys, derived from the
# tables above rather than restated: adding a layer to GIMMICK_LAYERS and
# GIMMICK_TOOLSETS is all it takes for that layer's tools to turn up in
# Settings with their own group header. settings.py cannot reach in for them
# (gui.py imports settings, not the other way round), so the tables are
# handed over instead.
#
# The scope is the layer id, which is what stops the seven toolboxes that all
# start at "1" being reported as conflicts -- see `duplicate_shortcuts`.
register_shortcut_definitions(
    ShortcutDefinition(
        f"tool_{layer_id}_{index}",
        f"Tool {index}: {tool_label}",
        f"Tools — {layer_label}",
        str(index),
        layer_id,
    )
    for layer_id, _view_type, layer_label in MainWindow.GIMMICK_LAYERS
    for index, (_tool_id, tool_label) in enumerate(MainWindow.GIMMICK_TOOLSETS[layer_id], start=1)
)


# Qt reads QT_MEDIA_BACKEND once, when the multimedia plugin loads, so the
# choice has to be made before any QMediaPlayer exists -- which is why this
# runs in main() ahead of QApplication rather than in MainWindow.
MEDIA_BACKEND_SETTING = "audio/backend"
ACCURATE_MEDIA_BACKEND = "windows"
COMPATIBLE_MEDIA_BACKEND = "ffmpeg"


def select_media_backend(settings: SettingsManager) -> str:
    """Choose Qt's multimedia backend for this run, and return it.

    Qt's FFmpeg backend -- the default -- reports playback position in fixed
    ~93ms steps of *song* time. At 0.25x that is one true reading every 351ms
    of wall clock, and no amount of interpolation fixes a source that coarse:
    the editor clock has nothing to interpolate between, so the playhead and
    the hitsounds drift against the music at every speed below 1.0x. Windows
    Media Foundation reports at 1ms granularity with ~4ms gaps and a measured
    rate error of 0.00%.

    WMF cannot decode Ogg Vorbis unless Microsoft's Web Media Extensions are
    installed, and osu! song folders are full of .ogg -- so this is a
    preference, not a decision. `MainWindow._audio_backend_failed` writes
    "ffmpeg" here the first time a song refuses to open, and the next launch
    uses it. An explicit QT_MEDIA_BACKEND in the environment always wins, and
    is the way back if that fallback ever fires wrongly.
    """
    forced = os.environ.get("QT_MEDIA_BACKEND", "").strip()
    if forced:
        return forced

    chosen = settings.string_value(MEDIA_BACKEND_SETTING, "").strip()
    if not chosen:
        chosen = ACCURATE_MEDIA_BACKEND if sys.platform == "win32" else ""
    if chosen:
        os.environ["QT_MEDIA_BACKEND"] = chosen
    return chosen


def main() -> None:
    # Before QApplication: the multimedia plugin reads QT_MEDIA_BACKEND when
    # it loads, and nothing can change it afterwards.
    settings = SettingsManager()
    select_media_backend(settings)

    app=QApplication(sys.argv)
    # Without these, QStandardPaths.AppDataLocation resolves to
    # AppData/Roaming/python -- shared with every other PySide app run by the
    # same interpreter, and where the song index would have been written.
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationName(APPLICATION_NAME)
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
    window.maybe_check_for_updates()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()