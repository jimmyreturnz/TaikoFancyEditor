# Review: mapper, security, performance

Written 2026-09-04 against the working tree at that date (v3.2.2 + the session's
uncommitted changes). Three passes over the same codebase, wearing a different
hat each time.

Everything below was checked against the code and, where a number appears,
measured. Claims I could not verify are marked **unverified** rather than
padded out. Nothing in the repo was changed to produce this report; the scripts
used are in the scratchpad, and the reproduction steps are in the appendix.

Reference map for every measurement:
`Hiro Shinosawa (CV Reina Kawamura) - mekurume (_gt) [eclosion]` — 1681 hit
objects, 9070 timing points, SliderMultiplier 2.8. A second, ordinary map is
used as the contrast case where it matters.

---

# Part 1 — As a taiko mapper

## 1.1 Bugs, confirmed

### B1. A manual drag in the Fancy Arranger is silently discarded (the one you hit)

**Severity: high.** This is the "I move the objects, then hit transform and it
goes back" report, and it has two independent causes.

**Cause A — no transformation selected.** `_apply_preview_offsets`
(`gui.py:13311`) only applies the drag offset to notes that the transformation
returned a position for:

```python
for index in self.selected:
    ...
    if index in output:            # <- output is the transform's result
        output[index] = (x + dx, y + dy)
```

With the transformation combo on its blank default entry,
`_calculate_selected_transform()` returns `{}`, so `output` is empty, so the
offset is applied to nothing. `update_preview` (`gui.py:13397`) rebuilds
`preview_positions` from `applied_positions` first, so the drag is wiped on the
next preview. `apply_selection` (`gui.py:13433`) uses the same function, so
Apply commits nothing at all.

Measured, 6 notes dragged by (40, 20) with no transformation chosen:

```
preview after transform:   [(256,192), (256,192), (256,192), ...]
preview after drag:        [(296,212), (296,212), (296,212), ...]
preview after re-preview:  [(256,192), (256,192), (256,192), ...]   <- reverted
```

With a transformation selected (`horizontal`) the same sequence keeps the drag
and Apply commits it correctly. So the bug bites exactly when you are using the
canvas as a free-hand mover rather than as a transform preview — which is the
natural thing to do.

**Cause B — any selection change zeroes the offsets.** `_selection_changed`
(`gui.py:13239`):

```python
if new_selection != self.selected:
    self.preview_offsets = {"all": [0.0, 0.0], "don": [0.0, 0.0], "kat": [0.0, 0.0]}
```

Measured: drag → change selection → change back → Apply. The drag is gone even
with a transformation selected.

**Underlying design issue.** A manual move is not document state. It lives as a
single per-group `dx/dy` (`preview_offsets`) that is re-derived on every
preview and reset on several ordinary actions. Two consequences worth deciding
on deliberately:

- you cannot move one note independently — the offset is per group
  (`all`/`don`/`kat`), so dragging one note moves everything selected in it;
- `_canvas_dragged` also writes the same delta into the transformation's
  `center_x`/`center_y` controls (`_sync_center_controls_after_drag`,
  `gui.py:13352`). Two channels carrying one gesture. I did not find a case
  where they double-apply, but it is the shape of bug that shows up later.

**Suggested fix**, in order of size: make `_apply_preview_offsets` start from
`applied_positions` for selected notes the transform did not place (fixes A);
stop resetting the offsets when the selection changes, or fold them into
`applied_positions` first (fixes B); longer term, give manual moves their own
per-note delta so they are not the transform's passenger.

### B2. Snap divisor does nothing on a gimmicked difficulty in the Editor page

**Severity: high for gimmick work.** This is the answer to "do you feel like
snaps is off" — yes, and it is not a feel problem, it is arithmetic.

`TimeAxisMixin.snap_points` (`time_axis.py:151`) is `self.base_timing or
self.timing_points`. `set_base_timing` is only ever called for gimmick-page
views (`gui.py:9456`, `10335`, `11462`). On the Editor page there is no base
timing, so the snap grid is built from the difficulty's own red lines — every
gimmick line included.

Measured on `mekurume [eclosion]`, using the exact list the Editor page would:

| cursor | governing red line | 1/4 | 1/8 | 1/16 |
| --- | --- | --- | --- | --- |
| 48 990 ms | 48 985 ms, **60000 BPM** (beat 1.0 ms) | 48 986 | 48 986 | 48 986 |
| 113 050 ms | 113 041 ms, **26.7 BPM** (beat 2250 ms) | 113 041 | 113 041 | 113 041 |
| 120 004 ms | 120 000 ms, 26.7 BPM | 120 000 | 120 000 | 120 000 |

