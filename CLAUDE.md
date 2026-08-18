# PickHero — CLAUDE.md

## Project Overview

Desktop guitar practice app. Scrolling Guitar Pro tabs with real-time pitch detection and visual hit/miss feedback. Python, PyGame, aubio, pyguitarpro. Must run on low-end hardware (no ML, no GPU).

## Language & Stack

- **Python 3.10+**, Windows primary target
- **aubio** for pitch detection (YIN algorithm) and onset detection
- **sounddevice** for audio capture from USB audio devices
- **pyguitarpro** for reading GP3/GP4/GP5 tab files
- **pygame** for UI rendering (scrolling display, game loop)
- **pygame.mixer** for backing track / metronome playback
- No ML frameworks. No TensorFlow, no CREPE, no PyTorch. Detection is signal-processing only.

## Architecture

Three threads:
1. **Audio thread** — `sounddevice` callback captures audio, feeds to aubio pitch/onset detectors, pushes detected notes to a thread-safe queue
2. **Main thread** — PyGame event loop, renders scrolling UI, reads detected notes from queue, runs note matcher against timeline
3. **Playback thread** (optional) — pygame.mixer for backing track audio

Modules:
- `pickhero/audio/` — capture, detection, note utilities. No UI dependencies.
- `pickhero/tabs/` — GP file loading, timeline data structure, Songsterr downloader. No UI dependencies.
- `pickhero/ui/` — PyGame rendering, game loop, menus. Depends on audio and tabs.
- `pickhero/config.py` — user settings (audio device, noise gate, visual prefs). JSON file in user home dir.

## Key Conventions

- **Module independence:** audio/ and tabs/ must be testable without PyGame. No pygame imports outside ui/.
- **Thread safety:** audio thread communicates with main thread via `queue.Queue`. No shared mutable state.
- **Note representation:** use MIDI note numbers internally (0-127). Convert to name/octave only for display.
- **Timing:** all timestamps in milliseconds (float). Timeline positions are ms from song start.
- **Frequency → note:** use `round(12 * log2(freq / 440) + 69)` for MIDI note number. Standard A4 = 440 Hz.
- **Tolerance:** pitch match within ±1 semitone = "close" (yellow). Exact semitone = "hit" (green). Timing window configurable, default 150ms.
- **Guitar tuning:** standard E2-A2-D3-G3-B3-E4 (MIDI 40-45-50-55-59-64). Support alternate tunings from GP file header.

## aubio Configuration

```python
# Pitch detection — yinfast = YIN computed via FFT, cheap at large windows
pitch_detector = aubio.pitch("yinfast", buf_size=4096, hop_size=512, samplerate=44100)
pitch_detector.set_unit("Hz")
pitch_detector.set_tolerance(0.15)  # YIN dip threshold — NOT the confidence filter!

# Onset detection — short window for strike-timing precision
onset_detector = aubio.onset("default", buf_size=2048, hop_size=512, samplerate=44100)
onset_detector.set_threshold(0.3)  # adjust based on testing
```

- Pitch window 4096 covers ~7.6 periods of low E (82 Hz); 2048 octave-errors on bass strings
- `set_tolerance` on yin/yinfast is the dip threshold (aubio default 0.15). Setting it high
  (e.g. 0.8) makes YIN accept the first weak dip — harmonics instead of the fundamental.
  The confidence filter (`get_confidence() >= 0.8`) is a separate knob; never pass one as the other.
- 44100 Hz is the configured default, but AudioCapture probes the device and falls back to its
  actual rate (USB interfaces often only accept 48000 in Windows shared mode); detectors are
  rebuilt at the resolved rate
- Noise gate: ignore buffers below configurable dB level

## Chord Verification

Monophonic pitch detection cannot say which string of a chord was mis-fretted, so `audio/chord_verify.py` answers that separately, using the
tab as a prior. For each expected note it scores competing pitch hypotheses on the partials no other expected note produces.

- **Presumption of innocence.** A string is marked wrong only on positive evidence of a wrong pitch, never on absence of evidence for the right
  one. A string whose expected note is an octave or fifth of a lower string in the same chord can never be confirmed (its partials are a strict
  subset of one already sounding); judging it anyway means ranking noise, which is exactly how the first calibration produced false alarms.
  This also reproduces the familiar behaviour that omitting a string from an open chord still passes.
