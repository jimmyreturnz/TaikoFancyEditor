"""M5 gimmick editor: pairing index, base timing and the structure builders.

The structure assertions are deliberately written as the literal file lines a
placement produces. These structures are the whole feature -- a barline note is
*only* its red lines -- so a test that checked counts or ranges would pass while
drawing the wrong note.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import gimmick_session as gs
from osu_io.timing import TimingPoint, serialize_timing_point

# 180 BPM from 0, the base timing every case below is placed against.
BASE = [TimingPoint.uninherited_at(0, 180.0)]


def lines(points) -> list[str]:
    return [serialize_timing_point(point, "") for point in points]


class ConfigTests(unittest.TestCase):
    def test_positive_fake_slider_length_is_rejected(self):
        """A positive length is a real, hittable drumroll -- the opposite of the
        object the tool exists to place."""
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(fake_slider_length=1.0)
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(fake_slider_length=0.0)

    def test_defaults_are_the_agreed_ones(self):
        config = gs.GimmickConfig()
        self.assertEqual(config.gimmick_bpm, 60000.0)
        self.assertEqual(config.spacing_ms, 1)
        # Kat's three independently configurable pairs default to what the
        # old derived formula (n, n+2, n+4 with n=1) used to produce, so a
        # map built against today's shipped defaults sees no change.
        self.assertEqual(config.kat_spacing1_ms, 1)
        self.assertEqual(config.kat_spacing2_ms, 3)
        self.assertEqual(config.kat_spacing3_ms, 5)
        self.assertEqual(config.fake_slider_length, -1.0)
        self.assertEqual(config.red_line_offset_ms, 0)

    def test_a_spacing_below_one_is_rejected(self):
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(spacing_ms=0)
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(kat_spacing1_ms=0)
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(kat_spacing2_ms=0)
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(kat_spacing3_ms=0)

    def test_duplicate_kat_spacings_are_rejected(self):
        """Two equal values would write two red lines onto one millisecond,
        where only the first is meaningful -- a quietly thinner note than the
        three spin boxes promised."""
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(kat_spacing1_ms=3, kat_spacing2_ms=3)
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(kat_spacing2_ms=5, kat_spacing3_ms=5)


class BarlineStructureTests(unittest.TestCase):
    def test_don_is_one_gimmick_line_and_a_mirrored_pair(self):
        points, notes = gs.barline_note(10000, BASE, gs.GimmickConfig(), kind="don")
        # The bars are drawn for a note the player actually hits, so one goes
        # on the snap unless place_notes says otherwise.
        self.assertEqual([note.time for note in notes], [10000])
        self.assertEqual(lines(points), [
            "9999,333.3333333333333,4,0,0,100,1,0",
            "10000,1,4,0,0,100,1,0",
            "10001,333.3333333333333,4,0,0,100,1,0",
        ])

    def test_place_notes_off_writes_the_bars_and_nothing_else(self):
        """A gimmick that only wants the barlines as scenery."""
        points, notes = gs.barline_note(
            10000, BASE, gs.GimmickConfig(place_notes=False), kind="don",
        )
        self.assertEqual(notes, [])
        self.assertEqual(len(points), 3, "the bars are unaffected")

    def test_kat_mirrors_n_n2_n4_around_the_gimmick_line(self):
        points, _ = gs.barline_note(10000, BASE, gs.GimmickConfig(), kind="kat")
        self.assertEqual([point.time for point in points], [
            9995.0, 9997.0, 9999.0, 10000.0, 10001.0, 10003.0, 10005.0,
        ])
        # Seven uninherited points, so seven barlines: the gimmick line plus
        # six restores whose only job is to draw a bar each.
        self.assertTrue(all(point.uninherited for point in points))
        self.assertEqual(sum(1 for point in points if point.bpm == 60000.0), 1)

    def test_dons_own_spacing_widens_it_without_touching_kat(self):
        """Don's spacing is `spacing_ms` alone now -- it no longer feeds a
        formula Kat's pairs are derived from."""
        config = gs.GimmickConfig(spacing_ms=3)
        points, _ = gs.barline_note(10000, BASE, config, kind="don")
        self.assertEqual([point.time for point in points], [9997.0, 10000.0, 10003.0])

    def test_kats_three_pairs_are_independently_configurable(self):
        """Each pair lands exactly where its own field asks, free of the old
        n/n+2/n+4 progression -- so hand-layering several structures on one
        millisecond region no longer forces the three pairs' widths apart in
        lockstep."""
        config = gs.GimmickConfig(kat_spacing1_ms=2, kat_spacing2_ms=6, kat_spacing3_ms=9)
        points, _ = gs.barline_note(10000, BASE, config, kind="kat")
        self.assertEqual([point.time for point in points], [
            9991.0, 9994.0, 9998.0, 10000.0, 10002.0, 10006.0, 10009.0,
        ])

    def test_restores_take_the_base_bpm_in_force_not_the_first_one(self):
        base = [TimingPoint.uninherited_at(0, 180.0), TimingPoint.uninherited_at(5000, 240.0)]
        points, _ = gs.barline_note(10000, base, gs.GimmickConfig(), kind="don")
        restores = [point for point in points if point.time != 10000]
        for point in restores:
            self.assertAlmostEqual(point.bpm, 240.0)

    def test_gimmick_bpm_is_configurable(self):
        config = gs.GimmickConfig(gimmick_bpm=30000.0)
        points, _ = gs.barline_note(10000, BASE, config, kind="don")
        centre = next(point for point in points if point.time == 10000)
        self.assertAlmostEqual(centre.bpm, 30000.0)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(gs.GimmickConfigError):
            gs.barline_note(0, BASE, gs.GimmickConfig(), kind="slider")


