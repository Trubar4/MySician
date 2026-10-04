"""What the input level did over a run, said where the question gets asked.

*"Muesste sich das Gate nicht automatisch anpassen? Es stand nicht da, dass
ich die Gitarre lauter machen soll. Bedienerfehler?"*

No. Three faults, one per class below:

- the automatic was switched off for ever by a keypress, with "(X/C)" in the
  corner of the HUD as the only tell;
- the completion screen said nothing at all about the input, because
  `_level_advice` goes silent the moment the song stops -- while the run log
  beside it carried the four numbers that answer the question;
- and the meter on that screen was labelled "Signal" while showing the ROOM,
  which is how -61 dB got read as his playing level by the player AND by me.
"""

import pygame
import pytest

from pickhero import level
from pickhero.config import Config
from pickhero.ui.scrolling import PlayingScreen
from tests.test_scrolling import _make_timeline


class TestTheVerdictOnARun:
    """The arithmetic, without a screen."""

    def test_nothing_heard_is_not_a_verdict(self):
        assert level.measure([], None, -50.0, True) is None

    def test_a_healthy_run_says_so(self):
        """Ruling the input out is worth a line. A screen that only speaks up
        when something is wrong cannot be told apart from one that is broken.
        """
        report = level.measure([-16.0, -28.0, -74.0], -74.0, -65.0, True)
        assert report.verdict == "fine"
        assert not report.is_fault
        assert "fine" in report.headline()

    def test_the_players_own_run(self):
        """level_loudest_db -15.9, playing -28.4, room -73.9, gate -50 by
        hand: a healthy signal behind a gate 15 dB above where the room puts
        it. The log read `level_under_gate_percent 24`."""
        report = level.measure([-15.9] + [-28.4] * 3 + [-70.0] * 4,
                               -73.9, -50.0, False)
        assert report.verdict == "gate"
        assert report.suggested == -65.0
        assert "-65 dB" in report.headline()
        assert "re-arms the automatic" in report.headline()

    def test_a_gate_one_press_away_is_not_worth_a_sentence(self):
        """GATE_STEP_DB: the keys move in 5 dB, so a gate within one press of
        the measurement is where the measurement put it."""
        samples = [-16.0, -28.0, -74.0]
        suggested = level.suggested_gate_db(-16.0, -74.0)
        assert level.measure(samples, -74.0, suggested, True).verdict == "fine"
        assert level.measure(samples, -74.0,
                             suggested + level.GATE_STEP_DB,
                             True).verdict == "fine"
        assert level.measure(samples, -74.0,
                             suggested + 2 * level.GATE_STEP_DB,
                             True).verdict == "gate"

    def test_too_quiet_outranks_the_gate(self):
        """Below QUIET_PEAK_DB the strikes keep arriving and carry the wrong
        pitch, and no gate value can fix a gain."""
        report = level.measure([-50.0, -60.0, -90.0], -90.0, -20.0, False)
        assert report.verdict == "quiet"
        assert "turn the interface up" in report.headline()

    def test_too_loud_is_its_own_answer(self):
        report = level.measure([-4.0, -10.0, -60.0], -60.0, -50.0, True)
        assert report.verdict == "loud"
        assert "down" in report.headline()

    def test_the_wrong_device_outranks_everything(self):
        """A room a tenth of a decibel under the playing makes every other
        number in a run unreadable, so it is asked first."""
        report = level.measure([-9.2, -37.2, -37.3], -37.3, -50.0, True)
        assert report.verdict == "room"
        assert "wrong device" in report.headline()

    def test_a_loud_room_with_an_instrument_in_front_of_it_is_fine(self):
        """The quantity is the DISTANCE. An earlier version of this rule used
        the gate ceiling as a proxy and convicted a healthy Focusrite: room
        -50.4 against a playing median of -29.6, 21 dB apart."""
        report = level.measure([-20.0, -29.6, -50.4], -50.4, -45.0, True)
        assert not report.hears_the_room

    def test_without_a_room_no_gate_is_suggested(self):
        """A low percentile of the PLAYING is not the room -- it ran from -35
        to -94 dB across one session's takes against a recorded -73."""
        report = level.measure([-16.0, -28.0], None, -50.0, True)
        assert report.suggested is None
        assert report.band is None
        assert report.verdict == "fine"
        assert "room not measured" in report.numbers()

    def test_the_numbers_are_always_there(self):
        """A verdict without the measurement behind it is not checkable."""
        report = level.measure([-15.9, -28.4, -74.0], -73.9, -50.0, False)
        numbers = report.numbers()
        for piece in ("-16 dB", "-74 dB", "-50 dB", "by hand"):
            assert piece in numbers
        assert "while playing" in numbers


