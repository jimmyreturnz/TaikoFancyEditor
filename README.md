# Taiko Fancy Arranger

**An osu!taiko editor with a visual pattern arranger built in.**

Taiko Fancy Arranger started as a tool for turning osu!taiko notes into visual patterns — text, shapes, equations, drawings, spirals — without placing every circle by hand. Version 2.0.0 grows it into an editor: browse your osu! Songs folder, open a difficulty, edit notes, edit scroll velocity, preview the chart the way osu! renders it, and still arrange notes into patterns when you want to.

Everything stays a playable osu! beatmap. Sections the editor does not model — storyboards, breaks, colours, editor bookmarks — are passed through byte-for-byte on save.

> **Current release:** v2.0.0  
> **Platform:** Windows x64  
> **Author:** [jimmyreturnz](https://osu.ppy.sh/users/11306153)

The original idea came from a random chat with maruaki101. Other inspirations include Alchyr's ranked maps *13 Stairs* and *Helios*, which use unusual note placement to create visual expression. You should go check it out [here!](https://osu.ppy.sh/beatmapsets/1093671#taiko/3819326)

---

## What is new in 2.0.0

This is the largest release so far, and the first that changes what the program *is*.

- **Song library.** The app now opens on your osu! Songs folder instead of an empty window. It scans for `Mode: 1` charts once, caches the index, and every later start shows the list immediately. Search, group by mapper or artist, sort, and toggle original-language metadata.
- **Editor page.** A new primary page holding stackable views: chart, SV editor, gameplay viewer, and density — several difficulties open at once, each view labelled with its difficulty.
- **Note editing.** Place and delete dons, kats, sliders and spinners on the beat grid. Sliders and spinners are click-dragged to length and can be resized afterwards.
- **SV editor.** View, add, drag, retime, copy and delete inherited (green) points, with a live effective-SV graph over red lines. Function mode generates eased SV sweeps across a selected range using seven curves.
- **Gameplay viewer.** A read-only preview where each object scrolls at the SV in force at its own time, with barlines — so an SV sweep can be judged without exporting.
- **Real undo.** A command-based history per difficulty. A 400-point generated sweep is one Ctrl+Z.
- **Rewritten file layer.** `[TimingPoints]` and `[HitObjects]` are modelled and regenerated; every other section is preserved verbatim. This also fixed a latent parser bug that discarded slider curve, slide count, length and spinner end times.
- **First-run setup and configurable shortcuts.** Language is chosen on first start, then the Songs folder. Thirteen actions are rebindable in Settings, including copy, paste, save-all and the tool digits.

Full detail lives in [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md).

---

## Windows SmartScreen notice

Taiko Fancy Arranger v2.0.0 is a currently unsigned Windows application. The release includes source-level security hardening, input validation, safer file handling, automated tests, and published SHA-256 checksums.

Windows Defender SmartScreen may still display an "unrecognized app" warning because the executable has not yet established download reputation.

---

## Languages

Currently supports English and Japanese. There is no Thai translation yet even though I am Thai myself 😂

Language is chosen on first start and can be changed in **Settings**. Changing it asks you to restart, because Qt does not retranslate widgets that already exist.

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

Above the views: beat snap, the merged time and percentage readout, the timing bar (kiai, bookmarks, SV, BPM), play and speed buttons. The snap divisor is global across the page, so every view stays on the same grid. Zoom (`Ctrl+wheel`) is per difficulty and shared by that difficulty's views.

Per-view chrome: **close**, **lock** (read-only), and the difficulty name at the right.

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
- Sliders and spinners are **click-dragged** to their length. Press near the right edge of an existing one to resize it.
- **Shift** at release places a big (finisher) note or slider.
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
- The graph shows **effective** SV — green over red where both exist — with a fixed 0.1x floor and an autoscaling ceiling.

**Function mode:** drag a range, then choose initial rate, final rate, position offset, whether to omit the first barline, and whether the sweep is relative to the final BPM. Seven curves are offered as tiles, each drawing the sweep you actually typed:

```text
linear   sin in   sin out   exp1.3   exp1.6   true exp   sin
```

Points are generated on the notes in range by default, or every N snaps. The default −5 ms offset makes sure the SV is already in force when the note it governs arrives. However many points it makes, it is one undo step.

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
5. Set **AR** and **CS** if needed — sliders from `0.00` to `10.00` in `0.01` steps, with a numeric field and pink `+` / `-` buttons. `AR 0.00` is the slowest approach rate and `CS 0.00` the biggest circle size. Left alone, `ApproachRate:10` and `CircleSize:7` remain.
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

Run from source with `python main.py`, or `run_from_source.bat` on Windows.

Packaging files:

| File | Purpose |
|---|---|
| `VERSION` | The release version, bundled into the build |
| `TaikoFancyArranger.spec` | PyInstaller spec — bundles `assets/`, `VERSION` and the compiled `.qm` translations |
| `requirements.txt` | Runtime dependency (PySide6) |
| `requirements-build.txt` | The above plus PyInstaller |
| `.env.example` | Documents that **no** environment variables are needed; never put secrets here |
| `.github/workflows/release-windows.yml` | Builds the portable ZIP and SHA-256 checksums, and attaches them to a published release |

Publishing a GitHub release tagged `vX.Y.Z` runs that workflow, which compiles translations, runs the test suite, builds, and uploads `TaikoFancyArranger-Windows-x64.zip` plus `SHA256SUMS.txt`. A matching `RELEASE_NOTES_vX.Y.Z.md` is bundled into the ZIP when present.

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
- Which page and view (Editor chart, SV editor, Fancy Arranger, …)
- Transformation name, selected-note count and parameters, if relevant
- Exact error message or traceback
- Steps that reproduce the problem
- A minimal `.osu` example if redistribution is allowed

Do not upload copyrighted audio or private beatmap assets unless permission has been granted.

---

## Project status

Version 2.0.0 is the current public release. The project focuses on creative single-player beatmap editing, arrangement and previewing. Multiplayer and automatic difficulty calculation are outside the current scope.

Not yet implemented, and next in line:

- **Gimmick editor** — a fourth page with a fake-slider lane, a chart lane and a barline lane, covering barlines, reverse barlines, invisible notes and slider gimmicks.
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
