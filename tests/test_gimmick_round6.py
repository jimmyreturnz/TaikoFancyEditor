"""Sixth owner feedback round: the convert tools, the two effects bits, the
standalone red line BPM, and oscillating SV.

Kept out of `test_gimmick_editor.py` on purpose -- that file builds a window
per test and takes minutes to run whole, so a round's worth of new behaviour
gets its own module that can be run on its own.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gui
from tests.test_gimmick_editor import _GimmickFixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class _Session(_GimmickFixture):
    def setUp(self) -> None:
        super().setUp()
        self._enter(gui.GimmickEntryDialog.USE_CURRENT)
        self.state = self.window._states[self.window._gimmick_pairing.target]
        self.document = self.state.document

    def _red(self, time_ms):
        return [p for p in self.document.timing_points if p.time == time_ms and p.uninherited]


class ConvertToolTests(_Session, unittest.TestCase):
    """Turning a range of the chart's own notes into gimmick structures."""

    def _range_of_notes(self, count: int) -> tuple[float, float, list]:
        notes = sorted(
            (n for n in self.document.hit_objects if n.is_circle), key=lambda n: n.time
        )[:count]
        return notes[0].time, notes[-1].time, notes

    def test_a_fake_slider_conversion_decorates_every_note_in_the_range(self):
        start, end, notes = self._range_of_notes(3)
        before = len(self.document.hit_objects)
        # The fixture already carries a drumroll of its own, so this counts
        # what the conversion added rather than what is in the map.
        existing = {n.uid for n in self.document.hit_objects}

        self.window._convert_notes_to_gimmick(
            self.window._gimmick_pairing.target, "fake_slider", start, end
        )

        offset = self.window._gimmick_config("fake_slider").fake_slider_offset_ms
        sliders = [
            n for n in self.document.hit_objects
            if n.uid not in existing and gui.MainWindow.is_fake_slider(n)
        ]
        self.assertEqual(len(sliders), len(notes))
        self.assertEqual(
            sorted(round(n.time) for n in sliders),
            sorted(round(n.time) + offset for n in notes),
        )
        # The chart's own notes are kept, not replaced: only sliders were added.
        self.assertEqual(len(self.document.hit_objects) - before, len(notes))

    def test_a_kat_converts_to_the_big_fake_slider(self):
        kat = next(n for n in self.document.hit_objects if n.is_circle and n.is_kat)
        self.window._convert_notes_to_gimmick(
            self.window._gimmick_pairing.target, "fake_slider", kat.time, kat.time
        )
        offset = self.window._gimmick_config("fake_slider").fake_slider_offset_ms
        slider = next(
            n for n in self.document.hit_objects
            if gui.MainWindow.is_fake_slider(n) and round(n.time) == round(kat.time) + offset
        )
        self.assertTrue(slider.is_finisher)

    def test_a_barline_conversion_writes_lines_and_no_extra_notes(self):
        start, end, notes = self._range_of_notes(3)
        before = len(self.document.hit_objects)
        reds = len([p for p in self.document.timing_points if p.uninherited])

        self.window._convert_notes_to_gimmick(
            self.window._gimmick_pairing.target, "barline", start, end
        )

        self.assertEqual(len(self.document.hit_objects), before, "no note is added")
        self.assertGreater(
            len([p for p in self.document.timing_points if p.uninherited]), reds
        )
        for note in notes:
            self.assertAlmostEqual(self._red(note.time)[0].bpm, 60000.0)

    def test_the_whole_conversion_is_one_undo_step(self):
        start, end, _ = self._range_of_notes(3)
        points_before = list(self.document.timing_points)
        notes_before = list(self.document.hit_objects)

        self.window._convert_notes_to_gimmick(
            self.window._gimmick_pairing.target, "fake_slider", start, end
        )
        self.state.history.undo(self.state)

        self.assertEqual(self.document.timing_points, points_before)
        self.assertEqual(self.document.hit_objects, notes_before)

    def test_converted_notes_each_get_their_own_selection_key(self):
        """`_next_original_index` reads the document, which a batch has not
        written to yet -- asking it per note gave every one the same key."""
        start, end, notes = self._range_of_notes(3)
        self.window._convert_notes_to_gimmick(
            self.window._gimmick_pairing.target, "fake_slider", start, end
        )
        indices = [n.original_index for n in self.document.hit_objects]
        self.assertEqual(len(indices), len(set(indices)))

    def test_both_layers_offer_the_tool(self):
        for layer in ("fake_slider", "barline"):
            self.assertIn("convert", dict(gui.MainWindow.GIMMICK_TOOLSETS[layer]))


class EffectsBitTests(_Session, unittest.TestCase):
    def test_a_fake_slider_omits_its_barline_by_default(self):
        self.window._place_gimmick("fake_slider", "don", 10000)
        added = [p for p in self.document.timing_points if p.uninherited and p.bpm == 60000.0]
        self.assertTrue(added)
        self.assertTrue(all(p.omit_first_barline for p in added))

    def test_the_config_can_switch_it_off(self):
        self.window.gimmick_configs["fake_slider"] = gui.GimmickConfig(omit_barline=False)
        self.window._place_gimmick("fake_slider", "don", 10000)
        added = [p for p in self.document.timing_points if p.uninherited and p.bpm == 60000.0]
        self.assertTrue(added)
        self.assertFalse(any(p.omit_first_barline for p in added))

    def test_a_barline_note_still_draws_its_bars(self):
        """The barline layer ignores the flag -- bars are its whole output."""
        self.window._place_gimmick("barline", "kat", 10000)
        added = [p for p in self.document.timing_points if 9995 <= p.time <= 10005]
        self.assertFalse(any(p.omit_first_barline for p in added))

    def test_a_barline_don_is_one_bar_either_side_and_a_kat_is_three(self):
        self.window._place_gimmick("barline", "don", 10000)
        self.window._place_gimmick("barline", "kat", 20000)
        self.assertEqual(
            sorted(round(p.time) - 10000 for p in self.document.timing_points
                   if 9990 <= p.time <= 10010 and p.uninherited),
            [-1, 0, 1],
        )
        self.assertEqual(
            sorted(round(p.time) - 20000 for p in self.document.timing_points
                   if 19990 <= p.time <= 20010 and p.uninherited),
            [-5, -3, -1, 0, 1, 3, 5],
        )

    def test_a_placement_inside_kiai_keeps_kiai_on(self):
        self.document.timing_points.append(
            gui.TimingPoint.inherited_at(9000, 1.0, kiai=True)
        )
        self.document.timing_points.sort(key=lambda p: p.time)
        self.window._place_gimmick("fake_slider", "kat", 10000)
        added = [
            p for p in self.document.timing_points
            if 10000 <= p.time <= 10005 and p.time != 9000
        ]
        self.assertTrue(added)
        self.assertTrue(all(p.kiai for p in added), "a gimmick must not end the chorus")

    def test_a_custom_redline_bpm_is_what_the_red_line_tool_writes(self):
        self.window.gimmick_configs["barline"] = gui.GimmickConfig(red_line_bpm=45.0)
        self.window._place_gimmick("barline", "red_line", 10000)
        self.assertAlmostEqual(self._red(10000)[0].bpm, 45.0)


