"""Gimmick editor session state and the structures its tools write.

Three things live here, all of them free of Qt so they can be tested directly:

* the **pairing index** -- which difficulty a gimmick session edits, remembered
  permanently in `gimmick_index.json` beside the song library's `song_index.json`
* the **base timing snapshot** -- a copy of the document's timing taken when the
  session is created
* the **structure builders** -- the exact timing points and hit objects each
  gimmick tool emits

Why a base timing snapshot exists
---------------------------------
A gimmick fills the file with 60000 BPM lines. Every one of those restarts
measure counting and collapses the snap grid, so a chart view that derived its
grid from the document being edited would redraw itself under the user's cursor
on every placement. The snapshot is taken once, before any of that, and drives
snaps, wheel scroll and the BPM overlay for the life of the pairing. It is never
recomputed from the edited file.

The output file *is* expected to end up with shifted barline phase. Placements
deliberately write no phase-restoring lines: the snapshot keeps the editor
smooth, not the map.

SV is likewise not auto-restored. An uninherited point resets SV to 1.0x
(`osu_io.timing.sv_at`), so every structure here silently drops the green-line SV
in force at its time. Putting a green line back is the SV layers' job, and they
place it on the same millisecond as the red line by design. When they do,
**the green line must be ordered after the red one**: osu! resolves a shared
timestamp by file order, and `sorted_by_time` is a stable sort on time alone.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from model.hit_object import (
    HITSOUND_CLAP,
    HITSOUND_FINISH,
    TYPE_CIRCLE,
    TYPE_SLIDER,
    HitObject,
)
from osu_io.timing import (
    TimingPoint,
    active_point_at,
    active_uninherited_at,
    sv_at,
    parse_timing_line,
    serialize_timing_point,
    sorted_by_time,
)

INDEX_VERSION = 1

# Defaults for every configurable value in the gimmick toolbox.
DEFAULT_GIMMICK_BPM = 60000.0
# A Don's mirrored restore pair sits at +/-`spacing_ms`. 1 is as tight as the
# format goes, which is what the shape wants -- a don reads as one thin
# object rather than a smear.
#
# Kat used to be derived from this same number (+/-n, +/-(n+2), +/-(n+4)),
# but a mapper hand-layering several barline structures on one millisecond
# region needs Kat's three pairs free of that progression -- so Kat now
# carries its own three spacings below (`DEFAULT_KAT_SPACING1_MS` and
# friends) and `spacing_ms` narrows to meaning only "Don's spacing". The three
# Kat defaults reproduce the old derived values (1, 3, 5) exactly, so neither
# an existing map nor an existing test sees any change from this split.
DEFAULT_SPACING_MS = 1
DEFAULT_KAT_SPACING1_MS = 1
DEFAULT_KAT_SPACING2_MS = 3
DEFAULT_KAT_SPACING3_MS = 5
DEFAULT_FAKE_SLIDER_LENGTH = -1.0
DEFAULT_RED_LINE_OFFSET_MS = 0
# +2 rather than +1 so a shiny note has +1 to itself: the two structures are
# drawn objects at the same millisecond, and sharing an offset stacks them on
# top of each other -- which is exactly what makes a shiny shiny, so an
# accidental overlap is indistinguishable from the effect.
DEFAULT_FAKE_SLIDER_OFFSET_MS = 2
DEFAULT_SHINY_OFFSET_MS = 1
# How many fake sliders one shiny note stacks. A stack of translucent
# drumrolls is what reads as white; one is just a fake slider.
DEFAULT_SHINY_COUNT = 3
DEFAULT_FAKE_SLIDER_SV = 10.0
# Only ever shown, never written unless the red-line tool is switched to a
# custom BPM -- an unchecked box means the base timing's own BPM instead.
DEFAULT_RED_LINE_BPM = 180.0

# Above this, an uninherited point is almost certainly a gimmick line rather
# than real timing. Used only to warn that a base snapshot looks polluted --
# nothing is filtered out on the strength of it.
DIRTY_BASE_BPM = 1000.0


class GimmickConfigError(ValueError):
    """Raised for a configuration value that would produce a broken map."""


@dataclass(slots=True)
class GimmickConfig:
    """One layer's toolbox settings.

    Held per layer rather than once for the page: a barline note's red line
    spacing and a fake slider's offset are both "how many milliseconds", but
    they describe different structures and are tuned against each other (see
    `spacing_collides`), so sharing one value made changing either wrong.

    `fake_slider_length` is negative on purpose -- a negative slider length is
    what makes osu! draw the object without it ever being hittable, which is the
    whole trick. A positive value here would silently turn every fake slider
    into a real drumroll, so it is rejected rather than clamped.

    `place_notes` is the barline layer's: with it off, Don and Kat write their
    red lines and no hit object, for gimmicks that only want the barlines.
    """

    gimmick_bpm: float = DEFAULT_GIMMICK_BPM
    spacing_ms: int = DEFAULT_SPACING_MS
    # Kat's three mirrored pairs, independently configurable -- see the
    # comment above `DEFAULT_SPACING_MS`. Validated distinct in
    # `__post_init__`: two equal values would write two red lines onto one
    # millisecond, where only the file-order-first of them is meaningful, so
    # a mapper who set two of these the same would get a quietly thinner note
    # than the three spin boxes promised.
    kat_spacing1_ms: int = DEFAULT_KAT_SPACING1_MS
    kat_spacing2_ms: int = DEFAULT_KAT_SPACING2_MS
    kat_spacing3_ms: int = DEFAULT_KAT_SPACING3_MS
    fake_slider_length: float = DEFAULT_FAKE_SLIDER_LENGTH
    red_line_offset_ms: int = DEFAULT_RED_LINE_OFFSET_MS
    fake_slider_offset_ms: int = DEFAULT_FAKE_SLIDER_OFFSET_MS
    # SV written on the gimmick line of a Don/Kat fake slider. The note there is
    # already squashed by the gimmick BPM; multiplying on top of it is what
    # takes the last pixel of it off the screen, leaving the fake slider one
    # offset later as the only thing drawn. Configurable because how far the
    # note has to go depends on the chart's own scroll speed.
    fake_slider_sv: float = DEFAULT_FAKE_SLIDER_SV
    place_notes: bool = True
    # Whether a barline note's bars go on both sides of it or only after it.
    # See `barline_note`: mirrored reads as one object centred on the note,
    # forward-only is the trailing style, and both are in use.
    mirror_lines: bool = True
    # How far from its object an SV layer's green lines sit. An SV point
    # governs what comes *after* it, so a chart that wants a note drawn at a
    # new speed needs the line slightly ahead of that note -- the same reason
    # the plain SV function tool defaults to -5ms.
    #
    # It lives here, per layer, rather than only in the Generate dialog because
    # a layer owns its green lines by exact millisecond: the offset decides
    # which milliseconds those are, so placing a line by hand and generating a
    # sweep have to agree on it or one of them lands somewhere the layer cannot
    # see. 0 keeps a layer's lines on its objects, which is what the two
    # gimmick layers want -- their objects *are* timing lines.
    sv_offset_ms: int = 0
    # The plain red-line tool's BPM. None means the base timing's own BPM at
    # that millisecond -- a line that only restarts measure counting. A typed
    # value is the standalone red-line effect: the scroll speed an uninherited
    # point carries is its BPM, so this is how a single line speeds the chart
    # up or slows it down without a green line. Separate from `gimmick_bpm`,
    # which is the squash the structures are built out of and is never what a
    # standalone line wants.
    red_line_bpm: float | None = None
    # The shiny note: `shiny_count` fake sliders stacked on one millisecond,
    # `shiny_offset_ms` after the snap. Its own offset rather than the fake
    # slider's, because the two must not land on the same millisecond -- see
    # `shiny_collides`.
    shiny_offset_ms: int = DEFAULT_SHINY_OFFSET_MS
    shiny_count: int = DEFAULT_SHINY_COUNT
    # What the shiny's red line BPM is multiplied by, at the note's own
    # millisecond; 1.0 leaves it at the chart's own. A red line's BPM is also
    # its scroll speed, so this is a speed change -- and the green line that
    # always follows it divides SV by the same number, so the note ends up
    # travelling at exactly the speed it did before and only the numbers in
    # the file changed.
    shiny_bpm_multiplier: float = 1.0
    # What a fake slider's or Don/Kat's restore line BPM is multiplied by;
    # 1.0 -- the default the owner asked for -- leaves it at the chart's own.
    # Unlike the shiny multiplier there is no green line to pay it back with:
    # nothing is drawn across the retimed section for this to keep looking the
    # same speed, so it is a plain speed change to the fake slider itself.
    fake_slider_bpm_multiplier: float = 1.0
    # Fake slider layer: set effects bit 3 (value 8) on the uninherited points
    # a fake slider writes, so its 60000 BPM line does not also draw a barline.
    # On by default -- a fake slider is decoration, and the bar it drew was
    # never wanted. The barline layer ignores it: bars are its whole output.
    omit_barline: bool = True

    def __post_init__(self) -> None:
        if self.fake_slider_length >= 0:
            raise GimmickConfigError("Fake slider length must be negative")
        if self.gimmick_bpm <= 0:
            raise GimmickConfigError("Gimmick BPM must be positive")
        if self.spacing_ms < 1:
            raise GimmickConfigError("Don spacing must be at least 1 ms")
        if min(self.kat_spacing1_ms, self.kat_spacing2_ms, self.kat_spacing3_ms) < 1:
            raise GimmickConfigError("Kat spacing must be at least 1 ms")
        if len({self.kat_spacing1_ms, self.kat_spacing2_ms, self.kat_spacing3_ms}) != 3:
            # Two equal spacings write their red lines onto the same
            # millisecond, and only the first of the pair in file order is a
            # meaningful line -- the second is a wasted spin box value.
            raise GimmickConfigError("Kat spacings must be distinct")
        if self.fake_slider_offset_ms < 1:
            raise GimmickConfigError("Fake slider offset must be at least 1 ms")
        if self.fake_slider_sv <= 0:
            raise GimmickConfigError("Fake slider SV must be positive")
        if self.red_line_bpm is not None and self.red_line_bpm <= 0:
            raise GimmickConfigError("Red line BPM must be positive")
        if self.shiny_offset_ms < 1:
            raise GimmickConfigError("Shiny offset must be at least 1 ms")
        if self.shiny_count < 1:
            raise GimmickConfigError("Shiny note count must be at least 1")
        if self.shiny_bpm_multiplier <= 0:
            raise GimmickConfigError("Shiny BPM multiplier must be positive")
        if self.fake_slider_bpm_multiplier <= 0:
            raise GimmickConfigError("Fake slider BPM multiplier must be positive")


def spacing_collides(barline: GimmickConfig, fake_slider: GimmickConfig) -> bool:
    """Whether any of the barline layer's spacings lands on the fake slider's
    own offset.

    A barline note puts restore lines at +/- `spacing_ms` (Don) and +/- each
    of `kat_spacing1_ms`/`kat_spacing2_ms`/`kat_spacing3_ms` (Kat); a fake
    slider puts its restore line at +`fake_slider_offset_ms`. Any one of those
    four values equalling the fake slider's offset means the two structures
    fight for the same millisecond whenever they are placed on the same snap,
    and only one uninherited point per millisecond is meaningful. Reported as a
    caution rather than enforced -- it is only wrong where they actually
    overlap, which the editor cannot know in advance.
    """
    offset = fake_slider.fake_slider_offset_ms
    return offset in (
        barline.spacing_ms,
        barline.kat_spacing1_ms,
        barline.kat_spacing2_ms,
        barline.kat_spacing3_ms,
    )


def shiny_collides(config: GimmickConfig) -> bool:
    """Whether the shiny offset lands on the fake slider's own offset.

    Both are drawn objects placed one offset after the snap, so equal offsets
    put them on the same millisecond -- and a stack of fake sliders on one
    millisecond, and which of the two an object is is read off exactly that
    distance. Equal offsets leave the editor no way to tell them apart.
    Reported as a caution rather than enforced, like `spacing_collides`.
    """
    return config.shiny_offset_ms == config.fake_slider_offset_ms


# Which structure an object is, is read off *where* it sits: a fake slider
# layer object one `shiny_offset_ms` after its gimmick line is a shiny note, one
# `fake_slider_offset_ms` after it is a fake slider. Not off how many objects
# share the millisecond -- a plain fake slider is routinely stacked as well, to
# brighten it, so the count says nothing about which of the two it is. This is
# also why the two offsets must differ; see `shiny_collides`.


# -- base timing -----------------------------------------------------------


def snapshot_timing(points: list[TimingPoint]) -> list[TimingPoint]:
    """Detached copy of `points`, safe to keep while the document is edited."""
    return [point.copy() for point in points]


def looks_gimmicked(points: list[TimingPoint], threshold: float = DIRTY_BASE_BPM) -> bool:
    """Whether a snapshot appears to already contain gimmick lines.

    "Use this one" on an already-gimmicked difficulty captures that mess as its
    base timing, permanently. The editor warns on this and continues; there is
    no filtering, because a genuine 1200 BPM stream is indistinguishable from a
    gimmick line without knowing the mapper's intent.
    """
    return any(point.bpm is not None and point.bpm > threshold for point in points)


def base_bpm_at(base_timing: list[TimingPoint], time_ms: float) -> float:
    """BPM of the base snapshot at `time_ms`, for the restore lines."""
    point = active_uninherited_at(base_timing, time_ms)
    return point.bpm or 120.0


# -- pairing index ---------------------------------------------------------


@dataclass(slots=True)
class GimmickPairing:
    """One remembered session: which file it edits, and its base timing.

    `reference` is the difficulty the base timing was read out of. It is asked
    for once, on entry, and remembered with the pairing, because a gimmick
    difficulty is the worst possible source for its own grid: by the second
    placement it is full of 60000 BPM lines, and every one of them restarts
    measure counting. A clean sibling difficulty keeps saying where the beats
    are however far the gimmick file drifts from them. It is kept only so the
    editor can say which file it is honouring -- the snapshot itself is stored,
    not re-read, so editing the reference later does not silently move the grid
    under an in-progress gimmick.
    """

    target: Path
    base_timing: list[TimingPoint] = field(default_factory=list)
    reference: Path | None = None


def load_index(path: Path) -> dict[str, GimmickPairing]:
    """Read the pairing index, or start empty when missing or unusable.

    Same shape and failure behaviour as `song_library.load_cache`: a corrupt or
    version-mismatched file is treated as no file at all, because the worst case
    is being asked the setup question once more.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if payload.get("version") != INDEX_VERSION:
        return {}
    pairings: dict[str, GimmickPairing] = {}
    for source, entry in payload.get("pairings", {}).items():
        try:
            target = Path(entry["target"])
        except (KeyError, TypeError):
            continue
        points = [parse_timing_line(line) for line in entry.get("base", [])]
        reference = entry.get("reference")
        pairings[source] = GimmickPairing(
            target,
            [p for p in points if p is not None],
            Path(reference) if reference else None,
        )
    return pairings