class FakeSliderTests(unittest.TestCase):
    def test_a_plain_fake_slider_writes_one_line_at_the_slider_not_the_gimmick_bpm(self):
        """No note, so nothing to hide -- a plain fake slider is one red line
        on the object itself, at the chart's own BPM, never the 60000 squash."""
        points, notes = gs.fake_slider(10000, BASE, gs.GimmickConfig())
        # Effects 8: a fake slider's line omits the barline it would otherwise
        # draw. On by default -- see GimmickConfig.omit_barline.
        # +2, not +1: the shiny note owns +1 (see DEFAULT_SHINY_OFFSET_MS).
        self.assertEqual(lines(points), [
            "10002,333.3333333333333,4,0,0,100,1,8",
        ])
        self.assertEqual(len(notes), 1)
        self.assertNotEqual(points[0].bpm, gs.DEFAULT_GIMMICK_BPM)

    def test_omit_barline_can_be_switched_off(self):
        points, _ = gs.fake_slider(10000, BASE, gs.GimmickConfig(omit_barline=False))
        self.assertEqual(lines(points), [
            "10002,333.3333333333333,4,0,0,100,1,0",
        ])

    def test_a_kat_fake_slider_is_the_big_one(self):
        """A drumroll is yellow whatever is under it, so size is the only thing
        it can say about which button the structure means."""
        _, don = gs.fake_slider(10000, BASE, gs.GimmickConfig(), kind="don")
        _, kat = gs.fake_slider(10000, BASE, gs.GimmickConfig(), kind="kat")
        _, plain = gs.fake_slider(10000, BASE, gs.GimmickConfig())
        self.assertTrue(kat[1].is_finisher)
        self.assertFalse(don[1].is_finisher)
        self.assertFalse(plain[0].is_finisher)

    def test_note_is_a_slider_with_a_negative_length(self):
        # +2: every object this layer writes sits on its restore line, which is
        # what lets the offset say whether it is a fake slider or a shiny.
        _, notes = gs.fake_slider(10000, BASE, gs.GimmickConfig())
        self.assertEqual(notes[0].to_line(""), "256,192,10002,2,0,L|356:192,1,-1,0:0:0:0:")
        self.assertTrue(notes[0].is_slider)
        self.assertLess(notes[0].length, 0)

    def test_don_and_kat_write_the_note_the_button_names(self):
        """The note on the snap is a real don or kat. The tools' small/big
        reading lives in the hover preview -- a fake slider is a drumroll and
        has no don/kat of its own to carry it."""
        _, don = gs.fake_slider(10000, BASE, gs.GimmickConfig(), kind="don")
        _, kat = gs.fake_slider(10000, BASE, gs.GimmickConfig(), kind="kat")
        self.assertEqual(don[0].note_kind, "don")
        self.assertEqual(kat[0].note_kind, "kat")
        # ...and the fake slider itself follows each of them.
        self.assertTrue(don[1].is_slider)
        self.assertTrue(kat[1].is_slider)

    def test_a_don_puts_the_gimmick_line_on_the_note_not_on_the_slider(self):
        """Only the note is inside the squashed section; the slider sits on the
        restore line, so it is drawn at the chart's own BPM."""
        config = gs.GimmickConfig(fake_slider_offset_ms=3)
        points, notes = gs.fake_slider(10000, BASE, config, kind="don")

        self.assertEqual([point.time for point in points], [10000.0, 10000.0, 10003.0])
        self.assertEqual(points[0].bpm, config.gimmick_bpm)
        self.assertAlmostEqual(points[2].bpm, 180.0)
        self.assertEqual([note.time for note in notes], [10000, 10003])

    def test_a_don_writes_three_points_squash_first(self):
        """The squash (gimmick BPM), then the SV that finishes taking the note
        off screen, then the restore the fake slider sits on -- in that order,
        because the green line must sort after the red one sharing its
        millisecond."""
        points, _ = gs.fake_slider(10000, BASE, gs.GimmickConfig(), kind="don")
        self.assertEqual(len(points), 3)
        self.assertTrue(points[0].uninherited)
        self.assertAlmostEqual(points[0].bpm, gs.DEFAULT_GIMMICK_BPM)
        self.assertEqual(points[0].time, 10000.0)
        self.assertFalse(points[1].uninherited)
        self.assertEqual(points[1].time, 10000.0)
        self.assertTrue(points[2].uninherited)
        self.assertEqual(points[2].time, 10002.0)
        self.assertNotAlmostEqual(points[2].bpm, gs.DEFAULT_GIMMICK_BPM)

    def test_fake_slider_bpm_multiplier_retimes_only_the_restore_line(self):
        """It governs the line the fake slider itself sits on -- never the
        squash, which stays at `gimmick_bpm` because hiding the note is the
        whole point of it."""
        config = gs.GimmickConfig(fake_slider_bpm_multiplier=0.5)
        points, _ = gs.fake_slider(10000, BASE, config, kind="don")
        self.assertAlmostEqual(points[0].bpm, config.gimmick_bpm)
        self.assertAlmostEqual(points[2].bpm, 90.0)  # 180 * 0.5

        plain_points, _ = gs.fake_slider(10000, BASE, config)
        self.assertAlmostEqual(plain_points[0].bpm, 90.0)

    def test_a_don_stacks_the_gimmick_sv_on_its_red_line(self):
        """The green line comes *after* the red one sharing the millisecond:
        osu! resolves a tie by file order, so the other way round the red line's
        own reset to 1.0x would win."""
        config = gs.GimmickConfig(fake_slider_sv=12.0)
        points, _ = gs.fake_slider(10000, BASE, config, kind="kat")

        self.assertTrue(points[0].uninherited)
        self.assertFalse(points[1].uninherited)
        self.assertEqual(points[1].time, 10000.0)
        self.assertAlmostEqual(points[1].sv_multiplier, 12.0)

    def test_a_plain_fake_slider_carries_no_gimmick_sv(self):
        """It has no note of its own to hide -- the slider *is* the object,
        and there is nothing to squash, so there is only the one line."""
        points, _ = gs.fake_slider(10000, BASE, gs.GimmickConfig())
        self.assertEqual(len(points), 1)
        self.assertTrue(all(point.uninherited for point in points))

    def test_configured_length_is_written_verbatim(self):
        config = gs.GimmickConfig(fake_slider_length=-0.0001)
        _, notes = gs.fake_slider(10000, BASE, config)
        self.assertEqual(notes[0].extras[2], "-0.0001")

    def test_placement_offset_does_not_apply(self):
        """The offset belongs to the plain red-line tool alone."""
        config = gs.GimmickConfig(red_line_offset_ms=-3)
        points, notes = gs.fake_slider(10000, BASE, config)
        self.assertEqual([point.time for point in points], [10002.0])
        self.assertEqual(notes[0].time, 10002)


