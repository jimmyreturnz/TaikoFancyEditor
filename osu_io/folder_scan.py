from dataclasses import dataclass
from pathlib import Path

from osu_io.parser import parse_osu


@dataclass(frozen=True, slots=True)
class DifficultyInfo:
    path: Path
    version: str
    audio_filename: str


def scan_song_folder(folder: str | Path) -> list[DifficultyInfo]:
    folder_path = Path(folder).resolve()
    difficulties: list[DifficultyInfo] = []

    for osu_path in sorted(folder_path.glob("*.osu"), key=lambda p: p.name.lower()):
        document = parse_osu(osu_path)
        difficulties.append(
            DifficultyInfo(
                path=osu_path,
                version=document.version or osu_path.stem,
                audio_filename=document.audio_filename,
            )
        )

    return difficulties