class KatSpacingConfigTests(_Session, unittest.TestCase):
    """Kat's three mirrored pairs are independently configurable, in place of
    the old single `spacing_ms` a formula (n, n+2, n+4) used to derive all
    three from. Don keeps `spacing_ms` to itself."""

    def setUp(self) -> None:
        super().setUp()
        # Distinct, none matching the old derived 1/3/5 -- so a test that
        # passed by accident against the old progression would fail here.
        self.window.gimmick_configs["barline"] = gui.GimmickConfig(
            kat_spacing1_ms=2, kat_spacing2_ms=6, kat_spacing3_ms=9,
        )

    def _offsets_near(self, centre_ms):
        return sorted(
            round(p.time) - centre_ms for p in self.document.timing_points
            if centre_ms - 15 <= p.time <= centre_ms + 15 and p.uninherited
        )

    def test_a_custom_kat_writes_lines_at_its_own_three_spacings(self):
        self.window._place_gimmick("barline", "kat", 10000)
        self.assertEqual(self._offsets_near(10000), [-9, -6, -2, 0, 2, 6, 9])

    def test_dons_own_spacing_is_untouched_by_the_kat_config(self):
        self.window._place_gimmick("barline", "don", 10000)
        self.assertEqual(self._offsets_near(10000), [-1, 0, 1])

    def test_expand_move_gathers_every_custom_spaced_line(self):
        """`_expand_move` used to grow a drag through +/-(n, n+2, n+4) built
        from one shared spacing -- with Kat's three pairs independent, that
        formula no longer names the lines this structure actually wrote, and
        a drag would have left whichever of them the formula missed behind."""
        self.window._place_gimmick("barline", "kat", 10000)
        note = next(n for n in self.document.hit_objects if n.time == 10000)

        notes, points = self.window._expand_move(self.state, {note.uid}, set())

        self.assertEqual({n.uid for n in notes}, {note.uid})
        self.assertEqual(
            sorted(round(p.time) - 10000 for p in points if p.uninherited),
            [-9, -6, -2, 0, 2, 6, 9],
        )

    def test_a_drag_moves_every_bar_and_leaves_none_behind(self):
        self.window._place_gimmick("barline", "kat", 10000)
        note = next(n for n in self.document.hit_objects if n.time == 10000)

        self.window._move_objects(
            self.window._gimmick_pairing.target, [note.uid], [], 200,
        )

        self.assertEqual(self._offsets_near(10200), [-9, -6, -2, 0, 2, 6, 9])
        self.assertEqual(
            [p for p in self.document.timing_points
             if 9985 <= p.time <= 10015 and p.uninherited],
            [], "nothing left at the structure's old position",
        )


class KatSpacingDialogTests(_Session, unittest.TestCase):
    """`GimmickConfigDialog` must round-trip the three Kat fields even from a
    layer that never shows their rows, and must refuse to close with two of
    them equal rather than handing back a config `GimmickConfig` itself would
    reject."""

    def test_the_dialog_round_trips_the_kat_fields_even_when_hidden(self):
        config = gui.GimmickConfig(kat_spacing1_ms=2, kat_spacing2_ms=6, kat_spacing3_ms=9)
        # Opened on a layer that does not show these rows (fake_slider): the
        # values must still come back unchanged, the same pattern as
        # `fake_slider_bpm_multiplier`'s round trip in
        # FakeSliderBpmMultiplierConfigTests above.
        dialog = gui.GimmickConfigDialog(config, "fake_slider", gui.GimmickConfig(), self.window)
        result = dialog.config()
        self.assertEqual(
            (result.kat_spacing1_ms, result.kat_spacing2_ms, result.kat_spacing3_ms),
            (2, 6, 9),
        )
        dialog.deleteLater()

    def test_the_barline_dialog_shows_the_defaults_and_edits_feed_back(self):
        dialog = gui.GimmickConfigDialog(
            gui.GimmickConfig(), "barline", gui.GimmickConfig(), self.window,
        )
        self.assertEqual(dialog.kat_spacing1_spin.value(), 1)
        self.assertEqual(dialog.kat_spacing2_spin.value(), 3)
        self.assertEqual(dialog.kat_spacing3_spin.value(), 5)
        dialog.kat_spacing1_spin.setValue(4)
        dialog.kat_spacing2_spin.setValue(8)
        dialog.kat_spacing3_spin.setValue(12)
        result = dialog.config()
        self.assertEqual(
            (result.kat_spacing1_ms, result.kat_spacing2_ms, result.kat_spacing3_ms),
            (4, 8, 12),
        )
        dialog.deleteLater()

    def test_accepting_with_two_equal_kat_spacings_is_refused(self):
        """Nothing in the spin boxes' own min/max stops two of them landing on
        the same value the way it stops every other field here -- so unlike
        those, this one combination has to be caught by hand before the
        dialog is allowed to close."""
        dialog = gui.GimmickConfigDialog(
            gui.GimmickConfig(), "barline", gui.GimmickConfig(), self.window,
        )
        dialog.kat_spacing1_spin.setValue(3)
        dialog.kat_spacing2_spin.setValue(3)
        with patch.object(gui.QMessageBox, "warning", lambda *a, **k: None):
            dialog.accept()
        self.assertNotEqual(dialog.result(), int(gui.QDialog.Accepted))
        dialog.deleteLater()


class TimingLineDialogEffectsTests(unittest.TestCase):
    """Double click a line: its two effects bits are editable there now."""

    def test_the_boxes_start_from_the_point(self):
        point = gui.TimingPoint.uninherited_at(1000, 180.0, kiai=True)
        dialog = gui.TimingLineDialog(point)
        self.assertTrue(dialog.kiai_check.isChecked())
        self.assertFalse(dialog.omit_barline_check.isChecked())
        dialog.deleteLater()

    def test_toggling_one_bit_leaves_the_other_alone(self):
        point = gui.TimingPoint.uninherited_at(1000, 180.0, kiai=True)
        dialog = gui.TimingLineDialog(point)
        dialog.omit_barline_check.setChecked(True)
        self.assertEqual(dialog.changes(point)["effects"], (1, 9))
        dialog.deleteLater()

    def test_an_unknown_bit_is_not_dropped(self):
        point = gui.TimingPoint.uninherited_at(1000, 180.0)
        point.effects = 4  # nothing this dialog offers
        dialog = gui.TimingLineDialog(point)
        dialog.kiai_check.setChecked(True)
        self.assertEqual(dialog.effects(point), 5)
        dialog.deleteLater()

    def test_no_change_means_no_command(self):
        point = gui.TimingPoint.inherited_at(1000, 1.5, kiai=True)
        dialog = gui.TimingLineDialog(point)
        self.assertEqual(dialog.changes(point), {})
        dialog.deleteLater()


