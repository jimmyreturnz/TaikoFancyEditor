"""Byte-exact .osu fixtures for parser and writer regression tests.

These are byte literals rather than checked-in files on purpose. Git line-ending
normalization would rewrite CRLF in a committed .osu and silently invalidate
every byte-exact assertion built on it, in a way that only reproduces on one
platform. Defining the bytes here makes the fixtures identical everywhere.

Each fixture targets a specific hazard in the current I/O layer:

FULL_V14          every hit-object shape the format allows, including the fake
                  slider whose trailing fields the parser currently discards,
                  plus inherited SV points and a kiai span
LEGACY_V4         two-field timing points, which the >= 7 field guard rejects
CP1252            a byte sequence that is not valid UTF-8, so the encoding
                  fallback and round-trip are exercised
COLOURS_BETWEEN   [Colours] sitting between [TimingPoints] and [HitObjects],
                  which any section-splicing writer must not disturb
NO_TIMING_POINTS  no [TimingPoints] section at all
"""
from __future__ import annotations

from pathlib import Path

FULL_V14 = (
    b"osu file format v14\r\n"
    b"\r\n"
    b"[General]\r\n"
    b"AudioFilename: audio.mp3\r\n"
    b"AudioLeadIn: 0\r\n"
    b"PreviewTime: 3000\r\n"
    b"Countdown: 0\r\n"
    b"SampleSet: Normal\r\n"
    b"Mode: 1\r\n"
    b"WidescreenStoryboard: 1\r\n"
    b"\r\n"
    b"[Editor]\r\n"
    b"Bookmarks: 2000,4000\r\n"
    b"DistanceSpacing: 1\r\n"
    b"BeatDivisor: 4\r\n"
    b"GridSize: 32\r\n"
    b"\r\n"
    b"[Metadata]\r\n"
    b"Title:Test Song\r\n"
    b"Artist:Tester\r\n"
    b"Creator:jimmyreturnz\r\n"
    b"Version:Oni\r\n"
    b"Source:\r\n"
    b"Tags:taiko\r\n"
    b"BeatmapID:0\r\n"
    b"BeatmapSetID:-1\r\n"
    b"\r\n"
    b"[Difficulty]\r\n"
    b"HPDrainRate:5\r\n"
    b"CircleSize:5\r\n"
    b"OverallDifficulty:6\r\n"
    b"ApproachRate:5\r\n"
    b"SliderMultiplier:1.4\r\n"
    b"SliderTickRate:1\r\n"
    b"\r\n"
    b"[Events]\r\n"
    b"//Background and Video events\r\n"
    b'0,0,"bg.jpg",0,0\r\n'
    b"//Break Periods\r\n"
    b"2,10000,12000\r\n"
    b"\r\n"
    b"[TimingPoints]\r\n"
    b"0,500,4,1,0,60,1,0\r\n"
    b"2000,-133.33333333333334,4,1,0,60,0,1\r\n"
    b"4000,-50,4,1,0,60,0,0\r\n"
    b"6000,400,4,1,0,70,1,8\r\n"
    b"\r\n"
    b"[Colours]\r\n"
    b"Combo1 : 255,192,0\r\n"
    b"Combo2 : 0,202,0\r\n"
    b"\r\n"
    b"[HitObjects]\r\n"
    b"256,192,1000,1,0,0:0:0:0:\r\n"
    b"256,192,1500,1,4,0:0:0:0:\r\n"
    b"256,192,2000,1,8,0:0:0:0:\r\n"
    b"256,192,2500,1,12,0:0:0:0:\r\n"
    b"256,192,3000,1,2\r\n"
    b"256,192,3500,2,0,L|624:192,1,280,0|0,0:0|0:0,0:0:0:0:\r\n"
    b"256,192,5000,12,0,7000,0:0:0:0:\r\n"
    b"256,192,54692,2,12,L|624:192,643,-0.0001\r\n"
)

LEGACY_V4 = (
    b"osu file format v4\r\n"
    b"\r\n"
    b"[General]\r\n"
    b"AudioFilename: song.mp3\r\n"
    b"Mode: 1\r\n"
    b"\r\n"
    b"[Metadata]\r\n"
    b"Title:Old Map\r\n"
    b"Version:Muzukashii\r\n"
    b"\r\n"
    b"[Difficulty]\r\n"
    b"HPDrainRate:6\r\n"
    b"CircleSize:5\r\n"
    b"OverallDifficulty:6\r\n"
    b"ApproachRate:5\r\n"
    b"SliderMultiplier:1.4\r\n"
    b"SliderTickRate:1\r\n"
    b"\r\n"
    b"[TimingPoints]\r\n"
    b"500,300\r\n"
    b"20000,240\r\n"
    b"\r\n"
    b"[HitObjects]\r\n"
    b"256,192,500,1,0\r\n"
    b"256,192,800,1,8\r\n"
    b"256,192,1100,1,0\r\n"
)

