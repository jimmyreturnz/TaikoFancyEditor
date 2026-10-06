"""App themes (theme.py), and the SV graph's labels staying inside the view."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

import gui
import theme
from settings_dialog import SettingsDialog

_APP: QApplication | None = None

# What a theme must never touch: they mean something in the chart.
CHART_COLOURS = ("#e54c2e", "#438dab", "#fbb706", "#738098", "#f4f7fb", "#ffffff")


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class ThemeMapTests(unittest.TestCase):
    def tearDown(self) -> None:
        theme.install("pink")

    def test_tests_run_on_pink(self) -> None:
        self.assertEqual(theme.active(), "pink")
        self.assertEqual(theme.css("color: #f3a6bd;"), "color: #f3a6bd;")

    def test_no_theme_output_is_another_key(self) -> None:
        # Or a colour passing through css() twice would move twice.
        for name in theme.THEMES:
            table = theme.build_map(name)
            self.assertFalse([v for k, v in table.items() if v in table and v != k], name)

    def test_chart_colours_are_left_alone(self) -> None:
        for name in theme.THEMES:
            table = theme.build_map(name)
            for colour in CHART_COLOURS:
                self.assertNotIn(colour, table, (name, colour))

    def test_stylesheets_are_recoloured_once_installed(self) -> None:
        theme.install("taiko")
        button = QPushButton()
        button.setStyleSheet("QPushButton { background: #f3a6bd; border: 1px solid #FF66AA55; color: #e54c2e; }")
        self.assertEqual(
            button.styleSheet(),
            "QPushButton { background: #d4432a; border: 1px solid #5aa7c755; color: #e54c2e; }")
        self.assertEqual(theme.color("#ff66aa").name(), "#5aa7c7")
        # Integers too: the song list's selected-row tint is written that way
        # and stayed pink in every theme.
        tint = theme.rgba(255, 102, 170, 46)
        self.assertEqual((tint.name(), tint.alpha()), ("#5aa7c7", 46))

    def test_switch_art_follows_the_theme(self) -> None:
        from pathlib import Path
        import config_sheet
        self.assertTrue(config_sheet.ui_asset("switch-on.svg").endswith("assets/ui/switch-on.svg"))
        theme.install("taiko")
        themed = Path(config_sheet.ui_asset("switch-on.svg")).read_text(encoding="utf-8")
        self.assertIn("#5aa7c7", themed)
        self.assertNotIn("#ff66aa", themed)

    def test_mockup_grounds_are_used_exactly(self) -> None:
        self.assertEqual(theme.build_map("matsuri")["#191f29"], "#17120f")
        self.assertEqual(theme.build_map("taiko")["#3a4554"], "#34445a")

    def test_settings_dialog_offers_every_theme(self) -> None:
        settings = gui.SettingsManager()
        dialog = SettingsDialog(settings, gui.ShortcutRegistry(settings))
        names = [dialog.theme_combo.itemData(i) for i in range(dialog.theme_combo.count())]
        self.assertEqual(names, list(theme.THEMES))
        self.assertEqual(dialog.theme_combo.currentData(), "pink")
        dialog.deleteLater()


class ChromeTests(unittest.TestCase):
    def test_only_a_word_too_wide_gets_break_points(self) -> None:
        from PySide6.QtGui import QFont, QFontMetrics
        metrics = QFontMetrics(QFont())
        width = metrics.horizontalAdvance("Inner Oni")
        self.assertEqual(gui.breakable_words("Inner Oni", metrics, width), "Inner Oni")
        long = gui.breakable_words("worldwidesuperstar x", metrics, width)
        self.assertIn(gui.ZERO_WIDTH_SPACE, long)
        self.assertTrue(long.endswith(" x"))
        self.assertEqual(long.replace(gui.ZERO_WIDTH_SPACE, ""), "worldwidesuperstar x")

    def test_control_strip_paints_its_own_ground(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QHBoxLayout
        holder = gui.opaque_strip(QHBoxLayout())
        self.assertTrue(holder.testAttribute(Qt.WA_StyledBackground))
        self.assertNotEqual(holder.property("seeThrough"), True)


class TimingBarLengthTests(unittest.TestCase):
    def test_an_edit_does_not_squeeze_the_bar_to_the_last_note(self) -> None:
        # can you hear my voice: last object 170343ms, decoded track 170793ms.
        # Each edit set the first and the info timer the second, so a green
        # line drag rescaled the bar on every step.
        bar = gui.TimingOverviewBar()
        bar.set_duration(170793)
        bar.apply_document_data((170343, [], [], [], None, []))
        self.assertEqual(bar.duration_ms, 170793)
        bar.set_duration(170793)
        self.assertEqual(bar.duration_ms, 170793)
        # And a document longer than its audio still fits on the bar.
        bar.apply_document_data((180000, [], [], [], None, []))
        self.assertEqual(bar.duration_ms, 180000)


class SvLabelTests(unittest.TestCase):
    def test_label_drops_below_a_point_at_the_top(self) -> None:
        # A point on a 7px top margin (a gimmick band at 100% volume) with an
        # 11px ascent: above it the text would start at y=-8.
        self.assertGreaterEqual(gui.SVEditorView._label_baseline(7.0, 11) - 11, 0)
        # With room, it stays above the point, as before.
        self.assertEqual(gui.SVEditorView._label_baseline(60.0, 11), 56.0)


if __name__ == "__main__":
    unittest.main()
