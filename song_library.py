"""Index every .osu under an osu! Songs folder, keeping only taiko charts.

No Qt import on purpose (same rule as `osu_io` / `model`): the scan is a plain
generator so the caller decides how much of it to run per UI frame, and the
cache is plain JSON so it can be inspected and deleted by hand.

`parse_osu` is deliberately *not* reused here. It reads every hit object of
every file; a real Songs folder is tens of thousands of files and the only
fields the browser needs are five header lines.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

CACHE_VERSION = 2
TAIKO_MODE = "1"

# Header keys we index. Everything else in [General]/[Metadata] is ignored.
# The Unicode pair is what osu! stores the song's own-language metadata in;
# the plain pair is the romanized one.
_WANTED = {
    "Mode": "mode",
    "Artist": "artist",
    "ArtistUnicode": "artist_unicode",
    "Title": "title",
    "TitleUnicode": "title_unicode",
    "Version": "version",
    "Creator": "creator",
    "Tags": "tags",
}
# Cache entry layout after [mtime, size]: keeps the JSON compact and maps
# straight onto TaikoDifficulty's fields (mode excepted, which only decides
# whether an entry becomes one).
_FIELD_ORDER = ("mode", "artist", "artist_unicode", "title", "title_unicode", "version", "creator", "tags")
# First section that can never hold one of the above; the file is huge past it.
_STOP_SECTIONS = {"[Events]", "[TimingPoints]", "[HitObjects]", "[Colours]", "[Difficulty]"}


@dataclass(frozen=True, slots=True)
class TaikoDifficulty:
    path: Path
    artist: str
    artist_unicode: str
    title: str
    title_unicode: str
    version: str
    creator: str
    tags: str

    @property
    def folder(self) -> Path:
        return self.path.parent

    def display_artist(self, original: bool = False) -> str:
        """Romanized by default; the song's own script when asked for it.

        Either field can be empty in a real map, so each falls back to the
        other rather than showing a blank where a name belongs.
        """
        pair = (self.artist_unicode, self.artist) if original else (self.artist, self.artist_unicode)
        return next((value for value in pair if value), "")

    def display_title(self, original: bool = False) -> str:
        pair = (self.title_unicode, self.title) if original else (self.title, self.title_unicode)
        return next((value for value in pair if value), self.folder.name)

    def song_label(self, original: bool = False) -> str:
        artist = self.display_artist(original)
        title = self.display_title(original)
        return f"{artist} - {title}" if artist else title

    def search_text(self) -> str:
        """Everything the library's search box matches against, lowercased."""
        return " ".join((
            self.artist, self.artist_unicode, self.title, self.title_unicode,
            self.version, self.creator, self.tags,
        )).lower()


def read_header(path: Path) -> dict[str, str] | None:
    """Return the indexed header fields, or None when the file cannot be read.

    Decoding is lenient (`errors="replace"`): these strings are only ever
    displayed in the browser, and the real load path re-reads the file through
    `parse_osu`, which does proper cp1252 fallback.
    """
    fields = dict.fromkeys(_FIELD_ORDER, "")
    fields["mode"] = "0"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped in _STOP_SECTIONS:
                    break
                key, separator, value = stripped.partition(":")
                if separator and key in _WANTED:
                    fields[_WANTED[key]] = value.strip()
    except OSError:
        return None
    return fields


def scan(root: Path, cache: dict[str, list]) -> Iterator[TaikoDifficulty | None]:
    """Walk `root`, yielding one item per .osu file: the taiko ones, else None.

    A generator, so the caller can run it a few milliseconds at a time and keep
    the window responsive. `cache` is mutated in place (mtime + size keyed) and
    belongs to the caller to persist; entries for vanished files are dropped
    once the walk finishes.
    """
    root = Path(root)
    seen: set[str] = set()
    for path in root.rglob("*.osu"):
        key = str(path)
        seen.add(key)
        try:
            stat = path.stat()
        except OSError:
            continue
        entry = cache.get(key)
        if not (entry and entry[0] == stat.st_mtime_ns and entry[1] == stat.st_size):
            header = read_header(path)
            if header is None:
                continue
            entry = [stat.st_mtime_ns, stat.st_size] + [header[name] for name in _FIELD_ORDER]
            cache[key] = entry
        # entry[2] is the mode; everything after it lines up with
        # TaikoDifficulty's fields, in _FIELD_ORDER.
        yield TaikoDifficulty(path, *entry[3:]) if entry[2] == TAIKO_MODE else None
    for stale in set(cache) - seen:
        del cache[stale]


def songs_from_cache(cache: dict[str, list], root: Path) -> Iterator[TaikoDifficulty]:
    """Rebuild a previous scan's taiko entries without touching the disk.

    What makes the second start instant: the list is on screen before the
    verification walk begins. Entries are filtered by `root` because the cache
    outlives a change of songs folder.
    """
    root = Path(root)
    for key, entry in cache.items():
        if entry[2] != TAIKO_MODE:
            continue
        path = Path(key)
        if root in path.parents:
            yield TaikoDifficulty(path, *entry[3:])


def load_cache(path: Path) -> dict[str, list]:
    """Read the on-disk index, or start empty when it is missing or unusable."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if payload.get("version") != CACHE_VERSION:
        return {}
    return payload.get("files", {})


def save_cache(path: Path, cache: dict[str, list]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": CACHE_VERSION, "files": cache}),
        encoding="utf-8",
    )


def group_by_song(difficulties) -> dict[Path, list[TaikoDifficulty]]:
    """Group difficulties by their folder -- one folder is one song."""
    songs: dict[Path, list[TaikoDifficulty]] = {}
    for difficulty in difficulties:
        songs.setdefault(difficulty.folder, []).append(difficulty)
    return songs
