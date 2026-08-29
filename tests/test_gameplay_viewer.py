"""M6 gameplay viewer: an osu!taiko-like preview where every object approaches
the hit position at the speed in force at *its own* time.

That per-object rule is the whole feature. A chart view shows notes on a time
axis, where an SV change is invisible by construction; integrating one shared
scroll position instead would be osu!mania's model, in which objects can never
pass each other. So most of these tests pin positions against the full_v14
fixture, whose timing points were written for exactly this kind of arithmetic:

    0     500ms/beat (120 BPM), uninherited
    2000  SV 0.75, inherited, kiai
    4000  SV 2.00, inherited
    6000  400ms/beat (150 BPM), uninherited, omit-first-barline
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QWheelEvent
from PySide6.QtWidgets import QApplication

import gui
import skin
from model.hit_object import HitObject
from osu_io.parser import parse_osu
from osu_io.timing import TimingPoint
from tests.osu_fixtures import write_fixture

_APP: QApplication | None = None


def setUpModule() -> None:
    global _APP
    _APP = QApplication.instance() or QApplication([])


def _wheel(view, delta: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    event = QWheelEvent(
        QPointF(100.0, 50.0), QPointF(100.0, 50.0), QPoint(0, 0), QPoint(0, delta),
        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
    )
    view.wheelEvent(event)


def _viewer(document) -> gui.GameplayViewerView:
    view = gui.GameplayViewerView()
    view.resize(800, 200)
    view.load_document(document)
    return view


class ScrollVelocityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        path = write_fixture(Path(self._temp.name), "full_v14")
        self.view = _viewer(parse_osu(path))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_velocity_is_one_beat_per_beat_length_at_1x(self):
        """Scroll speed is BPM x SV, so one beat covers the same distance at
        every BPM -- that is what makes px-per-beat a constant."""
        self.assertAlmostEqual(self.view.velocity_at(1000.0), 1 / 500.0, places=9)
        # 6000 onward is 150 BPM (400ms/beat), and the red line resets SV.
        self.assertAlmostEqual(self.view.velocity_at(6400.0), 1 / 400.0, places=9)

    def test_sv_scales_the_velocity_of_the_section_it_governs(self):
        self.assertAlmostEqual(self.view.velocity_at(2500.0), 0.75 / 500.0, places=9)
        self.assertAlmostEqual(self.view.velocity_at(4500.0), 2.0 / 500.0, places=9)

    def test_each_object_uses_the_speed_at_its_own_time(self):
        """The osu!taiko rule, and the reason this is not an integral: a note
        in a 2.0x section is twice as far out as one the same time away in a
        1.0x section, whatever lies between them and the playhead."""
        self.view.current_time = 0.0
        hit_x = self.view.width() * gui.GAMEPLAY_HIT_X_RATIO
        slow = self.view.x_for_time(1000.0) - hit_x  # 1.0x
        fast = self.view.x_for_time(4500.0) - hit_x  # 2.0x
        self.assertAlmostEqual(slow, 1000.0 / 500.0 * self.view.px_per_beat, places=6)
        self.assertAlmostEqual(fast, 4500.0 * 2.0 / 500.0 * self.view.px_per_beat, places=6)

    def test_a_green_line_moves_the_note_sitting_on_it(self):
        """'Every note to the right follows this speed, including the current
        note the green line is on.'"""
        self.view.current_time = 0.0
        before = self.view.x_for_time(4000.0)
        point = next(p for p in self.view.timing_points if p.time == 4000.0)
        point.set_sv(point.sv_multiplier * 2)
        self.view._rebuild_velocities()
        self.assertAlmostEqual(self.view.x_for_time(4000.0) - self.view._hit_x(),
                               (before - self.view._hit_x()) * 2, places=6)

    def test_a_green_line_leaves_earlier_notes_alone(self):
        self.view.current_time = 0.0
        before = self.view.x_for_time(1000.0)
        point = next(p for p in self.view.timing_points if p.time == 4000.0)
        point.set_sv(point.sv_multiplier * 2)
        self.view._rebuild_velocities()
        self.assertAlmostEqual(self.view.x_for_time(1000.0), before, places=6)

    def test_the_current_time_sits_on_the_hit_position(self):
        self.view.current_time = 3000.0
        self.assertAlmostEqual(
            self.view.x_for_time(3000.0), self.view.width() * gui.GAMEPLAY_HIT_X_RATIO, places=6
        )

    def test_scroll_speed_only_scales_the_picture(self):
        """Ctrl+wheel must not change *where* in the map the view is, only how
        much of it fits -- velocity is stored in beats for this reason."""
        self.view.current_time = 3000.0
        before = self.view.velocity_at(5000.0)
        self.view.px_per_beat *= 2.0
        self.assertAlmostEqual(self.view.velocity_at(5000.0), before, places=9)
        hit_x = self.view.width() * gui.GAMEPLAY_HIT_X_RATIO
        self.assertAlmostEqual(self.view.x_for_time(3000.0), hit_x, places=6)

    def test_a_zero_beat_length_does_not_divide_by_zero(self):
        """The invisible-note gimmick authors beat_length near zero, and a
        hand-edited map can carry a literal 0."""
        self.view.timing_points[0].beat_length = 0.0
        self.view._rebuild_velocities()
        self.assertTrue(all(velocity > 0 for velocity in self.view._velocities))


