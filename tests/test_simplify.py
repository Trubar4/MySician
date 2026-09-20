"""The easier reading, and the four things that make it checkable without ears.

*"Kann man Tabs vereinfachen, ohne dass sie wirklich schlechter klingen beim
mitspielen?"* — nobody here can listen, so nothing in this file claims a
sound. What it asserts is what a subset of the written notes can be held to:
every moment keeps its pitch classes, keeps its bass, keeps its chord name,
and the song keeps every one of its picks. A reduction that holds all four is
the same harmony and the same right hand, played with fewer fingers.
"""

from collections import defaultdict

import pytest

from pickhero.config import Config
from pickhero.tabs.chords import name_chord
from pickhero.tabs.simplify import doublings, how_much, simplified
from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline


def _n(ms, midi, string, fret=5, **kw):
    return NoteEvent(timestamp_ms=ms, duration_ms=200.0, midi_note=midi,
                     string=string, fret=fret, **kw)


def _song(notes):
    return Timeline(notes, SongMetadata(track_name="T"),
                    [MeasureInfo(i, i * 1000.0, (i + 1) * 1000.0)
                     for i in range(8)])


def _moments(timeline):
    out = defaultdict(list)
    for note in timeline.notes:
        out[round(note.timestamp_ms, 3)].append(note)
    return out


class TestWhatItLeavesOut:

    def test_an_octave_of_a_lower_string_goes(self):
        """E2 and E4 in one chord is one pitch class twice, and the higher
        one is masked by the lower one's own second harmonic."""
        song = _song([_n(0, 40, 6), _n(0, 47, 5), _n(0, 64, 1)])
        assert [x.midi_note for x in doublings(song)] == [64]

    def test_the_fifth_stays(self):
        """Measured together with the octaves they came to 60 % and looked
        like a free lunch. A power chord without its fifth is a single
        note -- `chord_verify` cannot confirm either, but "the app cannot
        hear it" and "the ear cannot hear it" are different claims."""
        song = _song([_n(0, 40, 6), _n(0, 47, 5)])
        assert doublings(song) == []

    def test_it_keeps_one_note_per_pitch_class_and_it_is_the_lowest(self):
        """My own first expectation here was wrong and the test caught it:
        three E's do not become two, they become ONE. That is the rule
        stated properly -- the lowest instance of each pitch class survives
        and every other instance of it goes."""
        song = _song([_n(0, 40, 6), _n(0, 52, 4), _n(0, 52, 3)])
        left = simplified(song)
        assert [x.midi_note for x in left.notes] == [40]

    def test_two_octaves_up_counts(self):
        song = _song([_n(0, 40, 6), _n(0, 64, 2)])
        assert [x.midi_note for x in doublings(song)] == [64]

    def test_notes_at_different_moments_are_not_doublings(self):
        """A melody that walks in octaves is a melody, not a chord."""
        song = _song([_n(0, 40, 6), _n(500, 52, 4), _n(1000, 64, 2)])
        assert doublings(song) == []


class TestWhatItRefusesToTouch:

    @pytest.mark.parametrize("technique", [
        {"bend": ((0.0, 0), (1.0, 2))},
        {"slide_to_next": True},
        {"slide_in": True},
        {"slide_out": True},
        {"hammer_to_next": True},
    ])
    def test_a_note_carrying_a_technique_stays(self, technique):
        """A bend or a slide is the music rather than the harmony."""
        song = _song([_n(0, 40, 6), _n(0, 52, 4, **technique)])
        assert doublings(song) == []

    def test_a_dead_note_stays(self):
        """It sounds no pitch, so it can be neither a doubling nor the
        reason for one -- and removing one from a muted strum changes the
        stroke."""
        song = _song([_n(0, 40, 6), _n(0, 52, 4, dead=True)])
        assert doublings(song) == []

    def test_a_dead_note_cannot_justify_removing_anything(self):
        song = _song([_n(0, 40, 6, dead=True), _n(0, 52, 4)])
        assert doublings(song) == []

    def test_a_note_hammered_into_stays(self):
        """The tab carries no link from a legato source to its target -- the
        matcher walks to the next note on the string -- so removing a target
        hands the credit to whatever comes after it."""
        song = _song([_n(0, 45, 5, hammer_to_next=True),
                      _n(0, 33, 6),
                      _n(400, 45, 5), _n(400, 33, 6)])
        assert [x.midi_note for x in doublings(song)] == []

    def test_the_same_note_is_taken_when_nothing_slides_into_it(self):
        """The control for the test above: without the hammer it goes."""
        song = _song([_n(0, 45, 5), _n(0, 33, 6),
                      _n(400, 45, 5), _n(400, 33, 6)])
        assert len(doublings(song)) == 2


