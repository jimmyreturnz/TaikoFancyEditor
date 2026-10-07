"""The approved mockup's motion (2026-10-07): a light running round whatever
is selected, a sheen across the Gimmick and Fancy Arranger tabs on hover, and
a rectangle that flies between the song list and the difficulty list on
Left/Right.

Every piece is off under `reduced_motion()` -- Windows' "Animation effects",
the switch the eased scrolling already obeys -- and costs nothing while the
thing it animates is not on screen.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QConicalGradient, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

import theme
from smooth_scroll import reduced_motion

# The pink theme's focus colour; theme.color() moves it to the active theme's.
FOCUS = "#ff66aa"

# One lap of the two lights round a rim, and one breath of its glow. Slow on
# purpose: the owner's verdict on the first, faster version was "more subtle".
RIM_LAP_MS = 5000
RIM_BREATH_MS = 3200
# 30 frames a second is plenty for a light this slow, and half the wakeups.
RIM_FRAME_MS = 33


class RimClock(QObject):
    """One timer for every living rim in the window, running only while at
    least one is on screen. A subscriber is a callable that repaints its own
    rim -- a rect, never a whole view."""

    _instance: "RimClock | None" = None

    def __init__(self) -> None:
        super().__init__()
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._timer = QTimer(self)
        self._timer.setInterval(RIM_FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._subscribers: list = []

    @classmethod
    def shared(cls) -> "RimClock":
        if cls._instance is None:
            cls._instance = RimClock()
        return cls._instance

    def phase(self, period_ms: int) -> float:
        """0..1 through the current `period_ms`; 0 under reduced motion, so
        a still rim is always drawn in the same place."""
        if reduced_motion():
            return 0.0
        return (self._elapsed.elapsed() % period_ms) / period_ms

    def subscribe(self, callback) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)
        if not reduced_motion() and not self._timer.isActive():
            self._timer.start()

    def unsubscribe(self, callback) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)
        if not self._subscribers:
            self._timer.stop()

    def _tick(self) -> None:
        for callback in list(self._subscribers):
            try:
                callback()
            except RuntimeError:
                # Its widget was deleted without unsubscribing.
                self._subscribers.remove(callback)
        if not self._subscribers:
            self._timer.stop()


class _WhileShown(QObject):
    def __init__(self, widget: QWidget, callback) -> None:
        super().__init__(widget)
        self.callback = callback
        widget.installEventFilter(self)
        if widget.isVisible():
            RimClock.shared().subscribe(callback)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Show:
            RimClock.shared().subscribe(self.callback)
        elif kind in (QEvent.Hide, QEvent.Destroy):
            RimClock.shared().unsubscribe(self.callback)
        return False


def tick_while_shown(widget: QWidget, callback) -> None:
    """Call `callback` every rim frame while `widget` is on screen."""
    _WhileShown(widget, callback)


def paint_living_rim(painter: QPainter, rect: QRectF, radius: float, width: float, active: bool) -> None:
    """A rim in the focus colour with two small lights running round it and a
    faint glow just inside that breathes. `active` False is the same rim,
    dimmer and still: the selection in a list that does not have the keys."""
    focus = theme.color(FOCUS)
    clock = RimClock.shared()
    inner = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(Qt.NoBrush)
    if not active:
        dim = QColor(focus)
        dim.setAlphaF(0.45)
        painter.setPen(QPen(dim, width))
        painter.drawRoundedRect(inner, radius, radius)
        painter.restore()
        return

    # The glow first, under the rim: a wider, faint stroke just inside it.
    breath = 0.5 - 0.5 * math.cos(2 * math.pi * clock.phase(RIM_BREATH_MS))
    glow = QColor(focus)
    glow.setAlphaF(0.06 + 0.12 * breath)
    painter.setPen(QPen(glow, width + 3))
    painter.drawRoundedRect(inner.adjusted(1.5, 1.5, -1.5, -1.5), radius, radius)

    base = QColor(focus)
    base.setAlphaF(0.55)
    bright = QColor(focus)
    bright.setAlphaF(0.95)
    white = QColor(255, 255, 255, 178)
    gradient = QConicalGradient(rect.center(), -360.0 * clock.phase(RIM_LAP_MS))
    # Two lights half a lap apart, each a narrow peak on the steady rim.
    for start in (0.0, 0.5):
        gradient.setColorAt(start + 0.00, base)
        gradient.setColorAt(start + 0.37, base)
        gradient.setColorAt(start + 0.42, bright)
        gradient.setColorAt(start + 0.44, white)
        gradient.setColorAt(start + 0.46, bright)
        gradient.setColorAt(min(1.0, start + 0.49), base)
    painter.setPen(QPen(gradient, width))
    painter.drawRoundedRect(inner, radius, radius)
    painter.restore()


class HoverSheen(QObject):
    """A band of light that sweeps once across a button as the pointer
    arrives, and a soft glow that holds while it stays. Drawn by a child
    widget laid over the button, so the button keeps its own paint."""

    SWEEP_MS = 700
    GLOW_MS = 350

    def __init__(self, button: QWidget) -> None:
        super().__init__(button)
        self.button = button
        self.overlay = _SheenOverlay(button, self)
        self.sweep = 1.0
        self.glow = 0.0
        self._sweep = QVariantAnimation(self)
        self._sweep.setDuration(self.SWEEP_MS)
        self._sweep.setStartValue(0.0)
        self._sweep.setEndValue(1.0)
        self._sweep.setEasingCurve(QEasingCurve.OutCubic)
        self._sweep.valueChanged.connect(self._set_sweep)
        self._glow = QVariantAnimation(self)
        self._glow.setDuration(self.GLOW_MS)
        self._glow.setEasingCurve(QEasingCurve.OutCubic)
        self._glow.valueChanged.connect(self._set_glow)
        button.installEventFilter(self)

    def _set_sweep(self, value) -> None:
        self.sweep = float(value)
        self.overlay.update()

    def _set_glow(self, value) -> None:
        self.glow = float(value)
        self.overlay.update()

    def _fade_glow(self, to: float) -> None:
        self._glow.stop()
        self._glow.setStartValue(self.glow)
        self._glow.setEndValue(to)
        self._glow.start()

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Resize:
            self.overlay.setGeometry(self.button.rect())
        elif kind == QEvent.Enter and self.button.isEnabled():
            if reduced_motion():
                self.glow = 1.0
                self.overlay.update()
            else:
                self._sweep.stop()
                self._sweep.start()
                self._fade_glow(1.0)
        elif kind == QEvent.Leave:
            if reduced_motion():
                self.glow = 0.0
                self.overlay.update()
            else:
                self._fade_glow(0.0)
        return False


class _SheenOverlay(QWidget):
    def __init__(self, button: QWidget, sheen: HoverSheen) -> None:
        super().__init__(button)
        self.sheen = sheen
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setGeometry(button.rect())
        self.show()

    def paintEvent(self, event) -> None:
        sheen = self.sheen
        if sheen.glow <= 0.0 and sheen.sweep >= 1.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, 5, 5)
        painter.setClipPath(clip)
        focus = theme.color(FOCUS)
        if sheen.glow > 0.0:
            # The glow rises from the button's foot, as in the mockup.
            rise = QLinearGradient(rect.bottomLeft(), rect.topLeft())
            low = QColor(focus)
            low.setAlphaF(0.32 * sheen.glow)
            rise.setColorAt(0.0, low)
            rise.setColorAt(0.7, QColor(0, 0, 0, 0))
            painter.fillRect(rect, rise)
            rim = QColor(focus)
            rim.setAlphaF(0.7 * sheen.glow)
            painter.setPen(QPen(rim, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        if sheen.sweep < 1.0:
            # A skewed band of light crossing left to right.
            width = rect.width() * 0.45
            x = -width + (rect.width() + width * 2) * sheen.sweep
            band = QLinearGradient(QPointF(x, 0), QPointF(x + width, 0))
            edge = QColor(focus)
            edge.setAlphaF(0.0)
            mid = QColor(focus)
            mid.setAlphaF(0.45)
            band.setColorAt(0.0, edge)
            band.setColorAt(0.35, mid)
            band.setColorAt(0.5, QColor(255, 255, 255, 110))
            band.setColorAt(0.65, mid)
            band.setColorAt(1.0, edge)
            painter.translate(x + width / 2, rect.center().y())
            painter.shear(-0.4, 0)
            painter.translate(-(x + width / 2), -rect.center().y())
            painter.fillRect(QRectF(x, rect.top() - 4, width, rect.height() + 8), band)


class MorphRect(QWidget):
    """A rim that flies from one rect to another over its parent: the
    song list's selection to the difficulty list's, and back, so Left and
    Right show where the keys went. Cancelled by the next one."""

    DURATION_MS = 240

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.hide()
        self._from = QRect()
        self._to = QRect()
        self._progress = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self.DURATION_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self.hide)

    def fly(self, start: QRect, end: QRect) -> None:
        """From `start` to `end`, both in the parent's coordinates."""
        if reduced_motion() or start.isNull() or end.isNull():
            return
        self._animation.stop()
        self._from, self._to = QRect(start), QRect(end)
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self._animation.start()

    def _step(self, value) -> None:
        self._progress = float(value)
        self.update()

    def current_rect(self) -> QRectF:
        t = self._progress
        a, b = QRectF(self._from), QRectF(self._to)
        rect = QRectF(
            a.left() + (b.left() - a.left()) * t,
            a.top() + (b.top() - a.top()) * t,
            a.width() + (b.width() - a.width()) * t,
            a.height() + (b.height() - a.height()) * t,
        )
        # Pinched in the middle of the flight, so it reads as moving rather
        # than as a box being resized.
        pinch = math.sin(math.pi * t)
        return rect.adjusted(rect.width() * 0.06 * pinch, rect.height() * 0.1 * pinch,
                             -rect.width() * 0.06 * pinch, -rect.height() * 0.1 * pinch)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.current_rect()
        focus = theme.color(FOCUS)
        fill = QColor(focus)
        fill.setAlphaF(0.10)
        halo = QColor(focus)
        halo.setAlphaF(0.25)
        radius = 4 + 6 * math.sin(math.pi * self._progress)
        painter.setPen(QPen(halo, 5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(focus, 2))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)