class TaikoProportionTests(unittest.TestCase):
    """Sizes and speeds pinned to osu!taiko's own constants.

    The preview exists to answer "what will this look like in game", so the
    numbers it draws with are the ruleset's, not ones chosen to look right --
    and a test is the only thing that stops them drifting back.
    """

    def test_a_note_is_the_rulesets_fraction_of_the_lane(self):
        """`TaikoHitObject.DEFAULT_SIZE = 0.475f`, a fraction of the playfield
        height. It used to be `min(30.0, height * 0.19)`, which stopped growing
        with the view entirely once it was tall enough."""
        self.assertAlmostEqual(gui.TAIKO_NOTE_SIZE, 0.475, places=6)
        for height in (120, 180, 400, 900):
            with self.subTest(height=height):
                self.assertAlmostEqual(
                    height * gui.TAIKO_NOTE_SIZE / 2.0, height * 0.2375, places=6)

    def test_a_finisher_is_the_rulesets_strong_scale(self):
        """`TaikoStrongableHitObject.STRONG_SCALE = 1 / 0.65f`. 1.4 was assumed
        here before, which drew every big note about 10% small."""
        self.assertAlmostEqual(gui.TAIKO_STRONG_SCALE, 1.0 / 0.65, places=6)
        self.assertGreater(gui.TAIKO_STRONG_SCALE, 1.5)


