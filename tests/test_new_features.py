
import tempfile
import unittest
from pathlib import Path
from osu_io.parser import parse_osu
from osu_io.writer import write_osu
from transformer import transform

class TransformationTests(unittest.TestCase):
    def test_vertical_stays_in_playfield(self):
        positions=transform("vertical",range(32),{"line_count":2,"notes_per_line":16,"direction":"bottom_to_top"})
        self.assertEqual(set(positions),set(range(32)))
        self.assertTrue(all(0<=x<=512 and 0<=y<=384 for x,y in positions.values()))
    def test_dvd_is_deterministic(self):
        params={"chunk_size":32,"seed":99,"step_size":30}
        self.assertEqual(transform("dvd_bouncing",range(32),params),transform("dvd_bouncing",range(32),params))
    def test_vertical_taiko_fits(self):
        indexes=list(range(20)); params={"note_times":{i:i*125 for i in indexes},"timing_points":["0,500,4,1,0,100,1,0"],"timing_mode":"map","anchor_mode":"selection_start","beats_per_line":2,"line_count":4,"margin_x":12,"margin_y":10}
        positions=transform("vertical_taiko",indexes,params)
        self.assertTrue(all(0<=x<=512 and 0<=y<=384 for x,y in positions.values()))

class WriterTests(unittest.TestCase):
    def test_writer_forces_ar_and_cs(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"map.osu"
            source.write_text("osu file format v14\n\n[General]\nAudioFilename:a.mp3\n\n[Metadata]\nVersion:Test\n\n[Difficulty]\nCircleSize:5\nApproachRate:8\n\n[HitObjects]\n256,192,1000,1,0,0:0:0:0:\n",encoding="utf-8")
            document=parse_osu(source); output=Path(directory)/"output.osu"; write_osu(document,output,"New"); text=output.read_text()
            self.assertIn("ApproachRate:10",text); self.assertIn("CircleSize:7",text)
if __name__=="__main__": unittest.main()
