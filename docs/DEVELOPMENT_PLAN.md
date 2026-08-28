# Taiko Fancy Arranger — Development Plan

Turning the visual arranger into a full osu!taiko editor.

> **Status:** M0 complete. M1 complete, including the `gui.py` integration.
> M2 complete: shell + chart viewing (v1) and Editor page layout v2 (shared
> deck split, global header, density as a view type, page-scoped global
> snap) both implemented 2026-08-08. M3 (note editing) implemented
> 2026-08-08, then revised the same day after owner feedback: tool row is
> global (not per-view), click-to-seek suppressed on Editor-page views,
> slider/spinner get distinct rendering, playback timing corrected further,
> paint performance improved, and Fancy Arranger regained its own timing
> bar + density chart with a wider control panel. M4 (SV editor: view/edit/
> delete SV points, function-mode eased sweeps) implemented 2026-08-08,
> reusing the same global-tool-row pattern. Second feedback round the same
> day: global keyboard shortcuts for tool digits, numbered button labels,
> repositioned SV value labels, Fancy Arranger row merge, hover ghost
> previews, click-drag slider/spinner placement with later resize and
> Shift-for-big, and a real timing-bar stutter bug fixed at the root. M5 not
> started. **Third feedback round, 2026-08-08** — the one that took M4
> from "a view that can add points" to an editor: keyboard delete, undo/redo
> that actually refreshes the views, drag in green-line mode, a snapped ghost
> preview, per-timestamp line colours, one-object-per-millisecond, copy and
> paste, note-position SV generation, SV views on the playback clock,
> difficulty-level zoom, and two layout corrections (status line, difficulty
> name). **Fourth feedback round, 2026-08-09** — making the SV editor tell the
> truth: green-over-red effective SV, an autoscaling curve graph, grid-snapped
> paste, rubber-band selection, kiai-preserving generation, spinner extents,
> Slider/Spinner naming, a restart prompt in the newly chosen language, and
> function endpoints that land exactly.
> **Fifth feedback round, 2026-08-09** — one-snap wheel scrolling, spinner
> extents that survive scrolling off-screen, Shift+click big don/kat, green and
> red lines draggable horizontally with the axis decided by click proximity to
> the value dot, a fixed 0.1x axis floor with only the ceiling autoscaling, and
> step connectors across gaps of two beats or more. **M6 (gameplay viewer)
> implemented 2026-08-09**, out of the planned order at the owner's request:
> each object scrolls at the SV in force at its own time, with barlines.
> **Sixth round, same day** — gimmick maps (thousands of timing points, some at
> absurd BPM) no longer freeze the editor, and the SV graph's axis stopped
> flickering.
> **Startup flow, 2026-08-10** — the app now opens on a **song library** rather
> than an empty editor: language on first start, then the osu! Songs folder,
> then a cached, non-blocking scan for `Mode: 1` charts, then song → difficulty
> → editor, with Esc back to the library and an unsaved-changes prompt on the
> way out.
> **Current release:** v3.1.0

---

## Next: the audio backend (the real cause of inaccurate slow playback)

Decided 2026-08-27, deferred out of v3.1.0 so the rest of it could ship.

**Measured, not guessed.** `QMediaPlayer`'s two Windows backends were sampled
against a wall clock while playing a generated 120s WAV, 12s per rate after a
2s warm-up, on PySide6 6.11.1:

| backend | rate error | position granularity | fresh reading @0.25x | worst extrapolation error |
| --- | --- | --- | --- | --- |
| `ffmpeg` (current default) | 0.10% | 93 ms steps | every 351 ms of wall | 8-44 ms |
| `windows` (WMF) | 0.00% | 1 ms | every 4 ms | ~1 ms |

The FFmpeg backend advances `position()` in fixed ~93 ms *song-time* chunks.
At 0.25x that is one true reading every third of a second of real time, with
the app extrapolating blind in between. **No interpolation scheme can fix a
source that coarse** -- which is why the osu!-style `InterpolatingFramedClock`
port in v3.1.0, correct as it is, did not make 25/50/75% feel right. WMF is
exact at every rate including 1.0x.

**The blocker:** WMF cannot decode Ogg -- `InvalidMedia` / `FormatError` on a
real `.ogg` from the Songs folder, where FFmpeg reports `BufferedMedia`. A
large share of osu! maps ship `.ogg`, and `QT_MEDIA_BACKEND` is process-wide
and read before `QApplication` exists, so the backend cannot be chosen per
file.

**Chosen design: auto-fallback on first failure.** Default to WMF; when a
source fails to load with `FormatError`, record that and fall back to FFmpeg.
Points to settle when this is built:

1. The fallback needs a process restart to take effect, so it has to be
   persisted (QSettings) and applied at the next launch -- decide whether to
   relaunch automatically or tell the user and let them.
2. The first Ogg map of a fresh install still fails once. Consider sniffing
   the audio extension of the map being opened and persisting the choice
   before the media is ever handed to Qt.
3. Keep a manual override in Settings regardless, so a wrong auto-decision is
   recoverable without hunting for the state file.
4. Re-run the measurement harness after the switch to confirm the numbers
   hold against real map audio rather than a generated WAV.

A later alternative, if Ogg accuracy turns out to matter: decode Ogg to PCM
ourselves and hand WMF the samples, which would make every map accurate at the
cost of a decode step on open.

## ~~The test suite is slow~~ — FIXED 2026-08-28

**Resolved in 692f3cb.** The diagnosis below was half right: the cost was
real and quadratic, but it was not a *widget* leak. `MainWindow.__init__`
registers an event filter and a `focusChanged` slot on `QApplication`, which
outlives every window and is never unhooked by `close()`. Qt runs every
application event through every installed filter, so N live windows put N
Python calls on every event. Releasing both in `closeEvent` made it flat
(171→976ms became 164→154ms over twelve windows) **with the same objects
still alive**. The eight QFrames per window are QComboBox popup containers
owned by the window, not leaked widgets.

Whole suite, 967 tests: 4+ hours → **106 seconds** (per file, 6 at a time).
`test_gimmick_editor` went ~650s → 65s, so the "run it per class" advice is
no longer needed. Still worth doing, in order: point `tools/run_tests.bat`
and `build_windows.bat` step `[6/9]` at a per-file runner, and move the
assertions that need no window off `MainWindow` entirely.

Original investigation, kept for the method:

Measured 2026-08-27. `unittest discover` in one process reached only 258 of
963 tests in 80 minutes and was still slowing down; extrapolated past 4 hours.
That is not the test count. Building twelve `MainWindow`s in a row, keeping no
Python reference and calling `gc.collect()` between each:

| window | build time |
| --- | --- |
| #1 | 184 ms |
| #12 | 1228 ms (6.7x) |

and **108 top-level widgets were still alive afterwards** — roughly nine leaked
per window. `close()` does not destroy them, and adding `deleteLater()` plus
`processEvents()` made it worse (216 alive). Qt walks every live top-level
widget, so each successive window costs more and the whole run degrades
quadratically.

The split is visible per file:

```
tests.test_editor_state        23 tests in   0.048s   pure logic
tests.test_editing_ux_round3   57 tests in 111.618s   MainWindow per test
```

~2 ms/test against ~2 s/test. `gui.MainWindow()` is constructed in `setUp` in
all 16 places it appears in the suite — never `setUpClass`.

**Workaround in use:** run one process per test file (and in parallel), so the
leak resets between files. `tools/run_tests.bat` and `build_windows.bat` step
`[6/9]` both still use single-process `unittest discover`, which is the shape
that burned ~5 hours in CI before the workflow's test step was removed — they
should move to the per-file runner.

**Real fix, in order of payoff:**

1. Find what keeps the widgets alive (a parentless child, a signal connection
   holding a reference, a module-level cache). Nine per window is a specific
   number and should be identifiable by diffing `app.topLevelWidgets()`.
2. Share one `MainWindow` per test class via `setUpClass` wherever the tests
   do not mutate global state.
3. Move assertions about pure functions off `MainWindow` entirely — the
   0.048s files show what the suite could look like.

## Refactor backlog

Opened 2026-08-27 while shipping v3.1.0. None of these is a bug; all of them
are things the v3.1.0 work kept tripping over. Ordered by how much pain each
one causes per week, not by size.

1. **Split `gui.py`.** It is ~11,600 lines in one file, and five parallel
   workstreams on it in one day had to be run in separate git worktrees purely
   to avoid lost updates. The natural seams are already visible: the view
   widgets (`TimelineGameplay`, `SVEditorView`, `GameplayViewerView`,
   `TimingOverviewBar`, `DensityOverview`), the dozen-plus `QDialog`
   subclasses, and the gimmick page's `MainWindow` methods. Do the dialogs
   first — they are the cleanest cut and touch the least shared state.

2. **`MainWindow` is a god object** at roughly 6,000 lines. Extracting the
   gimmick page into its own controller is the biggest single win and falls
   out of item 1.

3. **Rename `gui_draft.py`.** It is not a draft: `gui.py` imports `PARAMETERS`
   from it and the i18n gate reads it. It is the Fancy Arranger's parameter
   registry and should be named for that. Three references to update.

4. **`translations/taiko_ja.qm` is a tracked build artifact** and conflicts on
   every branch that adds a string — it conflicted twice in one day. It cannot
   simply be untracked, because running from source would then ship no
   Japanese at all. Either commit to generating it in `run_from_source.bat`,
   or add a merge driver that regenerates it from the `.ts`.

5. **Delete `transformer.py`'s `_timing_point_values`** (the `len(fields) < 7`
   / `fields[6]` string parser). It is the last surviving ad-hoc timing reader
   and is already dead — `gui.py` hands it real `TimingPoint` objects. Carried
   over from the M5 notes, still true.

6. **`patches_backup/`** holds ~60 one-shot patch scripts from before the
   project used branches. Untracked, so it never reached GitHub, but it
   pollutes every local code search. Delete it.

7. ~~**Entry-point inconsistency.**~~ **Fixed.** The premise was wrong:
   `run_from_source.bat` runs `gui.py`, same as the spec. `main.py` was a
   pre-GUI CLI that prompted for a song folder and wrote a renamed copy --
   nothing imported it and no build step packaged it. Both READMEs told people
   to launch the app with `python main.py`, which ran that CLI instead of the
   editor. Deleted; the READMEs now say `python gui.py`.

8. **Flaky test teardown.** `NotADirectoryError` on the fixture's
   `audio.mp3` surfaced once in three consecutive runs: Qt's media backend still
   holds the file when `TemporaryDirectory` cleans up. It is a teardown race,
   not a product bug, but it makes a red run ambiguous. Close the player
   explicitly in `tearDown`.

9. **Test suite runtime.** The full suite is slow enough that it was removed
   from the release workflow in v3.1.0 (see `tools/run_tests.bat`).
   `tests/test_gimmick_editor.py` alone runs for roughly 19 minutes. Most of
   the cost is constructing a real `MainWindow` per test; a shared
   class-scoped window would cut it hard. Until this lands, CI cannot be
   given the gate back.

10. **`HitsoundPlayer.offset_ms` is calibrated in song time** but the output
    latency it compensates for is wall-clock, so a value tuned at 1.0x is
    wrong at 0.25x. Not changed in v3.1.0 because it would invalidate every
    existing user's calibration on an unmeasured guess — it needs a real
    audio device to settle.


## Progress