class OscillatingSVTests(_Session, unittest.TestCase):
    """Oscillation reads the growth function as an amplitude, not a position."""

    def _generate(self, mode: str) -> list[float]:
        target = self.window._gimmick_pairing.target
        notes = sorted(
            (n for n in self.document.hit_objects if n.is_circle), key=lambda n: n.time
        )[:7]
        self.window._generate_sv(target, notes[0].time, notes[-1].time, {
            "initial_rate": 1.0, "final_rate": 1.06, "placement": "notes",
            "snap_divisor": 4, "position_offset": 0, "omit_barline": False,
            "relative_to_final_bpm": False, "function": "linear", "oscillate": mode,
        })
        return [
            p.sv_multiplier for p in sorted(
                (p for p in self.document.timing_points
                 if p.inherited and notes[0].time <= p.time <= notes[-1].time),
                key=lambda p: p.time,
            )
        ]

    def test_per_pair_alternates_around_the_base(self):
        values = self._generate("pair")
        self.assertGreater(len(values), 2)
        self.assertAlmostEqual(values[0], 1.0, places=3)
        for index, value in enumerate(values[1:], start=1):
            if index % 2:
                self.assertGreater(value, 1.0)
            else:
                self.assertLess(value, 1.0)

    def test_a_sweep_is_still_monotonic(self):
        values = self._generate("")
        self.assertEqual(values, sorted(values))

    def test_the_dialog_offers_the_modes_and_reports_the_choice(self):
        dialog = gui.SVFunctionDialog(0.0, 1000.0)
        self.assertEqual(dialog.parameters()["oscillate"], "")
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("pair"))
        self.assertEqual(dialog.parameters()["oscillate"], "pair")
        dialog.deleteLater()

    def test_the_preview_plots_the_oscillation_it_would_write(self):
        preview = gui.SVFunctionPreview()
        preview.set_range(1.0, 0.06 + 1.0)
        preview.set_oscillate("pair")
        values = [round(value, 4) for value in preview.rates(7)]
        self.assertEqual(values, [1.0, 1.02, 0.98, 1.04, 0.96, 1.06, 0.94])
        preview.deleteLater()


class ShinyNoteTests(_Session, unittest.TestCase):
    """A stack of fake sliders on one millisecond, and the layer that shows it."""

    def _layer(self):
        return self.window._gimmick_views[1].chart_view

    def _sliders_at(self, time_ms):
        return [
            n for n in self.document.hit_objects
            if gui.MainWindow.is_fake_slider(n) and round(n.time) == time_ms
        ]

    def test_the_shiny_tool_writes_a_stack_at_the_shiny_offset(self):
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        self.assertEqual(
            len(self._sliders_at(10000 + config.shiny_offset_ms)), config.shiny_count
        )

    def test_a_plain_fake_slider_is_not_a_shiny(self):
        self.window._place_gimmick("fake_slider", "regular", 10000)
        self.assertEqual(self.window._shiny_times(self.document), set())

    def test_a_stacked_fake_slider_is_still_a_fake_slider(self):
        """The offset says which structure it is, not the stack size: a plain
        fake slider gets stacked too, to brighten it."""
        config = self.window._gimmick_config("fake_slider")
        pairing = self.window._gimmick_pairing
        commands = self.window._gimmick_commands(
            self.state, pairing, "fake_slider", "regular", 10000,
            iter(range(1000, 1100)), copies=4,
        )
        self.state.history.push(gui.CompositeCommand(commands, "test"), self.state)
        at = 10000 + config.fake_slider_offset_ms
        self.assertEqual(len(self._sliders_at(at)), 4)
        self.assertNotIn(at, self.window._shiny_times(self.document))

    def test_the_layer_marks_the_shiny_and_nothing_else(self):
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        self.window._place_gimmick("fake_slider", "regular", 20000)
        layer = self._layer()
        self.assertIn(10000 + config.shiny_offset_ms, layer.shiny_times)
        self.assertNotIn(20000 + config.fake_slider_offset_ms, layer.shiny_times)
        self.assertTrue(layer.split_rows, "layer 2 draws two rows")

    def test_the_two_rows_are_apart_and_the_grid_is_between_them(self):
        layer = self._layer()
        baseline = layer.height() / 2
        self.assertLess(layer._row_y(baseline, False), baseline)
        self.assertGreater(layer._row_y(baseline, True), baseline)

    def test_layer_five_leaves_shiny_notes_alone(self):
        """Its Generate walks fake sliders; a shiny's speed comes from layer 4."""
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        self.window._place_gimmick("fake_slider", "regular", 20000)
        times = self.window._sv_layer_object_times("sv_fake_slider", self.document)
        self.assertIn(20000 + config.fake_slider_offset_ms, times)
        self.assertNotIn(10000 + config.shiny_offset_ms, times)
        self.assertNotIn(10000, times)

    def test_layer_four_still_reaches_the_shiny(self):
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        times = self.window._sv_layer_object_times("sv_chart", self.document)
        self.assertIn(10000, times, "the shiny's gimmick line is the chart layer's")

    def test_a_fake_slider_and_a_shiny_do_not_collide_on_one_snap(self):
        """The two rows exist so they can be read a millisecond apart, so the
        one-structure-per-millisecond rule cannot apply to this layer.

        A plain fake slider's own line no longer carries the gimmick BPM --
        there is nothing to squash, so it just holds the chart's own BPM (see
        the table in `gimmick_session.fake_slider`'s docstring) -- so the two
        structures placed on one snap no longer share a single gimmick line
        the way they did under the old squash-based scheme. Each still gets
        its own, undisturbed by the other."""
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "regular", 10000)
        self.window._place_gimmick("fake_slider", "shiny", 10000)

        self.assertEqual(len(self._sliders_at(10000 + config.fake_slider_offset_ms)), 1)
        self.assertEqual(
            len(self._sliders_at(10000 + config.shiny_offset_ms)), config.shiny_count
        )
        # The shiny's own red line, on the snap itself...
        self.assertEqual(
            len([p for p in self.document.timing_points if p.time == 10000 and p.uninherited]),
            1,
        )
        # ...and the fake slider's own, on its own offset -- two lines, not one.
        self.assertEqual(
            len([
                p for p in self.document.timing_points
                if p.time == 10000 + config.fake_slider_offset_ms and p.uninherited
            ]),
            1,
        )

    def test_the_same_structure_twice_on_one_snap_is_refused(self):
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        self.assertEqual(
            len(self._sliders_at(10000 + config.shiny_offset_ms)), config.shiny_count
        )

    def test_deleting_one_slider_takes_the_whole_stack(self):
        """The stack is one object to the mapper -- leaving two thirds of a
        shiny behind is the wreckage of one, not a smaller one."""
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        at = 10000 + config.shiny_offset_ms
        victim = self._sliders_at(at)[0]

        self.window._delete_gimmick_objects(
            self.window._gimmick_pairing.target, [victim.uid]
        )
        self.assertEqual(self._sliders_at(at), [])
        self.assertEqual(
            [p for p in self.document.timing_points if p.time == 10000 and p.uninherited], []
        )

    def test_the_function_tool_fills_a_range_with_shiny_notes(self):
        target = self.window._gimmick_pairing.target

        class _Stub:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return 1

            def times(self):
                return [10000, 11000]

            def object_kind(self):
                return "shiny"

            def shiny_count(self):
                return 4

            def bpm_multiplier(self):
                return 0.5

            def deleteLater(self):
                pass

        with patch.object(gui, "FakeSliderFunctionDialog", _Stub):
            self.window._generate_fake_sliders(target, 10000, 11000)

        config = self.window._gimmick_config("fake_slider")
        self.assertEqual(config.shiny_count, 4, "the chosen stack size is kept")
        self.assertEqual(config.shiny_bpm_multiplier, 0.5)
        for at in (10000, 11000):
            self.assertEqual(len(self._sliders_at(at + config.shiny_offset_ms)), 4)
        self.assertIn(10000 + config.shiny_offset_ms, self.window._shiny_times(self.document))

    def test_the_whole_generated_run_is_one_undo_step(self):
        target = self.window._gimmick_pairing.target
        notes_before = list(self.document.hit_objects)

        class _Stub:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return 1

            def times(self):
                return [10000, 11000]

            def object_kind(self):
                return "regular"

            def shiny_count(self):
                return 3

            def bpm_multiplier(self):
                return 1.0

            def deleteLater(self):
                pass

        with patch.object(gui, "FakeSliderFunctionDialog", _Stub):
            self.window._generate_fake_sliders(target, 10000, 11000)
        self.state.history.undo(self.state)
        self.assertEqual(self.document.hit_objects, notes_before)

    def test_the_bpm_multiplier_pays_itself_back_in_sv(self):
        """100 BPM at 2.4x is the same picture as 200 BPM at 1.2x."""
        config = self.window._gimmick_config("fake_slider")
        base_bpm = gui.base_bpm_at(self.window._gimmick_pairing.base_timing, 10000)
        config.shiny_bpm_multiplier = 0.5
        self.window._place_gimmick("fake_slider", "shiny", 10000)

        # The red+green pair sits on the snap itself, not on the stack --
        # see the table in gimmick_session.fake_slider's docstring.
        red = next(p for p in self.document.timing_points if p.time == 10000 and p.uninherited)
        green = next(p for p in self.document.timing_points if p.time == 10000 and p.inherited)
        self.assertAlmostEqual(red.bpm, base_bpm * 0.5)
        self.assertAlmostEqual(green.sv_multiplier * 0.5, 1.0, places=3)


