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
- **Measured, with the damped takes as the control** (`tools/check_ringing_rescue.py`): fast ringing 8/14 → **10/14**, and every damped take
  gains exactly **nothing**. A rescue firing on a damped take would be a note being invented, which is what that check exists to catch; it
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

## The Band Did Not Play To A Click

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
