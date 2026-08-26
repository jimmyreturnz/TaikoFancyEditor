"""The "Kiai and Sound Effect" layer: every timing point on one axis, plus its
Kiai and Volume range tools.

Three properties, one per thing the layer actually promises:

* a millisecond carrying both an uninherited and an inherited point draws
  **one yellow rule**, not a red and a green fighting over the same pixel,
* the Volume sweep writes whole percent inside 0..100 -- an oscillation fans
  past both ends by design, so the clamp is load-bearing rather than defensive,
* switching kiai on where no timing point exists writes a green line carrying
  the SV **already in force**, so the section does not silently flatten scroll
  speed at its own edges.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gui
from osu_io.timing import TimingPoint
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class _EmptyDocument:
    """Enough document for the layer's millisecond-ownership question."""

    hit_objects: list = []
    timing_points: list = []


class LayerRegistrationTests(unittest.TestCase):
    """The toolbox side: Kiai moved here, it was not copied here."""

    def test_kiai_left_the_fake_slider_layer(self):
        tools = dict(gui.MainWindow.GIMMICK_TOOLSETS["fake_slider"])
        self.assertNotIn("kiai", tools)

    def test_layer_carries_select_kiai_and_volume(self):
        tools = [tool for tool, _label in gui.MainWindow.GIMMICK_TOOLSETS["kiai_sound"]]
        self.assertEqual(tools, ["select", "kiai", "volume"])

    def test_layer_is_an_sv_view_in_the_gimmick_stack(self):
        layers = {layer: view for layer, view, _label in gui.MainWindow.GIMMICK_LAYERS}
        self.assertEqual(layers.get("kiai_sound"), "sv")


class LineColourTests(unittest.TestCase):
    """One entry per *timestamp*: the rule the timing overview bar already uses."""

    def test_red_and_green_on_one_millisecond_draw_one_yellow_rule(self):
        view = gui.SVEditorView()
        view.set_timing_points([
            TimingPoint.uninherited_at(1000, 120),
            TimingPoint.inherited_at(1000, 1.5),
            TimingPoint.inherited_at(2000, 0.5),
            TimingPoint.uninherited_at(3000, 180),
        ])

        self.assertEqual(
            view.line_kinds(), {1000.0: "yellow", 2000.0: "green", 3000.0: "red"}
        )


class RangeToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")

        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)
        self.state = self.window.state

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()

    def _point_at(self, time_ms: int, uninherited: bool = False) -> TimingPoint:
        return next(
            point for point in self.state.document.timing_points
            if round(point.time) == time_ms and point.uninherited == uninherited
        )

    def test_the_add_view_dialog_builds_a_real_view_for_it(self):
        dialog = gui.AddViewDialog([("Oni", self.path)], self.window, self.path)
        offered = [dialog.type_combo.itemData(i) for i in range(dialog.type_combo.count())]
        dialog.deleteLater()
        self.assertIn("kiai_sound", offered)

        self.window._add_editor_view("kiai_sound", self.path)
        frame = self.window._editor_views[-1]
        self.assertIsInstance(frame.sv_view, gui.SVEditorView)
        # Unfiltered: the layer's whole subject is every layer's points at once.
        self.assertIsNone(frame.sv_view.point_times)

    def test_the_layer_shows_every_point_rather_than_one_structure_s(self):
        # None means "owns every millisecond" -- the three structure SV layers
        # each answer with a set instead, which is what filters their view.
        self.assertIsNone(self.window._sv_layer_times("kiai_sound", _EmptyDocument()))

    # -- Kiai ---------------------------------------------------------------

    def test_kiai_edge_with_no_point_gets_a_green_line_at_the_current_sv(self):
        # The fixture's last inherited point before 4500 is 4000 at 2.0x, and
        # nothing sits on 4500 -- so the edge has to be invented, and inventing
        # it at 1.0x would halve the scroll speed the moment kiai came on.
        self.window._set_kiai_range(self.path, 4500.0, 5500.0)

        start = self._point_at(4500)
        self.assertFalse(start.uninherited)
        self.assertAlmostEqual(start.sv_multiplier, 2.0)
        self.assertTrue(start.kiai)

        end = self._point_at(5500)
        self.assertAlmostEqual(end.sv_multiplier, 2.0)
        self.assertFalse(end.kiai, "the closing edge is what ends the section")

    def test_kiai_range_is_one_undo_step(self):
        before = len(self.state.document.timing_points)
        self.window._set_kiai_range(self.path, 4500.0, 5500.0)
        self.assertEqual(len(self.state.document.timing_points), before + 2)

        self.window.undo()
        self.assertEqual(len(self.state.document.timing_points), before)

    def test_the_view_snaps_the_drag_so_the_handler_does_not(self):
        # Deliberately off-grid: the range arrives already snapped (the view
        # owns the grid *and* the Ctrl override that trades it for whole
        # milliseconds), so it must land where it was asked to rather than
        # being pulled back onto a beat here.
        self.window._set_kiai_range(self.path, 4507.0, 5503.0)
        self.assertTrue(any(
            round(point.time) == 4507 for point in self.state.document.timing_points
        ))

    # -- Volume -------------------------------------------------------------

    def _volume_params(self, **overrides) -> dict:
        params = {
            "initial_rate": 20.0, "final_rate": 100.0, "placement": "notes",
            "snap_divisor": 4, "position_offset": 0, "omit_barline": False,
            "relative_to_final_bpm": False, "function": "linear", "oscillate": "point",
        }
        params.update(overrides)
        return params

    def test_oscillated_volume_is_clamped_into_0_100(self):
        # 20 +/- 80 fans to -44 and to 100 across the fixture's notes in range,
        # so both ends of the clamp are exercised rather than merely available.
        before_ids = {point.uid for point in self.state.document.timing_points}
        self.window._generate_sv(
            self.path, 1000.0, 3500.0, self._volume_params(), volume=True,
        )
        volumes = [
            point.volume for point in self.state.document.timing_points
            if point.uid not in before_ids
        ]

        self.assertTrue(volumes, "the sweep generated nothing")
        self.assertTrue(all(isinstance(value, int) for value in volumes), volumes)
        self.assertTrue(all(0 <= value <= 100 for value in volumes), volumes)
        self.assertIn(100, volumes, "the upper clamp never fired")
        self.assertIn(0, volumes, "the lower clamp never fired")

    def test_a_volume_sweep_leaves_scroll_speed_and_kiai_alone(self):
        # 2000 is the fixture's kiai green line at 0.75x. Regenerating volume
        # over it replaces the point, so both properties have to be restated.
        self.window._generate_sv(
            self.path, 1000.0, 3500.0, self._volume_params(), volume=True,
        )
        point = self._point_at(2000)
        self.assertAlmostEqual(point.sv_multiplier, 0.75)
        self.assertTrue(point.kiai)

    def test_a_volume_sweep_is_one_undo_step(self):
        before = [(point.uid, point.volume) for point in self.state.document.timing_points]
        self.window._generate_sv(
            self.path, 1000.0, 3500.0, self._volume_params(), volume=True,
        )
        self.assertEqual(len(self.state.history.undo_stack), 1)

        self.window.undo()
        self.assertEqual(
            [(point.uid, point.volume) for point in self.state.document.timing_points],
            before,
        )


class VolumeDialogTests(unittest.TestCase):
    """Same generator, same options -- only the quantity changes."""

    def test_volume_mode_offers_whole_percent_in_0_100(self):
        dialog = gui.SVFunctionDialog(0.0, 1000.0, volume=True, initial_rate=60, final_rate=90)
        self.assertEqual(dialog.initial_rate_spin.decimals(), 0)
        self.assertEqual(dialog.initial_rate_spin.minimum(), 0.0)
        self.assertEqual(dialog.initial_rate_spin.maximum(), 100.0)
        self.assertEqual(dialog.final_rate_spin.value(), 90.0)
        dialog.deleteLater()

    def test_volume_mode_keeps_every_function_and_both_oscillations(self):
        volume = gui.SVFunctionDialog(0.0, 1000.0, volume=True)
        sv = gui.SVFunctionDialog(0.0, 1000.0)
        self.assertEqual(set(volume.function_buttons), set(sv.function_buttons))
        self.assertEqual(
            [volume.mode_combo.itemData(i) for i in range(volume.mode_combo.count())],
            [sv.mode_combo.itemData(i) for i in range(sv.mode_combo.count())],
        )
        volume.deleteLater()
        sv.deleteLater()


if __name__ == "__main__":
    unittest.main()
