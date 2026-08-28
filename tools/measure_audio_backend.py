"""Sample a Qt media backend against a wall clock while it plays a real file.

    python tools/measure_audio_backend.py <backend> <audio file> <rate> [seconds]

`backend` is a QT_MEDIA_BACKEND value ("windows" or "ffmpeg"). Reports the
measured song-time-per-wall-time ratio, the distinct position steps, and how
many times the backend actually reported a new position -- which is what
decides whether the editor clock has anything to interpolate between.

This is the harness behind the measurement table in docs/DEVELOPMENT_PLAN.md.
Use a real map's audio, not a generated WAV: FFmpeg's position granularity
turned out to be codec-dependent, so a synthetic file gave the wrong number.
"""
import os, sys
os.environ["QT_MEDIA_BACKEND"] = sys.argv[1]
from PySide6.QtCore import QUrl, QTimer, QElapsedTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

app = QApplication([])
player = QMediaPlayer(); out = QAudioOutput(); out.setVolume(0.0)
player.setAudioOutput(out)
errors = []
player.errorOccurred.connect(lambda e, m: errors.append((e, m)))
player.setSource(QUrl.fromLocalFile(sys.argv[2]))
player.setPlaybackRate(float(sys.argv[3]))

samples = []
wall = QElapsedTimer(); wall.start()
def sample():
    samples.append((wall.elapsed(), player.position()))
timer = QTimer(); timer.timeout.connect(sample); timer.start(4)
QTimer.singleShot(300, player.play)
seconds = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
QTimer.singleShot(int(300 + seconds * 1000), app.quit)
app.exec()

if errors:
    print(f"{sys.argv[1]:8s} {os.path.basename(sys.argv[2]):16s} ERROR {errors[0]}")
    raise SystemExit
moving = [s for s in samples if s[1] > 0]
if len(moving) < 5:
    print(f"{sys.argv[1]:8s} {os.path.basename(sys.argv[2]):16s} never advanced")
    raise SystemExit
steps = sorted({b[1] - a[1] for a, b in zip(moving, moving[1:]) if b[1] != a[1]})
gaps = [b[0] - a[0] for a, b in zip(moving, moving[1:]) if b[1] != a[1]]
span_song = moving[-1][1] - moving[0][1]
span_wall = moving[-1][0] - moving[0][0]
rate = span_song / span_wall if span_wall else 0
print(f"{sys.argv[1]:8s} {os.path.basename(sys.argv[2]):16s} rate={rate:.4f} "
      f"(asked {sys.argv[3]})  step_ms={steps[:4]}  median_wall_gap={sorted(gaps)[len(gaps)//2]}ms  "
      f"updates={len(gaps)}  over={span_wall}ms")