class TestTheCompletionScreenSaysIt:
    """The screen the question is asked on."""

    @pytest.fixture(autouse=True)
    def _screen_up(self, isolated_config):
        pygame.init()
        pygame.display.set_mode((1280, 720))
        yield
        pygame.display.quit()
        pygame.quit()

    def _screen(self):
        return PlayingScreen(_make_timeline(), config=Config())

    def test_the_run_report_survives_the_song_ending(self):
        """`_level_advice` returns "" once the song stops, deliberately: the
        tracked peak decays after the last note and it once reported a fault
        that was not there. The RUN's measurement does not decay."""
        screen = self._screen()
        screen._playing = False
        screen._level_samples = [-15.9] + [-28.4] * 3 + [-70.0] * 4
        screen._room_samples = [-73.9] * level.ROOM_SAMPLES
        screen._noise_gate_db = -50.0
        screen._auto_gate = False
        assert screen._level_advice() == ""
        report = screen._level_report()
        assert report is not None and report.verdict == "gate"

    def test_the_completion_screen_draws_it(self):
        """A fault on screen has to be VISIBLE on it: the screen is rendered
        with and without the level samples and the pixels compared."""
        screen = self._screen()
        screen._audio_enabled = True
        screen._song_completed = True
        screen._playing = False
        surface = pygame.Surface((1280, 720))

        screen._level_samples = []
        screen.render(surface)
        quiet = pygame.image.tostring(surface, "RGB")

        screen._level_samples = [-15.9] + [-28.4] * 3 + [-70.0] * 4
        screen._room_samples = [-73.9] * level.ROOM_SAMPLES
        screen._noise_gate_db = -50.0
        screen._auto_gate = False
        surface.fill((0, 0, 0))
        screen.render(surface)
        assert pygame.image.tostring(surface, "RGB") != quiet


class TestTheMeterSaysWhatItMeasures:
    """-61 dB on the completion screen is the ROOM, and it said "Signal"."""

    @pytest.fixture(autouse=True)
    def _screen_up(self, isolated_config):
        pygame.init()
        pygame.display.set_mode((1280, 720))
        yield
        pygame.display.quit()
        pygame.quit()

    def _labels(self, playing: bool, playback_ms: float) -> list[str]:
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._audio_capture = object()
        screen._playing = playing
        screen._playback_ms = playback_ms
        screen._signal_db_smooth = -61.0
        seen: list[str] = []

        class _Font:
            def render(self, text, *a, **k):
                seen.append(text)
                return pygame.Surface((10, 10))

            def get_height(self):
                return 10

        screen._draw_signal_meter(pygame.Surface((1280, 720)), _Font(),
                                  1280, 100)
        return seen

    def test_while_the_song_runs_it_is_the_signal(self):
        assert any("Signal: -61 dB" == t for t in self._labels(True, 1000.0))

    def test_once_the_song_stops_it_is_the_room(self):
        """It keeps moving after the song ends, so what it shows there is the
        room with nobody playing -- which the player read as his playing
        level, and so did I."""
        assert any("Room: -61 dB" == t for t in self._labels(False, 1000.0))

    def test_during_the_count_in_it_is_the_room(self):
        """Which is exactly what the automatic gate reads it as."""
        assert any("Room: -61 dB" == t for t in self._labels(True, -500.0))


