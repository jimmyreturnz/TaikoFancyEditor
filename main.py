from collections import Counter
from pathlib import Path
import sys

from osu_io.folder_scan import scan_song_folder
from osu_io.parser import parse_osu
from osu_io.writer import write_osu


def choose_difficulty(folder: Path):
    difficulties = scan_song_folder(folder)
    if not difficulties:
        raise FileNotFoundError(f"No .osu files found in: {folder}")

    print("\nAvailable difficulties:")
    for index, item in enumerate(difficulties, start=1):
        audio = f" | audio: {item.audio_filename}" if item.audio_filename else ""
        print(f"  {index}. {item.version}{audio}")

    while True:
        value = input("\nChoose difficulty number: ").strip()
        try:
            selected = int(value) - 1
            return difficulties[selected]
        except (ValueError, IndexError):
            print("Invalid selection.")


def print_summary(document) -> None:
    counts = Counter(note.note_kind for note in document.hit_objects)
    print(f"\nDifficulty: {document.version}")
    print(f"Audio: {document.audio_filename or '(not specified)'}")
    print(f"Hit objects: {len(document.hit_objects)}")
    print(f"Don: {counts['don']}")
    print(f"Kat: {counts['kat']}")
    print(f"Big don: {counts['big_don']}")
    print(f"Big kat: {counts['big_kat']}")


def unique_output_path(source: Path, new_version: str) -> Path:
    safe_version = "".join(
        character if character not in '<>:"/\\|?*' else "_"
        for character in new_version
    ).strip()
    candidate = source.with_name(f"{source.stem} [{safe_version}].osu")
    number = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem} [{safe_version}] ({number}).osu")
        number += 1
    return candidate


def main() -> None:
    folder_text = sys.argv[1] if len(sys.argv) > 1 else input("Song folder: ").strip().strip('"')
    folder = Path(folder_text).expanduser().resolve()
    selected = choose_difficulty(folder)
    document = parse_osu(selected.path)
    print_summary(document)

    suffix = input("\nNew difficulty suffix [(patterned)]: ").strip() or "(patterned)"
    new_version = f"{document.version} {suffix}".strip()
    output_path = unique_output_path(document.source_path, new_version)
    write_osu(document, output_path, new_version)
    print(f"\nExported safely to:\n{output_path}")


if __name__ == "__main__":
    main()