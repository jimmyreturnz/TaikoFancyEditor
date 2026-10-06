"""App colour themes, as a recolouring of the one palette the code is written in.

Every colour in the UI is a literal in the default (pink) theme -- some 300 of
them, across sixty stylesheets and the paint code. A theme is therefore not a
second set of literals but a map from those to its own colours, applied at the
two places a colour reaches Qt from Python:

- every `setStyleSheet` call, by wrapping `QWidget.setStyleSheet` and
  `QApplication.setStyleSheet` once (`install`);
- paint code, through `color()` instead of `QColor("#...")`.

So a new widget written in the pink literals is themed for free, and the pink
theme is the code exactly as written: `install` does nothing for it.

The theme is read once, when this module is first imported, because class
bodies in gui.py build QColors at import. Changing it needs a restart, as the
language does.

What a theme leaves alone, deliberately: the note colours (don, kat,
drumroll), osu!'s snap tick colours, the SV layer's green, star-rating colours
and the red of destructive actions. Those mean something in the chart, not
in the chrome, and none of them is a key below.
"""
from __future__ import annotations

import colorsys
import re

from PySide6.QtGui import QColor

SETTING = "appearance/theme"
DEFAULT = "pink"

# The pink palette's own roles. Each theme names a target for the anchors, and
# the colours derived from an anchor (its hover, its gradient stops, the dark
# tints behind a checked tile) follow it by the same hue turn.
_PRIMARY = "#f3a6bd"        # filled buttons: Save, current page, checked tool
_PRIMARY_FAMILY = ("#f7bfd0", "#f7bccd", "#f6b3c7", "#ee97b1")
_INK = "#17191f"            # text on a primary fill
_FOCUS = "#ff66aa"          # rims, slider fills, the focused lane
# The last three are the switch art's (assets/ui/switch-on-*.svg): hover, and
# the disabled track and knob.
_FOCUS_FAMILY = ("#2a2230", "#6b3a55", "#8a4a6a", "#b0587f", "#2a1f2a",
                 "#ff85bb", "#6b4459", "#9a8a93")
_ACCENT_TEXT = "#ff9dcc"    # pink text: values, the difficulty name
# The navy chrome, ground to text. Not #738098 (a snap tick) or #f4f7fb (the
# note outline): those belong to the chart.
_NEUTRALS = (
    "#0d1219", "#11151c", "#2b323d", "#12161d", "#151b24", "#161c25", "#171d26", "#191f29",
    "#1b212b", "#1d2431", "#1e2530", "#1f262f", "#222a36", "#252d39", "#262e3a",
    "#2a3341", "#2f3947", "#303947", "#39414d", "#3a4554", "#465164", "#4a5668",
    "#4d5664", "#56637a", "#7a8492", "#7d8794", "#aeb8c5", "#d5dce5", "#e8edf3",
)

# The navy's own steps, darkest first: lane, panel, ground, surface, raised,
# line, faint text, muted text, text. A theme that changes the ground names
# its own nine (the approved mockup's), and every other navy shade in the code
# is placed between the two steps it sits between.
_NEUTRAL_STEPS = ("#11151c", "#151b24", "#191f29", "#222a36", "#2a3341",
                  "#3a4554", "#7d8794", "#aeb8c5", "#e8edf3")

# name -> (primary, ink, focus, accent text, the nine steps or None)
THEMES = {
    "pink": None,
    "taiko": ("#d4432a", "#ffffff", "#5aa7c7", "#ff8f72",
               ("#0d1118", "#10151d", "#141a24", "#1c2430", "#26313f",
                "#34445a", "#7b889b", "#a9b6c6", "#e8edf3")),
    "matsuri": ("#c23b22", "#fff3e6", "#e3b04b", "#e8bc62",
                ("#0e0a08", "#120e0b", "#17120f", "#221a16", "#2e241e",
                 "#4a3a2f", "#93806e", "#c4b3a1", "#f3e9dc")),
    "kiai": ("#ff9f2e", "#1d1406", "#ff9f2e", "#ffc06b", None),
}


