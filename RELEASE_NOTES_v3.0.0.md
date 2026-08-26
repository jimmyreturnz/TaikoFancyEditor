# Taiko Fancy Arranger v3.0.0

The editor gets a gimmick page.

A "gimmick" in osu!taiko is a visual effect built out of timing points rather than notes: drumrolls of negative length that are drawn but never hittable ("fake sliders"), notes drawn out of barlines, and extreme BPM/SV values used to move objects around the screen. Building and editing these by hand in a text-based `.osu` file is slow and error-prone. v3.0.0 adds a dedicated editor for them, alongside hitsounds and a pass on millisecond-level timing accuracy.

## New

### The gimmick page

Six stacked layers over one difficulty: the normal chart, fake sliders, barline gimmicks, and an SV layer for each of the three.

- Entering the page asks once whether to edit the current difficulty in place or copy it to a new `[Gimmick]` difficulty, and remembers the answer permanently.
- It takes a base timing snapshot at that moment. A gimmick fills a file with 60000 BPM lines, every one of which collapses the snap grid, so the grid, wheel scroll and BPM overlay are driven from the snapshot instead of the file being edited — the grid never moves under the cursor.
- Any layer can be closed and comes back on the next visit; the page remembers its scroll position across page switches.

### Fake sliders and shiny notes

The fake slider layer places fake sliders, and "shiny" notes — several fake sliders stacked on one millisecond, which reads as a bright white glow beside a note. The two are told apart by position: an object one millisecond after the note or line it hangs off is a shiny, two milliseconds after is a fake slider.

The layer draws them on two rows (fake sliders on the ceiling, shiny below) with the snap grid down the middle, because at gimmick zoom they are one pixel column apart.

Tools: Fake Slider, Don, Kat, Shiny, Multiple Fake Slider (one click writes a whole run at a configured spacing), Function (fill a dragged range), Convert Notes (turn the chart's own notes into structures), and Kiai (drag a range, every timing point in it becomes one kiai section). A shiny's red line can be retimed to a multiple of the chart's BPM with its SV divided by the same amount, so it travels at the speed it already did.

### Barline gimmicks

Notes drawn out of red lines — a Don is one mirrored pair of bars, a Kat is three, each pair independently configurable, and they can be mirrored around the note or trail it.

A red line tool with its own configurable BPM, and a Function tool that fills a range with red lines on a millisecond count or the beat grid, with an optional BPM ramp and a chosen SV (defaulting to the speed already in force, since an uninherited point otherwise resets SV to 1.0x).

### Per-structure SV

Each of the three object layers has its own SV layer that owns exactly its structures' milliseconds, so the three stop showing each other's green lines. Copy and paste inside them maps by object index rather than by millisecond, so an SV shape lifted off four fake sliders lands on the next four whatever their spacing. Oscillating SV is available in the generator alongside the seven easing curves.

### Hitsounds

Notes now sound during playback, using samples shipped in `assets/se/`. Don, kat, big don and big kat each get their own. Circles only — fake sliders and barlines are silent.

A new Audio settings page carries hitsound enable/volume, a latency offset in milliseconds, and music volume (which previously had no UI at all).

### Millisecond-exact editing

osu! stores whole milliseconds while the beat grid is fractional, so at deep zoom an object sat visibly off its own gridline. The grid, the playhead and every placement now agree on one whole millisecond, rounded the way osu! rounds (halves up, where Python rounds halves to even). Holding `Ctrl` places at 1ms precision regardless of the snap divisor, with a guide line and a live millisecond readout in the corner of every view.

### Kiai

Kiai sections are drawn as a translucent orange band in every layer. Every timing point a gimmick tool generates now carries the kiai state of the section it lands in, instead of silently ending it. The gameplay preview's kiai flash reaches every visible note rather than dying whenever the playhead sat on a gimmick line.

## Fixed

- Slider and spinner tails can now be dragged with the Select tool.
- Right-clicking the timeline bar during a drag-selection scrubs the playhead without ending the selection.
- Alt+wheel changes the snap divisor without moving the view.
- Double-clicking a timing line can toggle kiai and omit-barline.

## Changed

- Refusals appear as a transient toast rather than silently doing nothing.
- Shift resizes the note preview immediately instead of waiting for the mouse to move.
- Every view opens at the same zoom.
- Every spin box has pink +/- buttons.
- New per-difficulty gimmick pairings (which difficulty edits in place vs. which `[Gimmick]` difficulty it copies to) are stored in `gimmick_index.json` in the app data folder, alongside the existing settings file.

## Notes

- Hitsound samples ship in `assets/se/`; the application looks for them relative to its own install, so a manual or partial copy of the program folder can lose sound without otherwise failing.
- The gimmick page's timing snapshot is taken once, on entry — closing and reopening the page against a difficulty that was edited elsewhere in the meantime takes a fresh snapshot.
- Slider length still uses osu!'s default `SliderMultiplier` of 1.4; this program does not yet parse a map's own value.
- The application is still unsigned. SHA-256 checksums are published with each release.

## Compatibility

Existing beatmaps, settings and workflows are unchanged; `gimmick_index.json` is created automatically the first time the gimmick page is used and does not affect maps that never open it. Always keep a backup, and open exported difficulties in the osu! editor to verify before use.