class TestTheFourPropertiesAHumanCannotCheck:
    """Asserted on a chord that really is doubled, because a rule that
    removes nothing passes all four for free."""

    def _pair(self):
        """A real open E major: E2 B2 E3 G#3 B3 E4 across all six strings.

        Three E's and two B's, so half of it is doubling -- and the half
        that survives is still an E major with an E in the bass."""
        notes = []
        for beat in range(4):
            ms = beat * 500.0
            notes += [_n(ms, 40, 6), _n(ms, 47, 5), _n(ms, 52, 4),
                      _n(ms, 56, 3), _n(ms, 59, 2), _n(ms, 64, 1)]
        song = _song(notes)
        return song, simplified(song)

    def test_a_six_string_open_chord_comes_down_to_four(self):
        """Not to three: strings 6-5-4-3 is one unbroken sweep and 6-5-3 is
        a stroke that has to miss the fourth string. The last E is kept to
        keep the stroke, which is what `_keep_the_stroke` is for."""
        song, easy = self._pair()
        first = [n for n in easy.notes if n.timestamp_ms == 0.0]
        assert sorted(n.string for n in first) == [3, 4, 5, 6]
        assert len(easy.notes) == len(song.notes) - 8

    def test_every_pick_survives(self):
        """The picking hand does exactly what it did; the fretting hand
        holds a smaller shape. This is the whole claim."""
        song, easy = self._pair()
        assert set(_moments(song)) == set(_moments(easy))

    def test_every_moment_keeps_its_pitch_classes(self):
        song, easy = self._pair()
        for ms, group in _moments(song).items():
            was = {n.midi_note % 12 for n in group}
            now = {n.midi_note % 12 for n in _moments(easy)[ms]}
            assert was == now

    def test_every_moment_keeps_its_bass(self):
        song, easy = self._pair()
        for ms, group in _moments(song).items():
            assert (min(n.midi_note for n in group)
                    == min(n.midi_note for n in _moments(easy)[ms]))

    def test_every_chord_keeps_its_name(self):
        song, easy = self._pair()
        for ms, group in _moments(song).items():
            was = name_chord([n.midi_note for n in group])
            now = name_chord([n.midi_note for n in _moments(easy)[ms]])
            assert was == now and was is not None

    def test_nothing_is_added_or_moved(self):
        song, easy = self._pair()
        kept = {id(n) for n in easy.notes}
        assert kept <= {id(n) for n in song.notes}

    def test_the_grid_comes_through(self):
        song, easy = self._pair()
        assert len(easy.measures) == len(song.measures)
        assert easy.metadata is song.metadata


class TestOnTheRealSongs:
    """The four properties again, on the player's own files rather than on a
    chord built to pass them."""

    def _songs(self):
        from pathlib import Path
        from pickhero.tabs.loader import list_tracks, load_gp_file
        folder = Path(__file__).resolve().parent.parent / "songs"
        found = sorted(folder.glob("*.gp*"))
        if not found:
            pytest.skip("no songs to read")
        for path in found:
            guitars = [t for t in list_tracks(path)
                       if t.get("is_guitar") and not t.get("is_percussion")]
            yield path, load_gp_file(path, guitars[0]["index"]
                                     if guitars else None)

    def test_the_four_properties_hold_on_every_one(self):
        for path, song in self._songs():
            easy = simplified(song)
            was, now = _moments(song), _moments(easy)
            assert set(was) == set(now), f"{path.stem}: lost a pick"
            for ms, group in was.items():
                assert ({n.midi_note % 12 for n in group}
                        == {n.midi_note % 12 for n in now[ms]}), path.stem
                assert (min(n.midi_note for n in group)
                        == min(n.midi_note for n in now[ms])), path.stem
                assert (name_chord([n.midi_note for n in group])
                        == name_chord([n.midi_note for n in now[ms]])), path.stem

    def test_it_really_does_something_on_a_chord_song(self):
        """Measured: Godsmack drops 867 of 2561 notes and not one strum. A
        rule that holds every property by removing nothing is not a rule."""
        best = max(how_much(song)[0] for _, song in self._songs())
        assert best > 100