At 48 990 the governing line is a 60000 BPM invisible-note line, so a 1/4
division is 0.25 ms — every millisecond is on the grid and the divisor is
meaningless. At 113 050 the governing line is one of the anti-barline wall's
2250 ms lines, so the next division is 562 ms away and the cursor snaps back to
the wall line itself. **1/4, 1/8 and 1/16 give identical answers at all three
positions.**

The gimmick page already solves this with a timing reference, and
`looks_gimmicked()` already exists to detect the situation. The Editor page has
neither the reference nor the warning.

**Suggested fix:** let the Editor page take a timing reference the same way the
gimmick page does (`set_base_timing` is already on the view), and warn via
`looks_gimmicked` when a difficulty is opened whose own timing cannot serve as
a grid.

### B3. `Mode:` is never read

`parse_osu` does not parse `Mode`, and `_load_map_path` (`gui.py:13169`) does
not check it. An osu!standard, mania or catch difficulty opens silently and is
rendered as a taiko chart. The song browser filters by mode, but "Open .osu"
and the difficulty combo do not.

Saving such a file rewrites `ApproachRate` and `CircleSize` from the Fancy
page's controls (`gui.py:13904` and friends). Hit objects survive — `extras`
are kept as verbatim strings (`model/hit_object.py`), and `_validate_output`
re-parses and compares — so this is not data loss, but it is an easy way to
quietly alter someone else's difficulty.

**Suggested fix:** parse `Mode` and refuse (or loudly warn) on anything but 1.

### B4. Fake slider classification and drumroll rendering disagree at `+0.001`

Introduced/exposed by today's work, listed so it is not forgotten.
`MainWindow.is_fake_slider` now accepts `length <= 0.001`, so a `+0.001` slider
is a fake slider to the gimmick layers. But `_note_end_time` /
`_compute_end_time` still split on `duration > 0`, so the same object is drawn
as a **real drumroll** (head + `taiko-roll-end` cap) in both the editor
timeline and the gameplay preview, while `-0.001` is drawn as a fake slider
(head + track, no cap).

`+0.001` derives a duration of +0.0027 ms and `-0.001` derives −0.0027 ms. At
that magnitude the sign is a rounding artefact, not something a player can see,
so the two should agree. `mekurume` writes 616 of its 640 decorations as
`+0.001`, so this is the common form, not the exotic one.

### B5. `TypeError` from an equation escapes its own error handling

`_equation` catches `(ValueError, ZeroDivisionError, OverflowError)` around the
sampling loop (`transformer.py`), but a wrong-arity call raises `TypeError`:

```
y=sin(x,x)   -> TypeError: math.sin() takes exactly one argument (2 given)
y=min()      -> TypeError: min expected at least 1 argument, got 0
```

Both propagate out of `transform()`. The preview catches broadly and shows
"Preview error", so the visible result is a confusing message rather than
"sin takes one argument"; `apply_selection` shows it in a warning box. Not a
crash, but the arity check belongs in `_validate_expression_tree` where the
other syntax errors are caught.

## 1.2 Snapping, in full

Beyond B2, the snap machinery is in good shape and I could not fault it:

- `snap_time` snaps relative to the governing uninherited point's own time, not
  to zero — which is what osu! does and what makes a mid-song offset change
  work;
- `osu_round` (`time_axis.py:42`) correctly rounds halves away from zero rather
  than to even, and the docstring explains why;
- `wheel_seek_time` keeps fractional milliseconds through the arithmetic and
  only rounds the result, so scrolling a section does not accumulate drift.

One nit: `snap_time` itself uses Python's built-in `round()` for the *division
index* (`time_axis.py:85`), not `osu_round`. A cursor exactly halfway between
two divisions therefore picks its division by banker's rounding — inconsistent
in direction, though the final millisecond still passes through `osu_round`.
Invisible in practice; fix it for the same reason `osu_round` exists.

## 1.3 What I would want that is not here

Ordered by how often I would reach for it.

**Timing and snapping**
1. **A timing reference on the Editor page** — see B2. Without it the Editor
   page is unusable for the gimmick difficulties this tool exists to make.
2. **Resnap all notes / resnap selection.** No equivalent exists. On a map
   whose offset moves, there is currently no way to fix the chart.
3. **A timing panel.** BPM and offset can only be typed into an existing red
   line's dialog. No "add a red line at the playhead with this BPM", no tap
   tempo, no offset nudge.
