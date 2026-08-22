"""User settings management.

Settings stored as JSON in the user's home directory.
"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_DIR = Path.home() / ".pickhero"
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Plausible bound for latency compensation. Kept here rather than in the UI so
# a stored runaway can be repaired on load, before any screen exists.
MAX_LATENCY_OFFSET_MS = 300.0


@dataclass
class StringCalibration:
    """Calibration data for a single guitar string."""
    midi_note: int        # detected MIDI note (e.g. 40 for E2)
    frequency: float      # median detected frequency (Hz)
    noise_floor_db: float  # noise floor measured before playing


@dataclass
class AudioConfig:
    """Audio capture and detection settings."""
    device_index: int | None = None  # None = system default
    sample_rate: int = 44100
    buf_size: int = 4096
    hop_size: int = 512
    confidence_threshold: float = 0.8
    onset_threshold: float = 0.3
    noise_gate_db: float = -60.0  # ignore signals below this dB level
    yin_tolerance: float = 0.15  # YIN dip threshold, NOT the confidence filter


@dataclass
class DisplayConfig:
    """Display and rendering settings."""
    width: int = 1280
    height: int = 720
    visible_beats: int = 16
    hit_zone_fraction: float = 0.20


@dataclass
class Config:
    """Application settings."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    songs_dir: str = "songs"
    tempo_factor: float = 1.0
    timing_window_ms: float = 150.0
    audio_latency_offset_ms: float = 0.0
    chord_threshold_ms: float = 50.0
    backing_track_enabled: bool = True
    count_in_beats: int = 4
    theme: str = "dark"
    max_fret: int = 24
    active_strings: list[bool] = field(default_factory=lambda: [True] * 6)
    chord_partial_credit: bool = True
    # Check each string of a chord against the raw audio and mark the ones
    # proven to be on the wrong fret. Costs ~380 ms before a chord's verdict
    # settles, so it can be turned off for pure latency.
    chord_verify: bool = True
    # Judge how far a bend actually went, from the pitch contour the detector
    # already produces. It can only ever turn a bent note from green to
    # yellow -- a bend played short is a note played imperfectly, not a note
    # missed -- but its thresholds are not yet fitted to real playing, so it
    # is a switch rather than a fact of life.
    bend_check: bool = True
    # Credit a written palm mute for a strike that carried no pitch at all.
    # A chug is choked on purpose and the riffs it lives in run too fast for
    # the audio window that would otherwise confirm it. Leniency, and the one
    # rule in the app not yet fitted to a recording.
    palm_mute_credit: bool = True
    # Manual scroll speed trim, on top of the speed derived from the song.
    # Above 1.0 scrolls faster, below 1.0 slower.
    scroll_speed_factor: float = 1.0
    # Shift the MIDI backing against the scrolling notes. Positive sounds
    # LATER. The two are generated from the same timeline, but what you hear
    # goes through a synth and a sound card while what you see does not.
    backing_offset_ms: float = 0.0
    # Per-song overrides for the above, keyed by song. How far the backing
    # lags depends on how busy the arrangement is, so one global value never
    # fits everything.
    song_backing_offsets: dict = field(default_factory=dict)
    # Recorded audio as a backing track, alongside the MIDI one rather than
    # instead of it: hearing both at once is how the recording gets lined up
    # against the click in the first place, and either can then be switched
    # off on its own.
    mp3_backing_enabled: bool = True
    # {song key: path to the recording}. Per song by nature -- there is no
    # sensible global default for "the audio of this song".
    song_mp3_paths: dict = field(default_factory=dict)
    # {song key: offset ms}. Positive sounds LATER. Separate from the MIDI
    # offset because an MP3 decoder emits encoder padding before the music
    # and how much depends on the encoder, so this cannot be derived.
    song_mp3_offsets: dict = field(default_factory=dict)
    wait_mode: bool = False
    sort_mode: str = "name_asc"
    calibration: dict = field(default_factory=dict)

    # Store default for HUD comparison (not serialized)
    _default_chord_partial_credit: bool = field(default=True, repr=False)

    def backing_offset_for(self, song_key: str) -> float:
        """This song's backing offset, or the global one if it has none."""
        if song_key and song_key in self.song_backing_offsets:
            return float(self.song_backing_offsets[song_key])
        return float(self.backing_offset_ms)

    def set_backing_offset_for(self, song_key: str, offset_ms: float) -> None:
        """Remember an offset for one song; without a key set the global one."""
        if song_key:
            self.song_backing_offsets[song_key] = float(offset_ms)
        else:
            self.backing_offset_ms = float(offset_ms)

    def mp3_path_for(self, song_key: str) -> str:
        """The recording chosen for this song, or "" if there is none."""
        if not song_key:
            return ""
        return str(self.song_mp3_paths.get(song_key, "") or "")

    def set_mp3_path_for(self, song_key: str, path: str) -> None:
        """Remember (or, with an empty path, forget) this song's recording."""
        if not song_key:
            return
        if path:
            self.song_mp3_paths[song_key] = str(path)
        else:
            self.song_mp3_paths.pop(song_key, None)

    def mp3_offset_for(self, song_key: str) -> float:
        """This song's recording offset. No global fallback on purpose --
        encoder padding belongs to the file, not to the setup."""
        if not song_key:
            return 0.0
        return float(self.song_mp3_offsets.get(song_key, 0.0))

    def set_mp3_offset_for(self, song_key: str, offset_ms: float) -> None:
        if song_key:
            self.song_mp3_offsets[song_key] = float(offset_ms)

    def get_string_calibration(self, string: int) -> StringCalibration | None:
        """Return calibration for a string (1-6), or None if not calibrated."""
        strings = self.calibration.get("strings", {})
        data = strings.get(str(string))
        if data is None:
            return None
        return StringCalibration(**data)

    def set_string_calibration(self, string: int, cal: StringCalibration) -> None:
        """Store calibration for a string (1-6)."""
        if "strings" not in self.calibration:
            self.calibration["strings"] = {}
        self.calibration["strings"][str(string)] = asdict(cal)

    def is_calibrated(self) -> bool:
        """True if at least one string has been calibrated."""
        strings = self.calibration.get("strings", {})
        return len(strings) > 0

    def save(self):
        """Save settings to JSON file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("_default_chord_partial_credit", None)
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls) -> "Config":
        """Load settings from JSON file. Returns defaults if file doesn't exist."""
        if not CONFIG_FILE.exists():
            return cls()
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            data.pop("_default_chord_partial_credit", None)
            audio_data = data.pop("audio", {})
            display_data = data.pop("display", {})
            # Migration: 2048 was the old default and there is no UI to set
            # buf_size, so any stored 2048 came from the old default
            if audio_data.get("buf_size") == 2048:
                audio_data["buf_size"] = 4096
            # Migration: same for the old 100 ms timing window default
            if data.get("timing_window_ms") == 100.0:
                data["timing_window_ms"] = 150.0
            # Repair: auto-sync used to be unbounded and could measure against
            # the wrong note in a repeating riff, walking the offset out to
            # values no real latency can explain. Anything past a plausible
            # range is a runaway, not a measurement — start over from zero.
            offset = data.get("audio_latency_offset_ms")
            if offset is not None and abs(offset) > MAX_LATENCY_OFFSET_MS:
                data["audio_latency_offset_ms"] = 0.0
            # The fret filter never survives a restart. It removes notes from
            # the song silently -- not drawn, not scored, not even counted as
            # missed -- so a limit left on from a previous session shows a
            # plausible-looking accuracy for a fraction of the music. It cost
            # a whole diagnostic run that way: a limit of 7 quietly deleted a
            # sixth of the test file and nothing on screen said the score was
            # measuring a filter. It stays a per-session choice (F), which is
            # the granularity a practice aid actually needs.
            data.pop("max_fret", None)
            return cls(
                audio=AudioConfig(**audio_data),
                display=DisplayConfig(**display_data),
                **data,
            )
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()
