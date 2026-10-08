"""Song select: osu!'s star colours, osu!.db's ratings, the scan's new fields,
and the page's Continue row and keyboard."""
from __future__ import annotations

import json
import os
import struct
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

import osu_db
from song_library import read_header
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    import settings as settings_module

    settings_module.APPLICATION_NAME = "TaikoFancyArrangerTests"
    _APP = QApplication.instance() or QApplication([])


def _string(value: str) -> bytes:
    data = value.encode("utf-8")
    assert len(data) < 128  # one ULEB128 byte
    return b"\x0b" + bytes([len(data)]) + data


def _osu_db(version: int, beatmaps: list[tuple[str, str, str, float]]) -> bytes:
    """A minimal osu!.db: (folder, file, md5, taiko nomod stars) per map."""
    single = version >= osu_db.FLOAT_STARS_VERSION
    out = struct.pack("<ii", version, 1) + b"\x01" + bytes(8) + _string("player")
    out += struct.pack("<i", len(beatmaps))
    for folder, osu_file, md5, stars in beatmaps:
        out += b"".join(_string(s) for s in ("A", "", "T", "", "me", "Oni", "a.mp3"))
        out += _string(md5) + _string(osu_file)
        out += b"\x04" + struct.pack("<hhhq", 1, 0, 0, 0) + bytes(16) + bytes(8)
        for mode in range(4):
            if mode == osu_db.TAIKO:
                pairs = [(64, stars + 1), (0, stars)]  # a DT rating first, to be skipped
                out += struct.pack("<i", len(pairs))
                for mods, value in pairs:
                    out += b"\x08" + struct.pack("<i", mods)
                    out += b"\x0c" + struct.pack("<f", value) if single else b"\x0d" + struct.pack("<d", value)
            else:
                out += struct.pack("<i", 0)
        out += bytes(12) + struct.pack("<i", 1) + bytes(17)
        out += bytes(23) + _string("") + _string("") + bytes(2) + _string("") + bytes(10)
        out += _string(folder) + bytes(13) + bytes(5)
    return out


class OsuDbTests(unittest.TestCase):
    def test_both_star_formats(self):
        """Doubles before 20250107, floats from it."""
        for version in (20240101, osu_db.FLOAT_STARS_VERSION):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "osu!.db"
                path.write_bytes(_osu_db(version, [("Song A", "A [Oni].osu", "aa", 5.25),
                                                   ("Song B", "B [Hard].osu", "bb", 2.5)]))
                ratings = osu_db.read(path)
                self.assertEqual(ratings[("song a", "a [oni].osu")].md5, "aa")
                self.assertAlmostEqual(ratings[("song a", "a [oni].osu")].taiko_stars, 5.25, places=5)
                self.assertAlmostEqual(ratings[("song b", "b [hard].osu")].taiko_stars, 2.5, places=5)


class StarColourTests(unittest.TestCase):
    def test_osu_spectrum_stops(self):
        import gui

        self.assertEqual(gui.star_colour(1.25).name(), "#4fc0ff")
        self.assertEqual(gui.star_colour(5.8).name(), "#c645b8")
        self.assertEqual(gui.star_colour(0.05).name(), "#aaaaaa")
        self.assertEqual(gui.star_colour(None).name(), "#aaaaaa")
        self.assertEqual(gui.star_colour(12).name(), "#000000")

    def test_rounds_away_from_zero_before_sampling(self):
        import gui

        self.assertEqual(gui.star_colour(1.245).name(), gui.star_colour(1.25).name())

    def test_text_is_dark_then_gold_then_its_own_spectrum(self):
        import gui

        self.assertEqual(gui.star_text_colour(6.4).alpha(), 191)
        self.assertEqual(gui.star_text_colour(7.0).name(), "#ffd966")
        self.assertEqual(gui.star_text_colour(9.9).name(), "#ff8068")


class BpmTextTests(unittest.TestCase):
    def test_a_subnormal_beat_length_is_infinite_bpm_not_a_crash(self):
        # EGTS 2022 writes `1.14514535393084E-319`: 60000 over it is inf, and
        # round(inf) used to stop the difficulty list at its first chart.
        import gui

        bpm = 60000.0 / 1.14514535393084e-319
        self.assertEqual(gui.format_bpm(180.0, bpm), "180–∞")
        self.assertEqual(gui.format_bpm(bpm, bpm), "∞")
        self.assertEqual(gui.format_bpm(150.2, 149.8), "150")


