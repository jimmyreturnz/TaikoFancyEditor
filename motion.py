"""The approved mockup's motion (2026-10-07): a light running round whatever
is selected, a sheen across the Gimmick and Fancy Arranger tabs on hover, and
a rectangle that flies between the song list and the difficulty list on
Left/Right, and a view carried by its header to a new place in the stack.

Every piece is off under `reduced_motion()` -- Windows' "Animation effects",
the switch the eased scrolling already obeys -- and costs nothing while the
thing it animates is not on screen.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QConicalGradient, QCursor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

import theme
from smooth_scroll import reduced_motion

# The pink theme's focus colour; theme.color() moves it to the active theme's.
FOCUS = "#ff66aa"
# The pink theme's filled-button colour. A sheen lights a *button*, so it is
# the button's own hue: in pink both are pink, but Monokai's tabs fill green
# and its rims are #f92672, and a pink glow on a green tab read as wrong.
PRIMARY = "#f3a6bd"

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
        focus = theme.color(PRIMARY)
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


class ReorderDrag(QWidget):
    """A view lifted by its header and carried to a new place in its stack.

    Drawn, not laid out: each item is snapshotted once and the snapshots move
    on an overlay, so nothing relays out while the pointer moves and a lane
    mid-playback is not repainted into a moving widget every frame. The real
    layout changes once, on drop (`on_drop(new index)`).

    Every snapshot *follows* its slot rather than animating to it: each frame
    it closes a fixed share of the distance left (FOLLOW_MS), so a pointer
    that changes its mind mid-glide retargets the glide without a restart --
    the owner's "sooooo smooth". The lifted one sits a little larger with a
    shadow under it. Esc puts everything back.

    `items` are siblings stacked top to bottom with no gap between them, which
    is what both pages' edge-to-edge views are.
    """

    FOLLOW_MS = 45.0
    LIFT_SCALE = 1.02
    FRAME_MS = 8
    # Within this far of the scroll area's top or bottom, the stack scrolls,
    # faster the nearer the edge.
    EDGE_PX = 48

    def __init__(self, container: QWidget, items: list, dragged: QWidget, global_pos: QPoint, on_drop) -> None:
        super().__init__(container)
        self.items = list(items)
        self.index = self.items.index(dragged)
        self.on_drop = on_drop
        self.rects = [QRect(item.mapTo(container, QPoint(0, 0)), item.size()) for item in self.items]
        self.pixmaps = [item.grab() for item in self.items]
        self.top = min(rect.top() for rect in self.rects)
        self.bounds = QRect(self.rects[0])
        for rect in self.rects[1:]:
            self.bounds = self.bounds.united(rect)
        self.order = list(range(len(self.items)))
        self.ys = [float(rect.top()) for rect in self.rects]
        self.grab_offset = container.mapFromGlobal(global_pos).y() - self.rects[self.index].top()
        self.drag_y = float(self.rects[self.index].top())
        self.lift = 0.0
        self.dropping = False
        parent = container.parentWidget()
        while parent is not None and not isinstance(parent, QAbstractScrollArea):
            parent = parent.parentWidget()
        self.scroll_area = parent
        self.setGeometry(container.rect())
        self.setCursor(Qt.ClosedHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.show()
        self.raise_()
        self.grabMouse()
        self.grabKeyboard()
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- where everything wants to be -------------------------------------------

    def slots(self) -> dict[int, float]:
        y, slots = float(self.top), {}
        for index in self.order:
            slots[index] = y
            y += self.rects[index].height()
        return slots

    def follow(self, global_y: int) -> None:
        """Carry the lifted view to the pointer at `global_y`, and work out
        which slot that puts it in."""
        y = self.parentWidget().mapFromGlobal(QPoint(0, global_y)).y() - self.grab_offset
        height = self.rects[self.index].height()
        # Held inside the stack, give or take half a view, so it cannot be
        # carried off into the empty page below the last one.
        self.drag_y = max(self.bounds.top() - height / 2, min(self.bounds.bottom() - height / 2, y))
        centre = self.drag_y + height / 2
        others = [index for index in self.order if index != self.index]
        y, place = float(self.top), len(others)
        for position, index in enumerate(others):
            if centre < y + self.rects[index].height() / 2:
                place = position
                break
            y += self.rects[index].height()
        others.insert(place, self.index)
        self.order = others

    def new_index(self) -> int:
        return self.order.index(self.index)

    # -- input --------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        if not self.dropping:
            self.follow(round(event.globalPosition().y()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.drop()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.order = list(range(len(self.items)))
            self.drop()

    def drop(self) -> None:
        if self.dropping:
            return
        self.dropping = True
        self.releaseMouse()
        self.releaseKeyboard()

    # -- frames -------------------------------------------------------------------

    def _autoscroll(self) -> None:
        area = self.scroll_area
        if area is None:
            return
        viewport = area.viewport()
        y = viewport.mapFromGlobal(QCursor.pos()).y()
        if y < self.EDGE_PX:
            step = -(self.EDGE_PX - y)
        elif y > viewport.height() - self.EDGE_PX:
            step = y - (viewport.height() - self.EDGE_PX)
        else:
            return
        bar = area.verticalScrollBar()
        before = bar.value()
        bar.setValue(before + round(step * 0.4))
        if bar.value() != before:
            self.follow(QCursor.pos().y())

    def _tick(self) -> None:
        elapsed = self._clock.restart()
        share = 1.0 if reduced_motion() else 1.0 - math.exp(-elapsed / self.FOLLOW_MS)
        if not self.dropping:
            self._autoscroll()
        slots = self.slots()
        settled = True
        for index in range(len(self.items)):
            if index == self.index and not self.dropping:
                self.ys[index] = self.drag_y
                continue
            gap = slots[index] - self.ys[index]
            self.ys[index] += gap * share
            if abs(gap) > 0.5:
                settled = False
        lift_to = 0.0 if self.dropping else 1.0
        self.lift += (lift_to - self.lift) * share
        if abs(lift_to - self.lift) > 0.02:
            settled = False
        self.update()
        if self.dropping and settled:
            self._finish()

    def _finish(self) -> None:
        self._timer.stop()
        self.hide()
        try:
            self.on_drop(self.new_index())
        finally:
            self.deleteLater()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.bounds, theme.color("#191f29"))
        for index in self.order:
            if index != self.index:
                painter.drawPixmap(QPointF(self.rects[index].left(), self.ys[index]), self.pixmaps[index])
        rect = QRectF(self.rects[self.index])
        rect.moveTop(self.ys[self.index])
        # The shadow under the lifted view: a few soft steps of black.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, round(22 * self.lift)))
        for step in range(1, 7):
            painter.drawRect(rect.adjusted(-step, -step + 3, step, step + 3))
        scale = 1.0 + (self.LIFT_SCALE - 1.0) * self.lift
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self.lift > 0.01)
        painter.translate(rect.center())
        painter.scale(scale, scale)
        painter.translate(-rect.center())
        painter.drawPixmap(rect.topLeft(), self.pixmaps[self.index])
        rim = QColor(theme.color(FOCUS))
        rim.setAlphaF(0.8 * self.lift)
        painter.setPen(QPen(rim, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