- **Wants 341 ms of audio after the strike**, so chord verdicts trail the pitch path by ~380 ms and can only downgrade what it already credited.
  `AudioCapture` keeps a ring buffer and emits one `StrikeWindow` per strike; the matcher applies verdicts in `process_strike_windows`.
- **The window ends at the next strike.** A window running into the following chord contains pitches the tab never expected there, and convicts
  strings that were played right — that is what made fast chord changes light up red. `_limit_pending_windows` trims to the gap actually
  available; under `MIN_WINDOW_MS` (200 ms, so chords closer than ~255 ms — eighths past about 118 BPM) the strike is dropped and gets no
  verdict at all. Two things keep a trimmed window honest: the analysis floor rises with `MIN_HZ_SECONDS / T`, since a short window cannot
  separate a semitone low down; and the intruder tier — the one that convicts a string whose expected note is masked — is allowed only at the
  full length, having been fitted there.
- **`MIN_WINDOW_MS` was stale for a whole cycle, and the sweep is what hid it.** It was fitted at 280 ms back when the analysis floor was a
  fixed 150 Hz. `MIN_HZ_SECONDS` then made shorter windows honest, but nobody lowered the constant — and `sweep_chord_window.py` could not
  report the winnings because it gated on the very value it was meant to test, printing "below floor" with nothing judged. The sweep now lifts
  the floor for the duration of its run and reaches well below it. Re-measured: no false alarm anywhere from 190 ms up, the first at 180 ms.
  **Any constant a tool is supposed to question must not also gate that tool.**
- **Thresholds are calibrated, not guessed** — see `tools/analyze_reference.py`, `tools/sweep_chord_window.py` and `reference_recordings/`.
  Re-fit them with real takes rather than tuning by feel; `tools/record_reference.py` records a labelled set including deliberately wrong takes,
  which the calibration needs. Current state on that set: 33 strings judged, 0 false alarms, 7/7 deliberate one-fret errors caught at the full
  window, and 0 false alarms at every window length down to 190 ms.

## Chords That Produce No Pitch

A strummed chord regularly gives monophonic YIN no single period to lock onto, so a correctly played strum arrives carrying no pitch at
all. Measured over `reference_recordings/`: strikes with no confident pitch run at **16-17 % on one or two strings** and **38-55 % from four
strings up** — an open A minor produced none in five strikes. Scored on pitch alone those strums are red however well they were fretted,
which is the "chords are not recognised" the player reports. It is not a speed problem: a fast single-note riff detects 47 of 49 and fast
power chords 38 of 39.

- **An unpitched strike credits a written chord of `MIN_UNPITCHED_CHORD_STRINGS` (3) strings or more.** The threshold is where the data
  breaks, not a feel: below three the detector nearly always does produce a pitch, so accepting a pitchless strike there would be leniency
  bought with nothing. Single notes and power chords therefore keep the behaviour they had.
- **This credits the strum, not the fretting.** The strike still goes to `chord_verify.py`, which reads the raw audio and convicts any string
  it can positively show wrong. That is what makes crediting safe, and it is the same presumption of innocence run one level up: the strum
  is assumed played until a partial says otherwise.
- **Verified end to end on the reference takes** by `tools/check_chord_credit.py` — real audio through `AudioCapture`, scored by the matcher,
  verdicts applied by the verifier. Correct chords go from 54 % credited to 100 %, while all three deliberate one-fret errors are still
  caught (3, 9 and 6 string verdicts) and a chord with a string left out still passes. Re-run it after touching either the credit or the
  verifier's thresholds; it exits non-zero when a correct take loses a string or an error slips through.
- For an error take the tab must be the CORRECT shape: the manifest records what was **played**, so telling the verifier to expect the wrong
  note asks it whether the error is the error it was given, and it rightly says no.

## Timing Diagnosis

Two numbers cannot say which timing problem a player has, so `matcher.timing_report()` (shown by **Y**) keeps the samples apart and names
one of five answers: `fine`, `latency`, `scatter`, `mixed`, `per_string`.

- **The histogram is the diagnosis.** One narrow hill away from zero is latency and K removes it; one wide hill over zero is the playing and
  no offset touches it; a split between strings is the detector, and one global offset cannot fix that either. The axis always contains zero,
  because how far the group sits FROM the beat is the thing being shown.