class MirroredBarlineTests(unittest.TestCase):
    """Bars either side of the note, or trailing it."""

    def test_mirrored_is_the_default(self):
        points, _ = gs.barline_note(10000, BASE, gs.GimmickConfig(), kind="don")
        self.assertEqual([int(p.time) - 10000 for p in points], [-1, 0, 1])

    def test_forward_only_writes_the_squash_and_its_restores_after_it(self):
        """The owner's map: a squash on the note and one restore a millisecond
        later, nothing before it."""
        config = gs.GimmickConfig(mirror_don_lines=False)
        points, _ = gs.barline_note(87124, BASE, config, kind="don")
        self.assertEqual([int(p.time) - 87124 for p in points], [0, 1])
        self.assertAlmostEqual(points[0].bpm, config.gimmick_bpm)

    def test_forward_only_kat_keeps_all_three_spacings(self):
        points, _ = gs.barline_note(
            10000, BASE, gs.GimmickConfig(mirror_kat_lines=False), kind="kat",
        )
        self.assertEqual([int(p.time) - 10000 for p in points], [0, 1, 3, 5])

    def test_the_two_kinds_mirror_independently(self):
        """The complaint this comes from: one box made a centred Don force a
        centred Kat, when the whole reason the spacings are separate is that
        Don and Kat are separate structures layered on one region."""
        config = gs.GimmickConfig(mirror_don_lines=True, mirror_kat_lines=False)
        don, _ = gs.barline_note(10000, BASE, config, kind="don")
        kat, _ = gs.barline_note(10000, BASE, config, kind="kat")
        self.assertEqual([int(p.time) - 10000 for p in don], [-1, 0, 1])
        self.assertEqual([int(p.time) - 10000 for p in kat], [0, 1, 3, 5])

        flipped = gs.GimmickConfig(mirror_don_lines=False, mirror_kat_lines=True)
        don, _ = gs.barline_note(10000, BASE, flipped, kind="don")
        kat, _ = gs.barline_note(10000, BASE, flipped, kind="kat")
        self.assertEqual([int(p.time) - 10000 for p in don], [0, 1])
        self.assertEqual([int(p.time) - 10000 for p in kat],
                         [-5, -3, -1, 0, 1, 3, 5])

    def test_custom_spacings_survive_forward_only(self):
        config = gs.GimmickConfig(
            mirror_kat_lines=False, kat_spacing1_ms=2, kat_spacing2_ms=6,
            kat_spacing3_ms=9,
        )
        points, _ = gs.barline_note(10000, BASE, config, kind="kat")
        self.assertEqual([int(p.time) - 10000 for p in points], [0, 2, 6, 9])


