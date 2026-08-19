# MySician — Session Handoff Notes

Read this together with `CLAUDE.md` before continuing work. Last updated: 2026-08-19 (second session).

`CLAUDE.md` says how the app is built and why. This file says where it stands,
what the user's setup is, and what is still open.

## What this is

Private Yousician-style guitar practice app for one user (Philipp). Forked from
[PickHero](https://github.com/Artemarius/PickHero) (MIT, see LICENSE) into
`Trubar4/MySician`. Working branch: **the one named in `UPLOAD_BRANCH`** at the
repo root — currently `claude/mysician-timing-remeasure-uaj3t8`. That file is
the single place the branch is written down; `tools/record_reference.py` reads
it so its upload hint can never send a recording to a branch nobody reads,
which has now happened twice. When the work moves, update that file first.

## User's setup (important for debugging)

- Windows PC, PowerShell; repo at `C:\Users\Admin\.vscode\MySician\mysician\mysician`
- Runs from source: `.venv` with **Python 3.12** (system Python is 3.14 — must
  NOT be used, aubio/pygame don't build there). Activate first:
  `.venv\Scripts\Activate.ps1`, then `python -m pickhero`
  ("No module named pygame" = venv not activated)
- Guitar → **Focusrite Scarlett** USB interface (native 48 kHz) → PC.
  No Jam Origin / MIDI input — pitch detection straight from audio (aubio).
- Plays metal, and practises on Yousician at Pro level — that is the bar the
  display is measured against.
- User is non-technical ("vibecoding"): give **copy-paste commands**, explain
  simply, and **answer in German**.
- Songs folder: `songs/` next to the repo, or `python -m pickhero --songs <path>`.
- Plays rock and metal, and also pop and country.

## Rulings from the user (do not re-litigate these)

- **Where a note is fretted never matters.** The same pitch on a lower string in
  a high position and on a higher string in a low position must score
  identically. Already true: matching compares MIDI pitch and never the string.
- **An octave error stays green.** The matcher's octave equivalence
  (`dist % 12`) exists to absorb the detector's octave slips on wound strings,
  and the user has decided it may keep crediting a genuinely wrong octave too.
- **A dead note counts as played on the strike alone** — "something came, that's
  good". There is no pitch in one to check.
- **A bend that does not reach its target scores yellow, not red**, and the
  target has to be held for as long as it is written.
- **On six-string chords, err toward tolerance** rather than convicting strings.

## State

506 tests (`python -m pytest tests -q`). Everything below is implemented,
calibrated where it needed calibrating, and pushed.

**Working:** GP3–GP5 loading, track picker, scrolling display with per-song
scroll speed, MIDI backing with per-song offset, pitch detection incl. power
chords, per-string chord verification, wait mode, latency auto-sync, bends,
slides, hammer-ons and pull-offs (drawn and scored), palm mutes and dead notes
(drawn and scored), progress tracking.

**Known-imperfect:** chord verification abstains on chords closer than ~255 ms
(eighths past about 118 BPM). GP7 files load with muting but no bends or
slides. Timing is measured and answered: plain input latency, which `K`
removes.

## The three subsystems worth knowing before touching anything

### 1. Detection (`audio/`)

Monophonic aubio YIN by design — no ML, no blind polyphony (`CLAUDE.md`,
"What NOT To Do"). Two things sit on top of it:

- `OnsetPitchCollector` waits for the pitch to settle after the attack and
  emits ONE strike note per pick, stamped from the **sample clock**, not the
  wall clock. Strikes therefore arrive ~130 ms after the physical strike;
  anything consuming detections must tolerate that (`late_window_ms`).
- `chord_verify.py` answers "was this string on the right fret", which the
  monophonic detector cannot. Score-informed, calibrated on real recordings.
  Its rules and thresholds are documented in `CLAUDE.md` — **re-fit with
  `tools/sweep_chord_window.py` and `tools/analyze_reference.py` rather than
  tuning by feel**; the reference takes in `reference_recordings/20260814_160019`
  are the ground truth, and every threshold in that file was fitted on them.

### 2. Matching (`matcher.py`)

Scores strikes against the timeline. Three tolerances that are deliberate and
must not be tightened without a way to measure what they cover:

- **Bends and slides** accept the whole pitch region the technique covers
  (`_build_pitch_ranges`).
- **Legato targets** (hammer-on, pull-off, both slide kinds) are never picked,
  so they inherit their source note's verdict (`_legato_credit`).
- **Chord verdicts** only ever downgrade, and only on positive evidence
  (`process_strike_windows`).

### 3. Display (`ui/scrolling.py`)

One scroll speed and one note-head size per song, chosen so the song's
tightest passage still fits full-size notes — notes must never visibly resize
while scrolling, which is the thing that made the display feel wrong before.
Technique marks live inside the note with a badge above it; the two colour
palettes (string vs feedback) are kept apart on purpose — see `CLAUDE.md`,
"Colour".

## What this session changed (newest first)

-9. **Detection is not the problem, and now there is proof.** The player's
   fresh take (`reference_recordings/20260819_195251`) scored 34.6 % in the
   app. The same WAV, through the same detector, the same `AudioCapture`
   callback path, the same song file, the same 200 ms window and the same
   chord verifier, scores **97.4 %** offline; the detector alone hears
   **52 of 54** written onsets with the right pitch. So the loss is somewhere
   between the live audio thread and the score, not in detection and not in
   matching. It has not been located yet — see topic 3 — and the run log
   below is the instrument built to locate it.
-8. **The play-along analyser was reading slowed-down takes at full speed.**
   The player practises at 80 %; the tool assumed 100 %, so the first bar
   lined up and everything after it walked away. It reported 22 % on a take
   it now reports 96 % on, and the whole of the last session's "detection is
   broken" reading came through that. The practice speed is now recorded into
   the manifest by the recorder (read from the app's own settings) and
   measured when it is not, over the eleven speeds the app can be in.
   `tests/test_play_along_alignment.py` keeps it honest.
-7. **A run log (`D`), and a completion screen that says which half failed.**
   Every scored run writes `~/.pickhero/run_<song>_<stamp>.txt`: one line per
   strike (raw stamp, adjusted stamp, playback position, pitch, confidence,
   what became of it), every written note's final verdict, and the header that
   explains a run — resolved sample rate, dropped buffers, gate, thresholds,
   tempo, offsets, filters. The completion screen names strikes heard next to
   notes credited, because "few strikes" and "many strikes, low score" are
   different faults in different places and one percentage cannot tell them
   apart. See `CLAUDE.md`, "When The Score Is Low, Say Which Half Is Low".
-6a. **Two clock bugs found by reading, not yet by measurement.** Changing the
   practice speed mid-song used to displace every later strike by
   `elapsed x change`, growing for the rest of the song and beyond what `K`
   can take back; the audio clock is now re-anchored on every speed change.
   And `_start_audio()` on an already-running capture left the old stream
   open, writing into the same ring — it stops first now. Whether either was
   in play in the 34.6 % run is exactly what the run log will say.

-6. **Reading time on dense songs, and the fret filter forgetting itself.**
   At full-size heads a dense tab gave 1.5 s of look-ahead at 683 px/s
   (canon.gp5) — faster than a fret number can be read. Heads now shrink per
   song, down to `MIN_HEAD_PX`, to buy look-ahead up to `READABLE_WINDOW_MS`;
   canon goes to 2.5 s at 409 px/s and easy songs are untouched. The
   docstring had promised this behaviour for a while without the code doing
   it. Rendering was never the problem: 74-106 FPS measured. Separately,
   `max_fret` no longer survives a restart — a filter left on silently deletes
   notes and shows a plausible accuracy for a fraction of the song.
-5. **A dropped audio buffer stopped the clock.** The single biggest cause of
   "notes are not recognised". `_audio_callback` returned on any status flag,
   discarding the buffer AND leaving the ring's sample counter frozen — so
   every later strike was stamped early, cumulatively. On the player's own
   play-along take: 42/46 strikes heard with nothing dropped, **17/46** with
   2 % dropped the old way, 40/46 with the counter still advancing. The audio
   is now always processed and overflows are counted and shown in the HUD.
   **Detection was never the bottleneck it looked like** — the detector reads
   that take at 91 % while the app scored it at 24 %.
-4. **Only honest notes are timed, and K says what it left.** Technique notes
   no longer contribute timing samples (`_times_its_own_strike`): a bend leaves
   its pitch on purpose and a legato target is never picked. This was the root
   cause of a bad offset that sat in the config for days — replayed against the
   real export, 18 of its 24 samples drop and K would have stayed silent.
   K itself now applies exactly what the report calls latency, the HUD line
   reads the same verdict so it can never offer a key that does nothing, and it
   names a residual ("49 ms still left, K again") instead of repeating the
   first-time prompt.
-3. **Chord verdicts at speed, and the tuning on screen.** `MIN_WINDOW_MS`
   turned out to be stale rather than physical — fitted at 280 ms when the
   analysis floor was a fixed 150 Hz, and never lowered after `MIN_HZ_SECONDS`
   made short windows honest. Re-swept: no false alarm from 190 ms up, so the
   floor is 200 ms and chords are judged down to ~255 ms apart. The sweep now
   lifts the floor while it runs, because gating it on the constant it was
   meant to question is what hid this. Separately, `metadata.tuning` was loaded
   and never shown; the HUD now names it (`tuning_name`, `tuning_notes`) and
   flags anything but standard with "← retune".
-2. **Chord credit for pitchless strums.** See topic 2 below.
-1. **Palm mutes and dead notes.** Both were in the GP files and neither reached
   the app. A dead note was loaded as an ordinary note on the fret the tab uses
   to say where the hand DAMPS, so it could not be hit at all and timed out as
   a miss — and a muted metal riff is full of them. The collector now reports a
   pitchless strike as `unpitched` instead of dropping it, and a written dead
   note is credited by any strike; it never competes for a pitched one, and is
   kept out of the timing report and chord verification. Palm mutes score
   exactly as before (the pitch is unchanged) and are drawn as the short stubs
   they sound like, with "PM" badged once per run. Both flags also come out of
   the GP7 XML path, which still carries no bends. `make_technique_test.py` got
   three muting sections.
0. **Timing report** (`Y`). The distribution of your strikes against the beat,
   with the verdict named and the raw samples exportable (`Shift+Y`). Two
   fixes came out of building it: the search radius now narrows as the offset
   becomes known, which took the measured share of strikes from 39 % to 98 %
   on the timing test, and neither the median nor a per-string difference is
   called an effect until it clears its own standard error.
1. **Techniques drawn Yousician-style** (`00f3a13`). Bend curve inside the note
   with a dark shadow (white on the amber string was invisible), badge above
   the leading edge naming the technique: `½ 1 1½` for bends, `SL`, `H`, `P`.
   Hammer-ons and pull-offs added. Legato notes inherit their source's verdict.
   String palette pulled off the feedback hues (A is teal now, not green).
2. **Bends and slides** (`ff3d2bb`). `NoteEvent` carries `bend`,
   `slide_to_next`, `slide_in`, `slide_out`, `hammer_to_next`. Matcher accepts
   the region a technique covers. Footer rewritten to carry **every** shortcut,
   two lines, shrink-to-fit; help overlay split into two columns.
3. **Chord window ends at the next strike** (`7bea460`). Fast chord changes were
   convicting correctly played strings, because the 341 ms window contained the
   NEXT chord. Now trimmed at the following onset, with the analysis floor
   rising as it shortens and the intruder tier withheld below full length;
   under 280 ms usable audio the chord gets no verdict at all.
4. Before that: MIDI robustness, per-song backing offset, track picker, chord
   test file, config protection from the test suite, scroll pacing, note
   sizing, latency auto-sync bounds. See `git log`.

## Diagnostic tools (all in `tools/`)

| Tool | What it answers |
|---|---|
| `make_timing_test.py` | Is my timing off, or is the tab sloppy? Click only where a note is. |
| `make_chord_test.py` | Are chords recognised? C/Am/G/D from isolated to strummed eighths. |
| `make_technique_test.py` | Are bends/slides/H/P and muting drawn right? States what each bar contains. |
| `record_reference.py` | Records a labelled take set, including deliberate wrong notes. |
| `analyze_reference.py` | Per-string verdict table over a take set; counts false alarms. |
| `sweep_chord_window.py` | How short may the chord window get before it lies? |
| `check_chord_credit.py` | Does crediting a pitchless strum still catch a wrong finger? Exits non-zero if not. |
| `record_reference.py --play-along` | Records the player playing a song through — the case the 29 isolated exercises do not contain. |
| `analyze_play_along.py` | Per written onset: heard right, heard as the wrong note, or not heard at all. Finds both the start offset AND the practice speed. |
| `D` in the app | The run log: what every strike did, and every note's verdict. The one place a live-only fault shows. |
| `simulate_timing.py` | Does the timing report name a fault that was injected on purpose? |

Generate the test songs with `python tools/make_*_test.py`; they land in `songs/`.

## Open topics

Ordered by what would help the user most. `NEXT_SESSION.md` holds the same
list as a paste-ready prompt, with what has already been ruled out on each.

1. ~~**Timing spread.**~~ **MEASURED AND CLOSED — 2026-08-17.** Two runs of the
   shortened timing test came back `latency`, +120 ms and +128 ms, with a
   scatter of only ±20 and ±16 ms and a between-string spread of 9 ms (within
   chance). One constant offset, which is what `K` exists for: it removes 60 %
   and 67 % of the total error respectively. **Nothing to build here.** Per-string
   offsets are NOT called for and must not be built on this evidence.
   The user plays tightly; the error is the input path, not the playing.

   Two things that made the earlier attempts unreadable, both now fixed:
   the first export came from the technique test (17 of 24 samples sat on
   bend/slide/legato notes, which cannot be measured), and the timing test
   itself repeated one pitch for eighty seconds, so 127 of its samples were
   refused as ambiguous. Keep diagnostic files pitch-varied and inside the
   fret filter's default range.
2. ~~**Chords come up RED.**~~ **DIAGNOSED AND FIXED — 2026-08-17.** A strummed
   chord gives monophonic YIN no period to lock onto, so a correctly played
   strum carried no pitch and was scored red: 38-55 % of strikes on chords of
   four strings and up, against 16 % on one or two, and an open A minor
   produced none at all in five strikes. Never a speed problem — a fast
   single-note riff detects 47 of 49 and fast power chords 38 of 39, so the
   verifier's 335 ms abstention was never the cause either.
   An unpitched strike now credits a written chord of three strings or more
   (`MIN_UNPITCHED_CHORD_STRINGS`), and the strum still goes to the verifier,
   which catches a wrong finger afterwards. Correct chords went from 54 % to
   100 % credited with every deliberate error still caught — see `CLAUDE.md`,
   "Chords That Produce No Pitch", and `tools/check_chord_credit.py`.

   The follow-on — chords too fast to get a per-string verdict — went with it:
   the 335 ms floor turned out to be **stale, not physical**. Re-swept, the
   reference takes show no false alarm from 190 ms up, so `MIN_WINDOW_MS` is
   200 ms and the required spacing is ~255 ms, i.e. eighths to about 118 BPM
   instead of 90. Faster than that still gets no verdict, which is the honest
   answer at that speed.
3. **FIRST: find where the app loses notes the detector heard.** This is now
   a narrow question with a measurement behind it, not an open one.

   The player's 2026-08-19 take, scored by the app at **34.6 % (27/78)**,
   reads as follows when the recording is put through the same code offline:

   | read through | result |
   |---|---|
   | the detector alone (`analyze_play_along.py`, 80 %) | 52/54 onsets, right pitch |
   | detector + matcher (`NoteMatcher`, 200 ms window) | 98.7 % |
   | + the real `AudioCapture` callback and the chord verifier | **97.4 %** |
   | the app, live, same take | **34.6 %** |

   So: not detection, not matching, not the chord verifier, not the song file
   (the player's `timing_test_100bpm.gp5` is the older 78-note version, which
   is what the offline runs used too), and not dropped buffers (the HUD line
   was absent, i.e. zero). Whatever it is, it is live-only, and the arithmetic
   says the app saw far FEWER strikes than the 60 the same audio yields
   offline: its timing samples sit at the right places with a small median, so
   the strikes it did see were stamped correctly.

   **Step one: one run with the run log.** `D`, or just play a song to the
   end — the file lands in `~/.pickhero/run_<song>_<stamp>.txt`. It answers,
   in order: how many strikes the audio thread produced at all; whether their
   stamps agree with the playback clock; whether the gate, the confidence
   threshold, a fret filter or a muted string was quietly eating them; and
   whether the string check took anything back. Do not build before reading
   one.

   Candidates the log will confirm or kill: the noise gate (`X`/`C` — the same
   take drops to 80 % at a -40 dB gate and to 52 % at -30 dB, against 96 % at
   the -60 dB default), a mid-song speed change under the old, un-anchored
   clock, and a second audio stream left open by `_start_audio`. The two
   longer-standing candidates stay parked until then: the confidence threshold
   (0.65 vs 0.80 measured as marginal — +2 power-chord and +4 chord strikes,
   but one extra WRONG pitch on single notes, over 150 strikes) and doubled
   strikes on isolated chords.

   Housekeeping for the same run: the player's local timing test is the older
   78-note build. `python tools/make_timing_test.py` regenerates the current
   one, whose eighth-note chord section has room to breathe.
4. **Ringing strings.** Real and measured (59 % everything ringing vs 100 %
   damped over the whole timing test), confirmed by the player at the
   instrument. Much smaller than the clock bug was, so re-measure before
   designing anything. `CLAUDE.md`, "Ringing Strings Defeat Detection".
5. **MP3 backing track.** The player's feature request, assessed as a
   moderate, non-research job. Wanted: a file picker, per-song path, on/off,
   and a per-song sync offset. Most of the machinery exists — `Config` already
   stores a per-song backing offset and `N`/`M` shift it live; `pygame.mixer.music`
   plays MP3 with position control. The only real question is the picker:
   PyGame has no file dialog, so either `tkinter.filedialog` (ten lines, gives
   the native Windows dialog) or a PyGame browser like `download_menu.py`.
   Recommend tkinter. Two things to settle with the player first: MP3 and MIDI
   backing as ALTERNATIVES (`B` cycling off → MIDI → MP3) rather than layered,
   and the fact that MP3 encoder delay varies per file, which is why the manual
   per-song sync is a requirement rather than a convenience.
6. **Settings screen.** 41 keys are handled and the footer needs two lines.
   Proposed split: shortcuts stay for everything used WHILE playing (space, K,
   W, L, tempo — hands are on the guitar), a settings screen for everything set
   once (device, noise gate, hit window, fret filter, muted strings, chord mode,
   theme, backing offset). The menu infrastructure already exists
   (`menu.py`, `device_menu.py`, `download_menu.py`, `calibration_menu.py`).
   A screen showing current state would have caught the fret-filter incident.
7. **Bend evaluation.** The visual exists; scoring is deliberately lenient
   because nothing keeps the pitch contour — though the detector DOES produce
   one, at one frame per ~11.6 ms, which `OnsetPitchCollector` discards. The
   user's decisions: reaching the target too shallowly should score yellow (not
   red), and the target has to be held for the note's written length, roughly a
   quarter-tone accurate.
8. **GP7 techniques** (deprioritised by the player). The hand-written GP7 XML
   path carries muting but no bends or slides.
9. **Palm-mute leniency** (deprioritised by the player, and unmeasured).
   Whether a chug that returns no pitch at all should credit its palm-muted
   note. Needs reference recordings, not a guess — see `CLAUDE.md`, "Muting".

## Conventions

- Commit style: imperative subject + explanatory body saying *why*, not what.
  No model names anywhere in commits, PRs or code.
- Always push to the branch named in `UPLOAD_BRANCH`, and update that file
  when the branch changes.
- **The player's local checkout drifted onto the old branch once.** Their pulls
  still brought this work in (a pull merges into whatever is checked out), but
  their pushes landed elsewhere and one push failed outright. If they report a
  push error, check which branch they are on before looking anywhere else.
- Run the full suite before pushing; add tests for every behaviour change.
- **Verify signal-processing changes against the reference recordings**, not
  against intuition. Synthetic tones are a trap unless they carry the property
  under test — the test fixture in `tests/test_chord_verify.py` documents two
  that matter (harmonic roll-off, uneven partials from the pluck point).
- User config: `~/.pickhero/settings.json`. New fields need defaults for old
  files; changed defaults need a migration in `Config.load`. `tests/conftest.py`
  redirects the config path — never remove it, the suite used to overwrite the
  user's real settings.
- Dev box needs `libportaudio2` and aubio built with `numpy<2`,
  `setuptools<74`, `--no-build-isolation`. Where aubio cannot be built, the
  seven `tests/test_detector.py` tests are the only ones that fail.
