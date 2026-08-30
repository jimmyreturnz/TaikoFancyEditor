# Taiko Fancy Arranger

I'll write a proper readme myself somedays, just read below first for brief understanding of what this tool can
If you want to ask on how to use it or suggest some ideas, my osu name is jimmyreturnz
my discord is also jimmyreturnz
thanks in advance!

いつかちゃんとしたREADMEファイルを書くつもりですがまずは下記を読んでこのツールがどんなものか簡単に理解してください
使い方の質問やアイデアの提案などがあれば僕のosu!ネームはjimmyreturnzです
Discordもjimmyreturnzです
よろしくお願いします！

**An osu!taiko editor with a visual pattern arranger built in.**

*日本語版は [README_JP.md](README_JP.md) をご覧ください。*

Taiko Fancy Arranger started as a tool for turning osu!taiko notes into visual patterns — text, shapes, equations, drawings, spirals — without placing every circle by hand. Version 2.0.0 grew it into an editor: browse your osu! Songs folder, open a difficulty, edit notes, edit scroll velocity, preview the chart the way osu! renders it, and still arrange notes into patterns when you want to. Version 3.0.0 adds a dedicated gimmick editor, for the fake sliders, barline tricks and extreme-SV effects that osu!taiko mappers build out of timing points rather than notes. Version 3.2.0 rebuilds song playback so slow practice holds its place, and puts your own osu! skin into every view that draws a note.

Everything stays a playable osu! beatmap. Sections the editor does not model — storyboards, breaks, colours, editor bookmarks — are passed through byte-for-byte on save.

