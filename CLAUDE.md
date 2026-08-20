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
all. Measured over `reference_recordings/`: strikes with no confident pitch run at **16-20 % on one or two strings** and **38-55 % from four
strings up** — an open A minor produced none in five strikes. Scored on pitch alone those strums are red however well they were fretted,
which is the "chords are not recognised" the player reports. It is not a speed problem: a fast single-note riff detects 47 of 49 and fast
power chords 38 of 39.

- **An unpitched strike credits a written chord of `MIN_UNPITCHED_CHORD_STRINGS` (2) strings or more.** It was 3 for one cycle, on the
  argument that a pitchless strike is rare below three strings and crediting it would be leniency bought with nothing. The rate was right and
  the conclusion was wrong: a two-string power chord goes pitchless on **16-20 %** of strikes, which is one in five of every chord in a metal
  song, and the question was never the rate but whether a wrong finger still shows. Re-measured through the real path
  (`tools/check_chord_credit.py`, power-chord takes now part of it): correct E5 **8/10 → 10/10**, G5 8/10 → 10/10, palm-muted E5
  **16/20 → 20/20**, fast E5 76/78 → 78/78 — and every deliberate one-fret error still caught, the palm-muted wrong take convicting **more**
  strings (6 → 10), not fewer. A single written note stays uncredited: a lone note with no pitch is what a dead note is for, and that path
  already exists.
- **The fifth of a power chord can never be confirmed, and that is not the same as never being checked.** Its partials are a subset of the
  root's, so a correctly played fifth is unprovable — but a fifth on the wrong fret sounds a different pitch, whose own partials convict it
  normally. That is why crediting a two-string shape is safe, and it is what the four error takes in `check_chord_credit.py` hold in place.
- **This credits the strum, not the fretting.** The strike still goes to `chord_verify.py`, which reads the raw audio and convicts any string
  it can positively show wrong. That is what makes crediting safe, and it is the same presumption of innocence run one level up: the strum
  is assumed played until a partial says otherwise.
- **Verified end to end on the reference takes** by `tools/check_chord_credit.py` — real audio through `AudioCapture`, scored by the matcher,
  verdicts applied by the verifier. Correct chords go from 54 % credited to 100 %, while all three deliberate one-fret errors are still
  caught (3, 9 and 6 string verdicts) and a chord with a string left out still passes. Re-run it after touching either the credit or the
  verifier's thresholds; it exits non-zero when a correct take loses a string or an error slips through.
- For an error take the tab must be the CORRECT shape: the manifest records what was **played**, so telling the verifier to expect the wrong
  note asks it whether the error is the error it was given, and it rightly says no.

## A Dropped Buffer Used To Stop The Clock

Every strike is stamped from the ring buffer's sample counter, which is what keeps timestamps free of wall-clock jitter. `_audio_callback`
used to `return` on any sounddevice status flag — so an overflowed buffer was discarded AND the counter stayed where it was. From then on
every strike in the song was stamped 10.7 ms early per dropped buffer, and the error accumulated until nothing matched.

Measured on a real play-along take (`reference_recordings/20260818_205930`), scored against the tab:

| condition | strikes heard correctly |
|---|---|
| nothing dropped | 42 / 46 |
| 2 % dropped, counter frozen (the bug) | 17 / 46 |
| 2 % dropped, counter still advancing | 40 / 46 |

**The lost audio was never the problem; the stopped clock was.** A status flag means samples were lost BEFORE the callback, so the buffer in
hand is still good and is now processed like any other. Overflows are counted and shown in the HUD, because a machine that drops audio loses
notes at random — indistinguishable, without a number on screen, from bad detection or bad playing.

This is also the warning the "detection is the problem" chapter below needs: the player's app scored 24 % on a take whose audio the detector
reads at 91 %. Everything measured from inside the app is measured through this.

## The Two Clocks, And The Speed Between Them

A strike is stamped in **recorded time** (the sample counter, real speed). The song runs in **song time**, which at 80 % practice speed
advances at 0.8 of it. `song = recorded x tempo` is only a position when both are counted from the same instant, so anything that changes
`tempo`, or restarts the stream, has to move that instant: `_reanchor_audio_clock()` sets the anchor to now and rewrites
`matcher.audio_offset_ms` to `anchor_song - anchor_recorded x tempo + sync`. Without it, pressing PgDn mid-song displaces every later strike
by `elapsed x change` — growing for the rest of the song, and not something `K` can take back. Strikes already queued at the moment of the
change were stamped under the old speed and are dropped rather than read under the new one.

`AudioCapture.start()` builds a **new** stream and a new ring every time. Called on a capture that is already running — which is what the
signal meter before the count-in does — the old stream is never closed and keeps writing into the same ring, so the counter advances at twice
real time. `_start_audio()` therefore always stops first.

