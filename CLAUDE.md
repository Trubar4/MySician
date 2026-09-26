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

## Which Input Of The Interface The Guitar Is In

`_audio_callback` was written to downmix every channel "so the guitar is picked up no matter which interface input (1 or 2) it is plugged
into" — and `_resolve_input_settings` asked for **mono first, then stereo**, which takes that chance away: Windows then hands over input 1
alone and a guitar in input 2 arrives as silence. A stream that opens, a meter that reads nothing, and no error anywhere to say why. The two
halves of the file contradicted each other and the resolver won.

- **Stereo is probed first now**, falling back to mono for a device that really has one input.
- **The channel is chosen, not averaged.** The mean would halve a guitar sitting in one input — 6 dB given away, where the pitch starts
  rotting below −38 dB and collapses under −44, so a quiet take gets blamed on the player or the detector. The channel with the most energy
  wins, held across buffers by an EMA so a rest cannot make it flap.
- **The run log names the input** (`input_device`: name, index, channels of how many, resolved rate). A log reporting a silent stream without
  saying WHICH device was silent cannot tell a wrong device from a blocked one — and on a machine listing the same interface under MME,
  DirectSound and WASAPI, that is the whole question.

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

**The latency compensation is a real-world delay and has to be scaled too.** `audio_latency_offset_ms` is the sound card's buffer plus
aubio's analysis window — a fixed number of SAMPLES, indifferent to the practice speed. It was added to the song-time equation unscaled, so
at 70 % it over-corrected by 30 % of itself. Measured on the player's own 70 % run with a −220 ms offset: every strike landed **114 ms before
its note**, 66 of that this bug — a third of the 200 ms hit window, spent before they had played anything; scaled it comes back to −48 ms. At
50 % it would be 110 ms, over half the window. Slowing a song down is what you do when a passage is too hard, and it was quietly making the
scoring harder. `_sync_offset_song_ms()` is now the only reader; `K` measures in song time and therefore divides before storing, so an offset
calibrated at one speed still holds at every other.

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
- **It works from half a run, and says that it is half a run.** `D` is pressed at any moment and mostly will be: a song abandoned a third of
  the way in leaves two thirds of its notes PENDING, and hits over `notes_written` then reads as a catastrophe. So the header carries
  `notes_reached`, `notes_not_reached`, `reached_ms`, `played_to_the_end` and the loop, and the HUD says "up to 40 s". Same lesson as the
  stated practice speed: a number is only readable next to what it is a number of.

## What A Run Log Answered, First Time Out

The instrument paid for itself on the first run: **91.9 % (57/62)**, against 34.6 % on the run before it, with the log naming everything
that was previously a guess — gate -65 dB, 0 dropped buffers, 44100 Hz resolved, no fret filter, no muted string, 0 strings taken back by
the verifier, the clock anchored at 5.4 ms. Every candidate on the list was cleared by reading, not by trying things.

What it could NOT say is which change did it, because two things moved at once: the fixes, and the player regenerating the timing test
(their copy was the older 78-note build). The one difference nobody had considered is that the 34.6 % run had `record_reference.py`
capturing from the same interface at the same time.

**Measured since, and the recorder is innocent.** Two runs of the same song four minutes apart, one of them with `record_reference.py
--play-along` capturing from the same interface: the recorded run scored **98.4 %** (61/62) and the unrecorded control **88.7 %** (55/62,
six strings read a semitone flat in the eighth-note chords). Both logs show `dropped_buffers 0` and the same input level, so the second
stream costs neither audio nor clock. The 34.6 % run had a different cause, and the offline replay of that take reading 97.4 % says the
audio was never the problem.

The five notes still lost were all named by the log rather than inferred: a two-string power chord that arrived pitchless (fixed, see the
chord credit above) and two one-semitone misreads.

## The Rushing That Was Not There

Worth keeping as a warning about the reading, not about the playing.

A run scoring 91.9 % exported samples whose error **ramped** inside every fast passage — 0.8 % on quarters, 4.2 % on eighths, 9.2 % on the
eighth-note chords — resetting at each phrase. That is not a clock (a clock accumulates and never jumps back) and not scatter (it has a
direction), so it was written up as the player rushing, which is the oldest fault in the book and fits the shape exactly.

The next clean run, at 98.4 % with the input level fixed, shows **0.1 % over the same fifteen seconds**. Same player, same song, same
week.

So the ramp was almost certainly an artifact of unreliable pitches: when a strike is read as the wrong note it is attributed to whichever
neighbour it fits, and in a passage of repeating pitches the attribution slides along with it, which draws a ramp out of nothing. **A
timing sample is only worth as much as the pitch that anchored it** — so read `level_loudest_db` and the strikes-heard-vs-landed line in
the run log BEFORE believing anything the timing report says about playing. Whether the player rushes at all is currently unknown, and
the honest answer to give them is that it has not been measured.

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

**Confirmed on the instrument.** The run before had 56 strikes heard and only 25 landing on a written note — the exact signature above,
strikes arriving with the wrong pitch. With the input turned up (`level_loudest_db` -10.2, median while playing -23.1) the same player,
same song, same code: **98.4 %, 61 of 62**, 45 timing samples, nothing ambiguous. The level was the whole of it.

## Neither Too Loud Nor Too Quiet, And Useless

The first run log the player ever produced from the EXE scored **7 of 127 notes reached**, and every number in it was noise except one line:

```
input_device   Mikrofonarray (2- Intel® Smart  — index default, 2 of 2 channel(s) at 44100 Hz
```

The laptop's built-in microphone array, picked up as Windows' DEFAULT recording device because no device had been chosen. The guitar was never in the signal path at all.

| | |
|---|---|
| room | **-37.3 dB** |
| median while playing | **-37.2 dB** |
| strikes in 59 s | 25 |
| **strikes carrying no pitch** | **24 of 25** |
| notes heard as themselves | 2 |

**A tenth of a decibel between the room and the playing.** The input sounded the same whether the guitar was played or not, which is the whole diagnosis in one comparison — and the app said nothing, because `_level_advice` had a rule for too loud (`peak >= CLIPPING_DB`) and one for too quiet (`peak < QUIET_PEAK_DB`) and the peak was **-9.2 dB**, comfortably between them. A room mic in a room with speakers in it produces loud peaks; that is not the same as hearing an instrument.

- **The threshold is the gate ceiling, not a number of its own.** A room needing `room + NOISE_MARGIN_DB` above `MAX_GATE_DB` is a room the detector cannot be protected from, whatever the player presses. That is a state, and it now has a sentence: *"Input is hearing the room, not the guitar — wrong device? Pick your interface with D in the song list."*
- **It outranks the automatic gate.** With the automatic on, `_level_advice` deliberately says nothing about the gate — but this is not about the gate, and there is no key on that screen which fixes it.
- **It never fires on the reference takes.** The four takes the automatic gate was fitted against measure rooms of about **-70 to -86 dB** — more than 13 dB of margin — and the empty-band case the gate advice already handles (a hot compressed signal, floor -26 dB) is a different quantity: the live floor BETWEEN strikes, not the room measured while the song is stopped.
- **The run log carries the verdict, not two numbers eight lines apart** (`input_hears_the_room`). The same rule as strikes-heard beside notes-credited.

**And the rest of that log was clean, which is the second half of the lesson.** `frames_over_budget_percent 0`, `mp3_worst_drift_ms 53`, `mp3_resyncs 0`, and the audio clock's 178-second leap between two strikes was a pause handled exactly as designed — the sample counter runs while the device stays open, and `_reanchor_audio_clock` put the offset back (the raw-to-adjusted offset is constant to 0.1 ms within each block either side of it). None of the app-side suspects for the picture/sound drift appear in it. **A wrong input device makes every other number in a run log unreadable**, so it has to be the first thing checked and the first thing the app is able to say.

## The Advice Was Telling Them To Press Two Keys That Undo Each Other

"It always shows me C and then X again. I am playing too quietly and too loudly." Both halves of that were true, and neither was about the
playing. `_level_advice` had two rules naming opposite keys — "barely above the gate, press X" (X lowers the gate 5 dB) and "background noise
reaches the gate, press C" (C raises it) — and a gate satisfying both needs `peak - floor >= QUIET_MARGIN_DB + NOISE_MARGIN_DB`, **18 dB**.
The tracked peak decays 3 dB/s and the floor recovers upward at the same rate, so between strikes a distorted rock signal is well inside 18 dB
and **no gate value exists**. Simulated over the player's own numbers: `X C X C X C…`, for ever.

C had no bound and the ceiling was **-20 dB**, so following the advice ratcheted the gate to the top in eight presses. What that cost, measured
on their run of a real song (1384 notes, 549 picks):

| | |
|---|---|
| audio discarded by the gate | **40 %** (8 % at -30 dB) |
| loudest hop / median while playing | -3.8 dB / -18.4 dB — the gate sat **1.6 dB under the median** |
| picks that produced a strike at all | 377 of 549 |
| **single-note picks heard** | **153 / 282 — 54 %** |
| **chord picks heard** | **224 / 267 — 84 %** |
| notes credited | 831 / 1384 — 60 % |

**The gate was set for the loudest thing in the song and it deleted the quietest.** Section by section the hit rate simply follows how many
strikes arrived: the clean single-note verses ran 0.39-0.56 strikes per pick and scored 11-30 %, the distorted chorus 0.78-1.07 and scored
55-89 %. A six-string strum survives a gate that a single clean note cannot reach, which is why the score looked like "chords work, solos do
not" and had nothing to do with either.

- **The gate has a band, and the band can be empty.** `gate_band()` returns above-the-room and below-the-playing, and `lowest > highest` is a
  real state — a hot, compressed signal has less than 18 dB to put a gate in. It has to be a state the advice can EXPRESS; being unable to say
  it is what made the panel ask for both keys.
- **When no gate satisfies both, the notes win.** A gate under the room costs spurious onsets, which the confidence filter and the candidate
  search already throw away. A gate over the playing costs the strikes themselves, and a strike that never arrives cannot be recovered by
  anything downstream. So X fires while the gate is above the band and C only while a real band exists to raise it INTO.
- **The property is asserted, not the wording.** Press whatever key the advice names, from any gate, over a grid of levels: it always stops,
  and never reverses direction. That is the thing that was broken; the sentence was only how it showed.
- **The ceiling is where the DETECTOR gives up** (`MAX_GATE_DB`, -50 dB). At a -44 dB loudest hop the pitch still comes back right 83 % of the
  time — see the table above — so there is nothing to be won by gating away audio that could still have been read. One clamp, in
  `set_noise_gate_db`, so the keys, the settings screen and a saved file all land in the same range; a stored gate above the ceiling is
  repaired on load, because it was only reachable through the bug.
- **The advice names the value to reach**, not just the key. `suggested_gate_db` puts it on the 5 dB grid the keys move in, and the run log
  prints the same number next to `level_under_gate_percent` — a percentage of discarded audio is only readable beside the value that would
  not have discarded it.

## The Gate Sets Itself, And Only Ever Downwards

"Can we build it so the gate adjusts itself?" Yes — but the measurement changed what it should adjust TO. Swept over the four real play-along
takes (`tools/sweep_noise_gate.py`, alignment and tempo fitted ONCE at -80 dB so a gate that deletes strikes cannot also choose the grid it is
judged against):

| gate | 20260824 | 20260818 | 20260819a | 20260819b |
|---|---|---|---|---|
| -80 … -55 dB | 43/62 | 42/62 | 27/62 | 44/62 |
| -50 dB | 43 | 40 | 27 | 41 |
| -40 dB | 42 | 41 | 27 | 35 |
| -30 dB | 39 | 24 | 12 | 16 |
| **-20 dB** | **24** | **0** | **0** | **0** |

**The response is flat across the whole safe range and then falls off a cliff**, and at -20 dB three of the four takes produce literally
nothing. So there is no optimum to hunt for — only a ceiling to stay under, which means a controller that hunts up and down is optimising
something with no gradient and can only do harm on the way up.

**The knee moves 15 dB between takes** — -55 dB on one, -40 dB on another — because it follows the interface gain, which is a knob on a box
the app cannot see. That is what makes this worth automating: not that the right value is hard to compute, but that it is different every
session and the player has no way to know it. And a gate costs nothing to keep low: a fully processed hop is **0.23 ms of its 11.6 ms**, so
there is no work being saved by discarding audio either.

- **The room is what the microphone hears while the song is NOT running**, including the count-in — the longest clean window a run offers,
  since the player is not meant to be playing yet. `gate = room + NOISE_MARGIN_DB`, capped at `MAX_GATE_DB`.
- **A low percentile of the PLAYING is not the room, and that was wrong for a day.** The run log estimated it as the quietest 2 % of the level
  samples. Measured across one session's takes, that percentile runs from **-35 dB on a dense passage to -94 dB on a sparse one**, against a
  recorded room of -73: it reports how busy the playing was. The log now prints the measured room or says `(nicht gemessen)`, and without one
  it suggests no gate at all.
- **Derived every song, not accumulated.** A value that only ever walks one way ends up wherever the last session left it.
- **Mid-song it can only ever come DOWN.** `_loudest_db` only rises, so the level it demands only rises with it: once satisfied the correction
  can never fire again, and it cannot flap the way the ADVICE it replaces did. Raising it mid-song could only delete strikes, and a strike that
  never arrives cannot be recovered by anything downstream.
- **Verified against the sweep it came from**: the rule picks -64, -78, -63 and -80 dB on the four takes and scores **43, 42, 27 and 44** —
  the best value in the entire sweep, on every take, not one note lost. `sweep_noise_gate.py` exits non-zero if that ever stops being true.
- **X or C switch the automatic off**, and so does the settings screen's own gate row. An automatic that silently undoes what you just set by
  hand is worse than one that was never offered. With it on, `_level_advice` says nothing about the gate at all — it would be naming a key the
  app is already pressing for you — and what is left there is the interface's GAIN, which no gate can fix and only a hand on the knob can.

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

**The player's own takes do not show this at all, and that has to be said before anything is built on the table above.** Splitting every
written note in the three real play-along recordings by whether its predecessor shared its string:

| predecessor | pitch heard right |
|---|---|
| same string | 133 / 159 |
| **different string** | **40 / 40** |

Not one string change costs anything. The catch is that in `timing_test_100bpm.gp5` every string change sits in the SLOW opening section,
where the previous note has decayed long before the next arrives, while every fast passage stays on one string. So the takes cannot settle
it either way — they hold the easy half of the case and none of the hard half.

**Recorded and measured** (block 5 of `record_reference.py`, read by `tools/analyze_ringing.py`) — and the answer is not the one the
synthesis gave:

| line | exactly right | an octave out | a DIFFERENT note | no pitch at all | usable strikes |
|---|---|---|---|---|---|
| slow, damped | 12 | 0 | **0** | 3 | 80 % |
| slow, ringing | 7 | 4 | **0** | 2 | 85 % |
| fast, damped | 12 | 1 | **0** | 3 | 81 % |
| fast, ringing | 6 | 2 | **0** | 6 | **57 %** |

**Nothing ever comes back as a different note.** The synthetic 3-of-8 predicted wrong pitches and there are none — not one, in any take. What
ringing strings actually cost is strikes that carry **no pitch at all**, and only at speed: slow is untouched, fast loses 24 points. Octave
slips appear too, but the matcher grants octave equivalence on purpose, so they stay green and cost nothing on screen.

That changes the fix, and made the planned one wrong: there is no wrong pitch to correct, only nothing to credit. See "Rescuing A Strike
That Carries No Pitch" below.

**Two tools lied before they told the truth here, both by walking two lists in step.** `analyze_ringing.py` reported 16 % for the DAMPED
takes — the control, known to work — because one pitchless strike shifts every comparison after it; and the regression check then convicted
the rescue of inventing notes for the same reason. Both use Needleman-Wunsch now. **A tool whose control comes back broken is measuring
itself**, and neither number should have been believed for a moment.

## Rescuing A Strike That Carries No Pitch

A strike with no pitch is not evidence of nothing. On a line played across the strings without damping it is the commonest thing that
happens at speed, and the note was played — the ringing neighbours simply left monophonic YIN no single period to lock onto.

So when a strike arrives unpitched and a **single** note is written there, the matcher holds it (`_hold_for_rescue`) and asks
`ChordVerifier.confirms` whether that written pitch is present in the audio window. Confirmed, the note is credited.

- **It only ever acquits.** `verify` asks which of several expected notes each string played and can convict; `confirms` asks one question
  about one note and can only answer yes or stay silent. No intruder tier: with a single expected note there is no chord to be masked by, so
  "something else is louder" only says another string is still ringing, which is the premise rather than evidence.
- **A note already marked MISS can still be rescued.** The window trails its strike by ~380 ms by design, so the verdict arrives after the
  note has timed out; refusing it for being late would throw the evidence away for arriving exactly when it was always going to.
- **A chord is not rescued and a dead note is not either** — both already have their own rule, and neither needs audio.
- **Measured, with the damped takes as the control** (`tools/check_ringing_rescue.py`): fast ringing 8/14 → **12/14** (10/14 as first written up, which was
  the batched harness — see "Four Tools Batched What The App Interleaves"), and every damped take gains exactly **nothing**. A rescue firing on a damped take would be a note being invented, which is what that check exists to catch; it
  exits non-zero if one ever does. The chord takes and the play-along takes are unchanged.
- It closes about half the gap, not all of it (57 % → 71 %, against 81 % damped). The two strikes it cannot recover are the high A4, whose
  partials sit among the ringing lower strings' harmonics at a margin of 3-5 dB — too little to act on.

## An Arpeggio Comes Back As One Note, And It Is Not Any Of Them

The gate was fixed and the same song still scored 29 %. The run log said `level_under_gate_percent 0`, so nothing was being discarded — and
yet **53 of the 62 strikes that matched nothing written were flagged `subharmonic`**. That flag is the whole answer.

A subharmonic pitch is not a reading of one string. The detector folds it up from BELOW the guitar's range because several strings that are
ringing together share that period — so its value names the chord sounding in the room, not the note just struck. Reconstructed from the log
(a note rings until the next note on its own string, which the log gives):

| what was ringing | what came back |
|---|---|
| A2 + E3 + B3 | **A2** |
| G2 + D3 + B3 | **G2** |

Fifteen of the twenty-three testable cases are exactly the common period of the sounding set. It appears here and not in block 5 because
block 5 is a melodic LINE: consecutive notes are often dissonant and share no strong period. An arpeggio is the opposite — the tab writes
single notes that are meant to ring into a chord, and consonant intervals have a very strong missing fundamental. So the same passage that
sounds best is the one the detector reads worst.

- **Where the pitch says nothing, ask the audio.** A subharmonic matching nothing written is worth as much about the note just struck as no
  pitch at all, so it now goes where a pitchless strike already goes: `_hold_for_rescue`, and `ChordVerifier.confirms` reads the raw window
  and says whether the written pitch is really there. No new mechanism — the one built for ringing strings, offered a case it was never shown.
- **A subharmonic that DOES fit keeps its own rule.** It proves the strum outright and needs no audio; only the unmatched ones are held.
- **An ordinary wrong pitch stays wrong.** A clean reading of one string is evidence, and the presumption of innocence does not extend to
  ignoring it. The flag is what separates the two.
- **The controls are what make it safe, and they are not this song.** `check_ringing_rescue.py` (damped takes gain +0, +0) and
  `check_chord_credit.py` (every deliberate one-fret error still caught) both come back unchanged. `check_subharmonic_rescue.py` scores the
  four play-along takes through the real matcher with the rule on and off: nothing lost, one note gained.
- **That +1 is not the size of the effect and must not be quoted as one.** The timing test is single notes on one string, where the case
  barely arises; the player's own song is 53 unmatched subharmonic strikes, 51 of them sitting on a written note that missed. **The gain on
  that song is not measured** — there is no recording of it. Ask for `record_reference.py --play-along` before believing any number.
- **The control has to hold the rule against ITSELF.** The first version of `check_subharmonic_rescue.py` compared the rule against a matcher
  with no chord verifier at all, and reported a take losing a note to a rule that had not fired once — it was measuring the chord verdicts.
  `subharmonic_rescue=False` exists for that one purpose.

## The Onset Detector Never Heard The Arpeggio At All

With the gate fixed and the subharmonic rule shipped, the same song scored 29 %. The rule had fired 25 times and credited **nothing**, and the
reason was one line: `_hold_for_rescue` required **exactly one** pending written note. That was right for the case it was built for — a line
across the strings, where the tab writes one note at a time — and silently wrong for an arpeggio, whose written notes overlap by design. On the
player's take the window held two or three notes every single time. It now asks about the pending note whose own onset is NEAREST the strike;
holding one does not consume it, since `_apply_rescue` only credits a note still not HIT or CLOSE when the window lands.

That was worth +3 notes. The thing underneath it was worth ten times more, and it is not in the matcher at all.

**`onset_threshold` was 0.3 — aubio's default — and at that value the detector hears 37 % of the picks in an arpeggio.** A new note under a
ringing chord is a small change in spectral flux, so the onset never fires, and a strike that never arrives cannot be recovered by the matcher,
the verifier or any rescue. Swept over the player's own take (`tools/sweep_onset_threshold.py`):

| threshold | picks heard | right pitch |
|---|---|---|
| 0.40 | 29 % | 11 % |
| **0.30** (was) | **37 %** | 12 % |
| 0.15 | 57 % | 19 % |
| **0.05** (now) | **83 %** | 26 % |
| 0.02 | 87 % | 28 % |

**A single-note line is indifferent to all of it** — the timing-test takes read 93-100 % of their picks at every value from 0.02 to 0.40, and
only the count of spurious strikes moves. So the old value cost nothing on the material it was chosen against and most of the song on material
nobody had recorded.

Measured through the real matcher on that passage: **18 % → 39 %** from the threshold alone, **→ 44 %** with the rescue fix on top. On the four
timing-test takes with a fixed tab: 60/62 unchanged, **38 → 45**, **36 → 44**, 62 → 61. Every deliberate one-fret error is still caught, and
the palm-muted takes are unchanged.

- **`onset_min_interval_ms` was built, measured and removed in the same hour.** The theory was that a low threshold lets a decay re-trigger, so
  aubio's 50 ms minimum should rise. Above 100 ms it starts merging real chugs (the fast palm-mute take's 5th percentile gap is 75 ms), and at
  50 ms nothing needed fixing: every fixed-tab control passes. A knob that changes nothing is a knob nobody can calibrate.
- **A stored 0.3 is migrated**, the way a stored `buf_size` of 2048 is: there is no UI to set it, so the value came from the old default.

## The Rescue Was Asking A Question With Half The Facts

With the onset threshold fixed, the arpeggio's error budget is no longer about strikes arriving: 145 written picks, 134 strikes, **129 of them
on the grid**. Of the 86 notes that still failed, **53 are "a strike is there and its pitch is subharmonic"** and only 12 have no strike at all.
So the whole remaining question is what `ChordVerifier.confirms` does with those.

The funnel said 32 held, 24 asked, **8 confirmed** — and the reason for the 16 refusals is not what it looked like:

| why `confirms` said no | how many |
|---|---|
| the margin over the runner-up was under 8 dB | **14** |
| a different note won outright | 2 |

In those 14 the written note **won**, at -0.7 to -10.4 dB — practically the loudest thing in the window. It failed only because a rival
hypothesis a semitone or two away scored nearly as high.

**It scored high on partials that were not its own.** `confirms` passed `others=[]`, so every candidate could claim any partial in its bands,
including those of the strings still ringing. It was built for a line played across the strings, where the neighbours are decaying and it
hardly matters; in an arpeggio they are the loudest thing in the window.

- **The tab already knows what is ringing** — a note sounds until the next note on its own string — so the matcher passes it
  (`_sounding_beside`), and `_score` excludes those partials from every candidate. The same rule `verify` has always applied to the tones of a
  chord, now applied to the tones that happen to be sounding.
- **It cuts both ways, which is what makes it safe.** The written note's own score loses its shared partials too, and where its partials are
  entirely a subset of what is already ringing, it becomes unconfirmable and the rescue abstains. That is the presumption of innocence, not a
  loophole.
- **Measured: the arpeggio goes 43 % → 49 %**, rescues 8 → 16, on the player's own recording against the real tab. The chorus take is
  unchanged at 68 %, the damped control takes gain **+0**, every deliberate one-fret error is still caught, and no play-along take loses a
  note.

## Two Tools Measured Themselves, Again

Both were caught only because a control came back wrong, which is the third time in this project.

- **`MAX_QUEUED_WINDOWS` is 16, and every offline harness ignored it.** The app drains `get_strike_windows()` once a frame; the check tools
  pushed a whole take through `_audio_callback` and collected once at the end, so the queue dropped everything but the **last sixteen** strikes.
  On a 45-second take that is a quarter of them, and the verifier then appears to do nothing when it was never given anything to do — which is
  exactly what the first run of `check_subharmonic_rescue.py` reported. All four check tools now drain as they go.
- **`check_ringing_rescue.py` builds its tab out of the strikes** (`intended()` aligns what was detected against the line that was asked for),
  so a detector setting that changes how many strikes there are also changes the ground truth. Lowering the onset threshold made a DAMPED take
  appear to gain a note — the tool's own definition of inventing one. It pins `FITTED_ONSET_THRESHOLD` now: it tests the rescue, at the settings
  the rescue was fitted at, and cannot be read as a verdict on those settings.

## A Take Of The Chorus Could Not Be Read At All

The player recorded the section that was asked for and it came back reported as "the intro again". The recording was right; the alignment
could not express what it was. `best_offset_at` searched offsets from 0 to 30 s — where the SONG starts inside the recording — on the
assumption that a take always begins at the beginning. A take that starts in the MIDDLE of the song needs the opposite: an offset as negative
as the song is long. Forced into the only range it had, the search put a 45-second take of the last verse at the opening, where 48 of its 134
strikes happened to land, and every number after that was read against the wrong bars.

- **The search covers the whole song now**, and cheaply: the optimum always sits at some (strike − onset) difference, so those are the
  candidates. Histogram them at the match width, then search finely around the busiest few regions — eight, not one, because the true offset
  can lose the raw count to a dense passage that lines up with a different bar.
- **Re-read, the take is the chorus**: song 133-177 s, **153 of 153 strikes on the grid**, 555 written notes over 149 picks. The material
  that every chord question in this file needed and no recording had.
- **The old numbers for that take are void**, not merely imprecise. This is the second time a default in the alignment path turned a good
  recording into evidence of a broken detector, after `--play-along` guessed the song.

## A Take Is Only Worth Its Manifest, Part Two

`record_reference.py --play-along` took the song as an OPTIONAL argument defaulting to `timing_test_100bpm.gp5`. Run without it — which is how
it was explained to the player — it recorded 45 seconds of a completely different piece and wrote the timing test's name into the manifest.
Read against that tab the take scores **3 of 46 notes**, which looks precisely like a detector that has stopped working; read against the notes
reconstructed from the run log, **58 of its 60 strikes** land on the grid. The song argument is required now.

**A run log is a tab.** It prints every written note with its time, string and pitch, which is all that is needed to score a take of a song that
is not in `songs/` — `sweep_onset_threshold.py --run-log` reads one, and that is how the arpeggio above was measured at all.

## The Calibration Was Wrong And It Did Not Matter

Worth writing down because the obvious conclusion was the wrong one. The player's stored calibration had the A string at **54.87 Hz (A1, an
octave low)** and the high E at **109.83 Hz (A2 — the A string's pitch)**. `_correct_octave_jump` halves a frequency whose half lands within a
semitone of a calibrated string, so a clean A2 would be pushed below the guitar's range and come back flagged as a subharmonic. It looked like
the source of the subharmonic flood.

**Measured on the take, with and without that calibration: 29 subharmonic strikes either way, and one note different in sixty.** The halving
needs `confidence < 0.9` and the readings here are confident. So the calibration is a latent trap and should be re-run, but it explains none of
this — and a fix shipped for it would have been a fix for nothing.

## The Room Could Never Be Heard

The automatic gate shipped inert, and the run log said so in as many words: `level_room_db (nicht gemessen)`. The room is what the microphone
hears while the song is NOT running — and the input stream was opened by `_start_audio`, which runs when the count-in ENDS. There was never a
moment with the device open and the song stopped, so the estimate never reached its minimum sample count and the gate never moved.

- **The stream opens when the count-in BEGINS.** That is the window the design was written around; it just was not open yet.
- **And `_start_audio` reuses it instead of reopening.** A device open on Windows is seconds — the freeze this project has now paid for at
  seeks, at the pause and at the instrument change. The two clocks are agreed by anchoring to `elapsed_ms()` rather than by restarting the
  counter, which is what `_reanchor_audio_clock` already did for every other case.
- **A feature that cannot be seen working is indistinguishable from one that does not work**, and the only reason this was caught in a day is
  that the log prints `(nicht gemessen)` instead of quietly printing a number.

## Eighty Percent That Does Not Feel Like Eighty Percent

"I think it is better now, but not good. From what turns green I have the feeling I was far worse than the 80 % it shows." That reading is
correct, and the run log says why. Between two runs of the same song, twenty minutes apart, only the onset threshold changed:

| notes in the chord | written | at 0.30 | at 0.05 | gain |
|---|---|---|---|---|
| 1 | 282 | 18 % | **50 %** | +91 |
| 2 | 88 | 58 % | 81 % | +20 |
| 4 | 532 | 73 % | **91 %** | +97 |
| 5 | 290 | 78 % | 82 % | +14 |
| **6** | **192** | **55 %** | **94 %** | **+74** |
| | 1384 | 59 % | 81 % | +296 |

The single-note gain is the arpeggio and it is real — scored against the true tab on the player's own recording, that passage reads 43 %
where it read 18 %. **The chord gain is a different thing entirely**, and it is not a measurement of the playing.

**A four- to six-string chord is credited from ONE strike.** The strum is heard; the fretting of the other five strings is not, and monophonic
detection can never report a second chord tone to confirm them. `chord_verify.py` is what polices that, and it can only convict a string whose
partials are not a subset of a lower one — which in an open chord is most of them. Over the whole song it took back **6 strings**. So at
711 strikes instead of 359, nearly every written chord has a qualifying strike near it, and nearly every chord goes fully green.

- **The two are counted apart now and reported apart.** `notes_heard_as_themselves` and `notes_credited_to_a_strum` in the run log, and a line
  under the score: *"389 of them were heard as themselves, 729 credited to a strum that was heard"*. On the player's run that is the whole
  answer — two thirds of the green rests on strums, not on notes. A percentage that mixes them cannot answer "was I really that good", and a
  player who feels the score is too kind is reading something real. Confirmed on the next run by the app's own instrumentation: **220 heard,
  657 credited to a strum**, from 239 productive strikes.
- **The run log now splits the score by chord size**, which is the line that makes it obvious: on that run, single notes **20 %**,
  four-string chords **94 %**. The percentage was never a lie; it was adding two different things.
- **And it cannot be lowered by checking the strings, because a missing string is not measurable.** The takes recorded for exactly this
  question say so (`tools/check_missing_string.py`): scored against the CORRECT shape, a power chord's omitted fifth reads **-48 dB against
  -21 dB** for one that was played — 27 dB apart, plainly separable. The same test on a six-string E major, with the high e left out, reads
  **-37 dB against -45 dB at the tenth percentile of the played ones**: a **14 dB overlap**, and the omitted string scores HIGHER than the
  played one does in the correct take. In a full chord the high strings' own partials are buried under the harmonics of the low ones, so
  there is nothing left to measure. No threshold exists, and "a chord with a string left out still passes" is therefore not a policy that can
  simply be reversed.
- **The number was not lowered, because nothing measurable says by how much.** The takes that fitted the chord credit are ISOLATED chords with
  long gaps, where a verification window always arrives; in a dense song at 273 ms spacing it often does not. Tightening the credit is
  therefore unmeasurable with the recordings that exist — and this project does not ship a threshold it cannot re-fit. What is needed is a
  play-along recording of a strummed CHORUS, not another arpeggio.
- **Two candidate fixes were built and thrown away for changing nothing measurable.** Narrowing the hit window from 200 ms to 80 ms moves the
  arpeggio by one note (43 % → 42 %). Refusing to let a strum's own second onset trim its verification window — a real effect, since an
  isolated strum fires again 53 to 181 ms later — leaves the window count on that recording at exactly 109 either way. A constant that changes
  nothing is a constant nobody can calibrate, the same reason `onset_min_interval_ms` lasted an hour.

## The Key That Wrote The File And Said Nothing

"Mit D passiert nichts." It was doing its job perfectly and never saying so. `_run_log_note` was drawn **only inside
`_draw_completion_overlay`**, so pressing `D` in the middle of a song wrote the file and put its confirmation on a screen the player would not
see for another four minutes. The same fault, twice over: `_auto_gate_note` was assigned in two places and **read nowhere at all** — the
automatic gate moved silently every song.

- **A note that expires, over the footer.** `_say()` puts one line on screen for `STATUS_NOTE_SECONDS`; `_status_note_text()` returns it only
  while it is still news. Both halves matter — a status message that outlives its situation is the fault this project already shipped for the
  MP3 offset, and one that is never shown is this one.
- **Leaving the song writes the run too.** Until now only reaching the last bar did, and nobody plays four minutes to the end while something is
  being diagnosed — so the run most worth reading was reliably the one that produced no file. `stop_audio` writes it unless the song already
  finished (which wrote its own). It says how far it got; that is what `notes_reached` is for.
- **This is the fourth time.** A backing track that silently does not play, a `U` with no line to name `Shift+U`, an automatic gate that shipped
  inert with `(nicht gemessen)` as its only tell, and now this. **A feature that cannot be seen working is indistinguishable from one that does
  not work** — and here it cost a week of diagnosis, because the file the whole investigation depended on was never being produced.

## Does The Picture Keep Real Time?

"Bei Takt 9 liege ich mit der Visualisierung bereits 300 ms zurück. Bis Takt 17 sind es nochmals ca. 300 ms." At 132 BPM in 4/4 that is bar 9 at
14.5 s and bar 17 at 29.1 s — **a linear 2.1 %**, from the first bars, not something that starts later. Two mechanisms produce exactly that and
they are fixed in different places: the tab and the recording disagreeing (measured at −1.08 % for this song — see the chapter above), or the
app's own clock losing time.

The picture advances by `perf_counter` deltas capped at `MAX_FRAME_STALL_S` (250 ms), so **every stalled frame is song time discarded** — a
machine that stalls scrolls slower than the wall, and the recording, which keeps its own clock in the sound card, walks away from it. That is a
picture falling behind sound, and it had no number.

- **`clock_real_s`, `clock_song_s`, `clock_ratio`, `clock_lost_ms`, `clock_stalls`** in the run log. Uncapped elapsed on one side, what was
  actually credited on the other, so the difference IS the time the cap threw away. A ratio of 0.98 says the picture ran 2 % slow and names the
  stalls that did it; a ratio of 1.000 says the app is honest and the divergence is in the files or in the recording's own transport
  (`mp3_worst_drift_ms`, `mp3_resyncs`, `mp3_worst_seek_ms`).
- **Not reset by a seek or a loop.** The question is what the machine did over the whole sitting, not since the last arrow key.
- Same rule as strikes-heard beside notes-credited, and as `frame_ms_median` beside `frames_over_budget_percent`: a percentage cannot be
  debugged, and two causes that look identical on screen have to be counted apart.

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
`hammer_to_next`. Extracted in `tabs/loader.py` — from pyguitarpro's already-normalised effects for GP3-5, and by hand from the GPIF XML for
GP6, GP7 and GP8 (`_parse_gpif_notes`).

- **Drawn inside the note, badge above it** — the way Yousician does it, and the only thing a six-lane layout allows: a curve arcing out of
  its lane reads as a note on the neighbouring string. The white technique line always gets a dark shadow (`_draw_technique_line`), because
  white on the amber string is invisible and an invisible technique will not be played.
- **Scored so the drawing is not a lie.** A bend accepts the whole region it covers (`_build_pitch_ranges`), so a correctly played technique is
  never marked wrong for leaving its written pitch. A hammered, pulled or slid-into note is never picked, so it inherits its source's verdict
  (`_legato_credit`); waiting for a strike on it could only ever end in a miss.
## How Far The Bend Went

"Judging how FAR a bend went needs a pitch contour the detector does not produce" was wrong in one word. The detector produces one every
~11.6 ms, the audio thread already sends it (`is_onset=False` readings on the same queue), and the matcher was throwing it away at the top of
`process_detected_notes`. Nothing new is measured; the readings are simply kept.

- **It can only ever turn green into yellow.** The player's ruling: a bend that arrives short is a note played imperfectly, not a note missed.
  A bend on a note already CLOSE or MISS is left alone — there is nothing left to take away.
- **Two questions, both of which the player named.** Did it get there (highest reading against the written top, within a quarter tone), and was
  it HELD (the tab says how long the bend stands at its top; touching the pitch on the way past is not holding it). The hold is only asked when
  the tab writes a hold worth the name — a bend across a sixteenth has no plateau.
- **Neither convicts on silence.** Too few readings inside the note returns "unknown" and the note keeps what it was given. Absence of evidence
  is the commonest thing in this signal path, and the chord verifier learned the same lesson the hard way.
- **The thresholds are measured now** (block 6, 2026-08-23, 18 bends). `tools/check_bends.py` prints the window each has to sit in — worst
  correct take against best deliberate error — and then runs the real matcher over the same audio: all 12 correct bends green, all 6
  deliberate errors yellow.
  - **Tolerance 50 cents, and it cannot be tightened.** The player's correct bends land 7 to 51 cents ABOVE the written top — every one of
    them overshoots. A 40-cent band starts marking their own good takes down; the deliberately shallow take misses by at least 63. Window
    50-63, and the guessed 50 turned out to sit in it.
  - **Hold 30 % of what the tab writes, as one unbroken run** (gap tolerated, below). Correct takes run 43-100 % of the written hold; the
    not-held take reaches 0 %. The guessed 50 % was too strict and would have marked down a real take.
  - **Both questions are one-sided.** Did it reach the top, and was it still up there. Overshoot is intonation, which nothing here was asked
    to judge — and judging it would convict the very takes recorded to prove the rule works.
- **Three things the recording changed, and each was a wrong answer first:**
  - **Only picks are bends.** The onset detector fires again during a note's decay; those ghosts came back as bends that never left the
    written pitch, and the first run of the tool duly reported that correct and shallow bends overlap so no threshold could work. A real pick
    peaks at −6 to −8.5 dB and every ghost at −21 to −49. The app never had this problem — it reads the contour over the note's WRITTEN
    window, out of the tab — but the tool did, and a tool whose control comes back broken is measuring itself.
  - **An octave error is not the bend collapsing.** During a vibratoed bend the detector throws out readings 9, 18 and 36 semitones below the
    written pitch. Left in, they read as the pitch falling off a cliff. `BEND_STRAY_SEMITONES` drops them.
  - **A hold is a run, not a count and not a span.** Counting frames on target marks vibrato down (only 37 % of a vibratoed bend's readings
    sit within a quarter tone — it is played by releasing and re-bending). A plain span from first to last lets a bend flicked up twice pass.
    The longest run with a tolerated gap of 250 ms tells all three apart, and from 250 ms upward the reading stops changing, so the value sits
    on a plateau rather than a knife edge.
- **Measured where the hold is looked for, too.** Looking only inside the written hold window sounds stricter and is weaker: a bend let go
  early takes the note with it, the window then holds no readings at all, and the rule abstains for want of evidence — which let two of three
  deliberately-not-held bends through. The run is measured anywhere in the note; what the tab asks for is a duration, not a place.
- **Wait mode stops the collection**, because it pins every timestamp to one instant and a contour whose readings all claim the same
  millisecond cannot say how long anything was held — the same reason timing samples stop there.

- **A sliding note gives up part of its sustain** so the connector has somewhere to be; back-to-back notes otherwise leave a few pixels.
- `tools/make_technique_test.py` writes a GP5 stating exactly which technique is where, so a wrong drawing is the app's fault.

## Handing The Tab To An Engraver

A classic tab — six lines, fret numbers, and stems saying how long each note is — is music ENGRAVING, and this project is not going to grow a notation engine. **verovio** does it, is Python, ships as a wheel, runs offline, and renders 150 bars in **90 ms**; its timemap gives the millisecond of every note and its SVG carries an id per note, which is what a playhead and per-note colouring need. What it cannot do is read Guitar Pro. `tabs/musicxml.py` is the bridge.

**The packaging question was asked first, and of a rendered page.** verovio carries 20 MB of fonts and schemas, imports perfectly happily without them, and then renders nothing — the exact class of fault this project keeps shipping. `tools/check_verovio.py` loads a tablature measure and fails unless the SVG really contains tablature AND becomes ink on a rasterised page — the second half was added after a page that passed every check turned out to be blank on screen; the Windows workflow runs it, and the EXE answers `--check-engraver` after it is built. The run log carries `engraver` (`ready` / `absent` / `present but its data files are missing`), because the EXE is the only place the answer counts.

Two things had to exist in the reader before the bridge could be honest:

- **`NoteEvent.duration_quarters`** — what the tab WROTE. Milliseconds cannot be read back into a note value: a tempo change or a triplet makes it ambiguous, and a stem is drawn from the written value or not at all.
- **`MeasureInfo.beats` / `beat_type`** — 3/4 and 6/8 at one tempo are the same length of time and a different piece of music.

Four things were measured rather than assumed, each after the export looked fine and the times did not:

- **An empty bar must be given its length.** A bar with no notes collapsed to nothing and moved every bar after it: Bon Jovi lost **42 s** over the song and the timing test 6, with every onset count still correct. A `<rest measure="yes"/>` fixes it, and a bar rest is reading the tab, not inventing.
- **`<forward>` is not honoured in the timemap; a rest is.** A bar padded with forwards renders at the right length and reports the wrong times — a playhead that drifts while the picture looks right.
- **But a rest cannot sit where a note is still sounding**, and guitar tab overlaps constantly: a let-ring bass note under a run of eighths. That is what VOICES are for, and each onset now goes into the first voice whose cursor has reached it.
- **The bar's tempo travels with it.** Without `<sound tempo>` verovio assumes 120 BPM and every position it reports is wrong by the ratio. And it is computed from this bar's start to the NEXT bar's start, not from its own `end_ms`: the GP3-5 reader sets `end_ms` from the last BEAT in the bar, so a sparsely filled bar reads short and its tempo comes out too fast.

**And then the timemap turned out to be the wrong thing to measure against.** Chasing a tail of mistimed onsets through four wrong theories — each one measured, each one refuted — the isolation test finally said it plainly:

| one bar: quarter, quarter REST, quarter, quarter | verovio reports |
|---|---|
| as standard notation | 0, 2, 3 — correct |
| **as tablature** | **0, 3, 4** |

**On a tab staff verovio mis-times rests**, advancing two quarters for one and overflowing the bar; `<forward>` is not honoured there either. So neither mechanism for silence survives a tablature staff, and every number its timemap reports for one is unreliable. Nothing in the export was wrong: the same document engraves correctly and reports wrongly.

**That costs nothing, because the timemap was a convenience and never the authority.** The app already knows when every note sounds — from its own timeline, which is the clock everything else in this project is anchored to. What is wanted from an engraver is the PICTURE. So each exported note carries an id of ours (`n<index into timeline.notes>`), verovio puts it on the `<g>` in the SVG, and `note_positions` reads the pixel coordinates back: **1314 of 1314 and 746 of 746 notes found on the player's own two songs**. Time comes from us, position comes from verovio, and neither is asked about the other.

**Four diagnostics in a row measured accumulated drift and called it a local fault** — per-bar error, first-divergence-in-order, onset counts per bar — because every one of them compared milliseconds against a clock that had already slipped. Comparing `qstamp` (quarters from the start, tempo-free) found the real first divergence in one run. When a measurement keeps blaming whatever it looks at, it is measuring itself.

## The Zoom That Made A Bigger Picture Of The Same Thing

`ui/tab_view.py` engraves the song once per song and zoom — 0.2 s for a whole one, and **1314 of 1314 and 746 of 746 notes placed** on the player's two songs at every zoom level — and answers one question per frame: where is the playhead. Two things it has to get right, and the first was wrong until it was measured:

- **Zoom is the PAGE WIDTH, not verovio's `scale`.** Stepping the scale makes a bigger bitmap of the identical layout, and once it is blitted to the window nothing whatsoever changes on screen. Measured, the page width moves a real song from **2.1 to 10.4 bars per line**, which is what a zoom is for. The test asserts how far through the document the music reaches rather than the page count — the same song can fit on one page at two zoom levels and be laid out completely differently, which is how the first version of the test passed a broken feature.
- **Positions are fractions of the page, never pixels.** A page whose viewBox is 24000 units wide rasterises to whatever SDL felt like — 1320 px here — and a mapping that ignores that is out by a factor of eighteen while looking entirely plausible.

The playhead interpolates between the notes either side of the moment while they sit on the same line of the same page, and snaps otherwise: one sliding diagonally across a line break is worse than one that steps.

**The completion overlay takes no decision of its own.** Every caller checks whether the song is over; the page view called it as well as `_draw_hud`, which already does, so the score was drawn over the page on every frame and the whole view looked like it showed nothing but the end of the song. One call, one guard, and the docstring now says which.

**It is a MODE of the playing screen, not a screen of its own** (`Shift+T`). A second screen would be a second copy of the transport, both offsets, the loop, the tempo and the run log — and this project has already paid for four readers of one plan. `+`/`-` mean zoom there and scroll speed on the board, which is the same key meaning what the view it is pressed in is about.

**Getting it inside the frame budget took two measurements and neither suspect was the one that mattered:**

| | per frame |
|---|---|
| first version | **12.4 ms** (budget 16.7; the scrolling view costs 3.2) |
| after slicing the verdict loop to the visible notes | 12.0 |
| **after `convert()` on the page** | **4.1** |

The loop over all 1314 notes was the obvious suspect and worth **0.4 ms**. The whole cost was the BLIT: an SVG loads as a 32-bit surface with an alpha channel the display does not share, and blitting the visible band cost **8.36 ms unconverted against 0.26 converted** (the page comes from resvg now, and the arithmetic is the same). Half a frame budget spent on a pixel format. The slice stays, because it is right and because the test that pins it is cheap — but the lesson is the older one: the thing that is slow is not the thing that looks expensive.

And every page is scaled during the build, while the "engraving…" note is on screen: left until a page is first looked at, the scaling costs **50 ms at the page turn**, three dropped frames exactly where the player is reading. Two or three pages is a tenth of a second, once.

## The Page That Loaded And Was Never Drawn

"Tab View ist unsichtbar." The page was engraved, the playhead moved, the verdict dots were on screen in the right places — and between them
was nothing at all. Every check the project had passed, because every one of them asked whether the SVG existed.

**SDL's SVG loader accepts verovio's output, reports a sensible size, and draws almost nothing**: measured on a real page, **20 ink pixels out
of 1.2 million**. A real rasteriser draws the same page at **11 %** ink. The claim written down here that "pygame can load it — no extra library
needed" was made on a surface that loaded, and never on one that had anything on it. That is this project's own recurring fault, committed in
the sentence that warns about it.

- **`check_verovio.py` fails unless the SVG becomes PIXELS**, and counts them through the app's own path. A check that stops at "the file
  parsed" is the check that passed this bug.
- **It costs a few hundred ms a page**, which is why the whole build moved off the game loop.

**And the obvious rasteriser was the wrong one, which only the Windows build could say.** cairosvg shipped first, the suite was green, and the
release build failed one step later: `no library called "cairo-2" was found`. cairosvg installs perfectly happily on Windows and then finds no
cairo, because cairo is a system library nobody has — and bundling it into the EXE is the same problem one layer down. **resvg-py** is one
self-contained 1.2 MB wheel with nothing underneath it. Measured on three real songs, it draws the same page and draws it faster:

| song | cairosvg | resvg | pixels strongly different |
|---|---|---|---|
| timing test | 195 ms, 19507 ink | **140 ms**, 18467 | 0.003 % |
| canon | 566 ms, 103132 | **329 ms**, 97272 | 0.051 % |
| Demo_v5 | 844 ms, 114930 | **327 ms**, 109378 | 0.035 % |

The differences are antialiasing. **A dependency that cannot be installed on the target platform is worse than no dependency**, and the only
reason this was caught in an hour is that the release workflow renders a page and counts its ink on the machine that matters.

## verovio's Resource Path Is Thread-Local

Rasterising three pages of a real song is **3.1 s** — verovio engraves in 0.2 s and the rasteriser spends the rest — and three seconds in the game
loop is a frozen app, which this project has already shipped twice. So `_build_tab_engraving` starts a thread, `_take_tab_engraving` picks the
result up on a later frame, and the "engraving… 40 %" note moves while it happens.

**And on that thread verovio silently stopped working.** Its default resource path is set at import, in the main thread, and is **thread-local**:
on any other thread the toolkit constructs without complaint, loads the score without complaint, and renders **212 characters of empty SVG**
against 21712 with the path set. Nothing raises. The only tell is a blank page — the same failure mode as the loader above, one layer down.

- **`engrave()` sets the path itself**, every time, on whatever thread is about to build a toolkit.
- **`_engraver_state()` and `tools/check_verovio.py` now run their check ON a thread**, because that is where the app engraves. Asked on the
  main thread, both report `ready` for a build whose every page is blank — a self-check that cannot see the fault it exists to catch.
- The test that pins it engraves on a thread and requires notes on the page; it fails on the unfixed code, which is the only thing that makes
  it worth having.
- **A surface must be `convert()`ed on the thread that owns the display**, so the pages are rasterised on the worker and scaled and converted
  in `_take_tab_engraving`. Converted anywhere else, the display converts them again on every blit — the 8.36 ms against 0.26 ms above.

## Three Guitar Pro Generations, One Parser

GP6 (`.gpx`), GP7 and GP8 (`.gp`) all store the same GPIF XML and differ only in what they wrap it in: GP7 and GP8 use a zip, GP6 uses a
container of its own — BCFZ compression around a BCFS sector image. `tabs/gpx.py` unwraps that container and hands the XML to the parser that
already existed, so there is no second loader and a fix for one generation is a fix for all three.

- **`.gpx` was not even in the song list.** `GP_EXTENSIONS` had never included it, so the files never appeared to be opened in the first
  place — the format work was the second half of the problem, not the first.
- **A real GP6 file stops one byte short of the length it declares.** The decompressor must accept that rather than treat it as damage:
  refusing it rejected **13 of alphaTab's 35 GP6 test files**, and the missing byte is padding inside the last 4 KB sector that no file's
  contents reach. alphaTab swallows the same end-of-stream exception for the same reason.
- **Verified against all 35 of those files** — every one decompresses, parses and loads through `load_gp_file`, yielding 843 notes with 6
  bends, 16 slides and 54 hammer-ons. The committed tests build containers by hand instead, since the files are not ours to vendor; what they
  hold still is the bit order and the sector arithmetic.
- **Both bit orders are needed and they are not interchangeable.** The chunk headers are most-significant-bit first, the offsets and lengths
  inside them least-significant first. Getting one backwards decompresses for a while and then collapses.
- **GPIF writes a bend value of 50 per semitone** (the unit GP5 used, kept through GP8) and a bend position as a percentage. A middle point
  with no position of its own sits **halfway**, not at zero — at zero the bend scoring would ask the player to hold a pitch before the string
  has been struck.
- **`list_tracks` reads GPIF too.** Without it the track picker was empty for every GP6/7/8 file, because the caller asked the GP3-5 parser,
  the exception was swallowed, and "no tracks" looks like a song with one track rather than like a format nobody read.

## The Page Wandered A Centimetre Once A Second

"Der Screen wandert alle Sekunde rauf und runter um 1 cm. Das ist sehr irritierend." Two separate causes, and each on its own is enough.

- **A note's y on a TAB staff is the STRING it is written on.** `at_ms` returned the note's own position, so an arpeggio rotating over three
  strings moved the playhead up and down by the string spacing, and the scroll rule dutifully followed it. `TabPage.systems` groups the
  placed notes into ROWS OF MUSIC — the six string lines of one staff sit a small even distance apart and the gap to the next row is several
  times that, so the break is found from the spacing the page itself uses rather than from a constant that would need refitting at every zoom.
  `at_ms` returns the system's band now, and "the same line" means the same band, so the playhead also interpolates across a whole system
  instead of snapping at every string change.
- **And the scroll rule moved on every frame.** Keeping the playhead at a fixed height means scrolling continuously, which cannot be read at
  all. `_tab_scroll_for` HOLDS its position while the current system is fully on screen and moves only when the music has left it.
- **The playhead is drawn across the system**, the way every notation app draws it. A short tick at the note's own height jumps between the
  strings even with the scrolling held still.
- **Rounding the y values to 1e-6 put a note a hair outside its own row**, which then became a band of its own and the page jumped to it. The
  band's edges are compared against the very values that made them, so they are not rounded at all.

## The Stutter Follows The SEEK, And Our Own State Is Innocent

The player narrowed it themselves, and every clause is a measurement: *"Der Haenger kommt immer erst wenn ich gespult habe. Es ist egal ob
Midi und MP3 ein sind oder nur MP3. U aus und B aus helfen nicht. Erst App zu loest das Problem. A aendert auch nichts. Andere Songs — gleiches
Problem."*

That rules out most of what had been suspected. It is not a stuck MIDI note (it happens with MP3 alone). It is not the mixer (`Shift+A` closes
and reopens it). It is not the input stream (`A`). It is not the song. Turning both backings OFF afterwards does not clear it, so whatever a
seek does is not undone by silence.

**And our own state is innocent, measured rather than assumed.** 200 seeks through a real `PlayingScreen`, in batches, counting objects,
threads and frame time after each:

| | after 0 | 50 | 100 | 150 | 200 seeks |
|---|---|---|---|---|---|
| live objects | 40576 | 40575 | 40575 | 40575 | 40575 |
| threads | 1 | 1 | 1 | 1 | 1 |
| frame median | 1.68 ms | 1.82 | 1.78 | 1.83 | 1.83 |

Nothing grows. So the fault is in what a seek does to a DEVICE, which is the half this machine cannot exercise — there is no MP3 decoder, no
MIDI port and no real mixer here.

- **What a seek touches on the audio side is `pygame.mixer.music.play(start=)` and the MIDI port**, and nothing else.
- **The controller reset was made a PANIC-only thing.** `_all_notes_off` had grown to 48 messages per call — CC 120, CC 123 and CC 121 on all
  sixteen channels — and it is called on every seek, which a held arrow key produces 25 times a second. CC 121 also puts volume, pan and
  sustain back to their defaults, so a seek could change how the backing SOUNDS; that is wrong on its own terms, quite apart from the traffic.
  A seek now sends 32 messages and never resets a controller; `panic()` and `close()` still send everything.
- **The run log counts `seeks`**, because the variable the player identified as the trigger was the one number the log could not report.

**This is not a diagnosis and must not be written up as one.** What is established is where it is NOT.

## Shift+A Could Not Reach The Thing That Was Humming

"Audio reopened bringt nichts. Starke Störgeräusche und komisches Dauerbrummen bleibt bis ich die App schließe."

**Two things in this process make sound**, and the key that exists to make the sound sane again reached one of them. `output.reopen()` closes
and reopens the MIXER, which plays the recording. The MIDI synth, which plays the backing, is a different device on a different port — so a
synth still holding a note could be silenced by nothing short of closing the app, which is exactly what the player described.

- **`Shift+A` silences the synth first, then reopens the mixer**, and says which of the two it reached. "reopened; MIDI synth silenced" against
  "reopened; no MIDI output to silence" is the difference between two diagnoses, and a key that reports only success can settle neither.
- **CC 123 was not enough on its own.** It asks a note to RELEASE: a patch with a long tail keeps sounding, and a stuck sustain pedal keeps it
  sounding for ever. `_silence` sends **CC 120 (All Sound Off)** and **CC 121 (Reset All Controllers)** as well, on all sixteen channels.
- **`panic()` is a module function, not a method.** A player dropped without being closed still has its notes sounding and by definition nothing
  is tracking them; reaching the PORT is the whole point.
- **The run log names `midi_output`**, because a log that names only the mixer cannot say that a hum surviving `Shift+A` is not the mixer.
- **This is still not a diagnosis.** The hum has never been reproduced here. What changed is that the one key meant to fix it can now reach
  both halves, and that its message says which half it reached — so the next report is evidence rather than another round of guessing.

## The Recording Was Already There, And It Was The Chorus

"Hab ich dir nicht schon vor ein paar Tagen ein Recording von Leave a light on gegeben?" Yes — three, on 2026-08-30, and asking for another was
a failure to look. Two of them name the song in their manifest; one is 45 s of the CHORUS (song 132.5-177.2 s, 557 written notes), which is
exactly the "play-along recording of a strummed CHORUS, not another arpeggio" the chord-credit chapter above says was needed and did not exist.

**Read with the rescue funnel, it named a hole nobody was looking for.** On that take 153 strikes arrive, **92 of them subharmonic (60 %)** —
and only **28** were ever held for rescue. The other 64 sat on a written CHORD, where `_hold_for_rescue` abstains by design, and were dropped
outright.

- **"It goes where a pitchless strike goes" was implemented for one of the two places it goes.** A pitchless strike is offered to
  `_unpitched_chord_credit` FIRST and only held for the audio when no chord explains it. The subharmonic path skipped straight to the hold, so
  on chord-written material — which is most of a rock song — it did nothing at all.
- **A subharmonic is if anything STRONGER evidence of a strum than silence is.** It exists only because several strings are sounding together
  and share a period, which is the very thing being credited. The matched-subharmonic path has said so since it was written ("the strum itself
  is proven"); the unmatched one now says it too.
- **Measured on the chorus take: 66.4 % → 69.5 %** (370 → 387 of 557), and the funnel goes 9 held / 3 confirmed → 28 held / 9 confirmed.
- **All three controls hold.** `check_chord_credit.py`: every correct chord still credited in full, every deliberate one-fret error still
  caught. `check_ringing_rescue.py`: the damped takes gain **+0**. `check_subharmonic_rescue.py`: no take loses a note, and one timing-test
  take gains two.

**And a second finding that is NOT fixed, written down so it is not rediscovered.** The 20-second take produced **one verification window for
177 strikes**. It runs at 9 strikes a second with a median gap of 107 ms, and `_limit_pending_windows` drops any window trimmed under
`MIN_WINDOW_MS`; only 1 % of its gaps clear 255 ms. So on dense strumming the chord verifier and the rescue are both inert — not wrong, absent.
Both takes also show a hard floor of 64 ms in their gap distribution, which is a re-trigger and not a pick. Whether that is worth a separate
onset rule is unmeasured; what is measured is that the evidence never arrives.

## Four Tools Batched What The App Interleaves

Asked to improve the arpeggio, the first measurement was of the measuring instrument, and it was wrong by a third.

The app drains both audio queues **every frame**. All four check tools pushed a whole take through `_audio_callback`, collected the strikes and
the windows into two lists, and then handed the matcher every strike followed by every window. Two bounded structures make that a different
program, and both drop the OLDEST entry: `AudioCapture.strike_queue` holds 16, and `NoteMatcher._pending_rescues` holds 32. So a 134-strike
take arrives with 70 holds and only the last 32 can still be answered.

| the same events, the same code, only the ORDER | notes credited |
|---|---|
| batched (what the tools did) | **46.0 %** |
| interleaved (what the app does) | **61.5 %** |

**The app was never at 46 %.** Every arpeggio figure written down before this — the 43 %, the 49 %, the "8 held / 3 confirmed" funnels — was
measured through the batched version and understates the rescue. `check_ringing_rescue.py`'s fast ringing take is 12/14, not the 10/14 in the
chapter above.

- **`tools/take_harness.py` is the one implementation**, returning `[("strike", …), ("window", …)]` in arrival order. Four copies of one
  capture loop is the "four readers of one plan" fault this project has already paid for at the repeats and at the transpose.
- **This is the fifth time a tool measured itself**, after `analyze_ringing.py`, `check_ringing_rescue.py`, the four drift diagnostics and the
  `MIN_WINDOW_MS` sweep that gated on the value it was testing. The tell each time was a control that came back wrong; here it was a funnel
  reporting 42 holds that were never asked about on a take whose gaps clear the window floor 83 % of the time.

## Acquitting Is Not Convicting, And They Had One Threshold

With the harness honest, the arpeggio's remaining loss is the verifier: 15 refusals, and instrumenting every one of them says why.

| why `confirms` said no | how many |
|---|---|
| **the written note WON and lost on the margin** | **10** |
| a different note won outright | 4 |
| the written note was masked and unscorable | 1 |

In those ten the written note is the strongest hypothesis in the window — −1.4, −6.0, −6.2, −6.3, −7.9, −8.3, −9.9, −11.8, −13.9, −18.0 dB —
and is refused because a rival **one or two semitones away** scores within 8 dB of it.

`MARGIN_DB` is 8 because at 5 dB `verify` mis-called a correctly played low E. But **`verify` and `confirms` ask different questions**, which
this file already said in as many words and the code did not: `verify` must CHOOSE which note a string played and can convict, so it must not
be talked into the wrong one; `confirms` only asks whether the written note is present and can never do anything but acquit. Borrowing the
conviction threshold for the acquittal is the same class of mistake as `MIN_WINDOW_MS` being fitted against a floor that had since moved.

- **`CONFIRM_MARGIN_DB` is 2.0, and it is fitted.** The two populations separate cleanly: over the arpeggio take the 54 rescues where the
  written note wins have a worst margin of **2.2 dB** (10th percentile 6.2, median 12.6), and over the DAMPED control takes — where a
  confirmation is by definition a note being invented, since nothing was left ringing — exactly one candidate wins, at **1.2 dB**. The window
  is 1.2 to 2.2.
- **The value sits at the top of that window because the two mistakes are not equal.** Refusing a real rescue costs one note of credit;
  accepting a false one turns a wrong note green, which is the thing the player has already said the score does too much of.
- **Everything else is untouched.** `present_db` still governs whether the note is loud enough to be there at all, and a rival that really wins
  is still refused — the rule only ever acquits.
- **Measured on the arpeggio: 61.5 % → 67.1 %** (99 → 108 of 161), rescues 44 → 54. The strummed chorus take goes 388 → 396. Every control
  holds at the shipped value: the damped takes gain **+0**, all **7/7** deliberate one-fret errors are still caught, the palm-muted wrong take
  stays at **0/57** green, and no play-along take loses a note.

**What is left is not detection.** Of the 53 notes still missing on that take, **23 have a strike in the window that was credited to a
NEIGHBOURING note** — and the tab writes 145 onset moments where 132 strikes were heard, so most of those are picks that were not played rather
than picks that were not read. Crediting them would be crediting notes on no evidence. Nine are subharmonic strikes the verifier still refuses
and eight have no strike within 650 ms.

## Counting The Rescues That Did Not Happen

A run of an acoustic arpeggio scored 24 %, and answering "why" meant reconstructing the funnel by hand out of the strike table: 95 strikes,
**53 of them subharmonic (56 %)**, 12 rescued. That is the arpeggio case this file already has a chapter on, and the numbers are in line with
it — but the interesting question, which of the two ways a rescue is lost, could not be read at all.

- **Held but never asked** means the audio window never arrived, because the next strike came too soon and `_limit_pending_windows` trimmed it
  away. **Asked but refused** means the verifier could not find the written note in the sound. They are fixed in completely different places.
- The run log carries `rescue_held`, `rescue_no_window`, `rescue_asked`, `rescue_already_credited` and `rescue_refused` now.
- **The first guess was wrong and the measurement said so.** Gaps between strikes on that take: 5th percentile 174 ms, median 314 ms, and
  **91 % of windows clear the 200 ms floor**. So the windows were arriving and the loss is in the verifier, which is where the next attempt
  belongs.

## The Rhythm Cannot Place A Take Of A Song That Repeats Itself

`best_offset_at` scored an alignment on TIMES alone, and the docstring said why in as many words: fitting on pitch would assume the answer to the
question being asked. That is right about the danger and wrong about the alternative, and Kid Rock's "Rock On" settles it. Its verse repeats one
rhythmic figure, so on a 45-second take of it the rhythm does not merely tie — **it actively prefers the wrong bars**. The true offset ranks
**93rd** by strikes-on-the-grid, 48 against the winner's 66; its pitch agreement is **61 of 72 against 12**. Read where the times point, the take
scores **30 %** of its written notes; read where the pitches point, **86 %**.

- **The candidates come from both histograms** — where strikes pile up on written onsets, and where they pile up on onsets whose pitch they
  carry. Without the second the true place is never even a candidate.
- **The times still answer; the pitches may only OVERRULE them**, and only by `ALIGN_PITCH_DOUBT` (3.0). Over the eight play-along takes the
  ratio is 1.00-1.05 where the two agree, 1.83-1.93 where the pitch answer is WORSE (and once 0.51), and 4.51 on the take the times cannot
  place. The window is 1.93 to 4.51. Same shape as `TEMPO_DOUBT_RATIO`, and for the same reason: a criterion that is usually right must not be
  replaced by one that is occasionally better.
- **The evidence is an F-measure, not a hit count.** Counting only the strikes that find a note of their own pitch is free in a dense passage:
  a chorus writing six strings a beat has some note of every pitch class at nearly every moment. Measured, that moved the arpeggio take from its
  true place at song −1 s into a chord section at 125 s, where 615 notes sit under its 134 strikes. Both directions — what share of the strikes
  landed on a note of their pitch, and what share of the notes written in the covered stretch got such a strike.
- **A subharmonic is not evidence about a note.** It names the chord sounding in the room, which is the same reason the matcher never scores
  one. Counted in, 62 % of the arpeggio take's strikes voted for that dense chorus; counted out, the true place wins two to one.
- **Every other take is unmoved**, which is what makes the change safe: all seven earlier play-along takes align exactly where they did.

## A Song Is As Long As It Is WRITTEN, Not As Long As Its Notes

"Whats up ist am Ende nicht mehr sync. Stimmt die Songlaenge nicht zum Tab?" The right question, and the answer is that the FILES agree and
the app did not:

| | |
|---|---|
| tab, 80 bars at a constant 3.69 s | **295.4 s** |
| recording | 292.5 s, music from 2.3 s to 291.0 s |
| what the app called the song's length | **243.5 s** |

`Timeline.duration_ms` was the end of the last NOTE. This tab's guitar sits out the outro, so fourteen written bars carry nothing — and the
app then agreed the song was over **fifty-two seconds before the music was**. The picture stops, the recording plays on, and from the inside
that is indistinguishable from a sync fault. It is the length of the written piece now, or the last note where that runs past the final bar
line (a let-ring note may, and a song is not over while something is still sounding).

Measured across the songs to hand: "What's Up" **+51.9 s**, Kid Rock +1.6 s, the other two exactly nothing. So it is rare and it is total when
it happens.

**And the auto-sync still cannot place this song**, which is the other half and is not fixed by this: 5 of 41 windows readable, and the
unreadable ones cluster at exactly ±4 and ±8 bars — 14.8 s and 29.5 s — because the verse is one four-chord loop. See the chapter below.

## A Song With Four Chords And Nothing Else

"Whats up ist am Ende nicht mehr sync." The run log named it and the offline measurement confirmed it: `mp3_sync_points 4`, the last at **178 s
of a 243 s song**, and sections reading `+0.80% +2.56% +3.51%`.

Measured without the app (`check_song_sync.py`): **5 of 41 windows readable**, all of them before 2:48, at a fitted drift of **−3.0 %** where a
real mismatch is about 1. The song is a four-chord loop repeating every eight bars, so the chroma matches equally well fourteen, twenty-eight and
forty-two seconds away — the lag table is a staircase of exactly those. This is the hardest possible case for auto-sync and it is not going to be
solved by a threshold.

- **What the app can honestly do is say where its points stop.** `mp3_sync_covers` in the run log: the span the points span, and it as a share of
  the song. Beyond the outermost point the map extrapolates a slope fitted on whatever was readable, and "synced at the start, apart at the end"
  is exactly what that looks like from the inside. The count of points alone cannot say it.
- **`MAX_DRIFT_RATE` was NOT lowered.** 3 % is inside it and wrong, which is an argument for tightening — and one counter-example is not a
  calibration. The Godsmack chapter set 5 % deliberately generous, and moving it on one song would be fitting to that song.
- **The fix for a song like this is a hand-placed point near the end** (`Shift+S`), which is what every other tool asks for too.

## Is It The Files Or The App? Answer That First

"The picture and the backing drift apart" has three causes and they are fixed in three different places: the tab is wrong, the recording is a
different arrangement, or the app's playback loses time. `tools/check_song_sync.py` settles the first two **without the app**, so the third is
only ever suspected once the other two are ruled out.

On the song that prompted it (Bon Jovi, "I'd Die For You", a Songsterr download against an MP3 of the video):

| | |
|---|---|
| tab | 147 bars, every one explicitly 4/4, **one** tempo automation of 132 BPM, no repeats, no jumps, no fermatas |
| tab length | 267.3 s — exactly what the app shows |
| recording | 270.6 s, **132.51 BPM** measured, CBR 192 kbps with an Info header |
| tab against recording | **-1.08 %, i.e. 2.6 s over four minutes** |

So the files agree and the 21 s the player sees is made in the app. Three things that measurement had to survive first:

- **Beat tracking on a full band mix is not a measurement.** aubio's tempo gave 136 BPM, a phase fit 137.8, an onset cross-correlation 133.1 —
  three answers on one file, none reproducible. Autocorrelating the onset envelope gives 132.51 and the same value in every 40-second slice.
- **Onsets do not survive a dense mix; CHROMA does.** Matching note attacks put 133 of 343 strikes on the grid and the best offset jumped
  between -39 s and +37 s at constant confidence. Comparing pitch-class energy instead gives a smooth curve with residuals under 0.4 s.
- **A pop song rhymes with itself, so a lag search always finds something.** Three of seventeen windows matched the wrong chorus, 14 to 28 s
  away. The first version gated them out by a confidence threshold — which had to be fitted per song and was therefore measuring the song. It
  fits a robust line instead (median over pairwise slopes) and prints the residual per window: the outliers are named, counted, and cannot move
  the answer. **Nothing pretends to identify an outlier in advance.**

`mp3_worst_drift_ms` is updated on every frame, not only at a correction, so a run log can carry the answer for the remaining case.

## The Offset Says Where It Starts, Not How Fast It Runs

A recording gets a per-song offset, and an offset is a constant: it can put the first bar in the right place and nothing else. When the tab and the recording run at different speeds — 1.09 % on the song this was built for, 2.7 s over four minutes — the offset that is right at the start is wrong by two and a half seconds at the end, and there is no value that is right at both.

**Shift+S takes the two.** Line the recording up near the start, line it up near the end, and the line between them is the speed. From offsets `O1` at song `S1` and `O2` at `S2`, `rate = rate_old x (1 - (O2-O1)/(S2-S1))`, and the offset is rewritten so the first point the player demonstrated does not move.

- **It goes into the LENGTH of the built copy, never into `time_scale`.** The scale is what makes one real second advance the song by `tempo` seconds — put a correction there and the notes scroll at the wrong speed, which is the one thing this must not do. `_mp3_build_tempo()` is `tempo x rate`, so the practice speed and the correction both land in `timestretch.build` and nothing else changes.
- **Measured end to end on the player's own files**, tab against recording by chroma:

| | Wanderung | gesamt | groesster Rest |
|---|---|---|---|
| original | **-10.85 ms/s** | **-2.67 s** | 423 ms |
| after the correction | **+0.00 ms/s** | **+0.00 s** | 464 ms |

  The systematic walk is gone completely; what is left is the band's own tempo moving, which nothing can follow. **A repair, not a cure** — and the HUD and this file both say so rather than promising sync.
- **`_mp3_source_fits` could not see it.** A rate correction changes the file while leaving `time_scale` exactly where it was, so the old check reported a fit and the copy was never built. `_mp3_loaded_build` records what the loaded source was made for; where it is unknown (a source this screen did not load) the scale still answers, but only while no correction is wanted.
- **And the stretch cache would have served the wrong file.** `cache_name` put the tempo in the readable part rounded to whole percent and hashed only the path — fine while the speed moved in 5 % steps, silently wrong the moment corrections move in fractions of one: 0.9891 and 0.9932 both read `099` and shared a file, so a second attempt at syncing a recording would have played the first attempt's copy. The tempo is in the hash now.
- **Two points closer than `MIN_SYNC_SPAN_MS` (30 s) are refused**, and the first point is KEPT — the offset moves in 10 ms steps, so over five seconds one keypress is 0.2 %, a fifth of the whole effect invented by the last key pressed. A rate outside 0.9-1.1 is refused outright and named: a real mismatch is about a percent, and playing a song at 80 % of its speed is indistinguishable from a broken recording.
- **Below 0.1 % nothing is built.** That is the threshold `stretch` itself gives up at, so a build would return the audio unchanged — five seconds of work bought with nothing.

## Two Clocks, And The Recording Is The One That Cannot Bend

"Trotzdem sind die Aufnahmen in anderen Tools wie Songsterr oder GoPlayAlong perfekt gesynct. Meine Annahme: Es liegt bei uns." That is a
proof, and it is right. The chapter above measured the drift correctly and then drew a conclusion the measurement does not support: **that no
cure exists.** A varying rate has no single correction — but it has a piecewise one, and every tool that solves this problem uses exactly that.

- **Go PlayAlong**: sync mode drags beats onto the audio; *"For most songs, 2–5 sync points are usually enough"*; auto-sync fills in the beats
  between the points the player set. The audio is never touched.
- **alphaTab**: a sync point is `(barIndex, occurence, ratioPosition, millisecondOffset)` — a place in the score and the millisecond in the
  media where it happens.
- **Guitar Pro 8**: the audio track has anchors set by double-clicking above the waveform, stored in the file.

All three warp the SCORE onto the recording. None stretches the audio. Integrated over the drift curve measured on the player's own song, the
worst error over four minutes:

| | worst error |
|---|---|
| one offset (the offset keys alone) | **1313 ms** |
| one offset and one rate (what shipped) | 241 ms |
| 3 sync points | 107 ms |
| **5 sync points** | **90 ms** |
| 9 sync points | 27 ms |
| 17 sync points | 7 ms |

Five points cross the 100 ms where picture and sound stop reading as one event. That is the whole design: nothing is modelled, every point is
a piece of the truth, and the line between two of them is the least that can be claimed.

- **The correction is applied to the TAB, never to the recording.** A recording's clock is in the sound card and can only be bent by seeking,
  and a seek is audible: following a 1 % warp that way means breaking the sound every five seconds for the whole song. The picture can be
  pulled by a fraction of a millisecond a frame and nobody sees it. **Correct the cheap side** — and that inverts what this app did, which was
  to seek the recording whenever it drifted 90 ms from a tab that was itself wrong.
- **So while the recording sounds, IT keeps time** (`_mp3_leads`) and `_follow_recording` pulls the song clock towards `song_at(recording)`.
  The pull is limited to `SYNC_PULL_FRACTION` (5 %) of the time that really passed, which is five times the authority needed to track a 1 %
  mismatch and far too slow to see; past `SYNC_SNAP_MS` (1.5 s) it jumps, because that is not drift but a seek or a loop turn.
  `Mp3Player.update(correct=False)` says the recording is not to be touched, and the pull carries the audio anchor and
  `matcher.audio_offset_ms` with it — moving song time without them would put every strike out by the whole correction, 2.6 s by the end of
  that song.
- **The stretched copy is for the practice speed and nothing else now.** The sync rate used to be multiplied into `_mp3_build_tempo`, which
  rebuilt the whole file for one percent — seconds of work, silence until it landed, a cache entry per attempt — to apply ONE rate to a
  recording whose rate varies by a factor of three. Warping the tab does it for free, and leaving it in the file as well would apply it twice.
- **`SyncMap` is exact in both directions** and the test asserts the round trip: the recording is a piecewise-linear function of song time, so
  the inverse is piecewise linear over the same points. A segment's slope is bounded to `MIN_RATE`/`MAX_RATE`, so two points set close
  together cannot imply a rate that runs the rest of the song away.
- **Outside the outermost points it extrapolates** rather than holding a value: a recording drifting at the last point is still drifting after
  it, and the slope is bounded so it cannot escape. A point near the end is still worth more than any extrapolation, which is what every tool
  that does this tells its users.
- **The run log carries `mp3_sync_points`, `mp3_sync_sections`, `mp3_worst_pull_ms` and `mp3_leads`.** A large pull with few points says where
  the next point belongs; `mp3_leads no` says the map was never in play at all, which is a different fault from a map that is wrong.

## The Panel Grew Downwards Into The Footer, And Vanished On Reopening

Two faults in the sync panel, and the second is this project's oldest one.

- **It was placed at a fixed height and grew DOWNWARD.** Three lines reached the keyboard shortcuts and drew over them. The footer is drawn
  FIRST now and reports the y it starts at; everything at the bottom of the screen stacks upward from there, so a fourth line pushes the block
  up instead of into the footer.
- **Seventeen points do not fit across a window.** A line drawn wider than the screen is centred, so BOTH ends are cut — the first point and
  the last, which are the two that matter most. `_fit_line` shrinks the MIDDLE away until it fits.
- **A song opened with points already measured showed nothing.** They were stored and used all along; the panel simply started empty every
  session, so nothing on screen could tell a synced song from one nobody had touched — the most expensive setting in the app, invisible. The
  panel is rebuilt on load when the song has anchors, and stays silent when it has none.

## The Score Was Drawn Through The Section It Names

Two lines of the completion overlay were placed at fixed offsets — "New Best!" at +132 in the 28 px font, the weakest section at +140 in the
18 px one — so a run that was both a personal best AND had a weak section drew one through the other, by **26 px**. Each line had been laid out
on the assumption that the other was absent.

Every line is stacked on the measured height of the one above it now, and the block is centred as a whole, so a run with three recommendations
and one with none are both readable instead of one of them hanging off the bottom edge. Same rule as the footer and the sync panel, which have
each been fixed for this once already. The test asserts the PROPERTY — no two lines overlap, in the case where every one of them is present —
rather than any particular position.

## A Run Log That Says The App Is Innocent

The player's log after a "Tonabsturz", with 31 seeks in it:

| | |
|---|---|
| `frame_ms_median` / `frames_over_budget_percent` | 7.7 ms / **2 %** |
| `clock_stalls` / `clock_ratio` | **0** / **1.0000** |
| `mp3_resyncs` / `mp3_worst_seek_ms` | **0** / **0** |
| `dropped_buffers` | **9** |

**The picture kept perfect time and the recording was never re-seeked once.** So the two mechanisms this app has for making sound go wrong
were both idle while the sound went wrong. What did happen is on the INPUT side: nine dropped buffers, against zero in every earlier log, and
one frame of 146 ms.

**And `hits 0` in that log is not a detection failure — it is a seek.** The strike table is empty and every reached note reads `miss` because
`seek()` calls `matcher.reset()`, which clears the trace and puts every note back to PENDING; the sweep then marks everything behind the
playhead as missed. A log taken straight after spooling describes what happened since the last seek and nothing before it. `seeks` is in the
header for exactly this reason: without it, that log looks like the detector died.

## A Song That Rhymes With Itself Beat The Median

"Bei diesem Song habe ich 3x gesehen, wie das Bild links/rechts springt." Godsmack's "Awake", and the run log named it in one line:
`mp3_worst_pull_ms 4759` against a `SYNC_SNAP_MS` of 1500 — the picture did not drift, it JUMPED, three times, because the sync map it was
following was wrong.

**The map was wrong in a way the outlier filter was built to miss.** The nineteen stored points walked from **-10.2 s to +17.9 s** over five
minutes, in plateaus about 8 s apart — a metal song whose riffs recur, so window after window matched the WRONG repeat. Re-measured here from
the player's own files: 47 windows, lags stepping from +10.9 s down to -17.9 s.

A straight line fits that staircase at **-12 %** almost perfectly. So the robust line found nothing to call an outlier and kept all of it, and
`SyncMap` then spread the nonsense across the song at its own clamp — `+11.11 %` appears four times in that log, which is `MAX_RATE` exactly.

- **A median is robust to a MINORITY of wrong readings; it is not robust to a majority.** Whole clusters, each consistent with the next, are a
  majority. Two things fix it and both are needed: the fitted slope is bounded to `MAX_DRIFT_RATE` (5 %, five times the ~1 % a real mismatch
  measures), and the OFFSET is chosen by consensus — the line the most windows sit within `OUTLIER_S` of — rather than by the median, which
  lands between two answers and belongs to neither.
- **Pairs closer than `MIN_SLOPE_SPAN_S` (30 s) are left out of the slope entirely.** The offset moves in fractions of a second and the noise
  is comparable, so a pair five seconds apart implies any rate at all.
- **Measured on the player's own files: 19 points spanning 28 seconds become 5 points spanning 1.2 s**, at a fitted drift of +1.9 %, with
  11 of 47 windows usable. That song repeats too much for the rest, and saying "11 of 47" is the honest answer.
- **The panel now says what the points COVER** (`measured 0:10–1:15 of 4:58`). Beyond the outermost point the map extrapolates, and a song
  whose points all sit in the first minute is a song whose last four are a guess.
- **`mp3_snaps` is in the run log.** A pull nobody can see is the design; a jump is a fault, and without a count it is only ever a report.

## Three Seconds Wide, And Blind To A Spike Of One

"Like a villain synced falsch." The run log had the whole answer in two lines. Twelve stored points, and the sections between them read
`-6.83% +11.11% +0.66% -0.15% +10.29% -3.14% +0.45% +2.93% -0.24% +0.53% -0.05%` — one of them clamped at `MAX_RATE` exactly.

Fitted, the song's real drift is **+0.17 %, smooth, and nine of the twelve points sit within 52 ms of it** (MAD 44 ms). The other three are
spikes:

| at | lag | off the line | times the scatter |
|---|---|---|---|
| 28 s | -1767 ms | **-1349 ms** | 31x |
| 70 s | +215 ms | **+561 ms** | 13x |
| 118 s | -89 ms | +175 ms | 4x |

Every one of them was kept, because `OUTLIER_S` is **3.0 s — sixty-eight times the scatter this song shows.** Each poisons the two sections
around it. Dropping the three takes the map's worst error from **1352 ms to 52 ms**.

- **The fixed threshold was fitted against the wrong case, and correctly so.** It exists to reject a window that matched the wrong chorus,
  which lands 14 to 28 s away — so it has to be seconds wide, and is then blind to everything smaller. A threshold set for the worst thing it
  must catch cannot also be the threshold for the commonest.
- **The scatter the song shows is the only thing that says what a disagreement is.** `spike_tolerance` is `3 x MAD` of the residuals, bounded
  below by 100 ms (where picture and sound stop reading as one event, so a reading nobody could see is never rejected) and above by the old
  `OUTLIER_S` (so a genuinely scattered song cannot grow its tolerance until a wrong chorus fits inside it). The factor has to keep 52 ms and
  drop 175 ms, so anything from 1.2 to 4.0 works and 3.0 is the middle of what was measured.
- **It is one filter improved, not a second one added.** A local rate bound between adjacent stored points would catch the same three spikes
  and would be two answers to one question — the reason `onset_min_interval_ms` lasted an hour.
- **All the controls hold**: the zero-drift synthetic control loses nothing (its scatter is 9 ms, so the floor governs and the tolerance never
  binds), the Godsmack staircase still collapses to one plateau, and a real 1 % drift is still followed to within 0.2 %.
- **A map measured before this is still wrong**, because the points are stored. `Ctrl+S` re-measures.

## One Stretch For The Speed And The Pitch, Not One Each

Playing a Drop C song on a Drop D guitar shifts the recording, and `build` did that as its own pass before stretching for the practice speed —
so a slowed-down transposed song went through WSOLA twice. It does not have to: a pitch shift IS a stretch by the pitch ratio read back at that
ratio, and reading back is uniform, so it leaves every sample's position as a FRACTION of the file exactly where it was and the rate curve
composes with it unchanged. One stretch by `ratio / tempo_factor`, then one resample by `ratio`.

| four minutes of audio, +2 semitones | before | after |
|---|---|---|
| 100 % speed | 7.9 s | 7.8 s |
| **80 % speed** | **14.1 s** | **11.0 s** |
| 70 % speed | 16.6 s | 13.6 s |

**At full speed it saves nothing, and that is the honest half.** There was only ever one stretch there; the eight seconds is the pitch shift
itself, and 56 % of it is the FFT cross-correlation at the heart of WSOLA. Searching a downsampled signal would cut that and would change which
offsets are chosen — measurable only by ear, on a build nobody here can listen to, and the player's verdict on the current one is "klingt noch
gut". Not built.

Verified exact rather than assumed: length lands within **1.0000x** of what the speed alone would give at 100, 80 and 70 %, and the pitch
within **0.1 cents** of what was asked for. The length matters as much as the pitch — an unchanged one means every sync point and every offset
still describes the file.

## A Key That Walks A List Nobody Can See

"Das Blättern mit R und Sh+R ist merkwürdig. Kannst du mir anzeigen, was als nächstes kommt." Both halves were real, and the second explains
the first.

A Drop C song reaches six tunings — Drop A, A#, B, C, C#, D — ordered by pitch. `R` stepped through them **and wrapped**, so one press at the
top jumped five semitones to the bottom: a whole recording rebuilt, seconds of silence, for a tuning nobody asked for.

- **It does not wrap.** `R` means higher and `Shift+R` means lower, all the way, and an end of the list is a sentence — the same rule as the
  zoom key and the scroll-speed floor.
- **The HUD names the next one in each direction** (`R -> Drop C#    Shift+R -> Drop B`), with an em dash where the list ends. A key you press
  to find out where it went is bad enough; this one reloads the song and rebuilds the stretched recording, so finding out costs seconds.
- **One helper answers both**, so the line cannot advertise a tuning the key refuses — the same property `K` and its HUD line are held to, and
  the test asserts it over every position in the list rather than asserting the wording.

## Three Ways A Keyboard Can Say Shift, And Only One Was Asked

`Shift+U` opens the tuner from the song list with the search box open, and it did not work on the player's machine: the key arrived carrying a
capital **"U"** with **no shift bit in `event.mod` at all**, so the guard fell through and the letter went into the search.

Three signals now, and any of them is the request: the event's own modifiers (the normal answer), the live keyboard state
(`pygame.key.get_mods()`, which catches a stale `event.mod`), and the CHARACTER — a capital U is what was typed, however the layout produced
it. With caps lock on the two swap over, which is the price and is small: the letter is still typeable with shift held.

`pygame.key.get_mods()` needs the video system and raises without it, so it is wrapped. **A key handler that can raise takes the app down with
it**, and it is asked on every keystroke in the song list.

## Which Build Is This? Nothing Could Say

Three fixes in a row came back as "does nothing" — `Shift+U` in the search box, `Shift+C` in the song, and the footer that would not wrap. All
three were in the tree, under test, and green. All three were an older EXE, and each one cost a round trip to establish, because **nothing in the
app could say which version was running**: "is it fixed" and "did it reach the machine" were the same question with no way to tell them apart.

- **`build.bat` stamps the build** with the short commit and the time, `pickhero.spec` carries the file into the EXE, and `build_info.py` reads
  it back. Running from a checkout there is no stamp, so the git HEAD is read STRAIGHT OFF THE FILESYSTEM — no subprocess, because this is asked
  while the window is coming up and a build stamp must never be why the app is slow to start or fails to start.
- **It is in the run log (`build`) and bottom-right in the song list.** The log is what gets sent; the list is what the player can read without
  playing anything.
- **Neither answer is a crash.** No stamp and no git gives "unknown build", which is itself information.

The stamp is generated, so it is gitignored: it describes one machine's build and never belongs in the tree.

## The Footer Was Wider Than The Screen, So Both Ends Were Gone

Twenty-three keyboard shortcuts are **2986 px** of text and the player's window is **1911**. `_blit_footer_lines` shrank the font until the
widest line fitted -- and when even the smallest still overflowed by a thousand pixels it drew it anyway, centred, which cuts BOTH ends. On the
player's screenshot the first entry and the last are simply not there, which is why a newly added key looked like a key that had never shipped.

It wraps at the `|` the entries already carry, so a shortcut is never broken across two lines and nothing is lost. A single entry wider than the
screen is left alone: shortening the text is a decision for whoever wrote it.

## One Helper For Every Way A Keyboard Says Shift

`Shift+U` needed three signals to work on the player's machine -- the event's modifiers, the live keyboard state, and the character -- because
the key arrived carrying a capital letter with **no shift bit in `event.mod` at all**. That was written up as a fix for one key, and it was not:
`Shift+C` then fell through to the plain `C` that raises the noise gate, for exactly the same reason, and every other shifted shortcut in the
playing screen was one report away from the same thing.

`shift_held(event)` is the one implementation now, used by all eleven of them and by the song list. **The class of fault is closed rather than
the two instances of it** -- which is what "one plan, four readers" means when the readers are keyboard shortcuts.

## The Diary Was Right And The Page Was Yesterday

"Wie lange habe ich heute gespielt? Davor waren es nur unter 3 min." Nothing was lost: the dashboard was rebuilt only when the APP closed, so
it described the state at the last close. Twelve sittings totalling 10.1 minutes read as three.

Leaving a song is where that question gets asked and is also where the sitting is written (`stop_audio` -> `close_session`), so the page is
rebuilt there too — 1.5 ms at a hundred sittings, on a frame that is tearing a screen down anyway, and after the write for the same reason the
one on the way out is. **A number that is right in the file and stale on the screen is indistinguishable from a diary that loses sittings.**

## Sync Points Belong To A RECORDING, Not To A Song

Seventeen points measured by ear or by Ctrl+S are the most expensive thing in a song's settings, and two ways of losing them were open.

- **Picking a different recording kept them.** Another rip has another intro and another encoder padding, so points measured against the old
  file put the new one out by seconds — while the panel still reads "17 points" and everything looks synced. Choosing a file at a DIFFERENT
  path now drops the points, the rate and the offset and says so; re-picking the same file after moving it keeps the work, because that is not
  a different recording.
- **`merge_stats.py` did not carry them.** A song measured on one machine had to be measured all over again on the other, which is the
  "a setting that moves house has to be followed into every reader" fault for the second time. `song_mp3_anchors` and `song_mp3_rates` travel
  now, under the same rule as the rest: an entry the receiving machine already has always wins.
- **Where they live:** `~/.pickhero/settings.json`, under `song_mp3_anchors`, keyed by the tab file's name without its extension. Written the
  moment a point is set, so nothing has to be saved by hand — and renaming the tab file starts the song over, which is what that key means.

## Sync Points Only Arrive If The Recording Does

`merge_stats.py` carries a song's sync points to the second machine, and they landed there useless: `song_mp3_paths` stores the ABSOLUTE path the
file chooser returned, so a settings file written on one computer points at a folder the other does not have. The app then reports the recording
as moved, and the most expensive setting in it sits beside a backing track that will not play.

- **A stored path that no longer exists falls back to a file of the same NAME in the songs folder.** Same name, same recording -- which is the
  rule the anchors already follow, since re-picking a file after moving it keeps the work. Nothing is dropped and nothing is written back: each
  machine keeps the path it was given, which is what lets the merge stay one-way and idempotent.
- **The name is split on both separators.** A backslash is not a separator on POSIX, so `Path(r"C:\x\take.mp3").name` is the whole string there
  -- and a settings file that travels between machines is the entire reason a name is being looked up at all.
- **A recording that is nowhere still reports its stored path**, so the app can name the file it cannot find. A path silently emptied is a
  backing track that vanished without a word.

## Five Points By Hand Before Every Song Is Not A Feature

Sync points work and the other tools ask for them, but placing five of them by ear before every song is a chore that will not be done twice.
The measurement that finds them already existed here as a diagnostic — `check_song_sync.py` compares the tab's pitch classes against the
recording's, window by window — so `audio/autosync.py` is the same measurement with its answer handed to the map instead of printed. **Ctrl+S**
runs it; `tools/check_song_sync.py --write-sync` does the same from the command line. It is one implementation, imported by both: two copies of
this would be two answers to one question.

- **The points that come out are all MEASURED.** The curve is thinned by Douglas-Peucker to the fewest windows whose straight lines still
  reproduce it to `SIMPLIFY_MS` (25 ms), so what is stored is a subset of what was read rather than a model of it. Measured on a synthesised
  recording with a known drift: 30 points at a 5 ms tolerance and 8 points at 25 ms give **the same worst error**, so the thinning costs
  nothing and the tolerance is not a knob anyone needs to turn.
- **The control that must read zero is what found the real bug.** A recording sitting exactly on the tab's grid came back saying **+50 ms with
  a scatter of only 10 ms** — a shift, not noise, and half the budget of the 100 ms this whole feature exists to get under. An analysis frame
  covers 8192 samples and was indexed by its FIRST one, so the recording's chroma reported everything half a frame early while the tab's
  chroma, built straight from note times, had no window at all. The two sides now describe the same stretch of time: recording frames are
  indexed by their centre, and the tab's chroma is smeared over the same span. The residual is **−25 ms**, stable across wildly different note
  envelopes, and it is left alone rather than tuned away — tuning it would be fitting to the synthesiser.
- **The lag resolution was coarser than the target.** The search stepped two analysis frames, which is 93 ms at 44.1 kHz and 186 ms on a
  stretched copy; measured, that quantisation alone left a **228 ms staircase**. Stepping one frame and finding the peak between samples with a
  parabola through three scores brings the worst error to **98 ms** and the scatter to ±9 ms.
- **End to end on that control: 710 ms of drift becomes 130 ms**, and 104 ms once the constant is nudged out with `Shift+N/M`. That is a
  synthetic control and must not be quoted as accuracy on real music — what it establishes is that the arithmetic recovers a drift it was never
  told about. The real number has to come from the player's own files, and `check_song_sync.py` prints the residual per window.
- **Two filters, answering different questions.** A window whose best lag beats its nearest rival by less than `MIN_MARGIN` could not tell one
  chorus from another; a window sitting further than `OUTLIER_S` from the robust line disagrees with all the others. Neither is a threshold on
  the correlation itself, which would have to be fitted per song and would then be measuring the song.
- **All the pitched tracks, not the one being practised.** A recording is the whole band, and matching a single guitar line against it throws
  away most of the evidence. Percussion is left out: a drum kit has no pitch classes, only noise across all twelve.
- **It runs on a thread with a percentage on screen**, because a four-minute song is a couple of seconds of arithmetic and seconds in the game
  loop is a frozen app — the bill this project has now paid at seeks, at the pause, at the instrument change and at the tab view.
- **"Could not read it" and "needs no correction" are different answers**, so the panel prints how many windows were usable next to how many
  there were. A feature that stays silent on failure is indistinguishable from one that does not work.

## The Band Did Not Play To A Click, And That Was The Wrong Conclusion

The player measured the picture against the recording bar by bar — and then measured it again at 70 % speed, where **the same bars were the same number of milliseconds out**. That one comparison settles what three sessions of guessing could not: anything the app loses (a stalled frame, a late resync) is proportional to REAL time, so at 70 % it would be 1.43x larger in song time. An offset that is unchanged in song milliseconds is a property of the FILES.

The app's own numbers say the same thing from the other side: `mp3_worst_drift_ms 53`, `mp3_resyncs 0`, `clock_ratio 0.9968` with all 1052 lost ms and all 7 stalls spent at startup. **The recording is played within 53 ms of where the song says it should be, and the picture keeps real time.** What is 1.7 s out is the tab against the music — which no clock in the app touches.

Re-measured on the chroma curve at a 6-second step:

| Abschnitt | lokale Wanderung |
|---|---|
| 0-60 s | -10.4 ms/s |
| 45-105 s | **-5.2** |
| 90-150 s | -7.0 |
| 135-195 s | **-17.7** |
| 180-240 s | -14.0 |

**The rate varies by a factor of three, so no single correction exists.** A 1995 rock band played to no click and the tab is a fixed grid at 132 BPM; the best possible stretch factor (+1.09 %) still leaves **423 ms** standing. That is worth having — it beats 2.7 s — but it is a repair, not a cure, and it must be offered as one.

- **The old verdict called this song "in sync".** `check_song_sync.py` compared the total drift against a guessed **3 seconds** and the song landed at 2.9 — so it printed "Tab und Aufnahme laufen zusammen" while measuring -1.08 %, and sent the whole investigation into the app. The threshold is `AUDIBLE_MS` (100 ms) now, which is where picture and sound stop reading as one event and is near the app's own 90 ms resync trigger. **A threshold nobody fitted is a threshold that will one day answer the opposite of the measurement it is made of.**
- **And the uniformity test measured itself first.** At `STEP_S = 15` a 60-second bucket held three windows, too few to fit, so the check fell back to a single bucket and reported a rate that triples as **steady**. The step is 6 s now and fewer than three buckets says "cannot tell" rather than "steady". Same lesson as `analyze_ringing.py` and `check_ringing_rescue.py`, for the fourth time.
- **The printed table is thinned, not the fit.** Every window is fitted; every fourth is shown, plus every outlier, because an outlier is the thing worth seeing.

## Leaving A Song Wrote The Log Twice

The player's upload had two logs one second apart for one run, and the second was **missing every mp3 line** while claiming to describe the same run. `stop_audio` is reached more than once on the way out — the screen is torn down and the state change calls it again — and by the second call the recording had been closed. `_run_log_written` guards the leaving path only: `D` is a request and must always produce a file.

## Every Bar Was Played Exactly Once

The player reported the picture and the backing recording drifting apart — synced at the start, about 25 s apart by 3:30, and **the same at 70 %
as at 100 %**. That last detail is the one that matters: practice speed stretches the picture and the stretched recording alike, so it cannot
open a growing gap. Something was missing from the song itself.

Searching the loader for `repeat`, `alternate ending`, `coda`, `segno`, `D.C.` returned **nothing at all**. Every bar was played once. A tab that
lines up with a recording only because it repeats is then shorter in the app than the music is, and the gap grows by the length of every repeat
that was skipped.

- **One plan, four readers.** The notes, the measure ranges and both backing-track extractions used to walk the bars themselves. `played_bars`
  answers "when is this written bar played" once and hands the same answer to all of them — four walks of their own is exactly how the picture
  and the backing would come to disagree, which is the same drift one level down.
- **The two formats are off by one from each other.** GP3-5 counts REPEATS (`repeatClose == 1` means go back once, so twice through); GPIF
  counts PASSES (`count="2"` means twice through). Read as a pass count, GP5's 1 means "play once" and a first/second-time ending never reaches
  its second bar. `Demo_v5.gp5` lost exactly one bar that way, which is how it was caught — so each format converts in its own reader and
  `BarRepeat.close_count` is documented as the number of times the section is PLAYED.
- **Bars are numbered by where they are PLAYED**, not where they are written. A repeated section is two passes on screen, and saying "bar 12"
  twice would make the weakest-section report name a place nobody can find.
- **Measured on the reference fixture**, which turns out to contain exactly the structure that was being dropped: `Demo_v5.gp5` opens with `|:`
  over bars 0-2 and a first/second-time ending. It now reads **771 notes over 52 bars against 729 over 49** — and bar 4, the second ending, was
  previously never played at all. The old number in the test was the bug written down. `canon.gp5`, which has no repeats, is unchanged to the
  note.
- **The GPIF side is verified by injection**: `|:` and `:|` added to bars 4-7 of a real file lengthens it by exactly four bars and 8.7 s, and the
  hand-built container in `tests/test_repeats.py` pins each convention.

## Playing A Drop C Song On A Drop D Guitar

"Wie schwierig ist es bei einem Lied die Stimmung anzupassen… im Idealfall muss man das Tab nicht anpassen, sondern nur die Tonhöhe des MP3s?"
The idea is right and the cheap half is cheaper than it looks: **Drop C and Drop D differ by a uniform two semitones, so the FRET NUMBERS DO
NOT MOVE.** The same shapes on a Drop D guitar are the same music a tone higher. The picture is untouched; what moves is the pitch the app
expects to HEAR and the pitch of the recording.

- **`Timeline.transposed(n)` shifts the notes and the tuning and nothing else.** Applied in `_load_song`, before the matcher, the MIDI backing
  and the guide track are built, so one transposed plan feeds all of them rather than each applying the shift for itself — the
  "four readers of one plan" fault this project has already paid for.
- **Only tunings of the same SHAPE can stand in for each other.** `uniform_shift` returns None for Drop C against Standard, because a Drop
  tuning has its sixth string lower relative to the others and no transposition expresses that. Moving a note to a different fret is a
  different operation and already exists (`tools/retune.py`). `reachable_tunings` is what the key steps through, so a player chooses a TUNING
  and never a number of semitones; DADGAD reaches only itself, and the key says so rather than looking dead.
- **`timestretch.pitch_shift` is the stretch we already had, read back at the pitch ratio.** The two length changes cancel exactly, and that
  matters more than it sounds: **an unchanged length means every sync point, the offset and the whole sync map still describe this file.**
  Nothing has to be measured again.
- **The shift is arithmetic and it is exact.** Measured against a sine of known pitch: +2, −2, +1, +5, −5 and +0.5 semitones all land within
  **0.23 cents**, a five-hundredth of a semitone. End to end on the player's own five-minute recording: built in **7.5 s**, length error
  **0 ms**, and the strongest pitch class moves 0 → 2 for +2 and 0 → 10 for −2, which is the whole point.
- **What it costs is not accuracy but the WSOLA artefacts the practice speed already has**, at a far smaller factor — a tone is 1.12 where
  50 % speed is 2.0. Whether that is acceptable on a full band mix is a question for ears, not for this file.
- **A shifted copy is a different cache entry**, because a transposed song quietly playing the untransposed copy is the fault the sync rate
  caused once already. `_mp3_source_fits` compares the transpose explicitly: the time scale cannot see it, since a pitch shift leaves the
  length exactly alone.
- **The HUD names both tunings** (`Tuning: Drop D … (written Drop C, +2 — R)`). The fret numbers on screen belong to the WRITTEN song, and
  without saying so they belong to a song nobody can find.

## Seven Strings On A Six-String App

Metal is written for seven and eight strings and this app plays six. The usual answer is "you need a seven-string"; the honest one is that the
notes usually fit anyway, because a seven-string in B standard and a six-string in **drop B share their lowest note**. `tools/retune.py`
rewrites the tab: every note keeps its exact MIDI pitch and only its string and fret change, which is arithmetic rather than arrangement.

Measured on the file that prompted it (I Prevail, "Blank Space", a real seven-string tab): both rhythm guitars, **1179 and 1884 notes, every
one of them fits**. The lead track loses four notes above the 24th fret and says which.

- **The tool checks its own claim.** It compares the multiset of pitches before and after: anything missing must have been reported as out of
  reach, and a pitch that appears where it did not before fails the run. A quietly transposed tab is worse than no tab.
- **A beat is placed as a whole, never note by note.** Two notes of one chord landed on the same string — impossible on a guitar, and
  undescribable in GP5: the played-strings byte has one bit per string, so one note is written and never read back and every byte after it is
  garbage. **The file would not open at all**, which is how it was found; reason alone had not.
- **A TIE has to follow the note it continues.** GP5 reconstructs a tied note's pitch from whatever that STRING was last playing, so a tie
  left behind when its predecessor moved reads back as a different note — one came out an octave low. The old-to-new string mapping is carried
  from beat to beat for exactly this.
- **The notes of a beat are written in string order**, because that is the order the reader walks them in.

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
- **A chug that comes back with no pitch is NOT credited — built, measured and removed in the same week.** The argument for crediting it was
  good: a palm mute is a note the tab TELLS the player to choke, a choked string sometimes gives YIN nothing to lock onto, and a chug riff runs
  too fast for the audio window that would otherwise confirm it (trimmed to the gap before the next strike, dropped under `MIN_WINDOW_MS`), so
  on exactly that passage no evidence can ever arrive. Every palm-muted take at the time was a power CHORD, where pitchless strikes really do
  run at 16-20 %.
- **Block 7 said the opposite for a single note.** On 87 correctly played chugs a strike arrives pitchless **3 times — 3.4 %**. On the take
  played a fret off, **3.5 %**: the same rate. So the leniency would have bought three notes in eighty-seven and paid by turning two wrong ones
  green, and being pitchless says nothing whatsoever about whether the fret was right. `tools/check_palm_mute.py` still reports the rate, and
  fails if it ever climbs back to a fifth of strikes — the point where the original premise would hold again.
- **What that recording did show is that a palm-muted low string is heard an OCTAVE above what was played** — 59 of 61 strikes. It costs
  nothing, because the matcher grants octave equivalence on purpose, and it is why scoring a chug take against the wrong tuning reads as zero
  rather than as an octave error.

## Reference Takes Are Only Worth Their Tuning

The block 7 chugs scored **0 of 87** the first time they were read, and nothing was wrong with the audio: the takes were played in drop D and
the manifest said E2. `record_reference.py` asked only for a UNIFORM tuning offset and told the player in as many words not to use a drop
tuning — which is not a thing to ask of someone who plays metal. They answered "standard", correctly, because five of their six strings were.

- **Ask for what varies, not for what is convenient to model.** The recorder now asks for the uniform offset AND for a dropped sixth string,
  and writes both into the manifest. Every tool reads `expected_midi` from there, so the fix reaches all of them at once.
- **A whole take set can be invalidated by one number** and it does not look like an error — it looks like the detector failing. The tell was
  that every strike came back exactly 10 semitones off, which is a tuning, not a mistake: real detection errors scatter.
- The existing session's manifest was corrected by hand rather than re-recorded (the audio is untouched, and the correction says so in a
  `corrected` field).

## A Tie Is One Note Written Twice

The player's screenshot: their tab holds a fret across two beats with a tie and a `let ring`, and the app drew **two notes side by side**. Both readers had half of it right and neither had the other half.

- **GPIF (GP6/7/8) played the continuation again.** `<Tie destination="true"/>` sits on the `<Note>` itself, not in the Properties block, and nothing read it — so the app asked for a pick the music does not contain, and every one of those was an unavoidable miss.
- **GP3-5 dropped it** (`note.type.value == 2`, skipped) **without lengthening the note it continues**, so a note held across two beats was drawn for one.
- **Neither made the note longer**, which is the whole point of a tie. `_extend_tied` finds the last event on that string and stretches it to cover the continuation; nothing is appended, because a tied note is not struck.

Measured on the player's own files: **Bon Jovi 850 → 746 notes** and **Papa Roach 1384 → 1314**. A hundred phantom picks in one song, every one of them scored as a miss, and the percentage they had been reading was built on top of that.

The control matters here as much as the fix: `tests/test_ties.py` builds the same file with the tie taken OFF and asserts it really is two notes of 2000 ms. Without it the class would pass on a loader that simply drops every second note.

**And the note was STILL drawn short, for a reason that had nothing to do with ties.** The player's file settles it: 1018 ties, no `LetRing` property anywhere — the "let ring" in the picture is a text annotation the file does not encode. The tie was merged perfectly (an eighth of 312 ms became 1562 ms, exactly the tied half note) and then the DRAWING threw it away. `_neighbour_gaps` returned `min(gap before, gap after)`, so a note was shortened by something that had already finished: 490 px of sustain cut to 98 px by an eighth note that came BEFORE it. A note can only ever run into the one that FOLLOWS it. Every long note after a quick one on its string was drawn short, in every song, since long before ties were touched — and the test pinned it, with `# backwards, not 800` written next to the assertion.

**A longer drawn note cannot push the picture and the sound apart, and it was worth measuring rather than asserting.** The drawn length is
read off the tab and touches no clock — not `_playback_ms`, not the audio anchor, not the recording's transport — so the only way it could
matter is by costing frames, and a stalled frame really is song time discarded (`clock_lost_ms`). Measured on 4000 notes of sixteenths, the
worst case there is: **1.72 ms a frame with every note plain, 2.34 ms with every note let-ring**, against a 16.7 ms budget. Six tenths of a
millisecond. What the player was seeing is the tab against the recording, which is the chapter above.

**And the last note on a string rang for ever.** With no neighbour to stop it the cap was `inf`, `int(inf)` raises, and the frame died — so
every song whose last note on any string is let-ring crashed when it reached it. It rings to the END OF THE SONG, which is a length.

**And "let ring" is a different thing that looks the same on screen.** The player's next screenshot showed the doubling gone and the note still short: a tie is one note written twice, but `let ring` does NOT change the written value — a let-ring eighth is still an eighth — it says the string is never damped, so the note sounds on until something else is played on it. Neither reader read it at all. It is `NoteEvent.let_ring` now, out of pyguitarpro's `effect.letRing` and GPIF's `<Property name="LetRing">`, and it changes the DRAWING only: the note is drawn up to the next note on its string, which is the cap every other note already has. Nothing about the scoring moves, because the pick is at the written moment either way.

## The Sound Went Bad And Only A Restart Fixed It

"Sobald der flatternde Sound (ist auch leiser) bei einem Song da ist, bleibt er auch bei jedem andern Song und auch bei nur Midi ohne mp3. Bei anderen Apps bleibt der Sound normal."

Every clause narrows it. It survives a song change, so it is not the file; it happens with no recording loaded, so it is not the time-stretch; other applications are unaffected, so it is not the sound card; a restart clears it, so it is **state this process holds**.

- **The mixer had no buffer of its own.** It was opened lazily in two places with pygame's defaults — 512 frames, 11.6 ms at 44.1 kHz — on a machine also running the aubio analysis thread, a WSOLA stretch and a 60 Hz game loop. A backing track is not a monitoring path and nobody can hear 46 ms of it, so there was nothing to be won by cutting it that fine, and a starved output is what "flattering" sounds like. `audio/output.py` owns the lifecycle and asks for 2048.
- **The run log named the input and said nothing about the output.** Device, rate, dropped buffers on one side; on the side the player was actually listening to, not a word. `output_device` now.
- **`Shift+A` reopens it, which is the experiment as much as the workaround.** If the sound comes back, the fault is the mixer; if it does not, it is the shared Windows device and no key in this app can reach it. Until now the only way to find out cost the whole sitting.

**This is not yet a diagnosis and must not be written up as one.** The buffer is the best-founded suspect and it has not been shown to be the cause — the fault has never been reproduced here, only reported.

## Three Things You Can Hear, Switched Separately

The MIDI backing is what the OTHER instruments play; `Shift+B` is the mirror of it — the written notes of the track being PLAYED, so the part
you are meant to produce can be heard while you learn it. They are separate toggles because they answer different questions, and somebody
learning a solo wants the second without the first.

- **Off by default.** Producing that part is the whole point of the app, and hearing it play itself on the first run would teach the wrong
  thing. The setting is remembered per player, not per song.
- **Built by running the same extraction the other way round** — the backing excludes the chosen track, the guide excludes every other one. No
  second code path to keep in step, and it costs no MIDI device: the output is shared.
- **`_midi_all()` is the only way the transport reaches them.** A seek, a pause, a loop turn, a tempo change has to move both or they drift
  apart, and a guide a bar out is worse than no guide. Going through one list is what stops the next transport call being added to only one.
- **A song without one says `—`**, the same as the backing, because a dash is the answer to "why does pressing it do nothing".

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
- **Its range is minutes, not milliseconds, and it needs three step sizes.** A recording is a different piece of music that happens to
  contain the same song, and the tab is not always the whole of it: a GP file holding only the solo has to be lined up against four minutes
  of music before it. `MAX_MP3_OFFSET_MS` is 8 minutes. `Shift+N`/`Shift+M` move 10 ms (what a sync is judged in), `Ctrl` a second (an
  intro), `Ctrl+Shift` ten seconds (reaching four minutes at a second a press is four minutes of pressing). The MIDI backing keeps its own
  offset on the plain keys, with `Alt` for a second — and its range is 10 s rather than the 400 ms it had, because 400 ms was chosen from
  what a synth and a sound card add, which is the wrong thing to choose it from: the tab and the backing do not always start on the same beat.
- **The offset reads in the unit it is judged in.** Milliseconds while it is a sync, seconds while it is an intro, minutes and seconds once
  the tab is only the solo — "-192.00 s" is not something anyone can check against a player's time display.
- **Practice speed is served by a stretched COPY of the file, not by playing it slower.** `pygame.mixer.music` plays at the recorded rate,
  and resampling to 80 % drops the pitch four semitones with it — so `audio/timestretch.py` makes a longer file at the same pitch (WSOLA:
  overlapping windows laid down at a new spacing, each slid to where it best continues the last) and `Mp3Player.set_source` is handed that
  instead. The player is told and reports SONG milliseconds throughout; the file's own time is `song × time_scale`, and `time_scale` is
  `1/tempo`. Every result is cached under `~/.pickhero/stretched/`, keyed by the file's size and mtime as well as the speed, so picking a
  different recording can never inherit the last one's stretch.
- **It keeps time, which is a different question from coming out the right length.** A file can be exactly 25 % longer and still put the
  beats in the wrong places. Measured with a click track at every speed: the spacing is off by **0.03 ms per second at worst — 5 ms over
  three minutes**. So when the recording feels out of sync, it is not the tempo; look at `mp3_worst_drift_ms` and `mp3_resyncs` in the run
  log, which say how far it actually wandered and how often it was pulled back. Individual transients scatter by up to 6 ms at 50 % speed,
  which is WSOLA sliding each window to where it best continues the last, and is the price of the pitch staying put.
- **Measured on the player's own guitar, not on a sine wave** (`tools/check_timestretch.py`): a chord, a fast line and a whole play-along
  take, at every speed from 90 % down to 50 %. The pitch moves by at most **15 cents** — a sixth of a semitone, and the size of the
  measurement's own precision — while the control column shows what merely playing the file slower would cost: **−182 cents at 90 % and
  −1200 at 50 %**. The length lands within 1 % everywhere. The check prints the width of its own correlation peak beside every reading,
  because on a sustained chord that peak is broad and a shift smaller than the width is not a reading at all.
- **It takes seconds, and the player has to be able to see them.** Measured: about 20 ms per second of stereo audio, so a four-minute song
  is five seconds here and plausibly three times that on a laptop, with the recording silent throughout. The first version said "one moment"
  and nothing moved, which is indistinguishable from a feature that does not work — and that is exactly how it came back. It shows the
  percentage now, and a build nobody wants any more is abandoned rather than finished: stepping the tempo down three times would otherwise
  build three copies before reaching the one that was asked for.
- **The recording is looked after while the song is PAUSED too.** `update()` returns early when not playing and the recording's update sat
  after that return, so a copy that landed during a pause was never swapped in and the progress line stood still until playback resumed.
  Pausing stops the music, not the work.
- **The stretch runs on a thread and the recording is silent until it lands.** A whole song is seconds of work, and seconds of work in the
  game loop is a frozen app — the fault this project already shipped once, when a seek reopened the input device. Playing on at the old speed
  meanwhile is not the lenient option either: it is a bar out within seconds. So the HUD says "fitting to 80 % speed" and the recording waits.
  A file SDL can stream but not decode into memory fails here and is named on screen ("convert it to OGG or WAV"), and a failed speed is not
  retried every frame.
- **Every failure is named on screen**: a file that has been moved, a decoder that cannot start from the middle of a file. A backing track
  that silently does not play is indistinguishable from a feature that does not work, and the player would go looking in the wrong place.
- **`play(start=)` really does seek — measured, not assumed.** A file whose pitch encodes its own timestamp, played from 5, 10 and 15 s
  with SDL's output captured to disk, comes back at the right pitch every time. So when a jump does not carry, the fault is in this app's
  path or in that particular file's decoder, and there is no point rewriting the transport. A decoder that cannot seek accepts
  `play(start=)` without complaint and starts from the top anyway, which is invisible — so a gap that stays open past
  `MP3_STUCK_DRIFT_MS` for `MP3_STUCK_FOR_MS` is named on screen rather than left to look like a dead key.
- **The file chooser is the operating system's, and the first one takes seconds.** Opened straight from the key press nothing is drawn in
  between, so the app just stops — indistinguishable from a dead key. The note goes up first and the dialog opens on the NEXT frame, once it
  has really been on screen. And every key repeat that arrived while it blocked was still in the queue afterwards, each one reopening it: the
  player had to cancel the same dialog over and over. Key repeat is 40 ms, so seconds of blocked frame are dozens of them; the keys are
  dropped when the dialog returns.
- **A song with no recording said nothing at all**, so the key that assigns one was invisible and `U` looked as though it had been removed.
  The line now names `Shift+U`. A feature that silently does nothing cannot be told apart from a broken one — the same rule as every named
  failure above.
- **A status message must never outlive its situation.** Picking a file set a note that outranks the ordinary HUD line, and nothing cleared
  it — so the offset the player was adjusting with `Shift+N`/`Shift+M` was never on screen at all, and the key looked dead. Notes are for
  what the live line cannot say, and they are dropped as soon as it can.
- **Pause silences it too, and that took a bug to notice.** Every route to the recording reaches `Mp3Player.seek`, and seeking STARTS
  playback — so nudging the offset on a paused song set the recording playing under a picture standing still, which is the one state the
  offset cannot be judged in. `_mp3_plays()` includes `self._playing` for that reason. Paused, `Shift+N`/`Shift+M` only store the value;
  the HUD shows it move regardless, so the key never looks dead.

## Skipping A Stretch With Nothing To Play

"Wie überspringe ich Leerstellen?" Measured first, over every track of the four songs to hand — because what a rest IS decides the constant:

| | inner rests >= 8 s | outro |
|---|---|---|
| the five guitar tracks | **1** (Kid Rock lead, 12.9 s) | 0 to 52 s |
| bass and vocal tracks | 2 to 5, running to 44 and 100 s | 17 to 67 s |

A guitar track's inner rests are either **4-6 s** — two bars, part of the music, and the player counts through them — or **12 s and up**,
which is a section it does not play, with nothing at all in between. So any threshold from 7 to 12 s picks out the same rests on this
material: `GAP_MIN_MS` sits on a plateau rather than on a knife edge.

- **`E` lands `GAP_LEAD_IN_MS` (3 s) before the next note**, not on it: there has to be time to read the fret and get the hand there.
- **The whole transport moves with it.** `seek` already carries the MIDI backing, the recording and the audio clock's anchor, so nothing new
  was needed — and a picture that jumps while the recording plays on is the sync fault this project has paid for several times.
- **A rest is measured from the END of the notes before it, not from their onset.** Counting from the onset finds three more rests on this
  material, every one of them a held note — and skipping over one would skip a note still sounding and still being scored.
- **The outro is the biggest hole and is deliberately not a jump.** 52 s on one song's lead guitar, 37 s on its rhythm track, and no next
  note to land in front of. It is ANNOUNCED instead ("Nothing left to play — 52 s of song to run"), because a picture scrolling through
  nothing looks exactly like a picture that has stopped.
- **A loop outranks it.** Jumping out of a loop would be undone by the loop itself on the very next frame, which is a key that looks broken.
- **The HUD says the rest is there and names the key**, only while the player is actually sitting in the hole. Announcing one before it
  arrives is noise on a line that is read at a glance.

## The Knob Had Two Ends And Named Neither

"Im Anhang ist ein Bild bei dem die Noten noch immer zu dicht sind." The layout is doing what it was built to do; the knob was turned the
other way and nothing said so. Measured across the factor range on the guitar tracks to hand:

| | 0.4x | 0.8x | 1.0x | 1.5x | 2.5x |
|---|---|---|---|---|---|
| Kid Rock lead, note pairs closer than one head | 5 % | 5 % | 5 % | **0 %** | 0 % |
| | 172 px/s | 220 | 275 | 412 | **683** |

**`-` is what makes the notes dense.** It buys look-ahead by pushing them closer together; `+` spreads them out by showing less of the song.
The HUD read `Scroll: 5.0 s ahead (0.8x, +/-)` — a number that sounds like a good thing, a factor, and two keys with no direction on either.
It names both ends now, and the head size, which is the thing being traded.

- **`SPACING_PERCENTILE` was measured and left alone.** Dropping it from 10 to 2 takes the Kid Rock tracks from 5 % and 4 % of pairs
  touching to 1 %, and costs a quarter to a third of the look-ahead — while changing **nothing at all** on the other three tracks, whose
  window is pinned elsewhere. That is a real trade with no free side, and the player already holds the knob; moving the default would spend
  every song's look-ahead on 3-4 points of density on some.
- **And one step of the knob was dead.** While the trade is live a step moves the window by 7-17 %; at 0.7x -> 0.6x, once the head is on its
  floor, it buys **1.7 %** — a number that moved and a picture that did not, followed by a refusal at the next press. The guard only refused
  a step worth under one MILLISECOND. It refuses anything under 5 % now, which is the middle of the gap between the live steps and the dead
  one. Same fault as "Slowing The Tab Down Did Nothing At All", one press further along.

## The Recording Kept The Tuning It Was Made In

"Wenn ich eine andere Stimmung wähle wird die Tonhöhe wohl nicht richtig angepasst. Es klingt zwar minimal anders, passt aber überhaupt
nicht zu den Tönen, die ich spiele." Correct, and it was one line. `_ensure_mp3_source` decided whether a copy had to be built from the
**practice speed alone**:

```python
if abs(wanted - 1.0) < 1e-3:        # "nothing to build"
    self._mp3_player.set_source(self._mp3_path(), ...)
    self._mp3_loaded_source_transpose = self._transpose
```

At 100 % speed that loads the ORIGINAL recording whatever the tuning — and then records the transpose it had **not** applied, so
`_mp3_source_fits` agreed and it was never rebuilt. The guitar sounds a tone above a backing at its written pitch, which is not a subtle
error and is exactly what was reported. The "minimal anders" is the tab, the MIDI backing and the guide track, all of which DID move.

- **The practice speed is not the only thing that makes the recording a different file.** The test is the speed AND the tuning.
- **And the memo in front of the cache had the same hole.** `_mp3_stretch_matches` compared `(tempo, path)` and not the transpose, so a copy
  built at +2 was handed straight back for the written tuning and the other way round. `timestretch.cache_name` hashes the semitones and had
  it right all along; it was the screen's own one-entry memo that did not — a second reader of one answer, disagreeing with the first.
- **Silent until the right copy lands**, the way a slowed-down song already is. Playing the unshifted file meanwhile is the one thing worse
  than silence, because it sounds like the feature working.
- The control is what makes it safe: at the written tuning and full speed, nothing is built and the original plays, exactly as before.

## Twenty-Five Steps A Second, For A Setting With Eleven Positions

"Ich drücke die Taste Bild ab einmal. Es zeigt kurz 95 % und springt dann auf 50 %. Drücke die Taste aber super kurz." Measured, and the
arithmetic is the whole diagnosis:

| | |
|---|---|
| `pygame.key.set_repeat(300, 40)` | a repeat every **40 ms** — 25 a second |
| practice speed | 50 % to 100 % in 5 % steps = **11 positions** |
| **holding PgDn to cross the entire range** | **700 ms** |
| scroll factor (22 positions) | 1.14 s |

One global repeat rate for every key in the app. That is right for an arrow key walking through a song, where the player is scrubbing, and
wrong for a discrete setting — and on top of it **a frame that stalls drains every repeat that arrived during it in one go**, so a single
press lands at the far end. The 95 % the player saw is the one frame that rendered between the first event and the burst.

- **Only REPEATS are gated, never the first press.** A key that feels dead is the fault this display has already been fixed for twice.
- **Coming off the key clears the gate**, so two deliberate presses both count. Exact where a timer alone would be a guess — the same reason
  `Shift+S` waits for its key to come up.
- **150 ms a step** walks the speed range in 1.5 s, which reads as a deliberate movement, and is nine times a frame, so a burst drained in
  one frame moves the setting by one step. The test presses the key ten times inside one frame and requires one step; it fails on the
  unfixed code, which is the only thing that makes it worth having.
- The scroll factor gets the same gate. It is the same fault, and two answers to one question is how this project has been bitten before.

## Two Complaints Pulling On One Knob — And It Was Never One Fault

"Bei schnelleren Songs wie I'd die for you oder Love walked in wird das Bild irgendwie unscharf und ich kann Töne kaum erkennen." Written
down before anything was built, because the first measurement said the obvious fix is the one that makes the OTHER complaint worse.

**The same player, the same week, asked for opposite things from `+`/`-`:**

| | px/s | smear at 60 Hz | the notes |
|---|---|---|---|
| `-` (0.8x) | 172 | 2.9 px | **denser** — the complaint before this one |
| 1.0x | 275 | 4.6 px | |
| `+` (1.5x) | 412 | 6.9 px | further apart |
| `+` (2.5x) | 683 | **11.4 px** | furthest apart |

So "the notes are too dense" wants `+` and "the picture is unsharp" wants `-`. **The knob cannot answer both**, and any fix that only moves
it trades one report for the other. That finding stands. What did not stand is the assumption underneath it: that one cause was behind one
sentence.

**Then the player's own machine was measured**, which is what this chapter had been waiting for. Two run logs (`D`), one per song, from
build `400ac736` on 1920x1080 at 60 Hz on Intel integrated graphics:

| | Thunder, "Love Walked In" | Bon Jovi, "I'd Die For You" |
|---|---|---|
| `frame_ms_median` | **19.9 ms** | **19.6 ms** |
| `frame_ms_worst_tenth` | 22.6 ms | 22.2 ms |
| `frames_over_budget_percent` | **95** | **93** |
| `clock_ratio` | 1.0000 | 1.0000 |
| `frames_measured` | 3600 | 3600 |

**The machine draws about 51 frames a second, not 60**, on both songs alike, and `record_frame_ms` samples the WORK before `clock.tick(60)`
pads it — so this is the drawing, not the wait. `clock_ratio 1.0000` says the app's clock is exact to four places, which rules out the other
half of this project's oldest lesson: nothing is being lost, everything is being drawn slowly. On an unsynced 60 Hz panel roughly one frame
in five is then shown twice, at no fixed interval, and the smear per shown frame is `v / 51` rather than `v / 60`.

**That is a constant, and a constant cannot pick out two songs.** So the tracks the logs actually name — `notes_written` 1819 is Thunder's
lead, 746 is Bon Jovi's distortion guitar — were measured against a song the player has never once complained about:

| track | px/s | head | smear at 51 Hz | onsets in its densest 2 s |
|---|---|---|---|---|
| Kid Rock lead — **never complained about** | **384** | 58 px | 7.5 px | 10 |
| **Thunder lead** | **384** | 63 px | 7.5 px | 20 |
| **Bon Jovi distortion** | 432 | 42 px | 8.5 px | 18 |

**Kid Rock's lead and Thunder's lead scroll at the identical 384 px/s**, with the identical smear, on the identical machine. One reads, one
does not. Persistence blur is real and is measured above, but it is NOT what selects these two songs, and the table this chapter opened with
would have sent the next session to the scroll speed for the rest of the week.

**What separates them is what happens on ONE STRING**, which is the only place two heads can actually collide — a head is never taller than
its lane, so notes on different strings cannot touch however busy the screen looks:

| track | pairs on one string | closer than a head | the worst one | fret numbers part-covered |
|---|---|---|---|---|
| **Thunder lead** | 1813 | 47 (2.6 %) | **24.6 px on a 63 px head** | **41 — and 22 of them inside five seconds from 3:52** |
| Bon Jovi distortion | 740 | 3 (0.4 %) | 32.7 px on a 42 px head | **0** |
| Kid Rock lead | 484 | 20 (4.1 %) | 51.4 px on a 58 px head | 5 |

**The count does not discriminate — Kid Rock crowds its heads MORE often than Thunder does.** The severity does: Thunder's worst pair overlaps
by 61 % of a head where Kid Rock's overlaps by 11 %, and Thunder puts 22 of them in one run. And the Thunder log stops at
`reached_ms 229084`, three seconds before that run begins.

**So one sentence was two faults, and only one of them is the one that was fixed here:**

- **The fret number was behind the next note.** `_draw_notes` drew head-then-number for each note in turn, so the following head landed on the
  number already painted. In a fast run on one string that is every number but the last. Heads are drawn in one pass and numbers in a second
  now — no geometry changed, no look-ahead was spent, and the `+`/`-` trade at the top of this chapter is untouched. It is asserted as the
  ORDER the surface is painted in: a pixel count cannot say whether the white it found belongs to the covered number or to the neighbour
  sitting in the same few pixels, and a first version of the test passed on the broken code for exactly that reason.
- **The pacing ignores its own tail on purpose.** `_spacing_percentile(10.0)` means a tenth of every song is by construction tighter than the
  head it is given. That is the right call for the look-ahead — a couple of freak-close notes must not set the pacing for everything else —
  but it means the fault above lives in the tail the percentile deliberately ignores, which is precisely where a solo lives.
- **Bon Jovi is NOT this fault.** Zero of its numbers were covered. It is the fastest scroller measured on this machine and it carries the
  smallest head — 432 px/s on a 42 px head whose two-digit number is 40 px wide — so its number is both the smallest and the fastest-moving
  in the collection. That one is the frame rate, and the frame rate is not fixed yet.

**What is still open, and it is the bigger of the two.** 19.7 ms a frame is the shared cause behind both songs and every other one; it simply
only becomes visible when the music asks for about ten notes a second, which is what both named songs do and what Kid Rock's lead (five a
second) does not. `App.run` calls `set_mode` without `vsync=1` and pads with a SOFTWARE `clock.tick(60)`, so the panel and the app beat
against each other on top of it. Nothing here has established WHAT costs the 19.7 ms, and the draw path has changed by 338 lines since the
build these logs came from — chord cards are drawn every frame now — so **the first move is a fresh `D` on the current build, not a guess.**

**Measured AGAIN, and it turned out to be a second COMPUTER rather than a second build.** The player has two laptops and the run logs
came from both, which is what made the numbers look like a fix:

| | NB1 — Intel Graphics, 1920x1080 at 60 Hz | NB2 — Iris Xe, 1920x1200 at **59 Hz** |
|---|---|---|
| `frame_ms_median` | 19.9 / 19.6 ms | **4.2 / 4.3 ms** |
| `frames_over_budget_percent` | 95 / 93 | **0** |
| delivered | ~51 a second | 2497 frames in 41.3 s = **60.5** |

A first reading blamed the cheap frames on the microphone — every NB2 run had the gate closed on everything, and `_draw_notes` asks the
matcher for a verdict per visible note per frame. **That was wrong**, and it is written down because it was nearly built on: the two sets
differ by MACHINE, and Iris Xe against the older integrated part is the whole four-fold difference. There is nothing to find in the audio
path.

**And the machine that makes its frames still has the complaint**, which is the finding that matters. NB2 draws in 4.2 ms of a 16.7 ms
budget, delivers a clean 60, and the player still reports smearing at 1.0x and slight juddering at 0.6x. So making the drawing cheaper
cannot be the answer: on Thunder's lead at 1.0x the smear is 7.5 px at NB1's 51 frames and 6.5 px at 60 — **the entire prize for fixing
the frame cost is 13 %**, and NB2 has already collected it.

**What is left on NB2 is not the frame COST but the frame PACING, and it has a number.** The panel runs at **59 Hz** and `App.run` pads
with `clock.tick(60)`, a software timer against a display nobody asked. Sixty offered into fifty-nine taken is a beat of one a second:
about once a second a frame is shown twice and the note holds still and then jumps double. That is judder, not blur, it is independent
of the scroll speed — which is exactly why the player still sees it at 0.6x, where the smear is only 3.9 px — and it is a different
fault from everything above.

**So the gap BETWEEN pictures is measured now** (`record_frame_shown`, reported as `frame_interval_median`,
`frames_per_second_shown` and `frames_uneven_percent`). `record_frame_ms` times the WORK before `clock.tick` pads it, which answers "can
the machine keep up" and says nothing about when the frames were actually handed over. Unevenness is counted against the run's OWN median
rather than against 16.7 ms, because a steady 17.4 is a different report from an average 16.7 that is really 16.7 and 33.3 in turns --
only the second one judders, and an average alone cannot tell them apart. A gap spanning a pause is dropped rather than charged to the
first frame back as a stutter that never happened.

**What it deliberately cannot see is the point of it.** Without vsync `flip` returns before the panel has shown anything, so a frame the
DISPLAY held twice never reaches the app. That splits the question in two, and the answer decides what gets built: **ragged** here is the
app's own timer and is fixable without vsync, while **dead even** says the remaining judder is the beat against a panel running at some
other rate, and only `vsync=1` answers that -- which needs `SCALED` in pygame 2, is a real change to how the window resizes, and may not
be honoured by the driver at all. That is why it is not being tried first.

**And the measurement is asserted to be WIRED, not only to be correct.** Six tests drive `record_frame_shown` by hand; a seventh runs the
real `App.run` loop for five frames and counts four gaps, because a measurement nothing calls is a feature that ships doing nothing --
which this project has shipped before.

**Measured on NB2, three runs, and they agree to a decimal:**

| | Thunder | Thunder | Bon Jovi |
|---|---|---|---|
| `frame_ms_median` | 8.3 | 9.3 | 9.4 |
| `frames_over_budget_percent` | 1 | 3 | 1 |
| `frame_interval_median` | 16.67 | 16.68 | 16.63 |
| `frame_interval_best_tenth` | 13.75 | 13.59 | 13.68 |
| `frame_interval_worst_tenth` | 19.48 | 19.90 | 19.59 |
| `frames_per_second_shown` | **60.0** | **59.9** | **60.1** |
| `frames_uneven_percent` | **15** | **18** | **15** |

**Nothing is being doubled inside the app.** A frame held twice would put the worst tenth near 33 ms; it is 19.5. What the spread shows is
JITTER -- give or take 3 ms on a 16.7 ms frame, a sixth of it, on one frame in six. `clock.tick` sleeps for most of its wait and Windows
cannot sleep to the millisecond, so this is the software timer's own resolution and nothing else's.

**How much that is worth in pixels, honestly: not much.** Because `_playback_ms` advances by REAL elapsed time, a frame computed 3 ms early
is not WRONG, it is simply early -- the note is drawn where it truly is. At 384 px/s the step wobbles between 5.2 and 7.7 px against a
nominal 6.5. That is a shimmer, not the hesitate-and-jump the player describes.

**The hitch has to come from the other side, and it is the one number the app cannot see: 60.0 handed to a panel Windows calls 59.** The
app offers sixty pictures a second into a display that shows fifty-nine, so one picture is periodically shown twice however even the app
is. How often depends on a digit Windows rounds off -- an exact 59 Hz beats once a second, 59.94 once every seventeen -- and neither the
run log nor `pygame` can tell them apart, because `flip` returns before the panel has done anything.

**Then NB1 was measured on the same build, and it overturned the machine explanation as well.** Three runs, and NB1 is now the FASTER
of the two:

| | NB1 Bon Jovi | NB1 Thunder | NB1 Thunder | (NB2, for comparison) |
|---|---|---|---|---|
| `frame_ms_median` | **5.9** | 7.0 | 6.6 | 8.3 – 9.4 |
| `frames_over_budget_percent` | 0 | 0 | 0 | 1 – 3 |
| `frames_uneven_percent` | **6** | 10 | 10 | 15 – 18 |
| `frames_per_second_shown` | 60.0 | 59.9 | 59.8 | 59.9 – 60.1 |

**So the 19.7 ms is gone on the machine that produced it**, and the second explanation has to go the way of the first. What actually
separates the two slow runs from the ten fast ones is not the build and not the laptop: it is that **those two are the only runs where the
guitar was audible.** Focusrite plugged in, `level_under_gate_percent 28`; every run since, on both laptops and four builds, has been a
dead microphone at 100 %. `_draw_notes` asks the matcher for a verdict and the feedback for a colour on every visible note, every frame,
and with nothing to judge that work does not happen.

**Which is the hypothesis this file already dismissed once, and dismissing it was the mistake.** It is written down twice now because the
lesson is the method, not the answer: two variables moved together each time, and each reading picked the one that had just changed.
`frame_ms` also swings by a factor of two between runs on ONE machine in ONE condition (4.2 to 9.4 on NB2), so nothing under about three
times is a finding at all. **One run settles it and it has not been done: NB1, this build, the interface plugged in, the guitar heard, D.**

**And NB1's panel runs at 60 where NB2's runs at 59, which the unevenness follows**: 6-10 % against 15-18 %, on the machine whose panel
matches what the app offers. That is the beat showing up exactly where the arithmetic says it should, and it is the strongest evidence for
the pacing story that exists so far.

**Which makes `vsync=1` the only remaining lever, and it answers both at once**: a blocked flip takes the panel's cadence, so the jitter
goes and the beat cannot exist. It also MEASURES the panel: with vsync on, `frames_per_second_shown` is the display's true rate, 59 or
59.94, and the question above answers itself. The cost is real and has to be tried rather than argued: `vsync` needs `SCALED` in pygame 2,
which fixes a logical size and letterboxes on resize instead of relaying out, and a driver may ignore the request entirely.

**What vsync will NOT do, and the player should hear it before it is built: the smearing stays.** It is `px/s ÷ refresh` and no pacing
touches it -- 6.5 px at 1.0x on this panel. Vsync is the fix for the juddering only.

**Built as a switch on `Z`, not as a decision.** Both halves of the trade are real and neither can be argued from a keyboard: vsync ends
the beat and the jitter together, and it costs `SCALED`, which fixes the drawing size so the window LETTERBOXES when it is dragged bigger
instead of laying the lanes out again — and a driver may refuse the request outright, which raises and is caught, because the window still
has to open. A refusal is said out loud: silence there reads as "it worked", and the next run log would be compared against a mode that
never happened.

- **The run log names the pacing** (`vsync   asked` / `off`). Two logs that differ in the one thing under test are worth nothing if
  neither says which was which, and this session has already lost a day to exactly that.
- **Every mode change goes through one door** (`_apply_display_mode`). The resize path called `set_mode` itself with the plain flags, so a
  drag would silently have dropped vsync — and it assigned the result to a local nobody read, so the loop went on drawing to the surface
  it already had. Both were there before this and both are gone with it.
- **The window is reopened only when the ANSWER changes**, never per frame: `set_mode` tears the surface down and builds it again.
- **And the driver said no.** On the player's machine `SCALED|RESIZABLE` with `vsync=1` raises, so the first shape of this reported vsync
  as impossible on a machine that had been asked exactly once. It asks down a ladder now — `SCALED|RESIZABLE`, then `SCALED` alone, which
  costs a window that cannot be dragged bigger — because a no to one way of asking is not a no to vsync. **A run came back that LOOKED as though it worked, and reading it that way was a mistake.** On NB1, whose panel really is 60:

| | vsync off | vsync on |
|---|---|---|
| `frames_uneven_percent` | 15 | **4** |
| `frame_interval_best_tenth` | 13.83 | **15.26** |
| `frame_interval_worst_tenth` | 19.48 | **18.13** |
| `frames_per_second_shown` | 59.9 | 60.1 |
| `frame_ms_median` | 6.8 | **10.2** |
| `frames_over_budget_percent` | 0 | **9** |

The jitter band looks halved — 5.7 ms wide against 3.9 — and the frame cost looks like `SCALED` paying for itself. **Then the player
answered the one question that settles it: the window can still be dragged bigger while the refusal is on screen.** `SCALED` fixes the
window; a resizable window means the fallback path ran and vsync was never on. So the whole table above is one noisy run against another
— NB1's unevenness had already been measured at 6, 10 and 10 % before any of this, and 4 % sits inside that spread.

**That is the third over-reading in this session and the first one where the rule to prevent it was already written down, two chapters
up, by the same hand: nothing inside the run-to-run spread is a finding.** It was applied to `frame_ms` and then not applied to
`frames_uneven_percent` an hour later. The rule is not "be careful with frame times"; it is that a single run of anything on these
machines carries a factor of two, and a change has to clear that before it is a change.

**So vsync is refused on this hardware, by both ways of asking, and that line of attack is closed.**

**The log names the OUTCOME anyway** — `on, window resizable`, `on, window fixed size`, `refused`, `off` — filled in by the window that
actually opened. `vsync_outcome` is deliberately not stored in the settings file: it belongs to this run on this machine, and the reason
it exists at all is that "asked" and "got" turned out to be different things nobody could tell apart afterwards.

**Which leaves the jitter, and it can be had without the driver.** `clock.tick` asks the system to sleep for most of the wait and Windows
cannot sleep to the millisecond — that is where the 3 ms comes from. `Shift+Z` waits in two parts instead: asleep until two milliseconds
before the frame is due, then spinning. **The spin is the whole cost and it is a tenth of one core, not the whole of it** — the first
estimate here said 60 % and nearly buried the idea, because it assumed the spin covered the entire wait rather than the last stretch of
it. The run log names the pacing (`steady` / `system timer`) beside the vsync outcome.

- **The due time walks forward by whole FRAMES, not from "now"**, so a frame that runs long is caught up rather than pushing every frame
  after it. A real stall — a seek, an engraving — resets it instead, because catching up half a second would run the picture flat out
  until it had.
- **The decision is split from the waiting** (`_frame_plan`), and only the decision is tested. The first attempt tested the whole thing by
  replacing `time.perf_counter` for the process, which stopped the spin from ever reaching its due time and hung the suite. A real busy
  wait cannot be tested by freezing time, and it does not need to be: the loop is two lines and the arithmetic is all of it.

**And then the third run came back measuring nothing again.** `pacing   system timer` — the switch had not been pressed, exactly as the
run before it said `vsync off` when the player believed it on, and the one before that said `asked` when it had been refused. Three
measurements of three settings, none of which were on.

**That is not the player forgetting. It is where the state was kept.** The only places a setting showed itself were a status note that
expires after eight seconds and a log written after the fact — so at the moment of pressing `D`, nothing on screen said what was being
measured. The HUD carries it now, beside the scroll line, in **the same words the log uses**: `Pace: steady (Shift+Z) | vsync: refused
(Z)`. Highlighted whenever either is away from its default, because an experiment the player has forgotten is running is worse than no
experiment — and highlighted in the STREAK colour, not the HUD accent, which is what half that panel is already drawn in: the first
version made "highlighted" and "normal" the same blue, and the player asked what it was supposed to mean and answered the question in the
same sentence.

**The rule this session keeps re-learning, now in its general form: a setting under test must be readable at the moment the measurement is
taken, in the same words the measurement will use.** Everything else — a toast, a keypress, a memory — is a way of finding out afterwards
that the run was worthless.

**And with the state finally on screen, the player flipped the switch several times, saw no difference, and was right about the number and
wrong about the world.** `_frame_ms` and `_frame_intervals` are rolling windows of the last `FRAME_SAMPLES` frames — a minute at 60 Hz.
Flipping the pacing inside that minute leaves the log averaging half of one mode with half of the other, **so the number could not have
shown a difference however large the difference was**. Three runs were spent on this before the buffers themselves were looked at.

Changing either setting throws the frame history away now, so a log is always about one mode — and `frame_intervals_measured` doubles as
how long that mode has actually been running, which makes a log taken two seconds after the switch say so itself.

**His screenshot and his log also disagreed outright**: the HUD read `vsync: on, window resizable` while the log said `refused`. They read
the same field, so the two were true at different moments — the outcome VARIES between attempts on this machine, granted once and refused
the next time. That is worth knowing on its own and it is another reason the history has to be dropped at the switch: a run that spanned
both is not a reading of either.

## The Pacing Is As Good As It Gets And It Was Never The Answer

Three runs on one machine in three minutes, same song, the only difference the switch:

| | vsync | pacing | `frames_uneven_percent` | interval band | `frame_ms_median` |
|---|---|---|---|---|---|
| 16:11 | on, window resizable | system timer | 4 | 15.29–18.17 | 10.3 |
| **16:12** | on, window resizable | **steady** | **2** | **15.67–17.71** | 13.2 |
| 16:14 | on, window resizable | system timer | 4 | 15.40–18.03 | 13.1 |

**Both things work, and both are now measured rather than argued.** vsync — granted this time, refused the last — took the unevenness from
the 12 to 18 % this machine used to show down to 4. Steady pacing halves that again to 2, and narrows the jitter band to 2.0 ms against
2.9. Reproducible, in the right direction, outside the run-to-run spread: this is a finding by the rule two chapters up.

**And the player still reports the same thing he reported on the first day: "Es ruckelt leicht und ist immer sehr schlierig. Geholfen hat
nichts."**

That is the result, and it is worth more than the improvement. The frame pacing is now within 2 ms of perfect and the complaint has not
moved, **so the complaint was never the frame pacing.** What is left is `px/s ÷ refresh` — 7.2 px at Bon Jovi's 432 px/s — which is a
property of a sample-and-hold display and which no amount of timing touches. The two days of vsync, jitter, spin-waiting and log fields
bought a real 8-fold improvement in a number that was not the one the player was looking at.

**So this line of work is finished, and it is finished by evidence rather than by exhaustion.** The switches stay, measured and documented,
because they are right and cost nothing. The judder hunt stops. What is left for a fast passage is the thing the player confirmed on day
one and that this file has now recommended three times: `Shift+T`, a page that does not move.



**Which leaves the jitter, and it is bigger than this chapter first allowed.** The reasoning that dismissed it was that `_playback_ms`
advances by real elapsed time, so a frame computed 3 ms early is early rather than wrong. True, and beside the point: the frame is SHOWN
on the panel's fixed grid, and one that is ready 3 ms late misses its refresh and is held for two. At 15 to 18 % of frames outside a fifth
of the median that is several hitches a second — where the 60-into-59 beat is one a second at worst. **The jitter is the larger effect and
the only one that does not need the driver's permission**, and its cause is named: `clock.tick` sleeps for most of its wait on a system
that cannot sleep to the millisecond.


**Measured in the two passages the player actually named, and they are two different faults after all** — at his window, over the solo of
each song, counting only pairs on ONE string, where two heads can really collide:

| | look-ahead | head | px/s | smear at 60 Hz | pairs closer than a head | median gap |
|---|---|---|---|---|---|---|
| **Bon Jovi solo, 1.0x** | 2.3 s | 42 px | **432** | **7.2 px** | **0 of 57** | 98 px |
| Bon Jovi solo, 0.6x | 3.7 s | 26 px | 270 | 4.5 px | 0 of 57 | 61 px |
| **Thunder solo, 1.0x** | 4.0 s | 42 px | 255 | 4.2 px | **30 of 55 (55 %)** | **33 px** |
| **Thunder solo, 0.6x** | 6.3 s | 26 px | 160 | 2.7 px | **30 of 55 (55 %)** | **20 px** |
| Thunder solo, 1.5x | 2.6 s | 42 px | 382 | 6.4 px | 18 of 55 (33 %) | 49 px |

**Bon Jovi's solo never overlaps at any speed** — nought of fifty-seven — and is the fastest thing measured in this collection. It is
smear and nothing else. **Thunder's solo overlaps at the MEDIAN**, not in the tail: half its notes sit closer together than a head is wide,
20 px against a 26 px head at 0.6x, and 26 px is `MIN_HEAD_PX` — **the floor**. There is no smaller head to spend, so at 0.6x the display
has already lost. Only 1.5x parts them, at 6.4 px of smear against 2.7.

**That is the trade at the top of this chapter with no room left in it.** For a passage of this density the scrolling view cannot be made
to work on a 60 Hz panel by any setting it has, and `Shift+T` -- which the player has already confirmed is fine there -- is not a
workaround but the answer.


**And the speed knob cannot separate the notes, which is the question the player asked.** Measured on Thunder's lead at his window:

| | look-ahead | head | px/s | smear at 60 Hz | pairs on one string closer than a head | tightest | tightest / head |
|---|---|---|---|---|---|---|---|
| `-` 0.6x | 7.7 s | 32 px | 197 | 3.3 px | **47** | 12.6 px | **39 %** |
| 1.0x | 4.6 s | 53 px | 328 | 5.5 px | **47** | 21.0 px | **40 %** |
| `+` 1.5x | 3.1 s | 53 px | 492 | 8.2 px | 32 | 31.5 px | 59 % |

**`-` does not reduce the crowding at all** — 47 pairs at 0.6x and 47 at 1.0x — because the head is sized WITH the speed: slower notes
are smaller notes, and the ratio that decides whether two heads touch barely moves. `+` does separate them, at 59 % against 40 %, and
pays for it in smear: 8.2 px against 5.5. **Separation and smear are the same number in pixels**, which is why the player reports
1.5x as readable and worse at the same time, and it is the top of this chapter restated in measurements.

**So the one lever nobody has pulled is the head width at a FIXED speed.** They are locked together today
(`window = spacing x usable_width / per_head`), so there is no way to ask for "the notes I have now, narrower". At 1.0x a 32 px head
would give 21.0 / 32 = 66 % separation — better than `+` delivers — at 328 px/s instead of 492, which is a third less smear. It is not
free: `MIN_FRET_DIGIT_PX` is 34 px because that is what a two-digit fret needs, so this buys reading room by spending digit size, and
the player has to say which he would rather have.

**The player confirmed `Shift+T` on the fast passage: no problem at all, because the page does not move.** Its playhead was borrowing
the board's white hit-zone colour onto a paper ground and could not be found; it has its own colour now, asserted as contrast against
`PAPER` rather than as a named blue.

**And the direction that costs nothing has now been tried, and it works:** `Shift+T` HOLDS the page and moves only at a line break, so a
page view has no persistence blur by construction and no crowding either. On the passage the player could not read while it scrolled,
the page view was fine. That is the answer for a fast song until the frame question above is settled.

## A Sheet Has No Hit Line

The scrolling view has one rule it cannot escape, and two days of this session were spent finding out that it cannot. **A note's x is its
time multiplied by a speed**, because the note has to arrive at the hit line at the moment it is played. So the distance between two notes
and the speed of the picture are THE SAME NUMBER, and every attempt to part the notes made the picture faster — which is the complaint that
started all this.

**The player found the way out, and it is not a knob.** A sheet that does not scroll has no hit line, so nothing has to reach a fixed point
at a fixed moment. **x is then free of time, and the PLAYHEAD carries the time instead** — running quickly through a sparse bar and slowly
through a dense one. The trade dissolves: every note can have the room it needs, at no cost in speed, because there is no speed.

`ui/sheet.py` is that arithmetic and nothing else — no drawing, so it is tested without a screen, and because two views will want the same
answer.

- **Proportional until it would overlap, then a floor.** Each gap is `max(gap_ms x per_ms, 1.18 heads)`. That single `max` is the whole
  idea: rhythm is visible wherever there is room for it, and legibility wins wherever there is not. A run of sixteenths therefore comes out
  EVENLY spaced rather than proportionally illegible, which is what an engraver does and what Songsterr does — read off the player's own
  screenshot of it, where a bar of sixteenths is visibly wider than the bar of crotchets beside it.
- **Rows are whole bars, justified to the line.** Bars are filled in until the next one would not fit, then the row is stretched to the full
  width rather than left ragged. A single bar too dense for a whole line is squeezed instead of dropped, and the row SAYS SO (`crowded`) —
  a silent overlap is the fault this view exists to end.
- **A note that is filtered out takes no room**, or a song with a fret limit is spaced for notes nobody can see.

The numbers this produces on the player's own songs are in the next section, measured on the real screens rather than on a
hypothetical 1200 px one.

### The view itself

Built. Two rows of music that do not move, the board's own coloured heads on them, and a playhead that runs unevenly across a line that
does not. Nothing in it is a second copy of anything: the clock, the keys, the matcher, the offsets, the chord cards, the HUD and the help
overlay are the ones the scrolling board uses, because none of them ever depended on the scrolling. Only the three things that need to know
where a note LANDED are new — the heads, the chord blocks, and the playhead.

- **The row in the hand is the top one**, so the row after it is always underneath and a line break is never a surprise. This is the rule
  the page view had to learn the hard way, one chapter down, and it was written here from the start rather than rediscovered.
- **It slides rather than jumps** (a quarter of a second, eased at both ends) — the same arithmetic the page view uses, extracted into
  `_glide_step` so the two cannot drift apart. The state is NOT shared: two views glide at once and switching between them must not make
  the other one jump. The snap distance is passed in, because a row of the sheet is whatever the head size makes it, so "too far to be a
  page turn" has to be counted in rows and not in pixels.
- **A note keeps the colour it lit up in, and NOTHING on the sheet is dimmed.** "Damit kann ich sogar super zurückschauen, wo Fehler
  waren" — and then, once it was on screen: "Die bereits passierten Noten werden ausgegraut. Lass sie einfach in der Farbe der Bewertung
  stehen ohne abdunkeln." The first build reused the board's colour path, which dims a note the moment it is done with — right there,
  because a note behind the hit line is in the way of the ones still coming, and wrong here, because on a sheet the row behind the
  playhead is the RECORD of the run and the only thing this view offers that a scrolling one never can. `_sheet_note_colour` does not
  dim: a judged note wears its verdict at full strength and keeps it, an unjudged one stays its own string's colour, and the playhead
  is what says where the music is.
- **The board under the notes is a fretboard, not a table.** The first build painted six alternating bands and no strings at all, which
  the player saw immediately: "Die Saiten am Griffbrett sind nicht mehr sichtbar." Alternating bands are exactly the look the scrolling
  view threw out — "the banding is what made the old display read as a table of rows instead of a fretboard" is a comment sitting in
  `_draw_lanes`, in this same file, and it was re-introduced anyway. One uniform panel now, with the six strings across it, thicker AND
  warmer towards the low E, and a wound string drawn as a dark core with a highlight so it reads as round rather than as a thick line.
  The thicknesses are the gauges of a light set (.010 to .046) divided by the first, scaled to the lane: **2 px for the high e against
  9 for the low E** on a 68 px lane. "Die tiefe Saite wesentlich dicker als die dünnste" is the strongest cue there is for which lane is
  which, and it works before a single fret number has been read.
- **Bar numbers and bar lines.** A sheet without them is a sheet you cannot talk about: "the run in bar 34" is how a passage gets found
  again and how a loop gets set.
- **The chords came along.** The grip cards are the board's own method, called unchanged. Only the BLOCK — the tint that says "these five
  notes are one grip" — had to be told where the notes ended up.

**The size is the one control, and +/- is it.** The head sets both how big a note is drawn AND how far apart two of them have to sit, so
one key moves legibility and bars-per-row together. The view OPENS at the size where two rows exactly fill the room there is
(`head_for_room`), which on the player's own machines is a **58 px head against 26 to 44 on the scrolling board**.

**The range goes down only, and the view opens at the top of it.** Two steps up were built, shipped and tried: "Verkleinern fühlt sich gut
an. Vergrößern macht keinen Sinn." They bought nothing the eye wanted and cost the row that shows what is coming, so they are gone rather
than left in as a way to make the view worse. What the five remaining steps do to Thunder's lead on the 1200 px screen — and this is the
answer to "can I set the bars per row with +/-":

| step | head | rows in sight | bars a row | row lasts | pairs closer than a head | tightest |
|---|---|---|---|---|---|---|
| 0.50x | 29 px | 3 | 6 | 8.9 s | 0 of 1620 | 35 px |
| 0.60x | 35 px | 3 | 5 | 7.5 s | 0 of 1592 | 42 px |
| 0.70x | 41 px | 2 | 4 | 6.1 s | 0 of 1549 | 49 px |
| 0.85x | 49 px | 2 | 3 | 4.5 s | 0 of 1486 | 61 px |
| **1.00x** | **58 px** | **2** | **3** | **4.4 s** | **0 of 1472** | **69 px** |

**Not one overlapping pair at any size, on either song.** The smallest step draws a 29 px head — still bigger than the 26 px floor the
scrolling view was pinned at — and parts every pair by at least 35 px, on the passage that started this whole session.

**Measured on the two songs this session is about, on the player's own screens, at the size the view opens at:**

| | head | rows | bars per row | row lasts | pairs on one string closer than a head | crowded rows |
|---|---|---|---|---|---|---|
| **Thunder lead**, 1920x1080 | 50 px | 84 | 3 | 4.5 s | **0 of 1486** | 0 |
| **Thunder lead**, 1920x1200 | 58 px | 86 | 3 | 4.4 s | **0 of 1472** | 0 |
| **Bon Jovi lead**, 1920x1080 | 50 px | 49 | 3 | 5.5 s | **0 of 1452** | 0 |
| **Bon Jovi lead**, 1920x1200 | 58 px | 49 | 3 | 5.5 s | **0 of 1452** | 0 |

**Thunder's solo had 47 overlapping pairs in the scrolling view, 30 of them inside five seconds. It has none here**, at a head bigger than
the scrolling view has ever drawn, with no speed spent — because there is no speed.

**The frame costs about two milliseconds more than the board's, and that is the whole story** — measured over 300 frames on Thunder's
lead at 1920x1200 with a matcher running, four runs:

| | median | worst tenth | worst |
|---|---|---|---|
| standard | 5.39 – 5.61 ms | 5.71 – 6.25 ms | 7.24 – 9.08 ms |
| hybrid | 7.17 – 7.30 ms | 7.52 – 7.69 ms | 9.19 – 10.65 ms |

**A retraction belongs here.** The first run of this measurement showed a 19.81 ms worst frame for the standard view against 7.58 for the
hybrid, and that went into this file as "the worst frame is two and a half times better". It was one outlier in one run: three further
runs put the standard view's worst at 7.24, 8.47 and 9.08. **Nothing inside the run-to-run spread is a finding** — this file's own rule,
written two chapters up after breaking it three times, and broken again here within the hour. The honest claim is the boring one: both
views sit at well under half the 16.7 ms budget, the hybrid costs about two milliseconds more, and the reason to prefer it is what is on
the screen and not what the profiler says.

The layout is built once per song, size and filter, never per frame; the drawing touches only the rows on screen. Both are held by tests,
because this display has had to move a loop out of the frame three times already.

**What it costs is a page turn every four to five seconds.** That is the trade, and it is the same one the tab view makes.

### Everything the board draws on a note

"Kann es sein, dass hammer-on, pull-off, bending etc. beim Hybrid fehlen?" It could, and they did. The sheet drew heads and fret numbers
and nothing else — no bend curves, no slide connectors, no legato arcs, no PM badges. **A view that leaves the techniques out is a view you
cannot practise a solo on**, which is the only thing this view was built for.

They cost almost nothing to add, because `_draw_slide`, `_draw_legato`, `_draw_bend` and `_draw_badge` take their geometry as arguments
and assume nothing about a scrolling board. What the sheet had to supply was the one thing it does differently: **where the technique is
going.** A hammer-on regularly points at the first note of the NEXT row, so `_sheet_next` is built over the whole song (with the layout,
not per frame — it walks every note), and `Row.x_at` clamps a target outside the row to that row's right edge. Which is what a technique
running off the end of a line should look like.

**Two things the sheet was quietly getting wrong, found while porting them.**

- **Note lengths.** The board chokes a palm-muted note to 1.3 heads and a dead one to a click, and lets a `let ring` note sound until the
  next note on its string; the sheet drew all of them at their written value. Its own comment says why that matters: *reading a chug as a
  held note is how a muted riff ends up played wrong*. And every note now leaves the same gap before its neighbour, without which a run of
  eighths renders as one unbroken ribbon. The numbers live in `sheet.py` as well as `scrolling.py` — this module may not import the
  drawing, since the drawing imports it — and a test asserts the two sets are equal, so they cannot drift apart in silence.
- **The loop was invisible.** The board shades the looped stretch; the sheet did not, so nothing said why the playhead kept going back to
  the same bar. The fret-filter trap in another costume. It shades across rows now, clamped by `x_at` at both ends, and a switched-off loop
  is shaded differently from a live one.

**Nothing is dimmed, marks included** — the same rule the heads follow. A badge that fades once its note is played takes half the record of
the run away, and the record is what the sheet is for.

The frame went from 4.5 ms to 5.2 ms median on Thunder's lead at 1920x1200, against a 16.7 ms budget.

### Three views, one key, one default

`Shift+T` walks all three — standard, hybrid, tab — because a view is chosen by LOOKING at it, so what the key has to do is keep going
until the right one is up. `O` → **View** sets which one a song opens in.

`_tab_mode` is a property over `_view` rather than a flag of its own. Two booleans have four states, three of them mean something, and the
fourth is the bug that gets shipped.

## The Screen Went Back To Being A Screen You Read Music Off

The player sent a screenshot of the hybrid view working and, in the same message, a list of what was in the way. Counted off that
screenshot: **eight lines down the left, two captions across the middle, six numbers down the right, and twenty-three keyboard shortcuts
across the bottom in two rows** — on a display whose entire purpose is to be read while both hands are on a guitar.

The rule he wrote, and it is a better one than the rule that produced the old HUD: **a line earns its place by saying something that
CHANGES and that nothing else on screen says.** Everything below follows from it.

### One line of keys, and each one carries its value

The footer is twelve entries now, and every one of them shows the state of the thing it names — the tempo you are at, the hit window in
force, the view you are in, the size you set — and **lights up when that is not at rest**. Twenty-three shortcuts with no values are not
a help system, they are wallpaper, and he read four of them.

The other thirty-odd keys moved into `H`, which is now the ONE place every bound key is written down. **The rule that every key is
documented did not go away, it moved**: `TestEveryKeyIsWrittenDownSomewhere` still reads `handle_event`'s own source, and now checks it
against `help_blocks()` — which had to be pulled out of the drawing to be readable at all. Adding a shortcut and forgetting to write it
down still fails in the suite.

### S, and everything about lining sound up

Six lines of sync arithmetic were permanently on the screen: the MIDI offset, the recording's offset, the strike-timing offset, the
fifteen sync points, the section rates, and the line telling you which keys change them. None of it is needed while playing; all of it is
needed while syncing. That is what a key is for. `S` opens it and `S` shuts it.

- **It is an OVERLAY, not room taken from the music.** The first build counted its lines in `_tab_room`, which is honest and wrong:
  less room means a smaller head, which means more bars on a row, which means different line breaks — so pressing `S` re-broke the music
  into a different page while you were looking at it. It draws over the bottom of the sheet instead, on a ground of its own, which is the
  part you are not reading while you line a recording up.
- **The warning built last session still gets out.** "The recording is guessed here, and it drifted 2128 ms where it was measured" is the
  one line that explains the picture at 3:49, and shutting it in a panel would make it invisible at exactly the moment it matters. The
  footer's `S: Sync` entry turns warning-coloured instead. One word is the whole signal.

### The tunings, on one line

`Tuning: CFA#D#GC  C#F#BEG#C#  DGCFAD  D#G#C#F#A#D#  EADGBE*` — the one being played in blue, the one it was written in starred. A tab in
Drop C played on a standard-tuned guitar is wrong on every single note and nothing else on screen says so.

The window is bounded by what a guitarist would actually do: **at most one step down** (further down is a floppy string, not a choice),
**up to three up**, ending at the standard-shaped tuning, and **never more than five**. The player's rule as written ("original in the
middle, one lower, three higher") does not survive contact with his own song — Bon Jovi is written in Standard, so there is nothing above
it and the original cannot be in the middle. What is invariant is the pair that must be there: **the tuning being PLAYED and the one it
was WRITTEN in are facts, everything else in the strip is a suggestion**, so the trim takes the suggestions first and only touches a fact
when both cannot fit.

### What went, and what was kept against instructions

Gone: the hit window, the scroll line, the frame-pacing line, the H/C/M breakdown beside the accuracy, the tempo caption in the middle,
and the view's own caption over the staff (bars-per-row, page number, zoom — all of it now beside the key that changes it).

Kept, and said out loud rather than done quietly:

- **The loop line, but only while a loop is on.** A loop silently repeating eight bars is the fret-filter trap in another costume, and no
  other line would mention it.
- **The streak**, moved to the right column under the accuracy rather than deleted — it is feedback, not chrome, and it only appears at
  three.
- **`S: Sync` in the footer**, which he did not list. A key nobody can find is a key nobody presses; that is this project's own rule.

### A second pass, from living with it

- **`U: MP3` was missing from the footer.** It is a sound you can hear or not, the same kind of switch as `B` and `Shift+B` beside it, and
  this player has already reported `U` looking removed once — when its line went quiet, the key looked unbound. A dash where there is no
  file, the same as the other two.
- **The help says what everything is set TO.** "G: hit window" is a key; "±150 ms" is the answer to the question you opened the page with.
  Every entry that names one setting now carries its current value in a column of its own — the view, the tempo, the gate, the fret limit,
  the muted strings, the tuning, each backing, the timing offset, the vsync outcome, whether the sync panel is open. It turns a page you
  read once into a page worth opening mid-song.
- **The grip cards were sitting on the top string.** On the scrolling board they hang above a lane band that starts halfway down the
  window; the sheet reaches into that corner. They are a tenth smaller now, and `_hud_top_used` counts them, so the music starts below
  them and the head shrinks to fit — the same measured-not-guessed fix the top margin needed one section up.
- **And the chord names on the sheet were unreadable.** The first build named every GROUP at 20 px, which on a song that strums sixteenths
  is twenty-four names across one row, each over the note heads. Three rules now, each from looking at the thing:
  - only where the chord **changes**, from the list built once per song — the same list the board draws from, so the two views can never
    name a chord differently;
  - **plus the chord in force at the row's left edge**, because a row whose chord started on the row above sits in front of you for four
    seconds saying nothing (the board never needed this: there the change itself scrolls past);
  - and **never a name that would land on the one before it** — measured on the player's screenshot, three changes inside a bar came out
    as "DadA/EF#", which is worth less than one name.

  The decision is `sheet_chord_names`, which takes a `width_of` callable rather than a font, so the rule is tested without a screen and
  the drawing cannot use different widths from the decision that placed them. The row's top strip grows from 20 px to 54 when the names
  are on, and `head_for_room` is told about it — otherwise the extra height is simply taken off the bottom row.

### Two things the cleanup found

- **The top margin was a constant.** `TAB_TOP_MARGIN = 150` was fitted to the old eight-line column. With three lines it left 90 px of
  empty window above the music and took it off the bottom row. It is measured now (`_hud_top_used`), the same fix `_tab_room` needed at
  the bottom for the same reason.
- **A feedback loop, caught by a test.** Putting bars-per-row in the footer's `+/-` entry made the footer longer, which can push it to a
  second line, which changes the room, which changes the head size, which changes bars-per-row. `test_a_second_frame_lays_nothing_out`
  failed with "laid out 2 times" — at 44.4 px and then 44.5. The number is not written there. The bar numbers are on the screen anyway,
  which is where a player would count them.

**And the frame got cheaper**, which was not the point but is worth recording: 300 frames of Thunder's lead at 1920x1200 went from a
5.4 ms median on the board and 7.2 on the sheet to **3.7 and 4.5**. Text is what this display spends its frame on — measured at 79 % of
one, in the chapter that built the font cache — so deleting forty text surfaces a frame is the cheapest millisecond in the file.

## Two Rows Of Music And Nothing Else

"Mir reichen 2 Zeilen des Tabs in der Mitte. Danach sollte oben und unten genug Platz sein für die Anzeigen, die aktuell reinlappen."
The page filled the window between a 56 px margin and a 104 px one, and the HUD — which is text with no ground of its own — was printed
straight over the staff. Both are right; neither could give way, because neither knew the other's size.

- **The room is measured now, from the things that take it.** `_footer_block` was split out of the footer drawing so two callers can ask
  the same question at different moments: the footer draws the block, and `_tab_room` asks how tall it came out BEFORE laying a page out.
  A constant was the obvious fix and it is the wrong one — the footer is between two and five lines depending on the window width, which
  is exactly what a constant cannot follow.
- **Two rows, taken from the rows the page actually has.** A median row spacing was tried first and measured 0.107 of the page over the
  whole song against 0.094 on the page being looked at, so the window showed two rows and a third of a third one with its beams sliced
  off at the bottom edge. `row_window` cuts through the MIDDLE of the gaps either side instead, so the boundary never falls through a row.
  The test asserts that property rather than the arithmetic: every row on the page is wholly inside the window or wholly outside it.
- **`system_pitch` went with it.** It was written for the median, replaced within the hour, and would have sat there tested and unused —
  which is the same thing `PAPER` was doing (see below) and the fault this file keeps writing up.
- **The playhead is the full height of what is shown, and 5 px wide.** Fitted to its own system it was a short mark in a tall picture and
  took hunting for, and hunting for the playhead is the one thing this view exists to spare.
- **The paper is white, and it is white in ONE place.** `PAPER` sat unused two screens above a hardcoded `background="#eeece7"` in
  `rasterise`, so the constant every comment in the file talks about was decoration and changing it changed nothing. The player asked for
  harder contrast; the reason for an off-white ground — paper does not glare — is a print argument and not a screen one.
- **And the tab label moved off the tempo.** Both wanted the centre of the top edge and the HUD is drawn second, so they read as one
  illegible line: the same complaint as the page over the staff, one row up.
**And the row stepping was wrong in a way only the player would notice.** The rule was "hold the page while the current row is anywhere
on screen, then move" — right for a window as tall as the page, and the exact opposite of what it is for with a window two rows tall. It
showed rows in PAIRS: the playhead sat in the top row for one row and in the BOTTOM row for the next, so half the song was played with
nothing visible underneath. Measured before it was changed, on Thunder, row by row: `OBEN, unten, OBEN, unten` all the way down the page.

**The row IS the state now** (`_tab_offset_for`). The offset follows from which row is being played, so it holds by itself while the
playhead crosses a row and steps exactly one row when it leaves — and the row after the one in the hand is always the one underneath it.
Same measurement after: `OBEN` seven times out of seven. `_tab_scroll_for` went with the old rule rather than sit there tested and unused.

**The price is named rather than hidden: the page now steps once a ROW instead of once every two**, about every four seconds on this song
against every eight. That is the trade the player asked for — a preview at every line break costs a page turn at every line break.


## The Songs Folder Killed The App Before It Drew A Frame

```
FileNotFoundError: 'C:\Users\Admin\Downloads\songs'
PermissionError: [WinError 5] Zugriff verweigert: 'C:\Users\Admin'
  pickhero/ui/menu.py line 162, in scan_files
```

`songs_dir` is **relative** by default, so it resolves against wherever the app was STARTED from — and a portable .exe is started from wherever
it was downloaded to. Windows reported that folder as not found, and `mkdir(parents=True)` then walked up and tried to create the player's
own home directory, which is denied. The app died before the first frame, on a machine where it had been running for weeks.

- **The folder is chosen in one place now** (`Config.songs_path`), and one that cannot be made falls back beside the settings file — the
  one directory this app already knows it can write to, because it has been writing `settings.json` there all along.
- **And `scan_files` never raises.** A folder it cannot read is an empty list and a line saying which folder and why. A screen saying "no
  songs here" is a screen; a traceback is not.

## No Offset Can Repair A Tempo

"Es ist schon am Start um ca. 3 Sekunden daneben. Mit einmaligem Anpassen und Syncpoint ergänzen, klappt es nur am Anfang. Danach läuft es
wieder auseinander. Der Song dauert bei YouTube 4:13 und das ist genau mein MP3. In der App zeigt es mir 4:55 an."

That last sentence is the whole diagnosis, and it is a subtraction. Measured on the file:

    80 bars, every one of them 3.692 s long, written tempo 65 BPM
    4:55 of tab against a 4:13 recording
    ratio 1.163 -- the tab is 16.3 % slow, and the record is really at 75.6 BPM

**An offset moves the whole song by a constant; it cannot repair a rate.** That is exactly what he described: one sync point lines the
start up, and forty-one seconds of error accumulate over the rest.

**And nothing in this app could fix it either.** Every bound here was fitted for a recording of the SAME performance:

| | bound | needed |
|---|---|---|
| `autosync.MAX_DRIFT_RATE` | 5 % | 16.3 % |
| `syncmap.MIN_RATE` / `MAX_RATE` | 10 % | 16.3 % |
| `config.MIN_MP3_RATE` / `MAX_MP3_RATE` | 10 % | 16.3 % |

A tab written at the wrong tempo is a different fault from a band drifting, and widening the bounds to swallow it would let back in the
thing they were fitted to reject -- Godsmack's staircase of wrong-chorus matches fits a straight line at −12 %.

**And the first version of this chapter got the cause wrong**, which is worth keeping. It read the 16.3 % as tempo and said so. Then
Songsterr's reply for the same song arrived: **it times 72 bars where the tab has 80**. Eight bars of that tab are not in the recording at
all, and once they are taken out only about 3 % is really the tempo — inside every bound above. Saying "the tab is 16 % too slow" would
have sent the player to change a tempo that is very nearly right.

Two causes, one number, and no way to tell them apart from lengths alone. **So the line names both** and says what could settle it.

**It is said rather than corrected**, as the numbers to act on. `written_tempo_gap` reads the bar grid: when every bar is the
same length the file is written at one tempo, and the ratio against the recording says what that tempo would have to be. "This tab is 4:55
and the recording is 4:13 — either the written 65 BPM should be about 76, or the tab has 16 % of music the recording does not." A file that
carries tempo CHANGES gets no answer at all, because one number cannot describe it and a wrong one would send the player to fix something
that is right.

**What can repair it is Songsterr's bar map**, which is per bar and absorbs any tempo error by construction — the panel says so, with the
two keys. This is the song that feature exists for.

## One Hold Of Escape Was One Press Too Many

"ESC reagiert oft zu sensibel und ich fliege aus dem Song und die App schließt sich sofort."

`pygame.key.set_repeat(300, 40)` is one **global** setting for every key, and this file has now paid for it three times: a short press on
PgDn walking the practice speed from 100 % to 50 %, escape leaving the song AND closing the app on one hold, and — reported later —
**space**: "Ich klicke space, es zählt von 2 auf 1 und stoppt. Ich drücke nochmals space und es läuft."

Space is the worst of the three, because the repeats arrive while the frame is STALLED on loading the recording and are then drained
together: an even number of them and the song is paused with no sign of why. Escape crosses two screens that do different things — in the
song it goes back to the list, on the list it closes the app — so holding it for **340 milliseconds** does both.

Two guards, and they fix different halves:

- **A toggle never repeats** (`NEVER_REPEAT`: escape and space), guarded in `App._process_events` rather than on each screen — it is the one door every screen's events come
  through, and a screen added later would otherwise have to remember. A KEYDOWN that arrives while escape is still physically down is
  dropped; the KEYUP re-arms it. Only the toggles: the arrows, the tempo and the size keys want their repeats, and taking those would be a
  different bug.
- **The song list does not close on the first press.** It says "Press ESC again to close MySician" and waits for a second, deliberate one.
  Anything else in between disarms it, because a screen that stays armed is a trap set an hour ago. Escape is the way back out of the
  song, the settings, the tuner and the device list, so the player arrives on that screen with it already under their finger.

## A Break Is Not An Outlier

"Ich lade den gleichen Song und das identische GP-File, habe aber Probleme, dass es Sync ist." So it was measured, on the three songs to
hand, before anything was touched:

| | usable windows | covered | what the map claimed |
|---|---|---|---|
| Bon Jovi | 32 of 42 (76 %) | 0:00–3:56 of 4:26 | -0.76 % drift, 2.1 s of correction |
| Godsmack | **11 of 47 (23 %)** | **0:00–1:26 of 4:56** | +1.91 % drift |
| What's Up | **5 of 41 (12 %)** | 0:00–3:08 of 4:20 | -3.02 % drift, 5.1 s of correction |

**And the filter throwing them away was the wrong one.** Not the margin filter, which drops a window that cannot tell one chorus from
another — that took 4, 12 and 17. The **outlier** filter took Godsmack from 35 to 11 and What's Up from 24 to 5, with its tolerance pinned
at the three-second ceiling, which means the readings genuinely disagreed by seconds.

**They disagreed because the curve has STEPS, not a slope.** Godsmack's raw readings: `1:06 → +11.19 s`, `1:36 → −1.89 s`. Thirteen
seconds, in thirty. That is not a recording running fast; that is thirteen seconds of music one of them has and the other does not — a
repeat, an added bar, or a window that matched the wrong chorus.

**A single straight line was fitted to the whole song**, so everything after the first step was, correctly by its own logic, an outlier.
The line described the first minute and the remaining three and a half were extrapolated from it.

### What changed

Three filters now, answering three different questions:

- the **margin**: this window could not tell one chorus from another;
- the **breaks**: the two stopped being the same piece of music HERE, so stop fitting and start again;
- the **residual, within a section**: this one window disagrees with the others around it.

A break is told from an outlier by what drift could do. Neighbouring windows are 6 s apart and drift is bounded at 5 %, so drift can move
them by 0.3 s; a jump past a second is not a speed. **And a break is the curve moving and STAYING moved** — a section shorter than four
readings and thirty seconds is folded back into the one beside it, because a one-window spike that comes straight back is an outlier and
splitting there strands the good readings after it. Measured: Bon Jovi's outro has five such spikes of about 14 s, and splitting at all of
them cost 12 readings and a minute and a half of coverage.

| | usable windows | covered | map vs its own readings |
|---|---|---|---|
| Bon Jovi | 32 of 42 | **0:00–3:55 (89 %)** | 3 ms median, 24 ms worst |
| Godsmack | **29 of 47** | **0:00–4:55 (100 %)** | 0 ms median, 24 ms worst |
| What's Up | 5 of 41 | — | **nothing stored** |

**What's Up is the important row.** Four chords repeated for four minutes; its windows match +9.9, −34.4, −6.2 and +21.1 s. It used to
store a map claiming −3.02 % drift and 5.1 s of correction, **and that map looked exactly as measured as a good one**. A reading that
keeps under a third of a song's windows is now refused outright and the panel says so, with what to do instead. Not thin — wrong.

**And where the two part company is named**, because "29 of 47 windows usable" is a number nobody can act on and "1:29 (−8.8 s)" is a
place to put a point.

### And the loudest finding was not in the algorithm at all

**Thunder's tab is 6:21 long and the recording is 4:40.** 248 bars at 156 BPM against a 4:40 file — a 27 % difference. The tab the player
replaced it with is 4:40 in 91 bars at 78 BPM, and matches the recording **to a tenth of a second**. They are two different transcriptions
of one song, and no offset, rate or map can bridge that.

A whole session went on Thunder's sync — the drifting solo, the extrapolation past 3:22, the `Shift+S` workflow, two real bugs in
`SyncMap` — and **nothing on screen ever compared the two numbers**. The check is one subtraction. It is now the first thing the listening
says, in different words from every other verdict, because it is the only one that means "go and get another file" rather than "place a
point".

Measured across every song the player has, with the new Thunder tab in place:

| tab | tab length | recording | apart |
|---|---|---|---|
| What's Up | 4:55 | 4:52 | 1 % |
| Bon Jovi | 4:27 | 4:30 | 1 % |
| Godsmack | 4:58 | 4:50 | 3 % |
| Kid Rock | 5:11 | 5:21 | 3 % |
| Thunder (old) | **6:21** | 4:40 | **27 %** |
| Thunder (new) | 4:40 | 4:40 | **0 %** |

And with the right tab, Thunder needs almost nothing from the map: **39 of 44 windows usable, 0:00–4:37 of 4:37 covered, offsets between
−1.4 s and −0.7 s** — seven hundred milliseconds of drift over the whole song, against the 13 seconds the old tab implied.

**One more thing this found, in the reporting rather than the measurement.** A section can be big enough to fit and still hold a wrong
match at its edge: Thunder's first 36 s hold six readings, two at +1.2 s and four at −23.5 s. Comparing raw section endpoints reported a
24.9 s break that the stored points do not contain, and **a break the map does not have is a line that lies**. Breaks are read off the
readings the map is built from.

### Taking Songsterr's own map — and what it turned out to be worth

The player gets almost all his tabs from Songsterr, so the obvious move was to take their per-bar map instead of measuring one. Their meta
and video-points replies for Thunder, read for real:

- `api/meta/2333598` → `revisionId`, and `aiGenerated: true` — this tab was transcribed FROM the audio, which is why it fits it.
- `api/video-points/2333598/3088787/list` → three entries, **91 points each**, against a tab of **91 bars**. Bar lengths run 3.02 to 3.18 s
  against a written 3.077, so it is real per-bar timing and not a tempo restated.
- The three videos carry **the same curve shifted by a constant** — exactly −25.15 and −23.25 s on all 91 points, to the last decimal. So
  the shape is the data and the video is irrelevant; the constant belongs to whatever recording the player actually has, and is found by
  warping the tab through the map and measuring what is left (`align_to_bar_times`), which reuses the listening rather than inventing a
  second way to compare two things.

**And then it lost.** Measured on the player's own Thunder recording, scored only on readings each map had NOT been fitted to — our own
map is built from these windows, so an in-sample number would have flattered it by a factor of five:

| | held-out half 0 | held-out half 1 |
|---|---|---|
| one offset, no map | 192 ms | 200 ms |
| **Songsterr, 91 bar times** | **80 ms** | **92 ms** |
| **our own listening** | **16 ms** | **8 ms** |

The prediction going in was that Songsterr's map would beat the measurement and make the DTW work unnecessary. **It is five to ten times
coarser.** Their points are timestamps into a YouTube re-upload, and their own transcription pipeline carries its own error; our chroma
correlation against the actual file has neither problem.

**And What's Up broke two things in it that Thunder could not have.** Thunder's three videos carry one curve shifted by a constant, so any
entry would do and the first build took the longest. What's Up's six entries carry **three different timelines**: the main video has 72
points over 253 s, the backing tracks 78 over 278 s. They are different cuts of the piece, and **the longest is the wrong one** — the
player's recording is 4:13. So every candidate comes back from the fetch now and each is tried against the recording; the one the windows
agree about wins. Which of them is the right video is a question only the recording can answer.

**The second thing it broke turned out to be a rule, not a revision.** None of the three timelines matched the tab's 80 bars, and the
first answer was "your tab is an older revision — download it again". He did, and got the same 80 bars. The tab is not wrong:

    80 bars, of which bar 0 and bars 70-79 carry no note on ANY pitched track
    the last note is at 4:15, and the clock runs to 4:55
    Songsterr times the 72 that have music in them

A Guitar Pro export is regularly **padded out to the end of the sheet**. So a map of fewer bars than the tab is accepted when every bar
past it is empty (`_covers`) — and on What's Up that map fits: it picks the 72-point main video over the 78-point backing one, **21 of 39
windows agree with it to 17 ms**, and the offsets run −0.1 s to +11.2 s, which is the 3.5 % the recording really runs at. Refusing on the
count alone threw away a map that was right.

**And the clock now explains itself.** "This tab runs to 4:55 but its last note is at 4:15 — 10 empty bars at the end" is in the sync
panel, because that number is the one he compared against YouTube three times before concluding he had the wrong file.

**The old finding, for the record:**  a map with MORE bars than the tab, or one that stops inside the music, is still refused rather than stretched — stretching
it would be silent and wrong everywhere after the first difference — and the line names the counts on offer, because "Songsterr times 72 or
78 bars and this tab has 80" is a thing to act on and "a different revision" on its own is not.

**What it is for is the songs the listening cannot read at all.** What's Up is four chords repeated for four minutes and its windows match
+9.9, −34.4, −6.2 and +21.1 s — a made map does not care that a song repeats itself, and a windowed search can do nothing else. So the
listening is asked first and Songsterr only when it comes back empty, and the panel never dresses one up as the other: it says which
measurement is under the song, and that this one is the coarser.

**And the player decides which measurement runs, not the app.** The first build made the bar map an automatic fallback — used when the
listening judged ITSELF unreadable and never otherwise — and then added `Alt+S` as a one-off override on top. He read that exactly right:
*"Ich habe das Gefühl es entscheidet noch immer selbst."* He was right. Asking the listening whether the listening worked is a circle with
the player outside it, and a one-off override does not let him back in — it just lets him interrupt.

So it is one setting with four answers, per song, and `Ctrl+S` obeys it:

| | what Ctrl+S does |
|---|---|
| **listen, then Songsterr if that fails** | the old behaviour, and the default, because a song nobody has decided about has to do something |
| **listen only** | never asks Songsterr, even with nothing to show for it |
| **Songsterr's bar map only** | never listens, even when the map does not fit |
| **by hand only** | Ctrl+S does nothing and says so |

`Alt+S` walks the four and names the one it lands on. Neither of the two "only" answers falls back to the other: falling back would be the
app deciding again, which is the thing that was reported. The sync panel names the source in force, the stored link, and the key that
changes them — it was written down only in the help page.

The first build made it an automatic fallback only — used when the listening judged itself
unreadable and never otherwise — which left a player who can HEAR that the listening got it wrong with nothing to press. "Where it works"
is a judgement the listening makes about itself, and that is not the last word. `Ctrl+U` takes the link off the clipboard — the app has no text field and building one for a URL somebody just copied out of their browser
is a screen nobody wants. A map whose bar count differs from the tab's is refused rather than stretched: it is a different revision, or the
repeats written out differently, and stretching it would be silent and wrong everywhere after the first difference.

### One ENTER Fetches The Song, Not The Tab

*"Wenn wir beim Downloader explizit Songsterr nutzen (keine anderen Dienste) und dort das GP holen und die Youtube-ID und die Sync-Infos,
dann würde mir das einen Haufen Arbeit sparen. yt-dlp fände ich praktischer. Ich gebe die Daten nicht weiter und nutze sie nur lokal
offline."*

**Learning a new song was four jobs, and Songsterr answers all four with one reply.** The download screen fetched the tab and stopped;
the player then found an MP3 somewhere, pasted the Songsterr link back into the app with `Ctrl+U`, and pressed `Ctrl+S` hoping the
listening would read a song that repeats itself. On What's Up it does not — its windows read +9.9, −34.4, −6.2 and +21.1 s and mean none
of it. But `api/video-points` had been carrying the answer the whole time: a timestamp per measure **and the YouTube id of the recording
those timestamps were made against**. Three of the four jobs were being done by hand next to a reply that already contained them.

So `ENTER` on the download screen now fetches:

| what | where it lands | why there |
|---|---|---|
| the tab | `songs/<name>.gp5` | unchanged |
| every video's bar map | `songs/<name>.songsterr.json` | **beside the tab**, so copying the songs folder to the second laptop carries the sync with it — the same rule `mp3_path_for` already follows for the recording |
| the Songsterr id | settings, against the tab's stem | which is what `song_key` is, so `Ctrl+U` is not needed |
| the audio | `songs/<name>.mp3` | same stem, so `mp3_path_for` finds it after a move |

**The video is chosen by `feature: null`, not by length.** Songsterr marks the recording the tab was written from with a null feature;
everything else is a backing track, a live cut or somebody's cover, and those are *different recordings of the song* rather than the same
one shifted. Thunder hides this — its three videos carry one curve shifted by exactly −25.15 and −23.25 s, so any entry would do. What's
Up does not: **72 points over 253 s in the main video against 78 over 278 s in the backing ones**. The first build took the longest and
handed that song the backing track. `main_entry` takes the marked one; `candidates_for` puts it first and keeps the rest, because the
recording on disk is not always the video it was pulled from and the fit is what decides.

**And pulling the audio from that video collapses the hard part of the sync.** The bar map is in video time. Up to now the app had to
find the constant between video time and whatever MP3 the player owned, by listening — which is the step that fails on a repetitive song.
Take the audio from the video the points were made against and there is no constant to find; there is an MP3 encoder delay of about
26 ms, well inside what the fit reads anyway. The fit still runs rather than the offset being assumed to be zero, because the player may
already have had a recording or replaced the one that came down.

**ffmpeg is bundled, and that is not optional.** yt-dlp downloads what YouTube serves — m4a (AAC) or webm (Opus). The app plays through
SDL_mixer, which decodes MP3, OGG, FLAC and WAV, and *neither of YouTube's two formats is on that list*. Without ffmpeg the audio lands on
disk and silently will not play, which is this project's oldest failure mode wearing a new hat. `tools/fetch_ffmpeg.py` puts one in
`tools/` at build time and the spec bundles it; the second laptop gets the .exe and needs nothing installed. When the fetch fails the
build still succeeds and the download screen says **"ffmpeg was not found"** in words — `missing()` returns which half is absent, because
"ffmpeg was not found" and "this video is private" send the player to completely different places.

**Every step reports itself, and only the tab is required.** A song with no video on Songsterr is still a song to practise; it just syncs
the way it did before. So `grab_song` never raises for a missing piece — it returns a `Grab` whose `notes` say what came and what did
not, and the screen holds those lines until a key is pressed. A song that quietly arrived without its recording looks exactly like one
that arrived with it, until the player is standing in front of it with a guitar.

Two smaller things that were bugs waiting to be reported:

- **The finished download nudges the event loop.** Every state change on that screen is read inside `handle_event`, and `handle_event`
  only runs when pygame has an event. A player who takes his hands off the keyboard while a song downloads generates none, so the
  finished download would sit there until something was touched — indistinguishable from a hang. A posted `USEREVENT` closes it.
- **The bar map is read from disk before the network.** `_songsterr_bar_times` asks the cache first, so a song fetched in the app syncs
  with no network at all and pressing `Ctrl+S` twice does not ask Songsterr twice. A link pasted by hand still goes out — and what comes
  back is written to the same place, so that song is offline from the second press on.


### Three Things The First Download Round Got Wrong

The player ran it on three songs the day it shipped, and each one found something.

**1. `.gp5` was a lie on disk.** *"Why does it download a gp5 and when I use songsterr-downloader.com I get a gp?"* — because `download_gp5`
named every file `.gp5` whatever Songsterr actually held. It never broke anything, which is why it survived: the loader dispatches on the
file's **content** (`zipfile.is_zipfile`, then the GPX magic), so a Guitar Pro 7 file called `.gp5` opened perfectly. But it is the wrong
file to hand to Guitar Pro itself, and the extension is now taken from the source URL. The old test asserted the bug — it mocked a source
ending in `.gp` and then asserted the output was `test.gp5`.

**2. A failed download opened a browser at a page that does not exist.** `https://www.songsterr.com/a/wsa/{id}` is not a Songsterr URL.
Theirs are `<artist>-<title>-tab-s<id>`, and they redirect any slug with the right `-s<id>` tail to the right page — so the slug is built
from the search result, which already carries both names. And the reason is now **returned rather than swallowed**: `_source_of` says
whether Songsterr did not answer, has no revision, or **holds no Guitar Pro file for this tab at all** — not every tab on Songsterr has a
source file behind it. Those are the same empty result and completely different problems, and the first build reported all of them by
opening a window with nothing in it.

**3. "No audio: ffmpeg was not found" is half a message.** It names the missing thing and not the fix, to a player who is not a developer,
and it came straight back. What the fix IS depends on what he is running, and the .exe is the case that matters: there is no source tree in
it to run a script from — but `_search_folders` looks **beside the executable**, so `ffmpeg.exe` dropped next to `MySician.exe` works with
no rebuild at all. `missing()` says that sentence when frozen and `python tools/fetch_ffmpeg.py` when not. Telling somebody to `pip
install` inside an .exe is advice they cannot act on.

### DEL Deletes The Song, Not The File

*"Ich brauche eine Möglichkeit Tabs inkl. allem (außer History) zu löschen."*

A tab stopped being one file the day the download screen started writing a bar map and an MP3 beside it. Deleting it in Explorer leaves
both behind **and nine settings** — speed, recording path, offset, rate, Songsterr id, sync source, anchors, transpose, backing offset —
and `song_key` is the tab's STEM and nothing else, so the next song that takes the same name inherits all of it.

Two rules make it safe rather than merely thorough:

- **Only what sits beside the tab, under the tab's own name.** The recording the player picked out of his Downloads folder is his. The one
  this app downloaded is next to the tab and named after it. Same folder **and** same stem, or it is not touched — so `whatsup_original.mp3`
  and `AC-DC - Thunder (live).mp3` both survive a delete of `AC-DC - Thunder`.
- **The practice history stays.** `progress.py` and `practice_log.py` are a record of what he DID, and deleting a file does not undo an
  evening of playing it. He asked for that by name.

**The settings are found, not listed.** Every per-song setting is a dict named `song_something`, so `forget_song` walks the dataclass
fields. A hand-written list would be correct on the day it was written and silently wrong the first time a tenth was added — and the way
that failure shows up is a deleted song's sync landing on a different song months later, which nobody would trace back to here.

**DEL asks first, and the question counts the files.** It sits one row from the arrow keys and it cannot be undone, so the first press
names the song, says how many files will go, and says the history is kept; the second does it. **Any other key cancels** — and the armed
song is dropped on every keypress that is not DEL, so arming on one song, moving down, and pressing again asks about the new song rather
than deleting the old one. Not while the search box is open: there DEL is what somebody reaches for to fix a typo. Afterwards the list is
re-read **from the disk**, because a file that would not delete is still there and a list that quietly dropped it would be claiming a
delete that did not happen.

### Picking A Recording Is The Whole Job Now

*"Wenn ich nur noch das MP3 laden und mit sh+U im Song verknüpfen muss und die Songsterr Sync schon im Song ist, dann reicht das vorläufig auch."*

YouTube's bot check killed the audio half of the download — *"Sign in to confirm you're not a bot"* — and the answer to *"soll ich mich einloggen?"*
is no: `--cookies-from-browser` makes automated downloads run **as a named account**, and what gets restricted when the abuse machinery fires again
is his Gmail. So the recording goes back to being his, and the only thing that has to be automatic is what happens after he picks it.

**Shift+U now runs the sync, if there is none.** The recording is new, it has no sync points, and there is exactly one thing to do with it. Two
guards decide when it stays out of the way:

- **Points already there are the player's own work** — a Shift+N/M nudge, a Shift+S pin, an anchor set by ear in the middle of a song that drifts —
  and re-picking the same file is exactly what somebody does after moving it. `_forget_sync_for_new_recording` has already cleared them when the
  FILE genuinely changed, so an empty list means there is nothing of his to lose.
- **Sync by hand is a choice, not a gap.**

And no link needs pasting, which is the other half of what he asked for: the bar map is cached beside the tab at download time and
`_songsterr_bar_times` reads the disk before the network. `Ctrl+U` is now only for a tab that arrived some other way.

**One thing he asked for that was already true.** He asked for it to "take the Songsterr route". `auto` — the default — already does, and does it
better: it listens first (8–16 ms on his own Thunder recording) and falls back to the bar map (80–92 ms) only when the listening reads nothing.
Forcing Songsterr would make every song where the listening works five to ten times coarser to fix the songs where it does not. `Alt+S` is still
there for a song he can hear it got wrong.

### The Search Box Takes A Link

*"Kann ich in der Suche auch direkt den Songsterr Link eingeben, wenn ich dort meine Wunschversion gefunden habe, oder die ID?"*

He has already chosen his version over there. Searching for its name hands him the other four transcriptions of the same song to pick from again —
the work he did on Songsterr's own site being asked for a second time. So `find` reads three shapes:

| typed | what comes back |
|---|---|
| a Songsterr link | **one** answer, the song it names — searching for a URL's text finds nothing anyway |
| a bare number | **both**, the id first and marked ★ |
| anything else | the search, unchanged |

**A bare number is genuinely ambiguous and not rarely**: `2112` is a Songsterr id and a Rush album, `1979` is one and a Smashing Pumpkins single.
Reading it as an id only makes a song named after a number unfindable. Reading it as text only makes typing an id pointless. So it is both, in the
order that costs nothing to be wrong about.

A link Songsterr does not know comes back **empty**, never as the search's results wearing the link's clothes — that would have the player download
a song he did not ask for.

**And Ctrl+V, because a Songsterr URL is 60 characters of slug nobody types.** Without it, "paste a link" means reading it off the screen and
copying it in by hand, which is not pasting. The tkinter that does it moved to `ui/clipboard.py` so the search box and `Ctrl+U` share one seam. The
box draws the **tail** of a long query: the id lives on the end, and a caret that has walked off the right edge looks like a box that stopped
taking input.

### Two Things Called Sync In One Panel

The player opened the sync panel, read it, and reported the recording as out of sync:

```
SYNC   this tab runs to 4:55 but its last note is at 4:03 — 14 empty bars at the end
SYNC   source: listen, then Songsterr if that fails | Songsterr 2407981 stored | Alt+S changes it
Sync: -108 ms  — play on, still measuring
```

*"Ist das die bar map? Es ist leider nicht Sync."*

**Not one of those three lines is about the recording.** The first is the empty-bars explanation for the clock. The second is a setting. And the
third — the one he read as "still lining the recording up" — is the **strike-timing offset**, which measures how late HIS PLAYING arrives through
the microphone and the sound card, and which touches the recording not at all. It was called `Sync:` and it sat one line under the recording's own
sync in the same panel. Two different things called Sync, eight pixels apart. That is the panel's fault, not his: it is now `Your playing: +0 ms (K)`.

**And the panel could not say whether the recording was lined up at all.** It listed the source and the stored Songsterr id and stopped, which reads
like everything is set — so a song that had been measured and a song that never had looked identical. `_recording_sync_line` says which:

- no recording → nothing, there is nothing to line up
- a recording and no points → `the recording is NOT lined up yet — Ctrl+S measures it`
- points → `lined up: 3 sync points out to 4:12   |   Ctrl+S measures again`

A live measurement still outranks the stored count. This is the project's own rule turned on its own status panel: a state that cannot be read off
the screen is indistinguishable from a broken one, and the player spent a round asking the app a question the app was already holding the answer to.

### R Renames The Song, Not The File

*"Brauche eine Rename Song Möglichkeit in Taboverview."*

A tab from Songsterr arrives called `Thunder - Love Walked In v3`, and the player wants it tidy. But **the name is the song's identity**: `song_key`
IS the tab's stem. Renaming in Explorer leaves the speed, the recording, the sync points, the Songsterr id, the transpose **and the practice
history** behind under the old name — looking like a rename that wiped the setup, with the leftovers waiting to be inherited by the next song that
takes the old name.

So `R` moves all of it: the tab, the bar map, the audio beside it, every per-song setting, and the progress record. `Config.rename_song` walks the
dataclass fields exactly as `forget_song` does — a hand-written list would be wrong in the same way, and the failure shows up as one setting quietly
lost months later.

Three things it refuses to get wrong:

- **A name already taken moves nothing**, checked before the first `rename()`. A half-done rename leaves the tab under one name and its recording
  under another, which is worse than not renaming at all.
- **A character Windows refuses never reaches the box.** A colon is a rename that dies with an error nobody can read; the place to say so is the
  keypress, not the ENTER.
- **The editor owns every key while it is open.** Otherwise typing a name is a minefield — `d` in "Thunderstruck" arms the delete, `f` opens the
  search, ESC leaves the song list.

Afterwards the list is re-read from the disk and the cursor follows the song to its new name.

### A Text Box Is A Text Box, Wherever It Is Asked About

*"Während ich im Rename bin, darf ich gewisse Buchstaben nicht drücken. Mit O komme ich direkt in Settings."*

The song list's own handler gave the rename editor every key. **The App never got that far.** It consumes `D`, `O`, `S`, `G` and `U` before
handing the event on, guarded by `not self._menu.is_searching` — a test written when the search box was the only text field that existed. A second
text box arrived and was not part of it, so `o` in the middle of typing a name opened the settings screen.

The fix is not a longer condition, it is a better question: `is_typing` is true for **any** box that owns the letters, and the App asks that. A text
field added later is covered by being a text field rather than by somebody remembering to come back here.

One deliberate difference between the two boxes: **Shift+U reaches the tuner while searching and types a capital U while renaming.** Tuning up in
the middle of hunting for a song is exactly when that shortcut is wanted, and it was asked for. In a name it is a letter — **U2 is a band**, and a
box that swallows a character is one the player cannot finish a name in.

### Opening The Song Is The Whole Setup

*"Beim jetzigen Versuch wurden MP3, Barmap und GP geladen. Im Song hat das MP3 gefehlt und ich habe es von Hand zugewiesen. Danach hat der Sync
automatisch funktioniert. Ich hätte gerne, dass das beim ersten Öffnen automatisch passiert."*

Two halves had to be joined, and the first one is the more interesting failure.

**The download writes a settings entry pointing at the audio — and an entry is a NOTE ABOUT a file, not the file.** Rename the tab, re-download it
under a different suffix, carry the settings to a second machine: the entry now points at nothing while the recording sits right beside the tab
under the tab's own name. So `_adopt_audio_beside_tab` looks there when no recording is assigned. **Same folder, same stem** is the rule everything
else here already follows — it is what the download screen writes, and what `mp3_path_for` already falls back to after a move — and it needs no
settings to be correct. A recording the player chose himself is never replaced.

**And then it measures itself**, on the first `update()` rather than inside `__init__`: the screen has not been drawn yet, so a measurement started
from the constructor says *"listening to the recording…"* into a frame nobody has seen. Same pattern as the file chooser, same reason.

Two guards, both of which are bugs if they are missing:

- **Once.** A measurement that finds nothing leaves the points empty, and re-arming on that would measure again every single frame for the rest of
  the song.
- **`Ctrl+S` disarms it.** The first thread has finished by the time the next frame runs, so the already-running guard does not catch it, and the
  whole measurement runs a second time.

### A Bar Map That Is Consistently Early Is A Constant

Songsterr's map is 80–92 ms against this player's own recording where the listening is 8–16, and that error is **mostly a constant** — the map's
shape is right and its zero is not. `SyncMap` keeps `base_offset_ms` as a separate term on top of the points for exactly this: `Shift+M` moves the
recording 10 ms later, it is stored per song, and it survives the map being measured again. Seven presses is the answer to "70 ms too early", and
the reason it is the right answer rather than a workaround is that a constant error has a constant correction.

### The Rule Was Written Down And Then Applied By Halves

*"Jetzt habe ich vor dem ersten Öffnen ein Rename gemacht. Das MP3 wurde wieder nicht gefunden."*

One chapter earlier this project wrote **"the file on disk outranks the note about it"** and then checked it only for an EMPTY note. Here is the
sequence that exposed the other half:

1. the download writes `song_mp3_paths["Thunder v3"] = "<songs>/Thunder v3.mp3"`
2. `R` moves the file to `AC-DC - Thunder.mp3` and carries the entry to the new key — **with the old file name still inside it**
3. `mp3_path_for` finds nothing at that path, falls back to the same NAME in the songs folder, finds nothing there either, and hands back the dead
   path
4. `_adopt_audio_beside_tab` sees a non-empty answer and stands down

A note pointing nowhere is **not "no recording"** — it is a wrong one, and the two fail differently. The adoption now runs whenever the stored path
does not exist, and `rename_song` repoints the entry at the file it just moved. Both, because one is the correct fix and the other is the one that
holds when something else gets it wrong.

The one thing adoption must not fight is `mp3_path_for`'s own fallback — a settings file carried to a second machine points at a folder that is not
there, and finding the same name in the songs folder is a LIVE answer, not a leftover.

### A Tab Songsterr Will Not Hand Over Is Not A Dead End

*"Mit dem Songsterr Downloader konnte ich das GP downloaden selbst. Das kam auch bei den nächsten 3 anderen Songs."*

Four songs in a row answered `Songsterr holds no Guitar Pro file for this tab` while a third-party downloader fetched all four. **So the file
request is the part that fails, and it is one request out of three.** The bar map is a separate call and it still works.

So the download carries on. The bar map is fetched and written **next to where the tab would have gone**, and the screen says the NAME to give a
tab fetched somewhere else:

```
Songsterr holds no Guitar Pro file for this tab (revision 88 has: ...)
Its bar map (124 bars) is saved and waiting.
Fetch the tab yourself and save it in your songs folder as:
Billy Talent - Swallowed Up By The Ocean.gp5
Then the sync is already set up.
```

Same folder, same stem is the only rule anything here follows, so the useful thing to say is the name.

**And the message quotes the reply.** Reaching it means the revision WAS fetched and parsed and simply has no usable `source` — which is a
different thing from the network being down. Whether the field moved, was renamed, or is genuinely absent for these tabs is a question the reply
itself answers, so the keys it DID carry are named. That turns the next screenshot into the answer instead of another round of guessing at an API
nobody here can reach.

### The Newest Revision Has No File, And An Older One Does

The player sent the two replies for Papa Roach 14907 and they answer the question outright:

| revision | what it carries |
|---|---|
| 6688469 (latest) | `aiGenerated, artist, audioV4, audioV4Midi, audios, tracks[].hash` — **no `source` key at all** |
| 5981666 (`prevRevisionId`) | the same, plus **`"source": ""`** |

**Songsterr keeps these tabs in their own format** — a hash per track, an `audioV4` mix — and a Guitar Pro file exists only where somebody uploaded
one. That is why a third-party downloader can produce a `.gp` for a tab this app cannot fetch: it *builds* one from Songsterr's own data rather than
finding a file.

But every revision carries `prevRevisionId`, so **the history is walkable**. `_source_of` follows it up to `SOURCE_HOPS = 8` and takes the first
revision with a real `source`. Eight because the walk costs one request per hop and a tab edited daily for a fortnight is not the same tab any more
— past that the bar count moves, and a bar map whose count differs is refused anyway, which is the safety net this leans on.

**And the revision comes back with the URL, because the bar map has to come from the same one.** A file from revision N timed by a per-bar map from
revision N+6 is two different edits of the song pretending to be one, and the drift would read as a bad measurement rather than as a mismatch.
`grab_song` asks for the found revision's video points first and falls back to the latest, since an old revision may have none at all.

Two details that are the difference between working and nearly working: **`"source": ""` is not a file** — a key that is present and empty has to
fail the same test as a missing one — and the walk stops on a revision it has already seen, because a history that points at itself is a loop.

When the walk finds nothing it says how far it looked: *"8 revisions checked back to 4100000 (it has: aiGenerated, artist, audioV4…)"*. "No file"
said of one revision is a guess; said of eight it is a finding.

### Ctrl+C Copies The Screen

*"Kannst du etwas bauen, damit ich Text am Screen mit der Maus markieren und kopieren kann, oder wenigstens ein generelles Ctrl+C?"*

Asked after reading a 200-character error off a **photograph of his monitor** and typing it back here to be diagnosed. Mouse selection would mean
laying out every string as characters with hit boxes, in a window whose entire job is drawing music. Copying the whole screen costs one key and
answers the same need.

It lives in `App._process_events`, next to the key-repeat guard and for the same reason: **that is the one door every screen's events come
through**, and a screen added later would otherwise have to remember. Each screen offers a `copy_text()`; one without it says so rather than
copying an empty string and looking like a key that does nothing.

**No text-box exception.** The first version guarded it behind "is anything being typed" — which would have switched it off on the search screen,
the exact screen it was asked for. Ctrl+C is a modified key and no box on any screen wants it.

One thing the tkinter needs: its clipboard is emptied when the interpreter is destroyed, so the hidden window is kept alive for one `update()`
after the append. Without it the copy appears to work and the paste comes back empty.

### A Filter Box That Cannot Spell Metallica

*"Im Filter kann ich keine Favoriten setzen. Bitte Sh+M für Favorit und Str+M für kein Favorit. Möglich?"*

The keys are **Ctrl+M** and **Ctrl+Shift+M**, not the pair he named, for one concrete reason: **`Shift+M` is how a capital M is typed.** A filter box
where it means "favourite" cannot spell Metallica — the same lesson U2 taught the rename editor one chapter ago. A Ctrl combination produces no
character at all, so it works mid-word and steals nothing from the box. It also leaves `Shift+M` on the favourites-only filter, where it already
was: the alternative was one key meaning two things depending on mode, which is the fault this file has now paid for three times.

**Set and unset, not toggle.** `M` toggles, and a toggle is the wrong shape here: while the box is open the note under it is the last thing being
read, so pressing a toggle means finding out afterwards which way it went. Two keys that SAY what they do can be pressed without looking, and
pressing one twice is harmless — it answers *"Already a favourite"* rather than quietly undoing the press before it.

`_toggle_favourite` is now one line through `_set_favourite`, because two copies of the same work drift apart.

### The Default Moved Because The Ground Moved

*"Ich hätte gerne standardmäßig Songsterr map nehmen, wenn noch nichts hinterlegt ist. Wenn schon was da ist, dann lassen wir es so."*

A week earlier this file argued the opposite, with numbers: measured on the player's own Thunder recording the listening is **8-16 ms** and the bar
map is **80-92**, so `auto` — listen first, fall back — produced the better answer. That was right, and it is now wrong, because what the default
governs changed underneath it.

Back then the measurement ran when `Ctrl+S` was pressed: a deliberate act, worth waiting for the finer answer. It now runs **by itself when a song
opens**, and the comparison is no longer "which measurement is more accurate" but:

| | cost at song open | can it fail |
|---|---|---|
| bar map | a **file read** — cached beside the tab at download time | no |
| listening | **seconds of FFT** on a worker thread, as the player reaches for the space bar | yes, and it has: one of his songs reads +9.9, −34.4, −6.2 and +21.1 s |

**A default is what happens to somebody who has not decided.** Making that the slow answer that sometimes reads nothing, rather than the instant one
that is 80 ms out and corrected by seven presses of `Shift+M`, is the wrong way round. The finer measurement is still one keypress away.

Three boundaries keep it honest:

- **Only when there is a map to reach for.** Setting Songsterr on a song with no id would leave `Ctrl+S` refusing with *"no link is stored"* — worse
  than listening.
- **Only when the player has not decided.** `listen`, `songsterr` and `hand` are all choices and none is overwritten.
- **It is stored, not just used for the run**, so the panel names it and `Alt+S` can move it. A default nobody can see is a decision the app made in
  secret.

And it touches the source only. The 70 ms the player dialled in with `Shift+M` lives in a different setting from the points, and defaulting one must
never quietly reach into the other.

### The Songs Folder Is The Sync

*"Wie bekomme ich alle Songs von NB1 auf NB2 mit Syncs etc.? Ich könnte einen iCloud Link nutzen."*

Copying the songs folder carried the tab, its bar map and its recording — and left the expensive part behind. The practice speed, the **sync points**,
the Songsterr id, the transpose and the star all lived in one `settings.json` in the home folder, keyed by the tab's stem. The second laptop got the
songs and none of the work.

**Two machines writing one settings file is the problem, not the copying.** A cloud folder syncs files; it cannot merge two edits of one JSON, and
this app saves that file on almost every keypress. Last writer wins, and what loses is silent. Putting `settings.json` in iCloud would have looked
like a fix for a week and then eaten an evening of sync points.

So the split is by **scope, not by convenience**:

| lives with the SONG (`<name>.mysician.json`) | lives on the MACHINE (`settings.json`) |
|---|---|
| practice speed, sync points, both offsets, playback rate | audio device index |
| Songsterr id and sync source | calibration |
| transpose, favourite, best score | input latency offset |

That right-hand column is the same list `merge_stats.py` has always refused to carry: an audio device index and a sound-card latency describe an
interface, and moving them breaks the other computer's input while looking like a settings problem.

Three rules make it safe:

- **What this machine already has WINS.** Only settings with no local entry are taken. That is the rule `merge_stats.py` has always used, and one
  rule in the project beats two — a sync that silently overwrites what you just adjusted is worse than no sync.
- **The recording travels as a NAME, not a path.** `C:\Users\Admin\…` means nothing on the other laptop, and it is only adopted if a file of that
  name is really beside the tab — otherwise this would store a path to nothing, which the app reports as a moved recording.
- **Never a raise.** One unreadable sidecar must not break the song list, and a song that plays beats a note about why its settings could not be
  written down.

**It is written on the way out, not on the way through.** Leaving a song, finishing a measurement, a rename, a download — the expensive moments. A
cloud folder that sees a file change forty times a minute is a cloud folder fighting itself. The sync points are the exception that writes at once:
they are minutes of measurement, and losing them to a crash is the one loss this exists to prevent.

**And the list reads them when it scans**, not when a song is opened, because a star has to show in the LIST — *"my favourites are gone"* is what
copying the folder used to look like. `reload_files` owns its own note, so the count is folded into the line it returns rather than set behind its
back; a second writer of that note leaves stale news standing, which is exactly what the first version did.

`sidecar.song_fields` is the **fourth** reader of "every per-song setting" — with `forget_song`, `rename_song` and `merge_stats` — and four are only
safe because none of them writes the names down. The one that did was found this week with two settings missing.

### Two Rules About Text Boxes, Revised By Use

*"DEL sollte auch während filtern gehen. Rename: Es sollte möglich sein mit Pfeiltasten zu springen."*

**DEL was kept out of the search box on a theory, and the theory was wrong.** The reasoning written down at the time was "there DEL is what somebody
reaches for to fix a typo" — true of a browser address bar, false of THIS box, which edits with backspace and does nothing with DEL at all. And the
order somebody actually works in is *find the song, then delete it*: filtering down to one row is how you find it. The two-press confirm was already
the protection; the guard was protecting nothing. It now works there and the search hint says so.

**The rename editor could only grow and shrink at the end**, which meant fixing the FRONT of a name — and the names that need fixing are Songsterr's,
where the artist sits at the front — was "delete the whole thing and type it again". It has a caret now, and every key is what it is in any other
text field: arrows move, Home and End jump, Backspace eats behind, Delete eats in front. DEL is a text key in here and the song list's own DEL never
sees the event, because the editor owns every key while it is open.

The caret is drawn **where it is**, as a bar. The trailing `_` was honest while the editor could only append; with arrow keys it would claim the
cursor is somewhere it is not, which is worse than no cursor.

Two tests had to be inverted for this, and that is the point worth keeping: both asserted the old rule faithfully, so changing the behaviour showed
up as a failing suite rather than as a surprise months later.

### A Stick Is A Real Folder

*"Auf NB2 geht keiner der Wege mit OneDrive, iCloud oder Dropbox. Ich könnte das Trio GP, MP3, Songsterr.Map von Hand kopieren."*

Two corrections, one of them his and one of them mine.

**His trio is a quartet, and that is the whole answer.** Since the settings moved into `<name>.mysician.json`, a song IS its four files — tab, bar
map, recording, settings — all beside each other under one name. So `settings.json` and `progress.json` are no longer on the list of things to
carry: the per-song half of both already travels with the songs. What is genuinely left over is **the chronological sitting log**, and whatever
still sits in an older `settings.json` written before the sidecar existed.

**And an iCloud share link is not a folder.** `https://www.icloud.com/iclouddrive/…` is a web page; the app needs a path it can read *and write* —
sidecars, measurements, settings. A read-only pull would make the second laptop a spectator. The answer for a machine where no cloud client can be
installed is the thing that was always there: a USB stick is a real folder.

So `Ctrl+I` on the song list takes a folder and pulls out what is missing. The merge core moved from `tools/merge_stats.py` into
`pickhero/transfer.py` for one blunt reason: **`tools/` is not in the .exe, and the laptop that most needs to merge is the one with only the .exe on
it.** The command line is now a front end for the same code rather than a second copy of it — and `practice_log.write` came along with it, because
the tool had its own copy of the write loop and a format with two writers is a format that drifts.

The rules are the ones this project already had, said once more where they now live:

- **Running it twice changes nothing the second time.** Sittings are keyed by when they started and which song.
- **What this machine has WINS**, per setting and per file. A song already here keeps the copy being practised, whatever the other machine says.
- **Per FILE, not per song.** A sidecar arriving beside a tab we already have can only add settings we have not got, and the tab is left alone.
- **Nothing that belongs to the MACHINE moves** — audio device, calibration, latency — and the report says so out loud, because "what did it just do
  to my sound card" is the question this feature would otherwise raise.
- A `.bak` is left beside anything rewritten, which is why it runs in one step instead of asking first.

Both new modules are named in `pickhero.spec`. They are imported inside the functions that use them, so nothing in the static graph reaches them —
the verovio lesson — and this is the one feature that exists *for* the .exe-only laptop, so failing there and nowhere else would be the worst
possible place for it.

### "Every Song Has Four Files" Was A Promise, Not A State

*"Ab wann hat jeder Song 4 Files? Muss ich in jeden Song reingehen? Einmalig beim Öffnen der App wäre praktischer."*

He read the feature correctly and found the hole in it. The sidecar was written on the way OUT of a song, after a measurement, a rename or a
download — all the moments when something CHANGES. Which meant a folder of fifty songs practised before the feature existed had **no** sidecars at
all, and "every song has four files" would have come true one song at a time, in whatever order they happened to be played. Before copying anything
anywhere he would have had to visit every single one.

So the song list writes the missing ones when it scans. Two rules keep it honest:

- **Only where there is something to carry.** A song nobody has touched gets no file: an empty sidecar is clutter that also LIES — a file saying
  "settings live here" when they do not. That song is simply three files instead of four, and copying it loses nothing.
- **An existing sidecar is never rewritten.** It may have arrived from the other machine and be newer than anything here, and overwriting it on a
  scan would silently undo an import.

Which makes the scan two-way and idempotent: it adopts what the folder knows and this machine does not, and it writes what this machine knows and
the folder does not. Run it twice and the second run does nothing.

### The Songs Folder Could Only Be Changed From A Command Line

*"Import hat auf NB2 funktioniert. Wie kann ich jetzt den Standort-Ordner ändern?"*

With `--songs` — which on the laptop that has nothing but `MySician.exe` on it means it could not be done at all. The **third** time this exact gap
has surfaced in a week: the merge tool lived in `tools/` and so did not exist in the .exe, `ffmpeg` needed a script to fetch it, and now this. The
pattern is worth naming: **anything that can only be reached from a shell does not exist on the machine that most needs it.**

It is a row in the settings screen rather than a new key, because CLAUDE.md already says where this belongs — the place for anything set once that
then lives on invisibly — and because a row SHOWS the current folder while a keybinding shows nothing. The path is shortened from the **front**: the
half that identifies it is the end, and every one of these paths starts the same way.

Two details that are the difference between working and nearly working:

- **The stored path is absolute.** A relative one resolves against wherever the .exe was started from, which is how this app once died before
  drawing a single frame — `Config.songs_path` carries that scar already.
- **It returns to the song list.** The answer to "did that work" is the list of songs, not a settings row that says a path.

And because pointing at a new folder is an ordinary scan, a folder carried over from the other machine brings its sidecars in on arrival — the same
path as every other reload, rather than a second one written for this case.

### Born To Be My Baby: Nothing Downstream Was Broken

*"Bei Born to be my baby scheinen alle Syncs zu versagen. Kannst du das bitte mal analysieren?"*

Measured on the player's own three files, every part that could have been at fault was fine:

| | reads |
|---|---|
| the listening | 40 of 43 windows, 0 ambiguous, one section, no breaks |
| Songsterr's bar map | 43 of 43 windows, 68 ms scatter, 149 points against 149 measures |
| the two maps against each other | within ~100 ms across the whole song |
| the follow loop, simulated at 60 fps over the real map | worst error **17 ms**, **zero** snaps |

The song is a real find in one respect — the recording runs about **1.2 % slower than the tab throughout**, so the offset walks from −0.2 s to
**+2.6 s** over four and a half minutes, the largest warp this project has met. `SYNC_PULL_FRACTION` gives 50 ms/s of authority against the 12 ms/s
that needs, so the picture tracks it without a visible correction. That part works.

**The failure was one step before all of it, and it was a regression from three days earlier.** He carried the tab and its `.songsterr.json` across
by hand — no sidecar, so no stored song id. Then:

1. `_bar_map_available()` said yes, because the **cache** is there
2. so the new "undecided songs start on the bar map" default set the source to `songsterr`
3. and `_start_auto_sync` refused, because it asked a **different question**: is an **id** stored

The measurement never ran. Nothing was ever stored. Every attempt answered *"no link is stored"*. Two questions about the same thing, asked
differently in two places — and the one that decides was not the one that does the work.

Both halves are fixed, because one is the correct fix and the other holds when something else gets it wrong:

- the guard now asks `_bar_map_available()`, the same question the default asks
- **the song id is taken out of the cache**, where it has been written since the first day and nothing ever read it back. `Ctrl+U` existed to type
  in by hand a number the song was carrying all along.

The lesson is not about Songsterr. It is that **a capability check and a permission check that disagree produce a feature that silently does
nothing** — and the default I had just changed turned a latent disagreement into every sync on the song failing.

### Naming The String Is The Player Saying So

*"Können wir noch was einbauen, damit ich beim Stimmen die Saite wählen kann, falls die falsche erkannt wird."*

He asked for the case where the tuner picks the **wrong** string. The bigger half is the one he did not name: `nearest_string` returns **None** when
no target owns the reading, and a string far enough out that nothing owns it is exactly when a tuner is most needed. A fresh string four hundred
cents flat got no answer at all — the screen simply sat there saying "Play a string" while he was playing one.

`1` to `6` name a string, and with one named the catch window stops applying: naming it IS the player saying which one it is. Three details:

- **6 is the low E**, the way a guitarist counts and the way the rest of this app already numbers strings (`F1`–`F6`, `active_strings`). The pips are
  drawn low-first, so the numbers read left to right — and each one is **written under its pip**, because a tuner is read with a guitar in both hands
  and 6-is-the-low-E is a convention rather than something the screen would otherwise say.
- **The same key lets go.** The player who pressed 5 to escape a wrong guess presses 5 again to stop, without hunting for a second key. Letting go
  drops that string's reading, which was measured against a target that is no longer the question.
- **An octave is still refused.** The window is ±900 cents — wide enough for any string a person would actually try to tune, and stopping short of
  1200 keeps YIN's octave error out. A reading an octave up is the detector being wrong, not the string being wrong.

### The Nut Rides With The Playhead

*"Es geht darum, dass der Balken die aktuelle Stimmung der Saite anzeigt. Der Wert je Saite ändert sich während dem ganzen Song nicht. Es ist nur
eine zusätzliche Orientierung, damit man besser weiß, ob man auf Saite D oder G ist."*

**I read his first sketch as a progress bar and built a plan for one.** It was a drawing over a screenshot, not a feature, and it meant the opposite
of what I took it for: not something that CHANGES with the song, but the one thing on screen that never does. Six letters on the playhead saying
which string each lane is. He had to stop me — and the lesson is that a mock-up drawn over a screenshot says what it should LOOK like and nothing
about what it MEANS, so the meaning has to be asked for rather than inferred.

It is called the **nut** because that is the part of a guitar where the open strings are named, in exactly this order. This one rides with the
playhead instead of sitting at the top of the neck — moving in the hybrid view, standing still in the standard one, which is the easier half.

Three things it has to get right:

- **See-through.** The notes underneath are the ones being played, so a label that hid them would cost more than it gives.
- **Only the row being played**, in hybrid. Six letters on every row would be a column of labelling down the page, and the question they answer is
  about the hand, which is on one row at a time.
- **`nut_letters` is where the two orders meet.** `tuning_notes` reads low to high because that is how a player tunes; the lanes are drawn the other
  way up, lane 0 being the high e. Reversing it once here beats a `5 - i` in every caller.

All six discs are identical, so one is built and blitted six times — six SRCALPHA surfaces a frame is sixty a second for a picture that never
changes.

### A Margin Is Measured In Centimetres, Not Pixels

*"Links und rechts am Rand ein Abstand von 1-2 cm, damit meine Augen nicht ganz bis an den Rand fahren müssen."*

`SHEET_SIDE_PAD = 24` was comfortable on the laptop it was written on and a hairline on a desk monitor, because a margin is about **the distance the
eye travels** and that is a physical measurement. It is a fraction of the width now — 3.5 %, about 1.2 cm at 1920 — and never less than the 24 px it
used to be, so no screen ends up worse off.

The reason this is safe where the footer was not: **it is horizontal.** The footer once changed its own height by saying how many bars were on a row,
which changed the room, which changed the head size, which walked 44.4 → 44.5 px. This changes how many bars fit on a row and leaves the row HEIGHT
alone. The test asserts that on the signature rather than by grepping the source — `_sheet_pad` is a staticmethod taking a width and nothing else, so
it cannot see the layout it feeds. That makes the loop impossible rather than merely absent today.

### The Lead-In: Where The Eye Already Is

*"Bei langen Tönen schaue ich bereits nach links, verpasse dann aber oft um 150 ms den ersten Ton."*

The diagnosis is his, and it is exact. On a held note at the end of a row the eye has already moved to where the music is about to continue — and
until now there was **nothing there to read**. So the entry was guessed, and a guess is late. 150 ms late, measured by the person doing the guessing.

So a second, quieter playhead runs in on the row below and reaches that row's first note **at the moment the music does**. Nothing about it is
decoration: it exists so the entry can be read instead of counted.

Three decisions worth keeping:

- **It targets the row's first ANCHOR, not the margin.** A row whose first bar opens with a rest has its first note further in, and that is the
  moment being led to.
- **Always, at every row change.** A cue that only turns up sometimes is one that cannot be planned around — and planning the entry is the whole job.
- **`lead_in_x` returns None outside its window**, so the caller has one thing to check and a bar that has already landed cannot be left on screen.

**And a measurement that changed the design.** The first version only moved, and the runway is the left margin and nothing more — **56 px at 1600
wide**. A second of travel across that is about a millimetre a frame, which is motion the eye can miss: the exact failure the feature exists to fix.
So it brightens as it comes, from 0.18 to 0.55 of the playhead's colour, over the same second. The brightening is the half of the signal that does
not depend on how wide the margin happens to be — and the share that drives it is computed from the position that was already chosen, so the two
can never disagree.

### What this is not

It is still a windowed search, and a window has no idea what the window before it found. That is what lets one match the third chorus
while its neighbour matches the first. **A monotone path (DTW) cannot do that by construction**, and on the chroma this file already
computes it runs over a whole song in 5 s — measured. That is the next piece of work, and the reason this one stops here rather than
tuning thresholds further.

**What Songsterr actually does, since it is the benchmark:** nothing like this. `songsterr.com/api/video-points/{song}/{revision}/list`
returns **a timestamp per measure** into a YouTube video. It is a made map, not a found one — by hand, or born with their own
transcription. Go PlayAlong asks for "2 to 5 points" by hand; Soundslice has you tap `T` on every barline. Every tool that solves this
well solves it with a human somewhere in the loop, which is worth knowing before spending another week on the search.

## The Recording Is Only Synced Where Somebody Listened

"Thunder synct beim Solo ganz schlecht und liegt weit daneben." Read straight off the run log, and the app was already telling him in a
line nobody reads at the moment it matters:

```
mp3_sync_points   11   22s:+11110ms 34s:+11116ms 40s:+11016ms 52s:+10976ms 76s:+10769ms
                       106s:+10703ms 118s:+10757ms 142s:+10756ms 166s:+10897ms 178s:+10895ms 202s:+10967ms
mp3_sync_covers   22-202s of 382s   47 %
```

**The last point is at 3:22 and the solo is at 3:49.** Every note past 3:22 is placed by extrapolating the last measured section, over
half a song that was never listened to — and the offsets that WERE measured wander from +10703 to +11116 ms, 413 ms of real drift, with
section rates swinging from -1.64 % to +0.59 %. Extrapolating thirty seconds past the end of that is worth a fifth of a second at best
and much worse at the rates this song has shown. `mp3_worst_drift_ms` reached 153 in one of the runs.

**The automatic pass did not fail silently, it failed loudly and in the wrong place.** The panel says `found by listening — 28 of 51
windows usable` and `measured 0:21–3:21 of 6:21`, which is honest and complete. It is also printed once, in blue, next to ten other blue
lines, while the player is at 0:00 — and read at 3:49, where it is the only thing on screen that explains what he is seeing, it is not
on screen at all in any form that says "this applies to you NOW".

**The line is said where it applies now.** `_beyond_sync_line` speaks only while the playhead is outside `SyncMap.covers()`, in the
warning colour, and the size it offers is `drift_seen_ms()` — how far the recording wandered where somebody WAS listening. A modelled
bound would be a promise; this is a number the song has already produced. Nothing is said inside the span, and nothing on a song with a
single stored offset, which claims to have been measured nowhere and so has no edge to fall off.

**And then the key it points at turned out to be broken, which is why this chapter grew.** `Shift+S` was going to be the answer that
needed no code — until it was read:

- **`SyncMap` threw the stored offset away the moment one point existed.** `offset_at` returned the interpolation and ignored
  `base_offset_ms` entirely. So on every song that had ever been synced, `Shift+N`/`Shift+M` were DEAD: the HUD went on printing a number
  the player could change while the recording did not move a millisecond. The oldest fault in this file, in the one place nobody looked.
- **And `_set_sync_point` saved that dead number.** `here = (playback_ms, self._mp3_offset())` — the nudge, not the offset in force. On
  this player's song the map reads +11.0 s in the solo and the stored offset is +0, so the one press meant to rescue a drifting tail
  would have written a point saying zero: not a repair, a demolition. **The advice to press it was given before the code was read**, and
  it would have cost him his sync.
- **The offset is a nudge ON TOP of the map now**, and a point records the sum. Pressing spends the nudge — left standing it would apply
  a second time, to the whole song, including the parts already right. The workflow is what it always looked like from outside: nudge
  with `Shift+N`/`Shift+M` until it sits, then `Shift+S` to pin it there.

**A test helper had to change with it, and that is worth its own line.** `_mark` set the stored offset to an absolute value and pressed;
it only ever worked because the map discarded the number and the press saved it anyway. It nudges by the difference now, which is what a
hand does.

## Slowing The Tab Down Did Nothing At All

"Kleiner machen geht nicht richtig — es haengt meist bei Groesse 1 und ignoriert kleiner machen. Groesser machen geht, aber das macht den Bildlauf schneller."

Exactly right, and measured on three real songs before touching anything:

| Faktor | Papa Roach | Bon Jovi | timing test |
|---|---|---|---|
| 0.4 | 191 px/s | 432 px/s | 173 px/s |
| 0.6 | **191** | **432** | **173** |
| 0.8 | **191** | **432** | **173** |
| 1.0 | 191 | 432 | 173 |
| 2.5 | 477 | 683 | 433 |

Every factor below 1.0 produced the identical picture. `window = max(MIN, min(fit_window, window))` clamped the window back to the size at which every note keeps its full head — so the number on screen walked down to 0.4 while nothing moved, which is the "feature that cannot be seen working" fault in its purest form.

- **The rule it was protecting is real but was read too widely.** Notes must not change size WHILE SCROLLING. The trim happens on a keypress, which is the same moment the automatic already resizes them — and the app deciding to shrink notes is a different thing from the player asking it to.
- **Two floors, and the wrong one was in the way.** `MIN_FRET_DIGIT_PX` (34 px of type, so 41.6 px for a two-digit head) was fitted in "Eleven Or Twelve" for reading a number crossing the screen at 430 px/s. Applied to a tab the player is deliberately slowing down it asks the wrong question — and it meant **every song containing a two-digit fret could not be slowed at all**, because its head already sat on it. That is most rock songs. A hand-requested slowdown uses the older, harder `MIN_HEAD_PX` (26) instead.
- **Measured after: Papa Roach 191 → 112 px/s, Bon Jovi 432 → 270, the timing test 173 → 102.** Bon Jovi is the two-digit case that could not move at all before.
- **A press that changes nothing is put back and says so.** At the floor the key now answers "the notes are already as small as they may get" rather than storing a factor the display is not honouring.
- **Removing the clamp broke the speed floor and the suite caught it**, in the one case nobody would have played: a song so dense its notes must overlap had its window recomputed straight back down through `MIN_VISIBLE_WINDOW_MS`, to 167 ms. The trade only runs when the player actually asked to slow down.

## Making The Tuner Simpler Meant Taking Things Away

Three things it asked of the player that it did not need to.

- **It asked which tuning, and the app already knew.** The song list reads every file's open strings and shows them on the row; the tuner is
  opened from that list, with a song under the cursor. So it opens on THAT song's tuning and says where it came from
  (`from Papa Roach - Leave A Light On`). All sixteen named tunings render to distinct letter strings, so `tuning_for_notes` is a lookup and
  not a guess — a song in a tuning nobody named, or one not read yet, falls back to Standard exactly as before, and `LEFT`/`RIGHT` still
  override. Picking by hand drops the song's name from the line, because it would then be describing something no longer true.
- **It showed six bars, of which five never move.** Finding the one that does is what the little arrow beside them was for — a cue that only
  exists because the layout hid the answer. One string gets the screen now: its note at 96 px, one needle as wide as the window, and six small
  pips underneath for which strings are done. A tuner is about the string in your hand.
- **It reported cents, which is a measurement and not an instruction.** "−34 ¢" asks the player to know that negative means flat and that flat
  means turning the peg the tightening way. `advice()` says **"Too low — tighten"**, then "Hold it…" inside the band, then "In tune" once it
  has been held; the number stays underneath in small type for anyone who wants it. It is tested as a property — flat says tighten, sharp says
  loosen — rather than by its wording.

## A Tuner Must Not Believe The Calibration

The playing screen has always had a chromatic strip — nearest note, cents, a bar. That is the wrong instrument for tuning up: it says "you are playing a G#", not "your D string is 34 cents flat", and it cannot show which strings are already done. `ui/tuner_menu.py` is the other one, opened with `U` from the song list.

**No library is involved, and none is needed.** The pitch is aubio's, which the app has run since the first day, and a tuner is that pitch against a target: `1200 x log2(heard / target)`. The 16 tunings were already in `note_utils.NAMED_TUNINGS`. What a tuner has to get right is not the arithmetic but what it refuses to say.

- **It reads the RAW pitch** (`get_tuner_data(raw=True)`, `detector.last_freq_raw`). `_correct_octave_jump` halves a frequency whose half lands near a CALIBRATED string, and this player's stored calibration has the A string an octave low. Being wrong about the octave while playing costs one note; being wrong about it while tuning makes them detune the guitar to match.
- **The catch window is derived from the tuning, not fixed.** The test asserting that no reading can be owned by two strings failed on **DADGAD**, whose G and A sit a whole tone apart — a fixed 2-semitone window owned both, and the tuner would have named whichever it rounded to. It is half the closest pair in the chosen tuning now, capped at `CATCH_SEMITONES`. Every named tuning is checked at ±0.99, ±0.5 and 0 semitones from every string.
- **A pitch no string owns names nothing.** A tuner that guesses sends the player the wrong way, and further out with every turn.
- **In tune is a state that has to be HELD** (`STEADY_MS`, 400 ms). One frame inside the band is a string passing through the note on its way somewhere else, and going out again takes the tick back.
- **Rows read low string first**, the order a guitarist tunes in and the reverse of the string NUMBERS, where 1 is the high e.

## A Note Head Is Squeezed Sideways, Not Downwards

A dense song shrinks its note heads to buy look-ahead — see `_recompute_scroll_speed`. What was never noticed is that the squeeze is
entirely **horizontal**: look-ahead is bought and sold in width, while the lane is as tall as it ever was. Measured on the song the player
reported, a solo track at 135 BPM whose sixteenths sit 111 ms apart, in a 1277x771 window:

| | |
|---|---|
| head after shrinking | 26 px (the `MIN_HEAD_PX` floor) |
| lane height | 56 px |
| **vertical space unused** | **30 px, 53 %** |
| look-ahead at full-size heads | 2.0 s — unreadable |

So the head now carries its own height (`_head_h_px`), taken from the lane rather than from the music: full-size on a roomy song, where it
equals the width and the note stays round, and full height on a dense one, where it does not. **This costs no look-ahead whatsoever** — the
window is computed from the width alone, and the test asserts that rather than trusting it.

Two things worth keeping straight, because the first version of the write-up got them backwards:

- **The height makes the NOTE bigger, not the number.** At a 26 px head a two-digit fret is limited by the width, and more height does
  nothing for it. The digits grew from 14 px to 21 px for a different reason: the old rule sized them at a fixed `radius * 1.1` and left
  room unused, where `_fret_font` now fits them to the space that is actually there.
- **Every fret number in a song is sized for the widest label in it.** Sized to its own label instead, a lone "5" towers over the "15"
  beside it, which reads as emphasis the music never asked for.

## One Curve For Every Note

"Im Moment sind die breiteren Noten weniger abgerundet und mehr eckig." Exactly right, and it followed from the head being squeezed sideways.

The corner radius was `min(head width / 2, head height / 2)` — and since a dense song buys look-ahead by narrowing the head while it keeps the
lane's full height (see the chapter above), the width is the smaller of the two on every song that matters. So a sustained note got corners
fitted to a width it does not have, and read as a box beside the round short ones next to it.

- **The curvature is the HEIGHT's business and nothing else's.** One shape for every note now — a rounded rectangle with a corner of half the
  head height — so a short note is a circle, a held one is a capsule, and the curve where they meet is identical. pygame clamps the radius to
  half the shorter side by itself, so a head narrower than it is tall stays a capsule instead of growing corners.
- **The ellipse branch is gone with it.** A short note used to be drawn as an ellipse and a long one as a rect, which is two shapes to keep in
  agreement for no gain: a square rounded rect IS a circle.
- Measured on a real board: 44x44 corner 22 and 222x44 corner 22, one radius for both. The test asserts the property — every head on a board
  carrying both a quick note and a held one comes back with the same corner — and it fails on the old rule for the dense case, which is the one
  the player was looking at.

## Eleven Or Twelve

"In a fast solo I can barely see whether it says 11 or 12." Measured on the app as it stood, in the same song:

| | |
|---|---|
| a ONE-digit fret | **42 px** of type |
| a TWO-digit fret | **21 px** — half of it |
| the head it sits in | 33 px wide, 49 px tall |

A number is wider than it is tall, and the head is squeezed **sideways** to buy look-ahead (see the chapter above) — so the digit was limited
by the one dimension the song was spending. The height was already free and could not help.

- **The head's WIDTH is sized for the widest label the song contains**, the same rule the FONT already followed. `_fret_digits` is therefore
  computed before the head rather than after it — it was set at the end of `_recompute_scroll_speed` and read at the start, which would have
  resized every note one frame in, and a note that changes size while scrolling is the one thing this display must not do.
- **Only the broken case pays.** Measured: a fast two-digit song goes 21 px → **34 px** of type and 4.0 s → 2.5 s of look-ahead; a fast
  single-digit song and a roomy song are bit-for-bit unchanged. Trading time for size is allowed here; trading away the warning is not, so the
  floor is still `MIN_VISIBLE_WINDOW_MS`.
- **The digits are bold.** A thin stroke is the first thing to disappear at speed, which is exactly when the fret number matters most.
- **An open string is grey, whichever string it is.** The lane already says WHICH string — that is what the six lanes are for — so the colour
  is free to say something the position cannot, and "nothing to fret" is the most useful thing it can say. It is what makes a chord read at a
  glance: the open strings drop back and the shape the hand has to make stands out. Grey is neither a string colour nor a feedback colour, so
  the separation the palette is built on holds.
- **Fingering cannot be coloured, and the reason is worth writing down.** Guitar Pro DOES carry a left-hand and a right-hand finger per note.
  Both were `Fingering.open` — "not given" — on **6193 of 6193** notes in the player's own tabs. So the field exists and transcribers leave it
  empty; inferring it from fret position would be a guess dressed up as data, which is what this project refuses everywhere else.

## Practice Speed Belongs To The Song

`tempo_factor` was one number for the whole app, so the solo being learned at 70 % opened the next song at 70 % too, and the song you had
finished opened slowed down because something else needed it. `song_tempo_factors` keys it by song; anything not in there starts at full
speed, and full speed is never written (an entry saying 1.0 says nothing).

- **The plain `tempo_factor` stays**, because tools outside the app read it: `record_reference.py` writes it into a take's manifest, and an
  analysis that does not know the speed reads a stretched take against the wrong grid — which cost a whole session once.
- **And keeping it is exactly what then wrote the wrong number.** `practice_tempo()` went on reading the global value, so the first take
  recorded after this change said 80 % for a song played at 100 %, and `analyze_play_along.py` read that take at **13 %** instead of 91 % —
  which looks precisely like a detector that has stopped working. It reads `song_tempo_factors[key]` now, and a song with no entry of its own
  is **1.0**, not the global value: that is what the app opens it at. A setting that moves house has to be followed into every reader, and the
  writer outside the app is the one nobody looks at.
- **The analysis no longer believes the manifest either** (`check_tempo`). It measures the speed anyway and overrules a stated one that
  another speed beats by a quarter — 51 strikes explained against 33, where a correct speed is a sharp peak (46 against 40 at its neighbour).
  Same rule as everywhere else here: a tool that reports a number without checking the assumption underneath it is measuring itself.
- **The settings screen shows it as "per song", not as a value.** A global number that no longer decides anything is a lie on a screen whose
  entire job is saying what is set.

## The Chord View Was A Key And Not A Setting

"Shift+C fehlt auch im Settingsmenü." Right, and that screen exists for exactly this: anything set once and then living on invisibly. The chord
view was screen state, so it was forgotten at every song and could not be found anywhere — which is indistinguishable from a feature that does
not exist, and is how the player reasonably concluded it did not.

It is `Config.chord_view` now: a row on the settings screen, marked when it is not standard, and written back when `Shift+C` toggles it in the
song. The key and the row are the same setting, which is the rule every other pair on that screen already follows.

## A Setting You Cannot See Is A Setting You Cannot Undo

Forty-one keys are handled while a song runs. That is right for the ones the hands reach for with the guitar still on — play, wait mode,
tempo, loop, `K` — and wrong for the rest: a fret limit, a muted string, a noise gate is set once and then lives on, invisible, changing how
everything scores. A fret filter left switched on once made whole songs unplayable and nothing on screen said so.

`ui/settings_menu.py` (`O` from the song list) is therefore not a way to CHANGE those settings — the keys already were — it is a way to SEE
them.

- **Anything not on its standard value is marked**, in the accent colour and with a dot, and the header names them: "2 settings away from
  standard: Fret limit, Strings played". That line is the whole feature. The test that matters asserts a changed fret limit and a muted
  string appear in it, and that a fresh config produces nothing — a screen that always claims something is off teaches the player to ignore
  it.
- **`R` resets one row, not everything.** The value that needs undoing is usually one somebody changed by accident; losing the audio device
  and the calibration along with it is a punishment for having noticed.
- **Every row explains itself in terms of the guitar**, in one line, for the selected row only. A setting whose effect on the score is
  invisible is left where it is out of fear.
- **Saved as it is changed**, so there is no OK button to forget. The device and calibration screens are opened from here with ENTER and come
  back here, not to the song list — `App._return_to` exists for exactly that.
- **The strings row is six settings in one**, with a cursor of its own (left/right picks, ENTER mutes). `active_strings` is indexed by GP
  string number minus one, so index 0 is the HIGH e while a guitarist names the low E first; the row is drawn low-first and the test pins the
  mapping, because an off-by-one here mutes the wrong string and reads as a detection fault.

## Two Keys That Were Right For One Job And Useless For The Other

- **An arrow key moved one BEAT, and nothing else.** That is the right step for placing a loop marker and useless for reaching the chorus of a
  four-minute song: at 273 ms a beat that is nine hundred presses, and with key repeat at 40 ms it is half a minute of holding the key while
  the picture scrolls past. So the same ladder the backing-track offset already uses — plain, Shift, Ctrl — with each step chosen from what it
  is FOR: a beat to place a loop, a **bar** to walk a phrase, **30 s** to reach a section.
  - **Shift SNAPS to the bar line**, rather than adding a fixed number of beats. The timeline carries real measures, so this stays on the bars
    through a time-signature change and lands where the tab is drawn rather than near it. A margin either side, because pressing back from just
    after a bar line has to reach the PREVIOUS bar and not stand still on the one just crossed.
  - A tab that parsed without measure info falls back to the beat, because a key that silently does nothing is worse than one that does less.
- **Changing instrument threw the position away.** The tracks of one file share a clock — bar 40 of the rhythm guitar is bar 40 of the lead —
  so restarting at the first note is not a fresh start, it is losing your place. And somebody comparing two versions of a passage changes track
  precisely BECAUSE they are at that passage. `_load_song` takes `resume_at_ms`, clamped to the new track's length, since it may be shorter.
  The screen is rebuilt from scratch on that path, so the position has to be carried over by hand.

## Nine Out Of Ten Red Notes Are Not About The Playing

"Kannst du meine Logs je Song lesen und mir helfen — wie ein Gitarrenlehrer." The run log already holds what a teacher needs; what nobody had
done is ask what a red note is EVIDENCE of. Every missed note in three complete runs of two real songs, classified by joining it to the strike
that arrived nearest it:

| why the note failed | Leave A Light On | the same song again | Californication |
|---|---|---|---|
| subharmonic — an arpeggio read as one note | 72 % | 71 % | 65 % |
| a strike arrived carrying no pitch | 17 % | 10 % | 23 % |
| **a clean reading of a WRONG pitch** | **8 %** | **9 %** | **9 %** |
| an octave out (green on screen; counted here for completeness) | 2 % | 2 % | 1 % |
| no strike within the window at all | 0 % | 8 % | 2 % |

**So a report that simply listed the weakest bars would spend most of its advice on passages that were played correctly**, which is the worst
thing a teacher can do and is exactly what the first version of this did. `tools/coach.py` therefore reports only what it can stand behind:

- **A strike that never arrived** is the one unambiguous fault, and **a clean reading of a different pitch** names the interval it went wrong
  by. Those two are the playing.
- **Timing is honest even where the pitch is not**: a subharmonic strike proves something was struck at that moment, whatever the detector
  made of it. So the timing per bar is measured over every strike, not only over the readable ones.
- **Everything else is counted and named as unreadable**, and a passage whose failures are all unreadable says so in as many words rather than
  being ranked among the weak ones. Absence of evidence is the commonest thing in this signal path.
- **The trust check runs first and outranks everything.** A wrong input device makes every other number in a log meaningless — this project
  has spent whole sessions on playing that was never in the signal path — so `trustworthy()` reports the room microphone, the gate that ate
  the audio, a peak below where the pitch rots, and dropped buffers, before a single bar is named.

**And the log had to become self-contained first.** The note table was `note_ms string midi verdict`: milliseconds locate a note for a machine
and for nobody else, and everything else needed the tab file beside it. It now carries the **bar**, the **fret**, what the tab asked for
(`tech`: bend, slide, hammer, dead, palm mute, let ring) and how many strings were written at that moment — so "practise bars 36 to 45, frets
0 to 5, there is a slide in there" comes out of the log alone, and a log can be handed to anybody without the song.

## Two Kinds Of History, And They Answer Different Questions

`progress.py` keeps the BEST a song has ever been played — one record per song, overwritten as it improves. That answers "am I getting better
at this piece" and cannot answer "how much did I play this month", because it forgets everything except the peak. `practice_log.py` is the
other half: one line of JSON per session, appended, never rewritten, read by `tools/practice_report.py` for day, month, year and song totals.

- **The app does not draw it.** A diary rendered inside the app competes with the notes for screen space, and the format is deliberately one
  anything can read — the question behind it was "so I can build a dashboard".
- **Time is real seconds with the song running.** Not song time, which at 70 % practice speed is shorter than the time actually spent, and not
  wall-clock time, which counts the coffee taken with the app paused.
- **A struck note is a strike the microphone heard**, right or wrong. Zero when audio is off, with the minutes still counted — the playing
  happened either way. Both counters live on the SCREEN, not in the matcher: the matcher is reset by a seek, a loop and a tempo change, and a
  diary that forgets an hour because somebody pressed PgDn is worse than none.
- **A session with no score says so** rather than claiming 0 %. A sitting spent looping four bars has no accuracy, and a zero is a lie a
  dashboard would happily average in.
- **Written once, when the sitting ends** — leaving the song and closing the window both reach it, because either can be the end and neither
  happens reliably. Under `MIN_SESSION_SECONDS` nothing is written: opening a song to look at it is not practice.
- **The dashboard is generated, not live** (`pickhero/dashboard.py` → one HTML file). It follows the layout of the player's OWN Yousician
  dashboard, because that is the one they read without thinking — but with the charts drawn as plain SVG rather than pulled from a CDN: an
  offline-first practice app whose dashboard needs the internet to draw a bar chart is a contradiction. Everything is added up in Python and
  the browser only draws, so the arithmetic is testable and the drawing is checked by looking at it.
- **It rebuilds itself when the app closes, and it had to move into the package to do it.** `pickhero.spec` bundles `pickhero/` and nothing
  else, so a builder in `tools/` is simply absent on the machine running the EXE — which is the machine whose dashboard most needs to keep
  itself current. `tools/make_dashboard.py` is now the command line around it, so a fix reaches both at once. On the way OUT rather than on
  the way in, and AFTER `close_session()`: that call is what writes the sitting just finished, so a page built at startup is permanently one
  session stale and never shows the practising somebody just did. Measured: 1.5 ms at 100 sittings, 38 ms at 5000, 224 ms at 20000 — all of
  it after the last frame. A failure prints and is swallowed; an exception on the way out is a crash on exit, which looks like data loss.
- **The week view sums in the browser, so it is tested in one.** Every day of the current week with all three figures and the week's own
  total, arrows one week back and forward. It deliberately ignores the year chips and the metric switch above it: a week showing one number
  cannot answer "what did I actually do", and a week emptied by a chip being off looks broken rather than filtered. Backwards stops at the
  first week ever practised and forwards at this one, because a nav that walks into empty weeks tells the player nothing. Days are handled in
  UTC throughout — the log writes local calendar days and they are compared as strings, so a timezone must never be allowed to shift one.
- **Two machines, one player: `tools/merge_stats.py` brings the other one's history over.** The constraint that shaped it is that running it
  twice must change nothing the second time — nobody remembers whether they already merged, and a doubled history cannot be told apart from
  having practised twice as much. Sittings merge by (`started`, `song`), which two machines cannot both invent and the same file cannot bring
  twice. `progress.json` is a high score, not a statistic: the better record wins WHOLE (mixing one run's hits with another's accuracy
  describes a run that never happened) and `attempts` takes the larger rather than the sum, because a sum is exactly what cannot be done
  twice — the honest count of sittings is in the practice log.
- **What a sync must NOT carry is the interesting half.** `merge_stats.py` brings the per-song settings across — practice speed, backing
  track, both offsets, favourites — and deliberately leaves the audio device index, the calibration and the latency offset alone. Those
  describe an interface and a sound card, not a player; copying the whole `settings.json` is the obvious move and would break the other
  machine's input while looking like a settings problem. An entry the receiving machine already has always wins: it was set there, on that
  instrument, and a sync that silently overwrites what you just adjusted is worse than no sync.
- **A song name lands inside a `<script>` tag**, and a song called `</script>` closes it. The embedded JSON escapes `<` and `>`; the test that
  found that is the reason it is written down here.

## Profiled Again, And This Time It WAS The Notes

Asked to make the app faster, and the answer is only worth having with a profiler in front of it. A frame of the player's own song
(1314 notes, 1280x720), broken into its steps:

| | per frame | share |
|---|---|---|
| `_draw_notes` | **1.03 ms** | 39 % |
| `_draw_lanes` (board, strings, bar lines) | 0.46 | 20 % |
| `surface.fill` | 0.37 | 16 % |
| `_draw_hit_zone` | 0.12 | 5 % |
| `_draw_hud` | 0.11 | 5 % |
| **whole frame** | **2.27 ms** | of a 16.7 ms budget |

**Inside `_draw_notes` it is the ROUNDING.** A frame issues 48 rounded-rect calls — 24 notes, a fill and a border each — and **46 of the 48
are the same size**, because one head size is chosen for the whole song. Stubbing the calls out puts the floor at 0.39 ms, so 0.64 ms of that
1.03 is SDL drawing arcs. Measured directly: 24 heads drawn as rounded rects is **0.519 ms**, the same 24 blitted from a cached surface is
**0.056 ms**.

So `_head_surface` draws each head once and blits it after that. **`_draw_notes` 1.03 → 0.44 ms, the whole frame 2.27 → 1.88 ms (-17 %).**
The cache holds **14 entries** on a real board — six string colours plain and dimmed, plus the open-string grey — and stops growing, which the
test asserts over 600 frames rather than trusting. The feedback colours are discrete, not a fade, so an animation cannot thrash it; it is
cleared with the font cache, because a Surface outlives `pygame.quit()` no better than a Font does. The picture is **pixel-identical**: 0
pixels differ in colour and 0 in alpha against drawing it in place.

**Three things were measured and NOT built, which is half the value of profiling:**

- **Baking the background into a surface is a LOSS.** A full-screen blit is **0.63 ms** against **0.23 ms** for a fill. The obvious
  optimisation is the wrong way round.
- **Baking the board strip saves 0.17 ms and costs the layering.** The strings are drawn OVER the bar lines the way they lie on a guitar, and
  the bar lines move; a baked strip would have to go under them and the strings would end up beneath the wires.
- **Everything else is already healthy**, so nobody looks there again: loading a GP6 container 25 ms, `NoteMatcher` 0.7 ms, `PlayingScreen`
  0.3 ms, and the audio callback **0.24 ms of its 11.6 ms hop — 2 %**.

**And one measurement of mine was worthless, which is the recurring lesson.** Timing `_neighbour_gaps` over a whole song showed it growing to
2.3 ms at 5600 notes — except `_draw_notes` calls it with the VISIBLE notes, never all of them. I had measured a loop the app does not run.
A number is only worth what the call that produced it is.

## The Notes Were Never What Cost The Frame

The app ran "slow and stuttering" on a thin 14" laptop, and the obvious suspect on a scrolling display is the scrolling. It was not. Profiled over
60 frames of the playing screen:

| | share of one frame |
|---|---|
| **rasterising text** (62 surfaces a frame) | **79 %** |
| of which the footer alone | 66 % |
| drawing every note | 8 % |
| looking fonts up (uncached `SysFont`) | 6 % |

**The footer is the list of keyboard shortcuts. It never changes at all**, and almost none of the rest does either — the title, the tempo, the
tuning, the hit window. Only the clock moves, once a second. So `_CachedFont` keeps the surface and blits it again: **15.2 ms a frame → 1.5 ms**,
and a dense song (4200 notes of sixteenths) draws in 2.7 ms where the budget is 16.7.

- **Wrap the font, not the call sites.** The ~180 `font.render(...)` calls in `ui/` are untouched and anything added later is cached without
  knowing. `__getattr__` delegates `size()`, `get_height()` and the rest.
- **A font does not survive `pygame.quit()`** — it is a dangling pointer and rendering with it segfaults, which is verified rather than assumed
  (the test suite found it, because several tests run an init/quit cycle). Hence `clear_font_cache()`, called by `App.run` on init and by an
  autouse fixture between tests. A cache tied to a session has to be dropped with it.
- **The cache is cleared wholesale at `MAX_ENTRIES`**, not evicted one at a time. The only text that really varies is the clock, a re-render
  costs a fraction of a millisecond, and an LRU here would be bookkeeping to save nothing.
- **The run log now says how long a frame took** (`frame_ms_median`, `frame_ms_worst_tenth`, `frames_over_budget_percent`), measured BEFORE
  `clock.tick(60)` pads the frame out — `clock.get_fps()` reports the padded rate and reads a healthy 60 right up to the moment the machine can
  no longer keep up, which is the one thing it is being asked. A median under budget with a fat tail is something arriving in bursts; a median
  over it is the drawing. They are fixed in different places, which is the same reason strikes are named next to notes.

## Pausing Was Reopening The Device, And Seeking Was Redecoding The File

The player reported the picture freezing for up to three seconds on every space bar with a backing recording, the sound stuttering on the way
back, and the song then "jumping until it is in sync again". Three separate faults, and the first is a lesson this project had already written
down for the arrow keys and never applied to the pause.

- **Pausing closed the input device and resuming opened a new one.** That is the identical fault as "Seeking Must Not Reopen The Input
  Device" — a real device open on Windows, seconds of frozen app — except the space bar did it twice, once each way. It also threw the matcher
  away, so a run log lost every strike from before the pause. `_resume_audio()` re-anchors a stream that is still open and only opens one when
  there genuinely is none.
- **Pausing STOPPED the recording, so resuming was a `play(start=)`.** That decodes the file up to the point it starts from, which four
  minutes in is the seconds the player was watching. `Mix_PauseMusic` costs nothing, and `get_pos()` stands still while it is held — measured,
  because a clock that kept running would put the recording exactly the length of the pause out on resume. So a paused SONG suspends the
  recording; muting it, changing the file or jumping elsewhere still stops it. `_update_mp3` runs every frame while paused too, so it has to
  make the same distinction or it cancels the hold on the very next frame.
- **A held arrow key was 25 decodes a second.** Key repeat is 40 ms and every repeat seeked the recording. The FIRST seek of a burst is still
  immediate — a loop turn is a seek too, and delaying it would start the recording late every time round — and the rest are collapsed into one
  once they stop arriving (`MP3_SEEK_SETTLE_S`), with the recording held silent meanwhile.
- **And a stalled frame moved the song by the whole stall.** `_playback_ms` advanced by real elapsed time with no cap, so three seconds of
  blocked frame scrolled three seconds of music past uncredited and landed the picture somewhere the player never saw — the "it stands still
  and then jumps". Capped at `MAX_FRAME_STALL_S`; losing the time is the cheaper of the two, and the recording is pulled back into line by the
  ordinary sync a frame later.
- **Leaving the device open means draining what it hears.** Both capture queues are unbounded and a strike window holds 341 ms of audio, so a
  long pause with the guitar in hand would fill memory with sound belonging to no moment in the song. The paused branch of `update()` throws
  it away every frame.
- **The clock now starts AFTER the slow work of resuming, not before.** Set first, whatever the device and the decoder take is charged to the
  song and the picture jumps forward by it on the very next frame.

## Changing Instrument Took Longer Than Opening The Song

Which is the tell, because the two go through the same `_load_song`. Whatever is slower has to be something the FIRST open does not have — and
what it does not have is an old screen.

- **The screen being replaced was never torn down.** It went on holding the input stream and the MIDI output port, so the new one opened a
  second of each; on Windows that is a real device open, the cost this project has now paid three times (seeks, the pause, this). It also lost
  the sitting, because `close_session` lives in `stop_audio` and nothing on this path called it — an hour of practice quietly gone for having
  switched track.
- **The file was unpacked twice per open and twice again per change.** `_track_options` read the track list for the labels and
  `_playable_track_indices` read it again to ask which are guitars. For a GP6 container that is the BCFZ decompression, twice. It is read once
  now and kept, keyed by the file: a song's tracks cannot change while it sits there being played. Measured: 2 reads per open → 1, and per
  instrument change → 0.

## The Same File, Parsed Three Times For One Keypress

"Der Spurwechsel dauert noch immer sehr lange." The screen teardown and the double track-list read were fixed a week ago and it was still slow,
because the expensive thing was never those. `_load_song` reads the file for the NOTES, then again for the MIDI backing, then again for the guide
track — the same bytes, the same parse, three times, and a track change does all three afresh.

Measured on the player's own files, the cost is entirely in the XML parse and none of it in the unzipping:

| | GPIF | unzip | parse | whole track change |
|---|---|---|---|---|
| 4 Non Blondes | 3.9 MB | 5.0 ms | **150.8 ms** | 712 → **184 ms** |
| Kid Rock | 2.9 MB | 4.0 ms | **142.4 ms** | 494 → **138 ms** |
| Papa Roach | 0.4 MB | 0.6 ms | 10.1 ms | 60 → **24 ms** |

- **`_gpif_root` keeps the parsed document**, keyed by size and modification time — the same rule the song index uses, so a file that has not
  changed is not read again and one that HAS is read afresh rather than believed.
- **Nothing in the loader mutates the tree** (every `append` in that module is to a Python list), which is what makes one parse shareable and
  is asserted rather than assumed.
- **Two entries.** Nothing here works on more than one song at a time, and a stale tree would be far worse than a slow one.
- What is left is the three note extractions themselves, which are real work per track: 35 ms each on the biggest file.

## A Dropout While Measuring Is Not A Dropout While Playing

"Beim Syncen zeigt es ab und zu an, dass über 130 audio dropouts waren." `Ctrl+S` is seconds of FFT over the whole recording on a worker
thread, and on a laptop that is enough to starve the audio callback. But **the measurement does not use the microphone**, and the player is not
meant to be playing during it — so those dropouts cost nothing, while the same number during a run loses notes at random.

Counted together, a harmless number and a serious one look identical, which is the fault this project keeps paying for. `dropped_while_busy` is
counted apart and the run log says so: `dropped_buffers 130 (130 of them while measuring — those cost nothing)`.

**This is instrumentation, not a fix, and must not be written up as one.** That the sync thread is what starves the callback is the
best-founded suspect and has not been shown: it cannot be reproduced here, since this machine has no input device. The next log answers it.

## What Is In A Song, Without Opening It

The song list says how many instruments a file holds and how each is tuned. Both answers need the file unpacked — and for a GP6 container,
decompressed first — which is far too slow to do for a whole folder while the player waits for a list to appear. So `tabs/song_index.py` reads
it once and remembers.

- **Kept on disk**, keyed by the file's size and modification time. A file that has not changed is never opened again; one that HAS changed is
  read afresh rather than believed — the same rule as every other cache here.
- **Read on a thread, newest file first**, so the list is on screen from the first frame and fills itself in. A song copied in a minute ago is
  the one being looked for. While it runs the header says `reading songs… 12/240`, because rows that fill themselves in need explaining.
- **A song not yet read shows nothing, and is filtered OUT rather than in.** With the filter on, a row with no answer yet would read as an
  answer of "yes" and the count beside it would be wrong.
- **Only GUITAR tracks are counted.** A drum track's "tuning" is not a tuning, a bass has four strings and a piano none, so counting any of
  them makes the number answer a different question. A file holding no guitar says **"no guitar track"** in as many words, and one that would
  not parse says "could not be read" — three states that must never look alike, because a blank row already means "not read yet". The track
  picker INSIDE a song still falls back to offering everything, so such a file can still be opened; the list is answering a different question.
- **`ENTRY_VERSION` is bumped when what an entry MEANS changes**, not when the code does. It went to 2 here: entries written while non-guitar
  tracks were counted hold a number nobody asked for, and are re-read rather than believed.
- **The same tuning six times is said once.** Six guitar tracks in standard tuning is one answer, not six; past two distinct tunings the rest
  are counted (`+2`) rather than listed.
- **`TAB` steps through the tunings the folder actually contains** and back to all of them — built fresh on every press, so a song indexed
  since the last one can join, and never offering a tuning that would empty the list. Not a letter, because the search box takes those; and the
  same key that steps through a song's tracks once one is open, which is the same idea one level up.
- **Letters, not names.** `tuning_name` knows "Drop D", but an unnamed tuning has to fall back to the letters anyway, so the letters are the
  answer here and the name is left to the tuning HUD. They read low string first — "E A D G B E" — which is the order a player tunes in.
- **The row is laid out from the right edge inwards** and the song name is cut to what is left. A long title would otherwise run under the
  score, and the thing it collides with is the thing being compared.

## What Grew With The Length Of The Song

After the text cache the frame was fine and the app still stuttered "now and then while playing" — and worse the longer the song had been
running, which is the whole clue. Two loops started at the beginning of the song every time they ran, and both are asked once per STRIKE, so
the cost arrived in bursts exactly when the hands were busiest. Measured on 4200 notes of sixteenths:

| | at 5 s | at 60 s | at 150 s |
|---|---|---|---|
| `get_active_notes_at_time` (up to 5 per strike) | 13 µs | 144 µs | **374 µs** |
| after | 2.1 µs | 2.5 µs | **2.4 µs** |
| `_mark_missed_notes` (1 per strike) | — | — | **5.6 ms** |
| after | — | — | **0.023 ms** |

- **A note that is sounding cannot have started before the longest note in the song.** That bound turns "which notes are sounding" from a scan
  of everything so far into a slice, and it is exact rather than a guess — `_longest_ms` is computed once at construction.
- **The missed-note sweep only looks at what has gone past since the last look.** Nothing before the mark can still be PENDING: that loop is
  what resolves them, and a note only becomes PENDING again on `reset()` — which puts the mark back to zero. A song position that moves
  BACKWARDS without a reset also resets it, rather than trusting a mark that describes a different moment.
- **`Timeline.duration_ms` was a property that scanned every note**, called twice a frame. Cached at construction with the rest.
- **The MIDI seek copied every event up to the seek point.** `get_program_changes_before` did `self._events[:end]` and scanned it, to find
  the handful of instrument assignments — 0.87 ms three minutes into a full arrangement, per player, on every seek, and a held arrow key is 25
  of them a second. The program changes are picked out once at construction instead.
- **What was profiled and found healthy**, so nobody looks there twice: the audio callback takes **3 % of its 11.6 ms hop** (aubio's own
  `process` is nearly all of it); MIDI's per-frame `update` is 3 µs; the MP3's per-frame path is O(1) arithmetic — its cost is entirely in the
  seeks below. A whole frame of a dense song at 2.5 minutes, update and render together, is **3.2 ms of 16.7**.
- **The MP3's re-seek backs off when it is not working.** Each one decodes the file up to that point, so a correction repeated every 1.5 s is
  a stutter bought with nothing — and a bigger offset makes each attempt more expensive, which is what the player noticed. If the drift after
  a correction is no better than before it, the gap doubles up to `MAX_RESYNC_GAP_MS`; holding sync puts it straight back. `mp3_worst_seek_ms`
  in the run log says what a seek actually costs, because without it the stall cannot be told from the drift it was meant to cure.

## The Board The Notes Sit On

Compared side by side with the reference the player reads without thinking, the gap was not the notes — it was that ours had nowhere to sit.
Six lines in an empty band give the eye nothing to rest on, so the only way to know where you are is to READ the number, which is the thing
that is hard to read in the first place.

- **The bar lines are drawn across the board**, before the strings so the strings lie over them the way they do on a guitar. The bar, not the
  beat: every beat is a picket fence behind the notes, and the bar is the unit a player counts in anyway.
- **A bar line whispers.** It was drawn as a lit nickel-silver wire and that was too loud — the eye went to it instead of to the notes, which
  is the opposite of what a landmark is for. `BAR_LINE_COLOR` is barely above the board and slightly COOLER than it, so it reads as a line ON
  the wood rather than as an object of its own. A landmark is noticed when looked for and not otherwise.
- **And they are thinned out rather than drawn at any spacing.** A fast song puts bars a few pixels apart. Past `MIN_BAR_LINE_GAP_PX` every
  second bar is drawn, then every fourth — halving, so the lines stay on real bar boundaries, where a fixed pixel spacing would drift off the
  beat and stop meaning anything.
- **The three lowest strings are brass and visibly thicker** (`STRING_THICKNESS` 1-6, `WOUND_TINT`), each drawn as a dark core with a lighter
  highlight so it reads as round rather than as a thick line. That is the cue that tells the low half of the board apart without reading
  anything — which is the entire point of drawing a fretboard instead of six rows.
- **The hit line stands proud of the board**, top and bottom (`HIT_LINE_OVERHANG_PX`). Flush with the edge it is one more vertical among the
  fret wires; running past it, it reads as the thing the board scrolls THROUGH — and the overhang stays visible where a long note covers the
  line itself.
- **The board is an object lying on a background, and only reads as one if the two differ.** They were ten points apart, near-black on
  near-black, so the board dissolved into the screen and the notes floated. Dark warm wood on a cool grey now: the same relationship the
  reference uses (dark fretboard, bright surround) at the brightness a dark theme is chosen for.
- **The string palette was re-sampled from the reference itself**, and the check is what makes it safe: its RED sits at (248, 98, 98), within
  a few points of `feedback_miss`. A string that looks like a missed note is exactly the collision this project already fixed once, so that
  hue is not in the set and two colours of the same family stand in for it. Neighbouring lanes never share a hue, because the lane above is
  the one a note can be confused with — asserted, not eyeballed.

## A Chord Is A Shape, And Six Lanes Cannot Show One

"Schaffen wir alternativ eine ähnliche View zu dieser bei Akkorden?" — the reference app keeps two chord diagrams in the corner: the grip being
played and the one coming next. Six fret numbers spread down six lanes say which notes to play and nothing whatsoever about the shape the hand
has to make, which is why every songbook, every chord app and Yousician draw a grid.

**What the files carry was measured before anything was drawn**, across the player's own three songs:

| | Whats up | Kid Rock | Leave A Light On |
|---|---|---|---|
| moments with two strings or more | 100 of 280 | 12 of 444 | 251 of 533 |
| **of those, nameable** | **100 %** | 92 % | 96 % |
| **distinct grips in the whole song** | **3** | 7 | 13 |

- **The shape is always there.** Every note has a string and a fret, so the grid comes out of the tab itself — a reading of what is written,
  which is the line `chords.py` already walks.
- **A song's whole vocabulary is three to thirteen grips**, so it is built once per song (`changes_in`) and never in a frame. Measured on the
  board: 0.25 ms of a 16.7 ms budget.
- **Which FINGER is not available and is therefore not drawn.** Two of the three songs carry no fingering at all; the third gives a finger for
  **41 of 120 positions** and `finger="None"` for the rest. A diagram that colours the fingers on one song and greys them on the next teaches
  nothing, and colouring a guess would be the invention this project refuses everywhere else.
- **A GP file MAY carry the transcriber's own diagrams** (`DiagramCollection`, with muted and barred strings) and one of the three does, with
  15 items against the 13 shapes computed from the notes. Not read: a picture that is better on one song in three is worse than one that is the
  same everywhere.
- **The card shows the grip being PLAYED, not the nearest one.** A chord is held until the next one starts, so a card that flipped at the
  halfway point would take the shape away exactly while the hand is still on it.
- **Only the CHANGES are kept.** 241 chord moments in one song are 86 grip changes: showing the same grip again at every strum is eight bars of
  noise and buries the moment that actually needs preparing — the same rule the chord NAMES on the board already follow.
- **Silent on a song that has none.** Kid Rock writes 12 chords in 444 moments; a panel that is always there and usually empty is a panel
  nobody looks at.
- **The diagram lies the way the BOARD does**: strings across, low E at the bottom, frets left to right from the nut. A songbook prints the grid
  upright with the low string on the left, and this app draws a tab the other way everywhere else -- mixing the two orientations means rotating
  the picture in your head between one glance and the next. The name stays at the top, where a card is read from. `string_rows()` is the one
  implementation of where a string lands, and the test asserts the ORDER matches the lanes rather than any particular pixel.
- **The two cards share the top left corner with the HUD text, and the text is what moves.** Drawn over each other neither can be read: the
  player's screenshot has the song title, the track, the tuning and the sync line straight through the diagrams. `_hud_left_x()` starts the left
  column past the cards while they are up, and at 12 px when they are not, so nothing moves for a player who never turns this on.
- **Both cards are the SAME size, and bigger than the first version.** The second was smaller to say "this one is next" -- the label already says
  that, and being smaller made the grip you have to PREPARE the harder of the two to read. At 210x184 they still clear the board, which matters
  because each chord block writes its name just above itself and a card hanging into the lanes would cover it.
- **`Shift+C` is tested before the plain `C`** that raises the noise gate. An `elif` chain is read in order, so a shifted key placed after its
  unshifted twin is never reached — which is exactly how the first version of this shipped inert.

**And the blocks go UNDER the note heads, not instead of them.** The reference app draws a chord as one coloured slab and nothing else, which it
can afford because it does not report per-string feedback; this app spent a whole chapter learning to say WHICH string was wrong, and a slab
would throw that away. So the block is a tint that spans the strings of the grip with the name at its leading edge, and the heads keep their own
colours on top of it. Strictly more than six separate heads, never less.

- **The block shows the WORST verdict of its strings.** A chord with one string wrong is not a chord that went well, and the block cannot show
  six answers; the detail is on the heads.
- **Its name sits at the LEADING edge**, because that is the moment the hand has to be ready — the same reason a note's leading edge is its time.
- **One switch for both halves.** Cards and blocks are one idea, and two keys for two halves of an answer is how a panel ends up with settings
  nobody can find. **Off by default**: it is an extension to the normal view, not the view.
- **The blocks are cached surfaces**, for the same reason the note heads are: an SRCALPHA surface per block per frame cost **2.0 ms of a 16.7 ms
  budget** on a real song. Cached it is **0.94 ms** and the cache holds six entries, because a song chooses one head size and the blocks come in
  very few sizes with it.

## The Chord Name Is Not In The Tab Either

Guitar Pro has a field for it, and it is empty: **5601 beats in the player's own tab, not one chord name** — the same story as the fingering.
So `tabs/chords.py` reads it out of the notes, which is a reading of what is written rather than an invention, and that is the line it has to
stay on the right side of. A name that is wrong now and then teaches the player to distrust the line, and then it is worth nothing even when
it is right.

- **It abstains, and abstaining is the commonest answer.** A run of single notes has no chord. Two notes are a chord only when they are a
  fifth (a fourth counts — that is the same fifth inverted); calling a third "C" would be a claim about a note nobody played.
- **Three or more must match a quality EXACTLY** on pitch classes. The one exception is a seventh without its fifth, because guitarists drop
  it constantly and the shape is unambiguous. Dropping a note from a TRIAD leaves something that is not a triad, and naming it anyway is the
  guess this refuses to make.
- **Named over its bass** when the bass is not the root (`Em/B`), and where two readings fit, the one whose root is in the bass wins —
  C-E-G-A is `C6`, the same notes over A are `Am7`. Both are what a player would call it.
- **Drawn at the CHANGE, not on every beat.** A name repeated over eight bars of the same chord is eight bars of noise; the moment the hand
  has to move is the thing worth seeing. Built once per song, because this display has been bitten twice by work that looked cheap until it
  ran once a frame.

## A Note Is Not Over Because The Clock Passed It

The player reported a note going DARK for a moment and then turning green, and being distracted by it. It was not a glitch — it was the app
drawing a state it had no business showing.

`get_note_color` ended with `dimmed(base_color) if is_past else base_color`, and `is_past` was `note.timestamp_ms < playback_ms`. So a note
was dimmed the instant its written time crossed the hit line. The verdict cannot arrive that soon: the strike is still inside the hit window
(200 ms) and the late window (370 ms) beyond it, and a chord verdict trails its strike by ~380 ms by design. **The dark phase was the whole
width of the window, drawn as "already missed".**

- **The matcher decides, not the clock.** While it still has the note PENDING the note keeps its full colour; once it is resolved the feedback
  effect takes over and paints hit, close or miss. One definition inside `_draw_notes`, so the technique marks and badges follow the same rule
  rather than each deciding for itself.
- **With audio OFF the clock is still the answer**, because nothing is coming to decide it and there is nothing to wait for.
- The test asserts the PROPERTY rather than a colour: full colour at 50, 100 and 150 ms past the note, then green — with nothing dimmed in
  between.

## Nothing Here Grows With The Song, And That Is Asserted

Three loops that began at the start of the song have now been found in this codebase, each one arriving as "it stutters now and then". The
fretboard and the chord names add two more per-frame loops over per-song lists, so the property is measured rather than assumed:

| song | chord changes | bars | one frame at 60 s |
|---|---|---|---|
| 1.5 min | 200 | 52 | 8.84 ms |
| 6 min | 800 | 202 | 8.94 ms |
| **24 min** | **3200** | **802** | **8.93 ms** |

Sixteen times the song costs **1 %**, so the loops stay: a bisect for a hundred-element list would be bookkeeping to save nothing. Seeking is
0.002 ms and an eight-minute MP3 offset changes the frame not at all. `tests/test_scaling.py` holds all of it, including that
`_build_chord_names` runs **once per song and never in a frame**.

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
├── dashboard.py         # the practice dashboard, written when the app closes
├── matcher.py           # note matching engine (hit/close/miss)
├── practice_log.py      # one line per session: minutes and notes struck
├── progress.py          # per-song progress tracking
├── runs.py              # every run of a song, one character per note
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
│   ├── gpx.py           # Guitar Pro 6 containers (BCFZ/BCFS) → GPIF XML
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
    ├── settings_menu.py    # everything set once, and what it is set to
    ├── stats_view.py       # the list of runs, and two of them stacked
    ├── strip.py            # the bottom strip: where you are, how it went, spooling
    ├── device_menu.py
    └── download_menu.py
```

## Testing

- `tests/test_detector.py` — feed known sine waves to aubio, verify correct note detection
- `tests/test_loader.py` — load a reference GP5 file, verify extracted notes match expected
- `tests/test_timeline.py` — verify timeline tick advancement, note activation windows
- `tests/test_downloader.py` — Songsterr search/download with mocked urllib responses
- `tests/test_strip.py` — the bottom strip: the arithmetic without a screen, and that the mouse really reaches it
- `tests/test_review.py` — what could not be judged, that a seek keeps the run, and the right-drag loop
- `tests/test_runs.py` — the stored runs, and the two synthetic ones, on strings and without a screen
- `tests/test_stats_view.py` — the list, picking two, and that marking a passage does not start playing
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

## The Strip Along The Bottom

*"Ich hätte gerne eine Anzeige am unteren Bildrand, wo ich im Song stehe. Hier könnten wir auch den %-Wert hinschreiben und aufteilen 82 %
(groß) und kleiner 90 % timing, 76 % Right Notes. Die Fortschrittsanzeige soll auch zum Spulen verwendet werden können. Sie ist wie eine
vereinfachte Miniatur des Tabs."*

One band across the bottom that is three things at once: where you are, how it went, and a way to move. `ui/strip.py` is the arithmetic and
nothing else -- no drawing, so it is tested without a screen, and because the drawing and the MOUSE need the same answers and must never
disagree about them. One implementation of "x is this millisecond" is what stops a click landing a bar away from where the marker was.

**The split was free, and that is the finding.** `MatchType.CLOSE` has meant *the right note played off the beat* since the matcher was
written, so the three numbers the player drew fall straight out of what `get_statistics()` already counts and nothing has to be measured
again:

| | |
|---|---|
| the big one | `hits / total` -- the right note, on time |
| Right Notes | `(hits + close) / total` |
| Timing | `hits / (hits + close)` -- of the right notes, how many landed |

Over what was REACHED, which is what `get_statistics` counts: a song abandoned at bar 9 reports the first nine bars. That is the honest half,
and the reason the numbers sit next to the clock, which says how far the run got. **`None` where the denominator is empty, never `0.0`** --
nothing played is not the same as everything missed, and a zero would claim it was, on the first bar of every run.

### Its height is a constant on purpose, and the reason is a loop this project has already paid for

The footer's height is MEASURED because it wraps. The strip's must not be, because the music's bottom margin is taken from it and the board's
note height from what is left -- and the footer already carries `N s ahead`, which comes out of that note height. A band sized from what the
strip DRAWS would close that circle, which is the 44.4 -> 44.5 px walk that `test_a_second_frame_lays_nothing_out` caught when bars-per-row
went into the footer.

So: what the band TAKES out of the music is a constant; where it is DRAWN is measured off the footer's top, because a band at a fixed height is
the fault this screen has been fixed for twice already, at the sync panel and at the completion overlay.

**The numbers column is a constant too, and that one was a real bug before it was a rule.** The miniature starts where the column ends, so a
column that appeared when the first verdict landed slid the whole song sideways, one bar into every run -- and would have taken a drag in
progress with it. It is asked of the AUDIO now, not of the score, and stays blank until there is something to say; the only thing that moves it
is `A`. Found by a verdict landing on the wrong pixel in a test, which is exactly what it would have looked like on screen.

**And its width was fitted by eye and was wrong.** At 168 px the miniature drew over the word "Notes". Measured at the sizes the strip actually
uses -- "100%" is 64 px of consolas 26, "100%  Right Notes" is 98 px of arial 12 -- it needs 186, and a test asserts the room rather than the
number, so a font change fails in the suite instead of on the player's screen.

### Nothing in it grows with the song

- **The miniature is built ONCE** per song, size and filter, into a surface that is then blitted. It walks every note -- which is the loop
  `_draw_tab_page` had to move out of the frame at 12.4 ms against a 16.7 ms budget. The dots are deduplicated by pixel, because a four-minute
  song puts dozens of notes on one pixel of one row. What survives the squeeze is DENSITY, which is what the miniature is for: a solo looks like
  a solo and a held chord looks like a gap, without reading anything.
- **The verdicts are painted once each, onto a layer of their own.** A note far enough behind the playhead can no longer change its mind -- the
  hit window, the late window, the chord verdict that trails its strike by ~380 ms and the rescue that arrives after the note timed out are all
  inside `SETTLE_MS` (1200 ms) -- so it is drawn and never looked at again. What is left is the last second of music, a handful of notes, redrawn
  each frame because a rescue may still turn one of them green.
- **A jump is stepped over rather than walked, and that is honest before it is cheap.** A seek puts every note back to PENDING and the sweep then
  marks everything behind the playhead missed -- `hits 0` in a run log is a seek, not a detection failure, which is the trap `seeks` is in the
  header for. Repainting from the start would fill the strip with a run nobody played, AND walk the whole song on every loop turn, which at a
  held arrow key is 25 walks a second. Past `JUMP_MS` (2 s) the watermark moves without painting.

**And the scaling test caught something that was not mine.** `test_a_song_four_times_as_long_draws_at_the_same_speed` failed at 1.90x against
its 1.6 budget, and the profile named a loop that was there already: `_last_note_end_ms()` walked every note in the song and is asked three
times a frame for the HUD's outro line -- **four million iterations over the eighty frames the test times**, dominating everything else. It was
sitting at 1.51x before this branch and the strip pushed it over. Computed once now: **1.51x -> 1.12x**, so the frame is materially better than
it was rather than merely not worse. **Fourth time this shape has been found here**, and it arrives the same way every time: "it stutters now
and then, and worse the longer the song has been running."

### The mouse had never reached this screen at all

`handle_event` returned `None` for every event that was not a key, so a pointer was dropped before anything could look at it. It is checked
before that gate now, and the strip is the only thing on the playing screen the mouse means anything to.

- **A click jumps straight away.** Waiting for the button to come up would make the one-press case feel like a dead key.
- **A drag moves the MARKER and not the song**, and the song moves once, when the button comes up. This is the property that matters and the
  test asserts it: every seek decodes the recording up to that point, and a dragged mouse fires an event a frame -- the same 25 a second that
  made a held arrow key stutter for seconds at a time. A second seek is skipped when the mouse did not really move.
- **A drag that leaves the strip goes on scrubbing**, clamped, because the mouse does not stop at an edge the hand cannot feel.
- **The preview says where it will land**, in the unit the clock is read in. A drag with nothing but a line to go on is a guess.

### What it draws

One dot per note, on its own string's row, low E at the bottom the way the board and the sheet already lie. It turns green, yellow or red as
the note is judged, so the strip is the record of the run as well as the position -- *"damit kann ich sogar super zurückschauen, wo Fehler
waren"*, one view further out. The looped stretch is shaded, because a loop silently repeating eight bars is the fret-filter trap in another
costume and the strip is the one place that can say so by showing it.

### Three corrections from living with it

- **The score is in one place now.** *"Prozentzahl rechts oben entfernen bitte."* It had been on screen twice for one build, and that was
  against his own rule -- *a line earns its place by saying something that CHANGES and that nothing else on screen says.* The top-right one
  was the duplicate: the strip carries the two numbers it is MADE of, and "82 %" on its own cannot answer "was it my fingers or my timing".
  The clock, the gate and the streak stay up there; only the percentage went.
- **The band is a fifth shorter and the rows sit together** (`STRIP_HEIGHT` 46 -> 37, `ROW_SPREAD` 0.66). *"Die Saiten naeher
  zusammenruecken, das ist bei Yousician auch so."* Spread over the whole height the six rows read as six separate lists of dots; clustered
  they read as ONE object, which is what a miniature is for -- and the margin it leaves is where the playhead and the loop shading are
  legible instead of buried among the notes. **Tighter is the ask; merged into a single ribbon is not**, so the test asserts the row pitch
  still clears `DOT_PX`: at 0.66 of 37 px the rows sit 4.1 px apart against a 3 px dot, so the six colours still say which string. The nine
  pixels come back to the music, since the band is what `_layout` and `_tab_room` take out of it.
- **It is in all three views, and that is now asserted rather than inferred.** *"Bitte in alle 3 Seiten einbauen."* It always was -- the
  strip is drawn from `_draw_hud`, which the board, the sheet and the page all call, which is the same reason the footer and the help
  overlay are not written three times. The test renders each of the three and requires ink in the band, because a view that grew a HUD of
  its own would break it silently and nothing on screen would say which one was missing it.

## What The App Could Not Actually Check

*"Kannst du Dinge, die du nicht bewerten kannst grau machen? Es ist ok, wenn du sie vorerst als gültig zählst, wie bisher auch, aber ich würde
gerne sehen, was du eigentlich nicht beurteilen konntest (zu schnell) oder nicht gehört hast (in einander klingen oder zu leise)."*

**Nothing new had to be measured, which is the finding.** Both answers were already in the matcher and being thrown away at the door:

- `_credit_proved` has separated "the written pitch was heard" from "a STRUM was heard and this string came with it" since the chapter on
  eighty percent that does not feel like eighty percent. That is the "zu schnell" case as well: a chord too close to the next one loses its
  verification window under `MIN_WINDOW_MS`, so it is credited to the strum and nothing convicts or clears the individual strings.
- The strikes that carry nothing a pitch can be read from — pitchless, or a subharmonic naming the chord in the room rather than the
  string — are already recognised at the two places that hand them to `_hold_for_rescue`. That is the "ineinander klingen" case exactly.

So `NoteMatcher.unreliable(note)` answers `"strum"`, `"unreadable"` or `None`, and the colour is **drained rather than replaced**: a note keeps
its hue and loses its conviction. A plain grey would have answered half the question and lost the other half — the verdict still counts, and
the player said so in the same sentence.

- **`unsure()` drains towards the colour's OWN luminance**, not towards a fixed grey, so a bright verdict and a dark one drain by the same
  amount instead of the dark one turning black. Tested as a property: the hue that dominates a verdict still dominates it drained, and the
  three drained verdicts are still three colours.
- **One helper, three views.** `_drained` is asked by the board, the sheet and the strip. Three readers of one question is the fault this
  project has paid for at the repeats, at the transpose and at the shifted keys.
- **A third answer the player asked for is deliberately absent, and it is worth writing down rather than quietly skipping.** "Zu leise"
  cannot be told from "not played" at the level of ONE note: both are silence where a strike should be. The evidence for it exists only for
  the RUN (`level_under_gate_percent`, `level_loudest_db`, `input_hears_the_room`), and inventing it per note would be a guess dressed as
  data. A test pins that a miss with nothing struck stays at full strength.

**And the strikes list is a second structure rather than a read of `strike_trace`.** That trace says of itself that nothing reads it back, and
that promise is worth more than the few hundred bytes saved by breaking it.

## Going Back Used To Destroy What It Went Back To Look At

*"Einen Rückblick, wie ich gespielt habe, sehe ich nun in der Progress bar. Können wir es so machen, dass ich zurückspringen kann und dann auch
in groß sehen, wie die Noten bewertet wurden? Sobald ich auf Play gehe, überschreibe ich die vorigen Werte."*

`seek()` called `matcher.reset()`. So the one action that asks "how did that passage go" was the action that deleted the answer — which is
exactly what `hits 0` in a run log taken after spooling has always meant, and this file has a chapter explaining that number rather than
fixing it.

- **A seek keeps every verdict now.** Nothing about moving the playhead is evidence about the playing.
- **`forget_from(ms)` is what spends them**, called when playing RESUMES, and only from the position. Practising four bars twice no longer
  costs the other four minutes. The price is named rather than hidden: a run that replays a passage is no longer one pass through the song,
  and its percentage is the best of several attempts at the parts repeated.
- **A loop turn forgets its own bars and nothing else.** It used to reset the whole matcher every few seconds, so looping four bars threw
  away everything played before the loop was switched on.
- **A tempo change does the same.** The speed changes what comes next, not what was already heard.

**And the board did not have the verdicts to show.** This is the half the tests missed and a rendered screenshot caught in one glance: the
board's colour came from `FeedbackRenderer`'s effects — which are ANIMATIONS, cleared on every seek — so after scrubbing back the sheet and
the strip carried the run and the board was painted in plain string colours. The record belongs to the matcher and the flash belongs to the
renderer; `get_note_color` takes the verdict as an argument now and falls back to it when its own effect has gone.

## Marking A Passage With The Mouse

*"Ich brauche auch noch eine Möglichkeit, um zu einer Passage zu springen, damit ich diese dann üben kann. Könnte ich das mit der Maus
markieren (wie loop beim spielen) und dann spielen klicken?"*

**The right button draws it.** Left-dragging the strip already spools, and a modifier wants a hand the player does not have free with a guitar
on. Two buttons, two meanings, nothing ambiguous — and the song does not move while the passage is being drawn.

- **It sets the markers, switches the loop on, and goes there — and does not start.** *"Loop setzen, hinspringen, warten."* A view that began
  playing the moment the button came up would spend the first seconds of the passage while the fretting hand was still on the mouse, and score
  them as missed. A song already running is paused, so landing always means landing with the hands free.
- **A right CLICK marks nothing.** The right button is also how a mouse gets put down.
- **The passage is shaded while it is being drawn**, in the live loop's own colour, so the thing being chosen is visible before it is chosen.

## Every Run Of A Song, Kept

*"Ich hätte gerne Vergleiche gemacht von mehreren Durchgängen des selben Songs."*

The app already knew how a run went — the matcher holds a verdict per note and the strip along the bottom draws them — and threw all of it away
the moment the song was left. `progress.py` kept the BEST a song had ever been played, one record overwritten as it improves, which by
construction cannot answer *"is this passage getting better"*: it forgets everything except the peak.

So a run is kept whole, as **one character per note of the timeline**, in `<name>.runs.json` **beside the tab** — *"Durchgänge liegen neben gp im
gleichen Ordner"* — which is the rule the sidecar, the bar map and the recording already follow, and which makes copying the songs folder carry
the history with it. `runs.py` is the arithmetic and the storage and no pygame, so the two synthetic runs below are tested on strings.

- **The case carries what nothing checked.** `h c m` are verdicts something stood behind; `H C M` are the same verdicts drained — a chord string
  credited to a strum nobody could confirm, a note whose window held a strike the detector could make nothing of — and `.` is never judged, which
  covers both "not reached" and "filtered out". It is the same distinction all three views drain the colour for, written down instead of drawn.
- **The counts come out of the string, not from beside it.** `Run.counts()` reads the characters, so the percentage in the list and the dots in
  the bar are the same arithmetic and cannot drift apart — and a synthetic run is counted by exactly the same rule as a real one.
- **A run of another TRACK is listed and not drawn.** It happened, so it belongs in the list; its verdicts describe notes that are not these
  notes, and drawing them against this song would put every dot in the wrong place. `note_count` and the track index are what say so.
- **It is written where the sitting is written** (`close_session`), so either route out of a song reaches it once, and a song opened and left
  without a note being judged writes nothing at all.
- **And it is a belonging of the tab.** `belongings()` is the one reader, so deleting a song takes its runs and renaming one carries them. The
  practice diary is a record of what the player DID and stays either way; these are per-note verdicts of a file that is about to stop existing,
  and the next song to take the name would inherit them.

### Two laptops, one history

*"Import bzw. Zusammenführen wäre sehr nett als Funktion."* The first build shipped the limit rather than the feature: an import is per FILE, so
a `.runs.json` already here kept its own and the other laptop's evenings were simply not taken. That is the right rule for everything else beside
a tab — a setting is one answer and "what this machine has wins" is what stops a sync undoing what you just adjusted — and it is the wrong rule
here, because **a history is a LIST, and two lists of different evenings have an obvious union.**

So the runs are the one belonging that MERGES. `Ctrl+I` pools them, and `tools/merge_stats.py` gets it for free, being a front end for the same
code.

- **Keyed by when it started AND what it says.** Two runs cannot share a microsecond, so the timestamp alone would do; carrying the verdicts as
  well makes the key stricter, and stricter fails the safe way. A doubled run would flatter the history; a dropped one is an evening gone, and
  this exists to stop that.
- **Sorted by TIME, not by which file was read second.** `common_errors` asks what the LAST run did, so after a merge the last run has to be the
  most recent evening. Getting this wrong would not crash anything — it would quietly report a mistake as still-being-made when the other laptop
  had already fixed it.
- **Idempotent**, because nobody remembers whether they already imported: running it twice adds nothing the second time. Same constraint as the
  sitting log, and the reason the key is a pair rather than a count.
- **Only where both sides have one.** A song this machine has never seen, or has never played, is copied whole by the ordinary file loop — the
  same answer by a cheaper route, and running both would count it twice.
- **A `.bak` is left beside it**, the way everything this import rewrites gets one.
- **The tab itself is still never replaced.** Merging the history and overwriting the song being practised are different things, and a test pins
  that the local `.gp5` comes through untouched.

### The two runs nobody played

- **Best ever** takes the best each note has ever been, over every run that fits. Not an evening that happened — it says so — but a real answer
  to *"is this song within reach at all"*: a passage red here was never once played, and a passage green here has been, on some evening, in one
  piece. At equal verdict the one that was actually HEARD wins over the one credited to a strum.
- **Most frequent errors** is *"wo ich in mehr als der Hälfte aller Durchgänge Fehler gemacht habe"*, and three rules stop it claiming what it
  cannot show:
  - **Only runs that reached the note vote on it.** A note nobody got to is not evidence either way, and counting it in the denominator would
    hide a passage at the end of a song that is wrong every single time it is reached.
  - **Only verdicts something stood behind.** *"Ja ausschließen"* — a drained verdict is the app saying it could not tell, so `H`, `C` and `M`
    abstain rather than vote, and they do not shrink the denominator either. The presumption of innocence the chord verifier runs on, one level
    up.
  - **Fixed is fixed.** A note the LAST run played right is dropped however many evenings it went wrong before.
  - **A CLOSE is not a Fehler.** It is the right note played off the beat, which is what the timing percentage answers for; counting it here
    would paint most of a run red and the word would stop meaning anything.

### The list, and two of them stacked

`Shift+D`, or the **Stats** button beside the clock — *"ein kleiner Statistics Knopf im Song (zB rechts oben)"*. A MODE of the playing screen,
not a screen of its own: the timeline, the loop, the clock and the seek are all here, and a comparison with its own would be a second answer to
where the song is. `ui/stats_view.py` owns the keyboard and the mouse while it is up, the way the track picker already does.

- **Opening it stops the clock.** The overlay covers the music, so a song left running behind it is a run being scored through a screen nobody
  can see — half a minute of red bought by reading a statistic. It also makes the one expensive frame free: opening builds a bar per row, which
  is **24.8 ms at 1400 notes and twelve runs**, and nobody sees a dropped frame in a paused song.
- **The bars are the strip's own drawing at another size.** `strip.dot` places a note here exactly as it does down there, so the miniature in the
  list and the miniature under the song are one object and cannot disagree. A drained verdict keeps its hue and loses its conviction here too.
- **`+`/`-` walk from the strip's height to the room there is**, both runs stacked — *"das Minimum ist die Progressbargröße, das Maximum die
  Größe der Standardansicht"*. Past twice the strip's height the six strings are drawn under the dots, for the reason the board draws a
  fretboard rather than a table of rows.
- **The frame is drawn round what is IN it**, in both modes. A border enclosing eight rows and six hundred pixels of nothing says the list failed
  to draw rather than that the player has played eight times.
- **Right-drag either bar to mark a passage**: loop, jump, wait. `take_passage` is one implementation, because two things now mark one — the
  strip under the song and this — and a second copy would be a second answer to what marking one means.
- **The run in progress is in the list before it is saved.** It is written when the song is LEFT, so without it the list would answer "how did
  that go" a song later than it was asked. Its line says it is not saved yet.

### Zooming in, because a whole song is a pixel a note

*"Wie kann ich beim Vergleichen nach rechts fahren? Pfeiltasten wären gut für links rechts, wenn ich etwas zoome."* He is right, and the
version before this could not be scrolled because it could not be zoomed: the whole song always filled the width. On a real song that is
**about a pixel between two notes**, which answers "where did it go wrong" and cannot answer "which note" — and "which" is what a comparison is
opened for.

**Two axes, two key pairs, because they answer different questions.** `+`/`-` is how BIG the bars are drawn, which is his own spec and is
unchanged. `UP`/`DOWN` zoom from the whole song down to a thirty-second of it, and `LEFT`/`RIGHT` move along it by a quarter of what is on
screen — a step the eye can follow across a picture that does not otherwise move.

- **It opens on the whole song, every time.** "Where" is asked before "which", and only the whole picture answers the first.
- **Zooming keeps the MIDDLE.** Zooming towards the left edge walks the thing being looked at off the screen, and the player would have to
  scroll back to it after every press.
- **A note outside the window is not placed at all**, rather than clamped to the edge. Clamping would pile every note before the view onto the
  left margin, which reads as a chord nobody played.
- **Bar lines and their numbers appear once there is room** (`BAR_TICK_PX`, `BAR_NUMBER_PX`). Without them a zoomed-in picture says a note went
  wrong and gives no way to NAME the place — and naming it is what turns "I keep getting this wrong" into a loop and a practice session.
  Thinned out below the spacing where they would be a picket fence behind the notes, which is what the board's own bar lines were thinned for.
- **The dot size is measured now, not fitted.** Two limits: vertically it must not close the gap between two string rows, horizontally it must
  not close the gap between two notes ON THE SAME ROW — the only place two dots can collide, since a row is a string. Taken at the tenth
  percentile of the gaps actually in the window, the same rule and the same reason as `_spacing_percentile` on the board. Zoomed out the
  horizontal limit binds and the dot stays small; zoomed in there is room and it grows, which is the whole point of zooming. Floored at twice
  `DOT_PX` at the overview, where a dot is allowed to touch its neighbour because what is being read there is density and colour.
- **The list rows are always the whole song.** A row is how the runs are told apart, and two rows showing different stretches would be a
  comparison nobody asked for.

**And `convert()` was 4.2 ms of a 5 ms bar.** An arrow press rebuilds two 1852x520 surfaces, and measured thirty times over: `Surface()` is
0.18 ms, `convert()` on top of it is **4.20 ms**, and asking for the display's format up front is **0.13 ms**. Same lesson as the tab page one
step earlier — the cost was never the drawing, it was the pixel format. **What that is worth per FRAME is deliberately not claimed**: the frame
after an arrow reads 12.8-13.0 ms over three runs against one earlier reading of 14.8, and nothing inside the run-to-run spread is a finding.
The operation is measured; the frame is not.

**And the percentage says what it is a percentage OF.** Two runs at 68 % are not the same evening when one covered the whole song and the other
gave up in the third bar, and side by side in a list nothing else says so — the bar shows it, but only to somebody already reading the bar. Each
line carries `judged N % of the notes` whenever that is under 98. Same rule as strikes-heard beside notes-credited, and as the practice speed
beside a take: a number is only readable next to what it is a number of.

**Measured on 1400 notes and twelve runs at 1920x1200**: the closed overlay costs nothing measurable (2.99 ms a frame, unchanged), the list
5.51 ms and the biggest comparison 5.70 ms, against a 16.7 ms budget. Every bar is built once per size and kept, and the loop over the song's
notes runs when a bar is built and never in a frame — which is the loop this codebase has now found growing with the song four times.

### N Walks The Places It Went Wrong

*"Ja bitte"* — to the offer that "Frequent errors" says a passage went badly and leaves the player to FIND it by reading pixels. On the
player's own song that row is 16 notes scattered over 12 places in four minutes, at about four pixels each in the whole-song view. The
statistic was there and the passage was not.

`N` loops the next one and waits: the same landing as a right-drag — loop set, jump, hands free, `SPACE` plays it — because `take_passage`
is one implementation and a second answer to "mark a passage" is how two of them come to mean different things.

- **Grouped by the tab's OWN bars**, not by a number of milliseconds. A bar is what a player counts in, what a loop is set in, and what
  the weakest-section report already names; it is read off the file rather than chosen here, the same reason the board's bar lines sit on
  real bar boundaries instead of at a pixel spacing. `error_nests` takes the verdict string and a bar number per note — integers and
  nothing else — so it is tested without a timeline and without a screen.
- **`NEST_BRIDGE_BARS` is reasoned, not fitted, and that is said out loud.** One clean bar between two wrong ones is part of the same
  passage to practise; two are two passages. There is nothing to fit it against — the run history starts the day it ships — so it is one
  constant to re-measure once a real history exists, and it is named rather than buried.
- **Only a MISS starts a nest.** A `CLOSE` is the right note played off the beat, which the timing percentage already answers, and a
  drained verdict (`M`) is the app saying it could not tell. Sending the player to practise a bar on either would be convicting on
  absence of evidence — the presumption of innocence this file runs on, one level up.
- **It walks the run being LOOKED at**: the row under the cursor in the list, the top one of a comparison. Including the two pretend runs,
  which is the point — "most frequent errors" is the row worth practising and it is the one that cannot say where its clusters are.
- **The overlay stays up and the comparison follows.** Closing it would make every step cost a `Shift+D`; and zoomed in, a nest three bars
  long is off screen more often than not, so the view is centred on it — the position only, never the zoom, which is the player's. In the
  whole-song view nothing moves, because the whole song is already shown.
- **The sentence is said in the overlay's own footer**, not only through `say()`: the panel covers the HUD, so the status note is behind
  it while this is up — a sentence nobody can see is the fault this project has now shipped four times. Room for it is reserved whether or
  not there is one, so pressing `N` cannot resize the panel the list is being read in.

**And it found a bug in the key it borrowed.** `take_passage` set the loop start before clearing the old end, and `_set_loop_start` swaps
the two when the new start is past an end still standing — so marking a LATER passage than the one already looped came out as a loop from
the old end to the new start, a stretch nobody chose. It was there for the right-drag too, and nobody had dragged twice.

### Clicking The Sheet Goes There

*"Klick auf Griffbrett in hybrid View, um an eine Stelle zu springen."*

The sheet is the one view where this is exact, and for the reason the view exists at all: **x owes nothing to the clock there**, so a
pixel is not a time and cannot simply be divided by a speed. `Row.ms_at` reads the click back through the very anchors the layout was
built from, which is what makes the mapping the inverse of the playhead rather than a second opinion about it — two mappings would
disagree by however much the layout squeezed a bar, which is most of a row on anything dense.

- **It snaps to the moment aimed at, not to the pixel** (`Row.moment_at`). Measured before it was changed: clicking the MIDDLE of a note
  head reads **68 to 328 ms past** that note, because a head is a hundred milliseconds wide in time and the note's own moment is its
  leading edge. So a click on a note landed just after it — the one place it is no use. The anchors are every onset the row gave room to
  plus the bar's own edges, so the nearest one is what the hand was aiming at. Nearest in X rather than in time, because the pixels are
  what the eye judged: on a squeezed bar two anchors far apart in time sit a few pixels apart.
- **It does not start or stop the song**, the same as a click on the strip — the two are one gesture at two sizes.
- **The geometry comes from the frame that was DRAWN** (`_sheet_hit`), not from working it out again in the handler: the scroll offset
  comes out of the glide, and asking it a second time would step it.
- The scrolling board is untouched. There a pixel really is a time, and nobody asked.

### Two Keys Meaning Two Things, And The Player Read It Right

*"Beim Vergleich bei + kann ich nicht mehr so weit zoomen, dass die Takte angezeigt werden."*

Measured first, and the bars were never missing: on his own song they appear from zoom step 2 (51 px apart), are numbered from step 3,
and at step 5 four and a half bars fill the width at 408 px apart. **They were on UP/DOWN.** `+`/`-` was the bar HEIGHT — his own spec,
and a key that meant something different from `+`/`-` in every other view of the app. That is the fault this file has now paid for at the
shifted shortcuts and at the scroll knob, and it is the reason a measurement that says "it works" still describes a broken feature.

- **`+`/`-` zooms now** — *"+/- innerhalb Stats Vergleich von 2 Durchgängen zoomt das Griffbrett"* — and the bar height moved to
  **UP/DOWN**, which keeps the setting he asked for. Not onto Shift: on a German keyboard `+` is unshifted and `Shift` gives `*`, so a
  shifted `+` is a key that does not exist on the machine this ships to.
- **The footer names both, with the position in each ladder**, because a knob whose ends are not named is the other half of the same
  fault.

### A Fret Number Fits In A Dot Or It Is Not Drawn

*"Können wir in der höchsten Zoom-Stufe nicht sogar alles anzeigen mit Bundnummern in Noten?"*

Two conditions, both measured rather than felt, and the dot is not made bigger to satisfy either:

- **`FRET_DIGIT_PX` is 11.** At that height a two-digit label is 12 px of type — six pixels a digit, which is the narrowest a digit can be
  and still have a stroke. Below it the number would be ink where a dot says more.
- **The label may take `LABEL_SHARE` (0.9) of the room between two notes on ONE string**, which is the same `row_gap` the dot was sized
  against. One measurement with two readers, so a number can never reach the note beside it — and `row_gap` was extracted out of
  `dot_size` for exactly that, rather than counted twice.
- **The ink is read off the colour actually PAINTED**, not off the note's string. They disagree constantly: the string colours sit at
  luminance 106-174 and the verdicts at 151-232, so a bright green wants black where its own string wanted white. `under` records what
  each layer put down, and a label is rendered once per (fret, ink) pair — two dozen frets and two inks against a couple of thousand notes.
- **Measured on the player's song**: numbers appear at zoom 4 and 5 (dot 15 and 22 px, gaps 25 and 50 px), and the bar is still built in
  1.9 ms. No new zoom steps were added — *"Nur Bundnummern, keine neuen Stufen."*

### How Many Runs For "Frequent Errors"

*"Wie viele Läufe brauche ich um häufige Fehler zu sehen?"* **Two** — and the row can stay away for much longer than that, which is the
half nothing on screen could say.

The rule needs a note wrong in more than half of the runs that REACHED it, and still wrong in the last. With two runs that means wrong in
both. What keeps it empty is the third rule: **a drained verdict abstains** (*"ja ausschließen"*), and on a strummed song most of the red
is exactly that — the run logs put it at 65-90 % across three complete runs of two real songs. So the player can miss the same bar three
evenings running and see no row at all, with nothing to say why.

- **The list says which rule kept it away**: not enough runs and how many there are, everything that kept going wrong went right last
  time, or "no note went wrong in over half of these runs — N of M could not be judged twice". The number of abstentions is the useful
  half and it was the one being left to be guessed at.
- **`_verdicts` is the rule, once.** The row and the sentence read the same generator, so the picture and the reason for the picture
  cannot disagree — the "four readers of one plan" fault, in the one place where being wrong would be invisible.
- **The room for the line is reserved whether or not there is one**, so the list does not jump the moment a second run lands.

### The Song List Takes The Mouse Wheel

*"Songübersicht: Können wir scrollen mit Mausrad erlauben?"* Three rows a notch, and it moves the **cursor** rather than a view of its
own: `_scroll_offset` is derived from the selection on every frame, so a wheel that only moved the view would be dragged straight back by
the next redraw — and everything else on that screen (ENTER, DEL, `R`, the tuner) acts on the selected song, so a list scrolled away from
its cursor answers a question nobody asked. It works with the search box open, because finding a song and browsing what the filter left
are the same job.

### The Run Worth Keeping Was The One Being Destroyed

*"Ich habe das Gefühl, dass nicht alle Durchgänge in den Statistiken gelandet sind."* He was right, and his own three files
said so without any guessing:

| | |
|---|---|
| `progress.json`, Kid Rock | 3 attempts — 46.3 %, 81.2 %, **83.9 %** over 490 notes |
| `<song>.runs.json` | **one** run, **128** of 490 notes judged |
| `practice_log.jsonl`, the same two sittings | `accuracy: null` |

Three files describing one evening, and each of them says something different. The timestamps settle it: the sitting began at
20:27:13, `progress` recorded 83.9 % at **20:32:30** — 317 s into a 311.8 s song, so at the last bar — and the run was stored at
20:35:01, after he had gone back and played the bars from 0:40 to 1:37 again.

**`forget_from` spends the verdicts from the seek onward, and the run was only ever written at the door.** So the complete pass
existed, was scored, went into `progress` — and was then overwritten by the drilling that follows finishing a song, which is the
most ordinary thing a player does. The one pass worth keeping is the easiest one in the app to destroy.

- **The run is banked where `progress` already records: at the last bar.** Leaving writes another only when the verdicts have
  CHANGED since (`_run_stored`), so finishing and walking away is one run rather than two.
- **And the diary asked a question that a seek answers wrongly.** `close_session` took its score from `_song_completed`, which is
  cleared by seeking off the end AND by pressing "loop the weakest section" on the completion screen — both of which happen
  seconds after a song is finished. `_finished_stats` is captured at the last bar instead, so an evening that scored 84 % stops
  writing `accuracy: null`.
- **`started` was the moment the run was SAVED**, not when it began: `runs.make` defaulted to `now` and the only caller ran at the
  door. On this sitting that is 20:35 for playing that started at 20:27. The screen carries the run's own start now, and banking a
  run sets the next one's — the next pass begins where the last was kept. `runs.now_iso` is the one owner of the format, because a
  file holding one local stamp beside one UTC stamp sorts by whichever digits are larger, and `common_errors` reads the LAST run
  off that sort.

The three sittings before the feature existed are genuinely gone and nothing can bring them back. Everything after this is kept.

### What A Run Log Says About Detection, And What It Cannot

*"Es wird unglaublich viel nicht erkannt oder es wird bei Powerchords nur ein Ton erkannt und alles wird grün."* Both halves are
in the log, and the first one is not what it feels like.

**On that run the app credited 84 % of what he reached** — 108 hits, 6 close, 14 missed, of 128 notes. And the 197 strikes are
**1.54 per written note**: the microphone was hearing MORE than the tab asks for, not less.

**Every single one of the 14 red notes had a strike within 300 ms.** Not one was silence. What the detector reported instead:

| the pitch that came back, against the note the tab writes there | of the 50 clean unmatched strikes |
|---|---|
| **a fourth or a fifth away** | **24** |
| nothing written within 300 ms | 11 |
| the same note, already credited to an earlier strike | 3 |
| everything else (a tone, a third, a sixth…) | 12 |

A fourth and a fifth are the interval between adjacent guitar strings and the two notes of a power chord. **Monophonic YIN is
reporting the neighbouring string** — the tab writes one voice of a chord and he sounds the whole thing. Only **2 of the 14** red
notes ever had their own pitch class heard at all, so this is not the matcher throwing a good reading away; the right pitch mostly
never arrived. That is the polyphony this file has measured twice before, on the material where it bites hardest.

**The chord half is visible too, in three numbers.** `chord_windows_judged 1`, `strings_taken_back 1`, on a run that reached two
written chords. The one chord the verifier got to judge, it convicted a string on. The other — five strings at 1:34 — was credited
green from a single strike carrying no pitch, and its verification window was killed by the strum's OWN second onset **93 ms
later**. Three onsets 93 ms apart for one strum, at `onset_threshold 0.05`.

- **So the log now counts the windows that never arrive** (`windows_dropped_short`), beside the ones that were judged. One judged
  and none dropped is a quiet song; one judged and thirty dropped is the whole answer, and until now the two were the same line.
- **What was NOT changed, and why.** The obvious move is to let a clean pitch a fourth away be held for the rescue, the way a
  subharmonic is. That is refused: *an ordinary wrong pitch stays wrong* is this file's own rule, and loosening it turns wrong
  notes green — which is the other half of what he reported in the same sentence. A rule that fixes one complaint by making the
  other worse is not a fix.
- **And nothing here licenses a threshold.** One song, two chords, no recording of what was actually played. Every chord constant
  in this file was fitted against `record_reference.py --play-along` takes, and the honest next step is one of those **on a song
  where the power chords are** — not another measurement of a log that cannot say what the hands did.

**Two things the log cleared on the way past.** `level_room_db -54.7` with the gate at `-50` by hand is not a setting mistake: the
automatic rule is `room + 6 dB` capped at `MAX_GATE_DB`, which lands on the same −50. His signal chain has a floor 15 to 30 dB
louder than the takes the gate was fitted against, and no value in the app reaches under that. And `build unknown build` — the
stamp did not survive into whatever he is running, so which version wrote this log is not knowable, which is the one question that
line exists to answer.

### The Same Run, Twice, And A Run Nobody Played

*"Diese beiden Runs schauen gleich aus."* They were the same run. Two faults sat next to each other in the banking that was built last session.

**Banking offered the run a second time.** `_write_run` has always refused to store a run identical to the last one it stored — and the stats list asked its OWN question, so the moment a run was banked at the last bar the live matcher put the very same verdicts up again, labelled "not saved yet". On his screenshot: `A 19:41 91 % T98 % R93 %` over `B 18:59 91 % T98 % R93 %`, the same bar drawn twice, one of them the banked run and the other the matcher it was read from. `unbanked_run()` is the screen's one answer now; `_write_run` and the list read it.

**And spooling to the last bar was banked as a run.** From his own files:

| | |
|---|---|
| `seeks` | **91** |
| `clock_song_s` / `song_ms` | **44.6 s** of a **208 s** song |
| `played_to_the_end` | True |
| verdicts | 135 hits, **1331 misses** |
| what `progress.json` recorded | an attempt at **9.2 %**, beside two real passes at 90 % |

He scrolled through the song; `_mark_missed_notes` marks every PENDING note behind the playhead as MISS, so the whole song went red and the completion path banked it. **Music a seek jumped over was never in front of the player.** `matcher.skip_to` carries the sweep's mark forward so those notes stay PENDING, which every reader already understands as not reached — `.` in the run history, nothing in the strip, and a score over what was actually played. A backward seek is `forget_from`'s business and is untouched.

- **The mark is carried to where the sweep's CUTOFF will be, not to the position.** Set to the position it sits ahead of the cutoff, which the sweep reads as the song having moved BACKWARDS — and it then starts again from zero and undoes the whole thing. The first version did exactly that and the test caught it.
- **Notes still inside the window at the seek target are judged normally.** They are in front of the player; only what was skipped is spared.

## What The Detector Does On A Real Metal Song

The first play-along takes of a song with power chords in it, recorded because the chapter above could only report a log and not what the hands did. Three takes of the same 60 seconds, read through the real detector against the tab reconstructed from the run log:

| take | picks heard | **right pitch** |
|---|---|---|
| played correctly, 18:46 | 100 % | **88 %** |
| **played deliberately wrong, 18:51** | 84 % | **7 %** |
| played correctly, 19:38 | 99 % | **98 %** |

**A twelve-fold separation, and it answers the complaint that started this.** *"Bei Powerchords wird nur ein Ton erkannt und alles wird grün"* is not what happens on this song: the wrong take scored **14 %** in the app and reads **7 %** at the detector. Of its verdicts, 360 are CLOSE against 82 hits — a one-fret error coming back as the neighbouring semitone, which is the app catching it rather than missing it.

**The noise gate is NOT the lever, and the run that suggested it was measuring two things.** Swept offline over all three takes at a fixed onset threshold:

| gate | correct 18:46 | wrong 18:51 | correct 19:38 |
|---|---|---|---|
| −80 … −45 dB | **88 %** right | **8 %** | **98 %** |
| −40 dB | 87 % | 1 % | 80 % |
| −30 dB | 66 % | 0 % | 0 % |

**Flat from −80 to −45 and then a cliff**, on every take — the same shape `sweep_noise_gate.py` found. So pressing `X` twice (−50 → −60 dB) changed `level_under_gate_percent` from 29 to 18 and changed what the detector reads **not at all**; the 88 % against 98 % is the two takes, not the gate. The app's scores either side (89.8 % and 91.3 %) sit inside his own run-to-run spread on correct takes. Nothing here is a finding about the gate, and saying otherwise would be the third time this file caught itself crediting whichever variable had just moved.

**And the tool that reads a run log as a tab could not read one.** `notes_from_run_log` matched the header line whole — and the log then grew `bar`, `fret`, `tech` and `chord` for the coach, so the one tool whose purpose is scoring a take of a song that is not in `songs/` had been inert since. It reads the header by NAME now. **A reader pinned to a column ORDER is a reader that stops working the next time the writer says more** — and this one failed silently, which is why nobody noticed.

### And The Stamp Made The Confusion It Was Built To End

*"Auf NB1 mache ich normal git pull und python -m pickhero — reicht das nicht?"* It does, and it was quietly reporting the wrong version while doing it.

`build.bat` writes the stamp **into the checkout** (`pickhero/_build_stamp.txt`, gitignored for exactly that reason), and `build_stamp()` preferred it wherever it was found. So any tree that had ever been built named the commit it was built at for ever after: pull, run from source, read `build` in the run log, and it says a version from days ago. **That is the "is it fixed or did it not reach the machine" question this whole feature exists to answer**, reintroduced by the feature.

- **The stamp only counts inside a built EXE.** `_frozen()` asks `sys.frozen` or `_MEIPASS` — does this process carry a stamp of its own, or is the file beside the module a leftover from a build run here.
- **A checkout asks git first and the stamp second**, because a source tree copied without its `.git` has nothing else to say and a stale answer beats "unknown build".
- **The test that pinned the bug said so in its docstring** — *"a bundle that also happens to sit in a git tree is still a bundle"* — which is true of an EXE and false of the tree it was built in. Inverted, and it fails on the old code.

## The Marker Built To Stop The Drift Drifted

*"Wie kann ich meine Beispielaufnahmen ins aktuelle Branch hochladen?"* — asked because the recorder had just told him to switch to a branch nobody was reading. Its own docstring says why that matters, having been written after it happened twice:

> A recording pushed to a branch nobody is reading is a recording that does not exist.

The fix at the time was `UPLOAD_BRANCH`, a file in the repo naming the branch, *"kept up to date by whoever is working on it"*. Nobody did. **It still named a branch from two sessions earlier**, so the hint printed a switch AWAY from the branch the recordings were wanted on — the same fault the marker exists to prevent, one level up, and worse than the checkout it replaced: the checkout was at least right this time.

- **The fallback was better than the thing it fell back from.** With no marker the tool takes the most recently committed `origin/claude/*` branch, which here is the right answer. A hand-kept file is only as fresh as somebody's memory, and it was competing with a fact the remote already holds.
- **So the marker is CHECKED rather than believed.** A work branch with newer commits than the one it names wins; the marker still decides a tie and the case where the remote lists nothing. Neither source is trusted outright, because a branch is a fact and a note about a branch is not.
- **Being overruled is said out loud** — *"UPLOAD_BRANCH still says 'X', which has older commits"*. A name nobody expected is worse than no name, and a silent correction is how the next stale marker goes unnoticed for a fortnight.
- **The rule is a pure function** (`pick_upload_branch`), so the nine cases are asserted without a git repo. What talks to git is the half that only lists refs.

## The Build File Nothing Ever Ran

`pickhero.spec` is Python, and the only machine that executes it is the one doing a Windows release. So when the ffmpeg bundling was added it went in six lines ABOVE `binaries = []`:

```python
for _ffmpeg in glob.glob(os.path.join("tools", "ffmpeg*")):
    binaries.append((_ffmpeg, "."))      # line 47
...
binaries = []                            # line 54
```

**Two faults in three lines, and the suite could not see either.** It raises `NameError` before PyInstaller reads a single module — and had it not raised, line 54 would have thrown the entry away, so ffmpeg would have been silently absent from the EXE and the downloaded audio would have landed on disk and not played. Which is this project's oldest failure mode, in the one file no test had ever opened.

- **The check is on the PROPERTY, not the line.** `tests/test_spec.py` parses the spec and requires every name it reads at module level to be bound above the line that reads it. Grepping for `binaries.append` would pass the next arrangement of the same mistake.
- **It fails on the unfixed spec** — verified, because a test that cannot fail is the thing it is meant to catch.
- **A file that only runs on the build machine is a file that is only tested there**, and "there" is a laptop belonging to somebody who is not writing the code. Everything in the tree that is Python gets read by something in the suite now.

**And the list of lazy imports in it was a hand-kept list, which is only ever as fresh as somebody's memory.** Six pickhero modules that are reached only from inside a function were missing from it — `autosync`, `build_info`, `recommendations`, `merge`, `youtube` and `chord_view` — one of them added by me a day earlier, in the same session that quoted the verovio lesson. So the rule is read off the TREE now: `tests/test_spec.py` walks every module, separates the imports that sit at module level from the ones that only ever run inside a function, and fails when one of the second kind is not named in the spec. Same shape as every other rule here that survived — the property, not the instance.

### A Folder Of Histories Looked Like An Empty Folder

*"Wenn ich im Sync\\songs nur die .mysician.json und die .runs.json ablege, geht es nicht. Da gp, mp3 und songsterr schon auf NB2 sind, sehe
ich keinen Grund, diese jedes Mal mitzukopieren."*

Both halves right. A tab is 0.4 to 3.9 MB and a recording is four to eight; the thing actually being moved is **one character per note** --
`MAX_RUNS` is 50, so a 1800-note song carries at most ~90 KB of history, and a sitting is one line of JSON. Carrying the megabytes back and
forth to move the kilobytes is the wrong shape.

**And the import could not read what he sent, for a reason that was never written down.** `import_songs` walked TABS (`find_songs`, which
globs `GP_EXTENSIONS`) and hung every belonging off the tab it found. A folder holding `<song>.runs.json` and nothing beside it therefore
held no songs, and the report said *"Nothing new"* -- the same sentence it says for a folder that really is empty. Two states, one line, and
the one that means "you did it right and I cannot see it" is the one that costs an evening.

- **A tab makes a song. So does a belonging whose tab is already HERE.** `songs_under` is keyed by STEM now, not by tab, and the second rule
  is the whole feature: the tabs are on the other computer already, which is the premise.
- **A stray file still cannot invent one.** A `.mp3` or a `.json` whose tab this machine has never seen is left alone -- nothing can say
  which song it is a belonging OF, and copying it into the songs folder would make a phantom the list has to explain.
- **`runs.path_for` answers about itself.** Given `Song.runs.json` it returns it rather than `Song.runs.runs.json`; `Path.stem` cannot see a
  two-part suffix, and the alternative was inventing a tab path that does not exist just to ask a question about a file already in hand.
- **A song is NEW when its TAB arrived.** Counting a folder of histories as "3 new songs" would be a count of something else, so the report
  says *"N files added beside songs you already have"* -- and `Report.anything` includes them, or an import that worked perfectly reports
  "Nothing new".

**`Ctrl+E` writes that folder**, mirroring `Ctrl+I` in every respect -- same chooser, same panel, same key-repeat drain -- and what it writes
is exactly what an import reads: `practice_log.jsonl`, `progress.json`, `settings.json`, and `songs/<stem>.runs.json` plus
`<stem>.mysician.json`. No tab, no recording, no bar map. The round trip is one test, because a format with two readers and no test between
them is how the sidecar and the practice log each drifted once.

**The date cutoff he asked for was measured away rather than built.** *"Idealerweise kann ich ein Datum wählen, bis zu dem es zurückgeht."*
The arithmetic says it buys nothing: the whole history over every song is **1-2 MB** against 4-8 MB for one recording, so "without MP3, gp and
songsterr" has already taken 99 % of it -- and the import is idempotent, so exporting the same evenings twice changes nothing the second time.
It would also have cost a trap: `runs.json` stamps in **UTC** and `practice_log.jsonl` in **local time**, so one chosen day compared as a
string against both loses a couple of hours at the boundary on one of them. Put to the player with the numbers, he took the version with no
filter at all.

**What is NOT built, said out loud:** `tools/merge_stats.py` gets no `--export`. It still does the sittings, the best scores and the settings
by hand and knows nothing about songs or runs, so an export from there would write a folder its own `--from` could not fully read back. Half a
front end is worse than one.

## The Anchor Was Measuring The Seek

*"Bei diesem Lauf wurde sehr wenig erkannt. Siehst du Gründe?"* The detector had almost nothing to do with it. On that run (Bad Omens, "Just
Pretend", Drop C, 1318 written notes, 56 % scored):

| | |
|---|---|
| written picks (a chord counted as one) | **484** |
| strikes the microphone produced | **559** |
| strike against the nearest written onset, in the worst section | **−6 to −23 ms** |
| `dropped_buffers` | 0, level −2.6 / −7.6 dB, room −54.9 |

More strikes than picks, on the grid. And 591 notes red.

**What lost them is how STALE each strike was when the matcher saw it** — `playback_ms − adjusted_ms`, which the log has carried per strike all
along and which nobody had ever subtracted:

| song time | staleness | strikes that matched nothing |
|---|---|---|
| 0:50–1:50 | **275–290 ms** | **0–8 %** |
| 2:20–3:20 | **452–455 ms** | **68–93 %** |

That is not a correlation. `hit_window` (150) + `late_window` (259) = **409 ms**, and past it `_mark_missed_notes` has already resolved the note:
the strike arrives to find its note marked MISS 45 ms ago. Same riff, same pitches, same detector, 95 % against 11 %.

**The offset was stable to ±10 ms WITHIN each block** — that is `_follow_recording`'s pull, which carries the anchor correctly and is innocent.
What jumped was the value each re-anchor SET: 29742 at 1:12, 46029 at 2:14, 62861 at 3:46, giving staleness of 280, 455 and **16** ms. Sixteen
is impossible — `OnsetPitchCollector` waits `COLLECT_FRAMES` (139 ms) before it emits anything at all. **So the anchor was not measuring the
pipeline. It was measuring whatever the machine had just been busy with.**

- **`elapsed_ms()` is the ring's sample counter**, running in the capture thread in real time. **`_playback_ms` only moves when a frame advances
  it.** Pairing them anywhere but on the frame pairs "audio time NOW" with "song time as of the last frame", and everything in between is baked
  into the offset for the whole block.
- **Both signs appear, which is what named the mechanism.** `toggle_play` anchors FIRST and starts the clock LAST — deliberately, so the
  recording's seek is not charged to the song (that fix has its own chapter) — so the resume work lands as extra staleness: **+168 ms** at 2:14.
  `seek()` anchors LAST, after the recording has moved, and the next frame then charges that same work to the song clock: **−432 ms** at 3:46,
  which is how a 16 ms pipeline got printed. `_start_audio` is the third instance: it reads the counter, then builds a `NoteMatcher` over the
  whole song, then computes the offset from the reading it took before.
- **The anchor is asked for now and TAKEN on the next frame** (`_reanchor_audio_clock` arms, `_apply_audio_anchor` takes), at the one instant in
  `update()` where the clock has been advanced, wait mode has settled it, and nothing has been matched against it yet. One door for all four
  callers, so re-ordering a resume sequence later cannot reintroduce it.
- **Measured on the player's own strikes, replayed through the real matcher**: as it happened **737 of 1318 (56 %)**; with the staleness at the
  284 ms the healthy blocks showed, **1140 (86 %)**. The app's own count was 709, so the replay is faithful to within 4 %.
- **The test asserts the property, at four different costs of slow work** (0, 50, 300 and 1500 ms): a strike stamped at the ring's current
  position must read as the song position now, whatever the machine was doing. All five fail on the unfixed code.
- **The log carries every anchor now** (`audio_anchors`, `audio_anchor_at`), not just the last one. A run with 112 seeks used a different offset
  in every block and printed one number at the end; reconstructing the rest by hand out of the strike table is what this session was.

**And a correction to my own last write-up.** I told the player the decisive number was missing from the log and would have to be added. It was
not: `adjusted_ms − strike_ms` IS `audio_offset_ms`, in every row of a table that has been written since the run log existed. **A log can carry
an answer for months without anyone subtracting two of its columns** — which is the same fault as a feature that cannot be seen working, one
level up, and it cost the first half of this diagnosis.

### The Grace Period Was A Guess That Happened To Be Close

With the anchor reproducible, the other half of the 409 ms budget is worth reading rather than assuming. `_late_window_ms` was
`150.0 + max(0, -sync_offset)`, with a comment saying the base "covers the onset collector delay" — **and nothing anywhere printed either
number**, so whether it did was not a question the app could answer.

It is not a constant. It is three things that are all known:

```
(COLLECT_FRAMES + 1) x hop / rate   the collector waits 12 hops before it emits
                                    anything at all, plus the callback block
+ one frame                         queued by the capture thread, drained by update()
= 168 ms   at 44.1 kHz and a 512 hop, against an assumed 150
```

**Eighteen milliseconds under, at the commonest configuration this app meets** — and close by luck rather than by arithmetic: at a 48 kHz
device (which is what a USB interface gives in Windows shared mode) the same constants say 155, and at a different `COLLECT_FRAMES` they say
something else again, from a file nobody would think to open from here.

- **One frame at the DISPLAY's budget, not the measured interval.** A grace period that moved with the frame rate would make two runs of the
  same song incomparable, which is the one thing a diagnostic must not do.
- **The pipeline term is capped at `MAX_PIPELINE_GRACE_MS` (300) and the compensated latency is not**, because they are different kinds of
  thing. Measured on the player's take, at every late window **up to 300 the furthest a strike ever reached was exactly `timing_window`**
  (150 ms) — so under the cap the window decides PATIENCE and nothing else. At 325 a strike credited a note 183 ms away and at 500 one 322 ms
  away, through the chord-sibling rule. A compensated latency is arithmetic: it moves `adjusted_ms` earlier by exactly that much, so the note
  has to stay alive by exactly that much, and capping it would make every strike late on a machine that needs a big offset.
- **Floored at the 150 that shipped.** No device may end up with less grace than the value this was measured safe at.

**And on the run that prompted it, this changes nothing**, which is the honest half: at a 284 ms staleness the score is 1140 of 1318 at a
259 ms window and 1140 at 277. The player's own `K` calibration of −109 ms had already carried him over the line. **The window was never the
fault** — that was the anchor, and saying otherwise would be this file crediting whichever variable had just moved, for the fourth time.

**What it buys is that the next run answers the question instead of a session doing it by hand.** The log carries `strike_delay_median`,
`strike_delay_worst_tenth`, `strike_delay_budget` (with the spare, signed) and `strike_delay_over_budget_percent` — the share of strikes that
arrived to find their note already marked missed, next to the count it is a share of. On the run that started this it would have read
**`over_budget_percent 60`** on line four of the log.

**The measurement of the reach measured itself first, which is the sixth time.** `process_detected_notes` runs `_mark_missed_notes` at its
top, so the first probe attributed the sweep's results to the strike that happened to be in hand and reported a 7625 ms reach at every
setting — including the shipped one, where the real answer is a flat 150. The tell was a control that came back broken: a reach that does not
respond to the knob under test is not a reading of that knob.

## Two Tracks Played As One Part

*"Ich möchte 2 Spuren aus einem GP in eine neue kombinieren. Spur 3 ist die Hauptspur. Immer wenn sie leer ist, füll sie mit Spur 2 auf. Wo
beide Töne haben, gewinnt Spur 3."*

A lead guitar that sits out the verses is a track with holes in it, and the rhythm guitar is what fills them. The question the request leaves
open is what "gewinnt" wins, and that was measured before anything was built, on the player's own file (Bad Omens, "Just Pretend"):

| | |
|---|---|
| bars with a note on either track | 81 |
| bars the lead sits out entirely | **25** |
| bars only the lead plays | 14 |
| bars both of them play | 42 |
| **notes wanting the SAME STRING at the same millisecond** | **184** |

**That last row decides the rule.** A guitar cannot play two notes on one string, and GP5 cannot even write it down — the played-strings byte
has one bit per string, so one note is written, never read back, and every byte after it is garbage. `tools/retune.py` paid for that once with
a file that would not open at all. So note-by-note interleaving is not a stricter version of this feature, it is a broken one.

- **The bar is the unit.** A bar the primary plays is the primary's, whole; a bar it does not play at all is taken from the next track in the
  list. Nothing is ever interleaved inside a bar, so **no moment can end up with two notes on one string** — by construction, not by a check.
  On that file: 563 + 1318 notes in, 957 out, **0 collisions**.
- **The cost is named rather than hidden: in all 42 shared bars the filler's notes are dropped**, 8 of the lead's against 24 of the rhythm's in
  the chorus. That is exactly where a solo is *meant* to replace the riff — and it is also the shape a wrong merge has, and counting cannot tell
  the two apart. What can be told apart is a bar the primary barely touches, and there is **one** on this file (bar 132, one note against
  three). So the rule is right for this song and would be wrong for a tab where the lead answers the riff bar by bar; those numbers are how to
  tell, not a guarantee.
- **The press ORDER is the priority.** `M` in the track picker appends a row and `M` again takes it out, so the panel shows `1)` and `2)` and
  not a tick — a tick says which tracks and not which wins, which is the one thing about a merge that has to be readable. The panel also says
  what Enter will DO (`merge: Lead — empty bars filled from Rhythm`), because Enter here reloads the song.
- **The tuning is the one thing that must agree.** A `NoteEvent` carries its string and its fret and the picture is drawn from them, so merging
  a bass into a guitar puts fret numbers on the board that are perfectly readable and a lie about what to press. The refusal is not theoretical:
  the first end-to-end run of this picked tracks 0 and 2 of that file and track 2 is the **bass** — caught by the rule, not by me.
- **A refused merge falls back to its PRIMARY and forgets itself.** The player asked for that track plus a filler, and the track alone is most of
  what they asked for; silence would be a song that opens as a different track for a reason nobody can see. And a refusal that stayed stored
  would say the same sentence at every open for ever.
- **The backing excludes BOTH tracks**, or the filler is heard twice — once under the hands and once in the backing. One `playing` set feeds the
  backing and the guide, rather than each working it out.
- **A run of a merge is not a run of its lead track.** `merged_track_id` is negative, because a real index never is. A stored run carries its
  track and `runs.py` already refuses to DRAW a run of another track against these notes — which is exactly right here and only works if the two
  are told apart. `_track_index` stays the primary, so the picker's `*` still marks a row that exists.
- **Remembered per song** (`song_track_merges`), because it is a property of the ARRANGEMENT and not of this sitting: a lead that sits out the
  verses sits out of them tomorrow too. It is a dict named `song_something`, so `forget_song`, `rename_song`, the sidecar and `merge_stats` pick
  it up without anyone writing the name down — the fourth reader rule, which is the only reason four readers are safe.
- **`track_menu_box` cannot see the merge line.** The panel reserves the room whether or not there is a sentence, and the size is computed from
  the rows alone — asserted on the SIGNATURE, so the loop is impossible rather than merely absent today. A panel that grew under the cursor is
  the 44.4 → 44.5 px walk the footer already paid for.
- **`tabs/merge.py` is arithmetic and nothing else** — no file reading, no pygame — so the rule is tested on bare timelines, and so the app and
  any tool that wants it cannot have two ideas of what a merge is.

## A Mark That Only A Hand Can Remove

*"Brauche eine Möglichkeit, um Songs mit neu zu markieren, wie M für Favorit. Alle Songs, die ich noch nie gespielt habe, sollen ein Neu
haben. Es geht nur weg, wenn ich es von Hand entferne."*

Two halves, and they pull against each other. **Every never-played song has to be marked without anyone marking it** — a folder of fifty songs
must not have to be visited one at a time, which is the "four files" promise this project already broke once. And **the mark has to survive the
song being played**: it is not "songs I have not played", it is a list of what he still intends to learn, and only he says when one is off it.

A derived rule (`new = never played`) does the first and fails the second: the badge would vanish the moment he practised the song, which is
exactly what he said must not happen. A stored list does the second and needs the scan to decide for every song it has never seen — a scan that
invents state, and a sidecar beside every song in the folder to carry it.

- **The stored entry is an OVERRIDE and its absence is a question.** No entry means new exactly while nothing has been played; an entry means
  what it says. So the whole folder is marked on the first run with **nothing written at all**, and a hand-set value is honoured for ever.
- **What closes the gap is pinning at the door.** `_load_song` is the one route into a song and it is reached BEFORE a single attempt can be
  recorded, so a song that is new at that moment has `True` written then. Playing it afterwards changes the history and not the answer.
- **Only one direction is pinned.** "Not new" needs no entry: a song that has been played can never become unplayed, so the derivation goes on
  answering the same thing for ever — and writing it would put a file beside every song in the folder to say what was already known.
- **`Ctrl+N` and `Ctrl+Shift+N`, not a toggle and not a plain letter.** Ctrl for the reason `Ctrl+M` is: `Shift+N` is how a capital N is typed,
  and a filter box that cannot spell "Nirvana" is not a filter box. Set and unset rather than toggle, because while the box is open the note
  under it is the last thing being read, so a toggle means finding out afterwards which way it went. And the plain `N` stays the sort key — a
  shortcut that has been under the player's fingers for months is not worth taking for a mnemonic.
- **`Shift+N` shows only the new ones**, and refuses when nothing is: a filter that empties the list looks exactly like a list that has lost its
  songs. Tested before the plain `N`, because an `if` chain is read in order and a shifted key placed after its unshifted twin is never
  reached — which is how the chord view once shipped inert.
- **The badge is in the streak colour, beside the star, in the left column.** Not the accent: the star and half the header are already drawn in
  it, and a mark sharing its colour with the mark next to it says nothing the position did not. Left of the name, so the eye scans one edge and
  a long title can push neither off the screen.
- **It is a dict named `song_something`**, so `forget_song`, `rename_song`, the sidecar and `merge_stats` carry it without anyone writing the
  name down — and the mark travels to the other laptop, which is what stops that machine deciding it afresh with no history to go on.

### Two faults it uncovered, both of them older than it

- **`shift_held` could not be reached from the song list.** It was written in `scrolling.py` to close the class of fault where a key arrives
  carrying a capital letter with **no shift bit in `event.mod` at all** — and this file recorded it as "used by all eleven of them and by the
  song list", which was not true: the list's own `Shift+M` still asked `event.mod` by hand. **A helper that closes a class of fault only closes
  it where it can be reached**, so it lives in `ui/keys.py` now and both import it. `Shift+M` was fixed on the way past.
- **The song list's footer has been cut at both ends all along.** Measured: **2554 px of text on a 1920 window**, before a single entry was
  added to it — so the first shortcut and the last were simply not there. That is the fault the PLAYING screen has a chapter for, and the fix
  never reached this screen: `_wrap_on_bars` sat in `scrolling.py` where the list could not use it. It is `ui/footer.wrap_on_bars` now, one
  implementation for both, and the bottom of the song list stacks UPWARD from the footer the way the sync panel and the completion overlay were
  each fixed to do. Without it the new key would have been invisible on the very screen it lives on, which is this project's own definition of a
  feature that does not exist.

## The Easier Reading, And The Half Of It That Is Not Cheap

*"Kann man Tabs vereinfachen, ohne dass sie wirklich schlechter klingen beim mitspielen? Weißt du die Strategie von Yousician?"*

**Yousician arranges by hand and says so in the exercise name**: `basic` is a simplified version, `main` is the core without the tricky
details, `full` is what is on the recording — with a second axis for the ROLE (`basic riff`, `main melody`, `chords`, `lead`), up to six
exercises a song, and a filter for the easiest or hardest level. They are separate written arrangements, and the level does **not** move while
you play. Rocksmith is the other model: every PHRASE authored in about twenty levels, each phrase raised and lowered on its own as you play it.

**Measured on the player's own six songs before anything was built**, and the answer is that there are two axes and only one of them is cheap:

| | notes | picks | notes/pick | octave doublings | on the beat |
|---|---|---|---|---|---|
| Godsmack, "Awake" | 2561 | 883 | **2.90** | **34 %** | 35 % |
| 4 Non Blondes | 480 | 280 | 1.71 | 21 % | 39 % |
| Bon Jovi, "I'd Die For You" | 746 | 488 | 1.53 | 5 % | 50 % |
| Kid Rock | 490 | 444 | 1.10 | 5 % | 75 % |
| Thunder, "Love Walked In" | 167 | 163 | **1.02** | **1 %** | 29 % |

- **An octave doubling is a pitch class a LOWER string is already sounding at that moment**, and dropping one removes a note and **not one
  pick**. On the metal song that is 867 notes gone and 883 strums unchanged: the picking hand does what it did and the fretting hand holds a
  smaller shape. **729 of those changes are three strings becoming two** — the classic three-note power chord becoming the two-note one, which
  is the simplification every guitarist already knows.
- **The fifth is NOT that, and reading it as one was the first mistake here.** Measured together with the octaves they came to 60 % and looked
  like a free lunch; a power chord without its fifth is a single note. `chord_verify` cannot confirm either — their partials are a subset of a
  lower note's — but **"the app cannot hear it" and "the ear cannot hear it" are different claims**, and only the first is established.
- **The rhythm is untouched on purpose.** Thunder's solo is 1.02 notes a pick. There is no fat on it, and the only way to make it easier is to
  take PICKS away, which is a different arrangement rather than a reduction of this one — which is exactly what Yousician's `basic` is, and it
  is not this. **So the songs where this is nearly free are the ones that were not the problem**, and the fast solo the player actually
  struggles with gets two notes lighter. The footer says `Q: Simple —` there rather than pretending otherwise.

**Four properties, because nobody here can listen.** Nothing in `tabs/simplify.py` claims a sound; what it claims is that the result is a
SUBSET of what was written in which every moment keeps its pitch classes, keeps its bass, keeps its chord name, and the song keeps every one
of its picks. All four hold on all six songs, asserted rather than assumed — and `test_it_really_does_something_on_a_chord_song` is what stops
a rule that holds them by removing nothing.

- **The stroke nearly broke the headline, and the measurement is what caught it.** A full open chord loses its inner strings too, so strings
  6-5-3 is a sweep that has to MISS the fourth — harder than the chord it replaces. On Kid Rock that turned 0 gapped strums into **5**.
  Five moments in four hundred is small, and a headline that is false five times is what this project writes chapters about: the removals are
  taken from the OUTSIDE in now and each is kept only while what is left is one unbroken sweep. Cost: five notes. An open E major therefore
  comes down to four strings and not three.
- **Three things are never touched**, and each is a bug avoided rather than a nicety: a note carrying a technique (a bend is the music rather
  than the harmony), a note some earlier note hammers or slides INTO (the tab carries no link to it — `_legato_credit` walks to the next note
  on the string, so removing a target hands the credit to the wrong note), and a DEAD note (it sounds no pitch, so it can be neither a doubling
  nor the reason for one).
- **My own two expectations were wrong and the tests said so.** Three E's do not become two, they become **one**: the rule keeps the lowest
  instance of each pitch class and nothing else. And the fixture built to prove "one note removed" had three E's in it.
- **The backing and the guide are untouched.** They are extracted from the FILE, so the band plays the whole song while you play the reduction
  — which is the Yousician situation exactly: `basic` against the full recording.
- **`Q`, which was the only plain letter this screen had left.** Not a mode of an existing key: `E` is already Skip here, and one key meaning
  two things depending on where you are is the fault this file has paid for three times. It is remembered per song (`song_simplify`), it earns
  a footer slot **only while it is on** — an entry that would say "off" on every song ever played is the wallpaper the footer was cut down to
  remove — and `H` carries what it is worth on this song (`on — 867 of 2561 notes left out (34 %)`).
- **A run of the easier reading is not a run of the full song, and that needed no new code.** `Run.fits` already compares `note_count`, so the
  two are listed together and neither is drawn against the other's notes.

## The Filter Box Would Not Let Go

*"Brauche eine Möglichkeit, dass der Textfilter F aktiv ist und ich rausklicke und dann alle anderen Tasten funktionieren, ohne dass ich weiter
in den Filter schreibe. Rausklicken entweder per Maus oder mit Pfeiltasten fahren -> schon kann ich U für Stimmen, M für Favorit, Str+N für Neu
nutzen."*

**The two states were always separate and nothing ever put one down without the other.** `_search_text` is the filter; `_search_active` is only
"the box owns the letters". Every route out of the box cleared both — so a filtered list could not be tuned from (`U`), starred (`M`) or sorted
(`N`) without retyping the filter afterwards. The feature was three lines away the whole time and the state to hold it already existed.

- **A key that acts on the LIST lets go of the box** (`LIST_KEYS`: up, down, page up, page down, home, end), in one place rather than in six,
  because a navigation key added later would otherwise have to remember. `ENTER` is deliberately not in the set: it leaves the screen, so
  dropping the focus first would be a state nobody is ever in.
- **A click in the box takes the letters, a click anywhere else gives them back** — what every text field on every screen does, and the half
  that was asked for by name.
- **`F` RESUMES rather than clears.** Leaving to press `U` and coming back to narrow the filter further is the whole workflow; clearing would
  make the way out a one-way door.
- **`ESC` empties a filter that has been let go**, instead of reaching the quit prompt — a trap on the one screen the player arrives at with
  ESC already under their finger, and the second guard that key has needed.
- **The box says which state it is in.** The caret and the fill are the signal, and the way back is named (`bon   (F edits)`): a filter you
  cannot get back into is one you have to retype.

## The Help Page Documented A Key That Was Off The Screen

*"Mit welchem Knopf habe ich Audio resettet? Steht nicht in h."* It is `Shift+A`, it has been in `help_blocks()` all along, and both halves
of his sentence were right anyway.

**Three whole sections were drawn past the bottom edge.** `_draw_help_overlay` lays the blocks down three columns and moved to the next one
only `if col + 1 < len(columns)` -- so once the third column was full it simply carried on downwards, off the window, with nothing on the page
to say so. Measured at the default 1280x720, where the bottom edge is 690:

| window | what falls off |
|---|---|
| **1280x720** | **"What you see", "Sound and scoring", "S: lining the recording up"** -- y=581 to y=1425 |
| 1600x900 | the sync block |
| 1920x1080 and up | nothing |

`Shift+A` sits in "Sound and scoring", together with `Y`, `Shift+Y`, `D`, `Z`, `Shift+Z`, `G`, `K` and `X/C`, and the whole sync block went
with it. **A feature that cannot be seen working is indistinguishable from one that does not work** -- the same fault as the `D` that wrote a
file and said nothing, and here it cost a round trip asking about a key the page already documents.

- **A full third column starts a PAGE now**, and `H` walks them and closes after the last, the way `Shift+T` walks the three views: the page
  is chosen by LOOKING at it, so what the key has to do is keep going. The heading says `Help 1/2` and the footer names the next page rather
  than "Press H to close" on a page that is not the last.
- **The packing is pure arithmetic** (`help_page_layout`), because a block's height is its heading plus a fixed step per line and needs no
  font metrics -- so the property is asserted without a screen, at five window heights, and `test_the_default_window_really_needs_more_than_one_page`
  pins the premise. Without it the whole class would pass on a page nobody can overflow.
- **A block taller than a whole column is drawn anyway**, at the top of one. Shortening it is a decision for whoever wrote it -- the same
  answer the footer gives a single entry wider than the screen.
- **And the wording was the other half.** The line read `Shift+A: reopen the audio output`. Somebody looking for "reset" finds nothing, so it
  says `reset the audio` first and what that means second.

**What is NOT fixed, and it is the class rather than the instance.** `TestEveryKeyIsWrittenDownSomewhere` maps `pygame.K_*` to a label, so it
is keyed by KEY and not by COMBINATION: `"a": "A: audio"` covers `Shift+A` as far as the suite is concerned, and the same goes for `Shift+B`,
`Shift+C`, `Shift+D`, `Shift+R`, `Shift+S`, `Shift+T`, `Shift+U`, `Shift+Y` and `Shift+Z`. Every one of them could go undocumented without
anything turning red.

## One Map Began Before Zero, And A Nudge Outlived Its Map

*"Warum ist der Song Californication mit dem Video bei Songsterr selbst perfekt sync? Mit Listen only ist es bei mir auch nicht Sync."*

**Songsterr plays the video their map was made for.** Their `video-points` are timestamps into one particular YouTube upload -- 55 entries for
this song, one per video -- so on their site there is nothing to measure and nothing that can be wrong. A player's own MP3 is a different file
with a different start (intro trim plus encoder padding, about 2.6 to 3.6 s here), so exactly ONE constant has to be found. That is the only
thing that can fail, and on this song it does not fail: measured through the app's own path, **48 of 51 windows at 95 ms of scatter**. The two
independent measurements agree to 1-180 ms across five minutes:

| song | listening | Songsterr | apart |
|---|---|---|---|
| 30 s | -3649 ms | -3399 ms | 250 |
| 150 s | -3063 | -3092 | 29 |
| 300 s | -1288 | -1289 | **1** |

So the alignment was never the problem. Two other things were, and neither is about Songsterr.

**One candidate map begins at -40.25 s and killed the other forty-three.** `replace_times` moves every note onto the map and `NoteEvent`
refuses a negative timestamp, so `align_to_bar_times` raised on the first such candidate before any good one had been tried -- and the source
was pinned to Songsterr, so nothing fell back to the listening either. The song had `mp3_sync_points 0` and no way to get any.

- **A uniformly shifted map IS the same map**, which is why `_from_zero` is a normalisation rather than a repair: the fit looks for one
  constant, so moving every bar by S moves that constant by -S and `start - (time + constant)` is unchanged to the millisecond. Asserted
  through the real `_fit_one` with the lags moved by whatever shift the normalisation chose, not by a number written into the test.
- **Only a map that really begins before zero is touched**, so all 38 candidates that worked before are bit-identical. That control is what
  the change rests on.

**And the hand offset outlived the map it was compensating for.** `base_offset_ms` is a nudge ON TOP of the points, and a -3000 ms dialled in
by hand while the song had no map at all was then added to every measured point -- **exactly three seconds out from the first bar to the last**,
which is why "listen only" was no better. `_set_sync_point` has spent the nudge since the day it was written and says why in as many words;
`Ctrl+S` and `Ctrl+Shift+S` never did.

| | points | rate | offset |
|---|---|---|---|
| picking a different recording | cleared | cleared | **cleared** |
| `Shift+S` | adds | -- | **spent** |
| **`Ctrl+S`** (was) | replaced | reset | **left standing** |
| **`Ctrl+Shift+S`** (was) | cleared | reset | **left standing** |

- **The measured points are absolute**, computed against the recording without reference to the nudge, so keeping it is double counting. It is
  dropped and **said out loud** (`your -3.00 s nudge was dropped -- the points measure the offset themselves`): a number dialled in to correct a
  bias in the OLD map is real work, dropping it is right because the new measurement may not carry the same bias, and a number that vanishes
  without a word is the next report.
- **`Ctrl+Shift+S` clears the offset too**, which makes "clear" mean clear and gives the one key that removes an offset nobody wants any more --
  the alternative was pressing `Ctrl+M` until it reached zero.
- **The property is asserted over every path that writes the sync**, not over the one that was reported. Same shape as `shift_held` reaching ten
  shortcuts and not the song list, and as the capability check that disagreed with the permission check on Born To Be My Baby: **a rule applied
  in one of the two places it belongs is a rule that will be found again from the other side.**

## Forty-Four Maps Of One Curve, And A Bar Line At The Video's Start

*"Obwohl songsterr bar map only, Ctrl+S: listening schnell bis 50 %, comparing bei 50 % bleibt haengen. Was wird compared und warum?"*

Nothing was hanging. **Each candidate map costs a full drift curve over the song** -- 3.5 s here, more on a laptop -- and Californication
offered **44 of them**, so `comparing` really was two and a half minutes of arithmetic. And the progress said 50 % throughout because the
arithmetic was wrong in a way only many candidates expose: `0.5 + 0.5 * (taken + 1) / n * f` restarts at a half for EVERY candidate and
reaches 51 % on the first of 44. A measurement doing exactly what it should, reading as a freeze.

**34 of those 44 are one curve shifted** -- Songsterr's own uploads of the same transcription against different videos. The fit's whole job
is to find one constant, so a map differing from another only by a constant is not a second answer to try, it is the same answer written
down again. `_distinct` keeps one of each SHAPE, first wins, so `candidates_for`'s ordering still decides.

| | candidates | after | run |
|---|---|---|---|
| Californication | 44 | **11** | 2.6 min -> **42 s** |
| Reckless | 3 | 3 | 8.6 s |

The bar says `comparing map 3 of 11` now and walks 50 % to 100 % once.

**And one bar line in the map is not a measurement.** Songsterr's bar 0 for Papa Roach's "Reckless" sits at **0.0 s** -- the video's start,
not the first bar line -- so that bar reads **1834 ms where every other bar of the song reads about 2970**. `SyncMap` clamps the segment to
`MAX_RATE` and spreads the rest over the music: the `+11.11 %` first section in the player's run log, and most of the 628 ms the picture was
pulled by.

`_without_spikes` refuses a point sitting further from the LINE BETWEEN ITS NEIGHBOURS than drift could carry it -- `MAX_DRIFT_RATE` over the
distance to each neighbour, floored at `SPIKE_FLOOR_S`. Both are bounds this module already carries; nothing is fitted here.

- **Worst first, then measured again.** One bad point drags the interpolation for the two beside it: on that song bar 1 also fails while bar 0
  is in, and passes the moment it is gone. So exactly one point goes, and it is the one that is wrong.
- **Dropping is the honest repair.** The line between the neighbours is what `simplify` already claims for a point it thinned away, and outside
  the outermost point `SyncMap` extrapolates. Nothing is invented; one reading is refused.
- **Measured: Reckless loses 1 of 71, Californication 2 of 127, and Bon Jovi -- the song that syncs well, and the control this rests on --
  loses none of 149.**

### And then the follow loop answered the question the player actually asked

*"Haben wir selbst Fehler in der Visualisierung?"* No, and this is the measurement rather than an assurance. `_follow_recording` simulated at
60 fps over each stored map, on his own two songs:

| | points | worst lag | snaps |
|---|---|---|---|
| Reckless, Songsterr's bar map | 39 | **206 ms** | 0 |
| **Reckless, the listening** | 17 | **0 ms** | 0 |
| Californication, Songsterr's bar map | 66 | 158 ms | 0 |
| **Californication, the listening** | 20 | **0 ms** | 0 |

**The same loop, the same song, the same recording: 0 ms on one map and 206 on the other.** So the picture follows perfectly what it is given,
and what it is given wobbles. `SYNC_PULL_FRACTION` is 50 ms/s; a bar map that steps 200 ms between two bar lines 3 s apart demands 66 ms/s and
the picture never catches up -- which is *"haengt hinterher"*, and it is over `AUDIBLE_MS`.

**The wobble is Songsterr's own reading and cannot be filtered away**, which is the honest half. Compared against the listening on the same
song, their bar times are up to **345 ms** out around 2:10 -- systematic over several bars, not a spike, so no rule that refuses outliers can
reach it. The spike filter is correctly scoped at "no drift could do this" and stops there.

**So on both of these songs the answer is the listening**, and that is now a measured statement rather than a preference: `Alt+S` -> listen
only, `Ctrl+S`. It also puts a number against the default set two chapters up -- the bar map is instant and cannot fail, and on the player's
own songs it costs 200 ms of lag where the listening costs none.

## Two Numbers For The Same Guess, One Eight Times Too Big And One Eighteen Times Too Small

"Warum wird davor geraten? Es ist viel zu viel Arbeit das davor von Hand zu syncen." The panel read
`before the measured part (0:33-4:45) - the recording is guessed here, and it drifted 2217 ms where it was measured`,
and that number is the reason it looked like a stretch of work. It is the wrong quantity.

Reproduced exactly on his own Californication (41 of 51 windows, covered 0:23-4:55, first point 0:33, drift 2217 ms),
the four windows before the coverage read:

| window | offset read | margin | what happened to it |
|---|---|---|---|
| **0:00** | **3.385 s** | 0.025 | **refused as an outlier** |
| **0:06** | **3.443 s** | 0.067 | **refused as an outlier** |
| 0:12 | 28.66 s | 0.013 | the wrong part of the song, 25 s away |
| 0:18 | 28.66 s | 0.001 | not readable |
| 0:24 | 3.648 s | 0.083 | kept |

**The first two were readings**, at two and five times `MIN_MARGIN`. They were refused because they sit 374 and 290 ms
off the straight line fitted over 0-102 s, against a tolerance of 236 ms -- and the curve over that stretch is not
straight: it rises from 3.385 to a plateau at 3.65 and then falls. A single line through a whole section pushes its
bent ends out.

**So the size of the guess is measurable, and it is a quarter of a second.** Against those two refused readings the map
extrapolates -3658 and -3655 where they measured -3385 and -3443: **273 ms and 212 ms**. The line was claiming 2217.

- **`drift_seen_ms()` is the drift over the WHOLE measured span**, offered as the uncertainty over the 33 seconds being
  extrapolated. Eight times too big, and frightening in a way that sends the player to a workflow they do not need.
- **And the obvious replacement is wrong the other way.** The local rate at the first point is 0.043 %, which over 34 s
  predicts **15 ms** -- eighteen times too small. **The error comes from curvature the map never saw**, so no number
  computed from the stored points can bound it, and a line that prints one is guessing with a decimal point on it.
- **What is known is the distance**, and that is what a player acts on: a few seconds outside is nothing, a minute
  outside is worth a point. The line says how far out it is and which key closes it, and `_gap_text` reads it in the
  unit a transport is read in. The test asserts the PROPERTY -- that no millisecond figure appears at all -- rather
  than the wording.
- **One `Shift+S` closes it**, which is the practical answer and was buried under the number.

**A candidate fix for the filter was measured and NOT built.** Replacing the section-line residual with the
neighbour-based "no drift could do this" test that the bar map already uses (`_without_spikes`) gains Bon Jovi +2,
Born To Be My Baby +3, Thunder +3, Godsmack +1 -- and loses **Kid Rock -5** and What's Up -2, while still not rescuing
Californication's intro: the endpoint sits next to a wrong-chorus match and gets convicted instead of it, by 10 ms of
margin. A change that trades two songs for four is not a fix.

## Strumming Direction Is In The Format And In None Of The Tabs

"Ist eigentlich die Strumming Direction (Strumming Pattern) irgendwo in GPs enthalten?" Guitar Pro carries **two**
separate things, in GP3-5 and in GP6/7/8 alike:

- **Brush** -- `BeatEffect.stroke` (`BeatStroke`: direction and speed), GPIF `<Property name="Brush">`. The chord is
  spread over time, arrow up or down.
- **Pick stroke** -- `BeatEffect.pickStroke`, GPIF `<Property name="PickStroke">`. The n / V marks over the notes,
  which is what a player reads as a strumming pattern.

Counted over the player's eight tabs: **0 of 32 787 beats** carry either. `<AutoBrush/>` appears in all of them and is
a track-level playback setting, not per-beat data. `Effects.gp5` in `tests/fixtures/` has one of each, so the readers
work and the tabs are empty.

Same story as the fingering (0 of 6193) and the chord names (0 of 5601): **the field exists and transcribers do not
fill it in.** Deriving a strumming pattern from the note positions would be a guess dressed as data, which is the
invention this project refuses everywhere else.

## The Sound Card Keeps Its Own Time, And Nobody Was Watching It

"Warum wird hier in Takt 64-67 so wenig erkannt?" Not the detector. Every written pitch in bars 65-67 was heard at
**confidence 1.00**, within 400 ms of where the tab asks for it. What lost them is one column of the run log nobody had
read down:

| bar | written | right pitch found at | conf | **stale** |
|---|---|---|---|---|
| 65 | 208.0 s, MIDI 72 | -400 ms | 1.00 | **737** |
| 66 | 211.2 s, MIDI 69 | -420 ms | 1.00 | **774** |
| 66 | 212.8 s, MIDI 70 | -393 ms | 1.00 | **773** |
| 67 | 214.4 s, MIDI 70 | -43 ms | 1.00 | **860** |
| **68** | 219.2 s, MIDI 74 | +173 ms | 1.00 | **393** -> **hit** |
| 70 | 225.6 s, MIDI 70 | -100 ms | 1.00 | 436 -> hit |
| 71 | 228.8 s, MIDI 65 | -130 ms | 0.99 | 545 -> hit |

`strike_delay_budget` is 630 (`timing_window` + `late_window`). Past it `_mark_missed_notes` has already swept the note,
so the strike arrives to a note that no longer exists. Bars 60-67 sit at 718-860 and go almost entirely red; bars 68-71
sit at 393-545 and go almost entirely green. **Same playing, same detector, one number in between** -- and the number
is why pausing helped, which the player noticed before anything was measured: `_resume_audio` re-anchors.

**The lag is not constant, it WALKS.** Split by anchor block:

| block | song | stale start -> end | |
|---|---|---|---|
| 0 | 126-149 s | 435 -> 558 | +5.3 ms/s |
| **3** | **157-217 s** | **462 -> 829** | **+6.1 ms/s** |
| 4 | 219-230 s | 421 -> 574 | +14.5 ms/s |

Every block starts at ~430 -- exactly `late_window_ms`, the designed pipeline -- and leaves. Measured over block 3,
which contains no anchor and where the offset moved by **-0.8 ms** in sixty seconds:

| | |
|---|---|
| ring buffer's sample counter | **+60 070 ms** |
| song clock (`_playback_ms`) | **+60 436 ms** |
| **ratio** | **1.00610 (+0.61 %)** |

**A strike is stamped from `written / sample_rate` and the song runs on `perf_counter`, and those are two different
clocks.** The mp3 pull is innocent (the offset did not move), and `mp3_worst_drift_ms 58` with `mp3_resyncs 0` says the
OUTPUT device and `perf_counter` agree over the whole run -- so it is the input counter that is 0.61 % slow against both.

- **The app cannot make a sound card keep the wall clock, so it tracks it.** `_track_audio_clock` nudges the offset
  towards what an anchor would set, every frame. Same answer `_follow_recording` gives for the output side, and for the
  same reason.
- **`_apply_audio_anchor` stays what it is: the answer for an EVENT**, where the clocks really jumped and the strikes in
  hand belong to a moment the song has left. This is for the slow walk in between, which no event explains.
- **It creeps and never jumps.** A step is capped at `AUDIO_CLOCK_PULL_FRACTION` (10 %) of the time that really passed,
  so a queued strike cannot be displaced wholesale. It is deliberately more generous than `SYNC_PULL_FRACTION` (5 %):
  that pull moves the PICTURE and must stay invisible, this one moves only the arithmetic that places a strike.
- **Slack first**, `AUDIO_CLOCK_SLACK_MS` (25 ms). `elapsed_ms()` advances one callback block at a time (512 samples,
  11.6 ms) while the song clock moves smoothly, so the error carries a sawtooth of about one block; chasing it would
  hand the quantisation back as jitter. Twice a block, and the real drift crosses it in four seconds.
- **Both directions.** A counter running fast places a strike in the FUTURE, which loses the note just as surely.
- **Wait mode comes right for free.** There `_playback_ms` is held while the audio clock runs on, and the tracking loop
  pulls the offset down to match -- which is what a strike arriving during the freeze should be credited at.
- **The run log carries `audio_clock_ratio` and `audio_clock_pulled_ms`**, because a correction that cannot be seen
  working is indistinguishable from one that does not work. That the loop is WIRED into the frame is asserted by driving
  the real `update()`, not the helper -- this project has shipped an unwired feature four times.

**What it is worth, on the player's own run: 29 % -> 40 %.** 126 of 393 strikes were over budget; 38 red notes had a
strike of the right pitch, in the right place, and only too late to be delivered. Concentrated in bars 54-67, which is
the stretch he asked about. That is an upper bound for this one mechanism and not a promise about the run: the other
183 misses have other causes.

**And no, it was not built in three days ago.** Nothing since `d77b029` (2026-09-19) touches `elapsed_ms()`,
`audio_offset_ms`, the frame clock or the late window -- the only change to any of it made the budget 17 ms MORE
generous. The drift is between a crystal and `perf_counter`, which no commit in this tree can reach. What the anchor
commit DID do is measure the block-to-block jumps (280/455/16 ms) and attribute all of them to the anchor; the walk
WITHIN a block was in that same data and nobody subtracted it. **The second half of a fault looks like a new fault**,
and the log said `build unknown build`, so which version produced it cannot be established either.

## What NOT To Do

- Don't add ML-based pitch detection. aubio YIN is sufficient and runs everywhere.
- Don't create a web UI or Electron wrapper. This is a desktop app.
- Don't add accounts, cloud sync, or anything that sends the player's playing anywhere. Offline-first, local files only.
  **Songsterr is the one exception and it is one-way**: the download screen fetches a tab, its bar map and the audio of the recording
  that map was made against, and everything it fetches is written to disk beside the tab so the song works on a machine with no
  network from then on. Nothing is uploaded, nothing is accounted for, nothing is remembered anywhere but the player's own folder.
  Asked for by name: *"Wenn wir beim Downloader explizit Songsterr nutzen (keine anderen Dienste) ... Ich gebe die Daten nicht weiter
  und nutze sie nur lokal offline."* **Songsterr only** -- no second service, no search across the web.
- Don't over-abstract. Simple classes, no deep inheritance hierarchies. This is a ~3K LOC app, not a framework.
- Don't add **blind** polyphonic transcription ("here is audio, name every note") and don't add ML. aubio YIN is monophonic and stays the note detector.
  Verifying the notes the tab already predicts is a different problem and is allowed: `audio/chord_verify.py` checks each expected string against
  the partials no other chord tone can produce. That is signal processing against a known answer, costs ~1 ms per strike, and is what makes
  per-string right/wrong feedback possible.