| Item | State |
|---|---|
| M0 crashes (`group_pages`, `status_label`, duplicate status, `selection_finalized`) | done |
| M0 Image-to-Drawing wired into `DrawingDialog` | done |
| M0 `security_utils.py` + `tests/test_security.py` tracked | done |
| M0 README transformation list corrected | done |
| M1 regression fixture net (`tests/osu_fixtures.py`) | done |
| M1 `osu_io/timing.py`, five parsers consolidated | done |
| M1 `fields[5]` truncation (R2), `note_kind` (R3) | done |
| M1 writer section regeneration, validation (R4, R7, R8) | done |
| M1 `DifficultyState` / `History` / commands wired into `gui.py` | done |
| M2 page switcher (Editor first) + Editor page shell | done |
| M2 multi-difficulty chart-view stacking, per-view chrome | done |
| M2 snap set extended to 21 divisors; symmetric (top+bottom) grid mode | done |
| M2 `eventFilter` / gameplay-frame clock made page/view-aware | done |
| M2 layout v2: shared deck split (`self.timeline` Fancy-Arranger-only) | done |
| M2 layout v2: global header (Undo/Redo/Save/Export new difficulty) | done |
| M2 layout v2: density as a 5th view type, bottommost-in-group | done |
| M2 layout v2: Editor-page-global snap sync (own combo + Alt+wheel) | done |
| M2 layout v2: symmetric-grid / orphaned-group / button-styling bug fixes | done |
| Activating a difficulty auto-opens a default chart + SV view for it | done |
| Playback position: nsec-precision extrapolation, threshold resync | done |
| Gameplay-frame scheduler: fixed cadence instead of restart-every-tick | done |
| M3 note placement/deletion (`note_place_requested`/`note_delete_requested`) | done |
| M3 tool row (1 select, 2 don, 3 kat, 4 slider, 5 spinner, 6 new combo) | done |
| M3 multi-view refresh on edit (`TimelineGameplay.refresh_notes`) | done |
| Tool row moved from per-view to global, driven by focused chart view | done |
| Click-to-seek suppressed on Editor-page views (Fancy Arranger unaffected) | done |
| Slider = yellow brush, spinner = skin image, snap-tick pens cached | done |
| Playback timing: gradual correction replaces threshold-ignore | done |
| Fancy Arranger: own timing bar + density chart, wider control panel | done |
| M4 `SVEditorView`: red/green vertical lines, SV step-graph, time axis | done |
| M4 SV point add/edit(drag-merge)/delete via signals + real mouse events | done |
| M4 function mode: `SVFunctionDialog`, 7 easing curves, preview square | done |
| M4 global SV tool row, visibility swaps with the note tool row on focus | done |
| Global 1-6 keyboard shortcuts for tool rows, guarded against text entry | done |
| Tool buttons show "N. Name" text instead of a bare number + tooltip | done |
| SV editor: per-point value label moved to the graph line, "x.xxx" only | done |
| Fancy Arranger: kiai bar merged into the time row, percentage added | done |
| Hover ghost preview (translucent don/kat/slider/spinner) while placing | done |
| Spinners always render at "big note" size; sliders show their extent | done |
| Slider/spinner placement is click-drag; existing ones resize afterward | done |
| Shift held at release places/leaves a "big" (finisher) slider | done |
| Fixed: note placement no longer reloads (stutters) the timing bars | done |
| Status/log line moved to the left of Settings + the page tabs | done |
| Difficulty name shown once, at the right of each view's chrome | done |
| SV editor: click selects a green line; Delete/Backspace removes the selection | done |
| Undo/redo re-read the document into every open Editor view | done |
| SV editor: green-line mode drags an existing point instead of stacking | done |
| SV editor: snapped dashed ghost line + SV dot before placing | done |
| SV editor: red / green / **yellow** vertical lines resolved per timestamp | done |
| One hit object and one inherited point per millisecond (spinners excepted) | done |
| Inserted notes get a unique `original_index` + an `applied_positions` entry | done |
| Ctrl+C / Ctrl+V for chart notes and for SV points, scoped to the Editor page | done |
| Function mode: generate on notes in range (default) or every N snaps | done |
| Function mode: position offset defaults to −5 ms | done |
| SV views ride the playback clock alongside chart views | done |
| Zoom (`window_ms`) is per difficulty, shared by all its views | done |
| R11 closed: transforms order the selection by note time, not by key | done |
| SV graph reads green-over-red where both share a millisecond | done |
| SV graph axis sits on round bounds (0.5x / 1.5x), sticky, both labelled | done |
| SV graph joins the points with straight segments, not a staircase | done |
| SV function chooser is seven square buttons; configurables moved left | done |
| SV function preview plots real rates, so a falling sweep falls | done |
| Paste (notes and SV) snaps its anchor to the beat grid | done |
| SV lines rubber-band select, with auto-scroll past the view edges | done |
| A plain left click deselects, in both chart roles and the SV view | done |
| Generated/placed SV points carry the kiai state they land in | done |
| Spinners render a grey start-to-end band with edge caps | done |
| "Drumroll"/"Denden" relabelled "Slider"/"Spinner" (ids unchanged) | done |
| Language switch prompts to restart, in the newly chosen language | done |
| Function mode prefills the range's current SV at both ends | done |
| Function mode reaches the exact final rate on its last point | done |
| Wheel = 1 snap, Shift+wheel = 1 beat, in every scrolling view | done |
| Spinner/slider extents stay drawn once their start scrolls off-screen | done |
| Shift+click places a big (finisher) don or kat | done |
| Green/red lines drag horizontally to retime, snapped to the grid | done |
| Click proximity to the value dot picks the drag axis (SV vs retime) | done |
| SV axis floor fixed at 0.1x; only the ceiling autoscales, from 2.5x | done |
| SV points two or more beats apart connect with a step, not a diagonal | done |
| M6 `GameplayViewerView`: each object scrolls at the SV at its own time | done |
| M6 barlines every `meter` beats + one per uninherited point | done |
| Gimmick maps (thousands of points, absurd BPM) no longer freeze the editor | done |
| Timing lookups are binary searches that allocate nothing | done |
| SV graph axis is per document, so it cannot flicker while scrolling | done |
| First-start language picker, before any UI text exists | done |
| osu! Songs folder chosen once, remembered in `library/songs_folder` | done |
| Sliced, cached scan for `Mode: 1` charts; the window stays responsive | done |
| Song library page: search, song list, difficulty list, open into the editor | done |
| Esc (or the Songs tab) leaves the editor, asking about unsaved difficulties | done |

`gui.py`'s flat attributes (`document`, `applied_positions`, `selected`, the
undo/redo stacks, ...) are now `@property` forwarders onto `self.state`, one
`DifficultyState` per source path visited this session, cached in
`self._states`. `_load_map_path` is `_ensure_state` (parse once, cache) +
`_activate_state` (switch the active difficulty, only touching what changed:
audio source is left alone when the new difficulty shares the previous one's
file, and each difficulty keeps its own `playhead_ms`). `apply_selection` /
`reset_applied` / `undo` / `redo` push `MoveNotes` / `SetNotePositions`
through `self.state.history` instead of snapshotting `applied_positions`.

The Editor page (`MainWindow._build_editor_page`) sits before the Fancy
Arranger page (`_build_fancy_arranger_page`) in a `QStackedWidget`. As of
layout v2 the two pages are **not** identical below a shared deck: the big
note-scrolling `self.timeline` and its own snap combo / playback row live
only on the Fancy Arranger page; the Editor page has its own viewing-controls
strip (`self.editor_snap_combo`, the relocated `self.timing_bar`,
`self.editor_play_button` + speed buttons) above its view stack. Only the
global header (Open .osu, Undo, Redo, Save, Export-new-difficulty, Settings,
page tabs) is genuinely shared. `+` opens `AddViewDialog` (type + difficulty,
now five types including density); chart views are real `TimelineGameplay`
instances in `symmetric` mode (grid ticks anchored to the view's top/bottom
edges, notes centered on the baseline), grouped under a per-difficulty
header. `self._chart_views` (all TimelineGameplay instances) feeds the
`_render_gameplay_frame` clock broadcast; `self._editor_chart_views` (the
Editor-page subset, excluding `self.timeline`) feeds the Editor page's own
global snap sync. The gimmick view type is the last chrome-only placeholder —
and it is now scheduled for deletion rather than completion: M5 moves the
gimmick editor to its own page (see Next steps), so nothing will open it from
`AddViewDialog` any more.

Known gap: the spec's "when content fits, the wheel scrolls the timeline
horizontally" fallthrough for the Editor page's view-stack scroll area is not
implemented; wheel there scrolls the view stack (or does nothing) via
default `QScrollArea` behavior. Left as a follow-up rather than blocking the
shell on a UX nuance with no functional risk.

**Default views on open.** `_activate_state` now opens a `chart` + `sv` view
for a difficulty the first time it's activated (checked via
`state.source_path not in self._editor_view_groups`), so the Editor page is
never empty right after opening a map. Closing both and switching away and
back reopens the default pair — closing is not remembered as "user wants
none," since there's no per-difficulty preference storage for that yet.

**Playback accuracy and frame pacing.** Two independent problems, both in the
`_render_gameplay_frame` / `_player_position_changed` / audio-anchor system:

- `_predicted_audio_position()` now extrapolates with `QElapsedTimer.nsecsElapsed()`
  instead of `.elapsed()` (integer milliseconds). The old rounding error scaled
  with playback rate — 1ms of truncation became `rate` ms of position error —
  so it was worst exactly where "accuracy across all speeds" matters most.
- `_player_position_changed` used to hard reset the extrapolation anchor on
  *every* `QMediaPlayer.positionChanged` signal, including ones that only
  differed from the prediction by backend rounding noise; each reset was a
  visible correction jump. It now only re-anchors past
  `POSITION_RESYNC_THRESHOLD_MS` (30ms) of disagreement — real drift still
  self-corrects promptly, backend jitter no longer shows up as a stutter.
- `_render_gameplay_frame`'s frame gate used to `gameplay_frame_clock.restart()`
  every time it fired a frame, so a timer tick landing a little late
  permanently shifted every future frame's timing too — the ~120fps target
  was really an uneven 8–12ms sawtooth. It now schedules off a fixed
  `self._next_frame_due_ns` cadence that isn't reset each frame, with a
  stall-resync (`if self._next_frame_due_ns < now_ns`) so a real stutter
  doesn't turn into a burst of catch-up frames afterward.

### Bugs found and fixed along the way

Beyond the planned risks: `editor_timeline_metadata` read `PreviewTime` from
`[Editor]`, but it is a `[General]` key, so the yellow PreviewTime marker
documented in the README has never appeared on any real beatmap.

R4 was also downgraded on inspection. It raises `IndexError` before the payload
is assembled, and the write goes through a temp file, so nothing reaches disk —
an outright failure to write on an unusual section order, not silent corruption.

---

## Next steps

In order.

M6 was built ahead of these, at the owner's request, and did not need the extraction below: a
distance axis shares no mechanics with a time axis beyond the wheel step, which is now
`wheel_seek_time()`.

1. **Extract a `TimeAxisWidget` base from `TimelineGameplay` / `SVEditorView`.** M4 deferred this
   deliberately; the third feedback round then duplicated `zoom_changed`, the `set_time` throttle,
   selection + Delete, and the hover-ghost pattern across both, in two slightly different
   spellings. M5's gimmick editor is a third widget on the same axis, so this is the last cheap
   moment — after M5 it is three copies, not two. This is a prerequisite for M5, not optional
   cleanup. The fourth round added a third pair to the pile: rubber-band range selection plus
   `_auto_scroll_selection` now exists in both widgets, in two spellings that differ only in what
   they select.
2. **M5 — gimmick editor, on its own page** (tools `7` fake slider, `8` gimmick effect), as
   specced below. Locked with the owner 2026-08-10: it is a **fourth tab in the global header**
   (`Songs | Editor | Gimmick | Fancy Arranger`), *not* a view type inside the Editor page — so
   the `gimmick` entry in `AddViewDialog` and its placeholder frame go away when this lands. See
   "M5 — Gimmick editor" below for what that changes. Two things it must confirm rather than
   assume:
   - the barline pass generates **uninherited** points in dense clusters at `t ± 2/4/6`, and the
     one-object-per-millisecond rule the third round added is deliberately **inherited-only**, so
     it does not stand in the way — but nothing tests that interaction yet;
   - `transformer.py:333` `_timing_point_values` still carries the old `len(fields) < 7` /
     `fields[6]` string parser (the last surviving ad-hoc timing reader). It is dead for the app
     today, since `gui.py` hands it real `TimingPoint` objects and it takes the attribute branch.
     Delete it rather than let a gimmick code path find it.

Deferred, not blocking: R5 (`set_document_background` index repair), R10 (bounded undo stacks,
now multiplied by open difficulties), R3/R4 as recorded in the risk table.

**Risk-table corrections found while re-reading:** R1 and R6 are **already fixed** and were
mis-recorded as open. `gui.py`'s `kiai_ranges` delegates to `osu_io.timing.kiai_spans` (which
masks `EFFECT_KIAI` correctly) and `extract_timing_points` delegates to `uninherited_points`
(which has no field-count requirement). Both were replaced during M1's consolidation; only the
table entries were left behind. They are struck through below.

---

## Why

The app today moves note x/y coordinates and nothing else. We are adding a new primary **Editor**
page (note editing, SV editing, gimmick tools, gameplay preview) while keeping the existing
**Fancy Arranger** as a second page.

The blocker is that the current data layer physically cannot express these features:

- `OsuDocument` (`osu_io/parser.py`) is a **line buffer**, not a model. Only `[HitObjects]` is
  parsed; `[TimingPoints]` exists solely as raw text. Five independent ad-hoc timing parsers are
  scattered across `gui.py`, `transformer.py` and `gui_draft.py`.
- `write_osu` patches only fields 0 and 1 (x, y) of each note's original line. Nothing can be
  added, removed or reordered.
- **`parser.py:23` discards every field past `fields[5]`.** The fake slider
  `256,192,54692,2,12,L|624:192,643,-0.0001` parses to `hit_sample="L|624:192"` with
  `643,-0.0001` **lost**. Invisible today only because the writer never regenerates the line —
  the moment `[HitObjects]` is regenerated (required for note editing *and* for placing fake
  sliders) every slider, drumroll and spinner in every map is destroyed.
