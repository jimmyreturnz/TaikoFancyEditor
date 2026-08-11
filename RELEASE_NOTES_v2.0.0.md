# Taiko Fancy Arranger v2.0.0

The visual arranger becomes an editor.

v1.x could move the x/y coordinates of notes that already existed. v2.0.0 can browse your Songs folder, open a difficulty, place and delete notes, edit scroll velocity, preview the chart the way osu! renders it, and still arrange notes into patterns — with a file layer that can actually express all of it.

## New

### Song library

The app opens on your osu! Songs folder instead of an empty window.

- First start asks for a language, then the Songs folder (pre-filled with `%LOCALAPPDATA%/osu!/Songs`).
- Only taiko charts (`Mode: 1`) are listed. The scan is cached, so every start after the first shows the list immediately and verifies it in the background.
- The scan never blocks the window — it runs one frame's worth of work at a time.
- Search across artist, title, both Unicode variants, difficulty, creator and tags; group by mapper or artist; sort A→Z or Z→A; toggle original-language metadata.
- `Esc` returns here from the editor, listing by name any difficulty with unsaved changes.

### Editor page

A new primary page. Views stack vertically, grouped under their difficulty, several difficulties at a time.

- **Chart view** — notes on a snap grid anchored to the view's top and bottom edges.
- **SV editor** — red, green and yellow timing lines plus an effective-SV graph.
- **Gameplay viewer** — read-only osu!taiko preview; every object scrolls at the SV in force at its own time, with barlines.
- **Density view** — the note-density heatmap, now openable per difficulty.

Beat snap is global across the page; zoom is shared per difficulty. Each view can be closed or locked read-only.

### Note editing

Tools `1` select, `2` don, `3` kat, `4` slider, `5` spinner, `6` new combo.

- Left click places at the snapped time, with a translucent ghost preview.
- Sliders and spinners are click-dragged to length and can be resized by their right edge afterwards.
- `Shift` at release places a big (finisher) note.
- Right click deletes one note; `Delete` removes the selection.
- One object per millisecond, spinners excepted — a replacement is a single undo step.
- `Ctrl+C` / `Ctrl+V`, with paste snapped to the grid.

### SV editor

Tools `1` select, `2` green line, `3` function.

- Add, drag, retime, copy and delete inherited points. The drag axis is chosen by how close the click was to the value dot: near it changes SV, away from it retimes.
- Uninherited (BPM) points are never deleted here.
- The graph reads green over red where both share a millisecond — the real effective SV — with a fixed 0.1x floor and an autoscaling ceiling.
- Rubber-band selection with auto-scroll, `Ctrl+A` for the visible window.
- **Function mode**: drag a range, pick from seven curves (`linear`, `sin in`, `sin out`, `exp1.3`, `exp1.6`, `true exp`, `sin`), each tile drawing the sweep you actually typed. Generates on the notes in range by default, or every N snaps. Optional "relative to final BPM" keeps perceived speed constant across a BPM change. Default −5 ms offset so the SV is in force when its note arrives. However many points it writes, it is one undo step.

### Settings and shortcuts

Thirteen rebindable actions, up from three: play/pause, undo, redo, copy, paste, save-all (`Ctrl+S`), back-to-songs, and the six tool digits. The shortcut table is now translated — it had been silently English in Japanese mode.

## Fixed

- **Parser truncation (latent data loss).** Every field past `fields[5]` was discarded, so a slider's curve, slide count and length and a spinner's end time existed only because the old writer never regenerated the line. Note editing requires regenerating it — without this fix, saving would have destroyed every slider and spinner in every map.
- **PreviewTime never appeared.** It was read from `[Editor]`; it is a `[General]` key. The documented yellow marker had never shown on any real beatmap.
- **Undo/redo looked dead on the Editor page.** They repainted only the arranger canvas, so views kept showing the pre-undo document.
- **Every inserted note aliased the map's first note** in the applied-position table. Inserted notes now get their own identity.
- **Playback drift at slow rates.** A steady bias never crossed the old fixed threshold, so it went uncorrected for the whole playback. Replaced with gradual correction plus a hard resync only at a real discontinuity, and nanosecond-precision extrapolation whose rounding error no longer scales with playback rate.
- **Frame pacing.** The frame clock restarted on every frame, turning a late tick into a permanent shift; it now runs on a fixed cadence with stall resync.
- **Gimmick maps no longer freeze the editor.** Timing lookups are allocation-free binary searches against the uninherited points only, note circles are cached pixmaps, slider and spinner end times are cached per edit, and barlines are capped per frame — a map with thousands of points at absurd BPM asked for tens of millions of barlines.
- **`kiai_ranges` and `extract_timing_points`**, security tests excluded from CI by an over-broad gitignore, and the transformation list in the README have all been corrected.

## Changed

- `[TimingPoints]` and `[HitObjects]` are modelled and regenerated on save; **every other section is passed through byte-for-byte**, so storyboards, breaks, colours and editor bookmarks survive untouched. The only deliberate loss is `//` comments inside those two sections.
- Undo is command-based (`MoveNotes`, `InsertHitObjects`, `EditTimingPoints`, `CompositeCommand`, …) with one history per difficulty, replacing position-only snapshots. Switching difficulty no longer wipes selection, undo and redo.
- The file writer validates timing points before taking its `.bak`, `fsync`s, and falls back to UTF-8 when a cp1252 document gains non-Latin text.
- Save writes every changed difficulty at once; Export new difficulty writes into the same song folder. The Fancy Arranger's own export and apply-to-original are unchanged.
- "Drumroll" and "Denden" are labelled **Slider** and **Spinner** throughout; internal ids are unchanged.
- The UI font is the system font +2pt, and the widths it used to overflow are now measured rather than hardcoded.

## Notes

- The gimmick editor (fake sliders, barline gimmicks, invisible notes) is specced but **not** in this release.
- Slider length uses osu!'s default `SliderMultiplier` of 1.4; this program does not yet parse a map's own value. Slider timing is exact.
- Some residual playback inaccuracy at non-100% speeds comes from Qt Multimedia's time-stretching, not from position tracking.
- The application is still unsigned. SHA-256 checksums are published with each release.

## Compatibility

Existing beatmaps and workflows are unchanged. Always keep a backup, and open exported difficulties in the osu! editor to verify before use.