class SkinnedCircleElementTests(unittest.TestCase):
    """Which circle artwork each note kind asks the skin for.

    `taikobigcircle` is a separate file that skinners **redraw** rather than
    scale -- a different shape, an extra ring, a different rim -- so drawing
    `taikohitcircle` at the finisher diameter is the wrong art at the right
    size. Big drumrolls were doing exactly that: the head went through
    `_draw_skinned_roll`, which never took the flag.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        folder = Path(self._temp.name)
        for name in ("taikohitcircle", "taikohitcircleoverlay",
                     "taikobigcircle", "taikobigcircleoverlay",
                     "taiko-roll-middle", "taiko-roll-end"):
            image = QImage(16, 16, QImage.Format_ARGB32)
            image.fill(QColor("white"))
            image.save(str(folder / f"{name}.png"))
        self.skin = skin.TaikoSkin(folder)
        self.asked: list[str] = []
        inner = self.skin.scaled
        self.skin.scaled = lambda element, diameter, colour=None: (
            self.asked.append(element) or inner(element, diameter, colour))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _circles_drawn_for(self, note) -> list[str]:
        view = gui.GameplayViewerView()
        view.resize(800, 200)
        view.timing_points = [TimingPoint(time=0.0, beat_length=500.0)]
        view.beat_points = list(view.timing_points)
        view._beat_times = [0.0]
        view.slider_multiplier = 1.4
        view._rebuild_velocities()
        view.set_skin(self.skin)
        view.notes = [note]
        view.note_times = [note.time]
        view._end_times = {}
        view._phantom_ends = {}
        view._max_extend_ms = 0.0
        end = view._compute_end_time(note)
        if end is not None:
            view._end_times[note.uid] = end
            view._max_extend_ms = end - note.time
        # A millisecond short of the target: a circle *on* it has been hit and
        # is no longer drawn, which is what HitPositionTests covers.
        view.current_time = float(note.time) - 1.0
        self.asked.clear()
        view.grab()
        # `approachcircle` is the hit target, not a note; it is asked for on
        # every frame regardless of what is on screen.
        return [e for e in self.asked if e.startswith("taiko") and "circle" in e]

    @staticmethod
    def _circle(finisher: bool):
        return HitObject(x=256, y=192, time=1000, type=1,
                         hit_sound=4 if finisher else 0)

    @staticmethod
    def _drumroll(finisher: bool):
        return HitObject(x=256, y=192, time=1000, type=2,
                         hit_sound=4 if finisher else 0,
                         extras=("L|624:192", "1", "200.0"))

    def test_a_normal_note_uses_the_small_circle(self):
        self.assertEqual(self._circles_drawn_for(self._circle(False)),
                         ["taikohitcircle", "taikohitcircleoverlay"])

    def test_a_finisher_uses_the_big_circle(self):
        self.assertEqual(self._circles_drawn_for(self._circle(True)),
                         ["taikobigcircle", "taikobigcircleoverlay"])

    def test_a_normal_drumroll_head_uses_the_small_circle(self):
        self.assertEqual(self._circles_drawn_for(self._drumroll(False)),
                         ["taikohitcircle", "taikohitcircleoverlay"])

    def test_a_big_drumroll_head_uses_the_big_circle(self):
        """The one that was wrong: the right diameter, the wrong file."""
        self.assertEqual(self._circles_drawn_for(self._drumroll(True)),
                         ["taikobigcircle", "taikobigcircleoverlay"])


class HitPositionTests(unittest.TestCase):
    """What reaching the hit position does to each kind of object.

    osu!taiko takes a circle away the moment it lands: that is what being hit
    looks like, and the explosion is the only thing left saying it happened. A
    drumroll is still being hit while its body crosses the target, so it
    travels straight through; a spinner occupies its span in place.
    """

    def _view(self, notes, at):
        view = gui.GameplayViewerView()
        view.resize(900, 200)
        point = TimingPoint(time=0.0, beat_length=500.0, meter=4)
        view.timing_points = [point]
        view.beat_points = [point]
        view._beat_times = [0.0]
        view.slider_multiplier = 1.4
        view._rebuild_velocities()
        view.notes = sorted(notes, key=lambda n: n.time)
        view.note_times = [n.time for n in view.notes]
        view._end_times, view._phantom_ends, view._max_extend_ms = {}, {}, 0.0
        for note in view.notes:
            end = view._compute_end_time(note)
            if end is not None:
                view._end_times[note.uid] = end
                view._max_extend_ms = max(view._max_extend_ms, end - note.time)
        view.current_time = at
        return view

    @staticmethod
    def _circle(time_ms, finisher=False):
        return HitObject(x=256, y=192, time=time_ms, type=1,
                         hit_sound=4 if finisher else 0)

    @staticmethod
    def _drumroll(time_ms):
        return HitObject(x=256, y=192, time=time_ms, type=2, hit_sound=0,
                         extras=("L|624:192", "1", "400.0"))

    @staticmethod
    def _spinner(time_ms, end_ms):
        return HitObject(x=256, y=192, time=time_ms, type=8, hit_sound=0,
                         extras=(str(end_ms),))

    def test_a_circle_is_gone_the_moment_it_lands(self):
        note = self._circle(1000)
        view = self._view([note], at=1000.0)
        self.assertTrue(view._has_been_hit(note))

    def test_a_circle_short_of_the_target_is_still_drawn(self):
        note = self._circle(1000)
        view = self._view([note], at=999.0)
        self.assertFalse(view._has_been_hit(note))

    def test_a_finisher_goes_the_same_way(self):
        note = self._circle(1000, finisher=True)
        view = self._view([note], at=1000.0)
        self.assertTrue(view._has_been_hit(note))

    def test_a_drumroll_passes_through_to_the_back(self):
        """Its body is still being hit as it crosses, so it keeps travelling."""
        note = self._drumroll(1000)
        view = self._view([note], at=1400.0)
        self.assertFalse(view._has_been_hit(note))

    def test_a_spinner_is_unchanged(self):
        note = self._spinner(1000, 2000)
        view = self._view([note], at=1500.0)
        self.assertFalse(view._has_been_hit(note))

    def test_a_hit_circle_is_not_painted_and_its_neighbour_still_is(self):
        """Through the paint path rather than the predicate, because the flash
        pass iterates the same notes separately and has to agree with it."""
        landed, coming = self._circle(1000), self._circle(2000)
        view = self._view([landed, coming], at=1000.0)
        drawn = []
        original = gui.draw_note_sprite

        def spy(painter, fill, outline, x, y, radius, skin=None, big=False):
            drawn.append(round(x))
            return original(painter, fill, outline, x, y, radius, skin, big)

        gui.draw_note_sprite = spy
        try:
            view.grab()
        finally:
            gui.draw_note_sprite = original
        self.assertEqual(len(drawn), 1, "only the note still approaching")
        self.assertGreater(drawn[0], view._hit_x())

    @staticmethod
    def _fake(time_ms, length=-400.0):
        return HitObject(x=256, y=192, time=time_ms, type=2, hit_sound=0,
                         extras=("L|624:192", "1", repr(length)))

    def test_a_fake_slider_loses_its_head_at_the_target(self):
        note = self._fake(1000)
        view = self._view([note], at=1000.0)
        self.assertTrue(view._has_been_hit(note))

    def test_a_fake_slider_still_short_of_it_keeps_its_head(self):
        note = self._fake(1000)
        view = self._view([note], at=999.0)
        self.assertFalse(view._has_been_hit(note))

    def _what_is_drawn(self, note, at):
        """(skin elements asked for, phantom bodies, note heads) for one frame."""
        view = self._view([note], at=at)
        phantom = view._compute_phantom_end(note)
        if phantom is not None:
            view._phantom_ends[note.uid] = phantom
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("taikohitcircle", "taikohitcircleoverlay",
                         "taiko-roll-middle", "taiko-roll-end"):
                image = QImage(32, 32, QImage.Format_ARGB32)
                image.fill(QColor("white"))
                image.save(str(folder / f"{name}.png"))
            loaded = skin.TaikoSkin(folder)
            asked = []
            inner = loaded.scaled
            loaded.scaled = lambda e, d, c=None: (asked.append(e) or inner(e, d, c))
            view.set_skin(loaded)

            bodies, heads = [], []
            body = gui.GameplayViewerView._draw_phantom_body
            sprite = gui.draw_note_sprite
            gui.GameplayViewerView._draw_phantom_body = (
                lambda s, *a: bodies.append(1))
            gui.draw_note_sprite = (
                lambda *a, **k: heads.append(1))
            try:
                view.grab()
            finally:
                gui.GameplayViewerView._draw_phantom_body = body
                gui.draw_note_sprite = sprite
        return asked, bodies, heads

    def test_before_the_target_it_draws_its_head_and_its_extent(self):
        note = self._fake(1000)
        asked, bodies, heads = self._what_is_drawn(note, at=900.0)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(len(heads), 1)

    def test_past_the_target_only_the_cap_carries_on(self):
        """The head is played and gone the way a circle's is, and the backwards
        extent goes with it; the cap travels out to the left on its own."""
        note = self._fake(1000)
        asked, bodies, heads = self._what_is_drawn(note, at=1200.0)
        self.assertIn("taiko-roll-end", asked)
        self.assertNotIn("taikohitcircle", asked)
        self.assertEqual(bodies, [], "no backwards extent")
        self.assertEqual(heads, [], "no head")


class DrumrollPartsTests(unittest.TestCase):
    """A drumroll is `taikohitcircle` + `taiko-roll-middle` + `taiko-roll-end`,
    a fake slider included, in **both** views that draw notes.

    A fake slider's length is zero or negative, so its body has no width and
    only the caps land; drawing the cap only when there was a body to cap left
    it a bare head. And the editor timeline drew its own bar beside a skinned
    head, which was the one place the two views disagreed about the object.
    """

    ELEMENTS = ("taikohitcircle", "taikohitcircleoverlay",
                "taiko-roll-middle", "taiko-roll-end")

    def _skin_with_spy(self, folder):
        for name in self.ELEMENTS:
            image = QImage(32, 32, QImage.Format_ARGB32)
            image.fill(QColor("white"))
            image.save(str(folder / f"{name}.png"))
        loaded = skin.TaikoSkin(folder)
        asked = []
        inner = loaded.scaled
        loaded.scaled = lambda e, d, c=None: (asked.append((e, c)) or inner(e, d, c))
        return loaded, asked

    def _preview_parts(self, length):
        with tempfile.TemporaryDirectory() as directory:
            loaded, asked = self._skin_with_spy(Path(directory))
            view = gui.GameplayViewerView()
            view.resize(900, 200)
            point = TimingPoint(time=0.0, beat_length=500.0, meter=4)
            view.timing_points = [point]
            view.beat_points = [point]
            view._beat_times = [0.0]
            view.slider_multiplier = 1.4
            view._rebuild_velocities()
            view.set_skin(loaded)
            note = HitObject(x=256, y=192, time=1500, type=2, hit_sound=0,
                             extras=("L|624:192", "1", repr(length)))
            view.notes = [note]
            view.note_times = [1500.0]
            view._end_times, view._phantom_ends, view._max_extend_ms = {}, {}, 0.0
            end = view._compute_end_time(note)
            if end is not None:
                view._end_times[note.uid] = end
                view._max_extend_ms = end - note.time
            else:
                phantom = view._compute_phantom_end(note)
                if phantom is not None:
                    view._phantom_ends[note.uid] = phantom
            view.current_time = 1000.0
            asked.clear()
            view.grab()
            return asked

    def test_a_real_drumroll_draws_all_three_pieces(self):
        names = [element for element, _colour in self._preview_parts(400.0)]
        for piece in ("taiko-roll-middle", "taiko-roll-end", "taikohitcircle"):
            with self.subTest(piece=piece):
                self.assertIn(piece, names)

    def test_a_negative_length_slider_still_gets_its_cap(self):
        """A negative length collapses the *track*, not the cap: `max(end_x,
        x)` clamps the body to nothing, so the cap butts onto the head and
        reaches to its right, which is where osu! draws it."""
        for length in (-400.0, 0.0):
            with self.subTest(length=length):
                names = [element for element, _c in self._preview_parts(length)]
                self.assertIn("taiko-roll-end", names)
                self.assertIn("taikohitcircle", names)

    def test_every_piece_takes_the_drumroll_colour(self):
        for element, colour in self._preview_parts(400.0):
            if element in ("taiko-roll-middle", "taiko-roll-end", "taikohitcircle"):
                with self.subTest(element=element):
                    self.assertIsNotNone(colour, "tinted, not raw art")
                    self.assertEqual(
                        (colour.red(), colour.green(), colour.blue()),
                        gui.DRUMROLL_COLOR,
                    )

    def test_the_editor_timeline_draws_the_same_pieces(self):
        """The skin is meant to reach every view that draws a note."""
        with tempfile.TemporaryDirectory() as directory:
            loaded, asked = self._skin_with_spy(Path(directory))
            document = parse_osu(write_fixture(Path(directory), "full_v14"))
            document.hit_objects = [
                HitObject(x=256, y=192, time=2000, type=2, hit_sound=0,
                          extras=("L|624:192", "1", "400.0"))
            ]
            view = gui.TimelineGameplay()
            view.resize(900, 200)
            view.skin = loaded
            view.load_document(document)
            view.current_time = 2000.0
            asked.clear()
            view.grab()
        names = [element for element, _colour in asked]
        self.assertIn("taiko-roll-middle", names)
        self.assertIn("taiko-roll-end", names)


class DrawOrderTests(unittest.TestCase):
    """Bottom to top: anything with a body, then fake sliders, then the
    hittable notes. The note is the thing being played, so it stays readable
    and the decoration stacked around it sits behind."""

    def _order(self, notes, at):
        view = gui.GameplayViewerView()
        view.resize(900, 200)
        point = TimingPoint(time=0.0, beat_length=500.0, meter=4)
        view.timing_points = [point]
        view.beat_points = [point]
        view._beat_times = [0.0]
        view.slider_multiplier = 1.4
        view._rebuild_velocities()
        view.notes = sorted(notes, key=lambda n: n.time)
        view.note_times = [n.time for n in view.notes]
        view._end_times, view._phantom_ends, view._max_extend_ms = {}, {}, 0.0
        for note in view.notes:
            end = view._compute_end_time(note)
            if end is not None:
                view._end_times[note.uid] = end
                view._max_extend_ms = max(view._max_extend_ms, end - note.time)
            else:
                phantom = view._compute_phantom_end(note)
                if phantom is not None:
                    view._phantom_ends[note.uid] = phantom
        view.current_time = at
        painted = []
        original = gui.GameplayViewerView._draw_note

        def spy(self, painter, note, *args):
            painted.append(note.uid)
            return original(self, painter, note, *args)

        gui.GameplayViewerView._draw_note = spy
        try:
            view.grab()
        finally:
            gui.GameplayViewerView._draw_note = original
        return painted

    def test_a_note_is_painted_after_the_fake_slider_stacked_on_it(self):
        circle = HitObject(x=256, y=192, time=1500, type=1, hit_sound=0)
        fake = HitObject(x=256, y=192, time=1500, type=2, hit_sound=0,
                         extras=("L|624:192", "1", "-400.0"))
        order = self._order([circle, fake], at=1000.0)
        self.assertLess(order.index(fake.uid), order.index(circle.uid))

    def test_a_real_drumroll_is_painted_before_both(self):
        circle = HitObject(x=256, y=192, time=1500, type=1, hit_sound=0)
        fake = HitObject(x=256, y=192, time=1500, type=2, hit_sound=0,
                         extras=("L|624:192", "1", "-400.0"))
        roll = HitObject(x=256, y=192, time=1500, type=2, hit_sound=0,
                         extras=("L|624:192", "1", "400.0"))
        order = self._order([circle, fake, roll], at=1000.0)
        self.assertLess(order.index(roll.uid), order.index(fake.uid))
        self.assertLess(order.index(fake.uid), order.index(circle.uid))

    def test_a_spinner_goes_down_with_the_bodies(self):
        circle = HitObject(x=256, y=192, time=1500, type=1, hit_sound=0)
        spinner = HitObject(x=256, y=192, time=1500, type=8, hit_sound=0,
                            extras=("2500",))
        order = self._order([circle, spinner], at=1000.0)
        self.assertLess(order.index(spinner.uid), order.index(circle.uid))


class PlayfieldTests(unittest.TestCase):
    """The skin's playfield, from the wiki's own element list.

    Each element is optional on its own -- the same per-element fallback the
    notes use -- so what is pinned here is *which* element is asked for and
    *when*, not that any particular skin supplies it.
    """

    ELEMENTS = (
        "taiko-slider", "taiko-bar-right", "taiko-bar-right-glow",
        "taiko-barline", "approachcircle",
        "taikohitcircle", "taikohitcircleoverlay",
    )

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        folder = Path(self._temp.name)
        for name in self.ELEMENTS:
            image = QImage(32, 32, QImage.Format_ARGB32)
            image.fill(QColor("white"))
            image.save(str(folder / f"{name}.png"))
        self.skin = skin.TaikoSkin(folder)
        self.asked: list[str] = []
        for method in ("scaled", "stretched"):
            inner = getattr(self.skin, method)
            setattr(self.skin, method, self._spy(inner))

    def _spy(self, inner):
        def wrapped(element, *args, **kwargs):
            self.asked.append(element)
            return inner(element, *args, **kwargs)
        return wrapped

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _view(self, kiai: bool, at: float = 1000.0):
        view = gui.GameplayViewerView()
        view.resize(900, 200)
        point = TimingPoint(time=0.0, beat_length=500.0, meter=4)
        view.timing_points = [point]
        view.beat_points = [point]
        view._beat_times = [0.0]
        view.slider_multiplier = 1.4
        view.kiai_bands = [(0, 20000)] if kiai else []
        view._rebuild_velocities()
        view.set_skin(self.skin)
        view.notes = []
        view.note_times = []
        view._end_times, view._phantom_ends, view._max_extend_ms = {}, {}, 0.0
        view.current_time = at
        self.asked.clear()
        view.grab()
        return view

    def test_the_playfield_is_drawn_outside_kiai_too(self):
        self._view(kiai=False)
        for element in ("taiko-slider", "taiko-bar-right", "approachcircle"):
            with self.subTest(element=element):
                self.assertIn(element, self.asked)

    def test_the_lane_kiai_overlay_is_only_asked_for_during_kiai(self):
        self._view(kiai=False)
        self.assertNotIn("taiko-bar-right-glow", self.asked)
        self._view(kiai=True, at=0.0)
        self.assertIn("taiko-bar-right-glow", self.asked)

    def test_no_glow_is_drawn_at_the_hit_target(self):
        """`taiko-glow` and `lighting` are a bloom around a marker where
        nothing is judged. Kiai is the notes pulsing and the lane's own kiai
        state, both of which mean something."""
        self._view(kiai=True, at=0.0)
        for element in ("taiko-glow", "lighting"):
            with self.subTest(element=element):
                self.assertNotIn(element, self.asked)

    def test_the_bar_is_stretched_to_the_whole_view(self):
        """1024x200 art "stretched to fit screen width" -- aspect deliberately
        ignored, which is why it does not go through `scaled`."""
        view = self._view(kiai=False)
        bar = self.skin.stretched("taiko-bar-right", view.width(), view.height())
        self.assertEqual((bar.width(), bar.height()), (view.width(), view.height()))

    def test_the_input_drum_is_not_drawn(self):
        """The wiki files it under the playfield; it is where the player hits,
        not where the notes are, and nobody is hitting this preview."""
        self._view(kiai=False)
        for element in ("taiko-bar-left", "taiko-drum-inner", "taiko-drum-outer"):
            with self.subTest(element=element):
                self.assertNotIn(element, self.asked)

    def test_a_skin_with_no_playfield_still_draws_the_notes(self):
        """Per element, not per skin: the whole point of the fallback."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            image = QImage(32, 32, QImage.Format_ARGB32)
            image.fill(QColor("white"))
            image.save(str(folder / "taikohitcircle.png"))
            self.skin = skin.TaikoSkin(folder)
            view = self._view(kiai=False)
            self.assertEqual(view.width(), 900)   # painted without raising


