# Taiko Fancy Arranger — Development Plan

Turning the visual arranger into a full osu!taiko editor.

> **Status:** M0 and M1 authorized. M2–M6 recorded for continuity, not started.
> **Current release:** v1.0.3

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

### M3 — Note editing

Tools `1` select, `2` don, `3` kat, `4` slider, `5` spinner, `6` new combo. Left click places,
right click on a note deletes. Requires `[HitObjects]` regeneration from M1.

### M4 — SV editor

Tools `1` select, `2` green line, `3` function. Red lines render vertical with **BPM at bottom +
current SV**; green lines vertical with **SV only**; plus a **horizontal green line acting as an
SV-speed graph**, draggable up/down in select or green-line mode to change that line's speed.

Function mode: drag to select start/end; SV is applied to notes within the range. Parameters —
initial rate, final rate, position offset, omit barline (default **off**), relative to final BPM
(default **on**) — each a toggle with its option beside it. Every function gets a **preview square
rendering 20 fixed dot positions** plus its name, from initial to final rate, as a visual idea
only. Then **Generate**.

Functions: `linear`, `sin in`, `sin out`, `exp1.3`, `exp1.6`, `true exp`, `sin`.

### M5 — Gimmick editor

Tool `7` = fake slider, placed literally as `256,192,<time>,2,12,L|624:192,643,-0.0001`.

- **Barline** — per don note one uninherited point at `t−2` and `t+2`; per kat note at
  `t±2, t±4, t±6`. Optional **invisible note** toggle adds an extreme-BPM uninherited point at the
  note's exact time (default `beatLength = 0.0001`, exposed as a parameter).
- **Reverse barline** — same machinery inverted: barlines fill the range and gaps are left at note
  positions, wider gap = kat, thinner = don.
- **Slider gimmick** — small slider for don, big slider for kat; fake, visual only.

Further transformations deferred by the owner.

### M6 — Gameplay viewer

Renders like real osu!taiko: chart scrolls at SV speed, barline every 4 whole beats by default,
plus a barline at every uninherited point.

---

## Correctness risks

| # | Risk | Location | Severity |
|---|---|---|---|
| R2 | Parser truncates past `fields[5]`; sliders lose curve/slides/length, spinners lose `endTime`. Masked today, becomes silent destruction the moment `[HitObjects]` is regenerated | `osu_io/parser.py:23` | **High (latent)** |
| R1 | `kiai_ranges` reads `bool(int(fields[7]))` instead of masking bit 0, so `effects=8` (omit-first-barline) reads as kiai-on. **Barline gimmicks set that bit en masse — the overview bar turns fully orange the day M5 ships** | `gui.py:200` | **High** |
| R11 | `sorted(self.selected)` is time-ordered only by the accident that `original_index` follows file order. Once insertion exists this silently scrambles every pattern; must sort by `(time, uid)` | `gui.py:1965` | **High (future)** |
| R3 | `is_kat`/`is_finisher` test hitsound bits unconditionally, so drumrolls and spinners render and drag as don/kat circles | `model/hit_object.py:10-15` | Medium |
| R4 | Flat `shift` assumes `[Difficulty]` precedes `[HitObjects]`; violation rewrites fields of unrelated lines, undetectable by the count-only assertion | `osu_io/writer.py:59` | Medium |
| R5 | `set_document_background` appends `f"{ending}[Events]{ending}"` as a **single list element** containing two newlines, so every later index repair is off by one; also places `[Events]` after `[HitObjects]` | `gui.py:292` | Medium |
| R6 | `extract_timing_points` requires ≥7 fields; v4 maps write 2 → zero timing points → silent fabricated 120 BPM | `gui.py:112`, `:134` | Medium |
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
