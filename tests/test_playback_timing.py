"""Playback position accuracy/smoothness.

_predicted_audio_position extrapolates with nanosecond precision (integer-ms
elapsed() rounding used to scale with playback rate). _player_position_changed
blends small QMediaPlayer.positionChanged discrepancies in gradually instead
of either hard-snapping (visible stutter) or ignoring them below a fixed
threshold (a small steady-state bias -- worse at non-1x rates -- would then
never get corrected). _render_gameplay_frame schedules off a fixed cadence
instead of restarting its clock every tick, so a timer firing a little late
doesn't permanently shift every future frame too.
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

    def _make_playing(self, rate: float = 1.0) -> None:
        fake = _FakePlaying(rate)
        self.window.player.playbackState = fake.playbackState
        self.window.player.playbackRate = fake.playbackRate

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