class PlayfieldKiaiWashTests(unittest.TestCase):
    """The built-in white wash is a stand-in for the skin's own kiai art.
    Drawn as well as it, a skinned lane would be washed twice over."""

    def _corner_lift(self, elements) -> int:
        """How much the far corner of the lane brightens on the beat.

        The corner, deliberately: nothing else that pulses reaches the far end
        of the lane, so a change there is the full-rect wash and nothing else.
        """
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in elements:
                image = QImage(8, 8, QImage.Format_ARGB32)
                image.fill(QColor("white"))
                image.save(str(folder / f"{name}.png"))
            loaded = skin.TaikoSkin(folder)

            def render(at):
                view = gui.GameplayViewerView()
                view.resize(400, 200)
                point = TimingPoint(time=0.0, beat_length=500.0, meter=4)
                view.timing_points = [point]
                view.beat_points = [point]
                view._beat_times = [0.0]
                view.kiai_bands = [(0, 20000)]
                view._rebuild_velocities()
                view.set_skin(loaded)
                view.notes, view.note_times = [], []
                view._end_times, view._phantom_ends, view._max_extend_ms = {}, {}, 0.0
                view.current_time = at
                return view.grab().toImage().pixelColor(399, 4)

            on_beat = render(0.0)          # pulse 1.0
            off_beat = render(499.9)       # pulse ~0.0
            return on_beat.red() - off_beat.red()

    def test_a_skin_with_no_kiai_art_gets_the_built_in_wash(self):
        self.assertGreater(self._corner_lift(["taikohitcircle"]), 0)

    def test_a_skin_that_lights_its_own_lane_is_not_washed_as_well(self):
        self.assertEqual(
            self._corner_lift(["taikohitcircle", "taiko-bar-right-glow"]), 0)