class ShinyNoteTests(unittest.TestCase):
    """A stack of fake sliders on one millisecond, which is what reads white."""

    def test_a_shiny_writes_its_configured_stack_at_its_own_offset(self):
        config = gs.GimmickConfig()
        points, notes = gs.fake_slider(10000, BASE, config, shiny=True)
        self.assertEqual(len(notes), config.shiny_count)
        self.assertTrue(all(note.is_slider for note in notes))
        self.assertEqual({note.time for note in notes}, {10000 + config.shiny_offset_ms})
        # The note the glow decorates stays on the snap, so both its lines --
        # red then green -- sit there too, not on the stack's own offset.
        self.assertEqual([point.time for point in points], [10000.0, 10000.0])
        self.assertTrue(points[0].uninherited)
        self.assertFalse(points[1].uninherited)
        self.assertNotAlmostEqual(points[0].bpm, gs.DEFAULT_GIMMICK_BPM)

    def test_the_defaults_keep_the_two_structures_apart(self):
        config = gs.GimmickConfig()
        self.assertEqual(config.fake_slider_offset_ms, 2)
        self.assertEqual(config.shiny_offset_ms, 1)
        self.assertEqual(config.shiny_count, 3)
        self.assertFalse(gs.shiny_collides(config))

    def test_an_equal_offset_is_reported_as_a_collision(self):
        self.assertTrue(gs.shiny_collides(gs.GimmickConfig(shiny_offset_ms=2)))

    def test_a_stack_of_one_is_allowed(self):
        """The offset says it is a shiny, not the stack size -- a plain fake
        slider gets stacked too, to brighten it."""
        self.assertEqual(gs.GimmickConfig(shiny_count=1).shiny_count, 1)
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(shiny_count=0)

    def test_the_bpm_multiplier_retimes_the_red_line_and_pays_it_back_in_sv(self):
        """200 BPM at 1.2x becomes 100 BPM at 2.4x -- the same speed, written
        in numbers a gimmick has room to move in."""
        base = [
            TimingPoint.uninherited_at(0, 200.0),
            TimingPoint.inherited_at(5, 1.2),
        ]
        config = gs.GimmickConfig(shiny_bpm_multiplier=0.5)
        points, _ = gs.fake_slider(10000, base, config, shiny=True)
        red_line = points[0]
        self.assertAlmostEqual(red_line.bpm, 100.0)
        self.assertAlmostEqual(points[-1].sv_multiplier, 2.4)
        self.assertEqual(points[-1].time, red_line.time)
        self.assertTrue(points[-1].inherited, "the green line goes after the red one")

    def test_the_green_line_is_always_written_even_at_multiplier_one(self):
        """It is the handle the SV layer is made of, not only a correction for
        a retimed line -- so a shiny at the default multiplier still gets one,
        simply restating the SV already in force."""
        base = [
            TimingPoint.uninherited_at(0, 200.0),
            TimingPoint.inherited_at(5, 1.2),
        ]
        points, _ = gs.fake_slider(10000, base, gs.GimmickConfig(), shiny=True)
        self.assertEqual(len(points), 2)
        self.assertFalse(points[1].uninherited)
        self.assertAlmostEqual(points[1].sv_multiplier, 1.2)
        self.assertFalse(any(point.bpm == gs.DEFAULT_GIMMICK_BPM for point in points if point.uninherited))

    def test_copies_overrides_the_configured_stack(self):
        _, notes = gs.fake_slider(10000, BASE, gs.GimmickConfig(), shiny=True, copies=7)
        self.assertEqual(len(notes), 7)

    def test_a_don_shiny_still_writes_the_hittable_note(self):
        config = gs.GimmickConfig()
        _, notes = gs.fake_slider(10000, BASE, config, kind="don", shiny=True)
        self.assertEqual(notes[0].note_kind, "don")
        self.assertEqual(len(notes), 1 + config.shiny_count)

    def test_owner_file_case_185_bpm_with_inherited_1_15x_at_5ms(self):
        """The owner's real file: 185 BPM from 0, an inherited 1.15x point at
        5ms, and a shiny placed at 65393 with `shiny_bpm_multiplier=0.5`.

        92.5 BPM = 185 x 0.5; SV 2.3x = 1.15 / 0.5. `assertAlmostEqual` on the
        numbers rather than a literal line: `repr(float)` carries more digits
        of this particular division than the owner's file happened to show,
        and that trailing noise is not what this test is guarding.
        """
        base = [
            TimingPoint.uninherited_at(0, 185.0),
            TimingPoint.inherited_at(5, 1.15),
        ]
        config = gs.GimmickConfig(shiny_bpm_multiplier=0.5)
        points, notes = gs.fake_slider(65393, base, config, shiny=True)

        self.assertAlmostEqual(points[0].bpm, 92.5)
        self.assertAlmostEqual(points[1].sv_multiplier, 2.3)
        # Red before green, both on the note's own millisecond, the red one
        # carrying omit_first_barline (never the green one).
        self.assertEqual([point.time for point in points], [65393.0, 65393.0])
        self.assertTrue(points[0].uninherited and points[0].omit_first_barline)
        self.assertFalse(points[1].uninherited or points[1].omit_first_barline)
        self.assertFalse(any(point.bpm == gs.DEFAULT_GIMMICK_BPM for point in points if point.uninherited))

        self.assertEqual(len(notes), 3)
        self.assertEqual({note.time for note in notes}, {65394})