class HandAuthoredShinyTests(_Session, unittest.TestCase):
    """The owner's own file, which is what the classifier has to survive.

        256,192,65393,1,0,0:0:0:0:            <- a normal note
        256,192,65394,2,8,...,-0.0001  x3     <- shiny:        note + 1
        256,192,65395,2,12,...,-0.0001 x3     <- fake sliders: note + 2

    with one red+green pair governing each of the two positions, and no 60000
    BPM squash line anywhere. So the structure cannot be found by looking for a
    gimmick BPM -- it is the distance back to the *note* that says which is
    which.
    """

    def setUp(self) -> None:
        super().setUp()
        document = self.document
        document.hit_objects.append(gui.HitObject(
            x=256, y=192, time=65393, type=1, hit_sound=0, hit_sample="0:0:0:0:",
        ))
        for at, hit_sound in ((65394, 8), (65395, 12)):
            for _ in range(3):
                document.hit_objects.append(gui.HitObject(
                    x=256, y=192, time=at, type=2, hit_sound=hit_sound,
                    extras=("L|624:192", "643", "-0.0001"), hit_sample="",
                ))
        document.hit_objects.sort(key=lambda note: note.time)
        document.timing_points.extend([
            gui.TimingPoint(65393, 648.648648648649, 4, 1, 0, 100, 1, 9),
            gui.TimingPoint(65393, -43.4782608695652, 4, 1, 0, 100, 0, 1),
            gui.TimingPoint(65395, 487.80487804878, 4, 1, 0, 100, 1, 9),
            gui.TimingPoint(65395, -1.001001001001, 4, 1, 0, 100, 0, 1),
        ])
        document.timing_points.sort(key=lambda point: point.time)

    def test_the_stack_one_ms_out_is_the_shiny(self):
        self.assertIn(65394, self.window._shiny_times(self.document))

    def test_the_stack_two_ms_out_is_a_fake_slider(self):
        self.assertNotIn(65395, self.window._shiny_times(self.document))

    def test_layer_five_reaches_the_fake_sliders_and_not_the_shiny(self):
        times = self.window._sv_layer_object_times("sv_fake_slider", self.document)
        self.assertIn(65395, times)
        self.assertNotIn(65394, times)
        self.assertNotIn(65393, times)

    def test_the_chart_sv_layer_reaches_the_note_and_its_shiny_together(self):
        times = self.window._sv_layer_object_times("sv_chart", self.document)
        self.assertIn(65393, times, "which is where the shiny's own green line is")

    def test_kiai_has_no_say_in_what_the_stack_is(self):
        """Position alone decides. Kiai used to be a fourth condition, which
        made the same bytes mean two different objects depending on a flag
        written somewhere else -- maddening in a hand-edited file."""
        self.assertIn(65394, self.window._shiny_times(self.document))
        for point in self.document.timing_points:
            if point.time == 65393:
                point.set_kiai(False)
        self.assertIn(
            65394, self.window._shiny_times(self.document),
            "+1 is a shiny whether or not a chorus happens to cover it",
        )

    def test_the_layer_draws_them_on_opposite_rows(self):
        layer = self.window._gimmick_views[1].chart_view
        layer.refresh_notes(self.document)
        self.assertIn(65394, layer.shiny_times)
        self.assertNotIn(65395, layer.shiny_times)
        baseline = layer.height() / 2
        self.assertGreater(layer._row_y(baseline, True), layer._row_y(baseline, False))


