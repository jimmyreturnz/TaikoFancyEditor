"""Score the time-stretch, so tuning it is not done by ear and argument.

    python tools/measure_stretch_quality.py <audio file> [rate]

Two numbers, both reference-free so they work on real music:

- **splice ratio** -- the largest sample-to-sample jump in the output over the
  largest one in the source. A time-stretch can only ever rearrange existing
  audio, so it cannot legitimately produce a steeper edge than the source
  contains: anything above 1.0 is a discontinuity the splice invented, which is
  what a click is. This is the number that tracks "harsh".
- **warble** -- on a constant-amplitude sine, how much the output envelope
  moves. Splices that land out of phase cancel, and the dip is heard as
  warbling rather than as a click, so a click detector alone misses it. Ideal
  is 0.

Plus the realtime factor, because every quality lever here costs CPU and the
budget is one core minus whatever the editor needs to paint.
"""
from __future__ import annotations

import array
import math
import sys
import time

sys.path.insert(0, __file__.rsplit("tools", 1)[0])

from PySide6.QtCore import QUrl, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat  # noqa: E402

import audio_engine  # noqa: E402
from audio_engine import CHANNELS, SAMPLE_RATE, TimeStretcher, downmix_to_mono  # noqa: E402


def decode(path: str, app: QApplication) -> array.array:
    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(CHANNELS)
    fmt.setSampleFormat(QAudioFormat.Int16)
    decoder = QAudioDecoder()
    decoder.setAudioFormat(fmt)
    chunks: list[bytes] = []
    decoder.bufferReady.connect(lambda: chunks.append(bytes(decoder.read().constData())))
    decoder.finished.connect(app.quit)
    decoder.setSource(QUrl.fromLocalFile(path))
    decoder.start()
    QTimer.singleShot(60000, app.quit)
    app.exec()
    pcm = array.array("h")
    pcm.frombytes(b"".join(chunks))
    return pcm


def max_step(frames: array.array) -> int:
    """Largest absolute first difference on the left channel."""
    left = frames[::CHANNELS]
    return max((abs(b - a) for a, b in zip(left, left[1:])), default=0)


def warble(frames: array.array, window: int = 256) -> float:
    """Spread of the output envelope, as a fraction of its mean."""
    left = frames[::CHANNELS]
    peaks = [max(map(abs, left[i:i + window]))
             for i in range(0, len(left) - window, window)]
    peaks = [p for p in peaks if p > 0]
    if len(peaks) < 4:
        return 0.0
    mean = sum(peaks) / len(peaks)
    variance = sum((p - mean) ** 2 for p in peaks) / len(peaks)
    return math.sqrt(variance) / mean


# Deliberately inharmonic. A single sine is the case WSOLA is best at -- the
# splice search can always find a whole period to land on -- so it scores well
# no matter how the stretch is tuned. Three partials that share no common
# period cannot all be aligned by one offset, and the ones that miss beat
# against each other. That beating is the artifact being hunted.
CHORD_HZ = (220.0, 311.1, 466.2)


def tone(seconds: float, partials=CHORD_HZ, amplitude: int = 9000) -> array.array:
    out = array.array("h")
    scale = amplitude / len(partials)
    for i in range(int(SAMPLE_RATE * seconds)):
        value = int(sum(
            scale * math.sin(2.0 * math.pi * hz * i / SAMPLE_RATE) for hz in partials))
        out.append(value)
        out.append(value)
    return out


def sine(seconds: float, hz: float = 440.0, amplitude: int = 12000) -> array.array:
    out = array.array("h")
    for i in range(int(SAMPLE_RATE * seconds)):
        value = int(amplitude * math.sin(2.0 * math.pi * hz * i / SAMPLE_RATE))
        out.append(value)
        out.append(value)
    return out


def tonality(frames: array.array, hz: float = 440.0) -> float:
    """Share of the output's energy still sitting at the input frequency.

    Goertzel rather than a full transform: only one bin is wanted, and this is
    a dozen lines with no dependency. A clean stretch of a pure tone scores
    1.0; splices smear energy into sidebands and drag it down.
    """
    left = frames[::CHANNELS]
    count = len(left)
    if count < 1000:
        return 0.0
    coefficient = 2.0 * math.cos(2.0 * math.pi * hz / SAMPLE_RATE)
    first = second = 0.0
    for sample in left:
        first, second = sample + coefficient * first - second, first
    power = first * first + second * second - coefficient * first * second
    energy = sum(float(sample) * sample for sample in left)
    if energy <= 0:
        return 0.0
    return power / (energy * count / 2.0)


def run(source: array.array, rate: float, seconds: float):
    mono = downmix_to_mono(source)
    stretcher = TimeStretcher()
    stretcher.reset(0.0)
    wanted = int(SAMPLE_RATE * seconds)
    collected = bytearray()
    started = time.perf_counter()
    while len(collected) // (2 * CHANNELS) < wanted:
        block = stretcher.pull(source, mono, 8192, rate)
        if not block:
            break
        collected += block
    elapsed = time.perf_counter() - started
    out = array.array("h")
    out.frombytes(bytes(collected))
    produced = len(out) // CHANNELS / SAMPLE_RATE
    return out, elapsed / produced if produced else 0.0


def main() -> None:
    app = QApplication([])
    path = sys.argv[1]
    rates = [float(sys.argv[2])] if len(sys.argv) > 2 else [0.75, 0.5, 0.25]
    music = decode(path, app)
    # A slice from a minute in: intros are often quiet, and a quiet excerpt
    # flatters every one of these numbers.
    start = min(len(music) // 2, SAMPLE_RATE * CHANNELS * 60)
    music = music[start:start + SAMPLE_RATE * CHANNELS * 12]
    chord = tone(8.0)
    pure = sine(8.0)

    sequence, overlap = audio_engine.SEQUENCE_FRAMES, audio_engine.OVERLAP_FRAMES
    print(f"sequence={sequence} ({sequence / SAMPLE_RATE * 1000:.0f}ms) "
          f"overlap={overlap} ({overlap / SAMPLE_RATE * 1000:.0f}ms) "
          f"search=+-{audio_engine.SEARCH_FRAMES} "
          f"step={audio_engine.SEARCH_STEP}/{audio_engine.CORRELATION_STEP}")
    print(f"{SAMPLE_RATE / sequence:.0f} splices/sec, "
          f"{overlap / sequence * 100:.0f}% of output cross-faded")
    print(f"  source tonality={tonality(pure):.3f} warble={warble(chord):.3f} "
          f"(the ceiling every row below is measured against)")
    print(f"{'rate':>6} {'tonality':>9} {'warble':>8} {'click':>7} {'cpu':>7}")
    for rate in rates:
        out, cost = run(music, rate, 4.0)
        chord_out, _ = run(chord, rate, 3.0)
        pure_out, _ = run(pure, rate, 3.0)
        # On the tone, not the music: a clipped master already contains steps
        # near full scale, so the ratio there is swamped and says nothing.
        click = max_step(pure_out) / max(1, max_step(pure))
        print(f"{rate:>6} {tonality(pure_out):>9.3f} {warble(chord_out):>8.3f} "
              f"{click:>7.2f} {cost:>6.2f}x")


if __name__ == "__main__":
    main()