class RedLineTests(unittest.TestCase):
    def test_a_custom_bpm_overrides_the_base_timing(self):
        """An uninherited point's BPM is its scroll speed, so the red line tool
        is a standalone speed change once it stops echoing the chart."""
        points, _ = gs.red_line(10000, 180.0, gs.GimmickConfig(red_line_bpm=45.0))
        self.assertAlmostEqual(points[0].bpm, 45.0)

    def test_no_custom_bpm_means_the_bpm_it_was_handed(self):
        points, _ = gs.red_line(10000, 180.0, gs.GimmickConfig())
        self.assertAlmostEqual(points[0].bpm, 180.0)

    def test_a_non_positive_custom_bpm_is_refused(self):
        with self.assertRaises(gs.GimmickConfigError):
            gs.GimmickConfig(red_line_bpm=0.0)

    def test_offset_shifts_the_point_off_the_snap(self):
        config = gs.GimmickConfig(red_line_offset_ms=-1)
        points, notes = gs.red_line(10000, 180.0, config)
        self.assertEqual(notes, [])
        self.assertEqual(points[0].time, 9999.0)
        self.assertAlmostEqual(points[0].bpm, 180.0)

    def test_zero_offset_lands_on_the_snap(self):
        points, _ = gs.red_line(10000, 200.0, gs.GimmickConfig())
        self.assertEqual(points[0].time, 10000.0)