def save_index(path: Path, pairings: dict[str, GimmickPairing]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": INDEX_VERSION,
        "pairings": {
            source: {
                "target": str(pairing.target),
                "reference": str(pairing.reference) if pairing.reference else None,
                # Stored as the file's own text, so a snapshot round-trips
                # through the same parser the map does.
                "base": [serialize_timing_point(point, "") for point in pairing.base_timing],
            }
            for source, pairing in pairings.items()
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def index_key(source: Path) -> str:
    """Absolute path of the difficulty the user entered the tab from.

    Moving the Songs folder loses every pairing, which costs one re-answer of
    the setup dialog and a fresh base snapshot.
    """
    return str(Path(source).resolve())


def difficulty_setting(document, key: str, default: float) -> float:
    """Read one [Difficulty] value out of a document's raw lines.

    `write_osu` always rewrites ApproachRate and CircleSize from its arguments,
    so copying a difficulty faithfully means handing it back the values the
    source already had rather than letting the export defaults through.
    """
    section = ""
    for line in document.lines:
        stripped = line.rstrip("\r\n").strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if section == "Difficulty" and stripped.startswith(f"{key}:"):
            try:
                return float(stripped.split(":", 1)[1].strip())
            except ValueError:
                return default
    return default


def gimmick_path_for(source: Path) -> Path:
    """`... [Oni].osu` -> `... [Oni] [Gimmick].osu`, in the same folder."""
    return Path(source).with_name(f"{Path(source).stem} [Gimmick].osu")


def gimmick_version_for(version: str) -> str:
    return f"{version} [Gimmick]"


# -- structure builders ----------------------------------------------------
#
# Each returns (timing points, hit objects) for one placement. The caller wraps
# both lists in a single CompositeCommand so a placement is one undo step.

# The hitsound the *real* note a placement writes carries. Don and Kat mean an
# actual don and an actual kat here; what the structure drawn beside that note
# does to say which of the two it is differs per layer -- a fake slider changes
# size (see `_SLIDER_HITSOUND`), a barline note changes how many bars it draws.
_KIND_HITSOUND = {"regular": 0, "don": 0, "kat": HITSOUND_CLAP}

# ...and the hitsound the *fake slider* carries. A drumroll has no don/kat of
# its own -- it is yellow whatever is under it -- so the only thing it can say
# about which button the structure means is its size, and the finisher bit is
# what makes it the big one. Kat is the big fake slider; don is the small one.
_SLIDER_HITSOUND = {"regular": 0, "don": 0, "kat": HITSOUND_FINISH}


def _circle(time_ms: int, hit_sound: int, x: int = 256, y: int = 192) -> HitObject:
    """A plain, hittable taiko note."""
    return HitObject(
        x=x, y=y, time=time_ms, type=TYPE_CIRCLE, hit_sound=hit_sound,
        hit_sample="0:0:0:0:",
    )


def fake_slider(
    time_ms: int,
    base_timing: list[TimingPoint],
    config: GimmickConfig,
    *,
    kind: str = "regular",
    x: int = 256,
    y: int = 192,
    shiny: bool = False,
    copies: int | None = None,
) -> tuple[list[TimingPoint], list[HitObject]]:
    """A drumroll of negative length -- what draws without ever being hittable.

    Three structures share this builder, because everything about the drawn
    object -- its shape, its offsets, which kind gets the finisher bit -- is
    identical between them, and only the timing points that shape it differ.
    Which one is built is read off `kind` and `shiny`:

    * **plain fake slider** (`kind="regular"`, not shiny) -- no note, so
      nothing to hide. One red line, on the slider itself, `fake_slider_offset_ms`
      after the snap and retimed by `fake_slider_bpm_multiplier`. There is no
      squash: a squash exists to hide a hittable note, and this structure does
      not have one.
    * **shiny** (`shiny=True`) -- a glow around a note that *stays visible*, so
      squashing it is not an option: that would delete the very note the glow
      decorates. Its red line sits on the snap itself, retimed by
      `shiny_bpm_multiplier`, and a green line always follows it restating the
      SV that multiplier divides out of the timeline -- see
      `GimmickConfig.shiny_bpm_multiplier`. `shiny_count` sliders, the stack
      that reads as white, sit `shiny_offset_ms` later, beside the note rather
      than on it. With a Don/Kat `kind` a real, hittable note is written on the
      snap as well, undisturbed by any of this.
    * **Don / Kat, not shiny** -- the one case that deliberately hides its
      note. A gimmick-BPM line squashes the snap to nothing; an SV line on top
      of it (`fake_slider_sv`) -- the uninherited point has just reset SV to
      1.0x, so this is a green line stacked after the red one -- takes the
      already-squashed note the rest of the way off screen; and the chart's
      own BPM comes back `fake_slider_offset_ms` later (retimed by
      `fake_slider_bpm_multiplier`), which is exactly where the fake slider --
      small for Don, big (finisher bit) for Kat -- is the only thing left to
      look at.

    Wherever there is a green line, it is always written *after* the red line
    sharing its millisecond: osu! resolves a shared timestamp by file order,
    and an uninherited point resets SV to 1.0x, so the other way round would
    silently cancel it.

    `copies` overrides the slider count for one call; otherwise it is
    `shiny_count` for a shiny and 1 otherwise. The note, where there is one, is
    what the player hits; the fake slider is the decoration next to it, which
    is why it cannot share the note's millisecond -- two hit objects on one
    timestamp leaves one of them permanently invisible.

    None of this takes the red-line placement offset -- that belongs to the
    plain red-line tool alone.
    """
    if kind not in _KIND_HITSOUND:
        raise GimmickConfigError(f"Unknown fake slider kind: {kind!r}")
    time_ms = int(time_ms)
    if copies is None:
        copies = config.shiny_count if shiny else 1
    copies = max(1, int(copies))
    omit = config.omit_barline

    if shiny:
        # The note the glow decorates stays on the snap and stays visible, so
        # the red line governing it sits there too rather than on an offset --
        # there is nothing to squash. The stack that makes the glow read as
        # white is a separate, later object; see `shiny_offset_ms`.
        slider_at = time_ms + config.shiny_offset_ms
        red_bpm = base_bpm_at(base_timing, time_ms) * config.shiny_bpm_multiplier
        points = [
            TimingPoint.uninherited_at(time_ms, red_bpm, omit_first_barline=omit),
            # Always written, even at multiplier 1.0 (then it simply restates
            # the SV in force): it is the handle the SV layer for this
            # structure is made of, not only a correction for a retimed line.
            TimingPoint.inherited_at(
                time_ms, sv_at(base_timing, time_ms) / config.shiny_bpm_multiplier,
            ),
        ]
    elif kind == "regular":
        # No note, no squash: the drawn object alone, one offset after the
        # snap, at the chart's own BPM.
        slider_at = time_ms + config.fake_slider_offset_ms
        bpm = base_bpm_at(base_timing, slider_at) * config.fake_slider_bpm_multiplier
        points = [TimingPoint.uninherited_at(slider_at, bpm, omit_first_barline=omit)]
    else:
        slider_at = time_ms + config.fake_slider_offset_ms
        restore_bpm = base_bpm_at(base_timing, slider_at) * config.fake_slider_bpm_multiplier
        points = [
            TimingPoint.uninherited_at(time_ms, config.gimmick_bpm, omit_first_barline=omit),
            TimingPoint.inherited_at(time_ms, config.fake_slider_sv),
            TimingPoint.uninherited_at(slider_at, restore_bpm, omit_first_barline=omit),
        ]

    notes = [] if kind == "regular" else [_circle(time_ms, _KIND_HITSOUND[kind])]
    notes.extend(
        HitObject(
            x=x, y=y, time=slider_at, type=TYPE_SLIDER, hit_sound=_SLIDER_HITSOUND[kind],
            extras=(f"L|{x + 100}:{y}", "1", _format_length(config.fake_slider_length)),
            hit_sample="0:0:0:0:",
        )
        for _ in range(copies)
    )
    return points, notes


def _format_length(length: float) -> str:
    """Keep -1 as "-1" rather than "-1.0"; osu! reads both, mappers read one."""
    return str(int(length)) if length == int(length) else repr(float(length))


def barline_note(
    time_ms: int,
    base_timing: list[TimingPoint],
    config: GimmickConfig,
    *,
    kind: str = "don",
) -> tuple[list[TimingPoint], list[HitObject]]:
    """A note drawn out of barlines: one gimmick BPM line plus mirrored restores.

    Every uninherited point emits a barline at its own time, so the count of
    restore lines is the count of bars drawn. Don is a single mirrored pair,
    at +/-`spacing_ms`; kat is three mirrored pairs, at +/-`kat_spacing1_ms`,
    +/-`kat_spacing2_ms` and +/-`kat_spacing3_ms`, independently of each other
    and of Don's own spacing -- which is what makes kat read as the wider note,
    and what lets a mapper hand-layer several structures on one millisecond
    region without a shared "n" forcing their widths apart in lockstep.

    With `place_notes` on (the default) a real, hittable note goes on the snap
    as well, so the drawn bars are something the player actually plays. Off, the
    bars are all that is written -- for gimmicks that use them as scenery.

    The gimmick line always lands exactly on `time_ms` -- the placement offset
    does not apply here.
    """
    if kind == "don":
        spacings = (config.spacing_ms,)
    elif kind == "kat":
        spacings = (
            config.kat_spacing1_ms, config.kat_spacing2_ms, config.kat_spacing3_ms,
        )
    else:
        raise GimmickConfigError(f"Unknown barline note kind: {kind!r}")

    # Mirrored by default -- bars either side of the note read as one object
    # centred on it. Forward only is a real style, though: a note whose bars
    # all trail it, written as a squash on the note and a single restore one
    # millisecond later, is what several hand-made maps use, and there was no
    # way to ask for it.
    offsets = (
        [-value for value in spacings] + list(spacings) if config.mirror_lines
        else list(spacings)
    )

    time_ms = int(time_ms)
    points = [TimingPoint.uninherited_at(time_ms, config.gimmick_bpm)]
    for offset in offsets:
        at = time_ms + offset
        points.append(TimingPoint.uninherited_at(at, base_bpm_at(base_timing, at)))
    points.sort(key=lambda point: point.time)
    notes = [_circle(time_ms, _KIND_HITSOUND[kind])] if config.place_notes else []
    return points, notes


def sv_restore_point(
    points: list[TimingPoint],
    time_ms: float,
    restore_at: float,
) -> TimingPoint:
    """Green line re-stating the SV in force at `time_ms`, at `restore_at`.

    A gimmick's uninherited lines reset SV to 1.0x, which silently drops the
    chart's scroll speed for everything after them. This hands that speed back.

    Written even when the chart was already at 1.0x by its own timing. The line
    changes nothing on its own then -- the uninherited point it follows has just
    said 1.0x -- but it is the handle the SV layer for that structure is made of:
    a layer matches its green lines by exact millisecond, so with nothing there
    the fake slider layer had no line to show, drag or generate from, which is
    the whole of what layer 5 is for. Writing one that agrees with the timing
    beats making the user place one by hand before they can edit it.
    """
    active = active_point_at(sorted_by_time(points), time_ms)
    if active is None:
        return TimingPoint.inherited_at(restore_at, 1.0)
    return TimingPoint.inherited_at(
        restore_at,
        1.0 if active.uninherited else active.sv_multiplier,
        template=active,
    )


def preserve_kiai(points: list[TimingPoint], document_points: list[TimingPoint]) -> list[TimingPoint]:
    """Carry the map's kiai state at each point's time onto that point.

    Kiai lives on the timing point, not on a span: whatever point is active at a
    millisecond decides whether kiai is on there. So *every* line a gimmick
    writes inside a kiai section has to say so, or the first one silently ends
    the section for everything after it -- which is what made a gimmick placed
    in a chorus switch the chorus off.

    Applied to the built points rather than passed into each builder: it is the
    same rule for red lines, green lines and every structure, and the state it
    reads is the live document's, which the builders deliberately do not see.
    """
    ordered = sorted_by_time(document_points)
    for point in points:
        active = active_point_at(ordered, point.time)
        if active is not None and active.kiai:
            point.set_kiai(True)
    return points


def red_line(
    time_ms: int,
    bpm: float,
    config: GimmickConfig,
) -> tuple[list[TimingPoint], list[HitObject]]:
    """The plain red-line tool: one uninherited point at snap + offset.

    This is the only tool the placement offset applies to. Nudging it off the
    snap is how a gimmick avoids sharing a millisecond with the real chart's own
    timing line, since only one uninherited point per millisecond is meaningful.

    `bpm` is the base timing's own BPM at that millisecond -- what the caller
    reads when the layer is configured to leave it alone. `red_line_bpm`
    overrides it: an uninherited point's BPM *is* its scroll speed, so a typed
    value is a standalone speed change with no green line involved.
    """
    at = int(time_ms) + config.red_line_offset_ms
    return [TimingPoint.uninherited_at(at, config.red_line_bpm or bpm)], []


# -- oscillating SV --------------------------------------------------------


def oscillating_series(
    base: float,
    difference: float,
    count: int,
    ease,
    *,
    per_pair: bool = False,
) -> list[float]:
    """SV values fanning out from `base`, alternating side.

    Index 0 sits on the base and `difference` is always the *total* amplitude
    reached by the last point. What the caller chooses is how fast the fan
    opens, and both modes are available on every growth function:

    * **per point** (default) -- the amplitude advances on every point, so the
      two sides peak at different widths::

          base 1.40, difference 0.12, exp1.6, 7 points ->
          1.400  1.407  1.379  1.440  1.337  1.490  1.280

    * **per pair** -- it advances once per up/down pair, so each pair is
      symmetric about the base::

          base 1.00, difference 0.06, linear, 7 points ->
          1.00  1.02  0.98  1.04  0.96  1.06  0.94

    Linear is usually configured as a per-step value rather than a total.
    Converting that to a total is the dialog's job, so this stays one formula.

    `ease` is `gui.sv_ease` bound to a growth function; passed in rather than
    imported so this module stays free of the GUI.
    """
    if count <= 0:
        return []
    if count == 1:
        return [base]
    levels = count // 2 if per_pair else count - 1
    values = []
    for index in range(count):
        level = (index + 1) // 2 if per_pair else index
        amplitude = difference * ease(level / levels)
        sign = 1 if index % 2 else -1
        values.append(base + sign * amplitude)
    values[0] = base
    return values