class SliderMultiplierScrollTests(unittest.TestCase):
    """The map's SliderMultiplier has to reach the scroll speed.

    osu!taiko's scroll distance is `100 * SliderMultiplier * 1.4 * ScrollSpeed
    / beatLength`. This view left SliderMultiplier out of it, so two maps that
    scroll three times apart in game previewed at identical spacing.
    """

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._path = write_fixture(Path(self._temp.name), "full_v14")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _view_with_multiplier(self, multiplier: float):
        document = parse_osu(self._path)
        document.slider_multiplier = multiplier
        return _viewer(document)

    def test_a_faster_multiplier_scrolls_proportionally_faster(self):
        slow = self._view_with_multiplier(0.7)
        fast = self._view_with_multiplier(2.1)
        self.assertAlmostEqual(
            fast.velocity_at(1000.0) / slow.velocity_at(1000.0), 3.0, places=6)

    def test_the_assumed_multiplier_is_the_unchanged_baseline(self):
        """Normalised against the value assumed before the parser read the real
        one, so an ordinary map previews at the scale it always did."""
        view = self._view_with_multiplier(gui.SLIDER_MULTIPLIER_ASSUMED)
        self.assertAlmostEqual(view._scroll_scale, 1.0, places=9)
        self.assertAlmostEqual(view.velocity_at(1000.0), 1 / 500.0, places=9)

    def test_a_nonsense_multiplier_cannot_stop_the_chart(self):
        """Hand-edited maps carry zeroes; a zero here would freeze every object
        on the hit position and divide by nothing downstream."""
        view = self._view_with_multiplier(0.0)
        self.assertGreater(view.velocity_at(1000.0), 0.0)


