"""The time-stretch, checked without an audio device.

`TimeStretcher` is deliberately free of Qt and of I/O so this file can assert
the two properties that matter and that a listening test cannot measure: that
the output-to-source time mapping is exactly the requested rate, and that the
pitch does not move. Those are the two halves of "slow playback is accurate":
the first is why the playhead can be trusted, the second is the whole reason
this exists instead of `QMediaPlayer.setPlaybackRate`.
"""
from __future__ import annotations

import array
import math
import unittest

from audio_engine import (
    CHANNELS, OVERLAP_FRAMES, SAMPLE_RATE, SEQUENCE_FRAMES, TimeStretcher,
    downmix_to_mono,
)

TONE_HZ = 440.0


def _sine(seconds: float, hz: float = TONE_HZ, amplitude: int = 12000) -> array.array:
    samples = array.array("h")
    for i in range(int(SAMPLE_RATE * seconds)):
        value = int(amplitude * math.sin(2.0 * math.pi * hz * i / SAMPLE_RATE))
        samples.append(value)
        samples.append(value)
    return samples


def _pitch_hz(frames: array.array) -> float:
    """Frequency by zero crossings on the left channel.

    Enough to tell a time-stretch from a resample, which is the only question
    being asked: at 0.25x a resample drops 440Hz to 110Hz, a quarter of the
    tolerance any of these assertions use.
    """
    left = frames[::CHANNELS]
    crossings = sum(1 for a, b in zip(left, left[1:]) if (a < 0) != (b < 0))
    return crossings / 2.0 / (len(left) / SAMPLE_RATE)


def _pull_seconds(stretcher: TimeStretcher, source, mono, seconds: float,
                  rate: float) -> array.array:
    wanted = int(SAMPLE_RATE * seconds)
    collected = bytearray()
    while len(collected) // (2 * CHANNELS) < wanted:
        block = stretcher.pull(source, mono, 4096, rate)
        if not block:
            break
        collected += block
    out = array.array("h")
    out.frombytes(bytes(collected))
    return out


def _tonality(frames: array.array, hz: float = TONE_HZ) -> float:
    """Share of the signal's energy still sitting at `hz`, by Goertzel.

    Counting zero crossings is not enough on its own: a stretch can hold the
    crossing rate exactly while mangling the waveform between them, which is
    what a bad overlap does. This is the number that caught it.
    """
    left = frames[::CHANNELS]
    coefficient = 2.0 * math.cos(2.0 * math.pi * hz / SAMPLE_RATE)
    first = second = 0.0
    for sample in left:
        first, second = sample + coefficient * first - second, first
    power = first * first + second * second - coefficient * first * second
    energy = sum(float(sample) * sample for sample in left)
    return power / (energy * len(left) / 2.0) if energy > 0 else 0.0


class TimeStretchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # One tone for the whole class: generating six seconds of sine in
        # Python is the slowest thing in this file by a wide margin.
        cls.source = _sine(6.0)
        cls.mono = downmix_to_mono(cls.source)

    def _stretch(self, rate: float, seconds: float = 1.0):
        stretcher = TimeStretcher()
        stretcher.reset(0.0)
        out = _pull_seconds(stretcher, self.source, self.mono, seconds, rate)
        return stretcher, out

    def test_the_source_is_the_tone_the_other_tests_assume(self):
        self.assertAlmostEqual(_pitch_hz(self.source), TONE_HZ, delta=1.0)

    def test_output_maps_back_to_source_time_at_exactly_the_rate(self):
        """The property the playhead depends on. WSOLA slides *where* a grain
        is taken from, never how far the read head advances, so the mapping
        from output time to source time is the requested ratio and nothing
        else -- which is why time-stretching cannot introduce drift.
        """
        for rate in (1.0, 0.75, 0.5, 0.25):
            with self.subTest(rate=rate):
                stretcher, out = self._stretch(rate)
                frames = len(out) // CHANNELS
                self.assertGreater(frames, 0)
                self.assertAlmostEqual(
                    stretcher.source_frame / frames, rate, places=3)

    def test_slowing_down_does_not_move_the_pitch(self):
        """The reason for the whole module. Qt's setPlaybackRate is a plain
        resample -- measured at 0.261s of source per wall second for a 0.25x
        request -- which drops this tone to 110Hz.
        """
        for rate in (0.75, 0.5, 0.25):
            with self.subTest(rate=rate):
                _stretcher, out = self._stretch(rate)
                self.assertAlmostEqual(_pitch_hz(out), TONE_HZ, delta=5.0)

    def test_most_of_the_output_is_untouched_source(self):
        """The shipped-then-fixed bug: with the sequence equal to the overlap,
        every output sample was a blend of two different parts of the song --
        comb filtering end to end. SoundTouch, which is what BASS_FX runs and
        so what osu! sounds like, cross-fades about a tenth of its output.
        """
        self.assertLess(OVERLAP_FRAMES * 4, SEQUENCE_FRAMES)

    def test_a_stretched_tone_is_still_that_tone(self):
        """Guards the same bug by its effect rather than its cause. Before the
        fix a 440Hz sine came out of a 0.25x stretch with 1.5% of its energy
        still at 440Hz, while the zero-crossing pitch check above still passed.
        """
        self.assertGreater(_tonality(self.source), 0.99)
        for rate in (0.75, 0.5, 0.25):
            with self.subTest(rate=rate):
                _stretcher, out = self._stretch(rate)
                self.assertGreater(_tonality(out), 0.95)

    def test_rate_one_is_a_straight_copy(self):
        """Not an optimisation so much as a guarantee: at the rate the editor
        spends almost all its time at, the samples reaching the device are the
        samples that were decoded, with no splice to colour them."""
        _stretcher, out = self._stretch(1.0, seconds=0.2)
        frames = len(out)
        self.assertEqual(out.tobytes(), self.source[:frames].tobytes())

    def test_a_reset_does_not_splice_across_the_jump(self):
        """A seek that carried the overlap would fade the old position into the
        new one, which is a click exactly when the user asked to be elsewhere.
        """
        stretcher = TimeStretcher()
        stretcher.reset(0.0)
        _pull_seconds(stretcher, self.source, self.mono, 0.3, 0.5)
        stretcher.reset(SAMPLE_RATE * 2.0)
        self.assertEqual(stretcher.source_frame, SAMPLE_RATE * 2.0)
        out = _pull_seconds(stretcher, self.source, self.mono, 0.2, 0.5)
        self.assertGreater(len(out), 0)
        self.assertAlmostEqual(_pitch_hz(out), TONE_HZ, delta=5.0)

    def test_running_off_the_end_stops_rather_than_reading_past_it(self):
        stretcher = TimeStretcher()
        stretcher.reset(len(self.mono) - 64)
        self.assertEqual(stretcher.pull(self.source, self.mono, 4096, 0.5), b"")

    def test_the_downmix_stays_in_range_on_full_scale_input(self):
        """Averaged rather than summed: a summed downmix of two loud channels
        overflows 16-bit and the correlation scores turn to nonsense on exactly
        the loud material where splices are most audible."""
        loud = array.array("h", [32767, 32767, -32768, -32768])
        mono = downmix_to_mono(loud)
        self.assertEqual(list(mono), [32767, -32768])


if __name__ == "__main__":
    unittest.main()
