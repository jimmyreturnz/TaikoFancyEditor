# Taiko Fancy Arranger

**Turn osu!taiko notes into visual patterns without manually placing every circle.**

Taiko Fancy Arranger is a desktop tool for osu!taiko players and mappers who want to create storyboard-like note layouts, geometric patterns, text, equations, drawings, spirals, and other visual effects directly inside a beatmap.

The program keeps the notes as playable osu! hit objects while providing a visual workspace to select, preview, move, transform, undo, redo, and export arrangements.

> **Current release:** v1.0.1  
> **Platform:** Windows x64  
> **Author:** [jimmyreturnz](https://osu.ppy.sh/users/11306153)

The original idea came from a random chat with maruaki101. Other inspirations include Alchyr's ranked maps *13 Stairs* and *Helios*, which use unusual note placement to create visual expression. You should go check it out [here!](https://osu.ppy.sh/beatmapsets/1093671#taiko/3819326)

---

## Windows SmartScreen notice

Taiko Fancy Arranger v1.0.2 is a new, currently unsigned, but implemented safety measures to ensure that the program can be run safely.
Windows application. Windows Defender SmartScreen may display
an "unrecognized app" warning because the executable has not
yet established download reputation.

## What can it do?

- Open an osu! song folder by selecting any `.osu` difficulty
- Switch between difficulties in the same song folder
- Play and seek through the song with a taiko gameplay timeline
- View beat-snap lines, timing points, bookmarks, PreviewTime, Kiai sections, density, and the beatmap background
- Drag-select part of a map or select the full difficulty
- Preview a transformation before applying it
- Arrange all selected notes together or split Don and Kat notes
- Drag a generated pattern directly inside the transformation view
- Adjust pattern position, size, rotation, spacing, margins, seeds, and other parameters
- Choose a system font for Text transformations
- Draw shapes using multiple independent strokes
- Undo and redo committed transformations
- Configure exported Approach Rate and Circle Size
- Export an arranged difficulty without destroying the source map
- Back up the original file before applying changes to it

---

## Transformations

Taiko Fancy Arranger includes transformations such as:

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
- Straight line and polyline
- Wave and zigzag
- Bézier path
- Random
- Random Walk
- DVD Bouncing
- Pinwheel

Some transformations support chunking, direction controls, seeded randomness, or **Back and Forth** traversal.

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

Drawing accepts multiple independent strokes. The strokes are treated as one visual shape rather than being connected into an artificial path.

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

## Quick start

### 1. Open a difficulty

Click **Open .osu** and select a difficulty from an osu! song folder.

The program reads the difficulty's:

- Audio file
- Hit objects
- Inherited and uninherited timing points
- Kiai sections
- Bookmarks
- PreviewTime
- Background image
- Other `.osu` difficulties in the same folder

### 2. Select and navigate notes

Drag across the gameplay timeline to select a section.

Useful controls:

```text
Ctrl+A              Select the whole map while the gameplay view has focus
Escape              Clear the current selection
Space               Play or pause
Mouse wheel         Seek by beat snap
Shift+wheel         Seek by whole beats
Ctrl+wheel          Zoom the gameplay timeline
Alt+wheel           Change beat snap
Shift+Left/Right    Move by one whole beat
Alt+Left/Right      Move by one current snap division
Ctrl+Left/Right     Jump to the nearest bookmark
```

Bookmark navigation does nothing when no bookmark exists in the requested direction.

### 3. Choose a transformation

Select either:

```text
All Notes
Split Don / Kat
```

Then choose a transformation and adjust its parameters.

The transformation pane shows a preview only. The `.osu` file is not changed until the transformation is applied and the map is exported or written.

### 4. Position the result

Use either:

- Position X and Position Y controls
- Direct dragging inside the transformation view

In **Split Don / Kat** mode:

- Dragging a Don moves the selected Don pattern
- Dragging a Kat moves the selected Kat pattern

### 5. Apply or undo

Click **Transform selected notes** to commit the preview to the current editing session.

```text
Ctrl+Z    Undo
Ctrl+Y    Redo
```

Undo and redo can be used repeatedly across committed transformations.

### 6. Configure Approach Rate and Circle Size

The top toolbar contains **AR** and **CS** controls next to **Export applied map**.

Each control supports:

- A slider from `0.00` to `10.00`
- `0.01` increments
- A numeric field for entering an exact value
- Pink `+` and `-` buttons

Meaning of the minimum values:

```text
AR 0.00    Slowest approach rate
CS 0.00    Biggest circle size
```

If the controls are not changed, the existing defaults remain:

```text
ApproachRate:10
CircleSize:7
```

The selected values are used by both **Export applied map** and **Apply all changes to original file**.

### 7. Export

Use **Export applied map** to create a separate arranged difficulty.

Use **Apply all changes to original file** only when intentionally updating the loaded `.osu`. The program creates a backup before replacing the original.

---

## Gameplay, timing, and density views

The gameplay timeline displays visible notes around the current song position.

The dedicated full-song timing bar includes:

- A thin white horizontal center line
- Kiai highlighting centered on the line
- Green inherited timing-point markers above the line
- Red uninherited timing-point markers above the line
- Yellow markers where inherited and uninherited timing points overlap
- Blue bookmark markers below the line
- A yellow PreviewTime marker below the line
- Current playback position
- Visible gameplay viewport

The density chart is separate and contains the white-to-yellow note-density visualization without timing points or Kiai highlighting.

The playback row provides:

- Moving time display
- Play and pause
- `25%`, `50%`, `75%`, and `100%` playback-rate buttons

The beatmap background is displayed in the transformation view. Its opacity can be adjusted, and another image can be dragged into the transformation view to use as the beatmap background.

---

## Important mapper notes

### Always keep a backup

Although the program provides export and backup behavior, keep a separate copy of important beatmaps before editing.

### Transformations change hit-object coordinates

Taiko Fancy Arranger changes the X and Y positions of selected hit objects. Note timing, hitsounds, and note order are preserved unless a future feature explicitly says otherwise.

### Visual readability depends on note count

Text, equations, drawings, and detailed shapes need enough selected notes to remain recognizable. If a pattern looks incomplete, try:

- Selecting more notes
- Increasing the number of notes per pattern
- Reducing text complexity
- Reducing the number of Pinwheel blades
- Increasing equation resolution
- Adjusting graph bounds or size

### Test the exported map in osu!

Always open the exported difficulty in the osu! editor and verify:

- Note positions
- Timing
- Approach Rate
- Circle Size
- Background
- Difficulty name

---

A tagged GitHub release such as `v1.0.2` can use the included GitHub Actions workflow to build and attach the Windows ZIP automatically.

---

## Feedback and bug reports

When reporting a bug, include:

- Taiko Fancy Arranger version
- Windows version
- Transformation name
- Selected-note count
- Parameters used
- Exact error message or traceback
- Steps that reproduce the problem
- A minimal `.osu` example if redistribution is allowed

Do not upload copyrighted audio or private beatmap assets unless permission has been granted.

---

## Project status

Version 1.0.2 is the current public release. The project focuses on creative single-player beatmap arrangement and previewing. Multiplayer and automatic difficulty calculation are outside the current scope.

---

## Further plans

- Rotation for some transformations that has not been added yet
- web release (?)
- 日本語版
- chart editing with multiple charts of the same audio file support.
- SV Editing with configurable effect functions (taking Alchyr’s design as a reference)
- easier usage of equation transformation
- Image-based outline tracing transformation
- Gimmick SV Tool with whatever I could think of, basic one are barline, slider, shiny, reverse barline. Planned to experiment with some different ideas to be implemented here too
- might add updater later, but not anytime soon
- Proper UI Design
- fullalt transformation(?)

—--

## License

Taiko Fancy Arranger is released under the MIT License. See [`LICENSE`](LICENSE) for details.

This project is a community-made tool and is not affiliated with or endorsed by osu! or ppy Pty Ltd.
