from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from osu_io.parser import parse_osu
from osu_io.writer import write_osu
from transformer import available_transformations, transform

PLAYFIELD_WIDTH = 512
PLAYFIELD_HEIGHT = 384


PARAMETERS: dict[str, list[dict[str, Any]]] = {
    "pinwheel": [
        {"key": "chunk_size", "label": "Notes per Pinwheel", "type": "int", "min": 4, "max": 4096, "default": 256},
        {"key": "center_x", "label": "Center X", "type": "float", "min": 0, "max": 512, "default": 256, "step": 1},
        {"key": "center_y", "label": "Center Y", "type": "float", "min": 0, "max": 384, "default": 192, "step": 1},
        {"key": "num_blades", "label": "Blades", "type": "int", "min": 1, "max": 24, "default": 6},
        {"key": "blade_curl", "label": "Blade Curl", "type": "float", "min": -4, "max": 4, "default": 0.8, "step": 0.05},
        {"key": "rotation_offset_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
        {"key": "blade_spread_deg", "label": "Blade Spread", "type": "float", "min": 0, "max": 360, "default": 60, "step": 1},
        {"key": "inner_radius", "label": "Inner Radius", "type": "float", "min": 0, "max": 190, "default": 18, "step": 1},
        {"key": "outer_radius", "label": "Outer Radius", "type": "float", "min": 1, "max": 256, "default": 170, "step": 1},
        {"key": "wander_strength", "label": "Wander Strength", "type": "float", "min": 0, "max": 80, "default": 8, "step": 1},
        {"key": "wander_seed", "label": "Wander Seed", "type": "int", "min": 0, "max": 999999, "default": 12345},
        {"key": "inner_circle_notes", "label": "Inner Circle Notes", "type": "int", "min": 1, "max": 512, "default": 24},
        {"key": "inner_circle_radius", "label": "Inner Circle Radius", "type": "float", "min": 1, "max": 190, "default": 18, "step": 1},
        {"key": "inner_circle_enabled", "label": "Inner Circle", "type": "choice", "choices": [("Enabled", True), ("Disabled", False)], "default": True},
        {"key": "radius_growth_curve", "label": "Radius Growth", "type": "choice", "choices": [("Linear", "linear"), ("Ease Out", "ease_out"), ("Ease In", "ease_in")], "default": "ease_out"},
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
    ],
    "equation": [
        {"key": "graph_size", "label": "Graph Size (%)", "type": "int", "min": 10, "max": 300, "default": 100},
        {"key": "equation_mode", "label": "Graph Type", "type": "choice", "choices": [("Explicit", "explicit"), ("Implicit", "implicit"), ("Parametric", "parametric")], "default": "implicit"},
        {"key": "equation", "label": "Equation", "type": "text", "default": "tan(x^2+y^2)=1{|x|<3}{|y|<3}"},
        {"key": "x_expression", "label": "x(t)", "type": "text", "default": "cos(3*t)"},
        {"key": "y_expression", "label": "y(t)", "type": "text", "default": "sin(2*t)"},
        {"key": "x_min", "label": "X Minimum", "type": "float", "min": -100, "max": 100, "default": -5, "step": 0.1},
        {"key": "x_max", "label": "X Maximum", "type": "float", "min": -100, "max": 100, "default": 5, "step": 0.1},
        {"key": "y_min", "label": "Y Minimum", "type": "float", "min": -100, "max": 100, "default": -3.75, "step": 0.1},
        {"key": "y_max", "label": "Y Maximum", "type": "float", "min": -100, "max": 100, "default": 3.75, "step": 0.1},
        {"key": "t_min", "label": "t Minimum", "type": "float", "min": -100, "max": 100, "default": 0, "step": 0.1},
        {"key": "t_max", "label": "t Maximum", "type": "float", "min": -100, "max": 100, "default": 6.283, "step": 0.1},
        {"key": "resolution", "label": "Resolution", "type": "int", "min": 32, "max": 384, "default": 160},
        {"key": "chunk_size", "label": "Notes per Curve", "type": "int", "min": 2, "max": 4096, "default": 256},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 20},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 20},
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
    ],
    "text": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "text_size", "label": "Text Size (%)", "type": "int", "min": 20, "max": 100, "default": 90},
        {"key": "chunk_size", "label": "Notes per Text", "type": "int", "min": 2, "max": 2048, "default": 128},
        {"key": "text", "label": "Text", "type": "text", "default": "67"},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 20},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 20},
        {"key": "reverse", "label": "Direction", "type": "choice", "choices": [("Forward", False), ("Reverse", True)], "default": False},
    ],
    "horizontal": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "line_count", "label": "Lines", "type": "int", "min": 1, "max": 32, "default": 8},
        {"key": "notes_per_line", "label": "Notes per line", "type": "int", "min": 1, "max": 128, "default": 16},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 12},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 10},
        {"key": "direction", "label": "Direction", "type": "choice", "choices": [("Left to Right", "left_to_right"), ("Right to Left", "right_to_left")], "default": "left_to_right"},
    ],
    "vertical": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "line_count", "label": "Columns", "type": "int", "min": 1, "max": 32, "default": 8},
        {"key": "notes_per_line", "label": "Notes per column", "type": "int", "min": 1, "max": 128, "default": 16},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 12},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 10},
        {"key": "direction", "label": "Direction", "type": "choice", "choices": [("Top to Bottom", "top_to_bottom"), ("Bottom to Top", "bottom_to_top")], "default": "top_to_bottom"},
    ],
    "circle": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per circle", "type": "int", "min": 2, "max": 256, "default": 36},
        {"key": "radius", "label": "Radius", "type": "float", "min": 10, "max": 190, "default": 150, "step": 1},
        {"key": "center_x", "label": "Center X", "type": "float", "min": 0, "max": 512, "default": 256, "step": 1},
        {"key": "center_y", "label": "Center Y", "type": "float", "min": 0, "max": 384, "default": 192, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "ellipse": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per ellipse", "type": "int", "min": 2, "max": 256, "default": 48},
        {"key": "radius_x", "label": "Radius X", "type": "float", "min": 10, "max": 250, "default": 200, "step": 1},
        {"key": "radius_y", "label": "Radius Y", "type": "float", "min": 10, "max": 190, "default": 120, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "square": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per square", "type": "int", "min": 4, "max": 256, "default": 32},
        {"key": "side_length", "label": "Side length", "type": "float", "min": 20, "max": 380, "default": 280, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "triangle": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per triangle", "type": "int", "min": 3, "max": 256, "default": 30},
        {"key": "radius", "label": "Radius", "type": "float", "min": 10, "max": 190, "default": 160, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "diamond": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per diamond", "type": "int", "min": 4, "max": 256, "default": 32},
        {"key": "width", "label": "Width", "type": "float", "min": 20, "max": 500, "default": 320, "step": 1},
        {"key": "height", "label": "Height", "type": "float", "min": 20, "max": 380, "default": 260, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "infinity": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per infinity", "type": "int", "min": 4, "max": 256, "default": 64},
        {"key": "width", "label": "Width", "type": "float", "min": 20, "max": 500, "default": 380, "step": 1},
        {"key": "height", "label": "Height", "type": "float", "min": 20, "max": 380, "default": 220, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "star": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per star", "type": "int", "min": 5, "max": 256, "default": 32},
        {"key": "points", "label": "Star points", "type": "int", "min": 3, "max": 16, "default": 5},
        {"key": "outer_radius", "label": "Outer radius", "type": "float", "min": 10, "max": 190, "default": 170, "step": 1},
        {"key": "inner_radius", "label": "Inner radius", "type": "float", "min": 5, "max": 180, "default": 75, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "spiral": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per spiral", "type": "int", "min": 2, "max": 256, "default": 80},
        {"key": "turns", "label": "Turns", "type": "float", "min": 0.25, "max": 8, "default": 2.5, "step": 0.05},
        {"key": "start_radius", "label": "Start radius", "type": "float", "min": 0, "max": 180, "default": 0, "step": 1},
        {"key": "end_radius", "label": "End radius", "type": "float", "min": 1, "max": 190, "default": 170, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "arc": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per arc", "type": "int", "min": 2, "max": 256, "default": 24},
        {"key": "radius_x", "label": "Radius X", "type": "float", "min": 10, "max": 250, "default": 180, "step": 1},
        {"key": "radius_y", "label": "Radius Y", "type": "float", "min": 10, "max": 190, "default": 130, "step": 1},
        {"key": "start_angle_deg", "label": "Start angle", "type": "float", "min": -360, "max": 360, "default": -180, "step": 1},
        {"key": "sweep_angle_deg", "label": "Sweep angle", "type": "float", "min": -360, "max": 360, "default": 180, "step": 1},
    ],
    "straight_line": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per line", "type": "int", "min": 2, "max": 256, "default": 16},
        {"key": "length", "label": "Length", "type": "float", "min": 10, "max": 500, "default": 400, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "wave": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per wave", "type": "int", "min": 2, "max": 256, "default": 32},
        {"key": "width", "label": "Width", "type": "float", "min": 20, "max": 500, "default": 440, "step": 1},
        {"key": "amplitude", "label": "Amplitude", "type": "float", "min": 1, "max": 190, "default": 100, "step": 1},
        {"key": "cycles", "label": "Cycles", "type": "float", "min": 0.25, "max": 8, "default": 2, "step": 0.05},
        {"key": "phase_deg", "label": "Phase", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "zigzag": [
        {"key": "back_and_forth", "label": "Traversal", "type": "choice", "choices": [("Restart Each Chunk", False), ("Back and Forth", True)], "default": False},
        {"key": "chunk_size", "label": "Notes per zigzag", "type": "int", "min": 2, "max": 256, "default": 48},
        {"key": "width", "label": "Width", "type": "float", "min": 20, "max": 500, "default": 440, "step": 1},
        {"key": "height", "label": "Height", "type": "float", "min": 20, "max": 380, "default": 240, "step": 1},
        {"key": "segments", "label": "Segments", "type": "int", "min": 1, "max": 32, "default": 8},
        {"key": "rotation_deg", "label": "Rotation", "type": "float", "min": 0, "max": 360, "default": 0, "step": 1},
    ],
    "random_walk": [
        {"key": "seed", "label": "Seed", "type": "int", "min": 0, "max": 999999, "default": 12345},
        {"key": "step_size", "label": "Step size", "type": "float", "min": 1, "max": 100, "default": 35, "step": 1},
        {"key": "max_turn_deg", "label": "Maximum turn", "type": "float", "min": 1, "max": 180, "default": 55, "step": 1},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 20},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 20},
    ],
    "random": [
        {"key": "chunk_size", "label": "Notes per chunk", "type": "int", "min": 1, "max": 256, "default": 64},
        {"key": "seed", "label": "Seed", "type": "int", "min": 0, "max": 999999, "default": 12345},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 20},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 20},
    ],
    "taiko": [
        {"key": "beats_per_line", "label": "Beats per line", "type": "float", "min": 0.25, "max": 16, "default": 2, "step": 0.25},
        {"key": "line_count", "label": "Lines", "type": "int", "min": 1, "max": 32, "default": 8},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 12},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 10},
        {"key": "min_bpm", "label": "Minimum BPM", "type": "float", "min": 1, "max": 500, "default": 30, "step": 1},
        {"key": "max_bpm", "label": "Maximum BPM", "type": "float", "min": 30, "max": 10000, "default": 1000, "step": 10},
    ],
    "vertical_taiko": [
        {"key": "beats_per_line", "label": "Beats per line", "type": "float", "min": 0.25, "max": 16, "default": 2, "step": 0.25},
        {"key": "line_count", "label": "Columns", "type": "int", "min": 1, "max": 32, "default": 8},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 12},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 10},
        {"key": "min_bpm", "label": "Minimum BPM", "type": "float", "min": 1, "max": 500, "default": 30, "step": 1},
        {"key": "max_bpm", "label": "Maximum BPM", "type": "float", "min": 30, "max": 10000, "default": 1000, "step": 10},
        {"key": "direction", "label": "Direction", "type": "choice", "choices": [("Top to Bottom", "top_to_bottom"), ("Bottom to Top", "bottom_to_top")], "default": "top_to_bottom"},
    ],
    "dvd_bouncing": [
        {"key": "seed", "label": "Seed", "type": "int", "min": 0, "max": 999999, "default": 12345},
        {"key": "step_size", "label": "Step size", "type": "float", "min": 1, "max": 150, "default": 35, "step": 1},
        {"key": "margin_x", "label": "Margin X", "type": "int", "min": 0, "max": 255, "default": 20},
        {"key": "margin_y", "label": "Margin Y", "type": "int", "min": 0, "max": 191, "default": 20},
    ],
}

