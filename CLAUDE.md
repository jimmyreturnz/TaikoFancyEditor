# Taiko Fancy Arranger — working notes

A PySide6 editor for osu!taiko charts. `gui.py` is the monolith (~12k lines);
`osu_io/` parses and writes `.osu`, `model/` holds the undo commands,
`gimmick_session.py` builds gimmick structures, `time_axis.py` is the shared
scrolling/snapping mixin.

## Measure first. Every time.

This is the rule the project keeps re-learning, so it goes first.

Four separate investigations here reached a confident, reasonable, **wrong**
diagnosis before anyone measured:

| The confident answer | What measurement said |
| --- | --- |
| Pure-Python time-stretch is infeasible without numpy | 0.11x realtime mono, 0.22x stereo. `sum(map(mul, ...))` over `array` slices does the multiply-add in C. |
| The test suite leaks widgets | Same object count either way. It was `QApplication.installEventFilter` never being released — event fan-out, not memory. 4 hours → 106s. |
| Slow playback needs better clock interpolation | The FFmpeg backend reports position in coarse steps. The clock was correct and starved of input. |
| WMF's rate runs 1.8% fast, calibrate it out | A fixed ~12ms offset in the harness, divided by a short window. Real error 0.04%. |
| Stutter is the big timeline views | A 28-pixel-tall overview bar cost more than the full timeline above it. |

So: **do not optimise, diagnose, or "fix" a performance or timing problem
until a number says which thing to touch.** Write the harness, keep it in
`tools/`, and put the numbers in the commit message. Three of the four rows
above cost real work spent on the wrong thing.

Corollary: when a measurement surprises you, check the harness before you
believe it. The 1.8% row above was a harness artifact, and the check was
simply running it over a longer window.

### Audio: we own the transport

`audio_engine.TrackPlayer` replaced `QMediaPlayer` for song playback on
2026-08-29. Read that module's docstring before touching anything audio; the
short version is that the three things making osu!'s editor accurate all live
below the level Qt's player exposes:

- the clock is a **sample cursor** (`QAudioSink.processedUSecs()`, measured to
  be the play cursor and not the write cursor), not a signal in whole ms;
- slowing down **preserves pitch** (WSOLA in `TimeStretcher`), because osu!'s
  editor uses `AdjustableProperty.Tempo` rather than `Frequency`;
- a rate change is a **live ratio the next grain reads**, so it cannot stall.
  `QMediaPlayer.setPlaybackRate` lost 114ms of song time on a 0.25x -> 1.0x
  switch, which is what "the offset moves when I click a speed button" was.

