"""Take the README's screenshots from the running app.

    python tools/readme_screenshots.py <map.osu> [out_dir] [--at MS]

Real platform (the offscreen one has no fonts), at 1920x1080. Writes nothing
of the user's: settings come from the test suite's private copy (importing
`tests` swaps it in), and the song index and gimmick index are read from
temporary copies. Nothing is saved to the map.

The README uses Hyper Bass (RENKA chan Drop) [Drop the GIMMICK], which has
every gimmick the editor writes, at 1:55.8 (a kiai with shiny notes).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tests  # noqa: F401,E402  -- private settings, before any window exists

from PySide6.QtCore import QStandardPaths  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import gui  # noqa: E402
from settings import APPLICATION_NAME, ORGANIZATION_NAME  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
MAP = Path(args[0]).resolve()
OUT = Path(args[1]) if len(args) > 1 else ROOT / "docs" / "images"
AT_MS = float(sys.argv[sys.argv.index("--at") + 1]) if "--at" in sys.argv else 115800.0
OUT.mkdir(parents=True, exist_ok=True)

app = QApplication.instance() or QApplication([])
app.setOrganizationName(ORGANIZATION_NAME)
app.setApplicationName(APPLICATION_NAME)

# The real song index and gimmick index, copied, so a warm scan and the map's
# pairing are there and nothing writes back.
scratch = Path(tempfile.mkdtemp(prefix="taiko-readme-"))
real_root = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
copies = {}
for name in ("song_index.json", "gimmick_index.json"):
    source = real_root / name
    copies[name] = scratch / name
    if source.is_file():
        shutil.copy2(source, copies[name])
gui.MainWindow._library_cache_path = lambda self: copies["song_index.json"]
gui.MainWindow._gimmick_index_path = lambda self: copies["gimmick_index.json"]


class _UseCurrent:
    """Answers the gimmick entry question with "use this difficulty"."""

    CREATE = gui.GimmickEntryDialog.CREATE
    USE_CURRENT = gui.GimmickEntryDialog.USE_CURRENT
    CANCEL = gui.GimmickEntryDialog.CANCEL
    reference = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def exec(self) -> int:
        return 1

    def selected_action(self) -> str:
        return self.USE_CURRENT

    def selected_reference(self):
        return None

    def deleteLater(self) -> None:
        pass


def settle(rounds: int = 12, wait_ms: int = 0) -> None:
    """Let the window catch up. `wait_ms` is real time, for what animates
    or loads on a timer: the notes' glide, the banner's crossfade."""
    for _ in range(rounds):
        app.processEvents()
    if wait_ms:
        QTest.qWait(wait_ms)


def shoot(widget, name: str) -> None:
    settle()
    path = OUT / f"{name}.png"
    widget.grab().save(str(path))
    print(f"{path.name}: {path.stat().st_size // 1024} KB", flush=True)


window = gui.MainWindow()
window.resize(1920, 1080)
window.showMaximized()
settle()

# -- song select ---------------------------------------------------------------
window.start_library()
library = window._library
for _ in range(20000):
    if getattr(library, "_scan_iterator", None) is None:
        break
    library.scan_step()
settle()
for row in range(window.song_list.count()):
    folder = window.song_list.item(row).data(gui.Qt.UserRole)
    if folder and Path(folder).resolve() == MAP.parent:
        window.song_list.setCurrentRow(row)
        break
settle(40, wait_ms=1500)
library.stop_preview()
shoot(window, "song-select")

# -- editor, with the gameplay preview under the chart and SV ---------------------
window._load_map_path(MAP)
window._show_page(gui.PAGE_EDITOR)
window._add_editor_view("gameplay", MAP)
window.seek_audio(AT_MS)
shoot(window, "editor")

# -- gimmick editor ----------------------------------------------------------------
original_entry = gui.GimmickEntryDialog
gui.GimmickEntryDialog = _UseCurrent
try:
    if window._enter_gimmick_page():
        window._show_page(gui.PAGE_GIMMICK)
        window.seek_audio(AT_MS)
        shoot(window, "gimmick-editor")
finally:
    gui.GimmickEntryDialog = original_entry

# -- fancy arranger: a run of notes as a star --------------------------------------
window._show_page(gui.PAGE_FANCY)
state = window.state
window.seek_audio(AT_MS - 20000)
chosen = {
    note.original_index for note in state.document.hit_objects
    if note.is_circle and AT_MS - 21500 <= note.time <= AT_MS - 18000
}
window._selection_changed(chosen)
page = window.control_tabs.widget(0)
page.combo.setCurrentIndex(page.combo.findData("star"))
settle(30, wait_ms=1200)
shoot(window, "fancy-arranger")

# -- dialogs -----------------------------------------------------------------------
config = window._gimmick_config("barline")
dialogs = {
    "config-barline": lambda: gui.GimmickConfigDialog(config, "barline", window._gimmick_config("fake_slider"), window),
    "convert-notes": lambda: gui.ConvertNotesDialog(config, "barline", window, AT_MS - 4000, AT_MS),
    "generate-sv": lambda: gui.SVFunctionDialog(AT_MS - 4000, AT_MS, window, initial_rate=1.0, final_rate=2.5),
    "settings": lambda: gui.SettingsDialog(window.settings, window.shortcuts, window),
}
for name, make in dialogs.items():
    dialog = make()
    if name == "generate-sv":
        dialog.function_buttons["sin_in"].setChecked(True)
    if name == "settings":
        dialog.nav.setCurrentRow(1)
    if name == "config-barline":
        dialog.show()
        settle()
        dialog.kat_spacing2_spin.setFocus()
    dialog.show()
    shoot(dialog, name)
    dialog.close()

shutil.rmtree(scratch, ignore_errors=True)
# The audio engine's thread would hold the process open; nothing here needs a
# clean shutdown.
os._exit(0)