class SVRestoreTests(unittest.TestCase):
    """A gimmick's red lines reset SV to 1.0x; this is what hands it back."""

    def test_the_sv_in_force_is_restated_at_the_end_of_the_cluster(self):
        points = [TimingPoint.uninherited_at(0, 180.0), TimingPoint.inherited_at(5000, 1.6)]
        restore = gs.sv_restore_point(points, 9999, 10006)
        self.assertIsNotNone(restore)
        self.assertTrue(restore.inherited)
        self.assertAlmostEqual(restore.sv_multiplier, 1.6)
        self.assertEqual(restore.time, 10006)

    def test_a_chart_already_at_one_gets_a_line_saying_so(self):
        """It changes nothing by itself -- and that is the point: it is the
        handle the structure's SV layer is made of, and a layer matches its
        green lines by exact millisecond."""
        points = [TimingPoint.uninherited_at(0, 180.0)]
        restore = gs.sv_restore_point(points, 9999, 10006)
        self.assertTrue(restore.inherited)
        self.assertAlmostEqual(restore.sv_multiplier, 1.0)
        self.assertEqual(restore.time, 10006)

    def test_an_uninherited_point_later_than_the_sv_cancels_it(self):
        points = [
            TimingPoint.uninherited_at(0, 180.0),
            TimingPoint.inherited_at(5000, 1.6),
            TimingPoint.uninherited_at(8000, 200.0),
        ]
        self.assertAlmostEqual(gs.sv_restore_point(points, 9999, 10006).sv_multiplier, 1.0)

    def test_hitsound_settings_come_from_the_point_being_restored(self):
        source = TimingPoint.inherited_at(5000, 1.6)
        source.volume, source.sample_set = 35, 2
        restore = gs.sv_restore_point(
            [TimingPoint.uninherited_at(0, 180.0), source], 9999, 10006
        )
        self.assertEqual((restore.volume, restore.sample_set), (35, 2))


class BaseTimingTests(unittest.TestCase):
    def test_snapshot_is_detached_from_the_document(self):
        original = [TimingPoint.uninherited_at(0, 180.0)]
        snapshot = gs.snapshot_timing(original)
        original[0].beat_length = 1.0  # what a gimmick placement would do
        self.assertAlmostEqual(snapshot[0].bpm, 180.0)

    def test_gimmick_lines_are_detected_but_not_removed(self):
        dirty = [TimingPoint.uninherited_at(0, 180.0), TimingPoint.uninherited_at(500, 60000.0)]
        self.assertTrue(gs.looks_gimmicked(dirty))
        self.assertFalse(gs.looks_gimmicked([TimingPoint.uninherited_at(0, 180.0)]))
        # Warn and continue: the snapshot keeps every point it was given.
        self.assertEqual(len(gs.snapshot_timing(dirty)), 2)


class IndexTests(unittest.TestCase):
    def test_pairing_round_trips_through_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gimmick_index.json"
            pairing = gs.GimmickPairing(
                Path(directory) / "song [Oni] [Gimmick].osu",
                [TimingPoint.uninherited_at(0, 180.0), TimingPoint.inherited_at(500, 1.5)],
            )
            gs.save_index(path, {"source.osu": pairing})

            loaded = gs.load_index(path)
            self.assertEqual(loaded["source.osu"].target, pairing.target)
            self.assertEqual(lines(loaded["source.osu"].base_timing), lines(pairing.base_timing))

    def test_a_corrupt_index_reads_as_empty_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gimmick_index.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(gs.load_index(path), {})
            self.assertEqual(gs.load_index(Path(directory) / "missing.json"), {})

    def test_a_version_bump_discards_the_old_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gimmick_index.json"
            path.write_text('{"version": 0, "pairings": {"a": {"target": "b"}}}', encoding="utf-8")
            self.assertEqual(gs.load_index(path), {})

    def test_gimmick_path_and_version_append_the_suffix(self):
        source = Path("C:/songs/1 Artist - Title (Creator) [Oni].osu")
        self.assertEqual(
            gs.gimmick_path_for(source).name,
            "1 Artist - Title (Creator) [Oni] [Gimmick].osu",
        )
        self.assertEqual(gs.gimmick_version_for("Oni"), "Oni [Gimmick]")


