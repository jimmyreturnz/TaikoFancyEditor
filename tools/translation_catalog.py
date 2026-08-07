from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXTS = ("MainWindow", "Parameters", "DrawingDialog", "ImageTraceDialog", "SettingsDialog", "Transformations")
SOURCE_FILES = ("gui.py", "gui_draft.py", "settings_dialog.py", "image_trace_dialog.py")
FIXED = {
    "Open .osu", "Play", "Pause", "Reset applied transforms", "Export applied map",
    "Difficulty", "Playback Rate", "Background Opacity", "Transformation mode",
    "All Notes", "Split Don / Kat", "Swap Don ↔ Kat", "Transform selected notes",
    "Apply all changes to original file", "Beat snap", "Duration", "Now", "Snap",
    "Wheel: seek", "Shift+wheel: 1 beat", "Ctrl+wheel: zoom", "All", "Don", "Kat",
    "None", "Position X", "Position Y", "Traversal", "Font", "Random Seed",
    "Column", "Columns", "Min BPM", "Max BPM", "Minimum BPM", "Maximum BPM",
    "Step Size", "Chunk", "Chunk Size", "Notes per Chunk", "Max Turn", "Maximum Turn",
    "Open Drawing Window", "Import Image...", "Undo", "Redo", "Clear", "Drawing",
    "Dark Lines", "Light Lines", "Alpha Outline", "Trace Mode", "Threshold",
    "Minimum Outline Length", "Simplification", "Invert", "Refresh Preview",
    "Import into Drawing", "Settings", "General", "Language", "Shortcuts", "Advanced",
}
UI_CALLS = {"QLabel", "QPushButton", "QCheckBox", "setText", "setToolTip", "setWindowTitle", "setPlaceholderText", "addItem", "addTab"}


def literal_strings(tree: ast.AST) -> set[str]:
    values = set(FIXED)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            pairs = {}
            for key, value in zip(node.keys, node.values):
                try: pairs[ast.literal_eval(key)] = value
                except Exception: pass
            for field in ("label",):
                try:
                    value = ast.literal_eval(pairs[field])
                    if isinstance(value, str) and value.strip(): values.add(value)
                except Exception: pass
            try:
                for choice in ast.literal_eval(pairs["choices"]):
                    if isinstance(choice, (tuple, list)) and choice and isinstance(choice[0], str): values.add(choice[0])
            except Exception: pass
        if isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name): name = node.func.id
            elif isinstance(node.func, ast.Attribute): name = node.func.attr
            if name in UI_CALLS:
                for arg in node.args[:2]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.strip(): values.add(arg.value)
    return values


def main() -> None:
    strings = set(FIXED)
    for filename in SOURCE_FILES:
        path = ROOT / filename
        if path.exists(): strings.update(literal_strings(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    out = ROOT / "translations" / "generated_strings.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ['"""Generated translation markers. Do not edit."""', 'from PySide6.QtCore import QCoreApplication', '', 'def mark_all():']
    for context in CONTEXTS:
        for text in sorted(strings, key=str.casefold):
            lines.append(f'    QCoreApplication.translate({json.dumps(context)}, {json.dumps(text, ensure_ascii=False)})')
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {len(strings)} strings across {len(CONTEXTS)} contexts.")

if __name__ == "__main__": main()