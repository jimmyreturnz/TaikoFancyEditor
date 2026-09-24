"""Keep the test suite out of the real user's settings.

`SettingsManager` is the app's own QSettings, and any test that moves a control
wired to it -- the view height, the note opacity -- wrote straight into the
real registry. The suite ended one run with the owner's Editor opening at 90px
views, and which test did it was not obvious because every file runs in its own
process in parallel.

So each test process gets a private INI file, seeded once from the real
settings. Seeded rather than empty: an empty store is a first launch, and a
first launch asks for a language and a Songs folder through modal dialogs that
would hang an offscreen run -- and the suite has always run against the
owner's real skin and preferences, which is what its expectations were written
against. Reads behave exactly as before; writes land in the copy and vanish
with the process.

Every `tests.test_*` module imports this package first, so the swap is in place
before any window is built.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

from PySide6.QtCore import QSettings

import settings as _settings

_DIRECTORY = tempfile.mkdtemp(prefix="taiko-tests-settings-")
atexit.register(shutil.rmtree, _DIRECTORY, True)
_PATH = os.path.join(_DIRECTORY, "settings.ini")

_real = QSettings(_settings.ORGANIZATION_NAME, _settings.APPLICATION_NAME)
_copy = QSettings(_PATH, QSettings.IniFormat)
for _key in _real.allKeys():
    _copy.setValue(_key, _real.value(_key))
_copy.sync()
del _real, _copy


def _private_settings(self) -> None:
    self._settings = QSettings(_PATH, QSettings.IniFormat)


_settings.SettingsManager.__init__ = _private_settings
