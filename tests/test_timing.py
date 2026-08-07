"""Tests for the structured timing-point model."""
from __future__ import annotations

import unittest

from osu_io.timing import (
    EFFECT_KIAI,
    EFFECT_OMIT_FIRST_BARLINE,
    TimingPoint,
    TimingValidationError,
    active_uninherited_at,
    iter_barline_times,
    kiai_spans,
    parse_timing_line,
    parse_timing_points,
    serialize_timing_point,
    sorted_by_time,
    sv_at,
    validate_timing_points,
)
from tests.osu_fixtures import FULL_V14, LEGACY_V4


def lines_of(raw: bytes) -> list[str]:
    return raw.decode("utf-8").splitlines(keepends=True)


class ParseTests(unittest.TestCase):
    def test_full_line(self):
        point = parse_timing_line("2000,-133.33333333333334,4,1,0,60,0,1")
        self.assertIsNotNone(point)
        self.assertEqual(point.time, 2000.0)
        self.assertFalse(point.uninherited)
        self.assertTrue(point.kiai)
        self.assertEqual(point.volume, 60)
        self.assertAlmostEqual(point.sv_multiplier, 0.75)

    def test_legacy_two_field_line_parses(self):
        """R6: requiring seven fields made v4 maps yield zero timing points."""
        point = parse_timing_line("500,300")
        self.assertIsNotNone(point)
        self.assertEqual(point.time, 500.0)
        self.assertEqual(point.beat_length, 300.0)
        self.assertTrue(point.uninherited, "a two-field legacy line is uninherited")
        self.assertEqual(point.meter, 4)
        self.assertEqual(point.volume, 100)
        self.assertAlmostEqual(point.bpm, 200.0)

    def test_legacy_fixture_yields_points(self):
        points = parse_timing_points(lines_of(LEGACY_V4))
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0].bpm, 200.0)
        self.assertAlmostEqual(points[1].bpm, 250.0)

    def test_comments_and_blanks_are_skipped(self):
        self.assertIsNone(parse_timing_line("// a comment"))
        self.assertIsNone(parse_timing_line("   "))
        self.assertIsNone(parse_timing_line("nonsense"))
        self.assertIsNone(parse_timing_line("1000"))

    def test_only_the_timing_section_is_read(self):
        points = parse_timing_points(lines_of(FULL_V14))
        self.assertEqual(len(points), 4)
        self.assertEqual([int(p.time) for p in points], [0, 2000, 4000, 6000])
        # Hit-object and colour lines also contain commas and must not appear.
        self.assertTrue(all(p.time <= 6000 for p in points))

    def test_source_line_index_is_recorded(self):
        points = parse_timing_points(lines_of(FULL_V14))
        raw = lines_of(FULL_V14)
        for point in points:
            self.assertTrue(raw[point.source_line_index].startswith(str(int(point.time))))


class SerializeTests(unittest.TestCase):
    def test_round_trip_is_byte_exact(self):
        for text in (
            "0,500,4,1,0,60,1,0",
            "2000,-133.33333333333334,4,1,0,60,0,1",
            "4000,-50,4,1,0,60,0,0",
            "6000,400,4,1,0,70,1,8",
        ):
            with self.subTest(line=text):
                point = parse_timing_line(text)
                self.assertEqual(serialize_timing_point(point, ""), text)

    def test_whole_section_round_trips(self):
        for point in parse_timing_points(lines_of(FULL_V14)):
            rebuilt = parse_timing_line(serialize_timing_point(point, ""))
            self.assertEqual(
                (rebuilt.time, rebuilt.beat_length, rebuilt.uninherited_flag, rebuilt.effects),
                (point.time, point.beat_length, point.uninherited_flag, point.effects),
            )


class DerivedValueTests(unittest.TestCase):
    def test_sv_multiplier(self):
        self.assertAlmostEqual(TimingPoint(0, -100, uninherited_flag=0).sv_multiplier, 1.0)
        self.assertAlmostEqual(TimingPoint(0, -50, uninherited_flag=0).sv_multiplier, 2.0)
        self.assertAlmostEqual(TimingPoint(0, -200, uninherited_flag=0).sv_multiplier, 0.5)

    def test_uninherited_points_report_unit_sv(self):
        self.assertEqual(TimingPoint(0, 500).sv_multiplier, 1.0)

    def test_bpm(self):
        self.assertAlmostEqual(TimingPoint(0, 500).bpm, 120.0)
        self.assertIsNone(TimingPoint(0, -100, uninherited_flag=0).bpm)

    def test_effects_are_masked_not_compared(self):
        """R1: effects=8 is omit-first-barline, not kiai."""
        omit = TimingPoint(0, 500, effects=EFFECT_OMIT_FIRST_BARLINE)
        self.assertFalse(omit.kiai)
        self.assertTrue(omit.omit_first_barline)

        both = TimingPoint(0, 500, effects=EFFECT_KIAI | EFFECT_OMIT_FIRST_BARLINE)
        self.assertTrue(both.kiai)
        self.assertTrue(both.omit_first_barline)

    def test_set_sv_rejects_uninherited(self):
        with self.assertRaises(ValueError):
            TimingPoint(0, 500).set_sv(2.0)

    def test_set_sv_round_trips(self):
        point = TimingPoint(0, -100, uninherited_flag=0)
        point.set_sv(1.6)
        self.assertAlmostEqual(point.sv_multiplier, 1.6)


