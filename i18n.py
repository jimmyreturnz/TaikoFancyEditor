from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTranslator

from settings import SettingsManager

_TRANSLATOR: QTranslator | None = None


def resource_roots() -> list[Path]:
    roots: list[Path] = []
    if hasattr(sys, "_MEIPASS"):
        roots.append(Path(sys._MEIPASS).resolve())
    roots.append(Path(__file__).resolve().parent)
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def translation_path(language_code: str) -> Path | None:
    if language_code == "en":
        return None
    filename = f"taiko_{language_code}.qm"
    for root in resource_roots():
        candidate = root / "translations" / filename
        if candidate.is_file():
            return candidate
    return None


def install_translator(app, settings: SettingsManager | None = None) -> str:
    """Install the persisted translator. Missing translations safely fall back to English."""
    global _TRANSLATOR
    settings = settings or SettingsManager()
    language = settings.string_value("language/current", "en")
    if language not in {"en", "ja"}:
        language = "en"
    if _TRANSLATOR is not None:
        app.removeTranslator(_TRANSLATOR)
        _TRANSLATOR = None
    path = translation_path(language)
    if path is None:
        return "en"
    translator = QTranslator(app)
    if translator.load(str(path)):
        app.installTranslator(translator)
        _TRANSLATOR = translator
        return language
    return "en"


def tr(context: str, text: str) -> str:
    return QCoreApplication.translate(context, text)
