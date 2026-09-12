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
    CHANNELS, CORRELATION_STEP, FADE_FRAMES, GRAIN_FRAMES, OVERLAP_FRAMES,
    SAMPLE_RATE, SEARCH_FRAMES, SEEK_COALESCE_MS, SEQUENCE_FRAMES,
    SEQUENCE_MS, TimeStretcher, _Engine, _centre_bias, downmix_to_mono,
    grain_offset_ms,
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


class FadeInTests(unittest.TestCase):
    """`_Engine._fade_in`, checked without a QApplication or an audio device --
    it is plain array math over `self._fade_remaining`, so `_Engine.__new__`
    (skipping `__init__`, which builds a QAudioFormat) is enough to reach it.

    Guards the seek-stutter report: `_open_sink` tears the sink down and
    rebuilds it on every seek while playing, cutting the old stream with
    nothing of its own and starting the new one at whatever sample the target
    lands on -- measured at 25% of full scale for a mid-song seek
    (`tools/measure_seek.py`). This is what softens that back down.
    """

    def _engine(self) -> _Engine:
        engine = _Engine.__new__(_Engine)
        engine._fade_remaining = FADE_FRAMES
        return engine

    def _constant_block(self, frames: int, value: int = 20000) -> bytes:
        return array.array("h", [value, value] * frames).tobytes()

    def test_the_first_frame_is_silence(self):
        """Zero slope at the very start (a raised cosine, not a straight
        ramp) is what keeps the fade's own onset from being a second, smaller
        click."""
        engine = self._engine()
        out = array.array("h")
        out.frombytes(engine._fade_in(self._constant_block(FADE_FRAMES + 5)))
        self.assertEqual(out[0], 0)

    def test_the_ramp_is_monotonic_and_reaches_full_scale(self):
        engine = self._engine()
        out = array.array("h")
        out.frombytes(engine._fade_in(self._constant_block(FADE_FRAMES + 5)))
        left = out[::CHANNELS]
        ramp = left[:FADE_FRAMES]
        self.assertTrue(all(b >= a for a, b in zip(ramp, ramp[1:])), "must not dip")
        self.assertEqual(list(left[FADE_FRAMES:]), [20000] * 5, "flat once the ramp ends")

    def test_both_channels_get_the_same_gain(self):
        engine = self._engine()
        out = array.array("h")
        out.frombytes(engine._fade_in(self._constant_block(FADE_FRAMES)))
        self.assertEqual(list(out[0::CHANNELS]), list(out[1::CHANNELS]))

    def test_fade_remaining_reaches_zero_and_then_the_method_is_a_no_op(self):
        engine = self._engine()
        engine._fade_in(self._constant_block(FADE_FRAMES + 100))
        self.assertEqual(engine._fade_remaining, 0)
        untouched = self._constant_block(10)
        # A caller only reaches _fade_in while _fade_remaining > 0; calling it
        # anyway here checks the ramp truly stopped rather than looping.
        out = array.array("h")
        out.frombytes(engine._fade_in(untouched))
        self.assertEqual(list(out[::CHANNELS]), [20000] * 10)

    def test_the_ramp_resumes_correctly_split_across_several_calls(self):
        """One `_fill` tick rarely has `FADE_FRAMES` of free buffer space in
        one go, so the ramp has to pick up mid-slope across calls exactly as
        if it had run in a single one."""
        engine = self._engine()
        whole = array.array("h")
        whole.frombytes(self._engine()._fade_in(self._constant_block(FADE_FRAMES + 10)))

        split = self._engine()
        pieced = array.array("h")
        chunk = self._constant_block(50)
        for _ in range((FADE_FRAMES + 10 + 49) // 50):
            pieced.frombytes(split._fade_in(chunk))
        self.assertEqual(list(pieced[:len(whole)]), list(whole))

    def test_open_sink_arms_the_fade(self):
        """The dial `_open_sink` sets, checked directly rather than through a
        real QAudioSink: every (re)open -- first play and every seek while
        playing alike -- must arm it, or the fix only reaches one of them."""
        engine = _Engine.__new__(_Engine)
        engine._fade_remaining = 0
        self.assertEqual(engine._fade_remaining, 0)
        # _open_sink also touches self._stretcher, self._sink and self._pump,
        # none of which this test can build without a QApplication -- the
        # constant itself, and that _open_sink assigns it, is what is being
        # guarded, so read the source rather than run the method.
        import inspect
        source = inspect.getsource(_Engine._open_sink)
        self.assertIn("self._fade_remaining = FADE_FRAMES", source)


class SeekCoalesceTests(unittest.TestCase):
    """`_Engine.seek`'s coalescing, exercised on a real `_Engine` (needed for
    the QTimer it arms) with the sink/pump/decoder replaced by stand-ins so no
    audio device is touched.

    Guards the scrub-stutter report: a drag fires one `seek_requested` per
    mouse-move (gui.py's `mouseMoveEvent`), and each used to tear the sink
    down and rebuild it before the last rebuild had produced a sample --
    `tools/measure_scrub.py` measured up to 549ms of the device getting
    nothing at all across a ~1s burst 8ms apart. `seek` now parks a target
    arriving within `SEEK_COALESCE_MS` of the last landed one instead of
    rebuilding again immediately.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtCore import QCoreApplication
        # QTimer(self) inside seek() needs a real QObject parent and, to be
        # started at all safely, an application instance -- shared with
        # whatever else in this process already created one, same pattern as
        # tests/test_gimmick_editor.py.
        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def _playing_engine(self):
        """An `_Engine` in PlayingState with a live-looking sink, and a
        `rebuilds` log standing in for `_open_sink` -- which would otherwise
        open a real `QAudioSink`. It restores `_sink`/`_pump` the way a real
        rebuild would, since `_land_seek` itself only tears them down.
        """
        from PySide6.QtMultimedia import QMediaPlayer
        from types import SimpleNamespace

        engine = _Engine()
        engine._state = QMediaPlayer.PlayingState
        rebuilds = []

        def fake_open_sink():
            rebuilds.append(engine._stretcher.source_frame)
            engine._sink = SimpleNamespace(stop=lambda: None)
            engine._pump = SimpleNamespace(stop=lambda: None)

        engine._open_sink = fake_open_sink
        fake_open_sink()  # as play() would: land already "hot"
        rebuilds.clear()
        return engine, rebuilds

    def test_an_isolated_seek_lands_immediately(self):
        engine, rebuilds = self._playing_engine()
        engine.seek(5000.0)
        self.assertEqual(len(rebuilds), 1)
        self.assertIsNone(engine._pending_seek_ms)

    def test_a_burst_of_seeks_lands_only_once(self):
        engine, rebuilds = self._playing_engine()
        engine.seek(1000.0)  # first of the burst: lands immediately
        for ms in (1010.0, 1020.0, 1030.0, 1040.0):
            engine.seek(ms)  # inside SEEK_COALESCE_MS of the last landing: parked
        self.assertEqual(len(rebuilds), 1,
                          "only the first seek of a burst should rebuild the sink")
        self.assertEqual(engine._pending_seek_ms, 1040.0,
                          "the timer must land the LAST requested position")
        self.assertTrue(engine._seek_timer.isActive())

    def test_the_parked_target_lands_once_the_burst_settles(self):
        engine, rebuilds = self._playing_engine()
        engine.seek(1000.0)
        engine.seek(1010.0)
        engine.seek(1020.0)  # parked; not landed yet
        self.assertEqual(len(rebuilds), 1)
        engine._land_pending_seek()  # what the timer's own timeout does
        self.assertEqual(len(rebuilds), 2)
        self.assertAlmostEqual(
            rebuilds[-1], 1020.0 / 1000.0 * SAMPLE_RATE, delta=1)
        self.assertIsNone(engine._pending_seek_ms)

    def test_seeks_spaced_apart_are_not_a_burst(self):
        """A single click well after the last one must not pick up latency
        meant for a drag."""
        engine, rebuilds = self._playing_engine()
        engine.seek(1000.0)
        engine._last_seek_wall -= SEEK_COALESCE_MS + 5  # simulate time passing
        engine.seek(2000.0)
        self.assertEqual(len(rebuilds), 2)
        self.assertIsNone(engine._pending_seek_ms)

    def test_a_paused_seek_is_never_parked(self):
        """Nothing is audible while paused, so there is no burst to protect
        against -- a paused seek must keep dropping the sink immediately,
        exactly as before this change."""
        from PySide6.QtMultimedia import QMediaPlayer
        engine, rebuilds = self._playing_engine()
        engine._state = QMediaPlayer.PausedState
        engine.seek(1000.0)
        engine.seek(1010.0)
        self.assertEqual(len(rebuilds), 0, "paused: the sink is dropped, not rebuilt")
        self.assertIsNone(engine._sink)
        self.assertIsNone(engine._pending_seek_ms)

    def test_a_repeat_of_the_landed_target_is_dropped_not_parked(self):
        """The click-stutter report. One plain click on an overview bar is a
        press *and* a release at the same pixel, so two identical seeks arrive
        milliseconds apart. Parked rather than dropped, the second landed
        `SEEK_COALESCE_MS` later as a whole second sink teardown and rebuild --
        the audio rewinding to where it already was, heard as one hiccup just
        after every click on the bar.
        """
        engine, rebuilds = self._playing_engine()
        engine.seek(4000.0)
        self.assertEqual(len(rebuilds), 1)
        engine.seek(4000.0)
        self.assertEqual(len(rebuilds), 1, "the duplicate must not rebuild")
        self.assertIsNone(engine._pending_seek_ms,
                          "and must not be parked to rebuild later either")

    def test_a_burst_that_does_move_is_still_parked(self):
        """The guard is on the *target*, not on being in a burst: a real drag
        still coalesces, which is the whole reason the window exists."""
        engine, rebuilds = self._playing_engine()
        engine.seek(4000.0)
        engine.seek(4000.0)   # dropped
        engine.seek(4200.0)   # a genuine move, mid-burst
        self.assertEqual(len(rebuilds), 1)
        self.assertEqual(engine._pending_seek_ms, 4200.0)

    def test_stop_cancels_a_parked_seek(self):
        """Otherwise a stale target could land later against a sink or track
        that has since moved on (load() calls stop() first for this reason)."""
        engine, rebuilds = self._playing_engine()
        engine.seek(1000.0)
        engine.seek(1010.0)  # parked
        engine.stop()
        self.assertIsNone(engine._pending_seek_ms)
        self.assertFalse(engine._seek_timer.isActive())


class SearchCostTests(unittest.TestCase):
    """`CORRELATION_STEP`'s effect on a grain's wall-clock cost, measured
    rather than assumed -- and measured as a *ratio* on whatever machine runs
    it rather than an absolute threshold, which would be flaky across CI
    hardware.

    Guards the sustained-slow-rate-playback stutter report: one grain
    (`SEQUENCE_FRAMES` of output, produced once every 20ms of playback at any
    rate) ran its correlation search at every frame with `CORRELATION_STEP=1`,
    costing 20-28ms against the pump's 10ms budget at the 117ms geometry
    (`tools/measure_slow_rate_playback.py`: ~12% of `_fill()` calls over
    budget at 0.25/0.5/0.75x). Subsampling the correlation input halved that
    with no change in `tools/measure_stretch_quality.py`'s tonality/warble/
    click numbers.

    The 20ms grain has since taken the same measurement to 0-1 calls over
    budget out of ~790, because the correlation window came down with the
    grain -- so this subsampling is no longer load-bearing, and if it ever
    costs quality it can go. It was accepted on the strength of metrics that
    only ran on synthetic signals, which is exactly how the 117ms grain got in.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = _sine(3.0)
        cls.mono = downmix_to_mono(cls.source)

    def _grain_cost(self, correlation_step: int, seconds: float = 2.0) -> float:
        import time
        import audio_engine as ae
        saved = ae.CORRELATION_STEP
        ae.CORRELATION_STEP = correlation_step
        try:
            stretcher = TimeStretcher()
            stretcher.reset(0.0)
            started = time.perf_counter()
            _pull_seconds(stretcher, self.source, self.mono, seconds, 0.5)
            return time.perf_counter() - started
        finally:
            ae.CORRELATION_STEP = saved

    def test_correlation_step_two_is_meaningfully_cheaper_than_one(self):
        cost_1 = self._grain_cost(1)
        cost_2 = self._grain_cost(2)
        self.assertLess(
            cost_2, cost_1 * 0.8,
            "subsampling the correlation should cost noticeably less than "
            "not subsampling it, on the same machine in the same run -- if "
            "not, the pump-budget fix this constant exists for has regressed")

    def test_the_shipped_default_actually_subsamples(self):
        self.assertGreater(
            CORRELATION_STEP, 1,
            "CORRELATION_STEP=1 measured 20-28ms/grain against a 10ms pump "
            "budget (tools/measure_slow_rate_playback.py) -- this guards "
            "against silently reverting to it")


if __name__ == "__main__":
    unittest.main()


class GrainGeometryTests(unittest.TestCase):
    """One grain length, one overlap, one reach, none of them SoundTouch's.

    Upstream's `calcSeqParameters` shipped here briefly and was audibly worse:
    the 125ms grain it asks for at 0.25x took a kick drum apart on a real
    master while every synthetic metric called it fine. A ladder of grain
    lengths through the real device settled on 20ms, and the reach then had to
    come down with it -- see `SEQUENCE_MS` for both tables. These pin the
    relationships that measurement established, not upstream's numbers.
    """

    def test_the_grain_is_one_length_at_every_rate(self):
        """Nothing left for the rate to change: 20ms is already shorter than
        the shortest sequence `calcSeqParameters` ever asks for (50ms), so the
        curve that used to pick between them has nothing to pick."""
        self.assertEqual(SEQUENCE_FRAMES, int(SAMPLE_RATE * SEQUENCE_MS / 1000.0))
        self.assertEqual(GRAIN_FRAMES, SEQUENCE_FRAMES + OVERLAP_FRAMES)

    def test_the_overlap_scales_with_the_grain(self):
        """The half that is easy to miss. A fixed 8ms overlap is 40% of a 20ms
        grain, and at 10ms it is 80% -- which is comb filtering, measured at
        0.699 tonality, a third of a 440Hz sine leaving its own frequency. It
        also makes a short grain *dearer*, because the search correlates over
        the overlap, which is how an earlier round concluded that short grains
        cost more."""
        self.assertEqual(OVERLAP_FRAMES, SEQUENCE_FRAMES // 8)
        self.assertLess(OVERLAP_FRAMES * 4, SEQUENCE_FRAMES)

    def test_the_reach_is_narrower_than_the_grain(self):
        """The 0.75x regression, pinned by its cause.

        Upstream's 25ms reach was sized against upstream's 117ms grain. Left at
        25ms against a 20ms grain it is wider than the whole grain, so a grain
        may be taken from source the previous grain already emitted: tonality
        at 0.75x measured **0.457**, and no amount of listening at 0.25x found
        it. Half the sequence put every rate back on the ceiling (0.999).
        """
        self.assertEqual(SEARCH_FRAMES, SEQUENCE_FRAMES // 2)
        self.assertLess(SEARCH_FRAMES, SEQUENCE_FRAMES)

    def test_the_search_is_forward_only(self):
        """Not backwards: a backward search would put a grain's nominal start
        below the read head, and the read head is what carries the mapping from
        output time back to song time."""
        source = _sine(6.0)
        mono = downmix_to_mono(source)
        stretcher = TimeStretcher()
        stretcher.reset(0.0)
        starts, produce = [], TimeStretcher._produce

        def record(self, src, mn, rate):
            before = int(self._read)
            ok = produce(self, src, mn, rate)
            if ok:
                starts.append(before)
            return ok

        TimeStretcher._produce = record
        try:
            _pull_seconds(stretcher, source, mono, 2.0, 0.25)
        finally:
            TimeStretcher._produce = produce
        self.assertGreater(len(starts), 3)
        # The read head advances by exactly sequence * rate; a backward search
        # would put a grain's nominal start below that.
        for index, start in enumerate(starts):
            self.assertAlmostEqual(
                start, int(index * SEQUENCE_FRAMES * 0.25), delta=1)

    def test_the_crossfade_is_linear_and_complementary(self):
        """overlapStereo is a straight ramp, and the two halves have to sum to
        one or the splice dips."""
        window = TimeStretcher()._window
        self.assertEqual(len(window), OVERLAP_FRAMES)
        self.assertEqual(window[0], 0)
        for i in range(1, OVERLAP_FRAMES):
            self.assertGreaterEqual(window[i], window[i - 1])
        self.assertAlmostEqual(
            window[OVERLAP_FRAMES // 2] / float(1 << 12), 0.5, delta=0.02)

    def test_the_centre_bias_prefers_a_splice_near_the_read_head(self):
        """SoundTouch's (1.0 - 0.25 * tmp * tmp). Without it the search takes
        any small improvement anywhere in its reach and the read head jitters;
        this is the term that holds it still, and it was the piece missing when
        playback was reported as morphing."""
        bias = _centre_bias(100)
        self.assertAlmostEqual(bias[50], 1.0, delta=0.01)
        self.assertAlmostEqual(bias[0], 0.75, delta=0.01)
        self.assertLess(bias[0], bias[50])
        self.assertLess(bias[99], bias[50])


class ReportedPositionTests(unittest.TestCase):
    """Where the audio is against where the engine says it is.

    `test_output_maps_back_to_source_time_at_exactly_the_rate` checks the
    aggregate ratio, which was never wrong -- which is exactly why this went
    unnoticed. A cycle emits a whole grain of *unstretched* source, so inside it
    the content advances at 1.0x while the map advances at `rate`. Measured
    uncorrected: 40.8 / 32.1 / 20.3ms at 0.25 / 0.5 / 0.75, and exactly 0 at
    1.0x -- the shape of "slow playback is not accurate, 100% is fine".
    """

    def test_the_correction_is_zero_at_full_speed(self):
        """1.0x takes the straight-copy path: no grain, no search, nothing to
        correct. Anything else invents an error in the one case that never had
        one."""
        self.assertEqual(grain_offset_ms(1.0), 0.0)

    def test_the_correction_is_half_the_lead_the_audio_can_have(self):
        for rate in (0.75, 0.5, 0.25):
            lead = SEQUENCE_FRAMES * (1.0 - rate) + SEARCH_FRAMES
            self.assertAlmostEqual(
                grain_offset_ms(rate), lead / 2.0 / SAMPLE_RATE * 1000.0, places=6)

    def test_the_correction_never_moves_while_the_rate_holds(self):
        """The regression that shipped once and must not again.

        A correction tracking each grain's own splice is more accurate on paper
        and unusable in practice: it steps at every grain boundary, and gui.py's
        interpolating clock abandons interpolation and jumps whenever the source
        disagrees by more than POSITION_ALLOWABLE_ERROR_MS * rate -- 8.3ms at
        0.25x. Measured 146 snaps in 1199 ticks, and reported as the playhead
        teleporting. A correction that is a pure function of the rate cannot do
        that, because nothing but a rate change can move it.
        """
        for rate in (0.75, 0.5, 0.25):
            with self.subTest(rate=rate):
                self.assertEqual(
                    len({grain_offset_ms(rate) for _ in range(50)}), 1)


class LookAheadTests(unittest.TestCase):
    """`ensure_grain`: the search costs ~20ms a grain and `_fill` runs on a 10ms
    tick, so a grain built on demand lands exactly when the sink has run dry."""

    def _fresh(self):
        source = _sine(6.0)
        stretcher = TimeStretcher()
        stretcher.reset(0.0)
        return stretcher, source, downmix_to_mono(source)

    def test_a_grain_is_built_before_it_is_needed(self):
        stretcher, source, mono = self._fresh()
        self.assertEqual(len(stretcher._pending), 0)
        stretcher.ensure_grain(source, mono, 0.25)
        self.assertGreaterEqual(
            len(stretcher._pending) // (CHANNELS * 2), SEQUENCE_FRAMES)

    def test_it_stops_at_one_grain(self):
        """`_pending` is also how long a rate change takes to be heard, so it is
        held at one grain rather than filled up: two is a quarter second of the
        old speed after the button says otherwise."""
        stretcher, source, mono = self._fresh()
        for _ in range(5):
            stretcher.ensure_grain(source, mono, 0.25)
        self.assertLess(
            len(stretcher._pending) // (CHANNELS * 2), 2 * SEQUENCE_FRAMES)
