"""What the audio pipeline actually does when the playback rate changes mid-play.

    python tools/measure_rate_change.py <backend> <audio file> <from> <to> [seconds]

Built for one specific report: clicking a speed button silences the music until
the user pauses and resumes. "Muted" is not something `position()` can show, so
this taps `QAudioBufferOutput` -- the decoded buffers on their way to the
device -- and prints the RMS level per 100ms slice across the rate change.
Silence after the change with buffers still arriving means the renderer kept
running and stopped producing sound; buffers ceasing means it stalled outright.

Also prints the source-time each slice covers, which is how the two rate models
tell themselves apart: a plain resample delivers `rate` seconds of source per
wall second, a time-stretch delivers one.
"""
import array, os, statistics, sys

os.environ["QT_MEDIA_BACKEND"] = sys.argv[1]
from PySide6.QtCore import QUrl, QTimer, QElapsedTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioOutput, QMediaPlayer

PATH, FROM_RATE, TO_RATE = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
SECONDS = float(sys.argv[5]) if len(sys.argv) > 5 else 6.0

app = QApplication([])
player = QMediaPlayer()
out = QAudioOutput()
out.setVolume(0.5)
player.setAudioOutput(out)
tap = QAudioBufferOutput()
player.setAudioBufferOutput(tap)

errors, events = [], []
player.errorOccurred.connect(lambda e, m: errors.append((e, m)))
player.playbackStateChanged.connect(
    lambda s: events.append((wall.elapsed(), f"state={s.name}")))

wall = QElapsedTimer()
wall.start()
buffers = []  # (wall_ms, start_time_ms, duration_ms, rms, frames)