Consequences worth remembering: there is no media backend to select any more
(`QT_MEDIA_BACKEND`, the Ogg fallback and the decoder setting are all gone, and
Qt's WMF backend is deprecated as of 6.10 anyway), the whole track is decoded
into memory (~50MB for 4:43), and the engine owns a thread that `closeEvent`
must shut down.

When porting from `ppy/osu`, port the *current* revision. This project's
`InterpolatingFramedClock` came from an older one and was missing both the
`AllowableErrorMilliseconds * Rate` scaling and the `DampContinuously` drift
recovery -- two real bugs inherited from reading a stale copy.

### The harnesses

- `tools/profile_playback.py <map> [frames] [--gimmick] [--profile]` — per-frame
  render cost against the 8.33ms budget. Reports the distribution, because
  stutter is the tail, not the mean.
- `tools/measure_audio_backend.py <backend> <audio> <rate> [seconds]` — position
  reporting granularity and rate accuracy. Use a real map's audio; FFmpeg's
  granularity turned out to be codec-dependent, so a generated WAV lied.
- `tools/measure_rate_change.py <backend> <audio> <from> <to> [seconds]` — what
  a mid-playback rate change does. Taps `QAudioBufferOutput` so "the audio went
  silent" is a number rather than a report, and reports the song time *lost* at
  the switch, which is the thing `position()` hides by looking correct again
  afterwards.
- `tools/profile_playback.py ... --gameplay --skin NAME` — the gameplay
  preview is the only view that draws the playfield, and a run without one open
  measures none of it. The full skinned playfield costs 0.11ms a frame over the
  built-in lane (1.80ms -> 1.91ms at 1882x170), which is the stretch and
  silhouette caches doing their job.
- `tools/bench_timestretch.py [rate] [seconds]` — whether the WSOLA inner loop
  keeps up. It prints the distinct splice offsets actually chosen, because a
  search that quietly degenerates looks fast for the wrong reason.

Two traps both harnesses were caught by, worth repeating:

- **Size the window realistically.** The offscreen platform's default is
  758×180. Paint cost scales with pixels, so measuring there understated
  everything by the ratio of the areas. `window.resize(1920, 1080)`.
- **Drive the path the app actually drives.** `repaint()` per widget charges
  each one its own compositing pass; the app calls `update()` and gets one
  coalesced pass. The difference was 2x.

## Performance shapes that keep recurring

Found by measurement, listed so the next one is recognised faster:

- **Per-frame work that redoes static work.** `TimingOverviewBar` repainted
  ~1150 marker lines every frame for content that never moves. The fix is a
  `QPixmap` static layer plus the few things that do move — `DensityOverview`
  has the reference implementation.
- **`setPen` per primitive.** Grouping draws by colour turns N pen changes
  into one per colour.
- **Binary search inside a forward-only walk.** `_draw_snap_grid` and
  `_draw_sv_curve` both searched the whole timing list per iteration while
  their cursor only moved forward. Hold the section until the walk leaves it.
- **`active_uninherited_at` is O(inherited points skipped).** It steps back one
  point at a time. On a gimmick difficulty that is thousands of steps per
  lookup. Feed it a pre-filtered list (`TimeAxisMixin.snap_beat_points`) rather
  than filtering per call — filtering per call is the cost the backward walk
  exists to avoid.
- **Application-wide event filters.** Qt runs every event in the process
  through every installed filter. Bail out on `event.type()` in one lookup,
  and release the filter in `closeEvent` (`_release_application_hooks`).

### The gameplay preview's numbers come from the ruleset

`GameplayViewerView` exists to answer "what will this look like in game", so
its constants are `ppy/osu`'s and not chosen by eye. Read them from
`osu.Game.Rulesets.Taiko` rather than re-deriving:

- `TaikoHitObject.DEFAULT_SIZE = 0.475f` — note diameter as a fraction of the
  playfield height (`TaikoPlayfield.BASE_HEIGHT = 200`). This was
  `min(30.0, height * 0.19)`, which also stopped growing once the view was
  tall enough.
- `TaikoStrongableHitObject.STRONG_SCALE = 1 / 0.65f` — a finisher is 1.538x a
  normal note, not 1.4x.
- Scroll distance is `100 * SliderMultiplier * VELOCITY_MULTIPLIER(1.4) *
  ScrollSpeed / beatLength` (`DrumRoll.cs`, `TaikoBeatmapConverter.cs`). The
  map's SliderMultiplier was missing here entirely, so two maps that scroll
  three times apart previewed identically.
- `CirclePiece` flashes kiai at `kiai_flash_opacity = 0.15f` with
  `BlendingParameters.Additive`. Additive is the half that matters: as plain
  alpha it lays a film over the note instead of lighting it, and every note in
  a kiai section drifted toward the same pale orange. The flash is also a
  **child of the circle piece**, so it inherits its masking — drawn as a plain
  ellipse it lit the whole bounding box including the transparent rim, which
  reads as a glow radiating from behind the note rather than the note pulsing.
  `skin.silhouette` is that mask: the artwork's own alpha, filled flat with the
  flash colour. It pulses once per **1/1 beat**, phased from the start of the
  kiai section rather than from whatever timing point governs the playhead.

### The playfield is the skin's, and 200 is the unit

`GameplayViewerView` draws the wiki's playfield list — the bar, its kiai glow,
the scrolling background, the barline, the hit target, `lighting`. **Not the
input drum** (`taiko-bar-left`, `taiko-drum-inner`, `taiko-drum-outer`): the
wiki files it under the playfield, but it is where the player hits rather than
where the notes are, and nobody is hitting a preview. `TaikoPlayfield
.BASE_HEIGHT` is 200 and **every** playfield element in the wiki is sized
against that same 200 (bar 1024x200, barline 4x175, background 776x162), so the
view's height *is* the 200 and each constant is the wiki's number over it. That is also what keeps `TAIKO_NOTE_SIZE` and the panels
from disagreeing about scale.

Three things that cost real time here:

- **Qt stamps `devicePixelRatio = 2.0` on any `@2x` file**, and every derived
  pixmap inherits it, so `drawPixmap(point, ...)` draws it at half size. A skin
  that ships some elements at `@2x` and some not (riun's `approachcircle`)
  therefore drew half its playfield at half scale. `TaikoSkin` normalises the
  ratio to 1 on load: the caller decides every size from the units above, so
  the ratio is not information. Note that `drawPixmap(QRectF, ...)` is immune,
  which is why the notes always looked right and the panels did not.
- **`lighting` goes over the bar, not behind it.** The wiki says behind; in
  practice most skins' `taiko-bar-right` is fully opaque (riun's is a flat
  `#0a0a0a`), so behind it the light never reaches the eye.
- **The built-in white wash is a stand-in, not an addition.** It is skipped
  when the skin supplies `lighting` or `taiko-bar-right-glow`, or a skinned
  lane is washed twice.

Deliberately not loaded, each because it depicts something this preview is not
doing: the **hit explosions** (`taiko-hit300*`) and `taiko-slider-fail` — a
judgement happens to a player, and nobody is playing, so a 300 burst would be
inventing an autoplay run; `taiko-glow` and `lighting` — a bloom around a
target where nothing is judged; the **input drum** (`taiko-bar-left`,
`taiko-drum-inner`, `taiko-drum-outer`) — where the player hits, not where the
notes are; `sliderscorepoint` — drumroll ticks need `SliderTickRate`, which the
parser does not read; and `pippidon*` — a mascot, in 6 of 39 installed skins,
and the only element needing BPM-synced frames.

### A drumroll is three pieces, and the wiki gives their origins

`taikohitcircle` (the head, in the drumroll colour) + `taiko-roll-middle` +
`taiko-roll-end`, in the gameplay preview **and** the editor timeline — one
object, so one drawing of it.

- `taiko-roll-middle` is **1px wide, origin TopLeft**: it is the track, meant
  to be *stretched* across the body rather than tiled.
- `taiko-roll-end` is **64x128, origin TopLeft**. TopLeft is the part that is
  easy to get wrong: the cap butts onto the far end of the track and reaches
  *past* it by its own width, mirroring the head reaching back past the start
  by its radius. Centred on the end, a roll comes up half a radius short.
- Both tint **multiplicatively** from the drumroll colour, like the head. They
  look like plain art and are not; see `skin.tinted`.

**A negative length collapses the track, not the cap.** `max(end_x, x)` clamps
the body to nothing, so the cap butts onto the head and sits to its *right* —
which is where osu! draws it, and is what a fake slider looks like in game.
Skipping the cap there leaves a bare head, which is not the object.

The note colours are `DON_COLOR` `#e54c2e`, `KAT_COLOR` `#438dab` and
`DRUMROLL_COLOR` `#fbb706`, one constant each because the two views had
drifted to different values for the same objects.

**A circle disappears when it reaches the hit position** (`_has_been_hit`),
because that is what being hit looks like; a drumroll travels straight through
(its body is still being hit as it crosses) and a spinner stays in place.

### The Kiai and Sound Volume layer has no horizontal axis to edit

Its lines belong to the milliseconds that put them there — a barline, a note, a
kiai edge — so dragging one sideways moves a section boundary out from under
whatever generated it. `_drag_axis_for_click` returns `"value"` there
unconditionally, dot or no dot, red line or green; double click types a volume
rather than opening `TimingLineDialog`, which carries a Time field.

The number it writes is `TimingPoint.volume`, which is osu!'s **hitsound**
volume for the section — so it has to reach the samples or the layer is a graph
of nothing. `hitsound_schedule` resolves it per note in one forward merge over
the sorted points (`active_point_at` walks backwards a point at a time, which on
a gimmick difficulty is thousands of steps a lookup), and `advance` applies it
per sample. 0 is silent and is obeyed: mappers set it deliberately.

**A fake slider is a drumroll with a negative `length`** — canonically
`256,192,54692,2,12,L|624:192,643,-0.0001`. The derived duration is negative,
so the object ends before it starts, no tick is generated and nothing is
hittable, while osu! still draws the head. The preview draws that backwards
extent **to scale**, which means the canonical one shows nothing (0.09px) and
a `-500` one reaches 714px back over its neighbours. Do not pad it to
visibility; the invisibility is the trick working.

## Things that are true and non-obvious

- **Line endings are mixed, and `core.autocrlf=true`.** `gui.py` used to be
  LF and is now uniformly CRLF; `offset_calibration.py` and
  `tests/test_offset_calibration.py` are LF. A scripted patch must **detect**
  the file's own newline rather than assume either — read bytes, normalise to
  `\n`, patch, write back in the newline the file arrived with. Several edits
  have silently failed their own assertion because of this — and one went out
  inside a commit whose message described the change it had not made. If a
  patch script asserts, fix it before committing.
- **Every `tr()` literal needs a catalog entry** in `translations/taiko_ja.ts`
  with a non-empty translation, or `tests/test_i18n.py` fails. After editing
  the `.ts`, run `tools/compile_translations.bat`. The `.qm` is generated and
  not tracked.
- **`taiko_arranger/taiko_arranger/` is a stale untracked copy.** It will
  pollute any repo-wide search. So will `patches_backup/`.
- **Tests: run per file in parallel**, not `unittest discover`. Discover in one
  process still runs for tens of minutes; per file finishes in about 70s:

  ```
  ls tests/test_*.py | sed 's#/#.#;s#\.py$##' \
    | QT_QPA_PLATFORM=offscreen xargs -P 6 -I{} .venv/Scripts/python.exe -m unittest {}
  ```

- **A test that passes can still prove nothing.** `QObject.receivers()` reports
  0 for Python-side connections, and replacing an instance attribute does not
  redirect an already-stored bound-method connection, so a spy set that way
  never fires either way. `tests/test_window_lifecycle.py` documents both.

## Conventions

- Comments explain **why**, and name the failure the code prevents. Match the
  density of the surrounding code.
- New timing points inherit the state in force at their time —
  `gimmick_session.carry_active_state` does kiai and volume for anything
  generated. Paste is the exception: it carries the clipboard's values.
- Non-trivial logic leaves one runnable check behind. Prefer a real regression
  test over a comment claiming it works.
- No new dependencies.