class BarlineTests(unittest.TestCase):
    """`barline_times` answers for the frame at `current_time`, so each test
    parks the playhead inside the range it asks about. A section is only walked
    across the time it is on screen for at its own velocity -- without that, one
    absurd-BPM section spends the whole per-frame budget off the left edge."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        path = write_fixture(Path(self._temp.name), "full_v14")
        self.view = _viewer(parse_osu(path))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_a_barline_every_meter_beats_from_an_uninherited_point(self):
        # 500ms/beat, meter 4 -> every 2000ms, up to the next red line at 6000.
        self.view.current_time = 2000.0
        self.assertEqual(self.view.barline_times(0.0, 5999.0), [0.0, 2000.0, 4000.0])

    def test_the_range_is_respected_without_walking_the_whole_section(self):
        self.view.current_time = 3500.0
        self.assertEqual(self.view.barline_times(2500.0, 4500.0), [4000.0])

    def test_omit_first_barline_drops_the_line_on_the_point_only(self):
        """The 6000ms red line sets effects bit 3; barline gimmicks set it in
        bulk, so its later measure lines must still appear."""
        self.view.current_time = 7750.0
        times = self.view.barline_times(6000.0, 9500.0)
        self.assertNotIn(6000.0, times)
        # 400ms/beat, meter 4 -> every 1600ms.
        self.assertEqual(times, [7600.0, 9200.0])

    def test_clearing_the_flag_brings_the_points_own_barline_back(self):
        point = next(p for p in self.view.beat_points if p.time == 6000.0)
        point.set_omit_first_barline(False)
        self.view.current_time = 6000.0
        self.assertIn(6000.0, self.view.barline_times(6000.0, 9500.0))

    def test_a_map_with_no_timing_points_draws_no_barlines(self):
        self.view.beat_points = []
        self.assertEqual(self.view.barline_times(0.0, 10000.0), [])

    def _document(self, *points: gui.TimingPoint):
        document = parse_osu(write_fixture(Path(self._temp.name), "full_v14"))
        document.timing_points = list(points)
        return document

    @staticmethod
    def _green(time_ms: float, sv: float) -> gui.TimingPoint:
        """An inherited point. TimingPoint defaults uninherited_flag to 1, so a
        negative beat_length alone still reads as a red line."""
        return gui.TimingPoint(time=time_ms, beat_length=-100.0 / sv, uninherited_flag=0)

    def test_a_green_line_inside_a_section_does_not_cut_its_barlines_short(self):
        """Spacing was judged from the velocity at the *red* line, but a green
        line partway through moves every measure line after it. At 0.25x the
        lines sit four times closer, so four times as many fit on screen -- the
        old reach and line count were both a quarter of what was needed, and the
        right-hand half of the screen lost its barlines."""
        view = _viewer(
            self._document(gui.TimingPoint(time=0.0, beat_length=500.0), self._green(4000.0, 0.25))
        )
        view.current_time = 10000.0
        times = view.barline_times(*view.visible_time_range())

        # 0.25x/500ms-beat -> 0.1 px/ms, so a 2000ms measure spans 200px and the
        # 800px view reaches thousands of ms past where the old window stopped.
        self.assertIn(12000.0, times, "barlines stop short of the right edge")
        self.assertIn(14000.0, times)
        self.assertIn(16000.0, times)
        for time_ms in times:
            self.assertAlmostEqual(time_ms % 2000.0, 0.0, places=6)

    def test_a_crawling_section_gets_every_line_that_fits_not_a_fixed_few(self):
        """The count is geometry, not a budget. At 0.01x a 2000ms measure is 8px,
        so dozens of barlines are genuinely on screen at once -- the old code
        sized the walk from the red line's velocity and handed back three."""
        view = _viewer(
            self._document(gui.TimingPoint(time=0.0, beat_length=500.0), self._green(1000.0, 0.01))
        )
        view.current_time = 30000.0
        times = view.barline_times(*view.visible_time_range())

        self.assertGreater(len(times), 20, "a 0.01x section fits far more than a handful")
        # Both of these sit within the 800px view; the old walk reached neither.
        self.assertIn(2000.0, times)
        self.assertIn(58000.0, times)
        self.assertEqual(len(times), len(set(times)))


class PaintAndInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        path = write_fixture(Path(self._temp.name), "full_v14")
        self.view = _viewer(parse_osu(path))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_painting_every_note_shape_does_not_raise(self):
        for time_ms in (0.0, 1500.0, 3500.0, 5500.0, 54692.0):
            with self.subTest(time_ms=time_ms):
                self.view.current_time = time_ms
                self.view.grab()

    def test_painting_a_near_stopped_section_does_not_hang(self):
        """A 0.01x section puts an unbounded amount of *time* on one screen;
        the visible range is capped rather than scanning the whole map."""
        self.view.timing_points[1].set_sv(0.01)
        self.view._rebuild_velocities()
        self.view.current_time = 2000.0
        start, end = self.view.visible_time_range()
        self.assertLessEqual(end - start, 2 * gui.GAMEPLAY_MAX_LOOKAHEAD_MS)
        self.view.grab()

    def test_a_barline_gimmick_section_does_not_generate_millions_of_lines(self):
        """Absurd-BPM red lines put a measure every 0.0004ms. Past the right
        edge none of them is visible, and asking for the time range's worth
        froze the view.

        There is no line-count cap to lean on -- the bound is the screen, since
        two lines rounding to the same pixel column are one line."""
        gimmick = [gui.TimingPoint(time=0.0, beat_length=500.0)]
        for i in range(200):
            gimmick.append(gui.TimingPoint(time=1000.0 + i * 5.0, beat_length=0.0001))
        self.view.timing_points = gimmick
        self.view.beat_points = list(gimmick)
        self.view._beat_times = [point.time for point in gimmick]
        self.view._rebuild_velocities()
        self.view.current_time = 1000.0
        start, end = self.view.visible_time_range()
        times = self.view.barline_times(start, end)
        self.assertLessEqual(len(times), self.view.width() + 4)
        self.assertEqual(len(times), len(set(times)))
        self.view.grab()

    def _fake_slider(self, length: float, time_ms: int = 1500, slides: int = 1):
        """The shape of a real fake-slider line, extras and all.

            256,192,54692,2,12,L|624:192,643,-0.0001

        `length` and `slides` are read off `extras`, so building the note any
        other way would not exercise the path the parser produces.
        """
        from model.hit_object import HitObject

        return HitObject(
            x=256, y=192, time=time_ms, type=2, hit_sound=12,
            extras=("L|624:192", str(slides), repr(length)),
        )

    def test_a_negative_length_slider_reaches_back_before_its_own_head(self):
        """A fake slider is a drumroll whose length is negative, so the duration
        derived from it is negative and the object ends before it starts. No
        tick is ever generated, which is why nothing is hittable while osu!
        still draws the head."""
        note = self._fake_slider(-50.0)
        self.view.notes = [note]
        self.view.note_times = [note.time]
        self.view._end_times = {}
        self.view._phantom_ends = {}
        phantom = self.view._compute_phantom_end(note)

        self.assertIsNotNone(phantom)
        self.assertLess(phantom, note.time)
        self.assertIsNone(
            self.view._compute_end_time(note),
            "a negative length must never be mistaken for a playable drumroll",
        )

    def test_a_positive_length_slider_has_no_phantom_body(self):
        """The other half of the same rule: a positive length is a real,
        scoreable drumroll and must be drawn forwards, not backwards."""
        note = self._fake_slider(50.0)
        self.assertIsNone(self.view._compute_phantom_end(note))
        self.assertIsNotNone(self.view._compute_end_time(note))

    def test_the_phantom_extent_is_to_scale_not_padded_to_visibility(self):
        """The canonical `-0.0001` fake slider reaches about a tenth of a pixel
        behind its head, and showing it larger would be inventing geometry the
        map does not have. Its invisibility is the trick working."""
        tiny = self._fake_slider(-0.0001, slides=643)
        big = self._fake_slider(-500.0)
        self.view.current_time = 0.0

        def extent(note):
            end = self.view._compute_phantom_end(note)
            velocity = self.view.velocity_at(note.time)
            return abs(end - note.time) * velocity * self.view.px_per_beat

        self.assertLess(extent(tiny), 1.0)
        self.assertGreater(extent(big), 100.0)

    def test_painting_a_fake_slider_stack_does_not_raise(self):
        """A shiny note is a stack of fake sliders on one millisecond."""
        notes = [self._fake_slider(-1.0) for _ in range(20)]
        self.view.notes = notes
        self.view.note_times = [note.time for note in notes]
        self.view._end_times = {}
        self.view._phantom_ends = {
            note.uid: self.view._compute_phantom_end(note) for note in notes
        }
        self.view.current_time = 1500.0
        self.view.grab()

    def test_a_real_drumroll_is_drawn_under_the_barlines(self):
        """Its body is a band seconds wide. Drawn in with the notes it covered
        every barline and every fake slider stacked on top of it, which is the
        whole picture in a gimmick section."""
        from model.hit_object import HitObject

        document = parse_osu(write_fixture(Path(self._temp.name), "full_v14"))
        document.timing_points = [gui.TimingPoint(time=0.0, beat_length=500.0)]
        # 500ms/beat, meter 4 -> a barline every 2000ms; the drumroll covers it.
        document.hit_objects = [
            HitObject(x=256, y=192, time=1000, type=2, hit_sound=0, extras=("L|624:192", "1", "5000"))
        ]
        view = _viewer(document)
        view.current_time = 4000.0
        image = view.grab().toImage()
        row = view.height() // 2
        barline_x = round(view.x_for_time(4000.0))
        self.assertNotEqual(
            image.pixel(barline_x, row),
            image.pixel(barline_x + 6, row),
            "the barline is hidden under the drumroll body",
        )

    def test_notes_are_blitted_from_the_sprite_cache(self):
        """Stroking an antialiased ellipse per note per frame was most of the
        frame; the cache is what makes a dense map scroll."""
        gui._NOTE_SPRITES.clear()
        self.view.grab()
        self.assertTrue(gui._NOTE_SPRITES, "no note sprite was cached")
        cached = len(gui._NOTE_SPRITES)
        for offset in range(5):
            self.view.current_time = 1000.0 + offset * 16.0
            self.view.grab()
        self.assertEqual(len(gui._NOTE_SPRITES), cached, "sprites are being rebuilt per frame")

    def test_a_wheel_notch_moves_one_snap_division(self):
        self.view.snap_divisor = 4
        self.view.current_time = 1000.0
        _wheel(self.view, -120)
        self.assertAlmostEqual(self.view.current_time, 1125.0, places=3)

    def test_shift_wheel_moves_one_whole_beat(self):
        self.view.snap_divisor = 4
        self.view.current_time = 1000.0
        _wheel(self.view, -120, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.view.current_time, 1500.0, places=3)

    def test_ctrl_wheel_does_not_rescale_the_view(self):
        """The viewer shows the chart at the speed the player sees it at, so
        there is nothing for a zoom gesture to mean here."""
        self.view.px_per_beat = gui.GAMEPLAY_PX_PER_BEAT
        for _ in range(5):
            _wheel(self.view, 120, Qt.KeyboardModifier.ControlModifier)
            _wheel(self.view, -120, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.view.px_per_beat, gui.GAMEPLAY_PX_PER_BEAT)

    def test_ctrl_wheel_does_not_seek(self):
        self.view.current_time = 1000.0
        _wheel(self.view, 120, Qt.KeyboardModifier.ControlModifier)
        self.assertAlmostEqual(self.view.current_time, 1000.0, places=6)


class GameplayViewIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        directory = Path(self._temp.name)
        (directory / "audio.mp3").write_bytes(b"\x00")
        self.path = write_fixture(directory, "full_v14")

        self.window = gui.MainWindow()
        self.window.show()
        self.window._load_map_path(self.path, refresh_difficulties=True)
        self.state = self.window.state
        self.window._add_editor_view("gameplay", self.path)

    def tearDown(self) -> None:
        self.window.close()
        self._temp.cleanup()

    def _frame(self):
        return next(f for f in self.window._editor_views if f.view_type == "gameplay")

    def test_the_gameplay_view_type_is_a_real_widget_not_a_placeholder(self):
        view = self._frame().gameplay_view
        self.assertIsInstance(view, gui.GameplayViewerView)
        self.assertGreater(len(view.notes), 0)
        self.assertIn(view, self.window._gameplay_views)

    def test_it_follows_the_shared_playhead(self):
        view = self._frame().gameplay_view
        self.window.seek_audio(3000)
        self.assertAlmostEqual(view.current_time, 3000.0, places=6)

    def test_placing_a_note_refreshes_it(self):
        view = self._frame().gameplay_view
        before = len(view.notes)
        self.window._place_note(self.path, "don", 1200.0, False, False)
        self.assertEqual(len(view.notes), before + 1)

    def test_an_sv_edit_refreshes_it(self):
        """A green line moves every note after it in this view, so the
        timing-point refresh path has to reach it too."""
        view = self._frame().gameplay_view
        inherited = next(p for p in self.state.document.timing_points if not p.uninherited)
        before = view.velocity_at(inherited.time + 1.0)
        self.window._edit_sv_point(self.path, inherited.uid, inherited.sv_multiplier * 2)
        self.assertAlmostEqual(view.velocity_at(inherited.time + 1.0), before * 2, places=9)

    def test_it_joins_the_editor_pages_global_snap(self):
        view = self._frame().gameplay_view
        self.window._editor_change_snap_from_wheel(1)
        self.assertEqual(view.snap_divisor, int(self.window.editor_snap_combo.currentData()))

    def test_closing_it_unregisters_it_everywhere(self):
        frame = self._frame()
        view = frame.gameplay_view
        self.window._close_editor_view(frame)
        self.assertNotIn(view, self.window._gameplay_views)
        self.assertNotIn(view, self.window._editor_snap_views)


if __name__ == "__main__":
    unittest.main()