def _hls(hex_colour: str) -> tuple[float, float, float]:
    value = hex_colour.lstrip("#")
    return colorsys.rgb_to_hls(*(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4)))


def _hex(h: float, l: float, s: float) -> str:
    clamp = lambda x: min(1.0, max(0.0, x))
    r, g, b = colorsys.hls_to_rgb(h % 1.0, clamp(l), clamp(s))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _follow(member: str, anchor: str, target: str, shift_lightness: bool) -> str:
    """`member` moved the way `anchor` moves to `target`.

    The primary family is all light fills, so it shifts lightness with the
    anchor and a hover stays a step lighter than its button. The focus family
    mixes a bright rim with near-black tints, and moving those by the rim's
    lightness change would crush the tints to black -- they keep their own.
    """
    # The target's hue outright, not the member's own offset from pink carried
    # over: a tint 18 degrees off pink is the same pink to the eye, but 18
    # degrees off kat blue is green.
    _mh, ml, ms = _hls(member)
    _ah, al, as_ = _hls(anchor)
    th, tl, ts = _hls(target)
    lightness = ml + (tl - al) if shift_lightness else ml
    saturation = ms * (ts / as_) if as_ else ms
    return _hex(th, lightness, saturation)


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _between_steps(member: str, steps: tuple[str, ...]) -> str:
    """`member` placed among `steps` where it sits among the navy's own.

    Inside the range it is mixed between the two steps either side of it, by
    lightness; past either end it keeps its own offset from the end step. So a
    hover a shade above the surface stays a shade above the theme's surface.
    """
    level = _hls(member)[1]
    levels = [_hls(step)[1] for step in _NEUTRAL_STEPS]
    if level <= levels[0] or level >= levels[-1]:
        end = 0 if level <= levels[0] else -1
        own, base, target = _rgb(member), _rgb(_NEUTRAL_STEPS[end]), _rgb(steps[end])
        return "#%02x%02x%02x" % tuple(min(255, max(0, t + o - b)) for t, o, b in zip(target, own, base))
    upper = next(i for i, step_level in enumerate(levels) if step_level >= level)
    lower = upper - 1
    span = levels[upper] - levels[lower]
    f = 0.0 if span <= 0 else (level - levels[lower]) / span
    a, b = _rgb(steps[lower]), _rgb(steps[upper])
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * f) for x, y in zip(a, b))


def build_map(name: str) -> dict[str, str]:
    spec = THEMES.get(name)
    if not spec:
        return {}
    primary, ink, focus, accent, steps = spec
    mapping = {_PRIMARY: primary, _INK: ink, _FOCUS: focus, _ACCENT_TEXT: accent}
    for member in _PRIMARY_FAMILY:
        mapping[member] = _follow(member, _PRIMARY, primary, True)
    for member in _FOCUS_FAMILY:
        mapping[member] = _follow(member, _FOCUS, focus, False)
    if steps is not None:
        for member in _NEUTRALS:
            mapping[member] = _between_steps(member, steps)
    return mapping


_HEX = re.compile(r"#([0-9a-fA-F]{6})(?=[0-9a-fA-F]{2}\b|\b)")
_FOCUS_RGBA = re.compile(r"rgba\(\s*255\s*,\s*102\s*,\s*170\s*,")

_active = DEFAULT
_map: dict[str, str] = {}
_focus_rgb = "255, 102, 170"


def css(text: str) -> str:
    """`text` with every pink-theme colour in it swapped for the active theme's."""
    if not _map or not text:
        return text
    text = _HEX.sub(lambda m: _map.get("#" + m.group(1).lower(), m.group(0)), text)
    return _FOCUS_RGBA.sub(f"rgba({_focus_rgb},", text)