class TestItIsRememberedPerSong:

    def test_the_full_song_is_the_default_and_is_never_written(self):
        """An entry saying "as written" says nothing, and would travel to
        the other laptop to say it again."""
        c = Config()
        c.set_simplify_for("Thunder", False)
        assert c.song_simplify == {}
        assert c.simplify_for("Thunder") is False

    def test_it_survives_a_round_trip(self):
        c = Config()
        c.set_simplify_for("Godsmack - Awake", True)
        assert c.simplify_for("Godsmack - Awake") is True

    def test_it_travels_and_is_forgotten_with_the_song(self):
        from pickhero.tabs import sidecar
        c = Config()
        c.set_simplify_for("Godsmack - Awake", True)
        assert "song_simplify" in sidecar.song_fields(c)
        assert sidecar.collect("Godsmack - Awake", c)["settings"]["song_simplify"]
        c.rename_song("Godsmack - Awake", "Godsmack")
        assert c.simplify_for("Godsmack") is True
        c.forget_song("Godsmack")
        assert c.song_simplify == {}


class TestQIsReallyWired:
    """A key that cannot be seen working is indistinguishable from one that
    does not work, and this project has shipped that four times."""

    def _app(self, tmp_path):
        import shutil
        from pathlib import Path
        import pygame
        from pickhero.ui.app import App
        src = Path(__file__).resolve().parent / "fixtures" / "canon.gp5"
        if not src.exists():
            pytest.skip("reference song missing")
        song = tmp_path / "Canon.gp5"
        shutil.copy(src, song)
        pygame.init()
        pygame.display.set_mode((640, 480))
        app = App(Config())
        app._menu = None
        return app, song

    def test_pressing_q_reloads_the_song_with_fewer_notes(self, tmp_path,
                                                          monkeypatch):
        import pygame
        app, song = self._app(tmp_path)
        monkeypatch.setattr(app._config, "save", lambda: None)
        app._load_song(song)
        full = len(app._playing_screen._timeline.notes)
        drops = app._simplify_drops[0]
        if not drops:
            pytest.skip("this fixture has no doubled notes")
        app._handle_playing_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q, mod=0,
                               unicode="q"))
        assert len(app._playing_screen._timeline.notes) == full - drops
        assert app._config.simplify_for(song.stem) is True

    def test_it_comes_back_when_the_song_is_reopened(self, tmp_path,
                                                     monkeypatch):
        app, song = self._app(tmp_path)
        monkeypatch.setattr(app._config, "save", lambda: None)
        app._config.set_simplify_for(song.stem, True)
        app._load_song(song)
        easy = len(app._playing_screen._timeline.notes)
        app._config.set_simplify_for(song.stem, False)
        app._load_song(song)
        assert len(app._playing_screen._timeline.notes) > easy

    def test_the_backing_still_plays_the_whole_song(self, tmp_path,
                                                    monkeypatch):
        """The band does not get simpler because you do. The backing and the
        guide are extracted from the FILE, so they are untouched -- which is
        also the Yousician situation: you play `basic` against the full
        recording."""
        app, song = self._app(tmp_path)
        monkeypatch.setattr(app._config, "save", lambda: None)
        app._load_song(song)
        before = app._playing_screen._guide_player
        app._config.set_simplify_for(song.stem, True)
        app._load_song(song)
        after = app._playing_screen._guide_player
        if before is None or after is None:
            pytest.skip("no MIDI output here")
        assert len(after._events) == len(before._events)

    def test_the_screen_says_so(self, tmp_path, monkeypatch):
        app, song = self._app(tmp_path)
        monkeypatch.setattr(app._config, "save", lambda: None)
        app._config.set_simplify_for(song.stem, True)
        app._load_song(song)
        footer = " ".join(t for t, _ in
                          app._playing_screen.footer_segments())
        assert "Simple on" in footer