- **Only a note that really sounded its written pitch, on a pick of its own, may be timed** (`_times_its_own_strike`). A dead note has no pitch;
  a bent or sliding one leaves its written pitch deliberately, and the collector reports the pitch it moved TO; a hammered, pulled or slid-into
  one is never picked, so any strike credited to it belongs elsewhere. A hammer-on SOURCE is picked normally and still counts. This cost real
  damage before it was enforced: a run over the technique test put 18 of its 24 samples on technique notes, scattered by ±75 ms, and `K` built
  an offset out of it that then sat in the config for days swallowing a third of the real latency. Under the rule those 24 samples become 6 —
  below the minimum — and `K` stays silent, which is the right answer. Real songs keep 88-92 % of their notes measurable.
- **`K` and the HUD line that advertises it read the same verdict.** They used to apply different thresholds to the same samples, so the line
  could offer a key that then did nothing — the failure that teaches a player the panel lies. One helper answers both, and the test asserts the
  property rather than the wording: the line offers `K` exactly when pressing `K` changes the offset.
- **Nothing is claimed inside its own noise.** A median built from loose strikes lands twenty-odd ms off the beat by chance, and two per-string
  medians of a couple of dozen samples differ by tens of ms the same way. Both are tested against the standard error of a median
  (`MEDIAN_SE_FACTOR`) before being called an effect — the same presumption of innocence the chord verifier runs on.
- **The search radius narrows as the offset becomes known** (`_search_radius_ms`). A wide search over a riff repeating one pitch finds two
  equally good candidates and rightly refuses, which cost 61 % of all strikes; narrowing brings them back (39 % → 98 % on the timing test).
  A song with no pitch variety at all still measures nothing, and that is the honest answer, not a bug.
- **Verified against injected faults** — `tools/simulate_timing.py` plays a song with a known latency, jitter or per-string delay and checks
  the report names it. Change any threshold and re-run it.

## Techniques (bends, slides, legato)

`NoteEvent` carries what the tab wrote: `bend` as ((position 0..1, semitones), ...), plus `slide_to_next`, `slide_in`, `slide_out` and
`hammer_to_next`. Extracted in `tabs/loader.py` from pyguitarpro's already-normalised effects; the hand-written GP7 XML path does not carry
them yet.

- **Drawn inside the note, badge above it** — the way Yousician does it, and the only thing a six-lane layout allows: a curve arcing out of
  its lane reads as a note on the neighbouring string. The white technique line always gets a dark shadow (`_draw_technique_line`), because
  white on the amber string is invisible and an invisible technique will not be played.
- **Scored so the drawing is not a lie.** A bend accepts the whole region it covers (`_build_pitch_ranges`) — judging how FAR it went needs a
  pitch contour the detector does not produce. A hammered, pulled or slid-into note is never picked, so it inherits its source's verdict
  (`_legato_credit`); waiting for a strike on it could only ever end in a miss.
- **A sliding note gives up part of its sustain** so the connector has somewhere to be; back-to-back notes otherwise leave a few pixels.
- `tools/make_technique_test.py` writes a GP5 stating exactly which technique is where, so a wrong drawing is the app's fault.

## Muting (palm mutes, dead notes)

`NoteEvent.palm_mute` and `NoteEvent.dead` carry what the tab wrote; both come out of pyguitarpro AND out of the GP7 XML path, where they are
plain flags rather than a curve to reconstruct. They are separate axes — a chug riff mixes them — and neither implies the other.

- **A dead note has no pitch to check, so the strike IS the evidence.** Its written fret says where the fretting hand damps the string, not
  what will sound; scored against that pitch, every dead note in a tab was a miss however well it was played. `OnsetPitchCollector` now reports
  a strike that produced no pitch as `unpitched` instead of dropping it, and `_dead_note_credit` accepts any strike — pitched or not — for a
  written dead note. A muted strum across three strings is one stroke, not three.
- **A dead note never competes for a pitched strike.** It accepts any pitch, so left in the ordinary candidate list it swallows the strike meant
  for the real note beside it. It is held back and only catches strikes nothing else explains. It is also kept out of the timing report (its
  pitch never sounded, so the offset would be invented) and out of chord verification (the verifier would hunt for partials that were never
  there and convict a neighbour for their absence).
- **A palm mute does not change the pitch**, so scoring is untouched — the picking hand chokes the note, it does not transpose it. What changes
  is the drawing: the note is capped at `PALM_MUTE_MAX_HEADS` rather than ringing for its written length, because promising a ring that will not
  happen is how a chug gets read as a held note.
