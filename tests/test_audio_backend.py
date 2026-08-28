"""Which multimedia backend the app asks Qt for, and when it stops asking.

Qt's FFmpeg backend reports playback position in fixed ~93ms steps of song
time, so at 0.25x the editor gets one true reading every 351ms of wall clock
and the playhead drifts against the music. Windows Media Foundation reports at
1ms granularity with a measured 0.00% rate error -- but has no Ogg Vorbis
decoder without a system codec, and osu! song folders are full of .ogg.

So the accurate backend is a preference that gives way on first refusal. These
tests cover the giving way, and the three ways it must NOT give way: a missing
file, an unrelated error, and a preference already switched.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

import gui
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


class FakeSettings:
    """Stands in for SettingsManager so tests never touch real QSettings."""

    def __init__(self, **values: str) -> None:
        self.values = dict(values)
        self.synced = False

    def string_value(self, key: str, default: str = "") -> str:
        return str(self.values.get(key, default))

    def set_value(self, key: str, value) -> None:
        self.values[key] = value

    def sync(self) -> None:
        self.synced = True


class BackendSelectionTests(unittest.TestCase):
    """select_media_backend runs before QApplication, so it touches only os.environ."""

    def setUp(self) -> None:
        self._saved = os.environ.get("QT_MEDIA_BACKEND")
        os.environ.pop("QT_MEDIA_BACKEND", None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("QT_MEDIA_BACKEND", None)
        else:
            os.environ["QT_MEDIA_BACKEND"] = self._saved

    def test_an_explicit_environment_variable_wins(self):
        """The documented escape hatch: it must beat a stored preference too."""
        os.environ["QT_MEDIA_BACKEND"] = "gstreamer"
        chosen = gui.select_media_backend(FakeSettings(**{"audio/backend": "ffmpeg"}))

        self.assertEqual(chosen, "gstreamer")
        self.assertEqual(os.environ["QT_MEDIA_BACKEND"], "gstreamer")

    def test_a_stored_preference_is_applied(self):
        chosen = gui.select_media_backend(FakeSettings(**{"audio/backend": "ffmpeg"}))

        self.assertEqual(chosen, "ffmpeg")
        self.assertEqual(os.environ["QT_MEDIA_BACKEND"], "ffmpeg")

    @unittest.skipUnless(os.name == "nt", "the accurate backend is Windows-only")
    def test_no_preference_asks_for_the_accurate_backend(self):
        chosen = gui.select_media_backend(FakeSettings())

        self.assertEqual(chosen, gui.ACCURATE_MEDIA_BACKEND)
        self.assertEqual(os.environ["QT_MEDIA_BACKEND"], gui.ACCURATE_MEDIA_BACKEND)


class BackendFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("QT_MEDIA_BACKEND")
        os.environ["QT_MEDIA_BACKEND"] = gui.ACCURATE_MEDIA_BACKEND

        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")

        self.window = gui.MainWindow()
        self.window.settings = FakeSettings()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()
        if self._saved is None:
            os.environ.pop("QT_MEDIA_BACKEND", None)
        else:
            os.environ["QT_MEDIA_BACKEND"] = self._saved

    def _stored(self) -> str:
        return self.window.settings.string_value(gui.MEDIA_BACKEND_SETTING, "")

    def test_a_format_error_on_a_real_file_switches_the_preference(self):
        self.window._audio_backend_failed(QMediaPlayer.FormatError, "no decoder")

        self.assertEqual(self._stored(), gui.COMPATIBLE_MEDIA_BACKEND)
        self.assertTrue(self.window.settings.synced, "the switch must survive the crash")

    def test_a_missing_audio_file_is_not_the_backend_s_fault(self):
        """A renamed or deleted song raises the same error and must not cost
        accuracy for every other song in the library."""
        self.window.state.audio_path.unlink()
        self.window._audio_backend_failed(QMediaPlayer.ResourceError, "gone")

        self.assertEqual(self._stored(), "")

    def test_an_unrelated_error_changes_nothing(self):
        self.window._audio_backend_failed(QMediaPlayer.NetworkError, "offline")

        self.assertEqual(self._stored(), "")

    def test_nothing_happens_while_the_compatible_backend_is_running(self):
        """Otherwise every undecodable file on ffmpeg would rewrite the
        setting and toast about a restart that would change nothing."""
        os.environ["QT_MEDIA_BACKEND"] = gui.COMPATIBLE_MEDIA_BACKEND
        self.window._audio_backend_failed(QMediaPlayer.FormatError, "no decoder")

        self.assertEqual(self._stored(), "")


if __name__ == "__main__":
    unittest.main()
