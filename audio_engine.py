"""The music transport: decode to PCM ourselves, and drive the output device.

Replaces `QMediaPlayer` for song playback. The reason is not preference, it is
that everything making osu!'s editor accurate lives below the level Qt's player
exposes, and none of it can be reached from up there:

- **The clock is a sample cursor.** `TrackBass.updateCurrentTime` asks BASS for
  a byte position and converts it. `QAudioSink.processedUSecs()` is the same
  thing -- measured, not assumed: writing into a 250ms sink buffer keeps the
  written cursor exactly 250ms ahead of `processedUSecs`, so what it reports is
  audio actually played out, not audio handed over. `QMediaPlayer.position()`
  is a signal in whole milliseconds arriving whenever the backend feels like it.
- **Slowing down preserves pitch.** osu!'s editor slows down with
  `AdjustableProperty.Tempo` (BASS_FX time-stretch) at exactly the rates this
  app offers -- 0.25, 0.5, 0.75, 1.0 -- not with a frequency change. Qt's
  `setPlaybackRate` is a plain resample: measured on the FFmpeg backend, a
  0.25x request delivers 0.261s of source per second of wall clock, which is a
  turntable slowed down, pitch and all. So the stretch is ours to do, and
  `TimeStretcher` below is the WSOLA that does it.
- **A rate change is a live attribute, not a restart.** osu! sets a tempo value
  on a running stream. `QMediaPlayer.setPlaybackRate` stalls the backend:
  measured on Windows Media Foundation, 114ms of song time vanishes across a
  0.25x -> 1.0x switch and 67ms at 1.0x -> 0.75x. Here the rate is a number the
  stretcher reads on its next grain, so there is nothing to stall.
- **Output latency is ours to choose.** The sink's buffer is 250ms by default
  and settable; `QMediaPlayer` never exposed it.

It also removes the Ogg problem outright. `QAudioDecoder` decodes through Qt's
FFmpeg decoder whatever the platform backend is, so there is no format the app
must fall back for, and no deprecated Windows backend to depend on.

The engine runs on its own thread. The stretch costs about 22% of a core at
0.25x (`tools/bench_timestretch.py`) -- affordable, but nowhere near affordable
on the thread that has 8.33ms to paint a frame in.
"""
from __future__ import annotations

import array
import math
from operator import mul

