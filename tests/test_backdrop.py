"""The Editor/Gimmick pages' map backdrop, and right click on the empty space
under the views opening Add view."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

import gui

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    import settings as settings_module

    settings_module.APPLICATION_NAME = "TaikoFancyArrangerTests"
    _APP = QApplication.instance() or QApplication([])


class BackdropTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.picture = Path(self._temp.name) / "bg.png"
        image = QImage(40, 20, QImage.Format_RGB32)
        image.fill(QColor("#ffffff"))
        image.save(str(self.picture))
        self.backdrop = gui.MapBackdrop()
        self.backdrop.resize(100, 100)

    def tearDown(self):
        self._temp.cleanup()

    def pixel(self) -> QColor:
        return self.backdrop.grab().toImage().pixelColor(50, 50)

    def test_no_map_is_plain_navy(self):
        self.assertEqual(self.pixel(), gui.MapBackdrop.NAVY)

    def test_the_picture_is_faded_by_the_opacity(self):
        self.backdrop.set_background(self.picture)
        self.backdrop.set_opacity(0)
        self.assertEqual(self.pixel(), gui.MapBackdrop.NAVY)
        self.backdrop.set_opacity(50)
        faded = self.pixel()
        navy = gui.MapBackdrop.NAVY
        self.assertAlmostEqual(faded.red(), (navy.red() + 255) / 2, delta=2)

    def test_a_paint_is_one_blit_until_something_changes(self):
        bakes = []
        original = self.backdrop._bake
        self.backdrop._bake = lambda: bakes.append(1) or original()
        self.backdrop.set_background(self.picture)
        for _ in range(3):
            self.backdrop.grab()
        self.assertEqual(len(bakes), 1)
        self.backdrop.set_opacity(25)  # unchanged: the default
        self.backdrop.grab()
        self.assertEqual(len(bakes), 1)
        self.backdrop.set_opacity(60)
        self.backdrop.grab()
        self.backdrop.resize(120, 90)
        self.backdrop.grab()
        self.assertEqual(len(bakes), 3)


class AddViewRightClickTests(unittest.TestCase):
    def test_only_the_empty_space_opens_add_view(self):
        container = QWidget()
        container.resize(200, 200)
        view = QPushButton("a view", container)
        view.setGeometry(0, 0, 200, 50)
        opened = []
        gui.add_view_on_empty_right_click(container, lambda: opened.append(1))
        container.customContextMenuRequested.emit(QPoint(10, 20))
        self.assertEqual(opened, [], "a right click on a view")
        container.customContextMenuRequested.emit(QPoint(10, 150))
        self.assertEqual(opened, [1])


class WindowTests(unittest.TestCase):
    def test_both_pages_have_a_backdrop_and_see_through_chrome(self):
        window = gui.MainWindow()
        try:
            self.assertEqual(len(window._backdrops), 2)
            for scroll in (window.gimmick_scroll,):
                self.assertTrue(scroll.viewport().property("seeThrough"))
            self.assertTrue(window.editor_views_layout.parentWidget().property("seeThrough"))
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
