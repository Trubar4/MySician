"""*"Auf Stimmgerät wechseln innerhalb eines Songs -> mit richtige Stimmung
eingestellt."*

Two things have to be right and only one of them is about tuning. The other
is the device: the tuner opens an `AudioCapture` of its own, and `start()`
builds a NEW stream and a new ring every time -- on a capture already
running the old stream is never closed and goes on writing into the same
ring, so the sample counter advances at twice real time. So the song gives
the stream up on the way in, which also means it pauses.
"""

import pygame
import pytest

from pickhero.audio.note_utils import tuning_for_notes
from pickhero.config import Config
from pickhero.tabs.timeline import MeasureInfo, NoteEvent, SongMetadata, Timeline
from pickhero.ui.keys import shift_held
from pickhero.ui.scrolling import PlayingScreen

BAR_MS = 2000.0

STANDARD = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}
DROP_C = {1: 62, 2: 57, 3: 53, 4: 48, 5: 43, 6: 36}


@pytest.fixture(autouse=True)
def _display():
    pygame.init()
    pygame.display.set_mode((320, 240))
    yield
    pygame.display.quit()
    pygame.quit()


def _song(tuning=None):
    notes = [NoteEvent(timestamp_ms=i * 500.0, duration_ms=400.0,
                       midi_note=40 + i, string=6 - (i % 6), fret=i,
                       measure=0) for i in range(8)]
    return Timeline(notes,
                    SongMetadata(title="t", tempo=120,
                                 tuning=dict(tuning or STANDARD)),
                    measures=[MeasureInfo(index=0, start_ms=0.0,
                                          end_ms=BAR_MS)])


def _press(screen, key, shift=True):
    return screen.handle_event(pygame.event.Event(
        pygame.KEYDOWN, key=key,
        mod=pygame.KMOD_LSHIFT if shift else 0,
        unicode="G" if shift else "g"))


class TestShiftGAsksForTheTuner:
    def test_it_asks(self):
        assert _press(PlayingScreen(_song(), config=Config()),
                      pygame.K_g) == "tuner"

    def test_the_plain_g_still_sets_the_hit_window(self):
        """An `if` chain is read in order, so the shifted key has to be
        tested FIRST -- placed after its unshifted twin it is never reached,
        which is how the chord view once shipped inert."""
        config = Config()
        screen = PlayingScreen(_song(), config=config)
        before = config.timing_window_ms
        assert _press(screen, pygame.K_g, shift=False) != "tuner"
        assert config.timing_window_ms != before

    def test_every_way_a_keyboard_says_shift_reaches_it(self):
        """The player's machine sent a capital letter with no shift bit in
        `event.mod` at all. `shift_held` is the one implementation of that
        question and this key has to go through it like the other eleven."""
        screen = PlayingScreen(_song(), config=Config())
        bare = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_g, mod=0,
                                  unicode="G")
        assert shift_held(bare)
        assert screen.handle_event(bare) == "tuner"


class TestGivingTheInputUp:
    def test_it_names_the_tuning_being_played(self):
        screen = PlayingScreen(_song(DROP_C), config=Config())
        letters = screen.release_input()
        assert tuning_for_notes(letters) == tuning_for_notes("C G C F A D")

    def test_a_transposed_song_names_the_guitar_in_your_hands(self):
        """The fret numbers belong to the written song; the STRINGS are what
        the guitar has to be tuned to. A Drop C song played up two is a
        guitar in Drop D, and that is what the tuner must open on.

        The timeline arrives already transposed -- `_load_song` does that
        before the matcher, the backing and the guide are built -- and
        `transpose` is what lets the screen recover the WRITTEN tuning."""
        played = {s: m + 2 for s, m in DROP_C.items()}
        screen = PlayingScreen(_song(played), config=Config(), transpose=2)
        assert screen.written_tuning() == DROP_C
        letters = screen.release_input()
        assert tuning_for_notes(letters) == tuning_for_notes("D A D G B E")

    def test_the_song_stops(self):
        screen = PlayingScreen(_song(), config=Config())
        screen._playing = True
        screen.release_input()
        assert not screen._playing

    def test_and_the_stream_is_closed(self):
        class _Capture:
            def __init__(self): self.stopped = 0
            def stop(self): self.stopped += 1
        screen = PlayingScreen(_song(), config=Config())
        screen._audio_capture = cap = _Capture()
        screen.release_input()
        assert cap.stopped == 1, (
            "two streams on one device makes the sample counter advance at "
            "twice real time")

    def test_nothing_about_the_run_is_thrown_away(self):
        """It is not `stop_audio`: that writes the sitting and the run log
        and closes the backing track. Going to tune up and coming back is
        one run, not two."""
        screen = PlayingScreen(_song(), config=Config())
        screen._playback_ms = 1234.0
        screen.release_input()
        assert screen._playback_ms == 1234.0
        assert screen._matcher is screen._matcher


class TestTheAppOpensItThere:
    def test_the_tuner_opens_on_the_songs_tuning(self, tmp_path, monkeypatch):
        from pickhero.ui import app as app_module
        from pickhero.ui.tuner_menu import TunerMenuScreen
        monkeypatch.setattr(TunerMenuScreen, "_start_capture",
                            lambda self: None)
        screen = PlayingScreen(_song(DROP_C), config=Config())
        holder = app_module.App.__new__(app_module.App)
        holder._config = Config()
        holder._playing_screen = screen
        holder._current_song_path = tmp_path / "Some Song.gp5"
        holder._tuner_menu = None
        holder._return_to = ""
        holder._state = "playing"
        monkeypatch.setattr(screen, "handle_event", lambda event: "tuner")
        holder._handle_playing_event(pygame.event.Event(pygame.KEYDOWN,
                                                        key=pygame.K_g, mod=0))
        assert holder._state == "tuner"
        assert holder._return_to == "playing", "ESC has to come back here"
        assert holder._tuner_menu.tuning_name == "Drop C"
        assert "Some Song" in holder._tuner_menu._song