class BarlineGenerateSvTests(_Session, unittest.TestCase):
    """Generating red lines writes a green line with each of them.

    Every uninherited point resets SV to 1.0x, so a run of red lines used to
    flatten the chart's scroll speed for its whole length. "Current speed" --
    the default -- restates what was already in force, so the run changes
    where the bars fall and nothing else.
    """

    def setUp(self) -> None:
        super().setUp()
        self.document.timing_points.append(gui.TimingPoint.inherited_at(9000, 1.7))
        self.document.timing_points.sort(key=lambda p: p.time)
        self.target = self.window._gimmick_pairing.target

    def _generate(self, sv):
        class _Stub:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return 1

            def times(self):
                return [10000, 10001, 10002]

            def bpms(self, times):
                return [180.0] * len(times)

            def sv_multiplier(self):
                return sv

            def deleteLater(self):
                pass

        with patch.object(gui, "BarlineFunctionDialog", _Stub):
            self.window._generate_barlines(self.target, 10000, 10002)
        return [p for p in self.document.timing_points if 10000 <= p.time <= 10002]

    def test_current_speed_keeps_the_chart_moving_at_the_speed_it_was(self):
        greens = [p for p in self._generate(None) if p.inherited]
        self.assertEqual(len(greens), 3)
        for point in greens:
            self.assertAlmostEqual(point.sv_multiplier, 1.7)

    def test_a_custom_multiplier_drives_the_whole_run(self):
        greens = [p for p in self._generate(0.25) if p.inherited]
        self.assertEqual(len(greens), 3)
        for point in greens:
            self.assertAlmostEqual(point.sv_multiplier, 0.25)

    def test_each_green_sorts_after_the_red_it_belongs_to(self):
        """osu! resolves a shared timestamp by file order, and the red line has
        just reset SV to 1.0x -- the other way round the reset wins."""
        written = self._generate(None)
        at_first = [p.uninherited for p in written if round(p.time) == 10000]
        self.assertEqual(at_first, [True, False])

    def test_the_generated_sv_belongs_to_the_barline_sv_layer(self):
        self._generate(None)
        owned = self.window._sv_layer_object_times("sv_barline", self.document)
        for at in (10000, 10001, 10002):
            self.assertIn(at, owned)

    def test_the_dialog_defaults_to_the_speed_already_in_force(self):
        dialog = gui.BarlineFunctionDialog(
            10000.0, 10002.0, base_timing=self.window._gimmick_pairing.base_timing,
            current_sv=1.7,
        )
        self.assertIsNone(dialog.sv_multiplier(), "current speed, not a number")
        self.assertAlmostEqual(dialog.sv_spin.value(), 1.7)
        dialog.sv_mode_combo.setCurrentIndex(dialog.sv_mode_combo.findData("custom"))
        self.assertAlmostEqual(dialog.sv_multiplier(), 1.7)
        dialog.deleteLater()


class BarlineBetweenNoteAndSliderTests(_Session, unittest.TestCase):
    """The owner's second real file, which the first rule got wrong.

        87124  note        + a 66666 BPM squash
        87125  red line    <- the don's barline, one forward
        87126  fake slider + its own SV

    The object at 87126 is two milliseconds after its *note*, so it is a fake
    slider -- but only one after the barline that happens to sit between them.
    Reading the nearest anchor of any kind called it a shiny; a note has to
    outrank a line, at both distances, before lines are considered at all.
    """

    def setUp(self) -> None:
        super().setUp()
        self.document.hit_objects.append(gui.HitObject(
            x=256, y=192, time=87124, type=1, hit_sound=0, hit_sample="0:0:0:0:",
        ))
        self.document.hit_objects.append(gui.HitObject(
            x=256, y=192, time=87126, type=2, hit_sound=12,
            extras=("L|624:192", "643", "-0.0001"), hit_sample="",
        ))
        self.document.hit_objects.sort(key=lambda note: note.time)
        self.document.timing_points.extend([
            gui.TimingPoint(87124, 0.90000900009, 666, 1, 0, 100, 1, 8),
            gui.TimingPoint(87125, 324.324324324324, 555, 1, 0, 100, 1, 0),
            gui.TimingPoint(87125, -61.5384615384615, 4, 1, 0, 100, 0, 0),
            gui.TimingPoint(87126, -15.3846153846154, 4, 1, 0, 100, 0, 0),
        ])
        self.document.timing_points.sort(key=lambda point: point.time)

    def test_the_slider_two_ms_after_its_note_is_not_a_shiny(self):
        self.assertNotIn(87126, self.window._shiny_times(self.document))

    def test_an_intervening_barline_does_not_make_it_one(self):
        """87125 carries a red line, so `at - 1` finds an anchor -- the note
        two milliseconds back has to win that argument."""
        reds = {round(p.time) for p in self.document.timing_points if p.uninherited}
        self.assertIn(87125, reds, "the barline this test is about")
        self.assertNotIn(87126, self.window._shiny_times(self.document))

    def test_it_belongs_to_the_fake_slider_sv_layer(self):
        self.assertIn(
            87126, self.window._sv_layer_object_times("sv_fake_slider", self.document)
        )

    def test_the_other_file_still_reads_the_way_it_did(self):
        """The +1 stack off a note is still a shiny -- this fix must not undo
        the case it was built for."""
        self.document.hit_objects.append(gui.HitObject(
            x=256, y=192, time=65393, type=1, hit_sound=0, hit_sample="0:0:0:0:",
        ))
        for _ in range(3):
            self.document.hit_objects.append(gui.HitObject(
                x=256, y=192, time=65394, type=2, hit_sound=8,
                extras=("L|624:192", "643", "-0.0001"), hit_sample="",
            ))
        self.document.hit_objects.sort(key=lambda note: note.time)
        shiny = self.window._shiny_times(self.document)
        self.assertIn(65394, shiny)
        self.assertNotIn(87126, shiny)


