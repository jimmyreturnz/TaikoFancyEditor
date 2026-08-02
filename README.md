# Taiko Fancy Arranger

**Turn osu!taiko notes into visual patterns without manually placing every circle.**

Taiko Fancy Arranger is a desktop tool for osu!taiko players and mappers who want to create fancy storyboard-like note layouts, geometric patterns, text, equations, spirals, and other visual effects directly inside a beatmap.

The program keeps the notes playable as osu! hit objects while giving you a visual workspace to select, preview, move, transform, undo, and export arrangements.

> **Current release:** v1.0.0  
> **Platform:** Windows x64


> **Author:** jimmyreturnz

The original idea is from a random chat with maruaki101 and other inspirations are from Alchyr's idea on his ranked maps called 13 stairs, and the other one is Helios where he places notes in a really interesting way to express something. You definitely should go check it out!

---

## Windows SmartScreen notice

Taiko Fancy Arranger v1.0.0 is a new, currently unsigned
Windows application. Windows Defender SmartScreen may display
an "unrecognized app" warning because the executable has not
yet established download reputation.

## What can it do?

- Open an osu! song folder by selecting any `.osu` difficulty
- Switch between difficulties from the same folder
- Play and seek through the song with a taiko gameplay timeline
- View beat snap lines, Kiai sections, density, and the beatmap background
- Drag-select part of a map or select the full difficulty
- Preview a transformation before applying it
- Arrange all selected notes together or split Don and Kat notes
- Drag a generated pattern directly inside the transformation view
- Adjust pattern position, size, rotation, spacing, margins, seeds, and other parameters
- Undo and redo committed transformations
- Export an arranged difficulty without destroying the source map
- Back up the original file before applying changes to it

---

## Transformations

Taiko Fancy Arranger includes transformations such as:

- Text
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

The program uses an available system font and fits the result inside the osu! playfield. Text size, margins, direction, note count, and position can be adjusted before applying the result.

### Mathematical equation patterns

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
- Inner circle note count and radius
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
- Timing points
- Kiai sections
- Background image
- Other `.osu` difficulties in the same folder

### 2. Select notes

Drag across the gameplay timeline to select a section.

Useful controls:

```text
Ctrl+A          Select the whole map while the gameplay view has focus
Escape          Clear the current selection
Space           Play or pause
Mouse wheel     Seek by beat snap
Shift+wheel     Seek by whole beats
Ctrl+wheel      Zoom the gameplay timeline
Alt+wheel       Change beat snap
```

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

- Position X and Position Y sliders
- Direct dragging inside the transformation view

In **Split Don / Kat** mode:

- Dragging a Don moves the selected Don pattern
- Dragging a Kat moves the selected Kat pattern

### 5. Apply or undo

Click **Apply to selection** to commit the preview to the current editing session.

```text
Ctrl+Z    Undo
Ctrl+Y    Redo
```

Undo and redo can be used repeatedly across committed transformations.

### 6. Export

Use **Export applied map** to create a separate arranged difficulty.

Use **Apply all changes to original file** only when you intentionally want to update the loaded `.osu`. The program creates a backup before replacing the original.

Exported maps use:

```text
ApproachRate:10
CircleSize:7
```

---

## Gameplay and density view

The gameplay timeline displays the visible notes around the current song position.

The density chart below the gameplay uses:

- White to yellow density coloring
- Yellow at peak density
- Orange Kiai highlighting
- A playback cursor
- A viewport indicator showing the currently visible gameplay range

The background image from the beatmap is displayed in the transformation view. Its opacity can be adjusted, and another image can be dragged into the transformation view to use it as the beatmap background.

---

## Important mapper notes

### Always keep a backup

Although the program provides export and backup behavior, keep a separate copy of important beatmaps before editing.

### Transformations change hit-object coordinates

Taiko Fancy Arranger changes the X and Y positions of selected hit objects. Note timing, hitsounds, and note order are preserved unless a future feature explicitly says otherwise.

### Visual readability depends on note count

Text, equations, and detailed shapes need enough selected notes to remain recognizable. If a pattern looks incomplete, try:

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
- Background
- Difficulty name

---

## Run from source

Requirements:

- Python 3.12
- Windows 10 or Windows 11 recommended

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python gui.py
```

Or run:

```text
run_from_source.bat
```

---

## Build the Windows release

Run:

```text
build_windows.bat
```

The portable build is created at:

```text
dist\TaikoFancyArranger\TaikoFancyArranger.exe
```

A tagged GitHub release such as `v1.0.0` can use the included GitHub Actions workflow to build and attach the Windows ZIP automatically.

---

## Feedback and bug reports

When reporting a bug, please include:

- Taiko Fancy Arranger version
- Windows version
- Transformation name
- Selected-note count
- Parameters used
- Exact error message or traceback
- Steps that reproduce the problem
- A minimal `.osu` example if redistribution is allowed

Do not upload copyrighted audio or private beatmap assets unless you have permission to share them.

---

## Project status

Version 1.0.0 is the first public release. The project focuses on creative single-player beatmap arrangement and previewing. Multiplayer and automatic difficulty calculation are outside the current scope.

---

## License

Taiko Fancy Arranger is released under the MIT License. See [`LICENSE`](LICENSE) for details.

This project is a community-made tool and is not affiliated with or endorsed by osu! or ppy Pty Ltd.
