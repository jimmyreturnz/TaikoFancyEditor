"""The Fancy Arranger page: osu!'s proportions on the canvas, and the page's
boxes as docks that can be moved, floated, closed and brought back.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gui

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class CanvasProportionTests(unittest.TestCase):
    """`OsuPlayfieldAdjustmentContainer`: 512x384 is 80% of a 640x480 frame,
    centred, 8 osu!pixels low -- osu!stable's (64, 56). The canvas used to
    stretch the playfield to the full height, 25% larger than osu! draws it."""

    def setUp(self) -> None:
        self.canvas = gui.TransformCanvas()
        self.canvas.resize(1624, 924)  # a 1600x900 render area inside the 12px margin
        self.canvas._update_view_geometry()
        self.render = self.canvas.render_rect
        self.playfield = self.canvas.playfield_rect
        self.scale = self.render.height() / 480.0

    def test_the_render_area_is_16_9(self):
        self.assertAlmostEqual(self.render.width() / self.render.height(), 16 / 9, places=6)

    def test_the_playfield_is_80_percent_of_the_height(self):
        self.assertAlmostEqual(self.playfield.height() / self.render.height(), 0.8, places=6)
        self.assertAlmostEqual(self.playfield.width() / self.playfield.height(), 4 / 3, places=6)

    def test_it_sits_at_osu_stables_64_56(self):
        frame_left = self.render.center().x() - self.render.height() * 4 / 3 / 2
        self.assertAlmostEqual(self.playfield.left() - frame_left, 64 * self.scale, places=4)
        self.assertAlmostEqual(self.playfield.top() - self.render.top(), 56 * self.scale, places=4)

    def test_notes_map_through_the_same_scale(self):
        self.assertAlmostEqual(self.canvas.view_scale, self.scale, places=6)

    def test_circle_size_is_osus(self):
        self.assertAlmostEqual(gui.osu_circle_radius(5.0), 32.0, places=6)
        self.assertAlmostEqual(gui.osu_circle_radius(7.0), 23.04, places=6)


if __name__ == "__main__":
    unittest.main()