- `gui.py:1873` wipes selection, undo, redo and preview cache on **every** difficulty switch,
  which is what makes multi-difficulty impossible.

---

## Decisions locked with the owner

| Decision | Choice |
|---|---|
| Page order | **Editor first**, Fancy Arranger second |
| Multi-difficulty | Views stack vertically, grouped **under their difficulty**, never movable |
| Barline offsets | Symmetric: don → `t±2`; kat → `t±2, ±4, ±6` |
| Invisible note | "Infinite BPM" uninherited point, **BPM exposed as a parameter**, default `beatLength = 0.0001` |
| Lock button | Makes a view **read-only** |
| SV model | Curve **generation + manual editing** of the generated points |
| First running milestone | Editor shell + chart viewing (M2) |

---

## Milestones

### M0 — Cleanup and flagged items (v1.0.4)

- **`gui.py:1758`** — `self.group_pages` is never assigned anywhere. Accepting a drawing raises
  `AttributeError` before `preview_cache.clear()` / `schedule_preview()`, so the preview silently
  fails to refresh. Use `self._transform_page_refs`.
- **`gui.py:2111`** — `self.status_label` does not exist (the attribute is `self.status`); the
  branch throws and writes a bogus "Preview error" into the status bar.
- Remove the orphaned duplicate `self.status` (`gui.py:1393` vs `:1402`, both added to toolbar).
- **Wire Image-to-Drawing** — `gui.py` never imports `image_trace_dialog`, so the feature is
  unreachable. Add the "Import Image..." button to `DrawingDialog`; its Japanese is already in the
  catalog, marked `vanished`.
- **Security tests** — `security_utils.py` and `tests/test_security.py` are gitignored by patterns
  meant for temporary review artifacts, so those 2 tests never run in CI.
- **`polyline` / `bezier`** — in `available_transformations()` but absent from `PARAMETERS`, so
  they never reach the GUI despite being in the README.
- `TimelineGameplay.selection_finalized` (`gui.py:694`) is declared and connected but never
  emitted, making `_selection_finalized` dead code.

### M1 — Foundations (no visible change)

**Step 0 — regression net first.** `tests/fixtures/` with a v14 map (kiai + inherited SV + fake
slider + spinner), a v4 map with two-field timing points, a cp1252 map, a map with `[Colours]`
*between* `[TimingPoints]` and `[HitObjects]`, and a map with no `[TimingPoints]`. Plus
`tests/test_osu_roundtrip.py`. This is what makes the writer change safe.

**`osu_io/timing.py`** — new leaf module, no Qt, no parser import (keeps the dependency graph
acyclic; `transformer.py` must use it without importing the parser). Mutable
`dataclass(slots=True)` `TimingPoint` storing all 8 osu fields verbatim plus `uid` and
`source_line_index` (`-1` when newly created). `uninherited_flag` stored as the **raw int** with
`uninherited` as a property, so serialization can never drift from the derived value.

Derived: `uninherited`, `kiai` (`effects & 1`), `omit_first_barline` (`effects & 8`), `bpm`,
`sv_multiplier` (`-100.0 / beat_length`). Constructors take a `template` point so bulk-generated
points inherit `sample_set`/`sample_index`/`volume` instead of silently resetting hitsound volume.

Parse **defaults per-field, never require 7 fields** — osu format v4 and earlier write only
`offset,ms_per_beat`.

**`model/hit_object.py`** — replace the misuse of `hit_sample` with `extras: tuple[str, ...]`
holding middle fields **verbatim as strings** (raw strings round-trip `-0.0001` and `L|624:192`
exactly; a typed slider model risks reformatting `643` → `643.0` across every existing map). Split
with a terminator test, not positional guessing, since sliders have 6, 8 or 9 fields:

> If the last field matches `^\d+:\d+:\d+:\d+:` it is the hit sample and `extras = fields[5:-1]`.
> Otherwise `hit_sample = ""` and `extras = fields[5:]`.

Add `to_line(ending)`, `uid`, type predicates, and gate `note_kind` on `is_circle` with new
`"drumroll"` / `"denden"` kinds.

**`osu_io/writer.py`** — the model owns `[TimingPoints]` and `[HitObjects]` and regenerates them;
**every other section is passed through byte-for-byte.** Verbatim passthrough is a real safety
property: storyboard events, breaks, `[Colours]`, `[Editor]` bookmarks and assorted `[General]`
keys are modelled nowhere here, and a full section-aware model would have to round-trip all of
them or silently drop mapper data. Regenerating exactly the two sections we model gets the
mutability where it is needed and keeps the guarantee everywhere else. Documented, deliberate
loss: `//` comments inside those two sections only.

Kill the flat `shift` via span recomputation, splice sections in descending start index, add
`validate_timing_points()`, strengthen the post-write assertion beyond hit-object count, move the
`.bak` copy to after validation, add the missing `fsync`, and fall back to UTF-8 when a `cp1252`
document gains generated non-Latin text.

**`model/editor_state.py`, `model/commands.py`, `model/history.py`** — pure data, no Qt import,
unit-testable without an offscreen QApplication. `DifficultyState` absorbs 9 `MainWindow`
attributes plus `history` and `playhead_ms`; `settings`, `shortcuts`, widget registries and the
single audio device stay global. Migrate behind `@property` forwarders so existing call sites keep
working. Split `_load_map_path` into `_ensure_state` / `_activate_state`, setting the player source
only when the audio file actually differs.

Command model replaces position-only snapshots: `MoveNotes` (with `merge_with` to coalesce drag
streams), `SetNoteFields`, `Insert`/`RemoveHitObjects`, `Insert`/`Remove`/`EditTimingPoints`, and
`CompositeCommand` — the load-bearing one, making a 400-point SV sweep a single undo step. One
`History` per `DifficultyState` gives independent per-difficulty undo for free.

### M2 — Editor page shell + chart viewing

Root layout (`gui.py:1337`) is a plain `QVBoxLayout`; the comment at `gui.py:1411-1413` already
documents rows 3–6 as a shared "player deck". Insert a page switcher between the toolbar and the
workspace, **Editor first**. The four view widgets need **no changes** to be reused — they take no
constructor args, hold no MainWindow reference and communicate purely by signal.

- Shrunken timeline strip at top: current time + position percentage to one decimal.
- `+` button, tooltip "open new view" → choose view type (chart / SV editor / gimmick editor /
  gameplay viewer) **and** difficulty.
- Views stack downward, grouped under their difficulty, never movable. Overflow scrolls
  vertically; when content fits, the wheel scrolls the timeline horizontally as today.
- Per-view chrome: **close** and **lock** (read-only) at left; difficulty name at top-right of the
  chart editor only.
- Snap set extended to 1/1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,18,27,32,36,48. Existing colors
  preserved exactly; new divisors get new colors, same behavior.
- Snap lines at **top and bottom** in chart and gimmick views so notes stay centered; **bottom
  only** in the SV editor.

`MainWindow.eventFilter` (`gui.py:1298-1317`) hard-codes three widget references and must become
page-aware. `_render_gameplay_frame` (`gui.py:2248`) is the single clock fan-out point and should
broadcast to a registered list.

**Implemented** (page switcher, view stacking, per-view chrome, snap extension, page-aware
`eventFilter`, clock broadcast). Then grilled with the owner on 2026-08-08 past what the bullets
above cover, revising the shell before M3 builds on top of it — see **Editor page layout v2**
below, which supersedes the "Shrunken timeline strip" and "Per-view chrome" bullets above.
**Also implemented** the same day.

### Editor page layout v2 (locked and implemented 2026-08-08)

**Shared player deck is no longer identical on both pages.** The big note-scrolling
`self.timeline` (circles scrolling by) is **Fancy-Arranger-only**; the Editor page's view stack
expands downward into the space it vacates instead of reserving room for a redundant chart at the
bottom.

**Global header row, both pages:**
`Open .osu ⋯ Undo | Redo | Save | Export ⋯ Settings`, with the Editor / Fancy Arranger page tabs
at top-right of this same row.
- `Save` writes every changed `DifficultyState` in `self._states` at once (batched
  apply-to-original), not just the active one.
- `Export` creates a **new difficulty file in the same song folder** — distinct from Fancy
  Arranger's existing "Export applied map" (arbitrary destination via file dialog), which stays.
- Difficulty selector, Play, Reset, the existing Export-applied-map / Apply-to-original buttons,
  AR and CS all move to be **Fancy-Arranger-only**, no longer in the global row.

**Editor page's own viewing-controls strip** (below the global header, Editor page only), left to
right: beat-snap combo → current time + percentage (one merged readout, not two) → timing bar
(kiai / bookmarks / SV / BPM — this **is** `self.timing_bar`, relocated here, not rebuilt) → ▶ +
speed buttons. `+` (open new view) stays on the Editor page, right-aligned in this same strip.

**Density heatmap becomes a 5th view type**, not shared-deck furniture: open it like chart/SV/
gimmick/gameplay via the `+` dialog. It stacks **bottommost within its own difficulty's group**
(other views for that difficulty sit above it), not globally bottommost across all difficulties.

**Snap divisor is global across the Editor page.** Alt+wheel must update every open chart view's
`snap_divisor` together (today it only updates `self.timeline`'s), so all views stay on the same
grid.

**Per-view difficulty-name label stays chart-only.** SV/gimmick/gameplay/density views rely on
the difficulty group header above them instead of repeating the name per view.
*Superseded by the third feedback round:* the group header put the name on the left while the
chart view's own label put it on the right, so it read as labelled on both sides. The header lost
its label and every view type gained the right-hand one instead.

**Tool palette (M3/M4/M5, shared placement rule — not yet built, placement locked only):** a
horizontal row of **square** tool buttons pinned to the **bottom** of each `EditorViewFrame`,
staying put even when the view's content exceeds the visible area (sticky footer, not part of the
normal scrolling flow). No tools exist yet — SV/gimmick/gameplay views are still placeholders;
this rule just fixes where M3/M4/M5 hang their buttons when they land.

Implementation notes: `_build_fancy_arranger_page` now owns Difficulty/Play/Reset/old-Export/AR/CS
plus `self.timeline`/`self.snap_combo`/`self.timeline_info`/the playback row, all unchanged in
behavior, just moved off `root` and into that page's own layout. `_build_editor_page` owns
`self.editor_snap_combo`, `self.timing_bar` (relocated, single instance), `self.editor_play_button`
+ `self.editor_playback_speed_buttons`, and the `+` button, all in one strip. `self._chart_views`
(all TimelineGameplay instances, for the clock broadcast) and `self._editor_chart_views` (the
Editor-page subset, for global snap sync) are tracked separately since `self.timeline` should not
follow the Editor page's Alt+wheel. `save_all_states`/`export_new_difficulty` are new `MainWindow`
methods; `History.dirty`/`mark_saved()` (already existed, unused until now) drive the former.

### Bugs found while building M2, fixed 2026-08-08

- **Symmetric snap grid was wrong.** `TimelineGameplay.symmetric` mode drew ticks straddling the
  middle baseline and centered notes on it. Fixed to the reverse: notes alone stay centered on the
  middle line; snap ticks anchor to the **top and bottom edges of the view rectangle** and grow
  inward, framing the notes.
- **Closing a difficulty's last view orphaned its group header.** `_close_editor_view` now calls
  `_prune_empty_difficulty_group`, which removes the group widget (header included) once its last
  `EditorViewFrame` closes.
- **Chrome buttons lost the app-wide pink `QPushButton` styling.** `EditorViewFrame`'s bare,
  selector-less local stylesheet broke the cascade for its own descendants. Fixed by styling
  `close`/`lock` explicitly rather than relying on inheritance from the global sheet.

### M3 — Note editing (implemented 2026-08-08)

Tools `1` select, `2` don, `3` kat, `4` slider, `5` spinner, `6` new combo, in the bottom tool-palette
row (see Editor page layout v2). Left click places, right click on a note deletes. Requires
`[HitObjects]` regeneration from M1.