**This is also why a diagnostic has to be told the practice speed.** A take played at 80 % is stretched against the written tab; read at
100 %, the first bar lines up and everything after it walks away. The player's own take read 22 % that way and **96 %** at the speed it was
played. `record_reference.py` now writes `tempo_percent` into the manifest (straight from the app's settings) and `analyze_play_along.py`
measures it when the manifest does not say.

## Seeking Must Not Reopen The Input Device

`seek()` and the loop restart used to close the sounddevice stream and open a new one, to give the matcher a fresh audio offset. On Windows
that is a real device open: the player reported the app freezing for about **ten seconds** after every arrow key, and a loop turn does the
same thing every few seconds. The offset was the only reason for it, and `_reanchor_audio_clock()` produces exactly that offset without
touching the hardware — see "The Two Clocks" above. Nothing else in a seek needs the stream restarted.

## When The Score Is Low, Say Which Half Is Low

A percentage cannot be debugged. The player's take scored **34.6 % in the app** and **97.4 %** through the identical detector and matcher run
over the recording offline — same audio, same song file, same hit window. Nothing on screen could say which of the two dozen steps in between
lost the notes, and the session before it was spent guessing at the wrong one.

Two things now answer that without another guess:

- **The completion screen names strikes heard next to notes credited** (`_heard_line`). Far fewer strikes than notes is the microphone path;
  as many strikes as notes with a low score is the matching. They are fixed in different places.
- **`D` writes a full run log** to `~/.pickhero/` (and every scored run writes one by itself). One line per strike — raw stamp, adjusted
  stamp, playback position, pitch, confidence, what became of it — plus every written note's final verdict, plus the header that explains a
  run: resolved sample rate, dropped buffers, gate, thresholds, tempo, offsets, filters. `matcher.strike_trace` is written only and never
  read back by the matcher.

## What A Run Log Answered, First Time Out

The instrument paid for itself on the first run: **91.9 % (57/62)**, against 34.6 % on the run before it, with the log naming everything
that was previously a guess — gate -65 dB, 0 dropped buffers, 44100 Hz resolved, no fret filter, no muted string, 0 strings taken back by
the verifier, the clock anchored at 5.4 ms. Every candidate on the list was cleared by reading, not by trying things.

What it could NOT say is which change did it, because two things moved at once: the fixes, and the player regenerating the timing test
(their copy was the older 78-note build). The one difference nobody had considered is that the 34.6 % run had `record_reference.py`
capturing from the same interface at the same time. **A score taken while the reference recorder is running is not evidence** until that is
ruled out — the offline replay of that very take reads 97.4 % through the identical code.

The five notes still lost were all named by the log rather than inferred: a two-string power chord that arrived pitchless (fixed, see the
chord credit above) and two one-semitone misreads.

## Rushing Is Not Scatter

The player's timing report says `mixed` with a ±46 ms spread, which reads as loose playing. The exported samples say something more
specific: inside every fast passage the error **ramps** from late to early and resets at the next phrase.

| passage | samples | first | last | drift |
|---|---|---|---|---|
| quarters, 7.8-16.2 s | 16 | +16 ms | -52 ms | 0.8 % fast |
| eighths, 17.4-23.1 s | 21 | +141 ms | -98 ms | **4.2 % fast** |
| eighth chords, 24.6-25.5 s | 4 | +18 ms | -64 ms | 9.2 % fast |

A ramp that resets at each phrase cannot be a clock (a clock would accumulate over the song and never jump back) and is not scatter either
— it has a direction. It is the oldest fault in the book, and `K` cannot touch it. The report does not name it yet; the samples are in the
`Shift+Y` CSV for whoever builds that.

## A Weak Input Does Not Lose Notes, It Renames Them

The obvious failure mode for a quiet signal is that strikes stop arriving. That is not what happens, and expecting it sends every
diagnosis the wrong way. Measured by attenuating the player's own play-along take in steps and reading it back through the real
detector — same audio, same code, only the gain changed:

| loudest hop (the number the HUD shows) | strikes produced | heard with the right pitch |
|---|---|---|
| -20 dB | 60 | 96 % |
| -32 dB | 56 | 96 % |
| -38 dB | 59 | 91 % |
| -44 dB | 58 | 83 % |
| -50 dB | 34 | 52 % |
| -56 dB | 5 | 9 % |

**The strike count barely moves until the very bottom; the pitch rots long before.** So a low-scoring run with plenty of strikes heard
is exactly what a weak input looks like — and also exactly what bad playing looks like, which is why the level is now written into the
run log (`level_loudest_db`, `level_median_playing_db`, `level_under_gate_percent`) rather than left to be guessed at. The knee is
around -38 dB and the collapse below -44, which is where `QUIET_PEAK_DB` comes from.

The HUD advice is bounded by the same measurement, and is **silent while the song is not running**: the peak decays once the playing
stops, so the completion screen used to report a level fault that was not there.

## Ringing Strings Defeat Detection

Measured with one note per string at a time (a new note on a string physically stops the old one — a summed test that lets both ring is
the synthetic trap `CLAUDE.md` warns about, and it produced a wrong root cause before this was corrected):

| passage | sustain | pitch detected correctly |
|---|---|---|
| quarters moving ACROSS strings | damped | 8/8 |
| quarters moving ACROSS strings | left ringing | **3/8** |
| pedal riff, all on one string | left ringing | 8/8 |

A line that walks across the neck while the strings it left keep sounding is polyphony, and monophonic YIN reports one pitch for it. On one
string the problem cannot arise. The collector's `SKIP_FRAMES` is NOT the lever — sweeping it from 3 to 12 changes nothing, because the old
note is still physically present however long you wait.

Confirmed at the instrument, not only in simulation: the player reports that muting after every note makes far more of it register, and that the
first note registers reliably once the string is damped before the next one. Over the whole timing test the same split appears — **59 % with
everything left ringing, 100 % with each string damped as it is left**.

The share that remains unexplained is the size of the effect, not its direction: the player measures ~24 %, well below the 59 % the model
gives, so a real guitar (sympathetic resonance, richer partials, longer sustain) is worse than the synthesis. Closing that gap needs the
song **played**, which `record_reference.py --play-along` records and `tools/analyze_play_along.py` reads back per onset — the 29 isolated
exercises cannot show it, because an isolated note with a rest after it is the case that already works.

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

## Two Backing Tracks, Switched Separately

The MIDI backing is generated from the same timeline as the notes, so it cannot drift: it is told a position and plays the events at it. A
recording has its own clock, running in the sound card, and the only control available is "start from here" — so `mp3_playback.py` compares
where the song is with where the recording got to and corrects only past `RESYNC_MS` (90 ms), no more often than `MIN_RESYNC_GAP_MS`
(1.5 s). A re-seek is audible; correcting an error nobody can hear costs more than the error.

- **They are separate toggles (`B` and `U`), not one control cycling through both.** The player asked for it that way and the reason is the
  workflow: lining a recording up against the click means hearing BOTH, then switching one off. A control that goes off → MIDI → recording
  makes the state the job needs unreachable.
- **Its own per-song offset (`Shift+N`/`Shift+M`), with no global fallback.** An MP3 decoder emits encoder padding before the music and how
  much depends on the encoder that made the file, so nothing about one song's value predicts another's, and nothing about the MIDI offset
  predicts this one.
- **Its range is seconds, not milliseconds, and it needs two step sizes.** The MIDI backing is generated from the same timeline as the
  notes, so it only ever needs the tens of milliseconds a synth adds; a recording is a different piece of music that happens to contain the
  same song, and can carry a count-in, an intro or studio silence before the first beat. `MAX_MP3_OFFSET_MS` is 30 s. `Shift+N`/`Shift+M`
  move 10 ms (what a sync is judged in), `Ctrl+N`/`Ctrl+M` move a second (reaching five seconds at 10 ms a press is five hundred presses).
- **A recording cannot be slowed down.** `pygame.mixer.music` plays at the recorded rate, and resampling to 80 % drops the pitch four
  semitones with it. Below full speed the recording is held silent and the HUD says why — the MIDI backing does follow the tempo and is the
  answer there. Lifting this needs a phase vocoder, not a setting.
- **Every failure is named on screen**: a file that has been moved, a decoder that cannot start from the middle of a file. A backing track
  that silently does not play is indistinguishable from a feature that does not work, and the player would go looking in the wrong place.
- **`play(start=)` really does seek — measured, not assumed.** A file whose pitch encodes its own timestamp, played from 5, 10 and 15 s
  with SDL's output captured to disk, comes back at the right pitch every time. So when a jump does not carry, the fault is in this app's
  path or in that particular file's decoder, and there is no point rewriting the transport. A decoder that cannot seek accepts
  `play(start=)` without complaint and starts from the top anyway, which is invisible — so a gap that stays open past
  `MP3_STUCK_DRIFT_MS` for `MP3_STUCK_FOR_MS` is named on screen rather than left to look like a dead key.
- **A status message must never outlive its situation.** Picking a file set a note that outranks the ordinary HUD line, and nothing cleared
  it — so the offset the player was adjusting with `Shift+N`/`Shift+M` was never on screen at all, and the key looked dead. Notes are for
  what the live line cannot say, and they are dropped as soon as it can.
- **Pause silences it too, and that took a bug to notice.** Every route to the recording reaches `Mp3Player.seek`, and seeking STARTS
  playback — so nudging the offset on a paused song set the recording playing under a picture standing still, which is the one state the
  offset cannot be judged in. `_mp3_plays()` includes `self._playing` for that reason. Paused, `Shift+N`/`Shift+M` only store the value;
  the HUD shows it move regardless, so the key never looks dead.

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
│   ├── mp3_playback.py  # a recording as a backing track, kept in sync
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