from PySide6.QtCore import (
    QElapsedTimer, QMetaObject, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtMultimedia import (
    QAudioDecoder, QAudioFormat, QAudioSink, QMediaDevices, QMediaPlayer,
)

# One format everywhere: the decoder is told to convert to it, so nothing
# downstream has to care what the file was.
SAMPLE_RATE = 44100
CHANNELS = 2

# How much audio sits between `write()` and the speakers. Qt's default is 250ms,
# a quarter second of latency nothing in the editor could compensate for. 40ms
# is short enough to stop mattering and long enough that a late pump does not
# underrun, given the pump runs every 10ms.
SINK_BUFFER_MS = 40
PUMP_INTERVAL_MS = 10

# A single seek rebuilds the sink (`_land_seek`) in ~10-40ms, measured
# (`tools/measure_seek.py`'s `_open_sink` timings). A *drag* fires one seek per
# mouse-move, faster than that -- `tools/measure_scrub.py` measured 549ms of
# the device getting nothing at all across a ~1s burst 8ms apart, because each
# seek tore the previous rebuild down before it produced a single sample.
# `_Engine.seek` coalesces any seek arriving within this many ms of the last
# landed one; comfortably above the worst rebuild cost measured so a burst
# never outruns its own landing.
SEEK_COALESCE_MS = 50

# How long a fade-in runs on every (re)opened sink -- the first play and every
# seek while playing alike. A seek's restart cuts the old stream with nothing
# of its own (`seek`'s docstring: no way to discard the buffered old position
# but a restart) and the new one starts at whatever sample the target happens
# to land on -- measured at 25% of full scale for a mid-song seek
# (`tools/measure_seek.py`), which is a click, not a timing stutter. Long
# enough to flatten that; short enough that a fade is not what it sounds like.
FADE_FRAMES = int(SAMPLE_RATE * 0.005)  # 5ms

# WSOLA, in frames.
#
# SEQUENCE is how much output one grain contributes and OVERLAP is how much of
# that is cross-faded with the previous grain. **SEQUENCE must be much larger
# than OVERLAP.** The first version of this had them equal, which meant every
# output sample was a blend of two different parts of the song -- comb
# filtering from end to end, measured at 0.015 tonality (a 440Hz sine came out
# with 1.5% of its energy still at 440Hz). SoundTouch, which is what BASS_FX
# runs and therefore what osu! sounds like, uses an 82ms sequence against an
# 8ms overlap, so roughly 90% of its output is untouched source. These are
# tuned by `tools/measure_stretch_quality.py`, not by ear.
#
# SEARCH is how far the read head may slide to find a splice that lines up with
# what was already written -- the whole difference between time-stretching and
# chopping. It has to span at least one period of the lowest frequency that
# matters, or bass simply cannot be aligned: 20ms covers 50Hz.
SEQUENCE_FRAMES = 3616      # 82ms
OVERLAP_FRAMES = 353        # 8ms
GRAIN_FRAMES = SEQUENCE_FRAMES + OVERLAP_FRAMES
SEARCH_FRAMES = 882         # +-20ms
# BASS_FX takes the same shortcut behind BASS_ATTRIB_TEMPO_OPTION_USE_QUICKALGO.
SEARCH_STEP = 2
# One grain (SEQUENCE_FRAMES of output) is produced synchronously inside
# `_Engine._fill`, once every ~82ms of playback regardless of rate -- the
# grain size, not the rate, sets how often the search runs. At
# CORRELATION_STEP=1 that search cost 20-28ms against the pump's 10ms budget
# (`tools/measure_slow_rate_playback.py` at 0.25/0.5/0.75x: ~12% of `_fill()`
# calls over budget, max 28ms), which is most of SINK_BUFFER_MS's 40ms of
# slack on a single grain and was the sustained-slow-rate stutter report.
# Subsampling the correlation input -- every other frame of the dot product,
# same trick as SEARCH_STEP but on the other axis of the search -- halved that
# cost (max 15-16ms, calls over budget ~0.6%, no run since produced a real
# hardware-buffer drain) with no measured change in
# `tools/measure_stretch_quality.py`'s tonality/warble/click numbers.
CORRELATION_STEP = 2
# Fixed point for the window, so the overlap-add stays in integer arithmetic.
WINDOW_BITS = 12
WINDOW_ONE = 1 << WINDOW_BITS


def _crossfade_window(frames: int) -> array.array:
    """Raised cosine rising from 0 to 1 across the overlap.

    Over the *overlap*, not over the grain: the fade has to complete inside the
    short blend region, and a window sized to the whole grain would leave the
    two halves summing to well under one for most of it.
    """
    return array.array("i", (
        int(WINDOW_ONE * (0.5 - 0.5 * math.cos(math.pi * i / frames)))
        for i in range(frames)
    ))


def downmix_to_mono(source: array.array) -> array.array:
    """The correlation input, built once per track rather than per grain.

    The splice search is the entire cost of `TimeStretcher`, and searching one
    channel instead of two halves it. Averaging rather than summing keeps it in
    16-bit range so the scores cannot overflow into nonsense on loud material.
    """
    return array.array("h", (
        (source[i] + source[i + 1]) // 2 for i in range(0, len(source), CHANNELS)
    ))


def _clip(value: int) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return value


class TimeStretcher:
    """Pitch-preserving time-stretch: WSOLA over interleaved 16-bit stereo.

    Deliberately free of Qt and of any I/O, so the interesting half is testable
    without an audio device -- the same reason `HitsoundPlayer.pending` is.
    """

    def __init__(self) -> None:
        self._window = _crossfade_window(OVERLAP_FRAMES)
        self.reset(0.0)

    def reset(self, source_frame: float) -> None:
        """Move the read head, dropping any overlap or pending output carried
        across the jump.

        Every seek and every stop comes through here. Keeping the tail would
        splice the end of the old position onto the start of the new one, and
        keeping the pending buffer would play a fragment of the old position
        first -- both audible at exactly the moment the user asked to be
        somewhere else.
        """
        self._read = float(source_frame)
        self._tail = array.array("h", bytes(2 * CHANNELS * OVERLAP_FRAMES))
        self._tail_mono = array.array("h", bytes(2 * OVERLAP_FRAMES))
        self._pending = bytearray()
        self._pending_rate = 1.0

    @property
    def source_frame(self) -> float:
        """Where in the source the *next returned* sample comes from.

        Not where the grain generator has reached: a whole grain is 82ms of
        output, and reporting the generator's position would put the playhead
        that far ahead of the audio. The pending buffer was produced at one
        rate, because a rate change only takes effect on the next grain.
        """
        pending_frames = len(self._pending) // (CHANNELS * 2)
        return self._read - pending_frames * self._pending_rate

    def pull(self, source: array.array, mono: array.array, frames: int,
             rate: float) -> bytes:
        """Return up to `frames` output frames, consuming `frames * rate`.

        Grains are generated whole and held in `_pending`, so the caller may
        ask for any size it likes -- the sink asks for whatever space it has
        free, which is far less than one grain.
        """
        wanted = frames * CHANNELS * 2
        while len(self._pending) < wanted:
            if not self._produce(source, mono, rate):
                break
        block = bytes(self._pending[:wanted])
        del self._pending[:wanted]
        return block

    def _produce(self, source: array.array, mono: array.array, rate: float) -> bool:
        """Append one grain's worth of output. False when the source runs out."""
        available = len(mono)
        start = int(self._read)
        if rate == 1.0:
            # Nothing to stretch: hand the decoded samples straight through, so
            # the rate the editor spends nearly all its time at has no splices
            # to colour it and costs nothing to play.
            end = min(available, start + SEQUENCE_FRAMES)
            if end <= start:
                return False
            self._pending += source[start * CHANNELS:end * CHANNELS].tobytes()
            self._pending_rate = 1.0
            self._read = float(end)
            return True

        low = max(0, start - SEARCH_FRAMES)
        high = min(available - GRAIN_FRAMES, start + SEARCH_FRAMES)
        if high <= low:
            return False
        # Which offset best continues the tail already written? Correlating
        # keeps waveform periods lined up across the splice; without it the
        # overlap cancels itself and the result warbles. `sum(map(mul, ...))`
        # runs the multiply-add in C, which is what makes this affordable in
        # Python at all -- an explicit loop is about ten times slower.
        tail_mono = self._tail_mono
        if CORRELATION_STEP > 1:
            tail_mono = tail_mono[::CORRELATION_STEP]
        span = OVERLAP_FRAMES
        best_offset, best_score = low, None
        for candidate in range(low, high, SEARCH_STEP):
            window = mono[candidate:candidate + span]
            if CORRELATION_STEP > 1:
                window = window[::CORRELATION_STEP]
            score = sum(map(mul, tail_mono, window))
            if best_score is None or score > best_score:
                best_score, best_offset = score, candidate

        grain = source[best_offset * CHANNELS:(best_offset + GRAIN_FRAMES) * CHANNELS]
        out = array.array("h", bytes(2 * CHANNELS * SEQUENCE_FRAMES))
        window = self._window
        tail = self._tail
        # Cross-fade only the overlap; the rest of the sequence is the source
        # untouched, which is where the quality comes from.
        for i in range(OVERLAP_FRAMES):
            weight = window[i]
            inverse = WINDOW_ONE - weight
            left = i * CHANNELS
            out[left] = _clip(
                (grain[left] * weight + tail[left] * inverse) >> WINDOW_BITS)
            out[left + 1] = _clip(
                (grain[left + 1] * weight + tail[left + 1] * inverse) >> WINDOW_BITS)
        out[OVERLAP_FRAMES * CHANNELS:] = grain[OVERLAP_FRAMES * CHANNELS:
                                                SEQUENCE_FRAMES * CHANNELS]
        # The tail is what naturally follows what was just emitted, so the next
        # grain is looked for against a real continuation of the song.
        self._tail = grain[SEQUENCE_FRAMES * CHANNELS:]
        self._tail_mono = mono[best_offset + SEQUENCE_FRAMES:best_offset + GRAIN_FRAMES]
        self._pending += out.tobytes()
        self._pending_rate = rate
        # The read head advances by sequence * rate: that ratio, and only it, is
        # what makes the output 1/rate times longer than the input. The search
        # moves where a grain is taken from, never how far the head advances,
        # which is why WSOLA changes duration without changing the mapping from
        # output time back to source time.
        self._read += SEQUENCE_FRAMES * rate
        return True



class _Engine(QObject):
    """Decoder, sink and pump. Lives on the audio thread and owns every Qt audio
    object here, because a QAudioSink may only be used from the thread that made
    it.

    Everything is a slot: the facade below talks to it exclusively through
    queued signals, which is why no lock appears anywhere in this file.
    """

    position_changed = Signal(float)
    duration_changed = Signal(int)
    state_changed = Signal(object)
    error_occurred = Signal(object, str)

    def __init__(self) -> None:
        super().__init__()
        self._format = QAudioFormat()
        self._format.setSampleRate(SAMPLE_RATE)
        self._format.setChannelCount(CHANNELS)
        self._format.setSampleFormat(QAudioFormat.Int16)
        self._decoder = None
        self._sink = None
        self._device = None
        self._pump = None
        self._stretcher = TimeStretcher()
        # ponytail: the whole track is decoded into memory -- 50MB for a 4:43
        # song, measured. Streaming would halve that at the cost of making every
        # backwards seek a re-decode, and backwards seeks are what an editor does.
        self._pcm = array.array("h")
        self._mono = array.array("h")
        self._rate = 1.0
        self._volume = 1.0
        self._written = 0
        self._segments = []
        self._fade_remaining = 0
        self._state = QMediaPlayer.StoppedState
        self._duration_ms = 0
        # Seek coalescing (SEEK_COALESCE_MS): the timer that lands a parked
        # target, the target itself, and when the last real rebuild landed.
        self._seek_timer = None
        self._pending_seek_ms = None
        self._last_seek_wall = None
        self._seek_clock = QElapsedTimer()
        self._seek_clock.start()

    # -- loading --------------------------------------------------------------

    @Slot()
    def release(self) -> None:
        """Let go of the device and the file, on the audio thread.

        Called blocking from `TrackPlayer.shutdown` before the thread's event
        loop is asked to quit, because a queued stop would simply never run --
        and until the decoder lets go, the source file stays open. On Windows
        that is a live lock: the dialog that generates a click track into a
        temporary directory could not delete it afterwards.
        """
        self.stop()
        self._release_decoder()

    def _release_decoder(self) -> None:
        """Stop the decoder and unhook it before dropping the reference.

        Its signals are queued, so a `bufferReady` emitted just before the
        object goes away is still in the queue when the slot runs -- and
        `self._decoder.read()` on a deleted C++ object raises. Disconnecting
        first is what makes that impossible rather than unlikely.
        """
        if self._decoder is None:
            return
        decoder, self._decoder = self._decoder, None
        try:
            decoder.bufferReady.disconnect(self._take_buffer)
            decoder.error.disconnect(self._decode_failed)
            decoder.durationChanged.disconnect(self._decoder_duration)
        except (RuntimeError, TypeError):
            # Already disconnected, or the C++ side is gone.
            pass
        decoder.stop()
        decoder.deleteLater()

    @Slot(str)
    def load(self, path: str) -> None:
        self.stop()
        self._release_decoder()
        self._pcm = array.array("h")
        self._mono = array.array("h")
        self._duration_ms = 0
        self._stretcher.reset(0.0)
        self.duration_changed.emit(0)
        if not path:
            return
        # Parented, so its lifetime is the engine's rather than whatever the
        # Python reference happens to be doing.
        self._decoder = QAudioDecoder(self)
        self._decoder.setAudioFormat(self._format)
        self._decoder.bufferReady.connect(self._take_buffer)
        self._decoder.error.connect(self._decode_failed)
        self._decoder.durationChanged.connect(self._decoder_duration)
        self._decoder.setSource(QUrl.fromLocalFile(path))
        self._decoder.start()

    @Slot()
    def _take_buffer(self) -> None:
        if self._decoder is None:
            return
        buffer = self._decoder.read()
        if not buffer.isValid():
            return
        block = array.array("h")
        block.frombytes(bytes(buffer.constData()))
        self._pcm.extend(block)
        # Extended as the track arrives rather than built in one pass at the
        # end, so playback can start before the decode has finished.
        self._mono.extend(downmix_to_mono(block))
        decoded_ms = int(len(self._mono) / SAMPLE_RATE * 1000)
        if decoded_ms > self._duration_ms:
            self._duration_ms = decoded_ms
            self.duration_changed.emit(decoded_ms)

    @Slot(int)
    def _decoder_duration(self, duration_ms: int) -> None:
        if duration_ms > self._duration_ms:
            self._duration_ms = duration_ms
            self.duration_changed.emit(duration_ms)

    @Slot(object)
    def _decode_failed(self, _error) -> None:
        message = self._decoder.errorString() if self._decoder is not None else ""
        self.error_occurred.emit(QMediaPlayer.FormatError, message)

    # -- transport ------------------------------------------------------------

    @Slot()
    def play(self) -> None:
        if self._state == QMediaPlayer.PlayingState:
            return
        if self._sink is None:
            self._open_sink()
        self._set_state(QMediaPlayer.PlayingState)

    @Slot()
    def pause(self) -> None:
        if self._state != QMediaPlayer.PlayingState:
            return
        # The sink is left running with nothing fed to it rather than
        # suspended: suspend/resume is where a device-level stall would come
        # back in, and an idle sink costs nothing.
        self._set_state(QMediaPlayer.PausedState)

    @Slot()
    def stop(self) -> None:
        if self._pump is not None:
            self._pump.stop()
            self._pump = None
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
        self._device = None
        self._written = 0
        self._segments = []
        # A seek parked by SEEK_COALESCE_MS is a promise to land later; stop()
        # (and load(), which calls it first) means that later never comes --
        # otherwise a stale target could land against a sink or track that has
        # since moved on.
        if self._seek_timer is not None:
            self._seek_timer.stop()
        self._pending_seek_ms = None
        self._set_state(QMediaPlayer.StoppedState)

    def _open_sink(self) -> None:
        song_ms = self._stretcher.source_frame / SAMPLE_RATE * 1000.0
        self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), self._format)
        self._sink.setBufferSize(
            int(SINK_BUFFER_MS / 1000.0 * SAMPLE_RATE) * CHANNELS * 2)
        self._sink.setVolume(self._volume)
        self._device = self._sink.start()
        self._written = 0
        self._segments = [(0, song_ms, self._rate)]
        self._fade_remaining = FADE_FRAMES
        self._pump = QTimer(self)
        self._pump.setTimerType(Qt.PreciseTimer)
        self._pump.timeout.connect(self._fill)
        self._pump.start(PUMP_INTERVAL_MS)

    @Slot(float)
    def seek(self, position_ms: float) -> None:
        """Move the playhead, landing the rebuild immediately unless one just
        happened.

        A single click lands with no added latency. A *drag* is a
        `seek_requested` per mouse-move (gui.py's `mouseMoveEvent` on the
        timeline bar) -- `tools/measure_scrub.py` fired 20 of these 15ms
        apart and measured 341ms of the device getting nothing at all, because
        each one tore the sink down before the previous rebuild (`_open_sink`,
        itself ~10-40ms) had produced a single sample; an 8ms-apart burst
        measured 549ms silent out of about a second. `_land_seek` is the
        actual rebuild; a seek arriving inside `SEEK_COALESCE_MS` of the last
        one only parks its target and (re)arms a trailing timer, so a burst
        collapses into the one rebuild the burst ends on, and the sink already
        playing keeps playing right up to that landing instead of going quiet.
        """
        self.position_changed.emit(position_ms)
        if self._sink is not None and self._state == QMediaPlayer.PlayingState:
            now = self._seek_clock.elapsed()
            if self._last_seek_wall is not None and now - self._last_seek_wall < SEEK_COALESCE_MS:
                self._pending_seek_ms = position_ms
                if self._seek_timer is None:
                    self._seek_timer = QTimer(self)
                    self._seek_timer.setSingleShot(True)
                    self._seek_timer.timeout.connect(self._land_pending_seek)
                self._seek_timer.start(SEEK_COALESCE_MS)
                return
            self._last_seek_wall = now
        self._land_seek(position_ms)

    @Slot()
    def _land_pending_seek(self) -> None:
        if self._pending_seek_ms is None:
            return
        position_ms, self._pending_seek_ms = self._pending_seek_ms, None
        self._last_seek_wall = self._seek_clock.elapsed()
        self._land_seek(position_ms)

    def _land_seek(self, position_ms: float) -> None:
        frame = max(0.0, position_ms) / 1000.0 * SAMPLE_RATE
        self._stretcher.reset(frame)
        if self._sink is None:
            return
        # The sink is holding up to SINK_BUFFER_MS of the *old* position and
        # offers no way to discard it but a restart. Playing it out would be the
        # seek audibly arriving late.
        playing = self._state == QMediaPlayer.PlayingState
        self._pump.stop()
        self._sink.stop()
        self._sink = None
        self._pump = None
        self._device = None
        if playing:
            self._open_sink()

    @Slot(float)
    def set_rate(self, rate: float) -> None:
        rate = float(rate)
        if rate == self._rate:
            return
        self._rate = rate
        if self._sink is not None:
            # No flush and no restart: the stretcher reads the new ratio on its
            # next grain, so audio already queued plays out at the old rate and
            # everything after it at the new one. The segment records where the
            # changeover lands in the output, which is what keeps _position_ms
            # honest across it -- without it the in-flight audio would be
            # measured at the wrong rate, which is the 114ms error that made
            # QMediaPlayer's own speed buttons move the offset.
            self._segments.append(
                (self._written,
                 self._stretcher.source_frame / SAMPLE_RATE * 1000.0,
                 rate))

    @Slot(float)
    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._sink is not None:
            self._sink.setVolume(self._volume)

    def _set_state(self, state) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    # -- the pump -------------------------------------------------------------

    @Slot()
    def _fill(self) -> None:
        if self._sink is None or self._device is None:
            return
        if self._state == QMediaPlayer.PlayingState:
            free_frames = self._sink.bytesFree() // (CHANNELS * 2)
            if free_frames > 0:
                block = self._stretcher.pull(
                    self._pcm, self._mono, free_frames, self._rate)
                if block:
                    if self._fade_remaining > 0:
                        block = self._fade_in(block)
                    self._device.write(block)
                    self._written += len(block) // (CHANNELS * 2)
                elif self._decoder is None or not self._decoder.isDecoding():
                    self.stop()
                    return
        self.position_changed.emit(self._position_ms())

    def _fade_in(self, block: bytes) -> bytes:
        """Ramp the leading frames up from silence -- what softens the splice
        a sink restart otherwise leaves at full volume (see `FADE_FRAMES`).

        A raised cosine rather than a straight ramp for the same reason
        `_crossfade_window` is one: gentler at the very start, which is
        where a residual click would otherwise hide.
        """
        samples = array.array("h")
        samples.frombytes(block)
        frames = len(samples) // CHANNELS
        ramp = min(self._fade_remaining, frames)
        done = FADE_FRAMES - self._fade_remaining
        for i in range(ramp):
            gain = 0.5 - 0.5 * math.cos(math.pi * (done + i) / FADE_FRAMES)
            for channel in range(CHANNELS):
                index = i * CHANNELS + channel
                samples[index] = int(samples[index] * gain)
        self._fade_remaining -= ramp
        return samples.tobytes()

    def _position_ms(self) -> float:
        """Song time of the sample leaving the device right now.

        `processedUSecs` is the play cursor, not the write cursor -- measured:
        with a 250ms buffer kept full, the written cursor stays exactly 250ms
        ahead of it. That makes this the sample-accurate equivalent of the byte
        position osu! reads out of BASS, rather than an estimate of one.
        """
        if self._sink is None:
            return self._stretcher.source_frame / SAMPLE_RATE * 1000.0
        played = self._sink.processedUSecs() / 1_000_000.0 * SAMPLE_RATE
        start_frame, song_ms, rate = self._segments[0]
        for segment in self._segments:
            if segment[0] <= played:
                start_frame, song_ms, rate = segment
            else:
                break
        # A segment the play cursor has left can never be needed again.
        while len(self._segments) > 1 and self._segments[1][0] <= played:
            self._segments.pop(0)
        return song_ms + (played - start_frame) / SAMPLE_RATE * 1000.0 * rate