def received(buf):
    raw = buf.constData()
    fmt = buf.format()
    # Whatever the backend hands us: only the two float/int16 shapes turn up in
    # practice, and an unknown one is worth seeing rather than silently scoring 0.
    code = fmt.sampleFormat()
    if code == fmt.SampleFormat.Float:
        samples, scale = array.array("f"), 1.0
    elif code == fmt.SampleFormat.Int16:
        samples, scale = array.array("h"), 32768.0
    else:
        buffers.append((wall.elapsed(), buf.startTime() / 1000.0,
                        buf.duration() / 1000.0, -1.0, buf.frameCount()))
        return
    samples.frombytes(bytes(raw)[:len(bytes(raw)) // samples.itemsize * samples.itemsize])
    # Peak, not mean: a slice that is mostly quiet but has one real transient in
    # it is not the silence this harness is looking for.
    peak = max((abs(v) for v in samples), default=0.0) / scale
    buffers.append((wall.elapsed(), buf.startTime() / 1000.0,
                    buf.duration() / 1000.0, peak, buf.frameCount()))


tap.audioBufferReceived.connect(received)
player.setSource(QUrl.fromLocalFile(PATH))
player.setPlaybackRate(FROM_RATE)

positions = []
QTimer(app, timeout=lambda: positions.append((wall.elapsed(), player.position())),
       interval=10).start()


def change_rate():
    events.append((wall.elapsed(), f"setPlaybackRate({TO_RATE})"))
    player.setPlaybackRate(TO_RATE)


QTimer.singleShot(300, player.play)
QTimer.singleShot(int(300 + SECONDS * 1000 / 2), change_rate)
QTimer.singleShot(int(300 + SECONDS * 1000), app.quit)
app.exec()

if errors:
    print(f"ERRORS: {errors}")
if not buffers:
    # The tap is FFmpeg-only, so WMF reaches here every time. The position
    # trace below is the only view of that backend this harness gets.
    print("no audio buffers delivered -- the tap saw nothing (expected on WMF)")

change_at = next((t for t, label in events if label.startswith("setPlaybackRate")), 0)
print(f"backend={sys.argv[1]}  {os.path.basename(PATH)}  {FROM_RATE} -> {TO_RATE}")
for t, label in events:
    print(f"  {t - change_at:+7.0f}ms  {label}")

if buffers:
    print(f"buffers={len(buffers)}  {buffers[0][4]} frames/buf")
    print()
    print("  wall(rel)  buffers  peak    source_ms  ratio")
    slices = {}
    for wall_ms, _start, dur_ms, peak, _frames in buffers:
        slices.setdefault(int((wall_ms - change_at) // 200) * 200, []).append((peak, dur_ms))
    for bucket in sorted(slices):
        peaks = [p for p, _ in slices[bucket]]
        delivered = sum(d for _, d in slices[bucket])
        print(f"  {bucket:+7d}   {len(slices[bucket]):5d}  {max(peaks):.4f}  "
              f"{delivered:9.1f}  {delivered / 200:.3f}")
    before = [p for w, _s, _d, p, _f in buffers if w < change_at]
    after = [p for w, _s, _d, p, _f in buffers if w >= change_at]
    print()
    print(f"peak before: {max(before, default=0):.4f} ({len(before)} buffers)")
    print(f"peak after : {max(after, default=0):.4f} ({len(after)} buffers)")

# The position trace is the only view of a backend the tap cannot see, and it
# answers the question that matters on its own: did the backend actually adopt
# the rate it was asked for, or is it still running at the old one while the
# editor's clock has already switched?
print()
moving = [p for p in positions if p[1] > 0]
for name, seq in (("before", [p for p in moving if p[0] < change_at]),
                  ("after ", [p for p in moving if p[0] >= change_at])):
    if len(seq) > 2:
        span = seq[-1][0] - seq[0][0]
        print(f"position rate {name}: {(seq[-1][1] - seq[0][1]) / span:.4f} "
              f"over {span}ms ({len(seq)} samples)")
steps = sorted({b[1] - a[1] for a, b in zip(moving, moving[1:])
                if b[1] != a[1] and b[0] >= change_at})
print(f"position steps after change: {steps[:6]}")
print(f"playbackRate() reads back as: {player.playbackRate()}")

# A rate change that makes the backend restate its position -- a jump, or worse a
# jump back to 0 -- is adopted by the editor as a fresh source reading and drags
# the displayed playhead with it. That is what "the offset deviated" looks like
# from in here, so measure the discontinuity rather than assuming smoothness.
jumps = [(b[0] - change_at, a[1], b[1]) for a, b in zip(positions, positions[1:])
         if abs((b[1] - a[1]) - (b[0] - a[0]) * TO_RATE) > 25 and b[0] >= change_at]
# How long the backend stops producing song time at the switch. A stall here is
# invisible to `position()` afterwards -- the rate looks right again -- but the
# editor's clock free-ran through it at the new rate, so every millisecond of
# stall is a millisecond the playhead is permanently ahead of the music.
window = [(t - change_at, ms) for t, ms in positions if -200 <= t - change_at <= 600]
if window:
    stalls = [(a[0], b[0] - a[0]) for a, b in zip(window, window[1:]) if b[1] == a[1]]
    run, best = 0, (0, 0)
    for t, gap in stalls:
        run = run + gap
        if run > best[1]:
            best = (t, run)
        if gap == 0:
            run = 0
    frozen = sum(gap for _t, gap in stalls)
    print(f"position frozen for {frozen}ms of the 800ms around the change; "
          f"longest single stall {best[1]}ms at {best[0]:+.0f}ms")
    expected = 600 * TO_RATE
    actual = window[-1][1] - next(ms for t, ms in window if t >= 0)
    print(f"song time in the 600ms after: {actual}ms, expected {expected:.0f}ms, "
          f"short by {expected - actual:.0f}ms")
print(f"discontinuities >25ms after the change: {len(jumps)}")
for t, before_ms, after_ms in jumps[:8]:
    print(f"  {t:+6.0f}ms  {before_ms} -> {after_ms}  ({after_ms - before_ms:+d}ms)")

