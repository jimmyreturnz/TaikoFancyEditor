"""Exact-string round-trip for hit objects.

Regenerating [HitObjects] is required for note editing and for placing fake
sliders. Until this test existed, the parser kept only fields[5] and discarded
the rest, so regenerating the section would have silently destroyed every
slider, drumroll and spinner in every map.

Every case here asserts byte-for-byte equality after a parse and re-serialize.
Numeric formatting matters as much as the values: "643" must not become "643.0"
and "-0.0001" must not become "-1e-04".
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from model.hit_object import HitObject
from osu_io.parser import parse_osu
from tests.osu_fixtures import ALL_FIXTURES, write_fixture

# (description, line)
CASES = [
    ("don", "256,192,1000,1,0,0:0:0:0:"),
    ("big don", "256,192,1500,1,4,0:0:0:0:"),
    ("kat whistle", "256,192,2000,1,2,0:0:0:0:"),
    ("kat clap", "256,192,2000,1,8,0:0:0:0:"),
    ("big kat", "256,192,2500,1,12,0:0:0:0:"),
    ("circle without sample", "256,192,3000,1,2"),
    ("new combo circle", "256,192,3000,5,0,0:0:0:0:"),
    ("drumroll", "256,192,3500,2,0,L|624:192,1,280,0|0,0:0|0:0,0:0:0:0:"),
    ("drumroll without sample", "256,192,3500,2,0,L|624:192,1,280"),
    ("bezier drumroll", "100,100,4000,2,0,B|200:200|300:100,2,560.5,0|0|0,0:0|0:0|0:0,0:0:0:0:"),
    ("spinner", "256,192,5000,12,0,7000,0:0:0:0:"),
    ("spinner without sample", "256,192,5000,12,0,7000"),
    ("mania hold", "64,192,6000,128,0,8000:0:0:0:0:"),
    ("fake slider", "256,192,54692,2,12,L|624:192,643,-0.0001"),
    ("sample with filename", "256,192,7000,1,0,1:2:3:40:hit.wav"),
]


class ExactRoundTripTests(unittest.TestCase):
    def test_lines_round_trip_byte_for_byte(self):
        for description, line in CASES:
            with self.subTest(case=description):
                note = HitObject.from_line(line)
                self.assertIsNotNone(note, f"{description}: failed to parse")
                self.assertEqual(note.to_line(""), line)

    def test_fake_slider_keeps_every_field(self):
        """The exact literal the gimmick editor will place."""
        note = HitObject.from_line("256,192,54692,2,12,L|624:192,643,-0.0001")
        self.assertEqual(note.extras, ("L|624:192", "643", "-0.0001"))
        self.assertEqual(note.hit_sample, "", "-0.0001 is not a hit sample")
        self.assertEqual(note.curve, "L|624:192")
        self.assertEqual(note.slides, 643)
        self.assertAlmostEqual(note.length, -0.0001)

    def test_hit_sample_is_detected_not_guessed(self):
        with_sample = HitObject.from_line("256,192,3500,2,0,L|624:192,1,280,0|0,0:0|0:0,0:0:0:0:")
        self.assertEqual(with_sample.hit_sample, "0:0:0:0:")
        self.assertEqual(with_sample.extras[-1], "0:0|0:0")

        without = HitObject.from_line("256,192,3500,2,0,L|624:192,1,280")
        self.assertEqual(without.hit_sample, "")
        self.assertEqual(without.extras, ("L|624:192", "1", "280"))

    def test_spinner_end_time(self):
        self.assertEqual(HitObject.from_line("256,192,5000,12,0,7000,0:0:0:0:").end_time, 7000)
        self.assertEqual(HitObject.from_line("64,192,6000,128,0,8000:0:0:0:0:").end_time, 8000)

    def test_moving_a_note_only_changes_coordinates(self):
        note = HitObject.from_line("256,192,54692,2,12,L|624:192,643,-0.0001")
        note.x, note.y = 100, 50
        self.assertEqual(note.to_line(""), "100,50,54692,2,12,L|624:192,643,-0.0001")

    def test_malformed_lines_are_rejected(self):
        for bad in ("", "256,192", "256,192,1000,1", "a,b,c,d,e"):
            with self.subTest(line=bad):
                self.assertIsNone(HitObject.from_line(bad))


class NoteKindTests(unittest.TestCase):
    """R3: drumrolls and spinners must not be classified as don/kat circles."""

    def test_circle_kinds(self):
        self.assertEqual(HitObject.from_line("256,192,0,1,0").note_kind, "don")
        self.assertEqual(HitObject.from_line("256,192,0,1,4").note_kind, "big_don")
        self.assertEqual(HitObject.from_line("256,192,0,1,2").note_kind, "kat")
        self.assertEqual(HitObject.from_line("256,192,0,1,8").note_kind, "kat")
        self.assertEqual(HitObject.from_line("256,192,0,1,12").note_kind, "big_kat")

    def test_slider_is_a_drumroll_regardless_of_hitsound(self):
        note = HitObject.from_line("256,192,54692,2,12,L|624:192,643,-0.0001")
        self.assertEqual(note.note_kind, "drumroll")

    def test_is_kat_stays_ungated_for_behavior_compatibility(self):
        """gui.py splits Don/Kat transformation groups on is_kat.

        Gating it on is_circle would move every drumroll into the Don group and
        change existing transformation output, so the raw hitsound test is kept
        and note_kind carries the object-type distinction instead.
        """
        note = HitObject.from_line("256,192,54692,2,12,L|624:192,643,-0.0001")
        self.assertTrue(note.is_kat)
        self.assertTrue(note.is_finisher)
        self.assertEqual(note.note_kind, "drumroll")

    def test_spinner_is_a_denden(self):
        self.assertEqual(HitObject.from_line("256,192,5000,12,0,7000").note_kind, "denden")

    def test_new_combo_bit_does_not_change_the_kind(self):
        self.assertEqual(HitObject.from_line("256,192,0,5,0").note_kind, "don")
        self.assertTrue(HitObject.from_line("256,192,0,5,0").is_new_combo)


class FixtureRoundTripTests(unittest.TestCase):
    def test_every_fixture_hit_object_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ALL_FIXTURES:
                with self.subTest(fixture=name):
                    source = write_fixture(Path(directory), name)
                    document = parse_osu(source)
                    raw = source.read_bytes().decode(document.encoding).splitlines()
                    for note in document.hit_objects:
                        self.assertEqual(
                            note.to_line(""),
                            raw[note.source_line_index],
                            f"{name}: line {note.source_line_index + 1} did not round-trip",
                        )

    def test_full_fixture_note_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            document = parse_osu(write_fixture(Path(directory), "full_v14"))
            kinds = [note.note_kind for note in document.hit_objects]
            self.assertEqual(
                kinds,
                ["don", "big_don", "kat", "big_kat", "kat", "drumroll", "denden", "drumroll"],
            )


if __name__ == "__main__":
    unittest.main()