class TrackPlayer(QObject):
    """`QMediaPlayer`'s shape over the engine, so the window keeps its API.

    Only the members gui.py actually uses are here, and the enums are
    QMediaPlayer's own, so every `== QMediaPlayer.PlayingState` comparison in
    the window keeps working untouched.
    """

    positionChanged = Signal(float)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    errorOccurred = Signal(object, str)

    _load = Signal(str)
    _play = Signal()
    _pause = Signal()
    _stop = Signal()
    _seek = Signal(float)
    _rate = Signal(float)
    _volume = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._thread.setObjectName("audio-engine")
        self._engine = _Engine()
        self._engine.moveToThread(self._thread)
        self._thread.finished.connect(self._engine.deleteLater)

        self._load.connect(self._engine.load)
        self._play.connect(self._engine.play)
        self._pause.connect(self._engine.pause)
        self._stop.connect(self._engine.stop)
        self._seek.connect(self._engine.seek)
        self._rate.connect(self._engine.set_rate)
        self._volume.connect(self._engine.set_volume)

        self._engine.position_changed.connect(self._on_position)
        self._engine.duration_changed.connect(self._on_duration)
        self._engine.state_changed.connect(self._on_state)
        self._engine.error_occurred.connect(self.errorOccurred)

        self._position_ms = 0.0
        self._duration_ms = 0
        self._state = QMediaPlayer.StoppedState
        self._playback_rate = 1.0
        self._source = ""
        self._thread.start()

    # -- what the window reads (cached: the engine is on another thread) ------

    @Slot(float)
    def _on_position(self, position_ms: float) -> None:
        self._position_ms = position_ms
        self.positionChanged.emit(position_ms)

    @Slot(int)
    def _on_duration(self, duration_ms: int) -> None:
        self._duration_ms = duration_ms
        self.durationChanged.emit(duration_ms)

    @Slot(object)
    def _on_state(self, state) -> None:
        self._state = state
        self.playbackStateChanged.emit(state)

    def position(self) -> float:
        return self._position_ms

    def duration(self) -> int:
        return self._duration_ms

    def playbackState(self):
        return self._state

    def playbackRate(self) -> float:
        return self._playback_rate

    # -- what the window commands --------------------------------------------

    def setSource(self, url) -> None:
        path = url.toLocalFile() if isinstance(url, QUrl) else str(url)
        self._source = path
        self._position_ms = 0.0
        self._duration_ms = 0
        self._load.emit(path)

    def source(self) -> QUrl:
        return QUrl.fromLocalFile(self._source) if self._source else QUrl()

    def play(self) -> None:
        # Mirrored here rather than waited for: the window reads playbackState
        # on the very next line in places, and a queued round trip to the audio
        # thread will not have happened yet.
        self._state = QMediaPlayer.PlayingState
        self._play.emit()

    def pause(self) -> None:
        self._state = QMediaPlayer.PausedState
        self._pause.emit()

    def stop(self) -> None:
        self._state = QMediaPlayer.StoppedState
        self._stop.emit()

    def setPosition(self, position_ms) -> None:
        self._position_ms = float(position_ms)
        self._seek.emit(float(position_ms))

    def setPlaybackRate(self, rate) -> None:
        self._playback_rate = float(rate)
        self._rate.emit(float(rate))

    def setVolume(self, volume) -> None:
        self._volume.emit(float(volume))

    def shutdown(self) -> None:
        """Stop the audio thread. Called from the window's closeEvent: an engine
        still pumping into a sink while Qt tears the process down is a teardown
        crash waiting to happen.
        """
        if not self._thread.isRunning():
            return
        # Blocking, not queued: `quit()` stops the event loop, so anything
        # merely posted to it would never be delivered and the device and file
        # would stay open for the life of the process.
        QMetaObject.invokeMethod(self._engine, "release", Qt.BlockingQueuedConnection)
        self._thread.quit()
        self._thread.wait(2000)