4. **Unsnapped-note detection.** Nothing warns that a note is off the grid —
   the one check every taiko mapper runs before submitting.

**Editing**
5. **Undo depth is 50** (`model/history.py:12`). That is a handful of minutes
   of placing notes. osu!'s editor is effectively unbounded. The comment says
   the old stacks held a full position copy per entry — but the commands are
   now deltas, so the cap can probably go up a long way cheaply.
6. **No Select All / Invert Selection / Select to end.**
7. **No nudge-by-one-division** keyboard action. Dragging is the only way to
   move a note in time, and at gimmick zoom that is fiddly.
8. **Bookmarks are read-only.** They are parsed and drawn on the overview bar
   (`gui.py:1059`, `5142`) but there is no way to add, remove or jump to one.
9. **No OD or HP editing.** AR and CS have controls (they matter for the
   std→taiko conversion), but the two values that actually change taiko
   gameplay — OverallDifficulty (hit windows) and HPDrainRate — cannot be
   edited anywhere.
10. **No "copy timing to all difficulties"**, which is the single most common
    multi-difficulty operation in a mapset.

**Playback**
11. **No metronome** and no way to hear only hitsounds.
12. Playback rate presets exist; **no keyboard shortcut** for them.

**Shortcuts**
13. Twelve bound actions in total (`settings.py:110-122`). Missing bindings for
    the snap divisor, save-as/export, delete, select-all, playback rate, and
    bookmark navigation. The rebinding UI is already there; the actions are not.

**Gimmick tooling** — the strongest part of the app, and the obvious next step:
14. An **anti-barline generator**. The recipe is now documented in CLAUDE.md and
    is entirely mechanical: a wall of one-barline red lines on a fixed grid,
    plus a two-line omit-first-barline pair per note, with the slit width
    encoding don/kat. It is `barline_note` in negative and would reuse most of
    that code.

---

# Part 2 — As a security reviewer

Threat model: a desktop app that opens `.osu` files and media from an osu!
Songs folder. Those files arrive from the internet via beatmap downloads, so
they are **untrusted input** even though the user thinks of them as their own.
Plus one network client (the updater).

Overall: better than typical for a hobby project. There is no `eval` of user
input, no `pickle`, no `shell=True`, no subprocess spawned from file content.
The two genuinely dangerous surfaces — the equation evaluator and the file
writer — are both carefully built. The findings below are mostly *unused
defences* rather than exploitable holes.

## S1. `security_utils.py` is dead code — MEDIUM

Nothing imports it except its own test (`tests/test_security.py`). Verified by
grep across the tree. Every limit it defines is therefore unenforced:

| Constant | Intended guard | Actually enforced? |
| --- | --- | --- |
| `MAX_OSU_BYTES` (32 MB) | `.osu` read size | **No** — `parse_osu` uses `read_bytes()` with no cap |
| `MAX_AUDIO_BYTES` (1 GB) | audio file size | **No** |
| `MAX_IMAGE_BYTES` / `MAX_IMAGE_PIXELS` | decompression bombs | **No** — but `image_to_drawing.py` has its own `MAX_DECODED_PIXELS` for the trace path |
| `MAX_AST_NODES` / `MAX_AST_DEPTH` | equation complexity | **No** — but `transformer.py` defines and enforces its own equivalents |
| `MAX_UNDO_STATES` | undo memory | **No** — `model/history.py` defines its own copy and does enforce it |
| `resolve_child_asset` | asset path traversal | **No** — `gui.resolve_song_asset` is a separate, equivalent implementation |

