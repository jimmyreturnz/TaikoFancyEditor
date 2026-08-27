"""Hitsounds: which notes sound, and when each one fires.

The audio itself is not exercised -- there is no point asserting that Qt can
play a wav. What matters, and what has somewhere to go wrong, is the selection
("circles only, and which of the four samples") and the scheduling ("each note
exactly once, never twice, never on a seek"), both of which `HitsoundPlayer`
keeps free of any audio call so they can be tested without a media device.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gui
from model.hit_object import (
    HITSOUND_CLAP,
    HITSOUND_FINISH,
    TYPE_CIRCLE,
    TYPE_SLIDER,
    TYPE_SPINNER,
    HitObject,
)

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


def circle(time_ms: int, hit_sound: int = 0) -> HitObject:
    return HitObject(x=256, y=192, time=time_ms, type=TYPE_CIRCLE, hit_sound=hit_sound)


def fake_slider(time_ms: int, hit_sound: int = 0) -> HitObject:
    """A drumroll of negative length -- every fake slider and every shiny."""
    return HitObject(
        x=256, y=192, time=time_ms, type=TYPE_SLIDER, hit_sound=hit_sound,
        extras=("L|356:192", "1", "-1"),
    )


class SampleChoiceTests(unittest.TestCase):
    def test_each_kind_of_circle_asks_for_its_own_sample(self):
        self.assertEqual(gui.hitsound_key(circle(0)), "normal")
        self.assertEqual(gui.hitsound_key(circle(0, HITSOUND_CLAP)), "clap")
        self.assertEqual(gui.hitsound_key(circle(0, HITSOUND_FINISH)), "finish")
        self.assertEqual(
            gui.hitsound_key(circle(0, HITSOUND_FINISH | HITSOUND_CLAP)), "whistle"
        )

    def test_nothing_that_is_not_a_circle_makes_a_sound(self):
        """Circles only is the whole rule: it silences every fake slider and
        every shiny (both are drumrolls) with no gimmick-specific special
        case, and it silences real drumrolls and spinners too."""
        self.assertIsNone(gui.hitsound_key(fake_slider(0)))
        self.assertIsNone(gui.hitsound_key(fake_slider(0, HITSOUND_FINISH)))
        self.assertIsNone(gui.hitsound_key(
            HitObject(x=256, y=192, time=0, type=TYPE_SPINNER, hit_sound=0)
        ))
        real_drumroll = HitObject(
            x=256, y=192, time=0, type=TYPE_SLIDER, hit_sound=0,
            extras=("L|356:192", "1", "120"),
        )
        self.assertIsNone(gui.hitsound_key(real_drumroll))

    def test_a_gimmick_structures_own_note_still_sounds(self):
        """A Don fake slider and a barline note each write a real circle the
        player actually hits -- silencing those would make playback lie."""
        self.assertEqual(gui.hitsound_key(circle(10000)), "normal")

    def test_the_schedule_is_sorted_and_drops_the_silent_objects(self):
        times, keys = gui.hitsound_schedule([
            fake_slider(300),
            circle(200, HITSOUND_CLAP),
            circle(100),
            fake_slider(301),
        ])
        self.assertEqual(times, [100.0, 200.0])
        self.assertEqual(keys, ["normal", "clap"])


class FiringWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.player = gui.HitsoundPlayer()
        self.player.set_schedule([
            circle(1000), circle(1100, HITSOUND_CLAP), circle(1200, HITSOUND_FINISH),
        ])

    def test_a_window_fires_the_notes_it_crosses(self):
        self.assertEqual(self.player.pending(950, 1150), ["normal", "clap"])

    def test_consecutive_windows_fire_each_note_exactly_once(self):
        """The windows have to tile the timeline: a note landing on a boundary
        must fire in one frame and not in both."""
        fired = []
        for start in range(900, 1300, 100):
            fired += self.player.pending(start, start + 100)
        self.assertEqual(fired, ["normal", "clap", "finish"])

    def test_a_note_exactly_on_the_boundary_is_not_doubled(self):
        self.assertEqual(self.player.pending(900, 1000), ["normal"])
        self.assertEqual(self.player.pending(1000, 1050), [])

    def test_going_backwards_fires_nothing(self):
        self.assertEqual(self.player.pending(1300, 1000), [])

    def test_a_seek_moves_the_cursor_without_firing(self):
        self.player.reset_to(900)
        self.player.reset_to(1300)
        # Everything between was skipped over, so the next real frame from
        # 1300 has nothing left behind it to catch up on.
        self.assertEqual(self.player.pending(self.player._cursor, 1350), [])

    def test_a_negative_offset_fires_earlier(self):
        """The latency knob: -40 means the sound is triggered while the
        playhead is still 40ms short of the note."""
        self.player.offset_ms = -40
        self.assertEqual(self.player.pending(950, 965), ["normal"])
        self.player.offset_ms = 0
        self.assertEqual(self.player.pending(950, 965), [])

    def test_a_positive_offset_fires_later(self):
        self.player.offset_ms = 40
        self.assertEqual(self.player.pending(1000, 1020), [])
        self.assertEqual(self.player.pending(1020, 1045), ["normal"])

    def test_the_offset_is_wall_time_so_it_scales_with_the_rate(self):
        """The knob compensates output latency, which is real time.

        Held as song time it was right only at 1.0x -- the error is
        offset * (1 - rate), zero at 100% and worst at the slowest speed,
        which is why every rate except the default sounded misaligned.
        At 0.5x, 40ms of real latency is 20ms of song.
        """
        self.player.offset_ms = -40

        self.player.playback_rate = 1.0
        self.assertEqual(self.player.pending(950, 965), ["normal"])

        self.player.playback_rate = 0.5
        # The note is now only 20ms of song early, so the same window that
        # caught it at 1.0x is past it before it is due.
        self.assertEqual(self.player.pending(950, 965), [])
        self.assertEqual(self.player.pending(975, 985), ["normal"])

    def test_the_offset_scales_by_magnitude_not_sign(self):
        self.player.offset_ms = -40
        self.player.playback_rate = -0.5
        self.assertEqual(self.player.pending(975, 985), ["normal"])

    def test_no_offset_means_the_rate_changes_nothing(self):
        for rate in (1.0, 0.75, 0.5, 0.25):
            self.player.playback_rate = rate
            self.assertEqual(self.player.pending(950, 1050), ["normal"], rate)

    def test_disabled_fires_nothing(self):
        self.player.enabled = False
        self.assertEqual(self.player.pending(0, 100000), [])

    def test_advance_leaves_the_cursor_where_it_arrived(self):
        self.player.reset_to(900)
        self.player.advance(1150)
        self.assertEqual(self.player._cursor, 1150.0)
        self.assertEqual(self.player.pending(1150, 1250), ["finish"])


class PoolTests(unittest.TestCase):
    def test_no_pool_is_built_until_a_sound_is_actually_wanted(self):
        """Four samples times the pool size is 32 QSoundEffect objects, and
        they outlive the window that owns them -- building them in the
        constructor made every MainWindow cost more than the last, which
        across a suite that builds hundreds of them is quadratic."""
        player = gui.HitsoundPlayer()
        self.assertEqual(player._pools, {})

    def test_the_first_sound_builds_every_pool(self):
        """The samples run to 1.5s and a dense stream overlaps ~20 deep, so a
        single effect per sample would cut off each previous hit."""
        player = gui.HitsoundPlayer()
        player._play("normal")
        self.assertEqual(set(player._pools), set(gui.HITSOUND_SAMPLES))
        for key, pool in player._pools.items():
            self.assertEqual(len(pool), gui.HITSOUND_POOL_SIZE, key)

    def test_a_volume_set_before_any_pool_exists_still_applies(self):
        """The settings are read at startup, long before the first sound."""
        player = gui.HitsoundPlayer()
        player.set_volume(0.25)
        player._play("normal")
        self.assertAlmostEqual(player._pools["normal"][0].volume(), 0.25, places=3)

    def test_playing_walks_round_the_pool(self):
        player = gui.HitsoundPlayer()
        for step in range(gui.HITSOUND_POOL_SIZE + 2):
            expected = (step + 1) % gui.HITSOUND_POOL_SIZE
            player._play("normal")
            self.assertEqual(player._next["normal"], expected)


if __name__ == "__main__":
    unittest.main()