# First GUI slice excludes point-list transformations until canvas capture exists.
GUI_TRANSFORMATIONS = [
    name for name in available_transformations()
    if name in PARAMETERS
]


class ParameterControl(QWidget):
    value_changed = Signal()

    def __init__(self, definition: dict[str, Any]) -> None:
        super().__init__()
        self.definition = definition
        self.scale = self._scale_for(definition)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(
            round(float(definition["min"]) * self.scale),
            round(float(definition["max"]) * self.scale),
        )

        if definition["type"] == "int":
            self.spinbox = QSpinBox()
            self.spinbox.setRange(int(definition["min"]), int(definition["max"]))
            self.spinbox.setSingleStep(int(definition.get("step", 1)))
        else:
            self.spinbox = QDoubleSpinBox()
            self.spinbox.setRange(float(definition["min"]), float(definition["max"]))
            self.spinbox.setSingleStep(float(definition.get("step", 0.1)))
            self.spinbox.setDecimals(self._decimals_for(definition))

        self.spinbox.setFixedWidth(92)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spinbox)

        self.slider.valueChanged.connect(self._slider_changed)
        self.spinbox.valueChanged.connect(self._spinbox_changed)
        self.set_value(definition["default"])

    @staticmethod
    def _scale_for(definition: dict[str, Any]) -> int:
        if definition["type"] == "int":
            return 1
        step = float(definition.get("step", 0.1))
        if step >= 1:
            return 1
        if step >= 0.1:
            return 10
        return 100

    @staticmethod
    def _decimals_for(definition: dict[str, Any]) -> int:
        step = float(definition.get("step", 0.1))
        if step >= 1:
            return 1
        if step >= 0.1:
            return 1
        return 2

    def _slider_changed(self, raw_value: int) -> None:
        value = raw_value / self.scale
        self.spinbox.blockSignals(True)
        self.spinbox.setValue(value)
        self.spinbox.blockSignals(False)
        self.value_changed.emit()

    def _spinbox_changed(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(float(value) * self.scale))
        self.slider.blockSignals(False)
        self.value_changed.emit()

    def value(self) -> int | float:
        return self.spinbox.value()

    def set_value(self, value: int | float) -> None:
        self.spinbox.setValue(value)
        self.slider.setValue(round(float(value) * self.scale))