# "Artist:Caf\xe9 Latin" - 0xe9 alone is not valid UTF-8, forcing the cp1252 path.
CP1252 = (
    b"osu file format v14\r\n"
    b"\r\n"
    b"[General]\r\n"
    b"AudioFilename: audio.mp3\r\n"
    b"Mode: 1\r\n"
    b"\r\n"
    b"[Metadata]\r\n"
    b"Title:Caf\xe9\r\n"
    b"Artist:Bj\xf6rk\r\n"
    b"Version:Futsuu\r\n"
    b"\r\n"
    b"[Difficulty]\r\n"
    b"HPDrainRate:5\r\n"
    b"CircleSize:5\r\n"
    b"OverallDifficulty:5\r\n"
    b"ApproachRate:5\r\n"
    b"SliderMultiplier:1.4\r\n"
    b"SliderTickRate:1\r\n"
    b"\r\n"
    b"[TimingPoints]\r\n"
    b"0,500,4,1,0,60,1,0\r\n"
    b"\r\n"
    b"[HitObjects]\r\n"
    b"256,192,1000,1,0,0:0:0:0:\r\n"
    b"256,192,1500,1,8,0:0:0:0:\r\n"
)

COLOURS_BETWEEN = (
    b"osu file format v14\r\n"
    b"\r\n"
    b"[General]\r\n"
    b"AudioFilename: audio.mp3\r\n"
    b"Mode: 1\r\n"
    b"\r\n"
    b"[Metadata]\r\n"
    b"Title:Sandwich\r\n"
    b"Version:Inner Oni\r\n"
    b"\r\n"
    b"[Difficulty]\r\n"
    b"HPDrainRate:5\r\n"
    b"CircleSize:5\r\n"
    b"OverallDifficulty:5\r\n"
    b"ApproachRate:5\r\n"
    b"SliderMultiplier:1.4\r\n"
    b"SliderTickRate:1\r\n"
    b"\r\n"
    b"[TimingPoints]\r\n"
    b"0,500,4,1,0,60,1,0\r\n"
    b"1000,-200,4,1,0,60,0,0\r\n"
    b"\r\n"
    b"[Colours]\r\n"
    b"Combo1 : 255,192,0\r\n"
    b"SliderBorder : 12,34,56\r\n"
    b"\r\n"
    b"[HitObjects]\r\n"
    b"256,192,1000,1,0,0:0:0:0:\r\n"
    b"256,192,1500,1,8,0:0:0:0:\r\n"
    b"256,192,2000,1,4,0:0:0:0:\r\n"
)

NO_TIMING_POINTS = (
    b"osu file format v14\r\n"
    b"\r\n"
    b"[General]\r\n"
    b"AudioFilename: audio.mp3\r\n"
    b"Mode: 1\r\n"
    b"\r\n"
    b"[Metadata]\r\n"
    b"Title:Untimed\r\n"
    b"Version:Kantan\r\n"
    b"\r\n"
    b"[Difficulty]\r\n"
    b"HPDrainRate:3\r\n"
    b"CircleSize:5\r\n"
    b"OverallDifficulty:3\r\n"
    b"ApproachRate:5\r\n"
    b"SliderMultiplier:1.4\r\n"
    b"SliderTickRate:1\r\n"
    b"\r\n"
    b"[HitObjects]\r\n"
    b"256,192,1000,1,0,0:0:0:0:\r\n"
    b"256,192,2000,1,8,0:0:0:0:\r\n"
)

# [Difficulty] AFTER [HitObjects], with no ApproachRate or CircleSize line.
# write_osu inserts the missing keys at difficulty_index + 1 and then applies a
# flat `shift` to every hit object's source_line_index, which assumes all
# insertions precede all hit objects. Here they do not, so the writer indexes
# past the note block. Unusual but legal: nothing in the format requires
# [Difficulty] to come first.
REVERSED_SECTIONS = (
    b"osu file format v14\r\n"
    b"\r\n"
    b"[General]\r\n"
    b"AudioFilename: audio.mp3\r\n"
    b"Mode: 1\r\n"
    b"\r\n"
    b"[Metadata]\r\n"
    b"Title:Reversed\r\n"
    b"Version:Oni\r\n"
    b"\r\n"
    b"[TimingPoints]\r\n"
    b"0,500,4,1,0,60,1,0\r\n"
    b"\r\n"
    b"[HitObjects]\r\n"
    b"100,192,1000,1,0,0:0:0:0:\r\n"
    b"200,192,1500,1,8,0:0:0:0:\r\n"
    b"300,192,2000,1,4,0:0:0:0:\r\n"
    b"\r\n"
    b"[Difficulty]\r\n"
    b"HPDrainRate:5\r\n"
    b"OverallDifficulty:5\r\n"
    b"SliderMultiplier:1.4\r\n"
    b"SliderTickRate:1\r\n"
)

ALL_FIXTURES = {
    "full_v14": FULL_V14,
    "legacy_v4": LEGACY_V4,
    "cp1252": CP1252,
    "colours_between": COLOURS_BETWEEN,
    "no_timing_points": NO_TIMING_POINTS,
}

# Kept out of ALL_FIXTURES until the writer handles it, so the shared
# round-trip tests stay green while this stays a known, tested defect.
KNOWN_BAD = {
    "reversed_sections": REVERSED_SECTIONS,
}


def write_fixture(directory: Path, name: str) -> Path:
    """Materialize a fixture into `directory` and return its path."""
    path = Path(directory) / f"{name}.osu"
    path.write_bytes(ALL_FIXTURES.get(name) or KNOWN_BAD[name])
    return path
