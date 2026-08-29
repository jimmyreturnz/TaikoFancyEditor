"""Can pure Python time-stretch audio in real time? Measure, don't assume.

    python tools/bench_timestretch.py [rate] [seconds]

Pitch-preserving slow playback needs WSOLA (what BASS_FX and SoundTouch do),
and every Python implementation on PyPI needs numpy, which this project does
not take. Whether that rules it out here is a speed question, and a speed
question gets a harness rather than an estimate.

Reports the realtime factor: below 1.0 the stretch keeps up with playback on
one core, above it does not. The inner loop is written the fast way on
purpose -- `sum(map(mul, ...))` over `array` slices runs the multiply-add in C
-- so this measures the ceiling, not a naive first draft.
"""
import array, math, sys, time
from operator import mul

RATE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
SR, FRAME, HOP_OUT, SEARCH = 44100, 1024, 512, 256

source = array.array("f", (
    math.sin(i * 0.05) * 0.6 + math.sin(i * 0.31) * 0.3
    for i in range(int(SR * SECONDS / RATE) + FRAME + SEARCH * 2)
))
window = array.array("f", (0.5 - 0.5 * math.cos(2 * math.pi * i / FRAME) for i in range(FRAME)))
out = array.array("f", bytes(4 * int(SR * SECONDS)))

hop_in = HOP_OUT * RATE
started = time.perf_counter()
read, write, grains, offsets = SEARCH, 0, 0, []
while write + FRAME < len(out):
    # WSOLA: slide the read head within +/-SEARCH to find the offset whose
    # overlap best matches what was just written, then overlap-add there. The
    # search is the whole cost, which is why it is the thing being measured.
    target = out[write:write + SEARCH]
    best, best_score = 0, -1e30
    for lag in range(-SEARCH, SEARCH, 4):  # coarse: every 4th lag
        start = read + lag
        score = sum(map(mul, target, source[start:start + SEARCH]))
        if score > best_score:
            best_score, best = score, lag
    start = read + best
    grain = source[start:start + FRAME]
    for i in range(FRAME):
        out[write + i] += grain[i] * window[i]
    offsets.append(best)
    read = int(read + hop_in)
    write += HOP_OUT
    grains += 1

elapsed = time.perf_counter() - started
print(f"rate={RATE}  produced {SECONDS:.1f}s of audio (mono) in {elapsed:.2f}s")
print(f"grains={grains}  realtime factor={elapsed / SECONDS:.2f}x  "
      f"(stereo would be ~{elapsed / SECONDS * 2:.2f}x)")
# Guards the number above: a search that silently degenerated, or an output
# left mostly silent, would look fast for the wrong reason.
print(f"peak={max(map(abs, out)):.3f}  distinct splice offsets chosen={len(set(offsets))}"
      f"  of {128} searched")
print("verdict:", "keeps up" if elapsed / SECONDS * 2 < 1.0 else "too slow on one core")