class KiaiRangeTests(_Session, unittest.TestCase):
    """The Kiai tool: one drag, one kiai section.

    It lives in the Kiai and Sound Volume layer now -- a kiai section is read
    against every layer at once, so the fake slider layer was never its home.
    """

    def _kiai_at(self, time_ms):
        return gui.in_kiai(gui.sorted_by_time(self.document.timing_points), time_ms)

    def _lines_at(self, *times):
        """Put a green line on each of `times`, at the SV already in force.

        The Kiai tool flags the lines already inside the range and writes
        none of its own -- the layer it lives in must never touch scroll
        speed, and inventing an inherited point to carry the flag is a
        scroll-speed edit. So a section's edges have to be real lines before
        the drag, which is what these tests set up here rather than relying on
        the tool to conjure them.
        """
        target = self.window._gimmick_pairing.target
        for time_ms in times:
            sv = gui.sv_at(gui.sorted_by_time(self.document.timing_points), time_ms)
            self.window._add_sv_point(target, time_ms, sv)

    def test_every_point_in_the_range_carries_kiai(self):
        # Kiai first: a shiny is refused outside one (see KiaiRefusalTests),
        # so placing it after the section exists is what a mapper actually
        # does, and lets this test its real subject -- that every line a
        # placement writes inside an existing section still carries kiai.
        self._lines_at(9000, 12000)
        self.window._set_kiai_range(self.window._gimmick_pairing.target, 9000, 12000)
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        self.window._place_gimmick("barline", "kat", 11000)

        inside = [
            p for p in self.document.timing_points
            if 9000 <= round(p.time) < 12000
        ]
        self.assertTrue(inside)
        self.assertTrue(all(p.kiai for p in inside), "one line without it ends the section")

    def test_the_section_starts_and_ends_where_the_drag_did(self):
        target = self.window._gimmick_pairing.target
        self._lines_at(9000, 12000)
        self.window._set_kiai_range(target, 9000, 12000)
        self.assertFalse(self._kiai_at(8999))
        self.assertTrue(self._kiai_at(9000))
        self.assertTrue(self._kiai_at(11999))
        self.assertFalse(self._kiai_at(12000))

    def test_the_edges_change_nothing_but_the_flag(self):
        target = self.window._gimmick_pairing.target
        self._lines_at(9000, 12000)
        before = gui.sv_at(gui.sorted_by_time(self.document.timing_points), 9000)
        self.window._set_kiai_range(target, 9000, 12000)
        self.assertAlmostEqual(
            gui.sv_at(gui.sorted_by_time(self.document.timing_points), 9000), before
        )

    def test_a_backwards_or_empty_drag_does_nothing(self):
        target = self.window._gimmick_pairing.target
        before = list(self.document.timing_points)
        self.window._set_kiai_range(target, 12000, 12000)
        self.assertEqual(self.document.timing_points, before)

    def test_the_whole_range_is_one_undo_step(self):
        target = self.window._gimmick_pairing.target
        self._lines_at(9000, 12000)
        self.window._place_gimmick("barline", "kat", 11000)
        before = [(p.uid, p.effects) for p in self.document.timing_points]
        self.window._set_kiai_range(target, 9000, 12000)
        self.state.history.undo(self.state)
        self.assertEqual(
            [(p.uid, p.effects) for p in self.document.timing_points], before
        )

    def test_the_tool_is_in_the_kiai_and_sound_effect_toolbox(self):
        self.assertIn("kiai", dict(gui.MainWindow.GIMMICK_TOOLSETS["kiai_sound"]))
        self.assertNotIn("kiai", dict(gui.MainWindow.GIMMICK_TOOLSETS["fake_slider"]))

    def test_every_layer_draws_the_section(self):
        """The band is background information, so it belongs in every layer
        rather than only in the one whose tool wrote it."""
        target = self.window._gimmick_pairing.target
        self._lines_at(9000, 12000)
        self.window._set_kiai_range(target, 9000, 12000)
        for frame in self.window._gimmick_views:
            view = getattr(frame, "chart_view", None) or frame.sv_view
            self.assertIn(
                (9000, 12000), view.kiai_bands, f"{frame.gimmick_layer} has no band"
            )

    def test_the_band_is_clipped_to_the_window(self):
        view = self.window._gimmick_views[0].chart_view
        view.kiai_bands = [(9000, 12000)]
        view.current_time = 10000.0
        view.window_ms = 200.0
        image = view.grab().toImage()
        self.assertGreater(image.width(), 0, "the layer painted without raising")


class SpinButtonTests(unittest.TestCase):
    def test_the_pink_pair_actually_carries_its_glyphs(self):
        """A typed width against the window stylesheet's padding left no room
        for the label, and Qt drew two blank pink squares."""
        dialog = gui.TimingLineDialog(gui.TimingPoint.uninherited_at(0, 180.0))
        dialog.show()
        container = dialog.value_spin.property("pinkRow")
        self.assertIsNotNone(container)
        buttons = container.findChildren(gui.QPushButton)
        self.assertEqual([button.text() for button in buttons], ["+", "-"])
        for button in buttons:
            self.assertGreaterEqual(
                button.width(), button.fontMetrics().horizontalAdvance(button.text()),
            )
        dialog.deleteLater()

    def test_the_buttons_still_step_the_value(self):
        dialog = gui.TimingLineDialog(gui.TimingPoint.uninherited_at(0, 180.0))
        before = dialog.time_spin.value()
        container = dialog.time_spin.property("pinkRow")
        container.findChildren(gui.QPushButton)[0].click()
        self.assertEqual(dialog.time_spin.value(), before + 1)
        dialog.deleteLater()


class BpmLabelTests(_Session, unittest.TestCase):
    def test_layer_three_writes_the_bpm_and_layer_six_no_longer_does(self):
        layers = {
            frame.gimmick_layer: getattr(frame, "chart_view", None) or frame.sv_view
            for frame in self.window._gimmick_views
        }
        self.assertTrue(layers["barline"].show_bpm_labels)
        self.assertFalse(layers["sv_barline"].show_bpm_labels)


class LayerRemovalTests(_Session, unittest.TestCase):
    def test_closing_a_layer_removes_it_from_the_stack(self):
        frame = self.window._gimmick_views[1]
        view = frame.chart_view
        frame.close_button.click()

        self.assertNotIn(frame, self.window._gimmick_views)
        self.assertEqual(
            len(self.window._gimmick_views), len(gui.MainWindow.GIMMICK_LAYERS) - 1
        )
        # ...and it stops being fed the playhead, which would call set_time on
        # a deleted C++ object.
        self.assertNotIn(view, self.window._chart_views)

    def test_re_entering_the_page_brings_every_layer_back(self):
        self.window._gimmick_views[1].close_button.click()
        self._enter(gui.GimmickEntryDialog.USE_CURRENT)
        self.assertEqual(
            len(self.window._gimmick_views), len(gui.MainWindow.GIMMICK_LAYERS)
        )

    def test_a_layer_is_still_focused_afterwards(self):
        self.window._gimmick_views[0].close_button.click()
        self.assertIsNotNone(self.window._active_chart_view)