class TestTheAutomaticComesBackNextSong:
    """One keypress used to switch it off for every song, for ever."""

    def test_a_stored_off_from_before_the_fix_is_repaired_once(self, tmp_path,
                                                               monkeypatch):
        """The same argument as the stored gate above the ceiling: a value
        that was only reachable through a bug cannot say that anybody chose
        it. Once, so a deliberate off from the settings screen stays off."""
        import json
        from pickhero import config as config_module

        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"audio": {"auto_gate": False}}))
        monkeypatch.setattr(config_module, "CONFIG_FILE", path)

        repaired = config_module.Config.load()
        assert repaired.audio.auto_gate
        assert repaired.audio.auto_gate_repaired

    def test_a_deliberate_off_stays_off(self, tmp_path, monkeypatch):
        import json
        from pickhero import config as config_module

        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"audio": {"auto_gate": False,
                                              "auto_gate_repaired": True}}))
        monkeypatch.setattr(config_module, "CONFIG_FILE", path)
        assert not config_module.Config.load().audio.auto_gate

    def test_the_settings_row_marks_its_own_choice(self, isolated_config):
        """Otherwise a first-ever run that switches it off there is undone by
        the repair on the next load."""
        from pickhero.ui.settings_menu import SettingsMenuScreen

        config = Config()
        screen = SettingsMenuScreen(config)
        row = next(s for s in screen._rows if s.key == "auto_gate")
        row.adjust(1)
        assert not config.audio.auto_gate
        assert config.audio.auto_gate_repaired


class TestTheClockRatioDoesNotMeasureItsOwnPauses:
    """`audio_clock_ratio 0.9325` on a run whose own control says 1.0.

    The device stays open while the song is paused -- that is the design, and
    `_reanchor_audio_clock` puts the offset back. The RATIO's accumulator did
    not know: the baseline was left standing over the early return, so the
    first frame back charged the whole pause to the audio side and one frame
    to the song side. Sixth instance in this project of a tool measuring
    itself, and the tell was the same as every other time -- a control that
    disagrees (`audio_clock_pulled_ms 73`, worst error 39 ms, 0 % of strikes
    over budget).
    """

    def _screen(self):
        screen = PlayingScreen(_make_timeline(), config=Config())
        screen._matcher = type("M", (), {"audio_offset_ms": 0.0})()
        screen._playing = True
        screen._playback_ms = 1000.0
        return screen

    class _Capture:
        def __init__(self):
            self.heard = 0.0

        def elapsed_ms(self):
            return self.heard

    def test_a_pause_does_not_count_as_a_drifting_device(self):
        screen = self._screen()
        capture = self._Capture()
        screen._audio_capture = capture

        for _ in range(60):                       # a second of honest playing
            capture.heard += 16.7
            screen._playback_ms += 16.7
            screen._track_audio_clock(0.0167)

        screen._playing = False                   # paused: ten seconds of it
        screen._track_audio_clock(0.0167)
        capture.heard += 10_000.0

        screen._playing = True
        for _ in range(60):
            capture.heard += 16.7
            screen._playback_ms += 16.7
            screen._track_audio_clock(0.0167)

        ratio = (screen._audio_clock_song_ms
                 / screen._audio_clock_heard_ms)
        assert ratio == pytest.approx(1.0, abs=0.02)

    def test_a_device_that_really_drifts_is_still_reported(self):
        """The control: the fix must not make the measurement blind."""
        screen = self._screen()
        capture = self._Capture()
        screen._audio_capture = capture
        for _ in range(120):
            capture.heard += 16.7 * 0.95          # a counter 5 % slow
            screen._playback_ms += 16.7
            screen._track_audio_clock(0.0167)
        ratio = (screen._audio_clock_song_ms
                 / screen._audio_clock_heard_ms)
        assert ratio == pytest.approx(1.0 / 0.95, abs=0.01)
