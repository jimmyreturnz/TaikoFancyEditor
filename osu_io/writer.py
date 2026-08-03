from pathlib import Path
import os
import shutil
import tempfile
import math
from osu_io.parser import OsuDocument, parse_osu


def _ending(line):
    return "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""


def _replace_key(line, value):
    end = _ending(line); content = line[:-len(end)] if end else line
    return content.split(":", 1)[0] + ":" + str(value) + end


def _replace_xy(line, x, y):
    end = _ending(line); content = line[:-len(end)] if end else line; fields = content.split(",")
    fields[0], fields[1] = str(x), str(y)
    return ",".join(fields) + end


def write_osu(document, destination, new_version, *, allow_overwrite_source=False, force_ar=10, force_cs=7, create_backup=False):
    destination = Path(destination).resolve(); source = document.source_path.resolve()
    if destination.suffix.lower() != ".osu":
        raise ValueError("Destination must be an .osu file")
    force_ar=float(force_ar);force_cs=float(force_cs)
    if not math.isfinite(force_ar) or not 0.0 <= force_ar <= 10.0:
        raise ValueError("ApproachRate must be between 0 and 10")
    if not math.isfinite(force_cs) or not 0.0 <= force_cs <= 10.0:
        raise ValueError("CircleSize must be between 0 and 10")
    if "\n" in str(new_version) or "\r" in str(new_version):
        raise ValueError("Difficulty version must be one line")
    if destination == source and not allow_overwrite_source:
        raise FileExistsError("Refusing to overwrite source")
    if destination.exists() and destination != source:
        raise FileExistsError(f"Destination exists: {destination}")
    lines = document.lines.copy(); section = ""; found_version = found_ar = found_cs = False
    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n").strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]; continue
        raw = line.rstrip("\r\n")
        if section == "Metadata" and raw.startswith("Version:"):
            lines[index] = _replace_key(line, new_version); found_version = True
        elif section == "Difficulty" and raw.startswith("ApproachRate:"):
            lines[index] = _replace_key(line, force_ar); found_ar = True
        elif section == "Difficulty" and raw.startswith("CircleSize:"):
            lines[index] = _replace_key(line, force_cs); found_cs = True
    if not found_version: raise ValueError("No Version field found")
    difficulty_index = next((i for i, line in enumerate(lines) if line.rstrip("\r\n").strip() == "[Difficulty]"), None)
    if difficulty_index is None: raise ValueError("No Difficulty section found")
    insert_at = difficulty_index + 1
    ending = _ending(lines[difficulty_index]) or "\n"
    if not found_cs: lines.insert(insert_at, f"CircleSize:{force_cs}{ending}"); insert_at += 1
    if not found_ar: lines.insert(insert_at, f"ApproachRate:{force_ar}{ending}")
    # Hit-object source indexes shift if fields were inserted before HitObjects.
    shift = (0 if found_cs else 1) + (0 if found_ar else 1)
    for note in document.hit_objects:
        lines[note.source_line_index + shift] = _replace_xy(lines[note.source_line_index + shift], note.x, note.y)
    payload = "".join(lines).encode(document.encoding)
    if destination == source:
        if create_backup:
            shutil.copy2(source, source.with_suffix(source.suffix + ".bak"))
        fd, temporary = tempfile.mkstemp(prefix=source.stem + "_", suffix=".osu.tmp", dir=source.parent)
        try:
            with os.fdopen(fd, "wb") as handle: handle.write(payload)
            parsed = parse_osu(temporary)
            if len(parsed.hit_objects) != len(document.hit_objects):
                raise ValueError("Validation failed: output hit-object count changed")
            os.replace(temporary, source)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=destination.stem + "_", suffix=".osu.tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload);handle.flush();os.fsync(handle.fileno())
            parsed = parse_osu(temporary)
            if len(parsed.hit_objects) != len(document.hit_objects):
                raise ValueError("Validation failed: output hit-object count changed")
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    return destination