def color(value) -> QColor:
    """QColor of a pink-theme colour, in the active theme."""
    if isinstance(value, str) and _map:
        value = _map.get(value.lower(), value)
    return QColor(value)


def rgba(r: int, g: int, b: int, a: int = 255) -> QColor:
    """`color()` for a pink-theme colour written as integers, keeping its alpha.

    The song list's selected-row tint was QColor(255, 102, 170, 46): no hex
    for the remap to see, so it stayed pink in every theme.
    """
    colour = color("#%02x%02x%02x" % (r, g, b))
    colour.setAlpha(a)
    return colour


def svg_asset(path: str) -> str:
    """`path`, or a recoloured copy of it when it is an SVG carrying a
    pink-theme colour.

    The switches, the hovered spin-box chevrons and the like are drawn by SVG
    files referenced from stylesheets as url(), so the colour is in the file
    and neither css() nor color() ever sees it -- the toggles stayed pink in
    every theme. The copy is written once per theme beside the user's temp
    files and reused.
    """
    if not _map or not path.lower().endswith(".svg"):
        return path
    import tempfile
    from pathlib import Path
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return path
    themed = css(text)
    if themed == text:
        return path
    target = Path(tempfile.gettempdir()) / "TaikoFancyArranger-theme" / _active / source.name
    try:
        if not target.is_file() or target.read_text(encoding="utf-8") != themed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(themed, encoding="utf-8")
    except OSError:
        return path
    return target.as_posix()


def apply_palette(app) -> None:
    """Give natively drawn controls the theme's focus colour.

    Anything without a stylesheet of its own -- the Settings sliders, selected
    text, a selected list row -- is filled from the palette's Highlight and
    Accent, which Qt takes from the *Windows* accent colour. On a pink accent
    that read as the app's pink in every theme; on any other it was neither.
    """
    from PySide6.QtGui import QPalette
    palette = app.palette()
    focus = color(_FOCUS)
    for role in (QPalette.Highlight, QPalette.Accent):
        palette.setColor(role, focus)
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def active() -> str:
    return _active


def install(name: str) -> None:
    """Make `name` the theme for this process. Before any widget is built."""
    global _active, _map, _focus_rgb
    _active = name if name in THEMES else DEFAULT
    _map = build_map(_active)
    if _map:
        focus = QColor(_map[_FOCUS])
        _focus_rgb = f"{focus.red()}, {focus.green()}, {focus.blue()}"
    from PySide6.QtWidgets import QApplication, QWidget
    for cls in (QWidget, QApplication):
        original = getattr(cls, "_unthemed_setStyleSheet", None) or cls.setStyleSheet
        cls._unthemed_setStyleSheet = original
        cls.setStyleSheet = (lambda original: lambda self, text: original(self, css(text)))(original)


def _load() -> None:
    # Through SettingsManager rather than a QSettings of its own, so the test
    # suite's private settings copy (tests/__init__.py) is the one read.
    from settings import SettingsManager
    install(SettingsManager().string_value(SETTING, DEFAULT))


_load()


if __name__ == "__main__":
    for theme_name in THEMES:
        table = build_map(theme_name)
        # A theme's output must never be another key, or a colour that passes
        # through css() twice (a constant baked into a stylesheet) moves twice.
        clashes = [v for k, v in table.items() if v in table and v != k]
        assert not clashes, (theme_name, clashes)
    install("taiko")
    assert css("a{color:#F3A6BD; b: #ff66aa55; c: rgba(255,102,170,13); d: #e54c2e}") == \
        "a{color:#d4432a; b: #5aa7c755; c: rgba(90, 167, 199,13); d: #e54c2e}", css("a{color:#F3A6BD; b: #ff66aa55; c: rgba(255,102,170,13); d: #e54c2e}")
    install("pink")
    assert css("#f3a6bd") == "#f3a6bd"
    print("ok", {name: len(build_map(name)) for name in THEMES})