class CarryActiveStateTests(unittest.TestCase):
    """Kiai and hitsound volume both live on the active timing point, so a
    gimmick line written without them redefines both for everything after it."""

    def setUp(self):
        quiet = TimingPoint.inherited_at(5000, 1.0, kiai=True)
        quiet.volume = 20
        self.document = [
            TimingPoint.uninherited_at(0, 180.0),
            quiet,
            TimingPoint.inherited_at(20000, 1.0),
        ]

    def test_a_line_inside_a_kiai_section_carries_kiai(self):
        points, _ = gs.fake_slider(10000, BASE, gs.GimmickConfig(), kind="kat")
        gs.carry_active_state(points, self.document)
        self.assertTrue(all(point.kiai for point in points))

    def test_a_line_outside_one_does_not(self):
        points, _ = gs.fake_slider(30000, BASE, gs.GimmickConfig(), kind="kat")
        gs.carry_active_state(points, self.document)
        self.assertFalse(any(point.kiai for point in points))

    def test_the_omit_barline_bit_survives_it(self):
        """Both live in `effects`; setting one must not clear the other."""
        points, _ = gs.fake_slider(10000, BASE, gs.GimmickConfig())
        gs.carry_active_state(points, self.document)
        self.assertTrue(all(point.omit_first_barline for point in points))
        self.assertEqual(points[0].effects, 9)

    def test_a_line_inside_a_quiet_section_keeps_it_quiet(self):
        """The regression: TimingPoint defaults to 100, so a structure placed
        in a 20% section used to slam the rest of that section to full."""
        points, _ = gs.fake_slider(10000, BASE, gs.GimmickConfig(), kind="kat")
        gs.carry_active_state(points, self.document)
        self.assertEqual([point.volume for point in points], [20] * len(points))

    def test_the_volume_is_read_per_point_not_once(self):
        """A structure can straddle a volume change; each line takes the value
        in force where it actually lands."""
        points = [
            TimingPoint.uninherited_at(10000, 180.0),
            TimingPoint.uninherited_at(25000, 180.0),
        ]
        gs.carry_active_state(points, self.document)
        self.assertEqual([point.volume for point in points], [20, 100])


class OscillatingSeriesTests(unittest.TestCase):
    def test_per_pair_linear_matches_the_worked_example(self):
        values = gs.oscillating_series(1.00, 0.06, 7, lambda t: t, per_pair=True)
        self.assertEqual(
            [round(value, 4) for value in values],
            [1.00, 1.02, 0.98, 1.04, 0.96, 1.06, 0.94],
        )

    def test_per_point_linear_opens_the_fan_on_every_point(self):
        values = gs.oscillating_series(1.00, 0.06, 7, lambda t: t)
        self.assertEqual(
            [round(value, 4) for value in values],
            [1.00, 1.01, 0.98, 1.03, 0.96, 1.05, 0.94],
        )

    def test_both_modes_reach_the_same_total_difference(self):
        """The mode changes how the fan opens, never how wide it ends up."""
        for ease in (lambda t: t, lambda t: t ** 1.6):
            for count in (5, 6, 7, 12):
                for mode in (True, False):
                    values = gs.oscillating_series(1.4, 0.12, count, ease, per_pair=mode)
                    widest = max(abs(value - 1.4) for value in values)
                    self.assertAlmostEqual(widest, 0.12)

    def test_exp16_reaches_the_total_difference_only_at_the_end(self):
        values = gs.oscillating_series(1.40, 0.12, 7, lambda t: t ** 1.6)
        self.assertEqual(
            [round(value, 3) for value in values],
            [1.400, 1.407, 1.379, 1.440, 1.337, 1.490, 1.280],
        )

    def test_first_point_sits_on_the_base(self):
        for count in (1, 2, 7, 20):
            values = gs.oscillating_series(1.4, 0.12, count, lambda t: t ** 1.6)
            self.assertEqual(len(values), count)
            self.assertAlmostEqual(values[0], 1.4)

    def test_sides_alternate_and_stay_positive(self):
        values = gs.oscillating_series(1.0, 0.5, 9, lambda t: t, per_pair=True)
        for index, value in enumerate(values[1:], start=1):
            if index % 2:
                self.assertGreater(value, 1.0)
            else:
                self.assertLess(value, 1.0)
        self.assertTrue(all(value > 0 for value in values))

    def test_empty_range_is_empty(self):
        self.assertEqual(gs.oscillating_series(1.0, 0.1, 0, lambda t: t), [])


if __name__ == "__main__":
    unittest.main()
