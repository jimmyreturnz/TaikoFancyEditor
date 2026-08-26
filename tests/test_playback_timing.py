"""Playback position accuracy/smoothness.

_predicted_audio_position extrapolates with nanosecond precision (integer-ms
elapsed() rounding used to scale with playback rate). _player_position_changed
blends small QMediaPlayer.positionChanged discrepancies in gradually instead
of either hard-snapping (visible stutter) or ignoring them below a fixed
threshold (a small steady-state bias -- worse at non-1x rates -- would then
never get corrected). _render_gameplay_frame schedules off a fixed cadence
instead of restarting its clock every tick, so a timer firing a little late
doesn't permanently shift every future frame too.

Two of these were live bugs at 25/50/75%. _change_playback_speed and
toggle_playback used to re-anchor from QMediaPlayer.position() -- whole
milliseconds, stale by up to one backend report, 0 before the backend has said
anything, and read *after* setPlaybackRate -- so the act of clicking a speed
button moved the playhead. And the hard-resync threshold was held in song
milliseconds while the discontinuities it classifies are real-time events, so
at 0.25x it sat four times too high and genuine stalls never snapped.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gui
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class _FakePlaying:
    """Stands in for QMediaPlayer's state/rate so tests don't need real audio."""

    def __init__(self, rate: float = 1.0) -> None:
        self.rate = rate

    def playbackState(self):
        return gui.QMediaPlayer.PlayingState

    def playbackRate(self):
        return self.rate

    def setPlaybackRate(self, rate):
        self.rate = float(rate)


class _FakeClock:
    """QElapsedTimer with the wall clock under the test's control.

    Real elapsed time is unusable here: the loop being tested is driven by how
    much *wall* time passed between two position reports, and back-to-back
    Python calls pass none of it -- which is a playback situation that cannot
    occur and would hide any rate dependence rather than expose it.
    """

    def __init__(self) -> None:
        self.now_ns = 0
        self._started_ns = 0

    def advance_ms(self, wall_ms: float) -> None:
        self.now_ns += int(wall_ms * 1_000_000)

    def start(self) -> None:
        self._started_ns = self.now_ns

    def restart(self) -> None:
        self._started_ns = self.now_ns

    def nsecsElapsed(self) -> int:
        return self.now_ns - self._started_ns


class PlaybackTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        path = write_fixture(directory, "full_v14")

        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(path, refresh_difficulties=True)

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()

    def _make_playing(self, rate: float = 1.0) -> _FakePlaying:
        fake = _FakePlaying(rate)
        self.window.player.playbackState = fake.playbackState
        self.window.player.playbackRate = fake.playbackRate
        self.window.player.setPlaybackRate = fake.setPlaybackRate
        return fake

    def _take_the_clock(self) -> _FakeClock:
        clock = _FakeClock()
        self.window.audio_anchor_clock = clock
        return clock

    # -- extrapolation --------------------------------------------------------

    def test_predicted_position_is_exact_anchor_when_paused(self):
        self.assertNotEqual(self.window.player.playbackState(), gui.QMediaPlayer.PlayingState)
        self.window.audio_anchor_position = 5000
        self.assertEqual(self.window._predicted_audio_position(), 5000.0)

    def test_predicted_position_extrapolates_while_playing(self):
        self._make_playing(rate=1.0)
        self.window.audio_anchor_position = 1000
        self.window.audio_anchor_clock.restart()
        predicted = self.window._predicted_audio_position()
        self.assertGreaterEqual(predicted, 1000.0)

    # -- resync: gradual for small errors, immediate for large ones -----------

    def test_small_discrepancy_is_blended_not_snapped(self):
        self._make_playing()
        self.window.audio_anchor_position = 1000
        self.window.audio_anchor_clock.restart()
        predicted = self.window._predicted_audio_position()
        noisy_report = int(predicted) + 10

        self.window._player_position_changed(noisy_report)

        self.assertNotEqual(self.window.audio_anchor_position, predicted, "must still correct")
        self.assertLess(
            abs(self.window.audio_anchor_position - noisy_report), 10,
            "must not snap all the way to the noisy report either",
        )

    def test_steady_state_bias_converges_over_repeated_reports(self):
        """A consistent small bias (e.g. backend latency at a slow rate) must
        not go permanently uncorrected just because no single report crosses
        the hard-resync threshold."""
        self._make_playing()
        self.window.audio_anchor_position = 1000
        self.window.audio_anchor_clock.restart()

        for _ in range(30):
            predicted = self.window._predicted_audio_position()
            self.window._player_position_changed(round(predicted) + 15)

        final_predicted = self.window._predicted_audio_position()
        self.assertLess(abs(self.window.audio_anchor_position - final_predicted), 5)

    def test_large_discrepancy_snaps_immediately(self):
        self._make_playing()
        self.window.audio_anchor_position = 1000
        self.window.audio_anchor_clock.restart()
        predicted = self.window._predicted_audio_position()
        big_jump = int(predicted) + 500

        self.window._player_position_changed(big_jump)

        self.assertEqual(self.window.audio_anchor_position, big_jump)

    def test_paused_report_re_anchors_exactly(self):
        self.window.audio_anchor_position = 1000
        self.window._player_position_changed(4242)
        self.assertEqual(self.window.audio_anchor_position, 4242)

    # -- the resync threshold is a real-time budget, not a song-time one ------

    def test_the_resync_threshold_scales_with_the_playback_rate(self):
        """200ms of real time is 50ms of song at 0.25x. Held as a fixed song-time
        number the threshold was four times too loose there, so a genuine stall
        never snapped and crawled in over a dozen reports instead."""
        for rate, error_ms, should_snap in (
            (1.0, 60.0, False), (1.0, 260.0, True),
            (0.25, 60.0, True), (0.25, 40.0, False),
        ):
            with self.subTest(rate=rate, error_ms=error_ms):
                self._make_playing(rate)
                clock = self._take_the_clock()
                self.window.audio_anchor_position = 1000.0
                clock.restart()
                report = round(1000.0 + error_ms)
                self.window._player_position_changed(report)
                if should_snap:
                    self.assertEqual(self.window.audio_anchor_position, report)
                else:
                    self.assertNotEqual(self.window.audio_anchor_position, report)

    # -- tracking convergence, at every rate ----------------------------------

    def _run_reports(self, rate: float, count: int = 20, quantize: bool = True) -> list[float]:
        """Simulate `count` position reports and return the tracking error at
        each one, in song milliseconds.

        The model is the real one: media advances at `rate` song-ms per wall-ms,
        the backend reports on a wall-clock cadence, and what it reports is
        behind the truth by a fixed *output latency in wall time* -- which is
        `latency * rate` song milliseconds, i.e. smaller at slow rates.

        `quantize` is Qt's whole-millisecond reporting. Real, and on by default,
        but it is 0.5ms of noise on top of an error that is only 10ms to begin
        with at 0.25x -- enough to swamp a measurement of the decay *ratio*, so
        the test that measures that one turns it off.
        """
        interval_wall_ms, latency_wall_ms = 50.0, 40.0
        self._make_playing(rate)
        clock = self._take_the_clock()
        true_position = 1000.0
        self.window.audio_anchor_position = true_position
        clock.restart()

        errors = []
        for _ in range(count):
            clock.advance_ms(interval_wall_ms)
            true_position += interval_wall_ms * rate
            report = true_position - latency_wall_ms * rate
            if quantize:
                report = round(report)
            errors.append(self.window._predicted_audio_position() - report)
            self.window._player_position_changed(report)
        return errors

    def test_tracking_converges_at_every_playback_rate(self):
        for rate in (0.25, 0.5, 0.75, 1.0):
            with self.subTest(rate=rate):
                errors = self._run_reports(rate)
                self.assertGreater(abs(errors[0]), 1.0, "the bias has to be real to be worth correcting")
                self.assertLess(
                    abs(errors[-1]), 1.0,
                    "must converge to within the reports' own 1ms quantization",
                )

    def test_convergence_is_not_rate_dependent(self):
        """The prediction already carries the rate forward, so the residual
        decays by (1 - POSITION_CORRECTION_FACTOR) per report whatever the rate
        is -- there is no ramp lag for a rate-aware gain to remove. A gain that
        acquired a rate term would break this."""
        decays = {}
        for rate in (0.25, 0.5, 0.75, 1.0):
            errors = self._run_reports(rate, count=6, quantize=False)
            decays[rate] = [errors[i + 1] / errors[i] for i in range(len(errors) - 1)]
        for rate, ratios in decays.items():
            for ratio in ratios:
                self.assertAlmostEqual(
                    ratio, 1 - gui.POSITION_CORRECTION_FACTOR, places=2, msg=f"rate {rate}",
                )

    # -- re-anchoring: never from the backend's rounded, stale position -------

    def test_changing_speed_while_paused_does_not_move_the_playhead(self):
        """QMediaPlayer.position() is 0 until the backend has reported, so this
        used to yank a paused playhead back to the start of the song -- on the
        speed-button click, which is why only 25/50/75% ever looked wrong."""
        self.window.player.position = lambda: 0
        self.window.seek_audio(31234.5)

        self.window._change_playback_speed(0.25)

        self.assertAlmostEqual(self.window.audio_anchor_position, 31234.5, places=6)
        self.assertAlmostEqual(self.window._predicted_audio_position(), 31234.5, places=6)

    def test_changing_speed_while_playing_introduces_no_step(self):
        fake = self._make_playing(1.0)
        clock = self._take_the_clock()
        self.window.player.position = lambda: 0
        self.window.audio_anchor_position = 1000.0
        clock.restart()
        clock.advance_ms(100.0)
        before = self.window._predicted_audio_position()
        self.assertAlmostEqual(before, 1100.0, places=6)

        self.window._change_playback_speed(0.25)

        self.assertEqual(fake.rate, 0.25, "the rate really did change")
        self.assertAlmostEqual(
            self.window._predicted_audio_position(), before, places=6,
            msg="the handover must be continuous: no jump at the instant of the change",
        )
        clock.advance_ms(100.0)
        self.assertAlmostEqual(
            self.window._predicted_audio_position(), before + 25.0, places=6,
            msg="and everything after it runs at the new rate",
        )

    def test_a_speed_change_mid_playback_leaves_nothing_for_the_blend_to_fix(self):
        """The old anchoring injected a step error that then had to be blended
        away over a dozen reports -- a visibly wrong playhead for as long as it
        took. There should be nothing to correct in the first place."""
        self._make_playing(1.0)
        clock = self._take_the_clock()
        self.window.player.position = lambda: 0
        true_position = 1000.0
        self.window.audio_anchor_position = true_position
        clock.restart()
        clock.advance_ms(50.0)
        true_position += 50.0

        self.window._change_playback_speed(0.5)

        clock.advance_ms(50.0)
        true_position += 50.0 * 0.5
        error = round(true_position) - self.window._predicted_audio_position()
        self.assertLess(abs(error), 1.0, f"step error of {error}ms survived the rate change")

    def test_resuming_playback_keeps_the_fractional_playhead(self):
        """seek_audio keeps the exact position on purpose; play must not round
        it off against the backend's whole-millisecond echo."""
        self.window.player.play = lambda: None
        self.window.player.position = lambda: 0
        self.window.seek_audio(7777.25)

        self.window.toggle_playback()

        self.assertAlmostEqual(self.window.audio_anchor_position, 7777.25, places=6)

    # -- frame scheduler --------------------------------------------------------

    def test_frame_scheduler_resyncs_after_a_stall_instead_of_bursting(self):
        self.window._next_frame_due_ns = 1000  # far in the past
        self.window._render_gameplay_frame()

        now_ns = self.window.gameplay_frame_clock.nsecsElapsed()
        self.assertGreater(self.window._next_frame_due_ns, now_ns)
        self.assertLessEqual(
            self.window._next_frame_due_ns,
            now_ns + self.window.gameplay_frame_interval_ns + 1_000_000,
        )

    def test_frame_scheduler_advances_by_exactly_one_interval_normally(self):
        # Just past due, not a stall -- setting this to 0 would itself be
        # more than a full interval behind "now" and trigger the resync
        # branch instead of the normal one.
        now_ns = self.window.gameplay_frame_clock.nsecsElapsed()
        self.window._next_frame_due_ns = now_ns - 1000
        before = self.window._next_frame_due_ns
        self.window._render_gameplay_frame()
        self.assertEqual(self.window._next_frame_due_ns, before + self.window.gameplay_frame_interval_ns)


if __name__ == "__main__":
    unittest.main()