`TimelineGameplay` gained `tool`/`new_combo` state (defaulting to `"select"`, changed only by a
chart view's own tool row, never anything on the shared Fancy Arranger timeline) and two signals:
`note_place_requested(note_kind, snapped_time_ms, new_combo)` on left click when `tool != "select"`,
and `note_delete_requested(uid)` on right click near an existing note (any tool, any view). The
widget still holds no `MainWindow` reference — `_add_editor_view` connects both signals to
`MainWindow._place_note` / `_delete_note`, which push `InsertHitObjects` / `RemoveHitObjects`
through the owning `DifficultyState`'s history and call `_refresh_difficulty_views` to re-read
notes into every open view of that difficulty (including the Fancy Arranger timeline, if it's
showing the same one) via the new `TimelineGameplay.refresh_notes` — `load_document` minus the
cursor/selection reset an edit must not cause.

New notes default to the playfield center (`256,192`; taiko gameplay ignores hit-object position),
`hit_sound=HITSOUND_CLAP` for kat, a straight 80-osu!px single-slide drumroll, and a 1-second
denden. `tests/test_note_editing.py` (12 tests) covers all of this: each placed shape, the
shared-timeline isolation, multi-view refresh, undo/redo, real `QMouseEvent`s driving
`mousePressEvent` (not just direct signal emission), a real write-to-disk round trip
(`write_osu` → `parse_osu`), and locking a view disabling both its content and tool row.

Deliberately deferred, none blocking: **no keyboard shortcuts** for tool switching (`1`–`6`) — the
number keys would need to stay scoped to `self.symmetric` views to avoid resurrecting the
Fancy-Arranger-timeline regression risk documented in Editor page layout v2's `eventFilter`
section, and the tool-row buttons would need a `tool_changed` signal back from the view to stay
visually in sync with a keyboard-driven change; skipped rather than shipped half-wired. **No
drumroll length/curve editing UI** — placed with a fixed reasonable default, matching M3's stated
scope ("left click places, right click deletes," not "and then resize").

### Owner feedback round after trying M3 (2026-08-08)

- **Tool row moved from per-view to global.** Reversed after trying the per-frame version in
  practice. `MainWindow._build_global_tool_row()` builds it once, added at the bottom of the Editor
  page (below the view stack, not literally the whole window, since tools are meaningless on Fancy
  Arranger). It acts on `self._active_chart_view`, tracked via `QApplication.focusChanged` (gated
  to `symmetric` views, so clicking `self.timeline` can never steal it) **and** set directly when a
  view is created, since `setFocus()` → `focusChanged` needs a truly active top-level window, which
  doesn't hold under every platform plugin (offscreen tests included).
- **Click-to-seek suppressed on Editor-page views.** `TimelineGameplay.mouseReleaseEvent`'s
  "short click = seek" shortcut now checks `not self.symmetric` first. Fancy Arranger's own
  `self.timeline` (`symmetric=False`) is unaffected — that's how users scrub it.
- **Slider = yellow (`self.slider_brush`), spinner = `assets/skins/spinner.png`** (not
  `spinner_warning.png` — corrected the name), scaled into the note's bounding circle via
  `spinner_pixmap()`, module-level-cached since constructing a `QPixmap` needs a live `QApplication`
  and shouldn't happen every paint.
- **Playback timing corrected further.** `_player_position_changed`'s fixed 30ms
  ignore-below-threshold had a real bug: a small *steady-state* bias (e.g. consistent decode/resample
  latency at a slow rate) never crossed the threshold on any single report, so it went uncorrected
  for the entire playback — exactly the "not accurate at slow rate" symptom reported. Replaced with
  `POSITION_CORRECTION_FACTOR` (0.3) gradual blending for small discrepancies, snapping immediately
  only at `POSITION_HARD_RESYNC_THRESHOLD_MS` (200ms, a real discontinuity). `tests/test_playback_timing.py`
  covers both paths plus convergence under a simulated steady bias.

  **Known limitation, not fully fixable from here:** some of the residual inaccuracy is plausibly
  Qt Multimedia's own FFmpeg-backed rate-changing, not this app's tracking code — osu!'s own engine
  (historically BASS/BASS_FX) does higher-quality time-stretching. Swapping the audio backend
  entirely is a much larger undertaking than tuning the estimator and wasn't attempted here.
- **Paint performance.** `TimelineGameplay._draw_snap_grid` constructed a new `QColor`/`QPen` per
  tick per frame; at fine snap divisors with several chart views open at once (now the default,
  since M2 auto-opens chart + SV per difficulty) that allocation churn was a real stutter
  contributor, separate from the frame-scheduler fix. `self._tick_styles` now builds every
  (color, width) combination once in `__init__` and `_draw_snap_grid` just looks one up.
- **Fancy Arranger regained its own timing bar + density chart**, lost when layout v2 moved
  `self.timing_bar` to the Editor page and retired the old shared `self.overview`. `self.fancy_timing_bar`
  and `self.fancy_density` are second instances of the same widget types (a `TimingOverviewBar` or
  `DensityOverview` can only live in one layout at a time) at the bottom of the Fancy Arranger page,
  timing bar first, density right below it. Kept in sync via `self._timing_bars` (both bars) and
  `self._density_views` (`self.fancy_density` appended alongside Editor-page density views, but
  reloaded on every `_activate_state` unlike those, since it always tracks the active difficulty).
- **Transform-controls panel widened** 390px → 460px (`workspace_splitter` resized to `[900, 460]`)
  so its longer button text (e.g. "Apply all changes to original file") fits without clipping.

`tests/test_gui_feedback_round.py` (8 tests) covers the click-to-seek isolation, slider/spinner
rendering, panel width, and the Fancy Arranger timing bar/density restoration.

### M4 — SV editor (implemented 2026-08-08)

Tools `1` select, `2` green line, `3` function, in the **global** SV tool row (same relocated-row
pattern M3's feedback round established — see there for why per-view was reversed). Red lines
render vertical with BPM + the 1.0x SV an uninherited point resets to; green lines vertical with
their SV multiplier; plus a horizontal step-graph connecting the effective SV over time (log-scaled
`SV_VISUAL_MIN`–`SV_VISUAL_MAX` = 0.25x–4x, a visual-only mapping that doesn't affect stored
values). Dragging the graph in select mode adjusts the nearest point's SV, merging into one undo
step per drag via `EditTimingPoint.merge_with` — same tested pattern as the model layer's own
`test_edit_timing_point_and_merge`. Green-line mode clicks to add a point at the click's time
(snapped) and vertical position (mapped to SV). Right click deletes the nearest **inherited** point
only — an uninherited (BPM) point is out of this milestone's scope, and removing the map's only one
would leave it without timing at all.

`SVEditorView` duplicates `TimelineGameplay`'s time-axis mechanics (`time_for_x`/`x_for_time`,
wheel zoom/seek/snap) rather than sharing a base class with it — a real "no changes to reuse" case
would have wanted a `TimeAxisWidget` split, but timeboxing this milestone against an already-large
session favored working duplication (the codebase already has some between `TimingOverviewBar` and
`DensityOverview`) over a base-class refactor mid-feature. It participates in the Editor page's
global snap sync via the same `self._editor_snap_views` list chart views use (renamed from
`_editor_chart_views` now that it holds both types).

Function mode: drag-select a range on the SV view → `SVFunctionDialog` (initial rate, final rate,
position offset in ms, omit barline — default **off**, applied to only the first generated point —
relative to final BPM — default **on** — a function chooser, and the 20-dot preview square, sharing
its easing math with the real generator via `sv_ease()` so the preview is an honest picture of what
Generate produces) → **Generate** computes points at roughly one per 20ms (capped 2–500, same order
of magnitude as the model layer's tested 400-point sweep) and pushes them as one
`CompositeCommand([InsertTimingPoints(...)], "generate_sv")` — one undo step regardless of point
count. "Relative to final BPM" scales the raw rate by `local_bpm / start_bpm` at each generated
point, so the perceived scroll speed matches the requested rate even where the range crosses a BPM
change, rather than just the literal SV multiplier.

Functions: `linear`, `sin in`, `sin out`, `exp1.3`, `exp1.6`, `true exp` (`(e^{kt}-1)/(e^k-1)`,
k=3), `sin` (symmetric ease-in-out).

`tests/test_sv_editor.py` (23 tests) covers every function's endpoints, the SV↔Y round trip, add/
edit/delete via both direct signals and real `QMouseEvent`s, drag-merging, the refusal to delete an
uninherited point, multi-view refresh, undo/redo, generation (undo-step count, endpoint values,
position offset, omit-barline scope, relative-to-final-BPM scaling), a write-to-disk round trip,
and — importantly — the *real* drag → dialog → Generate pathway (not just calling `_generate_sv`
directly): the SV view auto-opened by `_load_map_path` already has `function_range_requested`
wired to the real `_open_sv_function_dialog`, so a naive test that drags first and swaps in a fake
dialog class afterward hits the real, un-faked `QDialog.exec()` and hangs forever offscreen — the
fake class has to be in place *before* the drag.

### Second owner feedback round, after trying M3/M4 (2026-08-08)

- **Global keyboard shortcuts for tool digits.** `MainWindow._build_tool_shortcuts()` registers
  `QShortcut`s for `1`–`6` with `Qt.ApplicationShortcut` context, guarded by
  `should_ignore_shortcut_focus` (the same helper Play/Undo/Redo already use) so typing into a
  spin box doesn't switch tools. `_activate_tool_digit` routes to whichever of
  `global_tool_row`/`global_sv_tool_row` is currently *visible* (they use overlapping digits for
  different tools) and drives the real button's `setChecked(True)` rather than calling
  `_set_active_tool` directly, so the visual checked state and the keyboard shortcut can never
  desync — this is exactly the concern that shelved keyboard shortcuts during M3; routing through
  `QShortcut` at the `MainWindow` level instead of a per-widget `keyPressEvent` override sidesteps
  the earlier-identified risk to the shared Fancy Arranger timeline entirely, since the handler
  only ever touches `_active_chart_view` / `_active_sv_view`, both exclusively Editor-page views.
- **Tool buttons relabeled** from a bare number + tooltip to visible `"N. Name"` text (`"2. Don"`,
  `"3. Function"`, ...) on both tool rows.
- **SV editor's per-point value label moved** from a fixed bottom row to the step-graph line's own
  height (`_sv_to_y`), text simplified to `"x.xxx"`. BPM stays at the bottom, unchanged. Since the
  label's x position was already `x_for_time(point.time)`, it already scrolled with playback
  exactly like a note circle in the chart view — moving it vertically didn't need any new
  "follow the playhead" logic, just attaching it to the graph loop instead of the vertical-line loop.
- **Fancy Arranger's kiai/bookmark bar merged into the time row** (`timeline_row`), matching the
  Editor page's own strip layout instead of being a separate row below it; the time label gained
  the percentage the Editor strip already had (`_update_timeline_info` now formats both together).
- **Hover ghost preview.** `TimelineGameplay.setMouseTracking(True)` (needed since plain
  `mouseMoveEvent` only fires with a button held otherwise) tracks `self._hover_time`;
  `_draw_placement_ghost` paints a translucent don/kat circle, or a translucent slider/spinner via
  `_draw_extend_ghost`, at the snapped hover position whenever a placement tool is active. The same
  method also renders the *live* preview while actively drag-placing or resizing a slider/spinner,
  so there's one rendering path for "about to place," "placing," and "resizing," not three.
- **Spinners always render at the finisher/"big note" radius**, independent of the finisher
  hitsound bit — a visual-only convention for this app. Sliders now render their actual extent (a
  rounded bar from start to computed end time), not just a single circle at the start — necessary
  once the end could be dragged, and a plain visual gap before that.
- **Slider/spinner placement became click-drag** (start position → stop position) instead of a
  single click with a fixed default length; a plain click without drag still places a short object
  (`duration = max(20ms, end - start)`) rather than nothing. New signals
  `note_place_with_duration_requested(note_kind, start_ms, end_ms, new_combo, big)` and
  `note_duration_edit_requested(uid, new_end_ms)` — the *existing* `note_place_requested` stays
  don/kat-only, an intentional scope split rather than overloading one signal's meaning.
  **Extending an already-placed slider/spinner afterward** works by pressing near its computed
  right edge (`_extendable_note_near_edge`, checked before starting a new placement) and dragging;
  the edit is committed once on release, not per drag tick, so `SetNoteFields` didn't need a
  `merge_with` the way `MoveNotes`/`EditTimingPoint` do.
- **Slider length is computed from the dragged time duration** via the standard osu! formula
  (`length = duration × SliderMultiplier × 100 × SV / beatLength`, and its inverse for hit-testing
  an existing slider's end) using `SLIDER_MULTIPLIER_ASSUMED = 1.4` (osu!'s own default) — a
  **documented approximation**, since this app parses neither `[Difficulty]` nor any per-map
  `SliderMultiplier` override. Getting this exactly right would mean adding that parsing; out of
  scope for a click-drag gesture whose point was the *interaction*, not slider-length precision.
- **Shift held at release places (or leaves, when resizing) a "big" slider** — sets the finisher
  hitsound bit, which the existing `is_finisher` render path already draws at the bigger radius, so
  no separate "big" rendering path was needed. Caught a real bug while verifying this manually: the
  release handler originally read only `QApplication.keyboardModifiers()`, which reflects real,
  asynchronous OS keyboard state and is blind to a synthetic `QMouseEvent`'s own modifiers (and,
  per `wheelEvent` elsewhere in this class, can be stale even for real input) — fixed to combine
  `event.modifiers() | QApplication.keyboardModifiers()`, matching the pattern already established
  elsewhere in `TimelineGameplay`.
- **Fixed a real stutter/flicker bug**: `_refresh_difficulty_views` (run after *every* note
  place/delete) was unconditionally reloading every `TimingOverviewBar`, even though a note edit
  never touches `[TimingPoints]` or `[Editor]` bookmarks — pure wasted work, and a visible one,
  since `TimingOverviewBar.load_document` forces an immediate repaint outside the normal ~120fps
  broadcast cadence. Removed from the note-edit refresh path; `_refresh_difficulty_sv_views`
  (timing-point edits) still reloads them, correctly.

`tests/test_editing_ux_round2.py` (26 tests) covers all of the above: shortcut routing and its
text-entry guard, numbered button labels, the SV label's graph-line position, the Fancy Arranger
row merge and percentage, ghost painting for every tool and every live state without raising,
click-drag placement (including "a longer drag makes a longer slider" and the plain-click
fallback), resize-by-edge-drag for both shapes, Shift-for-big (and its absence), undo/redo, a
write-to-disk round trip, and — with a `TimingOverviewBar.load_document` spy — that note edits
no longer trigger it while SV edits still do.

### Third owner feedback round, after trying the SV editor (2026-08-08)

M4 shipped a view that could *add* SV points. This round is what turns it into an editor: the
things you reach for after the first minute — delete, undo, drag, copy — plus the placement rule
the format actually needs.

**Layout corrections (both pages).**

- **The status/log line moved to the left of the header.** `self.status` sat between the action
  buttons and `Settings` / the `Editor` / `Fancy Arranger` page tabs, so a message like "Applied
  transformation to 42 notes." read as a caption belonging to those three controls. It now sits
  immediately after `Export`, left-aligned, with the header's stretch after it — the buttons stay
  right, the log stays left, and the two can no longer be confused for one group.
- **The difficulty name is shown once, at the right.** It used to appear twice for a chart view:
  in the per-difficulty group header (left, above the group) and again at the right of the
  frame's chrome row. The group header label is gone; `EditorViewFrame` now shows
  `difficulty_name_label` at the right of *every* view type, not just chart — with the group
  header removed, nothing else would identify an SV/density/gimmick view's difficulty. This
  supersedes layout v2's "per-view difficulty-name label stays chart-only" bullet.

**SV editor: the missing editing verbs.**

- **Selection.** `SVEditorView.selected_uids` — clicking a green line in select *or* green-line
  mode selects it (and starts a drag), clicking empty space clears, `Ctrl+A` selects every
  inherited point in the visible window, `Escape` clears. `refresh_points` intersects the
  selection with the live points, so a point deleted in another view can't stay "selected".
- **Delete / Backspace** emit `points_delete_requested(list[uid])` — one signal for the whole
  selection, so `_delete_sv_points` pushes **one** `RemoveTimingPoints` rather than one per point.
  It keeps the single-delete path's inherited-only rule: an uninherited point in the selection is
  skipped, never removed. `TimelineGameplay` got the same treatment
  (`notes_delete_requested`/`_delete_notes`), **gated on `self.symmetric`** so the shared Fancy
  Arranger timeline — a transform-selection surface, not an editing one — still ignores Delete.
- **Undo/redo actually refresh.** This was the real bug behind "can't even undo or redo with
  shortcut": `MainWindow.undo`/`redo` reverted the command and then called only `refresh_canvas()`,
  which repaints the *transform canvas*. Every Editor-page view kept painting the pre-undo
  document, so from the Editor page the shortcut looked dead even though the model had already
  rolled back. New `_after_history_change` re-reads the document into every chart view, every SV
  view and the timing bars. `_history_target` also picks the difficulty of the focused Editor
  view rather than always the Fancy-Arranger-active one, since an Editor view can be showing a
  different difficulty entirely.
- **Green-line mode drags an existing point** instead of placing on top of it — landing on a green
  line now begins the same SV drag select mode does. This is not just convenience: with one
  inherited point per millisecond (below), placing there could only ever have replaced what was
  already there, which is the opposite of what a drag on a value control should do.
- **A snapped ghost line** (`_draw_placement_ghost`) previews the click: a dashed vertical line at
  the snapped time, a dot at the SV the cursor's height maps to, and the value as text — the SV
  editor's equivalent of the chart view's translucent note ghost, and the same
  `setMouseTracking(True)` mechanism. Placement already snapped through `snap_time`; what was
  missing was any way to *see* where it would land before committing.

**Vertical-line colour is resolved per timestamp, not per point.** The old paint loop drew one
line per point, so an uninherited and an inherited point sharing a millisecond drew over each
other and file order decided the winner — which is why a BPM line with SV stacked on it rendered
plain green. `SVEditorView.line_kinds()` returns `{ms: "red" | "green" | "yellow"}` (yellow =
both kinds present), matching the convention `TimingOverviewBar` already used for its markers.
Extracting it as a method rather than leaving it inline in `paintEvent` is what makes the rule
testable without grabbing pixels.

**One object per millisecond, spinners excepted.** Both editors now refuse to stack:

- `_insert_notes` removes any existing **non-spinner** hit object at a placed note's timestamp and
  pushes the removal + insertion as one `CompositeCommand`, so a replacement is a single Ctrl+Z.
- A **spinner (denden) is exempt in both directions**: placing one never displaces anything, and
  placing anything else never displaces a spinner. Spinner starts and ends routinely land on top
  of notes and neither should push the other out.
- `_insert_sv_points` applies the same rule to **inherited** points only. An inherited and an
  uninherited point legitimately share a timestamp — that is exactly the case now painted yellow —
  so BPM points are never displaced.
- Both rules run on the generated-sweep path too, so function mode overwrites the green lines it
  lands on instead of burying them.

**Inserted notes finally get their own identity.** `HitObject.original_index` defaults to `0`, so
until now *every* note placed by M3 aliased the map's first note in `applied_positions` and in a
chart view's selection set. `_next_original_index` assigns a fresh key, and `InsertHitObjects`
registers `applied_positions[original_index] = (x, y)` on apply and removes **only the keys it
created** on revert. Without both halves, a unique index would have turned the previously silent
aliasing into a `KeyError` on save; the write paths also switched to `.get(..., (note.x, note.y))`
so a note predating its entry is written where it is rather than failing the whole save.

**Copy and paste.** `Ctrl+C`/`Ctrl+V` route by which tool row is visible, exactly like the tool
digits: chart → notes, SV → green lines. The clipboard holds **plain data plus a time offset from
the first item**, never live objects, so a paste always mints fresh identities and a later edit
(or an undo) can't mutate what the clipboard "contains". Paste lands at the target view's
`current_time` and goes through the same no-stacking insert path.

The shortcuts are `Qt.WidgetWithChildrenShortcut` on `self.editor_page`, **not** `ApplicationShortcut`
like the tool digits. An application-context shortcut *consumes* the key press, so a global
`Ctrl+C` would silently break copying text out of every line edit and spin box in the app —
`should_ignore_shortcut_focus` can decline to act but cannot hand the key back. (The existing
digit shortcuts have this same hazard for `1`–`6`; not touched here, but worth knowing.)

**Function mode generates on note positions.** `_sv_generation_times` replaces the old "one point
roughly every 20ms":

- **Each note** (the new default) — one point per hit object inside the selected range, nothing
  between them. An SV sweep only has to be correct where a note actually is; the old cadence
  wrote hundreds of timing lines nothing could observe.
- **Every snap** — walks the beat grid at a chosen divisor (defaulting to the Editor page's
  current snap), for sweeps that have to move continuously.
- **Position offset defaults to −5 ms.** An SV point governs what comes *after* it, so it must
  land slightly before the note it is meant to affect. Easing progress is still measured from the
  un-offset position, so the lead-in doesn't skew the curve or move the endpoint rates.
- A note-placement range containing no notes generates nothing and says so in the status line,
  rather than silently producing a two-point sweep at the range edges.

**SV views ride the playback clock, and zoom is difficulty-level.** `_render_gameplay_frame` and
`seek_audio` now broadcast to `self._sv_views` alongside `_chart_views`, and `SVEditorView.set_time`
gained `TimelineGameplay`'s 16ms throttle so the extra views don't cost a repaint per view per
frame. Scrolling together is only half of it: `window_ms` is now stored **per difficulty**
(`self._difficulty_zoom`) and a `zoom_changed` signal from either widget type syncs every view of
that difficulty, with newly-opened views adopting the difficulty's current zoom. Without that, a
chart showing 2s and an SV editor showing 4s put the same x at two different times, and "follows
the playhead together" stops meaning anything.

**Timing-bar SV markers: verified, not changed.** The green-line/function paths already ran
`_refresh_difficulty_sv_views`, which reloads every `TimingOverviewBar`; there was no wiring gap
to fix. It is now covered by tests asserting the bar's `timing_markers` actually contains the
added/generated point and drops a deleted one, rather than only that `load_document` was called.
The one case that genuinely does not update the bar is an edit to a **non-active** difficulty —
correct, since both bars only ever show the active one.

`tests/test_editing_ux_round3.py` (57 tests) covers all of the above: header widget order,
one-label-per-frame chrome, select/Delete/Backspace/Escape on green lines, single-undo-step group
deletes, the inherited-only delete rule, undo/redo refreshing both view types, green-line drag vs
add, snap-on-place, ghost paint for both tools without raising, the three line colours, note and
SV replacement (and the spinner exemption in both directions), unique `original_index` plus its
`applied_positions` lifecycle, copy/paste for both editors including relative spacing and the
no-stacking paste, editor-page scoping of the clipboard shortcuts, note-vs-snap generation, the
−5 ms default, generation replacing what it lands on, SV views on the clock, zoom sync in both
directions, the timing-bar markers, and R11's time-ordered selection.

**R11 closed as a side effect.** `_calculate_selected_transform` used `sorted(self.selected)`,
time-ordered only because `original_index` followed file order. Unique insertion keys break that
assumption directly — a note placed *before* every existing one now gets the *highest* key — so
the risk stopped being hypothetical the moment this round landed. It now uses the
`DifficultyState.notes_in_time_order()` helper M1 wrote for exactly this and nothing had called
since.

**Still open from this round.** The M4 note that `SVEditorView` duplicates `TimelineGameplay`'s
time-axis mechanics rather than sharing a `TimeAxisWidget` base is now more expensive than it was:
`zoom_changed`, the `set_time` throttle, `selected_*`/Delete and the hover-ghost pattern all
exist twice, in two slightly different spellings. This round deliberately matched the existing
duplication rather than refactoring mid-feature; a base-class extraction is the right first move
before M5 adds a third view type with the same axis.

### Fourth owner feedback round, after using the SV editor and copy/paste (2026-08-09)

Round three made the SV editor usable; this round is about it telling the truth — the graph was
showing values the document did not contain and hiding the shape of what it generated.

**A red line no longer reports 1.0x when a green line sits on it.** `SVEditorView.sv_series()`
returns one `(time, effective SV)` per *timestamp*, and an inherited point at a timestamp wins over
an uninherited one there. An uninherited point does reset SV to 1.0x, but mappers stack a green
line on a red one precisely to set the speed the new BPM section starts at, and osu! gives the
later line in file order the final say. Emitting one sample per *point* meant the red line
contributed a 1.0x reading that the green line immediately overwrote — a spike down to 1.0x at
every BPM change carrying SV. This is the value-side counterpart to round three's per-timestamp
line *colour*; both existed because the old code treated coincident points as independent.

**The graph autoscales, onto round bounds, stickily.** `update_scale()` tracks the SV visible in
the current window, including the off-screen point governing the left edge. A fixed 0.25x–4x window
wasted almost all the height on a map that never leaves 0.9x–1.1x, and clipped one sweeping to 8x
flat against the ceiling. Both limits are labelled on the graph — without them the same drawn shape
could be a 0.9x wobble or a 1x→8x sweep. `_sv_to_y`/`_y_to_sv` read the fitted range, so placement
and dragging stay consistent with what is drawn, and `autoscale = False` freezes it.

*Revised within the round*, on owner feedback, into two rules that the first version had wrong:

- **Bounds sit on a ladder of round values** (`sv_bound_below`/`sv_bound_above`, 0.5 steps down to
  0.1 and then 0.01 near zero) rather than on a multiplicative pad around the observed min/max. A
  0.9x–1.1x section now reads **0.5x to 1.5x**; the padded fit produced axes like 0.72x to 1.38x,
  arithmetically correct and useless to read a line against. The ladder value is always *strictly*
  outside the value it encloses, so the line is never drawn along the very edge of the graph.
- **Bounds are sticky.** A bound moves outward only once a value comes within `SV_BOUND_NEAR` (1.05)
  of it, and back inward only once the visible range fits `SV_BOUND_SLACK` (1.3) inside it. Refitting
  exactly to the content every frame meant the axis moved constantly while scrolling, so the line's
  height stopped meaning anything from one moment to the next.

  Two details that are easy to get wrong and were, on the first attempt. Expansion has to go a
  *ladder step past* the current bound, not merely to the step enclosing the value — for a value
  that has crept up on a bound those are the same number, so the line stays pinned to the edge that
  triggered the expansion. And contraction has to refuse any bound the value is already near, or the
  two rules fight: 1.48x against a 1.5x bound expanded to 2.0x, whose enclosing step is 1.5x again,
  which contraction then pulled back to — retuning the axis on alternate frames forever. Both are
  covered by `test_expansion_and_contraction_do_not_fight_each_other`, which settles the scale from
  eight values chosen to sit on and around the ladder steps.

**The step graph became a curve.** SV is physically a step function and was drawn literally, as
horizontal runs joined by vertical risers. Accurate and unreadable: a generated sweep looked like a
staircase whatever easing produced it, so "did I generate exponential or linear?" could not be
answered from its own output. The points are joined directly instead, so the generating function
shows in its own output: linear reads straight, sin reads as an S, exponential reads as a hockey
stick. Dots mark the real points, so the line is never misread as a claim that SV ramps
continuously between them.

*Revised within the round.* The first version drew a Catmull-Rom spline through the points, on the
argument that a sparse note-placed sweep (five points across two seconds) reads as a polygon
otherwise. Reverted to **straight segments** on owner feedback, and the reasoning holds up better:
a spline invents curvature between points that no stored value justifies, and near a sharp SV change
it overshoots past values the document does not contain. That is an accuracy cost paid for a
smoothness nobody asked for. `catmull_rom_path()` is gone.

Labels are spaced at least `SV_LABEL_MIN_SPACING_PX` apart; "every snap" generation puts hundreds of
points on one screen and labelling each was both a smear and a `drawText` per point per frame.

**Paste snaps to the grid.** `_paste_notes`/`_paste_sv_points` snap the paste anchor through
`snap_time` before applying the stored relative offsets. The anchor was the raw playhead, which is
wherever the audio happens to be and essentially never exactly on a division, so a copied pattern
landed a few milliseconds off every snap it was built on. Offsets stay relative, so the pattern's
internal spacing is unchanged.

**SV lines can be rubber-band selected.** A press on empty space in select mode starts a range drag
(`select_anchor_time`, kept separate from `drag_anchor_time`, which is function mode's range) and
selects every *inherited* point inside it. `_auto_scroll_selection` reuses `TimelineGameplay`'s
acceleration curve so the view scrolls when the drag runs past an edge — without it a selection
could never exceed one screenful, since the view stood still however far the cursor went.

**A left click deselects.** `TimelineGameplay.mousePressEvent` clears the selection at press before
the drag rebuilds it, so a plain click with no drag deselects instead of silently keeping a
selection the user has visibly moved on from. Applied to the Fancy Arranger timeline too, not just
Editor-page views — same widget, same gesture, and its click-to-seek is unaffected.

**Generated SV keeps the kiai it lands in.** Kiai is a property of the active timing point, so a
generated point that doesn't carry it forward silently ends the kiai section it was dropped into —
across the whole range the sweep covers. Both `_generate_sv` and `_add_sv_point` now read
`active_point_at(...).kiai` and pass it to `TimingPoint.inherited_at`. Read **per point**, not once
per sweep: a selected range can cross a kiai boundary in either direction.

**Spinners show their extent.** A grey band from start to end with a cap at each edge. A spinner is
a duration, but only its start carried any visual weight, so where it *ended* was invisible unless
you dragged its edge and watched the ghost. Grey rather than a note colour, so it reads as "this
span is occupied" instead of competing with the don/kat/slider coding.

**"Drumroll" → "Slider", "Denden" → "Spinner".** Labels only: `slider` and `spinner` have been the
stable internal tool ids since M3 and are what the `.osu` type bits mean, so nothing but display
text changed. Japanese keeps the established word and appends the katakana form —
`4. 連打（スライダー）`, `5. 風船（スピナー）` — rather than replacing one with the other.

**The language switch offers to restart, in the new language.** `_prompt_language_restart` installs
the newly chosen catalog *before* building the message box, so someone who has just picked Japanese
is asked in Japanese rather than in the language they are leaving. Qt does not retranslate widgets
that already exist, so the rest of the running UI stays in the old language — which is exactly what
the restart is for. `Restart now` relaunches via `QProcess.startDetached` (before `quit()`, so the
new process is not a child of one about to exit) and falls back to a plain warning if the relaunch
fails; a frozen build is its own executable and takes only the original arguments, a source run
needs the interpreter in front of them.

**The function dialog was rebuilt in two columns.** Every configurable field on the **left**; the
function chooser and its preview on the **right**. The seven functions became a grid of square
toggle buttons (`function_buttons`, one `QButtonGroup`) instead of a drop-down: there are only
seven, choosing one is the decision the preview exists to inform, and a combo box hid six of them
behind a click. `parameters()["function"]` reads `selected_function()` now; `function_combo` is
gone.

**The preview shows the actual sweep, not the easing shape.** `SVFunctionPreview` takes the real
initial and final rates (`set_range`) and plots `rate_at(t)`, labelling both ends. It previously
normalised to 0..1 and always rose left-to-right whatever the rates were — so a 1.10x → 0.90x sweep
was drawn *ascending*, and the one question the preview most needed to answer ("did I get the
direction right?") was the one it could not. A flat sweep has no span to normalise against and is
drawn down the middle rather than dividing by zero. The rate spin boxes drive it live.

**Function mode: prefill, and endpoints that actually land.**

- The dialog opens with initial/final rate set to the SV already in force at each end of the
  selection (`sv_at`), so pressing Generate without changing anything is a no-op rather than a jump
  to an arbitrary 1.0x → 2.0x ramp.
- Progress now runs across the **generated points**, not the dragged range:
  `t = (time - times[0]) / (times[-1] - times[0])`. With note placement the last note is usually
  well before where the drag ended, so normalising by the dragged duration meant the final point
  never reached the requested final rate — the one value someone picking "1.0x to 2.5x" most cares
  about getting exactly. Both endpoints are now exact for every easing function.

`tests/test_editing_ux_round4.py` (68 tests) covers all of the above: green-over-red in both file
orders and one series entry per timestamp; the bound ladder (round values, strict enclosure, finer
steps near zero, the absolute limits); sticky bounds (expansion when a value nears either edge,
contraction only with slack, no oscillation across eight values chosen to sit on and around the
ladder steps, stability across repeated paints, the frozen-scale escape hatch, and that the SV↔y
round trip still holds on a fitted scale); that the smoothing helper is gone; dense-sweep painting;
grid-snapped paste for notes and SV with relative spacing preserved; rubber-band selection
including the inherited-only rule and auto-scroll in both directions; click-to-deselect in both
widget roles with drag-select still working; kiai preservation inside/outside/across a boundary and
for a hand-placed line; the spinner band's grey; the renamed labels and their Japanese; the restart
prompt being built in the new language and not relaunching when declined; the function chooser
(seven square exclusive buttons, no combo, configurables in the left column); the preview plotting
real rates and descending for a falling sweep across every easing function; and endpoint exactness
for every easing function plus the single-point case.

### Fifth owner feedback round, after using both editors (2026-08-09)

- **One wheel notch = one snap division; Shift+wheel = one whole beat**, in the chart views and the
  SV editor alike (both previously moved four). Two separate bugs behind "it moves way further than
  that": the literal `× 4` multipliers, and — the real one — `for _ in range(abs(steps))`, which
  turned a single physical click into several movements whenever `angleDelta` exceeded the 40.0
  accumulator threshold, which it does on ordinary hardware (a notch reports 120, more under
  Windows' lines-per-notch setting or a high-resolution wheel). One wheelEvent that crosses the
  threshold now produces exactly one movement and consumes the whole accumulated amount.
  `wheel_seek_time()` is the shared step, extracted when M6 added the third caller.
- **A spinner's start and end caps stay drawn once its start scrolls off-screen.** `paintEvent`'s
  visible-note slice started at `start_time`, so a long spinner vanished entirely the moment its
  start left the window, taking the end cap with it. `refresh_notes` tracks `_max_extend_ms` (the
  longest slider/spinner duration) and the slice starts that much earlier.
- **Shift+click places a big (finisher) don or kat**, matching the Shift-at-release rule sliders and
  spinners already had; `note_place_requested` carries the flag and the hover ghost previews the
  larger radius.
- **A green or red line can be dragged horizontally to retime it**, snapped to the grid, and
  **which axis a drag moves is decided at press time by how close the click landed to the point's
  own value dot** (`SV_DOT_HIT_RADIUS_PX`, `_drag_axis_for_click`). Within the radius the drag
  adjusts SV; anywhere else along the line it retimes. An uninherited point has no dot, so it is
  always a retime. Deciding once per gesture rather than moving both axes together is also what
  keeps undo coalescing: `History.push(..., allow_merge=True)` only merges with the stack's topmost
  command, so a drag that alternated between a retime command and an SV command would push a new
  undo step per mouse-move tick.
- **The SV graph's axis floor is fixed at 0.1x and only the ceiling autoscales**, from a default of
  2.5x. Anything slower is drawn on the ground rather than dragging the whole axis down — one 0.05x
  stop used to squash a map's entire ordinary SV range into the top of the graph. Contraction never
  goes below the 2.5x default, so the axis stops moving on any map that stays inside it. This
  replaces the fourth round's symmetric two-bound ladder; `sv_bound_below` survives as the ladder's
  other half and is still tested, but nothing in the axis calls it now.
- **Points two or more beats apart connect with a step, not a diagonal.** SV holds its value until
  the next green line, so a long flat stretch followed by a jump was being drawn as a slow ramp that
  the document does not contain. Closer than two beats still connects directly, which is what makes
  a generated sweep's easing readable (the fourth round's reason for the diagonal in the first
  place).

### Sixth owner feedback round: gimmick maps froze the editor (2026-08-09)

Opening a real barline-gimmick map — thousands of timing points, many of them uninherited at an
absurd BPM to draw visual barlines — hung the window. Four independent causes, all of the same
shape: work proportional to the *map* being done per frame, or per tick, instead of per edit.

- **`TimelineGameplay._draw_snap_grid` walked one tick per snap division.** An "infinite BPM"
  point (`beatLength = 0.0001`) makes a 1/4 division 25 nanoseconds wide, so one 2-second window
  asked for ~10⁸ ticks, each with its own timing lookup. Divisions finer than a pixel are invisible
  anyway; those sections are now skipped whole, jumping to the next timing point.
- **`DensityOverview.load_document` walked one window per 4 beats** to the end of the section. With
  the same points and a section running to the end of the song that is hundreds of millions of
  tuples — a hang on *open*, before anything was drawn. Windows are now never finer than
  `duration / DENSITY_MAX_WINDOWS`.
- **The timing lookups in `osu_io.timing` were linear scans, and two of them rebuilt a list per
  call.** `active_point_at` scanned; `active_uninherited_at` built a filtered copy of every point;
  `sv_at` **re-sorted the whole list** — and it is called once per slider per frame. All three are
  binary searches over the (already sorted) input now, allocating nothing.
- **Per-frame rebuilds in the paint paths.** `SVEditorView` recomputed its per-timestamp series and
  line colours from every point on every paint, then filtered the series three more times;
  `TimingOverviewBar` regrouped every marker per paint. Both are cached per edit now
  (`_rebuild_caches`, `_marker_kinds`), sliced by bisect, and collapsed to one line per pixel
  column — a gimmick map puts thousands of markers on the same x.

On a synthetic 14,000-point gimmick map every view now paints in single-digit milliseconds, from
"never returns".

**The SV graph's value flickered while scrolling** for the same underlying reason: the axis ceiling
was refitted to the *visible window* every frame, so it moved every time a fast point crossed the
view edge. The sticky-bound machinery (`SV_BOUND_NEAR`/`SV_BOUND_SLACK`) was damping a symptom. The
ceiling is now a property of the **document**, computed in `_rebuild_caches`: `SV_VISUAL_MAX` unless
the map exceeds it, in which case the enclosing round value. It cannot move while scrolling at all.

### Seventh owner feedback round: extreme BPM, missing barlines, kiai flash (2026-08-10)

- **The whole frame went blank on extreme timing.** `_draw_snap_grid` handed `QPainter.drawLine`
  an unclamped `x`; one section slow enough (`beatLength` ≥ ~2·10¹⁰) puts the tick after the
  visible one billions of pixels out, and `drawLine` takes a C int. The `OverflowError` was raised
  *inside* `paintEvent`, where Qt swallows it and leaves the frame half-painted — so the report was
  "all the notes disappear", not "it crashed". `x` is clamped to just off either edge now, and the
  starting tick's `snap_length` is floored by the same `min_snap_length` the loop already used
  (a near-zero beat length made `(start_time − point.time) // snap_length` overflow to infinity,
  and `int(inf)` raises).
- **Barlines went missing past ~60000 BPM.** `barline_times` walked each section from `start_ms`,
  which is sized for the map's *slowest* section — a section that crosses the whole range in a
  fraction of a millisecond spent its entire per-frame line budget off the left edge and drew
  nothing. Worse, thousands of such sections exhausted the global 600-line cap before the one on
  screen was reached. Each section is now walked only across the slice of time it is on screen for
  **at its own velocity**, so a dense section renders as the intended static picket fence
  (`( | | | | | )`) instead of vanishing. `barline_times` is therefore frame-relative: it answers
  for the playhead at `current_time`.
- **Kiai flash.** Notes in a kiai section brighten on every 1/1 beat and fade out across it,
  drumroll-yellow (`KIAI_PULSE_COLOR`) — the shine is that yellow showing through. Gameplay viewer
  only; the chart editor stays flat so the flash cannot fight selection colours. Gated on the
  *playhead* being in kiai as well as the note, so a section still approaching is drawn plain.
  Stacked objects compound because the flash is a second pass over the visible notes: a fake slider
  dropped on a note paints its own overlay, and nothing has to count the stack. Sections under
  `KIAI_PULSE_MIN_BEAT_MS` do not flash at all — a gimmick "beat" would strobe once per frame.
- **Real drumrolls render under everything**, barlines included (`bodies` / `foreground` split in the
  viewer's paint). A body is a band seconds wide; drawn in with the notes it covered every barline
  and every fake slider stacked on it. A fake slider has no body, so it stays with the notes.
- **Rendering is 3-5x faster on a dense map.** Measured on 8000 notes + 2000 SV points, 1600x300,
  per frame: gameplay viewer 4.14ms -> 1.42ms, chart view 2.94ms -> 0.58ms, SV editor 2.36ms ->
  1.14ms. Three causes:
  - Note circles are blitted from a **sprite cache** (`draw_note_sprite`) instead of stroking an
    antialiased ellipse per note per frame -- ~70us against ~2us each, and notes are identical
    circles in a handful of colours and two sizes.
  - Slider/spinner **end times are cached per edit** (`_end_times`), not recomputed per note per
    frame at two binary searches each.
  - `active_uninherited_at` is asked against the **uninherited points only** (`beat_points` /
    `_beat_points`) in all three views. Given the full list it steps back over inherited points one
    at a time, and this app's whole subject matter is maps with thousands of them between two red
    lines: it was 680k property reads per 60 frames in the SV editor alone.
- **Add view defaults to the difficulty being edited**, not whichever sorts first.
- **Tool buttons are one width** across both rows (`equalize_button_widths`, run from `showEvent`
  so the measurement happens after the style is final). The rows swap into the same spot, so
  per-label widths re-flowed the row on every switch.
- **The SV function chooser tiles are the preview.** Each square tile draws its own curve for the
  rates actually typed, so all seven can be compared at once; the separate preview widget is gone
  from the layout and now only renders the tiles.

### Startup flow and song library (implemented 2026-08-10)

Before this, the app opened on an empty editor and the only way in was `Open .osu` plus a file
dialog — the user had to know where their maps live and which file was taiko. The flow is now
**language → Songs folder → scan → song → difficulty → editor**, and the editor is something you
enter and leave rather than the whole application.

**Language first, once.** `main()` shows `LanguageDialog` when `setup/completed` is unset,
*before* `install_translator` and before `MainWindow` exists — Qt does not retranslate
widgets that already exist, which is the same constraint the Settings dialog's restart prompt
works around. The dialog is deliberately untranslated: it is the one screen that cannot know
which language the reader wants, so it says "Choose your language / 言語を選んでください" and
offers `English` and `日本語` literally. Nothing else is on it, so nothing else needs translating.

**Why `setup/completed` and not `language/current`.** The first version keyed the language screen
off `language/current` being unset — and it never appeared for anyone who had used the app before,
because the Settings dialog writes a language on *every* Apply, so an existing install already had
one stored. The same went for the folder prompt once a folder had been picked by an earlier build.
`setup/completed` is a flag with one meaning, set only after a songs folder has actually been
chosen: existing installs get the flow exactly once, and cancelling the picker leaves it unset so
the next launch offers it again instead of dropping the user on an empty library.

**Songs folder, remembered.** `library/songs_folder` in `QSettings`. `MainWindow.start_library()`
(called from `main()`, *not* `__init__`, so constructing a window in a test never walks someone's
disk) either scans the stored folder or — on a first start — explains what the picker is for
("Please select your osu! Songs folder", plus the note that the check only takes a while once) and
then opens it, pre-filled with `%LOCALAPPDATA%/osu!/Songs` when that exists. That message is built
after `install_translator`, so it is already in the language just chosen. Cancelling leaves the
library page with its "No songs folder selected yet." state and a `Change folder` button — no dead
end.

**`song_library.py`** — a leaf module with no Qt import, following the same rule as `osu_io` and
`model`:

- `read_header()` reads only the five fields the browser needs and stops at the first section that
  cannot contain them (`[Difficulty]`/`[Events]`/`[TimingPoints]`/`[Colours]`/`[HitObjects]`).
  `parse_osu` is deliberately **not** reused: it parses every hit object of every file, and a real
  Songs folder is tens of thousands of files. Decoding is lenient because these strings are only
  displayed; the load path still goes through `parse_osu` with its proper cp1252 fallback.
- `scan()` is a **generator**, one item per `.osu` file (the taiko ones, `None` for the rest), so
  the caller decides how much runs per frame. No threads: nothing here touches a widget, and a
  generator cannot race with the paint loop the way a worker would.
- The cache is JSON (`AppDataLocation/song_index.json`), keyed by path, validated by
  `st_mtime_ns` + `st_size`. **Non-taiko files are cached too** — they are the overwhelming
  majority of a Songs folder, and not re-reading them is where the second run's speed comes from.
  Entries for vanished files are dropped when the walk completes.
- `songs_from_cache()` rebuilds the previous scan's taiko entries **without touching the disk**, so
  every start after the first has the full list on screen before the verification walk begins.
  `_start_scan` shows that, then walks: anything new is appended to the visible list as it is
  found, and at the end the walk's own result replaces the cached one, which is where songs deleted
  since the last run disappear. Entries are filtered by the current root, since the cache outlives
  a change of songs folder.

**The loading UI does not block.** `_scan_step` runs on a `QTimer(0)` and consumes the generator
until `SCAN_SLICE_SECONDS` (8ms, one frame) has passed, then returns to the event loop. The song
list is rebuilt every 200 *found songs* rather than every tick, because redrawing thousands of rows
costs far more than a scan slice. The progress bar is indeterminate on purpose: the walk discovers
files as it goes, so there is no honest total to count towards until it has already finished.

**Browse, then edit.** Song list (folder = song, `Artist - Title · mapper · difficulty count`) on
the left, difficulties of the selected song on the right. Double-click a difficulty, or press
`Edit this difficulty`, and `_load_map_path` runs exactly as it always has — the library is a way
to choose a path, not a second loading path. `_load_map_path` follows the load to the Editor page
**only when the current page is the library**, so opening from the browser lands you in the editor
while switching difficulty from the Fancy Arranger page leaves you where you were.

**Filtering, grouping and language.** A second toolbar row holds `Group by` (nothing / mapper /
artist), `Sort` (A→Z / Z→A) and an `Original language metadata` checkbox; all three only change how
the same index is displayed, so none of them rescans. Grouping inserts bold `Qt.NoItemFlags` rows
as dividers — `_song_selected` treats an item with no `Qt.UserRole` path as a header and returns.
Z→A reverses the group order too, which is what "sort by artist, Z to A" has to mean once the list
is grouped by artist. The search box matches `TaikoDifficulty.search_text()`: artist, title, both
Unicode variants, difficulty name, creator and tags in one lowercased string, so one box covers
everything without a field selector.

**Original-language metadata** is a display toggle over the fields osu! already stores:
`ArtistUnicode`/`TitleUnicode` (the song's own script) versus `Artist`/`Title` (romanized). Either
side can be empty in a real map, so `display_artist`/`display_title` fall back to the other rather
than rendering a blank where a name belongs. Persisted as `library/original_metadata` and applied
instantly, since it is a way of looking at the list rather than a startup preference.

**Esc leaves the editor.** `MainWindow.keyPressEvent` handles it, which means it only fires when
the focused view did not want the key first. Both editor views used to swallow `Escape`
unconditionally; they now consume it only when it actually cleared a selection and call
`super().keyPressEvent` otherwise, so an `Escape` with nothing selected propagates to the window.
That is the whole mechanism — no application-context shortcut, which would have taken the key away
from the views entirely (the same hazard already documented for `Ctrl+C`).

**The unsaved-changes prompt.** `_confirm_leaving_editor` is shared by Esc, the `Songs` tab and
`closeEvent`, so there is one question with one answer set no matter which door you use:

- It lists **which difficulties** are unsaved by name (`self._states` filtered on `history.dirty`),
  because with several difficulties open "you have unsaved changes" does not say what is at stake.
- Three buttons: **Save** (runs `save_all_states`, the same batched write the header's Save does),
  **Continue without saving**, **Cancel**. "Continue without saving" is worded that way rather than
  "Discard" on purpose — nothing is written *and* nothing is thrown away: the `DifficultyState`
  stays in `self._states` with its history, so reopening the difficulty in the same session finds
  the edits still there. A "Discard" that silently kept the edits would be a lie.
- `closeEvent` gates on `event.spontaneous()`: a prompt belongs to someone closing the window (X,
  Alt+F4), not to a programmatic `close()` — which is what every test teardown does, and a modal
  prompt there has nobody to answer it.

**Leaving closes the views.** Either answer that continues (`_leave_editor`) runs
`_close_all_editor_views` before the page switches, so the Editor page is empty when you come back
to the song list. Reopening a difficulty then gets a fresh default chart + SV pair, because
`_activate_state` keys that off `_editor_view_groups`, which `_close_editor_view` prunes. Without
this the stack accumulated every view of every song visited in the session, and the tenth song
opened below nine songs' worth of scrollback. The `DifficultyState`s themselves are kept — the
views are the session, the edits are not.

### Shortcuts, fonts and the header, same round

- **Every real shortcut is configurable now.** `SHORTCUT_DEFINITIONS` grew from three entries to
  thirteen: play/pause, undo, redo, copy, paste, save-all (`Ctrl+S`, which had no binding at all
  before), back-to-songs, and the six tool digits. The Settings dialog's table is generated from
  that tuple, so it filled itself in; `_reload_shortcuts` now re-keys every `QShortcut` from the
  registry instead of just the first three. Keys handled inside a view's own `keyPressEvent`
  (Delete, Ctrl+A) stay out of the list deliberately: they are scoped to the focused view, and
  listing them would promise a rebind the widget would ignore.
- **`back_to_songs` is the one entry with no `QShortcut`.** `MainWindow.keyPressEvent` compares the
  pressed combination against the stored sequence instead, because registering it would take
  Escape away from the views entirely — the same consume-the-key hazard documented for `Ctrl+C`.
  Rebinding it still works; it is just resolved at press time.
- **The digit shortcuts stopped eating typing.** `1`–`6` are `ApplicationShortcut`s, which
  *consume* their key, so `should_ignore_shortcut_focus` (which can only decline to act) never
  stopped them swallowing a digit typed into a spin box — and the library's search box made that
  reachable in one keystroke. `_editor_view_focus_changed` now disables the six shortcuts outright
  while a text widget holds focus, which is the only way the key reaches the widget. Same hazard
  the plan already recorded for `Ctrl+C`; this is the half that could be fixed without giving up
  the global binding.
- **UI font is the system font +2pt**, set on the `QApplication` rather than as a stylesheet pixel
  size, so dialogs and DPI scaling follow it. A pixel-defined system font (`pointSizeF() == -1`)
  gets +3px instead, since adding to −1 would have produced a 1pt UI.
- **The widths that font broke are now derived, not typed.** The playback rows had
  `setFixedWidth(42)`/`(52)` and the Fancy Arranger time readout `setFixedWidth(190)`; a bigger
  font simply overflowed them. Both playback rows go through `equalize_button_widths` (which
  measures the polished labels) **in `showEvent`** — the window stylesheet, padding included, is
  applied after the pages are built, so a button measured at build time reports a width its padded
  self does not fit in. The time readout takes `fontMetrics().horizontalAdvance()` of its longest
  possible value. Button padding went 11px → 16px and tab padding 13px → 18px.
- **Settings' shortcut table sizes its own columns.** Action and Category are
  `QHeaderView.ResizeToContents`, since their contents are translations and no fixed width can be
  right in both languages; the key-editor column keeps the stretch.
- **Group headers in the song list are bold accent pink**, with `Qt.ItemIsEnabled` alone rather
  than `Qt.NoItemFlags`: unselectable either way, but a disabled item paints in the disabled
  palette, which greys out the accent colour the header exists to carry.
- **The header status line stopped eliding early.** It had `setMaximumWidth(460)` with the layout's
  stretch after it, so ordinary messages became "..." while empty space sat to their right. It now
  takes the stretch itself and only elides when the header really runs out of room (the full text
  has always been its tooltip).

`tests/test_song_library.py` (26 tests) covers the header reader including mapper/tags/Unicode
metadata, the taiko-only filter, the cache (unchanged files not re-read, edited files re-read,
deleted files purged, version guard), grouping by folder, the two lists, search across all five
fields, grouping and A→Z/Z→A ordering, the original-language toggle, opening into the editor, Esc
with and without a selection in the way, view teardown on the way back, all three answers to the
unsaved-changes prompt, the index-first second start (no file reopened before the list is
complete) and its deletion pass, group-header styling, and that every `SHORTCUT_DEFINITIONS` entry
is wired to a live `QShortcut` (`back_to_songs` excepted) and honours a rebind.

It also points `QSettings` at a test-only application name for the duration of the module
(`setUpModule`/`tearDownModule`), because these tests build real `MainWindow`s that read and write
preferences — without it a test toggling the original-language checkbox switched it on in the
running user's own settings, which is exactly what happened once before this was added.

**i18n:** `song_library.py` has no user-visible strings, so `test_i18n.py`'s `SOURCE_FILES` did not
need widening, and `LanguageDialog`'s literals introduce no new context. The new `MainWindow`
strings are in the catalog and compiled. The shortcut labels and categories went into the
`SettingsDialog` context, where **none** of them had ever been — they reach Qt through
`self.tr(definition.label)`, a non-literal the coverage test cannot see, so the shortcut table had
been silently English in Japanese mode since it was built.

### M5 — Gimmick editor

Revised 2026-08-08: barline / reverse barline / slider gimmick are no longer three separate tools.
Bottom tool-palette row has just two:

- Tool `7` = **fake slider**, placed literally as `256,192,<time>,2,12,L|624:192,643,-0.0001`.
  Direct placement, unchanged from the original design.
- Tool `8` = **Gimmick Effect**, working like SV's function tool: drag-select a start/end time
  range, then a popup (same interaction pattern as the SV function popup) lists the effects to
  apply within that range:
  - **Barline** — per don note one uninherited point at `t−2` and `t+2`; per kat note at
    `t±2, t±4, t±6`. Optional **invisible note** toggle adds an extreme-BPM uninherited point at
    the note's exact time (default `beatLength = 0.0001`, exposed as a parameter).
  - **Reverse barline** — same machinery inverted: barlines fill the range and gaps are left at
    note positions, wider gap = kat, thinner = don.
  - **Slider gimmick** — small slider for don, big slider for kat; fake, visual only.

Further transformations deferred by the owner.

Revised 2026-08-10 (owner): the gimmick editor is **three lanes in one view**, not one chart
lane with extra tools:

1. **Fake slider lane** — place fake sliders directly, Shift+place for a big one. Stacking them
   on a chart note is how a note is made to read brighter (see the kiai flash below).
2. **Chart lane** — the actual notes, as the chart view shows them today.
3. **Barline lane** — where the barline-gimmick tools and visualizations are placed. More
   effects to be added here later.

**Revised again 2026-08-10 (owner): the gimmick editor gets its own page, not a view type inside
the Editor page.** It becomes a fourth tab in the global header — `Songs | Editor | Gimmick |
Fancy Arranger` — alongside the song library, the note/SV editor and the arranger, instead of
being opened through the Editor page's `+` dialog. Consequences to carry into M5:

- Drop `gimmick` from `AddViewDialog`'s view types and retire the chrome-only placeholder in
  `_add_editor_view`; the three lanes above are the page's own layout, not stacked `EditorViewFrame`s.
- The page needs its own difficulty selector (or it follows the Editor page's active difficulty —
  decide before building), its own tool row, and its own entry in the `_render_gameplay_frame`
  clock broadcast and the per-difficulty zoom family, exactly like the Editor page's views.
- Tool digits `7`/`8` stop overlapping the note/SV rows the moment the rows live on different
  pages, so `_activate_tool_digit`'s "whichever row is visible" routing gains a third branch
  rather than a third set of digits.
- `PAGE_LIBRARY / PAGE_EDITOR / PAGE_FANCY` in `gui.py` gain a `PAGE_GIMMICK`; the page indices
  are already named constants, so inserting one is a one-line change plus its tab button.

### M6 — Gameplay viewer (implemented 2026-08-09)

Renders like real osu!taiko: chart scrolls at SV speed, barline every 4 whole beats by default,
plus a barline at every uninherited point.

`GameplayViewerView` is the first view whose x axis is **not** time. Chart and SV views plot on a
time axis, where an SV change is invisible by construction — spacing there only ever reflects the
clock. Here each object sits where its own scroll speed puts it, so a 2.0x section really is twice
as spread out and a generated sweep can be judged without exporting and opening osu!.

- **Each object approaches at the speed in force at its own time**, `px_per_beat × SV /
  beatLength`. This is the osu!taiko rule, corrected on owner feedback from a first version that
  integrated one shared scroll position across the whole chart: that is osu!mania's model, in which
  objects can never pass each other. A green line moves everything after it, *including a note
  sitting on the line itself* — which is also why function mode's −5 ms default offset is a
  fail-safe rather than a nicety: points authored inside osu! land exactly on the snap, so the
  offset guarantees the SV is already in force when the note it governs arrives.
- osu!taiko's scroll speed is proportional to BPM × SV, so one beat covers the same distance at
  every BPM — which is what makes `GAMEPLAY_PX_PER_BEAT` a constant rather than a function of beat
  length. `_rebuild_velocities` precomputes beats-per-ms per timing-point timestamp, so
  `velocity_at` is a binary search rather than a walk. Velocity is in **beats**, not pixels, so
  Ctrl+wheel scroll speed is a pure render-time scale and costs a repaint instead of a rebuild.
- A slider or spinner **body travels with its head**: one object at one speed, not a series of
  points each reading its own timing.
- `visible_time_range` is bounded by the *slowest* velocity in the map (so it never misses an
  object) and clamped by `GAMEPLAY_MAX_LOOKAHEAD_MS`; objects a fast section throws well off either
  edge are culled per note during paint.
- **Coincident points resolve the same way `sv_series` does**: an inherited point sharing a
  timestamp with an uninherited one wins over the 1.0x reset, since a green line stacked on a red
  one is exactly how mappers set the speed a new BPM section starts at.
- **Barlines** come from `barline_times(start, end)`: one at each uninherited point plus one every
  `meter` beats until the next one, jumping straight to the first line in range rather than walking
  a minutes-long section from its start. A point with **omit-first-barline** contributes its later
  measure lines but not the one on the point itself — that is the flag's whole purpose, and M5's
  barline gimmick sets it in bulk. Each section contributes at most a screen's worth of lines, and
  the frame at most `GAMEPLAY_MAX_BARLINES`: an absurd-BPM point puts a measure every 0.0004 ms, and
  the time range alone asks for tens of millions of them.
- **Read-only.** No tools, no selection, no editing signals; the wheel seeks the shared playhead
  and Ctrl+wheel changes the base scroll speed, a property of the preview and never of the map.
  It rides `_render_gameplay_frame`/`seek_audio` with the other views and joins the Editor page's
  global snap (its wheel needs a divisor), but **not** the per-difficulty `window_ms` zoom family:
  it scrolls by distance, so there is no shared time window for it to be in.
- Both refresh paths reach it: `_refresh_difficulty_views` (a note moved) *and*
  `_refresh_difficulty_sv_views` (a timing point moved every note after it).

`wheel_seek_time()` was extracted while wiring the third wheel handler — the one piece of
`TimeAxisWidget`'s job that had already been copied twice. The rest of that base class is still
unextracted and still owed to M5; this view didn't need it, since a distance axis shares no
mechanics with a time axis beyond the wheel step.

`tests/test_gameplay_viewer.py` (26 tests) pins velocities against the full_v14 fixture's timing
(120 BPM → 0.75x → 2.0x → 150 BPM), the per-object rule in both directions (a green line moves the
note sitting on it, and leaves earlier notes alone), scroll speed being scale-only, the
zero-beat-length guard, every barline rule including omit-first-barline and the gimmick-section
cap, a paint smoke test for each note shape, the capped visible range on a near-stopped 0.01x
section, wheel/Shift+wheel/Ctrl+wheel, and the MainWindow wiring (clock, both refresh paths, global
snap, clean unregistration on close).

---

## Correctness risks

| # | Risk | Location | Severity |
|---|---|---|---|
| R2 | Parser truncates past `fields[5]`; sliders lose curve/slides/length, spinners lose `endTime`. Masked today, becomes silent destruction the moment `[HitObjects]` is regenerated | `osu_io/parser.py:23` | **High (latent)** |
| R1 | ~~`kiai_ranges` reads `bool(int(fields[7]))` instead of masking bit 0, so `effects=8` (omit-first-barline) reads as kiai-on~~ **Already fixed by M1's consolidation, re-verified 2026-08-08:** `gui.py`'s `kiai_ranges` is a one-line delegate to `osu_io.timing.kiai_spans`, which masks `EFFECT_KIAI`. The table entry was stale, not the code | `gui.py` `kiai_ranges` | done |
| R11 | ~~`sorted(self.selected)` is time-ordered only by the accident that `original_index` follows file order. Once insertion exists this silently scrambles every pattern~~ **Fixed 2026-08-08 (third feedback round):** `_calculate_selected_transform` now uses `DifficultyState.notes_in_time_order()`. Went live rather than staying hypothetical the moment inserted/pasted notes started getting the next free key instead of all colliding on `0` | `gui.py` `_calculate_selected_transform` | done |
| R3 | `is_kat`/`is_finisher` test hitsound bits unconditionally, so drumrolls and spinners render and drag as don/kat circles | `model/hit_object.py:10-15` | Medium |
| R4 | Flat `shift` assumes `[Difficulty]` precedes `[HitObjects]`; violation rewrites fields of unrelated lines, undetectable by the count-only assertion | `osu_io/writer.py:59` | Medium |
| R5 | `set_document_background` appends `f"{ending}[Events]{ending}"` as a **single list element** containing two newlines, so every later index repair is off by one; also places `[Events]` after `[HitObjects]` | `gui.py:292` | Medium |
| R6 | ~~`extract_timing_points` requires ≥7 fields; v4 maps write 2 → zero timing points → silent fabricated 120 BPM~~ **Already fixed by M1 in `gui.py`, re-verified 2026-08-08.** One remnant survives: `transformer.py:333` `_timing_point_values`' string branch still guards `len(fields) < 7`. Dead today (`gui.py` passes real `TimingPoint` objects, taking the attribute branch); delete it before M5 gives it a caller | `transformer.py:333` | Low (dead code) |
| R7/R8 | `.bak` taken before validation; no `fsync` on the overwrite-source branch | `osu_io/writer.py:64`, `:68` | Low |
| R10 | Undo stacks unbounded; `MAX_UNDO_STATES=50` in `security_utils.py` is imported nowhere. Multiplies by open difficulties | `security_utils.py:9` | Low |

---

## Working rules

Carried over from the project context transfer, and reinforced by what went wrong during
localization:

- **No patch chains.** Commit reviewed source directly; never re-run a source-mutating script.
- **Preserve working behavior.** Do not replace broad sections of working code for a narrow fix.
- **Stable internal identifiers stay untranslated**; visible labels are translated at display time.
- **Every step ends with the full test suite green.**
- Add a regression test for anything that has already broken once.

### i18n gate

`tests/test_i18n.py` hardcodes `SOURCE_FILES = ("gui.py", "settings_dialog.py",
"image_trace_dialog.py")`. **New UI modules must be added there or their strings escape coverage
silently.** `test_contexts_are_limited_to_real_runtime_owners` caps contexts to six; new dialog
classes need that allow-list widened. Every new `tr()` call needs a catalog entry plus
`tools/compile_translations.bat`.

### Verification

```
QT_QPA_PLATFORM=offscreen python -m unittest discover -v
```

Per milestone, manually: open a real map, switch difficulty and confirm edits and undo survive,
then export and reopen in the osu! editor to confirm timing points, SV, barlines and hitsounds are
intact and the storyboard, breaks and colours are untouched.
