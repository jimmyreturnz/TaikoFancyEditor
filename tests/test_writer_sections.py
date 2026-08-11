"""Writer tests for the mutations the editor milestones need.

The old writer could only rewrite two fields of an existing line, so none of
this was expressible. These tests are the proof that SV generation, barline
gimmicks and note editing have a safe path to disk before any UI is built on
them.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from model.hit_object import HitObject
from osu_io.parser import parse_osu
from osu_io.timing import TimingPoint
from osu_io.writer import OsuWriteValidationError, write_osu
from tests.osu_fixtures import write_fixture


class WriterMutationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name)
        self.document = parse_osu(write_fixture(self.directory, "full_v14"))

    def tearDown(self):
        self._temp.cleanup()

    def write(self, name="out.osu"):
        destination = self.directory / name
        write_osu(self.document, destination, self.document.version)
        return parse_osu(destination), destination

    def untouched_sections(self, destination):
        """Everything outside the two generated sections, as raw text."""
        text = destination.read_bytes().decode(self.document.encoding)
        kept, section = [], ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1]
            if section not in ("TimingPoints", "HitObjects"):
                kept.append(line)
        return kept

    # -- SV generation ----------------------------------------------------

    def test_generated_sv_sweep_survives_a_write(self):
        """The shape an easing-function generator produces in M4."""
        before = len(self.document.timing_points)
        active = self.document.timing_points[0]
        for step in range(20):
            self.document.timing_points.append(
                TimingPoint.inherited_at(10000 + step * 50, 1.0 + step * 0.1, template=active)
            )

        reparsed, _destination = self.write()
        self.assertEqual(len(reparsed.timing_points), before + 20)

        generated = [p for p in reparsed.timing_points if 10000 <= p.time < 11000]
        self.assertEqual(len(generated), 20)
        for step, point in enumerate(sorted(generated, key=lambda p: p.time)):
            self.assertFalse(point.uninherited)
            self.assertAlmostEqual(point.sv_multiplier, 1.0 + step * 0.1, places=6)
            self.assertEqual(point.volume, active.volume, "generated SV must inherit volume")

    def test_editing_an_existing_sv_point_in_place(self):
        target = next(p for p in self.document.timing_points if not p.uninherited)
        target.set_sv(3.5)
        reparsed, _ = self.write()
        edited = next(p for p in reparsed.timing_points if p.time == target.time)
        self.assertAlmostEqual(edited.sv_multiplier, 3.5)

    def test_removing_timing_points(self):
        self.document.timing_points = [p for p in self.document.timing_points if p.uninherited]
        reparsed, _ = self.write()
        self.assertTrue(all(p.uninherited for p in reparsed.timing_points))
        self.assertEqual(len(reparsed.timing_points), 2)

    # -- barline gimmick --------------------------------------------------

    def test_barline_gimmick_cluster_survives(self):
        """Don gets t+/-2, kat gets t+/-2,4,6, exactly as specified for M5."""
        active = self.document.timing_points[0]
        added = 0
        for note in [n for n in self.document.hit_objects if n.note_kind in ("don", "kat", "big_don", "big_kat")]:
            offsets = (-6, -4, -2, 2, 4, 6) if note.is_kat else (-2, 2)
            for offset in offsets:
                self.document.timing_points.append(
                    TimingPoint.uninherited_at(
                        note.time + offset, active.bpm, omit_first_barline=True, template=active
                    )
                )
                added += 1

        reparsed, destination = self.write()
        self.assertEqual(len(reparsed.timing_points), 4 + added)

        clustered = [p for p in reparsed.timing_points if abs(p.time - 2000) <= 6 and p.omit_first_barline]
        self.assertEqual(len(clustered), 6, "kat at 2000 ms should get six barline points")
        self.assertTrue(all(not p.kiai for p in clustered), "omit-barline must not read as kiai")

        # The gimmick must not disturb anything it does not own.
        self.assertIn('0,0,"bg.jpg",0,0', self.untouched_sections(destination))

    def test_invisible_note_extreme_bpm(self):
        point = TimingPoint.uninherited_at(3000, 600_000_000.0)
        self.document.timing_points.append(point)
        reparsed, _ = self.write()
        written = next(p for p in reparsed.timing_points if p.time == 3000 and p.uninherited)
        self.assertAlmostEqual(written.beat_length, 0.0001)

    # -- note editing -----------------------------------------------------

    def test_adding_a_fake_slider(self):
        fake = HitObject.from_line("256,192,8000,2,12,L|624:192,643,-0.0001")
        self.document.hit_objects.append(fake)
        reparsed, destination = self.write()

        self.assertEqual(len(reparsed.hit_objects), 9)
        written = next(n for n in reparsed.hit_objects if n.time == 8000)
        self.assertEqual(written.to_line(""), "256,192,8000,2,12,L|624:192,643,-0.0001")

    def test_removing_notes(self):
        self.document.hit_objects = [n for n in self.document.hit_objects if n.note_kind != "denden"]
        reparsed, _ = self.write()
        self.assertEqual(len(reparsed.hit_objects), 7)
        self.assertNotIn("denden", [n.note_kind for n in reparsed.hit_objects])

    def test_changing_note_time_and_hitsound(self):
        note = self.document.hit_objects[0]
        note.time, note.hit_sound = 1200, 8
        reparsed, _ = self.write()
        moved = next(n for n in reparsed.hit_objects if n.time == 1200)
        self.assertEqual(moved.hit_sound, 8)
        self.assertEqual(moved.note_kind, "kat")

    def test_notes_are_written_in_time_order(self):
        self.document.hit_objects.append(HitObject.from_line("256,192,1,1,0,0:0:0:0:"))
        reparsed, _ = self.write()
        times = [n.time for n in reparsed.hit_objects]
        self.assertEqual(times, sorted(times))

    # -- passthrough ------------------------------------------------------

    def test_unrelated_sections_survive_heavy_mutation(self):
        source_text = (self.directory / "full_v14.osu").read_bytes().decode(self.document.encoding)

        active = self.document.timing_points[0]
        for step in range(50):
            self.document.timing_points.append(TimingPoint.inherited_at(9000 + step, 2.0, template=active))
        self.document.hit_objects = self.document.hit_objects[:4]

        _reparsed, destination = self.write()
        for marker in (
            'WidescreenStoryboard: 1',
            'Bookmarks: 2000,4000',
            '//Background and Video events',
            '0,0,"bg.jpg",0,0',
            '//Break Periods',
            '2,10000,12000',
            'Combo1 : 255,192,0',
            'Combo2 : 0,202,0',
            'Tags:taiko',
        ):
            self.assertIn(marker, source_text)
            self.assertIn(marker, destination.read_bytes().decode(self.document.encoding),
                          f"passthrough lost: {marker}")


class WriterValidationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp.name)
        self.document = parse_osu(write_fixture(self.directory, "full_v14"))

    def tearDown(self):
        self._temp.cleanup()

    def _expect_rejected(self):
        destination = self.directory / "rejected.osu"
        with self.assertRaises(OsuWriteValidationError):
            write_osu(self.document, destination, self.document.version)
        self.assertFalse(destination.exists(), "a rejected write must not leave a file")

    def test_rejects_non_finite_beat_length(self):
        self.document.timing_points.append(TimingPoint(5000, float("nan")))
        self._expect_rejected()

    def test_rejects_sign_flag_mismatch(self):
        self.document.timing_points.append(TimingPoint(5000, -100, uninherited_flag=1))
        self._expect_rejected()

    def test_rejects_out_of_range_volume(self):
        self.document.timing_points.append(TimingPoint(5000, 500, volume=250))
        self._expect_rejected()

    def test_no_temp_files_are_left_behind(self):
        self.document.timing_points.append(TimingPoint(5000, 0.0))
        with self.assertRaises(OsuWriteValidationError):
            write_osu(self.document, self.directory / "x.osu", self.document.version)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])


class BackupTests(unittest.TestCase):
    def test_backup_is_only_taken_after_validation_passes(self):
        """R7: the old writer copied the .bak before writing the temp file."""
        with tempfile.TemporaryDirectory() as directory:
            source = write_fixture(Path(directory), "full_v14")
            document = parse_osu(source)
            document.timing_points.append(TimingPoint(5000, float("inf")))

            with self.assertRaises(OsuWriteValidationError):
                write_osu(document, source, document.version,
                          allow_overwrite_source=True, create_backup=True)

            self.assertFalse(
                source.with_suffix(".osu.bak").exists(),
                "a failed write must not leave a backup behind",
            )
            self.assertEqual(source.read_bytes(), write_fixture(Path(directory), "full_v14").read_bytes())

    def test_successful_overwrite_creates_a_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_fixture(Path(directory), "full_v14")
            original = source.read_bytes()
            document = parse_osu(source)
            for note in document.hit_objects:
                note.x += 10

            write_osu(document, source, document.version,
                      allow_overwrite_source=True, create_backup=True)

            backup = source.with_suffix(".osu.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_bytes(), original)
            self.assertNotEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