- **"PM" is badged once per run, not per note**, the way paper tab writes it and dashes it onward — a disc over every note of a muted riff buries
  the music under its own labelling. A dead stroke inside the run does not break it (the hand never leaves the strings); a silence longer than
  `PALM_MUTE_RUN_GAP_MS` does, so the badge comes back when the riff does.
- Still unmeasured: whether a heavy chug that returns NO pitch should credit its palm-muted note. That is leniency, and it needs reference
  recordings before it is granted, not a feel for how it ought to behave.

## Colour

Two palettes share the screen and must never be confusable: `STRING_COLORS` says WHICH STRING, `feedback_*` says HOW IT WENT. The plain
Rocksmith palette collided with all three feedback colours at once — green on "correct", red on "missed", yellow on "close" — so the A string
looked like a note played right. Strings are now pulled off those hues (A is teal, high E crimson, B amber) and the feedback colours are far
brighter than any string, so the two read as different KINDS of colour even where the hue is nearest. Keep that separation when adding
anything new.

## pyguitarpro Data Extraction

GP file → iterate tracks → find guitar track(s) → iterate measures → beats → notes:
```python
# Each note gives: note.value (fret), note.string (1-6), beat.start, beat.duration
# Convert to: (timestamp_ms, midi_note, string, fret, duration_ms)
```

Tempo changes: GP files can have tempo changes per measure. Track cumulative time, don't assume constant BPM.

## PyGame Rendering

- **Window:** 1280×720 default, resizable
- **Layout:** 6 horizontal lanes (one per string), notes scroll right-to-left
- **Note display:** rectangles on string lanes, width proportional to duration, fret number drawn on note
- **Scroll speed:** pixels_per_ms = lane_width / visible_window_ms. Derive from BPM.
- **Hit zone:** vertical line on left side of screen. Notes passing through it are "active" for matching.
- **Target FPS:** 60 (PyGame clock.tick)

## File Organization

Keep it flat and simple. Don't over-engineer packages:
```
pickhero/
├── __init__.py
├── __main__.py          # python -m pickhero entry point
├── main.py
├── config.py
├── matcher.py           # note matching engine (hit/close/miss)
├── progress.py          # per-song progress tracking
├── audio/
│   ├── __init__.py
│   ├── input.py
│   ├── detector.py
│   ├── chord_verify.py  # per-string chord checking (score-informed)
│   ├── midi_playback.py
│   └── note_utils.py
├── tabs/
│   ├── __init__.py
│   ├── loader.py
│   ├── timeline.py
│   └── downloader.py
└── ui/
    ├── __init__.py
    ├── app.py
    ├── calibration_menu.py  # Guitar calibration wizard
    ├── colors.py          # Theme system (dark/light)
    ├── scrolling.py
    ├── feedback.py
    ├── menu.py
    ├── device_menu.py
    └── download_menu.py
```

## Testing

- `tests/test_detector.py` — feed known sine waves to aubio, verify correct note detection
- `tests/test_loader.py` — load a reference GP5 file, verify extracted notes match expected
- `tests/test_timeline.py` — verify timeline tick advancement, note activation windows
- `tests/test_downloader.py` — Songsterr search/download with mocked urllib responses
- Use `pytest`. Keep tests independent of audio hardware (mock sounddevice).

## Build & Run

```bash
pip install -r requirements.txt
python -m pickhero

# Package for distribution
pip install pyinstaller
pyinstaller pickhero.spec --noconfirm
# Or use build.bat on Windows
```

## What NOT To Do

- Don't add ML-based pitch detection. aubio YIN is sufficient and runs everywhere.
- Don't create a web UI or Electron wrapper. This is a desktop app.
- Don't add online features, accounts, or cloud sync. Offline-first, local files only.
- Don't over-abstract. Simple classes, no deep inheritance hierarchies. This is a ~3K LOC app, not a framework.
- Don't add **blind** polyphonic transcription ("here is audio, name every note") and don't add ML. aubio YIN is monophonic and stays the note detector.
  Verifying the notes the tab already predicts is a different problem and is allowed: `audio/chord_verify.py` checks each expected string against
  the partials no other chord tone can produce. That is signal processing against a known answer, costs ~1 ms per strike, and is what makes
  per-string right/wrong feedback possible.