class ConstructorTests(unittest.TestCase):
    def test_inherited_inherits_sample_settings(self):
        template = TimingPoint(0, 500, sample_set=2, sample_index=7, volume=35)
        generated = TimingPoint.inherited_at(1000, 2.0, template=template)
        self.assertEqual((generated.sample_set, generated.sample_index, generated.volume), (2, 7, 35))
        self.assertFalse(generated.uninherited)
        self.assertAlmostEqual(generated.sv_multiplier, 2.0)

    def test_extreme_bpm_for_invisible_notes(self):
        """The gimmick editor's invisible-note toggle writes a tiny beat length."""
        point = TimingPoint.uninherited_at(1234, 600_000_000.0)
        self.assertTrue(point.uninherited)
        self.assertAlmostEqual(point.beat_length, 0.0001)
        validate_timing_points([point])

    def test_rejects_non_positive_bpm(self):
        for bad in (0, -120, float("inf"), float("nan")):
            with self.subTest(bpm=bad), self.assertRaises(ValueError):
                TimingPoint.uninherited_at(0, bad)


class KiaiTests(unittest.TestCase):
    def test_spans_from_fixture(self):
        points = parse_timing_points(lines_of(FULL_V14))
        self.assertEqual(kiai_spans(points, 20000), [(2000, 4000)])

    def test_unterminated_kiai_runs_to_duration(self):
        points = [TimingPoint(0, 500), TimingPoint(1000, -100, uninherited_flag=0, effects=EFFECT_KIAI)]
        self.assertEqual(kiai_spans(points, 9000), [(1000, 9000)])

    def test_omit_barline_is_not_read_as_kiai(self):
        """R1 end to end: a bulk barline gimmick must not paint everything orange."""
        points = [TimingPoint(0, 500)] + [
            TimingPoint(float(t), 500, effects=EFFECT_OMIT_FIRST_BARLINE)
            for t in range(1000, 2000, 2)
        ]
        self.assertEqual(kiai_spans(points, 5000), [])


class QueryTests(unittest.TestCase):
    def test_active_uninherited_ignores_sv_points(self):
        points = parse_timing_points(lines_of(FULL_V14))
        self.assertEqual(active_uninherited_at(points, 3000).time, 0.0)
        self.assertEqual(active_uninherited_at(points, 7000).time, 6000.0)

    def test_active_before_first_point_falls_back_forward(self):
        points = [TimingPoint(1000, 400)]
        self.assertEqual(active_uninherited_at(points, 0).time, 1000.0)

    def test_empty_falls_back_to_120_bpm(self):
        self.assertAlmostEqual(active_uninherited_at([], 5000).bpm, 120.0)

    def test_sv_resets_at_uninherited_points(self):
        points = parse_timing_points(lines_of(FULL_V14))
        self.assertAlmostEqual(sv_at(points, 1000), 1.0)
        self.assertAlmostEqual(sv_at(points, 2500), 0.75)
        self.assertAlmostEqual(sv_at(points, 4500), 2.0)
        self.assertAlmostEqual(sv_at(points, 6500), 1.0, msg="uninherited point resets SV")

    def test_sort_is_stable_at_duplicate_times(self):
        first = TimingPoint(1000, 500, volume=10)
        second = TimingPoint(1000, -100, uninherited_flag=0, volume=20)
        third = TimingPoint(1000, 500, volume=30)
        order = [p.volume for p in sorted_by_time([first, second, third])]
        self.assertEqual(order, [10, 20, 30], "file order must survive equal timestamps")


class BarlineTests(unittest.TestCase):
    def test_one_barline_per_measure(self):
        points = [TimingPoint(0, 500, meter=4)]
        self.assertEqual(list(iter_barline_times(points, 8000))[:4], [0.0, 2000.0, 4000.0, 6000.0])

    def test_meter_is_honoured(self):
        points = [TimingPoint(0, 500, meter=3)]
        self.assertEqual(list(iter_barline_times(points, 4000))[:3], [0.0, 1500.0, 3000.0])

    def test_omit_first_barline_skips_the_downbeat(self):
        points = [TimingPoint(0, 500, meter=4, effects=EFFECT_OMIT_FIRST_BARLINE)]
        self.assertEqual(list(iter_barline_times(points, 5000))[0], 2000.0)

    def test_sections_restart_barlines(self):
        points = [TimingPoint(0, 500, meter=4), TimingPoint(3000, 400, meter=4)]
        times = list(iter_barline_times(points, 6000))
        self.assertIn(3000.0, times)
        self.assertIn(4600.0, times)


class ValidationTests(unittest.TestCase):
    def test_accepts_the_fixture(self):
        validate_timing_points(parse_timing_points(lines_of(FULL_V14)))

    def test_empty_is_allowed(self):
        validate_timing_points([])

    def test_rejects_sign_flag_mismatch(self):
        with self.assertRaises(TimingValidationError):
            validate_timing_points([TimingPoint(0, -100, uninherited_flag=1)])
        with self.assertRaises(TimingValidationError):
            validate_timing_points([TimingPoint(0, 500), TimingPoint(1, 100, uninherited_flag=0)])

    def test_rejects_non_finite_and_zero(self):
        for bad in (float("nan"), float("inf"), 0.0):
            with self.subTest(beat_length=bad), self.assertRaises(TimingValidationError):
                validate_timing_points([TimingPoint(0, bad)])

    def test_rejects_out_of_range_volume(self):
        with self.assertRaises(TimingValidationError):
            validate_timing_points([TimingPoint(0, 500, volume=101)])

    def test_requires_an_uninherited_point(self):
        with self.assertRaises(TimingValidationError):
            validate_timing_points([TimingPoint(0, -100, uninherited_flag=0)])

    def test_allows_dense_duplicate_timestamps(self):
        """Barline gimmicks legitimately cluster points at note time +/- 2,4,6 ms."""
        points = [TimingPoint(0, 500)]
        for offset in (-6, -4, -2, 2, 4, 6):
            points.append(TimingPoint(1000 + offset, 500))
        validate_timing_points(points)


if __name__ == "__main__":
    unittest.main()
