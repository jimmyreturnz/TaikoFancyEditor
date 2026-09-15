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

Taiko Fancy Arranger started as a tool for turning osu!taiko notes into visual patterns — text, shapes, equations, drawings, spirals — without placing every circle by hand. Version 2.0.0 grew it into an editor: browse your osu! Songs folder, open a difficulty, edit notes, edit scroll velocity, preview the chart the way osu! renders it, and still arrange notes into patterns when you want to. Version 3.0.0 adds a dedicated gimmick editor, for the fake sliders, barline tricks and extreme-SV effects that osu!taiko mappers build out of timing points rather than notes. 

Everything stays a playable osu! beatmap. Sections the editor does not support editing storyboards, breaks, colours, editor bookmarks yet.

> **Current release:** v3.3.3  
> **Platform:** Windows x64  
> **Author:** [jimmyreturnz](https://osu.ppy.sh/users/11306153)

The original idea came from a random chat with maruaki101. Main inspirations include Alchyr's ranked maps *13 Stairs* and *Helios*, which use unusual note placement to create visual expression. You should go check it out [here!](https://osu.ppy.sh/beatmapsets/1093671#taiko/3819326)

Many thanks to Mew’s beatmaps for studying reference that made the tool creation possible, and other player’s ideas!

---

## What is new in 3.3.3

Fancy Arranger's Apply made to keep what it shows, copy/paste and red lines tightened in the gimmick editor, and faster note placement on heavy gimmick maps.

- **Apply in Fancy Arranger keeps the notes where the canvas showed them** — the next click no longer snaps them back, a drag with no transformation is committed, and a centred transformation with a drag is no longer applied twice as far.
- **Pasted fake sliders keep their offset from the snap** instead of landing on the playhead.
- **Placing a note on a heavy gimmick map is about 20% faster** — 192ms → 153ms, undo 253ms → 206ms, on a 21,040-timing-point map.
- **Meter** is editable in a red line's double-click dialog.
- **Anti-barline slits default to 1 tick for Don and 2 for Kat**, and the gimmick editor's chart SV starts on the note (offset 0).
- **Red Line refuses a millisecond that already has a red line**, and says so.

Full notes: [`docs/releases/v3.3.3.md`](docs/releases/v3.3.3.md).

---

## What is new in 3.3.2

The slow-playback "kick lands late" report found and fixed, and wheel scrolling during playback made smooth.

- **Hitsounds now default to +22ms against the music** — the "kick lands late at 25%" report was a flat offset between chart and song (median +22ms on MP3, +23ms on OGG over 60 installed maps), not the time-stretch.
- **Wheel scrolling during playback no longer freezes the playhead** — 110 of 359 frames frozen at a notch every 25ms, now 0, and no freeze after a fast spin.
- **Right-clicking a fake slider's red line removes only that line**; right-clicking the slider still removes both.
- **Ctrl+[ / Ctrl+] nudge the hitsound offset by 1ms** while playing, to tune it by ear.
- **The offset calibrator takes key taps as soon as it opens.**

Full notes: [`docs/releases/v3.3.2.md`](docs/releases/v3.3.2.md).

---

## What is new in 3.3.1

Slow playback and hitsounds, both rebuilt from measurement.

- **Slow playback (25/50/75%) is substantially more accurate.** The time-stretch grain shortened from 117ms to 20ms, which also sounds better on real tracks (a kick drum used to come apart at 25%) and costs less CPU.
- **Known limitation:** a small timing gap between the playhead and the music remains at 25% speed, under investigation.

Full notes: [`docs/releases/v3.3.1.md`](docs/releases/v3.3.1.md).

---

## What is new in 3.3.0

A gimmick-editor release: a second anti-barline gimmick, one way into every converter, snapping that matches osu! exactly, and fixes across copy/paste, the SV generator's curves and the fake slider layer.

- **Hidden anti-barline**, in the barline layer's Convert Notes: one red line makes osu! draw the whole barline sheet, and each note's slit is opened by raising SV for a fraction of a beat — so the slit **opens with distance and closes at the hit position**, and the note's colour is only readable while it is far away.
- **Convert Notes is the one way into every converter**, and its numbers are asked per call instead of being read from — or written back to — the layer's saved Config.
- **Hide note** on every Don/Kat structure: off, the note stays visible inside its own structure.
- **Multiple Fake Slider previews the whole run** under the cursor, at the size Shift will give it.
- **Snapping matches osu!'s truncation** — a snapped position goes down to the whole millisecond, not to the nearest one. Measured over 25 installed maps.
- **Finisher notes are drawn bigger**, at the ruleset's own 1.538x a normal note.
- **Fixed:** copy/paste landed 1ms off on some beats; copying a gimmick lost its green line; pasting SV in a gimmick layer ignored the layer's offset; the SV generator's "true exponential" bent the same way for every range and its sine curves were the wrong way round; checked buttons clipped their own labels; the snap grid cost 15.3ms of a 16.3ms frame on an extreme chart.

Full notes: [`docs/releases/v3.3.0.md`](docs/releases/v3.3.0.md).

Full development log detail lives in [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md).

---

## Windows SmartScreen notice

Taiko Fancy Arranger v3.3.3 is a currently unsigned Windows application. The release includes source-level security hardening, input validation, safer file handling, automated tests, and published SHA-256 checksums.

Windows Defender SmartScreen may still display an "unrecognized app" warning because the executable has not yet established download reputation.

---

## Languages

Currently supports English and Japanese. There is no Thai translation yet even though I am Thai myself 😂

Language is chosen on first start and can be changed in **Settings**. Changing it asks you to restart, because Qt does not retranslate widgets that already exist.

---

### Slow playback

25%, 50% and 75% got a lot more accurate this update — the time-stretch now works in much shorter pieces (20ms instead of 117ms), which also fixed a kick drum sounding broken at 25% speed and is lighter on CPU too. There's still a small timing gap between what you see and what you hear at 25% that I'm still chasing down, but it's a fraction of what it used to be, so it should feel usable now instead of "stick to 100%".

Notes are mixed straight into the song's audio now too, instead of being played as their own separate sound — so they land on the music correctly at every speed, and can't pile up into one loud burst if you scroll or seek fast.

**Music offset (ms)** on the Audio settings page shifts the playhead to match when sound reaches your ears. Default value is 0ms, but you can calibrate it inside the settings.

**Hitsound offset (ms)** is separate from that — it nudges the notes' own sound against the music, since the notes are mixed into it now. It defaults to +22ms, which is where charts timed in osu! put their notes against this app's audio (measured over 60 maps). To fine-tune it by ear, slow the song to 25% and press **Ctrl+[** / **Ctrl+]** while it plays.

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

5. If update exists, upon opening the program, there will be a patch note alongside with an option to download the new version.
Python and PySide6 are bundled with the portable Windows release. Players using the release ZIP do not need to install Python or run `pip`.

> Windows may show a reputation warning for an unsigned new application. Review the repository and release files before running the program.

---

## First start

1. **Choose a language.** English or 日本語. This screen is deliberately untranslated — it is the one screen that cannot know which language you read.
2. **Choose your osu! Songs folder.** Pre-filled with `%LOCALAPPDATA%/osu!/Songs` when it exists. The folder is remembered, and can be changed later from the library page.
3. **Wait for the scan.** The first scan reads every `.osu` file once and takes a while on a large collection. It runs in slices, so the window stays responsive. Later starts show the cached list before verifying it. You may use the app while it is scanning, but the experience will not be that smooth on the editor and osu. Thus, it is suggested to wait for the scan, or make a small folder that consists of only maps that you would want to edit.

Cancelling the folder picker leaves the library empty with a **Change folder** button.

---

## Song library

The library page is the front door.

- Left: songs, shown as `Artist - Title · mapper · difficulty count`.
- Right: the difficulties of the selected song.
- Double-click a difficulty, or press **Edit this difficulty**, to open it in the editor.

Controls:

- **Search** — matches artist, title, both Unicode variants, difficulty name, creator and tags at once. Same behavior as osu!
- **Group by** — nothing, mapper, or artist.
- **Sort** — A→Z or Z→A, which reverses group order too.
- **Original language metadata** — shows `ArtistUnicode`/`TitleUnicode` instead of the romanized fields, falling back to whichever the map actually has.

Only taiko charts (`Mode: 1`) are listed.

---

## Editor page

The Editor page stacks views vertically, grouped under their difficulty. Open one with **+** (view type + difficulty). Opening a difficulty automatically gives it a chart view and an SV view.

Holding **Ctrl** places at 1ms precision regardless of the snap divisor, with a guide line and a live millisecond readout in the corner of every view.

View types:

| View | Explanation |
|---|---|
| Chart | Notes on a time axis, with a snap grid |
| SV editor | Red, green and yellow timing lines plus an effective-SV graph |
| Gameplay viewer | Read-only osu!taiko gameplay preview |
| Density | The white-to-yellow note density heatmap |

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
- Holding **Shift** will changes the note to finisher size.
- Right click deletes the note under the cursor
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
- Deleting green lines will not remove Red lines
- Double-clicking any timing line allows you to toggle kiai or the line's omit-barline flag.
- The graph shows **effective** SV — green over red where both exist — with a fixed 0.1x floor and an autoscaling ceiling. - to be changed to 0.01x

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

Leaving the editor with unsaved work lists which difficulties are unsaved by name and offers **Save**, **Continue without saving**, or **Cancel**. Continuing without saving keeps the edits in the session and nothing will be saved.

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
