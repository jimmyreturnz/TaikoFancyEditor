from __future__ import annotations

# Stable display contexts. Internal IDs such as en, ja, dark, light, alpha are excluded.
CONTEXT_STRINGS = {
    "MainWindow": {
        "Open .osu", "Play", "Pause", "Reset applied transforms", "Export applied map",
        "Difficulty", "Playback Rate", "Background Opacity", "Transformation mode",
        "All Notes", "Split Don / Kat", "Swap Don ↔ Kat", "Transform selected notes",
        "Apply all changes to original file", "Beat snap", "Duration", "Now", "Snap",
        "Wheel: seek", "Shift+wheel: 1 beat", "Ctrl+wheel: zoom", "All", "Don", "Kat",
        "None", "Open Settings", "Open osu! beatmap", "osu! beatmaps (*.osu)",
        "Open failed", "Open a map first", "Open a beatmap before adding a background.",
        "Background copy failed", "Apply failed", "Select notes in the bottom timeline first.",
        "All applied transformations reset.", "Write failed", "Original updated",
        "Applied all committed changes to:\n", "Overwrite original beatmap?",
        "This writes every committed transformation to the original .osu file. Continue?",
        "Source protected", "Choose a different filename.", "Export failed", "Export complete",
        "Created:\n", "Swap transformation, parameters, and position between Don and Kat",
        "Swapped Don and Kat transformations.", "Show or hide equation keyboard",
        "Open Drawing Window and draw a shape to preview.", "Preview reset to original coordinates.",
        "Open a map to begin.", "Adjust by 0.01",
    },
    "Parameters": {
        "Position X", "Position Y", "Traversal", "Font", "Random Seed", "Direction",
        "Rotation", "Column", "Columns", "Min BPM", "Max BPM", "Minimum BPM", "Maximum BPM",
        "Step Size", "Step size", "Chunk", "Chunk Size", "Notes per Chunk", "Max Turn",
        "Maximum Turn", "Maximum turn", "Beats per line", "Blade Curl", "Blade Spread",
        "Blades", "Bottom to Top", "Cycles", "End radius", "Explicit", "Implicit", "Parametric",
        "Inner Circle", "Inner Circle Radius", "Inner Radius", "Inner radius", "Left to Right",
        "Length", "Lines", "Outer Radius", "Outer radius", "Phase", "Radius Growth", "Radius X",
        "Radius Y", "Resolution", "Right to Left", "Segments", "Side length", "Star points",
        "Start angle", "Start radius", "Sweep angle", "Top to Bottom", "Turns", "Wander Seed",
        "Wander Strength", "X Maximum", "X Minimum", "Y Maximum", "Y Minimum", "t Maximum",
        "t Minimum", "Amplitude", "Graph Size (%)", "Graph Type", "Text", "Text Size (%)",
        "Auto Arrange", "Margin X", "Margin Y", "Center X", "Center Y", "Width", "Height",
        "Radius", "Start Angle", "End Angle", "Angle", "Seed", "Steps", "Enabled", "Disabled",
        "Forward", "Reverse", "Clockwise", "Counterclockwise", "Restart Each Chunk",
        "Back and Forth", "Top to Bottom / Left to Right", "Top to Bottom / Right to Left",
        "Linear", "Ease In", "Ease Out", "Enter text, for example 67, 日本, or ภาษาไทย",
        "Notes per Drawing", "Notes per Pinwheel",
    },
    "DrawingDialog": {
        "Drawing", "Undo", "Redo", "Clear", "Import Image...",
        "Draw at least one stroke before pressing OK.",
        "Draw one or more strokes. Notes are placed top-to-bottom, then horizontally within each row. Ctrl+Z: undo, Ctrl+Y: redo.",
    },
    "ImageTraceDialog": {
        "Import Image as Drawing", "No image selected", "Choose Image...", "Choose Image",
        "Images (*.png *.jpg *.jpeg *.webp *.bmp)", "Dark Lines", "Light Lines", "Alpha Outline",
        "Trace Mode", "Threshold", "Minimum Outline Length", "Simplification", "Invert",
        "Refresh Preview", "Import into Drawing", "Image tracing failed",
        "Threshold controls which pixels count as lines. Minimum Outline Length removes tiny noise loops. Simplification reduces point count after tracing; increase it for faster Drawing previews and cleaner outlines.",
    },
    "SettingsDialog": {
        "Settings", "General", "Language", "Shortcuts", "Advanced", "Restore Defaults",
        "Confirm before overwriting original beatmap", "Restart Taiko Fancy Arranger to apply the interface language.",
        "Restart required", "Please restart Taiko Fancy Arranger to apply the language change.",
        "Action", "Category", "Shortcut", "Settings storage location:", "Reset all settings",
        "Clear every saved setting and restore defaults?", "Use Reset all settings to clear every saved setting.",
        "Shortcut conflict: {sequence} is assigned to {first} and {second}.",
    },
}

IGNORED_LITERALS = {
    "+", "-", "00:00:000", "x(t)", "y(t)", "⌨", "▶", "❚❚", "⚙",
    "en", "ja", "dark", "light", "alpha", "日本語", "English",
}