"""Playback position accuracy/smoothness.

Adapted from osu!(lazer)'s InterpolatingFramedClock. Two clocks:

* The **source** clock (_source_clock_position) is the last raw
  QMediaPlayer.positionChanged report, extrapolated forward by elapsed real
  time * rate. Reports land tens of milliseconds apart, so this alone is far
  too sparse to drive a per-frame display.
* The **interpolated** clock (self.audio_anchor_position) is what is actually
  displayed. _advance_interpolated_clock nudges it 1/POSITION_INTERPOLATION_
  DIVISOR of the way toward the source clock every *rendered frame* (not
  every report), or snaps it straight to the source clock if the two have
  drifted more than POSITION_ALLOWABLE_ERROR_MS apart.

Blending every frame instead of only at report time is what makes the
correction invisible. A monotonic clamp is mandatory: a blend toward a source
clock that is momentarily *behind* the interpolated one (a negative error --
completely normal report jitter) would otherwise step the displayed playhead
backwards every time it happened, which is the single most visible symptom
this model exists to prevent. _predicted_audio_position (the interpolated
clock plus its own small sub-frame extrapolation, for callers between
rendered frames) carries the same monotonic guarantee.

_change_playback_speed and toggle_playback re-anchor from
_predicted_audio_position(), sampled *before* the rate/state actually changes
-- see their own comments for why reading QMediaPlayer.position() there used
to move the playhead on every speed-button click at 25/50/75%.
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

    def _take_source_clock(self) -> _FakeClock:
        clock = _FakeClock()
        self.window._source_report_clock = clock
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

    def test_predicted_position_never_travels_backwards(self):
        """A stale-but-higher last prediction (e.g. left over from just
        before a rate/state change) must clamp a lower raw reading up to it
        rather than let the displayed playhead visibly jump backwards."""
        self._make_playing(rate=1.0)
        self.window.audio_anchor_position = 1000.0
        self.window.audio_anchor_clock.restart()
        self.window._last_predicted_position = 1005.0

        predicted = self.window._predicted_audio_position()

        self.assertEqual(predicted, 1005.0)

    # -- _advance_interpolated_clock: the per-frame blend/snap -----------------

    def test_advance_blends_small_error_by_one_eighth_per_frame(self):
        self._make_playing(rate=1.0)
        self._take_source_clock()
        self.window.audio_anchor_position = 1000.0
        self.window.latest_audio_position = 1010.0  # source is 10ms ahead

        self.window._advance_interpolated_clock(0.0)

        self.assertAlmostEqual(self.window.audio_anchor_position, 1000.0 + 10.0 / 8.0, places=6)

    def test_advance_snaps_when_error_exceeds_allowable(self):
        self._make_playing(rate=1.0)
        self._take_source_clock()
        self.window.audio_anchor_position = 1000.0
        self.window.latest_audio_position = 1000.0 + gui.POSITION_ALLOWABLE_ERROR_MS + 5.0

        self.window._advance_interpolated_clock(0.0)

        self.assertEqual(self.window.audio_anchor_position, self.window.latest_audio_position)

    def test_advance_does_not_snap_just_under_the_allowable_error(self):
        self._make_playing(rate=1.0)
        self._take_source_clock()
        self.window.audio_anchor_position = 1000.0
        self.window.latest_audio_position = 1000.0 + gui.POSITION_ALLOWABLE_ERROR_MS - 1.0

        self.window._advance_interpolated_clock(0.0)

        self.assertNotEqual(self.window.audio_anchor_position, self.window.latest_audio_position)

    def test_advance_never_moves_backwards_on_a_negative_error(self):
        """The source clock reading behind the interpolated one is ordinary
        report jitter, not a seek -- blending toward it must never step the
        displayed playhead backwards."""
        self._make_playing(rate=1.0)
        self._take_source_clock()
        self.window.audio_anchor_position = 1000.0
        self.window.latest_audio_position = 990.0  # source is 10ms behind

        self.window._advance_interpolated_clock(0.0)

        self.assertGreaterEqual(self.window.audio_anchor_position, 1000.0)

    def test_advance_repeated_frames_converge_on_a_steady_bias(self):
        """A consistent small bias converges over a handful of frames even
        though no single frame's error ever exceeds the allowable threshold."""
        self._make_playing(rate=1.0)
        self._take_source_clock()
        self.window.audio_anchor_position = 1000.0
        self.window.latest_audio_position = 1015.0

        for _ in range(200):
            self.window._advance_interpolated_clock(0.0)

        self.assertLess(abs(self.window.audio_anchor_position - 1015.0), 0.01)

    def test_advance_while_paused_snaps_to_source_without_advancing(self):
        # Not playing: _advance_interpolated_clock must not extrapolate by
        # frame time, only ever reflect the (unextrapolated) source.
        self.window.audio_anchor_position = 1000.0
        self.window.latest_audio_position = 1000.0

        self.window._advance_interpolated_clock(50.0)

        self.assertEqual(self.window.audio_anchor_position, 1000.0)

    def test_player_position_changed_while_playing_only_updates_the_source(self):
        """No blending happens synchronously any more -- a report just moves
        where the source clock reads from; _advance_interpolated_clock is
        what actually moves the displayed playhead, once per frame."""
        self._make_playing(rate=1.0)
        self.window.audio_anchor_position = 1000.0

        self.window._player_position_changed(1500)

        self.assertEqual(self.window.audio_anchor_position, 1000.0)
        self.assertEqual(self.window.latest_audio_position, 1500.0)

    def test_paused_report_re_anchors_exactly(self):
        self.window.audio_anchor_position = 1000
        self.window._player_position_changed(4242)
        self.assertEqual(self.window.audio_anchor_position, 4242)

    def test_paused_report_under_one_ms_is_ignored(self):
        """Qt's own echo of a seek we just made must not perturb the anchor."""
        self.window.audio_anchor_position = 1000.4
        self.window.latest_audio_position = 1000.4
        self.window._player_position_changed(1000)
        self.assertEqual(self.window.audio_anchor_position, 1000.4)

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

    def test_changing_speed_rebases_the_source_clock_too(self):
        """The source clock needs the same handover as the interpolated one.

        _source_clock_position extrapolates the last backend report by elapsed
        real time * playbackRate(). Backend reports land tens of milliseconds
        apart, so leaving the report where it was would replay that whole
        interval at the new rate -- at 0.25x, an error well past
        POSITION_ALLOWABLE_ERROR_MS, which makes the next frame *snap* the
        displayed playhead to a wrong source. That snap is the speed-button
        jump this clock exists to remove, so it must not come back in through
        the source side.
        """
        self._make_playing(1.0)
        source = self._take_source_clock()
        self._take_the_clock()
        self.window.player.position = lambda: 0
        self.window.latest_audio_position = 1000.0
        self.window.audio_anchor_position = 1000.0
        source.restart()
        source.advance_ms(100.0)
        self.assertAlmostEqual(self.window._source_clock_position(), 1100.0, places=6)

        self.window._change_playback_speed(0.25)

        self.assertAlmostEqual(
            self.window._source_clock_position(), 1100.0, places=6,
            msg="rebased at the old rate: the source must not step at the change",
        )
        source.advance_ms(100.0)
        self.assertAlmostEqual(
            self.window._source_clock_position(), 1125.0, places=6,
            msg="and runs at the new rate from there -- not 1000 + 200 * 0.25",
        )

    def test_resuming_playback_keeps_the_fractional_playhead(self):
        """seek_audio keeps the exact position on purpose; play must not round
        it off against the backend's whole-millisecond echo."""
        self.window.player.play = lambda: None
        self.window.player.position = lambda: 0
        self.window.seek_audio(7777.25)

        self.window.toggle_playback()

        self.assertAlmostEqual(self.window.audio_anchor_position, 7777.25, places=6)

    # -- seeking: the one legitimate way to move backwards ---------------------

    def test_seek_backwards_is_not_clamped_away(self):
        """A monotonic clock guard must never fight a real seek: rewinding
        past the interpolated clock's current value has to actually land
        there, not get pinned to the higher pre-seek position."""
        self._make_playing(rate=1.0)
        self._take_source_clock()
        self._take_the_clock()
        self.window.player.position = lambda: 0
        self.window.audio_anchor_position = 5000.0
        self.window._last_predicted_position = 5000.0

        self.window.seek_audio(1000.0)

        self.assertEqual(self.window.audio_anchor_position, 1000.0)
        self.assertEqual(self.window._predicted_audio_position(), 1000.0)
        # And the next frame's blend must not fight the seek either: the
        # source clock was reset to the same position, so there is no error
        # for it to (wrongly) snap or blend away from.
        self.window._advance_interpolated_clock(0.0)
        self.assertEqual(self.window.audio_anchor_position, 1000.0)

    def test_seek_forward_still_works_after_a_backwards_seek(self):
        self._make_playing(rate=1.0)
        self.window.player.position = lambda: 0
        self.window.seek_audio(5000.0)
        self.window.seek_audio(1000.0)

        self.window.seek_audio(2000.0)

        self.assertEqual(self.window.audio_anchor_position, 2000.0)

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