> **Current release:** v3.2.0  
> **Platform:** Windows x64  
> **Author:** [jimmyreturnz](https://osu.ppy.sh/users/11306153)

The original idea came from a random chat with maruaki101. Other inspirations include Alchyr's ranked maps *13 Stairs* and *Helios*, which use unusual note placement to create visual expression. You should go check it out [here!](https://osu.ppy.sh/beatmapsets/1093671#taiko/3819326)

Many thanks to Mew’s beatmaps for studying reference that made the tool creation possible, and other player’s ideas!

---

## What is new in 3.2.0

This release rebuilds song playback and puts your osu! skin into every view that draws a note.

- **Audio playback rebuilt.** The app decodes the track and drives the sound card itself. Changing speed no longer moves the playhead (the old backend lost 114 ms of song time on a 0.25x to 1.0x switch), slowing down keeps the pitch, and the playhead follows the sample actually leaving the device.
- **Music offset calibration.** Tap along to a click track in **Settings → Audio** and it writes the app's own output offset. It never touches a beatmap's offset.
- **Skins.** Pick any taiko skin from your osu! Skins folder for note art, drumroll pieces and hitsounds — in the gameplay preview and the editor timeline layers alike, falling back per element to the built-in drawing.
- **The gameplay preview is the playfield.** The skin's bar, scrolling background, barlines and hit target, at osu!'s own proportions. Notes disappear when they land, drumrolls travel through, and kiai pulses on the beat.
- **Note opacity.** Set how solid notes are drawn in the editor layers, trading the snap grid's readability against the notes'.
- **Volume that means something.** The Kiai and Sound Volume layer's number now drives how loud don and kat actually play, its lines only move vertically, and Don and Kat mirror independently in the barline layer.
- **Fixes worth naming.** No more ear-splitting hitsound burst when scrolling fast, no backwards drift when seeking on a high-BPM section, Settings keeps its own scrolling, and the gimmick page's scrollbar scrolls.

Full notes: [`docs/releases/v3.2.0.md`](docs/releases/v3.2.0.md).

---

## What is new in 3.0.0

This release adds a fourth editor page for gimmicks — visual effects built out of timing points rather than notes — plus hitsounds and a run of precision fixes across the whole editor.

- **Gimmick editor page.** Six stacked layers over one difficulty: the normal chart, fake sliders, barline gimmicks, and an SV layer for each of the three. Entering it asks once whether to edit the difficulty in place or copy it to a new `[Gimmick]` difficulty, and takes a base timing snapshot so the grid stays put while thousands of 60000 BPM gimmick lines pile into the file.
- **Fake sliders and shiny notes.** Dedicated tools for fake sliders, shiny notes, runs of fake sliders at a fixed spacing, converting the chart's own notes into gimmick structures, and painting kiai over a dragged range.
- **Barline gimmicks.** Notes drawn out of red lines, mirrored around the note or trailing it, with a Function tool that fills a range with red lines on a millisecond count or the beat grid, including a BPM ramp.
- **Per-structure SV.** Chart, fake slider and barline layers each get their own SV layer that owns only its own structures' green lines, with index-based copy/paste and an oscillating curve added to the generator.
- **Hitsounds.** Notes sound during playback, using samples shipped in `assets/se/` — don, kat, big don and big kat. A new Audio settings page carries hitsound enable/volume, a latency offset, and music volume.
- **Millisecond-exact editing.** The grid, playhead and every placement now agree on one whole millisecond, rounded the way osu! rounds. Holding **Ctrl** places at 1ms precision regardless of snap, with a live millisecond readout.
- **Kiai everywhere.** Kiai sections draw as a translucent orange band in every gimmick layer, generated timing points carry the section's kiai state instead of silently ending it, and the gameplay viewer's kiai flash reaches every visible note.
- **Smaller editing improvements.** Slider and spinner tails drag with the Select tool, right-click on the timing bar scrubs the playhead without ending a drag-selection, refused actions show a toast instead of doing nothing, and every spin box has pink +/- buttons.

Full detail lives in [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md).

---

## Windows SmartScreen notice

Taiko Fancy Arranger v3.2.0 is a currently unsigned Windows application. The release includes source-level security hardening, input validation, safer file handling, automated tests, and published SHA-256 checksums.

Windows Defender SmartScreen may still display an "unrecognized app" warning because the executable has not yet established download reputation.

---

## Languages

Currently supports English and Japanese. There is no Thai translation yet even though I am Thai myself 😂

Language is chosen on first start and can be changed in **Settings**. Changing it asks you to restart, because Qt does not retranslate widgets that already exist.

---

## Audio settings

Notes make sound during playback, using hitsound samples shipped in `assets/se/`: don, kat, big don and big kat each have their own sample. Only circles are voiced — fake sliders and barline gimmicks stay silent.

The **Audio** settings page carries hitsound enable and volume, a latency offset in milliseconds, and music volume, which previously had no UI at all.

### Slow playback

25%, 50% and 75% **keep the original pitch**. The song is time-stretched rather than slowed like a record, so a quarter-speed stream still sounds like the instruments it was played on — the same thing osu!'s own editor does, and the reason it is worth practising against.

Changing speed does not interrupt anything. Audio already on its way to the speakers finishes at the old speed and everything after it runs at the new one, so there is no gap, no jump, and no drift introduced by the switch.

The playhead is positioned from the sample actually leaving the audio device, not from a progress signal, so it holds its place against the music at every speed.

**Music offset (ms)** on the Audio settings page shifts the playhead to match when sound reaches your ears. It ships at zero, because the right value depends on your device and drivers rather than on anything the app can know. Raise it if the notes look early against what you hear; it is measured in real time, so one value stays correct at every speed.

---

## Download and run

1. Open the repository's **Releases** page.
2. Download:

```text
TaikoFancyArranger-Windows-x64.zip
```

3. Extract the entire ZIP.
4. Run:

```text
TaikoFancyArranger.exe
```

Python and PySide6 are bundled with the portable Windows release. Players using the release ZIP do not need to install Python or run `pip`.

> Windows may show a reputation warning for an unsigned new application. Review the repository and release files before running the program.

---

## First start

1. **Choose a language.** English or 日本語. This screen is deliberately untranslated — it is the one screen that cannot know which language you read.
2. **Choose your osu! Songs folder.** Pre-filled with `%LOCALAPPDATA%/osu!/Songs` when it exists. The folder is remembered, and can be changed later from the library page.
3. **Wait for the scan.** The first scan reads every `.osu` file once and takes a while on a large collection. It runs in slices, so the window stays responsive. Later starts show the cached list before verifying it.

Cancelling the folder picker leaves the library empty with a **Change folder** button rather than a dead end, and offers the setup again next launch.

---

## Song library

The library page is the front door.

- Left: songs, shown as `Artist - Title · mapper · difficulty count`.
- Right: the difficulties of the selected song.
- Double-click a difficulty, or press **Edit this difficulty**, to open it in the editor.

Controls:

- **Search** — matches artist, title, both Unicode variants, difficulty name, creator and tags at once.
- **Group by** — nothing, mapper, or artist.
- **Sort** — A→Z or Z→A, which reverses group order too.
- **Original language metadata** — shows `ArtistUnicode`/`TitleUnicode` instead of the romanized fields, falling back to whichever the map actually has.

Only taiko charts (`Mode: 1`) are listed.

---

## Editor page

The Editor page stacks views vertically, grouped under their difficulty. Open one with **+** (view type + difficulty). Opening a difficulty automatically gives it a chart view and an SV view.

View types:

| View | What it is |
|---|---|
| Chart | Notes on a time axis, with a snap grid anchored to the top and bottom edges |
| SV editor | Red, green and yellow timing lines plus an effective-SV graph |
| Gameplay viewer | Read-only osu!taiko-style preview; objects scroll at their own SV |
| Density | The white-to-yellow note density heatmap |

The gameplay viewer's kiai flash reaches every visible note, not just whichever ones happened to be on screen when kiai started.

Above the views: beat snap, the merged time and percentage readout, the timing bar (kiai, bookmarks, SV, BPM), play and speed buttons. The snap divisor is global across the page, so every view stays on the same grid. Zoom (`Ctrl+wheel`) is per difficulty and shared by that difficulty's views. Right-clicking the timing bar while drag-selecting scrubs the playhead without ending the selection.

Per-view chrome: **close**, **lock** (read-only), and the difficulty name at the right.

An action the editor refuses — pasting into the wrong kind of view, for instance — shows a transient toast instead of silently doing nothing.

### Note editing

The tool row sits at the bottom of the page and acts on the focused chart view.

```text
1  Select
2  Don
3  Kat
4  Slider
5  Spinner
6  New combo
```

- Left click places at the snapped time; a translucent ghost previews where it lands.
- Sliders and spinners are **click-dragged** to their length. Press near the right edge of an existing one to resize it, or drag its tail with the **Select** tool afterwards.
- Holding **Shift** resizes the note preview immediately; releasing it places a big (finisher) note or slider.
- Right click deletes the note under the cursor; **Delete** removes the whole selection.
- One object per millisecond, spinners excepted — placing over a note replaces it, as a single undo step.

### SV editing

With an SV view focused, the tool row becomes:

```text
1  Select
2  Green line
3  Function
```

- Red lines are uninherited (BPM) points, green are inherited (SV), yellow means both share a millisecond.
- Click a green line to select it; drag vertically to change its SV, horizontally to retime it, snapped to the grid. Which axis you get depends on how close the click was to the value dot.
- Rubber-band select a range, `Ctrl+A` for everything visible, `Delete` to remove. Uninherited points are never deleted here.
- Double-clicking a timing line toggles kiai or the line's omit-barline flag.
- The graph shows **effective** SV — green over red where both exist — with a fixed 0.1x floor and an autoscaling ceiling.

**Function mode:** drag a range, then choose initial rate, final rate, position offset, whether to omit the first barline, and whether the sweep is relative to the final BPM. Seven curves are offered as tiles, each drawing the sweep you actually typed:

```text
linear   sin in   sin out   exp1.3   exp1.6   true exp   sin
```

Points are generated on the notes in range by default, or every N snaps. The default −5 ms offset makes sure the SV is already in force when the note it governs arrives. However many points it makes, it is one undo step.

### Millisecond precision

osu! stores whole milliseconds while the beat grid is fractional, so at deep zoom an object could sit visibly off its own gridline. The grid, the playhead and every placement now agree on one whole millisecond, rounded the way osu! rounds (halves up, where Python rounds halves to even).

Holding **Ctrl** places at 1ms precision regardless of the snap divisor, with a guide line and a live millisecond readout in the corner of every view.

### Editor shortcuts

```text
Space               Play or pause
Ctrl+Z / Ctrl+Y     Undo / redo (per difficulty)
Ctrl+C / Ctrl+V     Copy / paste notes or SV points, snapped on paste
Ctrl+S              Save every changed difficulty
Ctrl+A              Select all in the focused view
Delete              Delete the selection
Esc                 Back to the song list
1 - 6               Tools, routed to whichever tool row is showing
Mouse wheel         Seek by one snap
Shift+wheel         Seek by one beat
Ctrl+wheel          Zoom
Alt+wheel           Change beat snap
```

All of these are rebindable in **Settings**, except `Delete` and `Ctrl+A`, which belong to the focused view.

Leaving the editor with unsaved work lists which difficulties are unsaved by name and offers **Save**, **Continue without saving**, or **Cancel**. Continuing without saving keeps the edits in the session — nothing is written and nothing is thrown away.

---

## Gimmick editor page

A "gimmick" in osu!taiko is a visual effect built out of timing points rather than notes: drumrolls of negative length that are drawn but never hittable ("fake sliders"), notes drawn out of barlines, and extreme BPM/SV values used to move objects around the screen.

The gimmick page stacks six layers over one difficulty: the normal chart, a fake slider layer, a barline gimmick layer, and an SV layer for each of the three.

Entering it for a difficulty asks once whether to edit that difficulty in place or copy it into a new `[Gimmick]` difficulty, and remembers the answer permanently. It also takes a **base timing snapshot** at that moment: a gimmick fills a file with 60000 BPM lines, every one of which collapses the snap grid, so the grid, wheel scroll and BPM overlay are all driven from the snapshot instead of the file being edited — the grid never moves under the cursor as gimmick timing points pile up.

Kiai sections draw as a translucent orange band in every layer. Every timing point a gimmick tool generates carries the kiai state of the section it lands in, instead of silently ending it.

### Fake sliders and shiny notes

The fake slider layer places fake sliders, and "shiny" notes — several fake sliders stacked on one millisecond, which reads in-game as a bright white glow beside a note. The two are told apart by position: an object one millisecond after the note or line it hangs off is a shiny, two milliseconds after is a fake slider. The layer draws them on two rows, fake sliders on the ceiling and shiny below, with the snap grid down the middle, because at gimmick zoom they sit one pixel column apart.

Tools:

```text
Fake Slider
Don / Kat
Shiny
Multiple Fake Slider
Function
Convert Notes
Kiai
```

- **Multiple Fake Slider** writes a whole run of fake sliders at a configured spacing in one click.
- **Function** fills a dragged range.
- **Convert Notes** turns the chart's own notes into fake slider or shiny structures.
- **Kiai** drags a range and turns every timing point inside it into one kiai section.

### Barline gimmicks

Notes drawn out of red lines — a Don is one mirrored pair of bars, a Kat is three, and each pair is independently configurable. Bars can be mirrored around the note or trail it. A red line tool has its own configurable BPM, and a Function tool fills a range with red lines on a millisecond count or the beat grid, with an optional BPM ramp and a chosen SV.

### Per-structure SV

Each of the three object layers (chart, fake slider, barline) has its own SV layer, and each owns exactly its own structures' milliseconds — the three no longer show each other's green lines. Copy and paste inside them maps by object index rather than by millisecond, so an SV shape lifted off four fake sliders lands on the next four whatever their spacing. Oscillating SV joins the seven easing curves in the generator.

---

## Fancy Arranger page

The original visual arranger, unchanged in behaviour and now on its own page with its own timeline, timing bar, density chart, difficulty selector, AR/CS controls and export buttons.

### Transformations

- Text
- Drawing
- Mathematical Equation
- Horizontal
- Vertical
- Taiko
- Vertical Taiko
- Circle and ellipse
- Square, triangle, and diamond
- Star
- Spiral
- Infinity
- Arc
- Straight line
- Wave and zigzag
- Random
- Random Walk
- DVD Bouncing
- Pinwheel

Some transformations support chunking, direction controls, seeded randomness, or **Back and Forth** traversal.

Polyline and Bézier path exist in the transformation engine but are not yet selectable in the interface, because both need a way to enter control points that the parameter panel does not have yet. Freehand shapes are covered by **Drawing** in the meantime.

### Text patterns

Enter text such as:

```text
67
TAIKO
日本
ภาษาไทย
```

The Text transformation uses a selectable system font and fits the result inside the osu! playfield. Text size, margins, direction, note count, and position can be adjusted before applying the result.

Text notes are ordered from top to bottom, then horizontally within each visual row. Reverse direction keeps the top-to-bottom order and reverses only the horizontal order within each row.

You can also select fonts from your computer as well!

### Drawing patterns

Drawing accepts multiple independent strokes, treated as one visual shape rather than being connected into an artificial path. An image can be imported and traced into strokes.

Drawing notes are ordered:

1. From top to bottom
2. From left to right within each visual row

Reverse direction keeps the top-to-bottom order and changes each row to right-to-left.

Drawing-window shortcuts:

```text
Ctrl+Z    Undo the latest stroke
Ctrl+Y    Restore the latest undone stroke
```

### Mathematical equation patterns

> Warning: this is still an experimental feature, please expect some bugs.

Equation mode can generate note paths from graph outlines.

Supported modes:

- Explicit, for example `y=sin(x)`
- Implicit, for example `x^2+y^2=9`
- Parametric, for example `x(t)=cos(3*t)` and `y(t)=sin(2*t)`

Restrictions can be added to implicit or explicit expressions, for example:

```text
tan(x^2+y^2)=1{|x|<3}{|y|<3}
```

The built-in graph keyboard focuses on graph-related operations such as:

```text
sin  cos  tan
asin acos atan
sqrt abs
exp ln log
floor ceil
pi e
```

The equation renderer creates outlines only. It does not fill or color mathematical regions.

### Pinwheel patterns

Pinwheel creates a central burst with curved blades and optional seeded wander.

Its controls include:

- Inner circle
- Inner-circle note count and radius
- Number of blades
- Blade curl and spread
- Inner and outer radius
- Rotation
- Radius growth
- Wander strength and seed

### Arranger workflow

1. Drag across the timeline to select a section, or `Ctrl+A` for the whole map.
2. Choose **All Notes** or **Split Don / Kat**, then a transformation.
3. Adjust parameters, or drag the pattern directly inside the transformation view. In Split mode, dragging a Don moves the Don pattern and dragging a Kat moves the Kat pattern.
4. Press **Transform selected notes** to commit it to the session. `Ctrl+Z` / `Ctrl+Y` still apply.
5. Set **AR** and **CS** if needed — sliders from `0.00` to `10.00` in `0.01` steps, with a numeric field and pink `+` / `-` buttons (as on every spin box in the app now). `AR 0.00` is the slowest approach rate and `CS 0.00` the biggest circle size. Left alone, `ApproachRate:10` and `CircleSize:7` remain.
6. **Export applied map** writes a separate arranged difficulty. **Apply all changes to original file** overwrites the loaded `.osu`, after making a backup.

The transformation pane is a preview. Nothing is written until you export or apply.

The beatmap background is shown in the transformation view; its opacity is adjustable and another image can be dragged in to replace it.

---

## Saving and exporting

| Action | Where | What it does |
|---|---|---|
| Save | Global header | Writes every changed difficulty at once, backing each one up first |
| Export new difficulty | Global header | Writes a new difficulty file into the same song folder |
| Export applied map | Fancy Arranger | Writes an arranged difficulty to a destination you choose |
| Apply all changes to original file | Fancy Arranger | Overwrites the loaded `.osu`, after a backup |

Saving regenerates `[TimingPoints]` and `[HitObjects]` and passes every other section through unchanged. The only deliberate loss is `//` comments inside those two sections.

---

## Important mapper notes

### Always keep a backup

Although the program backs up before overwriting, keep a separate copy of important beatmaps before editing.

### Test the exported map in osu!

Always open the exported difficulty in the osu! editor and verify:

- Note positions and timing
- Timing points, SV and barlines
- Hitsounds
- Approach Rate and Circle Size
- Background and difficulty name

### Visual readability depends on note count

Text, equations, drawings, and detailed shapes need enough selected notes to remain recognizable. If a pattern looks incomplete, try:

- Selecting more notes
- Increasing the number of notes per pattern
- Reducing text complexity
- Reducing the number of Pinwheel blades
- Increasing equation resolution
- Adjusting graph bounds or size

### Known approximation

Slider length is computed with osu!'s default `SliderMultiplier` of 1.4, because this program does not yet parse the `[Difficulty]` section's own value. Slider *timing* is exact; only the stored length field can be slightly off on a map that overrides the multiplier.

---

## Building from source

```text
pip install -r requirements-build.txt
pyside6-lrelease translations/taiko_ja.ts
python -m unittest discover
pyinstaller --noconfirm --clean TaikoFancyArranger.spec
```

Run from source with `python gui.py`, or `run_from_source.bat` on Windows.

Packaging files:

| File | Purpose |
|---|---|
| `VERSION` | The release version, bundled into the build |
| `TaikoFancyArranger.spec` | PyInstaller spec — bundles `assets/`, `VERSION` and the compiled `.qm` translations |
| `requirements.txt` | Runtime dependency (PySide6) |
| `requirements-build.txt` | The above plus PyInstaller |
| `.env.example` | Documents that **no** environment variables are needed; never put secrets here |
| `.github/workflows/release-windows.yml` | Builds the portable ZIP and SHA-256 checksums, and attaches them to a published release |

Publishing a GitHub release tagged `vX.Y.Z` runs that workflow, which compiles translations, builds, and uploads `TaikoFancyArranger-Windows-x64.zip` plus `SHA256SUMS.txt`. A matching `docs/releases/vX.Y.Z.md` is bundled into the ZIP when present.

The program reads no environment variables and needs no `.env` file. `.env` and key/certificate files are gitignored.

Tests run headless:

```text
QT_QPA_PLATFORM=offscreen python -m unittest discover -v
```

---

## Feedback and bug reports

When reporting a bug, include:

- Taiko Fancy Arranger version
- Windows version
- Which page and view (Editor chart, SV editor, Gimmick editor, Fancy Arranger, …)
- Transformation name, selected-note count and parameters, if relevant
- Exact error message or traceback
- Steps that reproduce the problem
- A minimal `.osu` example if redistribution is allowed

Do not upload copyrighted audio or private beatmap assets unless permission has been granted.

---

## Project status

Version 3.2.0 is the current public release. The project focuses on creative single-player beatmap editing, arrangement and previewing. Multiplayer and automatic difficulty calculation are outside the current scope.

Not yet implemented, and next in line:

- Multi-difficulty editing of maps that share audio is possible today, but has not been exercised hard.

## Further plans

These ideas are exploratory and are not guaranteed for a specific release.

- Add rotation controls to transformations that do not support rotation yet
- Improve the usability of the Equation transformation
- Experiment with additional SV and visual gimmick concepts
- Improve the overall UI design
- Explore a possible web version
- Explore a possible full-alt transformation
- Consider adding an updater in a later version
- Thai localization ภาษาไทย

---

## License

Taiko Fancy Arranger is released under the MIT License. See [`LICENSE`](LICENSE) for details.

This project is a community-made tool and is not affiliated with or endorsed by osu! or ppy Pty Ltd.