So the file is not a hole so much as a decoy: it looks like the app's security
policy and enforces none of it. Two of its constants have been silently
reimplemented elsewhere (history.py's comment even says so). **Either wire it
up or delete it** — a security module that is not on any path is worse than no
module, because the next reader assumes the limits apply.

The practically missing limits are the size caps: `parse_osu` will read a 2 GB
`.osu` into memory, and `song_library.read_header` iterates lines with no
length cap, so a single enormous line does the same during a library scan.

## S2. Unbounded in-memory audio decode — MEDIUM (availability)

`TrackPlayer._take_buffer` (`audio_engine.py:342`) appends every decoded buffer
to `self._pcm` and a second downmixed copy to `self._mono`, with no duration or
size ceiling. CLAUDE.md records ~50 MB for a 4:43 track; that is roughly
180 KB/s of song, twice over. A one-hour audio file in a beatmap folder is
~1.3 GB of resident memory, and nothing refuses it. `MAX_AUDIO_BYTES` exists
for exactly this and is not used.

## S3. Unvalidated URL handed to the OS — LOW

`updater.py:342`:

```python
self.page_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.page_url)))
```

`page_url` is `payload["html_url"]` straight from the GitHub API response.
`_request` enforces `https://` for every URL the updater *fetches*, but this one
goes to the OS URL handler without that check, so a scheme like `file:` or a
handler-registered custom scheme would be honoured. The delivery path is HTTPS
to api.github.com, so this needs a compromised GitHub account or a broken TLS
chain to matter — but reusing the existing `https://` guard is a one-line fix.

## S4. Update authenticity rests on the same origin as the artifact — LOW

`install_beside` downloads `TaikoFancyArranger-portable.zip`, then downloads
`SHA256SUMS.txt` from the same release, and compares. That detects corruption
and truncation. It does not detect a malicious release, because both files come
from the same place — the checksum is not independently anchored. Detached
signature verification (minisign/GPG with a key baked into the binary) would
close it. Reasonable to accept for a hobby tool; worth knowing you are trusting
GitHub account security completely.

`zipfile.extractall` is used, and the comment claiming it sanitises `..` and
absolute paths is correct for CPython. There is no expanded-size cap, so a
malicious-but-correctly-signed release could fill the disk — which is not a
meaningful escalation over "it could just be malware".

## S5. Things that are done well (do not regress these)

- **The equation sandbox** (`transformer.py:651`) is the real attack surface and
  it is solid: an AST allowlist that rejects attribute access, subscripting,
  comprehensions, lambdas, f-strings and keyword arguments; calls restricted to
  a fixed dict of 16 math functions with a `Name`-only callee; node count,
  depth and source-length caps; an exponent cap and a magnitude cap on every
  intermediate result. I could not find an escape. The only gap is the arity
  `TypeError` in B5, which is a robustness bug, not an escape.
- **`resolve_song_asset`** (`gui.py:179`) rejects absolute paths, rejects any
  `..` component, and re-checks containment *after* `resolve()` — which also
  catches a symlink inside the folder pointing outside it. Correct. Its only
  omission versus the unused `resolve_child_asset` is a size cap.
- **`write_osu`** (`osu_io/writer.py:183`) is the best-engineered part of the
  codebase: it regenerates only the two sections it models and passes every
  other byte through, writes to a temp file in the destination directory,
  fsyncs, **re-parses and validates the result** (object counts, object
  identity, timing point identity, version, and that no section disappeared),
  and only then takes a backup and `os.replace`s. Interrupting a save cannot
  corrupt a map. Do not let anyone "simplify" this.
- HTTPS is enforced on every fetched update URL, and the digest is checked
  before anything is extracted outside the temp directory.

---

# Part 3 — Performance

All numbers from `tools/profile_playback.py` at 1920×1080, offscreen, with the
default editor layout (2 chart views, 1 SV view, 1 density view, 3 timing bars).
The frame budget is 8.33 ms because the editor targets 120 fps.

## 3.1 Measured baseline

| Map | median frame | p95 | p99 | over budget |
| --- | --- | --- | --- | --- |
| `mekurume [eclosion]` (1681 notes, **9070 timing points**) | 8.82 ms | 12.67 | 17.03 | **269/400 = 67%** |
| `Darling Game Over Love [Obsessive Devotion]` (ordinary) | 5.96 ms | 9.61 | 13.51 | 22/300 = 7% |

Per view, median paint:

| View | gimmick map | ordinary map |
| --- | --- | --- |
| `SVEditorView` 1882×180 | **3.15 ms** | 1.31 ms |
| `TimelineGameplay` 1882×180 | 0.86 ms | 0.80 ms |
| `TimingOverviewBar` 1009×28 | 0.11 ms | 0.11 ms |

So: the editor is comfortable on normal maps and misses the 120 fps budget two
frames in three on a gimmick map, and **the SV view is where it goes**. At
60 fps only the p99 overruns, so the visible severity depends on the refresh
rate you actually run.

cProfile over the same 400 frames, hot list:

```
0.580s  QPainter.drawLine        (72,873 calls = 182/frame)
0.302s  SVEditorView.paintEvent  (tottime)
0.185s  draw_note_sprite         (9,060 calls)
0.140s  _draw_sv_curve           (cum 0.669s = 1.67 ms/frame)
0.138s  QPainter.drawText        (12,516 calls = 31/frame)
0.104s  _index_at_or_before      (35,652 calls = 89/frame)
```

## 3.2 Ranked opportunities

### P1. Give `SVEditorView` a scrolling static layer — largest win

It is the only expensive view without one. `TimingOverviewBar` and
`DensityOverview` both cache their static content into a `QPixmap` and repaint
only the playhead; the SV view redraws its curve, its dots, its labels and its
red/green line ticks every frame.

The complication is that the SV view *scrolls*, so a document-keyed pixmap does
not work directly. The standard answer is a tile: render a pixmap covering
±50 % beyond the viewport, blit it at an x offset each frame, and re-render only
when the playhead leaves the tile or the document/zoom changes. On the numbers
above that would take the gimmick-map frame from ~8.8 ms toward ~6 ms and put
the median back inside budget.

Everything else in that paint is already tight (bisect slicing, per-pixel
dedupe, held timing sections) — there is no cheap constant factor left to find,
which is precisely why the caching layer is the answer.

### P2. Stop full-parsing every difficulty to fill a combo box

`gui.py:13185` parses every `.osu` in the folder with `parse_osu` purely to read
`Version:`. `song_library.read_header` already reads exactly those fields and
stops at `[Difficulty]`.

Measured on the mekurume folder: **40.2 ms vs 0.3 ms — 154×**. That is one
difficulty; a mapset with eight gimmick difficulties pays ~320 ms of blocking
parse every time you open a map or switch difficulty with
`refresh_difficulties=True`.

Complexity: unchanged asymptotically, but the constant is the whole file
including 9000 timing points versus the first ~30 lines.

### P3. `_note_near_x` and `_extendable_note_near_edge` scan every note

Both walk `self.notes` in full (`gui.py:1974` and `2012`) on every mouse press.
`self.notes` is time-ordered and `self.note_times` already exists alongside it,
so a `bisect` to the visible window would make these O(log n + k) instead of
O(n). Not currently a measured problem — a press is not a frame — but it is a
five-line change and removes a whole class of future "the editor hitches when I
click on a 20k-note map".

### P4. Rebuilt index dictionaries in the Fancy Arranger

`_apply_preview_offsets` (`gui.py:13313`) and `_calculate_selected_transform`
(`gui.py:13272`, `13290`) each build a dict over **all** hit objects every call.
They are called per preview, not per frame, so this is not hot today — but the
preview timer fires on every control change, and on a 10k-note map that is four
full passes per keystroke in a spin box. Cache them against
`history.revision`, which already exists for exactly this purpose (the preview
cache is keyed on it).

### P5. Nothing to do — recorded so it is not re-investigated

- `_visible_timing_points`, `_draw_sv_curve`, `_draw_snap_grid` and the timing
  bars are all already bisect-sliced and pixel-deduped, with comments naming the
  measurements that drove them.
- `song_library.scan` is mtime+size cached, generator-based, and evicts stale
  entries. Nothing to gain.
- `draw_note_sprite`'s pixmap cache is doing its job: 9060 calls cost 0.185 s
  total, i.e. 20 µs each including the blit.
- The `active_uninherited_at` backward walk was already fixed by
  `snap_beat_points`; `_index_at_or_before` at 89 calls/frame is not a problem.

---

# Appendix — reproducing the measurements

```bash
# Frame profile (add --profile for the hot list)
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tools/profile_playback.py \
  "D:/osu!/Songs/2523678 .../mekurume (_gt) [eclosion].osu" 400

# Snapping table (Part 1, B2)
#   builds the uninherited list the Editor page would use and calls
#   time_axis.snap_time at 1/4, 1/8, 1/16

# Fancy Arranger drag (Part 1, B1)
#   MainWindow -> _load_map_path -> _selection_changed -> update_preview
#   -> _canvas_dragged(40, 20) -> update_preview, comparing preview_positions

# Difficulty combo cost (Part 3, P2)
#   time parse_osu vs song_library.read_header over folder.glob("*.osu")
```

The three throwaway scripts are in this session's scratchpad:
`repro_transform.py`, `repro2.py`, `snapcheck.py`.

---

# Priority order, if it were my call

1. **B2** — snapping on the Editor page. It blocks the app's own core use case.
2. **B1** — the Fancy Arranger discarding drags. Silent data loss in a workflow.
3. **S1** — wire up or delete `security_utils.py`. Half an hour either way.
4. **P1** — the SV view's static layer. The one measured performance problem.
5. **B3** — the `Mode:` guard. Cheap, prevents editing the wrong file entirely.
6. **Feature 14** — the anti-barline generator. The thing this tool is *for*,
   and the recipe is already written down.