class SongDotTests(unittest.TestCase):
    def test_a_pack_shows_a_count_of_the_rest(self):
        import gui

        self.assertEqual(gui.song_dots_shown(6, 1000), (6, 0))
        # 1000px allows 20 dots at most, so 40 charts are 18 dots and "+22".
        self.assertEqual(gui.song_dots_shown(40, 1000), (18, 22))
        self.assertEqual(gui.song_dots_shown(20, 1000), (20, 0))
        # A narrow list gives the dots 40% of it: 300px is 8 slots.
        shown, more = gui.song_dots_shown(12, 300)
        self.assertEqual(shown + more, 12)
        self.assertLessEqual(shown, 8)


class ScanFieldTests(unittest.TestCase):
    def test_a_taiko_file_gives_background_bpm_notes_and_length(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_fixture(Path(temp), "full_v14")
            text = path.read_text(encoding="utf-8")
            fields = read_header(path)
            objects = [line for line in text.split("[HitObjects]")[1].splitlines() if line.strip()]
            self.assertEqual(fields["notes"], len(objects))
            times = [int(line.split(",")[2]) for line in objects]
            self.assertEqual(fields["length_ms"], times[-1] - times[0])
            self.assertGreater(fields["bpm_max"], 0)


class PageTests(unittest.TestCase):
    def setUp(self):
        import gui

        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        for name in ("Tester - Test Song", "Other - Second Song"):
            (self.root / name).mkdir()
            write_fixture(self.root / name, "full_v14")
            (self.root / name / "audio.mp3").write_bytes(b"")
        self.stored = {"library/songs_folder": str(self.root)}
        self.window = gui.MainWindow()
        self.window.settings.string_value = lambda key, default="": self.stored.get(key, default)
        self.window.settings.set_value = lambda key, value: self.stored.__setitem__(key, value)
        self.window.settings.sync = lambda: None
        self.window._library_cache_path = lambda: self.root / "index.json"
        self.window._start_scan()
        while self.window._scan_iterator is not None:
            self.window._scan_step()

    def tearDown(self):
        self.window.scan_timer.stop()
        self.window.close()
        self.window.deleteLater()
        self._temp.cleanup()

    def key(self, widget, key, text=""):
        # sendEvent, not widget.event(): only the former passes the filters.
        QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier, text))

    def test_typing_on_the_list_goes_to_the_search(self):
        self.key(self.window.song_list, Qt.Key_Z, "z")
        self.assertEqual(self.window.library_search.text(), "z")

    def test_arrows_in_the_search_move_through_songs(self):
        self.window.song_list.setCurrentRow(0)
        self.key(self.window.library_search, Qt.Key_Down)
        self.assertEqual(self.window.song_list.currentRow(), 1)

    def test_arrows_on_the_list_step_one_song(self):
        self.window.song_list.setCurrentRow(0)
        self.key(self.window.song_list, Qt.Key_Down)
        self.assertEqual(self.window.song_list.currentRow(), 1)
        self.key(self.window.song_list, Qt.Key_Up)
        self.assertEqual(self.window.song_list.currentRow(), 0)

    def test_opening_a_chart_puts_it_in_continue(self):
        library = self.window._library
        library.refresh_continue_row()  # built from the real settings, before the patch
        self.assertTrue(library.continue_row.isHidden())
        self.window.song_list.setCurrentRow(0)
        self.window._open_selected_difficulty()
        recent = json.loads(self.stored["library/recent"])
        self.assertEqual(Path(recent[0][0]), self.window.state.source_path)
        self.assertFalse(library.continue_row.isHidden())
        self.assertEqual(library.continue_cards[0].path, recent[0][0])

    def test_a_continue_card_shows_its_charts_art_and_no_age(self):
        """Owner's call: no "opened N min ago", the chart's background
        very dim behind the title instead."""
        from PySide6.QtGui import QImage
        song = next(Path(self.window.song_list.item(0).data(Qt.UserRole)).glob("*.osu"))
        image = QImage(64, 36, QImage.Format_RGB32)
        image.fill(Qt.red)
        image.save(str(song.parent / "bg.jpg"))  # the fixture's own background
        self.stored["library/recent"] = json.dumps([[str(song), 0.0]])
        self.window._library.refresh_continue_row()
        card = self.window._library.continue_cards[0]
        self.assertIsNotNone(card.art)
        self.assertFalse(hasattr(card, "when"))
        card.resize(240, 60)
        card.grab()  # paints without raising

    def test_a_path_saved_before_open_times_still_lists(self):
        song = next(Path(self.window.song_list.item(0).data(Qt.UserRole)).glob("*.osu"))
        self.stored["library/recent"] = json.dumps([str(song)])
        self.window._library.refresh_continue_row()
        self.assertEqual(self.window._library.continue_cards[0].path, str(song))

    def _last_song_file(self):
        row = self.window.song_list.count() - 1
        return row, next(Path(self.window.song_list.item(row).data(Qt.UserRole)).glob("*.osu"))

    def test_a_continue_card_selects_its_chart_and_does_not_open_it(self):
        row, song = self._last_song_file()
        self.window.song_list.setCurrentRow(0)
        self.window._library.show_recent(str(song))
        from gui import PAGE_LIBRARY
        self.assertEqual(self.window.page_stack.currentIndex(), PAGE_LIBRARY)
        self.assertIsNone(self.window.state)
        self.assertEqual(self.window.song_list.currentRow(), row)
        self.assertEqual(Path(self.window.difficulty_list.currentItem().data(Qt.UserRole)), song)

    def test_a_continue_card_clears_a_search_that_hides_its_song(self):
        row, song = self._last_song_file()
        self.window.library_search.setText("no song is called this")
        self.window._library.show_recent(str(song))
        self.assertEqual(self.window.library_search.text(), "")
        self.assertEqual(Path(self.window.difficulty_list.currentItem().data(Qt.UserRole)), song)

    def test_no_osu_db_says_so_and_rates_nothing(self):
        library = self.window._library
        self.assertIsNone(library.star_ratings)
        self.assertFalse(library.library_rating_note.isHidden())
        self.window.song_list.setCurrentRow(0)
        row = self.window.difficulty_list.item(0).data(Qt.UserRole + 1)
        self.assertIsNone(row["stars"])

    def test_a_rating_goes_stale_when_the_file_changes(self):
        folder = Path(self.window.song_list.item(0).data(Qt.UserRole))
        song = next(folder.glob("*.osu"))
        db = self.root / "osu!.db"
        for md5, stale in ((osu_db.file_md5(song), False), ("0" * 32, True)):
            with self.subTest(stale=stale):
                self.window._library.star_ratings = osu_db.read(self._write_db(db, song, md5))
                self.window._library.song_selected(0)
                row = self.window.difficulty_list.item(0).data(Qt.UserRole + 1)
                self.assertAlmostEqual(row["stars"], 4.5, places=5)
                self.assertEqual(row["stale"], stale)

    def test_the_preview_follows_the_difficultys_own_audio(self):
        """One folder, two songs: each difficulty previews its own file."""
        folder = Path(self.window.song_list.item(0).data(Qt.UserRole))
        first = next(folder.glob("*.osu"))
        (folder / "other.mp3").write_bytes(b"")
        (folder / "other.osu").write_bytes(first.read_bytes()
                                            .replace(b"AudioFilename: audio.mp3", b"AudioFilename: other.mp3")
                                            .replace(b"Version:Oni", b"Version:Zzz"))
        self.window._start_scan()
        while self.window._scan_iterator is not None:
            self.window._scan_step()
        row = next(i for i in range(self.window.song_list.count())
                   if self.window.song_list.item(i).data(Qt.UserRole) == str(folder))
        self.window._library.song_selected(row)
        queued = {}
        for index in range(self.window.difficulty_list.count()):
            self.window.difficulty_list.setCurrentRow(index)
            queued[self.window.difficulty_list.item(index).text()] = self.window._library._preview_pending[0].name
        self.assertEqual(queued, {"Oni": "audio.mp3", "Zzz": "other.mp3"})

    def test_the_banner_follows_the_difficultys_own_background(self):
        """Same folder, a background per difficulty: the art is the selected
        one's, and moving between two that share one reloads nothing."""
        folder = Path(self.window.song_list.item(0).data(Qt.UserRole))
        first = next(folder.glob("*.osu"))
        (folder / "other.osu").write_bytes(first.read_bytes()
                                            .replace(b'0,0,"bg.jpg"', b'0,0,"other.jpg"')
                                            .replace(b"Version:Oni", b"Version:Zzz"))
        self.window._start_scan()
        while self.window._scan_iterator is not None:
            self.window._scan_step()
        row = next(i for i in range(self.window.song_list.count())
                   if self.window.song_list.item(i).data(Qt.UserRole) == str(folder))
        library = self.window._library
        library.song_selected(row)
        shown = {}
        for index in range(self.window.difficulty_list.count()):
            self.window.difficulty_list.setCurrentRow(index)
            shown[self.window.difficulty_list.item(index).text()] = library._banner_art_key[1]
        self.assertEqual(shown, {"Oni": "bg.jpg", "Zzz": "other.jpg"})

    def test_the_beat_line_keeps_each_difficultys_own_tempo(self):
        """Same folder, different audio and timing: the beat is the selected
        difficulty's red lines, not the first difficulty's BPM."""
        folder = Path(self.window.song_list.item(0).data(Qt.UserRole))
        first = next(folder.glob("*.osu"))
        (folder / "other.mp3").write_bytes(b"")
        (folder / "other.osu").write_bytes(first.read_bytes()
                                            .replace(b"AudioFilename: audio.mp3", b"AudioFilename: other.mp3")
                                            .replace(b"0,500,4,1,0,60,1,0", b"0,300,4,1,0,60,1,0")
                                            .replace(b"6000,400,4,1,0,70,1,8", b"6000,250,4,1,0,70,1,8")
                                            .replace(b"Version:Oni", b"Version:Zzz"))
        self.window._start_scan()
        while self.window._scan_iterator is not None:
            self.window._scan_step()
        row = next(i for i in range(self.window.song_list.count())
                   if self.window.song_list.item(i).data(Qt.UserRole) == str(folder))
        self.window._library.song_selected(row)
        banner = self.window._library.song_banner
        beats = {}
        for index in range(self.window.difficulty_list.count()):
            self.window.difficulty_list.setCurrentRow(index)
            beats[self.window.difficulty_list.item(index).text()] = [beat for _t, beat in banner._tempo]
        self.assertEqual(beats, {"Oni": [500.0, 400.0], "Zzz": [300.0, 250.0]})

    def test_the_beat_is_phased_from_the_red_line_in_force(self):
        banner = self.window._library.song_banner
        banner.set_tempo([(0.0, 500.0), (1000.0, 400.0)], lambda: 0.0)
        self.assertAlmostEqual(banner.beat_phase_at(250.0), 0.5)
        self.assertAlmostEqual(banner.beat_phase_at(1200.0), 0.5)   # 200 into a 400ms beat
        # Past 300 BPM the flash halves until it is a pulse, not a flicker.
        banner.set_tempo([(0.0, 100.0)], lambda: 0.0)
        self.assertAlmostEqual(banner.beat_phase_at(100.0), 0.5)

    def _record_ui_sounds(self):
        played = []
        self.window._library.play_ui_sound = played.append
        return played

    def test_a_new_song_expands_and_a_new_difficulty_clicks(self):
        played = self._record_ui_sounds()
        self.window.song_list.setCurrentRow(0)
        self.key(self.window.song_list, Qt.Key_Down)
        # The first difficulty coming up with the song is not a second pick.
        self.assertEqual(played, ["select_expand", "select_expand"])
        folder = Path(self.window.song_list.currentItem().data(Qt.UserRole))
        first = next(folder.glob("*.osu"))
        (folder / "other.osu").write_bytes(first.read_bytes().replace(b"Version:Oni", b"Version:Zzz"))
        self.window._start_scan()
        while self.window._scan_iterator is not None:
            self.window._scan_step()
        played.clear()
        self.window.difficulty_list.setCurrentRow(1)
        self.assertEqual(played, ["select_difficulty"])

    def test_a_search_that_keeps_the_song_is_silent(self):
        self.window.song_list.setCurrentRow(0)
        played = self._record_ui_sounds()
        self.window._library.rebuild_song_list()
        self.assertEqual(played, [])

    def _current_folder(self):
        return self.window.song_list.currentItem().data(Qt.UserRole)

    def test_the_player_order_is_fixed_for_the_session(self):
        library = self.window._library
        first = list(library.song_order())
        self.assertEqual(sorted(first), sorted(library.library_songs))
        self.assertEqual(library.song_order(), first)
        library.library_songs[self.root / "Late - Addition"] = [object()]
        self.assertEqual(library.song_order()[:-1], first, "a late song joins the end; nothing moves")
        self.assertEqual(library.song_order()[-1], self.root / "Late - Addition")

    def test_next_selects_the_song_and_previous_comes_back(self):
        library = self.window._library
        library.song_order()
        self.window.song_list.setCurrentRow(0)
        start = self._current_folder()
        library.step_song(1)
        moved = self._current_folder()
        self.assertNotEqual(moved, start)
        self.assertEqual(Path(moved), library.song_order()[(library.song_order().index(Path(start)) + 1) % 2])
        library.step_song(-1)
        self.assertEqual(self._current_folder(), start)

    def test_the_next_button_steps(self):
        library = self.window._library
        self.window.song_list.setCurrentRow(0)
        start = self._current_folder()
        library.player_buttons["next"].click()
        self.assertNotEqual(self._current_folder(), start)

    def test_space_toggles_the_preview_but_types_inside_a_query(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        library = self.window._library
        toggles = []
        library.toggle_preview = lambda: toggles.append(True)

        def space(widget):
            return library.keys.eventFilter(widget, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " "))

        self.assertTrue(space(self.window.song_list))
        self.assertTrue(space(library.library_search))
        library.library_search.setText("yotsuya")
        self.assertFalse(space(library.library_search))
        self.assertEqual(len(toggles), 2)

    def test_space_on_the_song_list_after_editing_is_the_song_lists(self):
        """The Space shortcut is application-wide, so on the song list it fires
        before `LibraryKeys` sees the key. With a difficulty opened earlier it
        used to restart the editor's song from the editor's playhead."""
        import gui

        library = self.window._library
        toggles, editor = [], []
        library.toggle_preview = lambda: toggles.append(True)
        self.window.toggle_playback = lambda: editor.append(True)
        self.window._show_page(gui.PAGE_LIBRARY)
        # A difficulty was opened earlier: `document` reads it from the state.
        self.window.state = types.SimpleNamespace(document=object())
        self.window.play_shortcut.activated.emit()
        self.assertEqual((len(toggles), len(editor)), (1, 0))

    def test_the_beat_stripe_is_a_hidden_seek_bar(self):
        """Owner's easter egg: the stripe's whole width is the whole song,
        and nothing on screen says so."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent

        banner = self.window._library.song_banner
        banner.resize(400, 200)
        seen = []
        banner.seek_fraction.connect(seen.append)

        def press(x, y):
            event = QMouseEvent(QEvent.MouseButtonPress, QPointF(x, y), QPointF(x, y),
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            banner.mousePressEvent(event)
            release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(x, y), QPointF(x, y),
                                  Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
            banner.mouseReleaseEvent(release)

        press(100, 50)
        self.assertEqual(seen, [], "the art above the stripe is not a seek bar")
        press(100, 195)
        self.assertEqual(seen, [0.25])
        banner.resize(800, 200)  # the splitter widened the pane
        press(100, 195)
        self.assertEqual(seen[-1], 0.125)

    def test_a_fraction_seeks_the_song_lists_player_only(self):
        import gui

        library = self.window._library
        seeks = []

        class Player:
            def duration(self):
                return 200000
            def setPosition(self, ms):
                seeks.append(ms)
            def playbackState(self):
                return gui.QMediaPlayer.PausedState
            def position(self):
                return seeks[-1] if seeks else 0

        library.preview_player = Player()
        library._seek_preview_fraction(0.5)
        self.assertEqual(seeks, [100000])
        self.assertEqual(library.chart_preview.current_time, 100000)
        library.preview_player = None

    def test_the_chart_preview_shows_the_selected_difficulty(self):
        import gui

        library = self.window._library
        self.window.song_list.setCurrentRow(0)
        library.difficulty_changed()
        library._load_chart_preview()
        self.assertTrue(library.chart_preview.notes)
        library.chart_preview.resize(600, 300)
        natural = gui.osu_screen_height_for(600)
        self.assertEqual(library.chart_preview.height(), round(natural * 0.67))
        self.assertFalse(library.chart_preview.follows_view_opacity)

    def test_stop_with_nothing_playing_is_harmless(self):
        self.window._library.rewind_preview()

    def _write_db(self, db: Path, song: Path, md5: str) -> Path:
        db.write_bytes(_osu_db(osu_db.FLOAT_STARS_VERSION, [(song.parent.name, song.name, md5, 4.5)]))
        return db


if __name__ == "__main__":
    unittest.main()