class SVLayerOwnershipTests(_Session, unittest.TestCase):
    """Task 2's rewritten `_sv_layer_object_times`: one owner per millisecond."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.window._gimmick_pairing.target
        self.window._set_kiai_range(self.target, 0, 30000)

    def test_layer_four_no_longer_sees_a_plain_fake_sliders_line(self):
        """The bug this round fixed: a plain fake slider's own line used to
        leak into the normal-chart SV layer even though no note is there."""
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "regular", 10000)
        times = self.window._sv_layer_object_times("sv_chart", self.document)
        self.assertNotIn(10000 + config.fake_slider_offset_ms, times)

    def test_layer_five_sees_the_fake_slider_and_not_the_shiny(self):
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "regular", 10000)
        self.window._place_gimmick("fake_slider", "shiny", 20000)
        times = self.window._sv_layer_object_times("sv_fake_slider", self.document)
        self.assertIn(10000 + config.fake_slider_offset_ms, times)
        self.assertNotIn(20000 + config.shiny_offset_ms, times)

    def test_layer_five_sees_a_shiny_millisecond_once_it_carries_a_green_line(self):
        """The exception: a shiny's own millisecond stays invisible to layer 5
        normally (its speed is layer 4's business), but once something --
        hand-written or generated -- puts a green line exactly there, it has
        to be editable rather than lost."""
        config = self.window._gimmick_config("fake_slider")
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        at = 10000 + config.shiny_offset_ms
        self.document.timing_points.append(gui.TimingPoint.inherited_at(at, 1.2))
        self.document.timing_points.sort(key=lambda p: p.time)

        times = self.window._sv_layer_object_times("sv_fake_slider", self.document)
        self.assertIn(at, times)

    def test_a_shiny_placement_writes_exactly_one_inherited_point_at_the_snap(self):
        """Task 3: the restore-point append must not stack a second green
        line where the builder already wrote one."""
        self.window._place_gimmick("fake_slider", "shiny", 10000)
        greens = [p for p in self.document.timing_points if p.time == 10000 and p.inherited]
        self.assertEqual(len(greens), 1)


class GimmickLayerWiringTests(_Session, unittest.TestCase):
    """Task 5: the six layers share the editor's zoom and its scrub bar."""

    def test_every_layer_opens_at_the_default_editor_window(self):
        for frame in self.window._gimmick_views:
            view = getattr(frame, "chart_view", None) or frame.sv_view
            self.assertEqual(view.window_ms, self.window.DEFAULT_EDITOR_WINDOW_MS)

    def test_every_layer_shares_the_gimmick_pages_timing_bar(self):
        for frame in self.window._gimmick_views:
            view = getattr(frame, "chart_view", None) or frame.sv_view
            self.assertIs(view.timing_bar, self.window.gimmick_timing_bar)


class MultiFakeSliderToolTests(_Session, unittest.TestCase):
    """Task 1: one click, N independent plain fake sliders."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.window._gimmick_pairing.target
        # Kiai across the whole area these tests place in, to prove the tool
        # cannot accidentally produce a shiny even where one would be legal.
        self.window._set_kiai_range(self.target, 0, 30000)
        self.config = self.window._gimmick_config("fake_slider")

    def _sliders_at(self, time_ms):
        return [
            n for n in self.document.hit_objects
            if gui.MainWindow.is_fake_slider(n) and round(n.time) == time_ms
        ]

    def test_one_structure_per_position_with_the_right_spacing(self):
        self.window._multi_fake_slider_params = (0, 16, 2)
        before = {n.uid for n in self.document.hit_objects}

        self.window._place_gimmick("fake_slider", "multi", 10000)

        positions = list(range(10000, 10016 + 1, 2))
        for position in positions:
            self.assertEqual(
                len(self._sliders_at(position + self.config.fake_slider_offset_ms)), 1,
                f"no slider at {position}",
            )
        added = [n for n in self.document.hit_objects if n.uid not in before]
        self.assertEqual(len(added), len(positions))

    def test_none_of_the_structures_classify_as_shiny_even_inside_kiai(self):
        self.window._multi_fake_slider_params = (0, 16, 2)
        self.window._place_gimmick("fake_slider", "multi", 10000)
        self.assertEqual(self.window._shiny_times(self.document), set())

    def test_a_negative_start_offset_places_structures_before_the_click(self):
        self.window._multi_fake_slider_params = (-8, 0, 2)

        self.window._place_gimmick("fake_slider", "multi", 10000)

        for position in range(9992, 10001, 2):
            self.assertEqual(
                len(self._sliders_at(position + self.config.fake_slider_offset_ms)), 1,
                f"no slider at {position}",
            )
        # And nothing was written past the click -- Start and End were both
        # negative, so the run should not have overshot into positive space.
        self.assertEqual(self._sliders_at(10000 + self.config.fake_slider_offset_ms + 2), [])

    def test_the_whole_run_is_one_undo_step(self):
        self.window._multi_fake_slider_params = (0, 16, 2)
        notes_before = list(self.document.hit_objects)
        points_before = list(self.document.timing_points)

        self.window._place_gimmick("fake_slider", "multi", 10000)
        self.state.history.undo(self.state)

        self.assertEqual(self.document.hit_objects, notes_before)
        self.assertEqual(self.document.timing_points, points_before)

    def test_every_placed_note_gets_its_own_original_index(self):
        self.window._multi_fake_slider_params = (0, 16, 2)
        self.window._place_gimmick("fake_slider", "multi", 10000)
        indices = [n.original_index for n in self.document.hit_objects]
        self.assertEqual(len(indices), len(set(indices)))

    def test_the_tool_sits_between_shiny_and_function(self):
        tools = [tool_id for tool_id, _label in gui.MainWindow.GIMMICK_TOOLSETS["fake_slider"]]
        self.assertEqual(tools[tools.index("shiny") + 1], "multi")
        self.assertEqual(tools[tools.index("multi") + 1], "function")


class MultiFakeSliderDialogWiringTests(_Session, unittest.TestCase):
    """Selecting the tool opens its config every time; cancelling it falls
    back to Select rather than leaving the tool armed with nothing chosen."""

    def test_cancelling_the_config_falls_back_to_select(self):
        class _Stub:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return int(gui.QDialog.Rejected)

        with patch.object(gui, "MultiFakeSliderDialog", _Stub):
            self.window.gimmick_tool_buttons["fake_slider"]["multi"].setChecked(True)

        self.assertTrue(self.window.gimmick_tool_buttons["fake_slider"]["select"].isChecked())
        self.assertFalse(self.window.gimmick_tool_buttons["fake_slider"]["multi"].isChecked())

    def test_accepting_arms_the_tool_with_the_chosen_shape(self):
        class _Stub:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return int(gui.QDialog.Accepted)

            def parameters(self):
                return (5, 25, 4)

        with patch.object(gui, "MultiFakeSliderDialog", _Stub):
            self.window.gimmick_tool_buttons["fake_slider"]["multi"].setChecked(True)

        self.assertEqual(self.window._multi_fake_slider_params, (5, 25, 4))
        self.assertTrue(self.window.gimmick_tool_buttons["fake_slider"]["multi"].isChecked())


class SVPasteByIndexTests(_Session, unittest.TestCase):
    """Task 2: a gimmick SV layer's paste maps onto its own object index."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.window._gimmick_pairing.target
        # The fixture carries its own fake slider (FULL_V14's fixed
        # HitObjects, at 54692) -- layer 5's too, and left in place it is a
        # fifth "remaining" millisecond every one of these tests would have
        # to account for. Stripped so `self.owned` below is exactly the four
        # this class places.
        self.document.hit_objects = [
            n for n in self.document.hit_objects if not gui.MainWindow.is_fake_slider(n)
        ]
        self.window._set_kiai_range(self.target, 0, 30000)
        # Each placement writes a restore (green) line on the slider's own
        # millisecond -- see _gimmick_commands -- which is what layer 5's SV
        # view is made of.
        for t in (10000, 10100, 10200, 10300):
            self.window._place_gimmick("fake_slider", "regular", t)
        self.view = self.window._gimmick_views[4].sv_view
        self.window._active_sv_view = self.view
        self.owned = sorted(self.window._sv_layer_times("sv_fake_slider", self.document))
        self.assertEqual(len(self.owned), 4)

    def _point_at(self, time_ms):
        return next(
            p for p in self.document.timing_points
            if p.inherited and round(p.time) == time_ms
        )

    def test_paste_lands_on_owned_milliseconds_not_a_beat_grid_one(self):
        self.window._edit_sv_point(self.target, self._point_at(self.owned[0]).uid, 2.5)
        self.window._edit_sv_point(self.target, self._point_at(self.owned[1]).uid, 3.5)
        self.view.selected_uids = {
            self._point_at(self.owned[0]).uid, self._point_at(self.owned[1]).uid,
        }
        self.window._copy_sv_points()
        self.assertEqual(len(self.window._sv_clipboard), 2)

        # A cursor between the second and third owned milliseconds, so the
        # third and fourth are the two that remain: the old millisecond-delta
        # path would snap this to the beat grid and offset from there,
        # landing nowhere near either target.
        cursor = (self.owned[1] + self.owned[2]) / 2
        self.view.current_time = cursor
        self.window._paste_sv_points()

        self.assertAlmostEqual(self._point_at(self.owned[2]).sv_multiplier, 2.5)
        self.assertAlmostEqual(self._point_at(self.owned[3]).sv_multiplier, 3.5)
        grid_base = round(max(0.0, gui.snap_time(
            gui.extract_timing_points(self.document), cursor, self.view.snap_divisor,
        )))
        self.assertNotIn(grid_base, (self.owned[2], self.owned[3]))

    def test_a_short_clipboard_pastes_what_fits_against_few_remaining(self):
        # Distinct values, or a match against any restore point would pass.
        for index, t in enumerate(self.owned):
            self.window._edit_sv_point(self.target, self._point_at(t).uid, 1.1 + index / 10)
        self.view.selected_uids = {self._point_at(t).uid for t in self.owned}
        self.window._copy_sv_points()
        self.assertEqual(len(self.window._sv_clipboard), 4)

        # Only the last two owned milliseconds remain past this cursor.
        self.view.current_time = self.owned[2] - 1
        self.window._paste_sv_points()

        self.assertIn("2", self.window.status.text())
        self.assertIn("4", self.window.status.text())
        # The first two clipboard entries (owned[0]'s and owned[1]'s values,
        # in copy order) landed on the two positions that did remain.
        self.assertAlmostEqual(self._point_at(self.owned[2]).sv_multiplier, 1.1)
        self.assertAlmostEqual(self._point_at(self.owned[3]).sv_multiplier, 1.2)

    def test_no_owned_milliseconds_after_the_cursor_toasts_and_does_nothing(self):
        self.view.selected_uids = {self._point_at(self.owned[0]).uid}
        self.window._copy_sv_points()
        points_before = list(self.document.timing_points)

        self.view.current_time = self.owned[-1] + 1
        self.window._paste_sv_points()

        self.assertEqual(self.document.timing_points, points_before)
        self.assertTrue(self.window._toast.isVisible())

    def test_the_editor_pages_own_sv_view_keeps_the_millisecond_delta_path(self):
        """No `gimmick_layer` attribute at all -- it owns every millisecond,
        so the old offset-from-a-snapped-base behaviour is still correct
        there and must not be disturbed."""
        self.window._add_editor_view("sv", self.target)
        sv_view = [f for f in self.window._editor_views if f.view_type == "sv"][-1].sv_view
        self.assertFalse(hasattr(sv_view, "gimmick_layer"))

        source_point = self._point_at(self.owned[0])
        self.window._gimmick_views[4].sv_view.selected_uids = {source_point.uid}
        self.window._active_sv_view = self.window._gimmick_views[4].sv_view
        self.window._copy_sv_points()

        self.window._active_sv_view = sv_view
        sv_view.current_time = 5000.0
        self.window._paste_sv_points()

        expected_base = round(max(0.0, gui.snap_time(
            gui.extract_timing_points(self.document), 5000.0, sv_view.snap_divisor,
        )))
        pasted = self._point_at(expected_base)
        self.assertAlmostEqual(pasted.beat_length, source_point.beat_length)


class IncludeShinyGenerationTests(_Session, unittest.TestCase):
    """Task 3: a per-sweep opt-in that does not change layer 5's ownership."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.window._gimmick_pairing.target
        self.window._set_kiai_range(self.target, 0, 30000)
        self.window._place_gimmick("fake_slider", "regular", 10000)
        self.window._place_gimmick("fake_slider", "shiny", 20000)
        self.config = self.window._gimmick_config("fake_slider")
        self.shiny_at = 20000 + self.config.shiny_offset_ms

    def test_off_by_default_excludes_the_shiny_millisecond(self):
        times = self.window._sv_generation_times(
            self.state, 0, 30000, {"placement": "notes"}, layer_id="sv_fake_slider",
        )
        self.assertNotIn(self.shiny_at, times)

    def test_on_adds_the_shiny_millisecond_for_this_sweep_only(self):
        times = self.window._sv_generation_times(
            self.state, 0, 30000, {"placement": "notes", "include_shiny": True},
            layer_id="sv_fake_slider",
        )
        self.assertIn(self.shiny_at, times)
        # Ownership itself is untouched -- only this one sweep's targets grew.
        self.assertNotIn(
            self.shiny_at, self.window._sv_layer_object_times("sv_fake_slider", self.document)
        )

    def test_the_checkbox_only_appears_on_the_fake_slider_sv_layer(self):
        for layer_id, expected in (("sv_fake_slider", True), ("sv_chart", False), ("sv_barline", False)):
            dialog = gui.SVFunctionDialog(0.0, 1000.0, gimmick_layer=layer_id)
            self.assertEqual(
                gui.is_row_visible(dialog._form_layout, dialog.include_shiny_check), expected,
            )
            self.assertFalse(dialog.parameters()["include_shiny"], "off by default")
            dialog.deleteLater()


class FakeSliderBpmMultiplierConfigTests(_Session, unittest.TestCase):
    """Task 4: the plain fake slider's own BPM control."""

    def test_the_dialog_round_trips_the_field_even_when_hidden(self):
        config = gui.GimmickConfig(fake_slider_bpm_multiplier=2.5)
        # Opened on a layer that does not show this row (barline): the value
        # must still come back unchanged.
        dialog = gui.GimmickConfigDialog(config, "barline", gui.GimmickConfig(), self.window)
        self.assertEqual(dialog.config().fake_slider_bpm_multiplier, 2.5)
        dialog.deleteLater()

        dialog = gui.GimmickConfigDialog(config, "fake_slider", gui.GimmickConfig(), self.window)
        self.assertEqual(dialog.fake_slider_bpm_spin.value(), 2.5)
        dialog.fake_slider_bpm_spin.setValue(4.0)
        self.assertEqual(dialog.config().fake_slider_bpm_multiplier, 4.0)
        dialog.deleteLater()

    def test_the_function_dialog_writes_back_to_the_right_field_by_kind(self):
        target = self.window._gimmick_pairing.target

        class _Stub:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return 1

            def times(self):
                return [10000]

            def object_kind(self):
                return "regular"

            def shiny_count(self):
                return 1

            def bpm_multiplier(self):
                return 3.0

            def deleteLater(self):
                pass

        with patch.object(gui, "FakeSliderFunctionDialog", _Stub):
            self.window._generate_fake_sliders(target, 10000, 10000)

        config = self.window._gimmick_config("fake_slider")
        self.assertEqual(config.fake_slider_bpm_multiplier, 3.0)
        self.assertEqual(config.shiny_bpm_multiplier, 1.0, "the shiny field is untouched")


if __name__ == "__main__":
    unittest.main()