class PlayfieldView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.scene = QGraphicsScene(0, 0, PLAYFIELD_WIDTH, PLAYFIELD_HEIGHT)
        self.setScene(self.scene)
        self.setRenderHint(self.renderHints())
        self.setBackgroundBrush(QColor("#11151c"))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setMinimumSize(640, 480)
        self.setAlignment(Qt.AlignCenter)
        self.setSceneRect(0, 0, PLAYFIELD_WIDTH, PLAYFIELD_HEIGHT)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Taiko Arranger - Live Preview Draft")
        self.resize(1180, 720)

        self.document = None
        self.source_path: Path | None = None
        self.note_items: dict[int, QGraphicsEllipseItem] = {}
        self.original_positions: dict[int, tuple[int, int]] = {}
        self.preview_positions: dict[int, tuple[int, int]] = {}
        self.parameter_controls: dict[str, ParameterControl] = {}

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(35)
        self.preview_timer.timeout.connect(self.update_preview)

        self._build_ui()
        self._transformation_changed()
        self._set_loaded_state(False)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)
        self.setCentralWidget(central)

        left = QWidget()
        left.setFixedWidth(340)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        self.open_button = QPushButton("Open .osu file")
        self.open_button.clicked.connect(self.open_map)
        left_layout.addWidget(self.open_button)

        self.file_label = QLabel("No map loaded")
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet("color: #9aa4b2; padding: 4px;")
        left_layout.addWidget(self.file_label)

        left_layout.addWidget(QLabel("Transformation"))
        self.transformation_combo = QComboBox()
        self.transformation_combo.addItems(GUI_TRANSFORMATIONS)
        self.transformation_combo.currentTextChanged.connect(
            self._transformation_changed
        )
        left_layout.addWidget(self.transformation_combo)

        self.parameter_container = QWidget()
        self.parameter_form = QFormLayout(self.parameter_container)
        self.parameter_form.setContentsMargins(0, 4, 0, 4)
        self.parameter_form.setSpacing(9)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.parameter_container)
        left_layout.addWidget(scroll, 1)

        button_row = QHBoxLayout()
        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self.reset_preview)
        self.export_button = QPushButton("Export copy")
        self.export_button.clicked.connect(self.export_preview)
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.export_button)
        left_layout.addLayout(button_row)

        self.status_label = QLabel("Open a map to begin.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #9aa4b2;")
        left_layout.addWidget(self.status_label)

        self.playfield = PlayfieldView()

        root.addWidget(left)
        root.addWidget(self.playfield, 1)

        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #191f29; color: #e8edf3; }
            QPushButton { background: #2d6cdf; border: 0; border-radius: 6px;
                          padding: 9px 12px; font-weight: 600; }
            QPushButton:hover { background: #3c7bf0; }
            QPushButton:disabled { background: #39414d; color: #7d8794; }
            QComboBox, QSpinBox, QDoubleSpinBox {
                background: #252d39; border: 1px solid #3a4554;
                border-radius: 5px; padding: 5px;
            }
            QSlider::groove:horizontal { height: 5px; background: #333d4b; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; margin: -5px 0;
                                         background: #62a0ff; border-radius: 7px; }
            """
        )

    def _set_loaded_state(self, loaded: bool) -> None:
        self.transformation_combo.setEnabled(loaded)
        self.parameter_container.setEnabled(loaded)
        self.reset_button.setEnabled(loaded)
        self.export_button.setEnabled(loaded)

    def _clear_parameter_form(self) -> None:
        while self.parameter_form.rowCount():
            self.parameter_form.removeRow(0)
        self.parameter_controls.clear()

    def _transformation_changed(self) -> None:
        self._clear_parameter_form()
        name = self.transformation_combo.currentText()

        for definition in PARAMETERS.get(name, []):
            control = ParameterControl(definition)
            control.value_changed.connect(self.schedule_preview)
            self.parameter_controls[definition["key"]] = control
            self.parameter_form.addRow(definition["label"], control)

        if self.document is not None:
            self.schedule_preview()

    def current_params(self) -> dict[str, Any]:
        params = {
            key: control.value()
            for key, control in self.parameter_controls.items()
        }

        if self.transformation_combo.currentText() == "taiko":
            params.update(
                {
                    "note_times": {
                        note.original_index: note.time
                        for note in self.document.hit_objects
                    },
                    "timing_points": self._timing_point_lines(),
                    "timing_mode": "filtered",
                    "anchor_mode": "selection_start",
                }
            )

        return params

    def open_map(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open osu! beatmap",
            "",
            "osu! beatmaps (*.osu)",
        )
        if not filename:
            return

        try:
            document = parse_osu(filename)
            if not document.hit_objects:
                raise ValueError("No hit objects were parsed from this map.")
        except Exception as error:
            QMessageBox.critical(self, "Open failed", str(error))
            return

        self.document = document
        self.source_path = Path(filename).resolve()
        self.original_positions = {
            note.original_index: (note.x, note.y)
            for note in document.hit_objects
        }
        self.preview_positions = dict(self.original_positions)
        self.file_label.setText(
            f"{self.source_path.name}\n"
            f"{document.version} | {len(document.hit_objects)} notes"
        )
        self._create_note_items()
        self._set_loaded_state(True)
        self.schedule_preview()

    def _create_note_items(self) -> None:
        self.playfield.scene.clear()
        self.note_items.clear()

        border_pen = QPen(QColor("#f4f7fb"))
        border_pen.setWidthF(0.8)

        for note in self.document.hit_objects:
            is_finisher = bool(note.hit_sound & 4)
            is_kat = bool(note.hit_sound & (2 | 8))
            diameter = 12 if is_finisher else 8
            color = QColor("#4aa3ff") if is_kat else QColor("#ff4f5e")

            item = QGraphicsEllipseItem(
                -diameter / 2,
                -diameter / 2,
                diameter,
                diameter,
            )
            item.setBrush(QBrush(color))
            item.setPen(border_pen)
            item.setOpacity(0.82)
            item.setZValue(2 if is_finisher else 1)
            item.setPos(note.x, note.y)
            item.setToolTip(
                f"Index: {note.original_index}\n"
                f"Time: {note.time} ms\n"
                f"{'Kat' if is_kat else 'Don'}"
                f"{' finisher' if is_finisher else ''}"
            )
            self.playfield.scene.addItem(item)
            self.note_items[note.original_index] = item

    def _timing_point_lines(self) -> list[str]:
        section = ""
        result: list[str] = []

        for line in self.document.lines:
            content = line.rstrip("\r\n")
            stripped = content.strip()

            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1]
                continue

            if section == "TimingPoints" and stripped and not stripped.startswith("//"):
                result.append(content)

        return result

    def schedule_preview(self) -> None:
        if self.document is not None:
            self.preview_timer.start()

    def update_preview(self) -> None:
        name = self.transformation_combo.currentText()
        indexes = [note.original_index for note in self.document.hit_objects]

        try:
            positions = transform(name, indexes, self.current_params())
        except Exception as error:
            self.status_label.setText(f"Preview error: {error}")
            return

        self.preview_positions = positions
        for index, (x, y) in positions.items():
            self.note_items[index].setPos(x, y)

        self.status_label.setText(
            f"Previewing {name} on {len(indexes)} notes."
        )

    def reset_preview(self) -> None:
        self.preview_timer.stop()
        self.preview_positions = dict(self.original_positions)

        for index, (x, y) in self.original_positions.items():
            self.note_items[index].setPos(x, y)

        self.status_label.setText("Preview reset to original coordinates.")

    def export_preview(self) -> None:
        if self.document is None or self.source_path is None:
            return

        name = self.transformation_combo.currentText()
        default_path = self._unique_output_path(name)
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export transformed difficulty",
            str(default_path),
            "osu! beatmaps (*.osu)",
        )
        if not destination:
            return

        destination_path = Path(destination).resolve()
        if destination_path == self.source_path:
            QMessageBox.warning(
                self,
                "Source protected",
                "Choose a different filename. The source map will not be overwritten.",
            )
            return

        try:
            for note in self.document.hit_objects:
                note.x, note.y = self.preview_positions[note.original_index]

            write_osu(
                document=self.document,
                destination=destination_path,
                new_version=f"{self.document.version} ({name})",
            )
        except Exception as error:
            QMessageBox.critical(self, "Export failed", str(error))
            return
        finally:
            # Keep the parsed document's model aligned with the visible preview.
            for note in self.document.hit_objects:
                note.x, note.y = self.preview_positions[note.original_index]

        self.status_label.setText(f"Exported: {destination_path.name}")
        QMessageBox.information(
            self,
            "Export complete",
            f"Created a new difficulty:\n{destination_path}",
        )

    def _unique_output_path(self, transformation: str) -> Path:
        candidate = self.source_path.with_name(
            f"{self.source_path.stem} [{transformation}].osu"
        )
        number = 2

        while candidate.exists():
            candidate = self.source_path.with_name(
                f"{self.source_path.stem} [{transformation}] ({number}).osu"
            )
            number += 1

        return candidate


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()