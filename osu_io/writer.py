"""Safe .osu writing.

The model owns [TimingPoints] and [HitObjects] and regenerates them. Every other
section is passed through byte for byte.

That split is deliberate. Storyboard events, break periods, [Colours], [Editor]
bookmarks and assorted [General] keys are modelled nowhere in this codebase, so
verbatim passthrough is the only thing keeping mapper data intact, and a bug
there is invisible until someone notices their storyboard is gone. Regenerating
only the two sections we actually model gets the mutability that SV editing,
barline gimmicks and note editing need, and keeps the guarantee everywhere else.

Deliberate, documented loss: // comments inside those two sections.
"""
from pathlib import Path
import os
import shutil
import tempfile
import math

from osu_io.parser import OsuDocument, parse_osu
from osu_io.timing import (
    TimingValidationError,
    parse_timing_line,
    serialize_timing_point,
    sorted_by_time,
    validate_timing_points,
)

# Sections the model owns and regenerates. Order matters only for readability;
# the splice runs in descending index so earlier spans stay valid.
GENERATED_SECTIONS = ("TimingPoints", "HitObjects")


class OsuWriteValidationError(ValueError):
    """Raised when the output would not be a loadable beatmap."""


def _ending(line):
    return "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""


def _replace_key(line, value):
    end = _ending(line); content = line[:-len(end)] if end else line
    return content.split(":", 1)[0] + ":" + str(value) + end


def _section_spans(lines):
    """Recompute {section: (header index, one past last body line)}.

    Recomputed from the patched line list rather than carried from the parser,
    because Version/AR/CS patching can insert lines. This is what replaces the
    old flat `shift`, which assumed every insertion preceded every hit object
    and indexed past the note block when [Difficulty] came after [HitObjects].
    """
    spans = {}
    open_name = None
    open_index = 0
    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n").strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if open_name is not None:
                spans[open_name] = (open_index, index)
            open_name = stripped[1:-1]
            open_index = index
    if open_name is not None:
        spans[open_name] = (open_index, len(lines))
    return spans


def _point_fields(point):
    return (
        point.time, point.beat_length, point.meter, point.sample_set,
        point.sample_index, point.volume, point.uninherited_flag, point.effects,
    )


def _timing_body(document, ending):
    """Serialize timing points, reusing the original text for untouched ones.

    Only matters for maps whose timing lines omit trailing fields, which osu
    file format v4 and earlier do. Regenerating those would expand "500,300"
    into "500,300,4,0,0,100,1,0", substituting this parser's assumed defaults
    for whatever the game actually applies. Points that have not been edited are
    therefore written back exactly as authored.
    """
    body = []
    for point in sorted_by_time(document.timing_points):
        original = None
        if 0 <= point.source_line_index < len(document.lines):
            candidate = document.lines[point.source_line_index]
            reparsed = parse_timing_line(candidate, point.source_line_index)
            if reparsed is not None and _point_fields(reparsed) == _point_fields(point):
                original = candidate if candidate.endswith(("\n", "\r")) else candidate + ending
        body.append(original if original is not None else serialize_timing_point(point, ending))
    return body


def _generate_section(name, document, ending):
    if name == "TimingPoints":
        return _timing_body(document, ending)
    notes = sorted(document.hit_objects, key=lambda note: note.time)
    return [note.to_line(ending) for note in notes]


def _insert_position(lines, spans, name):
    """Where to add a missing section header, following osu!'s own ordering."""
    preferred = {
        "TimingPoints": ("Events", "Difficulty", "Metadata", "General"),
        "HitObjects": ("Colours", "TimingPoints", "Events", "Difficulty"),
    }[name]
    for previous in preferred:
        if previous in spans:
            return spans[previous][1]
    return len(lines)


def _splice_sections(lines, document, ending):
    """Replace generated section bodies, leaving every other line untouched."""
    spans = _section_spans(lines)
    targets = []
    for name in GENERATED_SECTIONS:
        body = _generate_section(name, document, ending)
        if name in spans:
            start, stop = spans[name][0] + 1, spans[name][1]
            # A section span runs to the next header, so it swallows the blank
            # line that separates the two. Leave those trailing blanks alone,
            # otherwise every rewrite quietly reflows the file.
            while stop > start and not lines[stop - 1].strip():
                stop -= 1
            targets.append((start, stop, body))
        elif body:
            at = _insert_position(lines, spans, name)
            targets.append((at, at, [f"[{name}]{ending}"] + body + [ending]))
    # Descending start index keeps earlier spans valid while later ones change.
    for start, stop, body in sorted(targets, key=lambda item: item[0], reverse=True):
        lines[start:stop] = body
    return lines


def _validate_output(parsed, document, new_version, original_spans):
    def note_key(note):
        return (note.x, note.y, note.time, note.type, note.hit_sound, note.extras, note.hit_sample)

    def point_key(point):
        return (point.time, point.beat_length, point.uninherited_flag, point.effects)

    if len(parsed.hit_objects) != len(document.hit_objects):
        raise OsuWriteValidationError(
            f"Output has {len(parsed.hit_objects)} hit objects, expected {len(document.hit_objects)}"
        )
    expected_notes = sorted((note_key(n) for n in document.hit_objects))
    if sorted(note_key(n) for n in parsed.hit_objects) != expected_notes:
        raise OsuWriteValidationError("Output hit objects differ from the document")

    if len(parsed.timing_points) != len(document.timing_points):
        raise OsuWriteValidationError(
            f"Output has {len(parsed.timing_points)} timing points, expected {len(document.timing_points)}"
        )
    expected_points = sorted(point_key(p) for p in document.timing_points)
    if sorted(point_key(p) for p in parsed.timing_points) != expected_points:
        raise OsuWriteValidationError("Output timing points differ from the document")

    if parsed.version != str(new_version):
        raise OsuWriteValidationError("Output difficulty name does not match the requested version")

    # The passthrough guarantee: no section may disappear.
    missing = set(original_spans) - set(parsed.section_spans)
    if missing:
        raise OsuWriteValidationError(f"Output lost sections: {sorted(missing)}")


def _encode(lines, encoding):
    payload = "".join(lines)
    try:
        return payload.encode(encoding)
    except UnicodeEncodeError:
        # A cp1252 document that has gained generated non-Latin text, e.g. a
        # Japanese filename in a hit sample. osu! reads UTF-8 unconditionally.
        return payload.encode("utf-8")


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

    try:
        validate_timing_points(document.timing_points)
    except TimingValidationError as error:
        raise OsuWriteValidationError(str(error)) from error

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
    ending = _ending(lines[difficulty_index]) or document.line_ending or "\n"
    if not found_cs: lines.insert(insert_at, f"CircleSize:{force_cs}{ending}"); insert_at += 1
    if not found_ar: lines.insert(insert_at, f"ApproachRate:{force_ar}{ending}")

    lines = _splice_sections(lines, document, ending)
    payload = _encode(lines, document.encoding)
    original_spans = set(document.section_spans)

    if destination == source:
        fd, temporary = tempfile.mkstemp(prefix=source.stem + "_", suffix=".osu.tmp", dir=source.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            _validate_output(parse_osu(temporary), document, new_version, original_spans)
            # Only back up once the replacement is known good, so a validation
            # failure cannot leave a stale .bak behind.
            if create_backup:
                shutil.copy2(source, source.with_suffix(source.suffix + ".bak"))
            os.replace(temporary, source)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=destination.stem + "_", suffix=".osu.tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            _validate_output(parse_osu(temporary), document, new_version, original_spans)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    return destination
