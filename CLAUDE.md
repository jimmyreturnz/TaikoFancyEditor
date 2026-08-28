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

### The harnesses

- `tools/profile_playback.py <map> [frames] [--gimmick] [--profile]` — per-frame
  render cost against the 8.33ms budget. Reports the distribution, because
  stutter is the tail, not the mean.
- `tools/measure_audio_backend.py <backend> <audio> <rate> [seconds]` — position
  reporting granularity and rate accuracy. Use a real map's audio; FFmpeg's
  granularity turned out to be codec-dependent, so a generated WAV lied.

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

## Things that are true and non-obvious

- **`gui.py` is LF; most other files are CRLF, and `core.autocrlf=true`.** A
  scripted patch must detect the file's own newline, not assume one. Several
  edits have silently failed their own assertion because of this — and one
  went out inside a commit whose message described the change it had not made.
  If a patch script asserts, fix it before committing.
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
