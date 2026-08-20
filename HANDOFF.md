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

- **Two Windows machines now.** `C:\Users\Admin\.vscode\MySician\mysician\mysician`
  on the first, `C:\Users\lwnthp0\Mysician\MySician` on the second. A fresh
  clone has no `.venv` (it holds binaries built for one machine's Python and
  cannot be committed), so every import fails one at a time and reads like a
  broken app. `setup.ps1` builds the whole environment in one command and
  verifies the imports; point them at it rather than at a list of pip
  commands. It also warns when the checkout is on the wrong branch.
- **A local install needs the MSVC C++ toolchain, and there is no way round
  it.** aubio's newest release is 0.4.9 from 2019 and PyPI carries the source
  tarball *only* — no wheel for any Python on any platform — so it is compiled
  on every install. The VS Code "C/C++" extension is not a compiler and the
  player reached for it first; what is needed is the **"Desktop development
  with C++"** workload in the Visual Studio Installer, or the standalone Build
  Tools. `setup.ps1` now checks for it with `vswhere.exe` BEFORE installing
  anything, and distinguishes "no Visual Studio at all" from "Visual Studio
  without the C++ workload", because the fix differs.
- **The escape hatch is the built exe.** GitHub Actions builds `MySician.exe`
  on every push to any `claude/**` branch, and its runner has the compiler. On
  a machine that cannot build aubio, that is the way to run the current code.
- Runs from source: `.venv` with **Python 3.12**. The system Python is newer
  on both machines (3.14 on the first, 3.13 on the second) and must NOT be
  used. The reason, since it comes up on every new machine: aubio has shipped
  no wheel since 0.4.9 in 2019, so it compiles from source everywhere — and it
  needs the numpy 1.x C API, whose last release (1.26.4) has no build above
  3.12. `setup.ps1` finds a 3.12 through the `py` launcher and says this in
  plain terms when it cannot. Activate first:
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

**Known-imperfect:** the timing report calls rushing "scatter" (topic 3a).
Chord verification abstains on chords closer than ~255 ms
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

-11. **A pitchless power chord now counts.** `MIN_UNPITCHED_CHORD_STRINGS`
   goes from 3 to 2. The old line was drawn on a rate (a pitchless strike is
   rare below three strings) when the question was whether a wrong finger
   still shows — and it does. Measured through the real path with the power
   chords added to `check_chord_credit.py`: correct E5 8/10 → 10/10, G5
   8/10 → 10/10, **palm-muted E5 16/20 → 20/20**, fast E5 76/78 → 78/78, and
   every deliberate one-fret error still caught, the palm-muted wrong take
   convicting 10 strings instead of 6. This is the metal case: one in five
   power-chord strikes arrives with no pitch at all.
-10. **The run log worked first time.** 91.9 % (57/62) against 34.6 %, with
   every previous suspect cleared by reading the file instead of trying
   things — see topic 3, and `CLAUDE.md`, "What A Run Log Answered, First
   Time Out". Two findings came out of it that are not scoring bugs: the
   player rushes 4 % inside fast passages (topic 3a), and the
   `20260818_194323` reference set is silent and must not be calibrated
   against (topic 3b).
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
3. ~~**Find where the app loses notes the detector heard.**~~ **LARGELY
   CLOSED — 2026-08-19, second session.** The next run scored **91.9 %
   (57/62)** with the run log attached, against 34.6 % before. What is left
   of this topic is small and named:

   - **The 34.6 % run has no confirmed cause, and one untested suspect.**
     Two things changed between the runs: the fixes below, and the player
     regenerating the timing test (their copy was the older 78-note build).
     Neither explains it on its own — the offline replay of that take reads
     97.4 % against the OLD file too. The one difference nobody weighed is
     that `record_reference.py` was capturing from the same interface at the
     same time. **Worth one experiment**: play the test once with the
     recorder running. If the score collapses again, every in-app score taken
     during a play-along recording is void, which matters for every future
     measurement. If it does not, the fixes did it and this is finished.
   - **Two one-semitone misreads** remain (a 47 read as 48, a 45 read as 44),
     which is the detector's honest resolution, not a bug. Nothing to do
     unless it grows.
   - **The pitchless power chord is fixed** — see topic 2 below and
     `CLAUDE.md`, "Chords That Produce No Pitch". That was the other two
     notes.
   - The old candidate list is dead: gate -65 dB, 0 dropped buffers, 44100 Hz
     resolved, no fret filter, no muted string, 0 strings taken back, clock
     anchored at 5.4 ms. All read off the log rather than tried.

   The evidence that closed it, kept for reference:

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

   Still parked, and still not worth touching without more data: the
   confidence threshold (0.65 vs 0.80 measured as marginal — +2 power-chord
   and +4 chord strikes, but one extra WRONG pitch on single notes, over 150
   strikes) and doubled strikes on isolated chords (one shows in the 91.9 %
   log at 12.1 s, harmless to the score).

3a. **Rushing, which the timing report calls scatter.** New, and the player's
   own samples say it plainly: inside every fast passage the error ramps from
   late to early and resets at the next phrase — 0.8 % fast on quarters,
   **4.2 % on eighths**, 9.2 % on the eighth-note chords. A ramp that resets
   cannot be a clock and is not scatter; it has a direction, and `K` cannot
   touch it. The report says `mixed` and sends them to practise slower, which
   is not wrong but is far less useful than "you speed up inside runs". A
   per-passage slope, tested against its own standard error the way the median
   and the per-string gap already are, would name it. See `CLAUDE.md`,
   "Rushing Is Not Scatter". **The player has not asked for this** — it sits
   behind their four priorities.

3b. **`reference_recordings/20260818_194323` is unusable and should not be
   calibrated against.** Every take peaks at -58 dBFS with an RMS of -70;
   the detector finds zero strikes in the whole set. Use `20260814_160019`,
   which is what every threshold in `chord_verify.py` was fitted on.
4. **Ringing strings.** Real and measured (59 % everything ringing vs 100 %
   damped over the whole timing test), confirmed by the player at the
   instrument. Much smaller than the clock bug was, so re-measure before
   designing anything. `CLAUDE.md`, "Ringing Strings Defeat Detection".
5. ~~**MP3 backing track.**~~ **BUILT — 2026-08-19.** `U` switches the
   recording on and off, `Shift+U` picks the file with the Windows dialog,
   `Shift+N`/`Shift+M` shift it against the notes, and both the path and the
   offset are stored per song. It runs ALONGSIDE the MIDI backing, both
   switched separately, which is what the player asked for and why `B` does
   not cycle. See `CLAUDE.md`, "Two Backing Tracks, Switched Separately".

   **The one limit worth knowing, and the follow-up it implies:** a recording
   cannot be slowed down. `pygame.mixer.music` plays at the recorded rate and
   resampling to 80 % drops the pitch four semitones with it, so below full
   speed the recording is held silent and the HUD says why. The player
   practises at 80 % a great deal, so this WILL come up. Lifting it needs a
   phase vocoder over the decoded samples (numpy is already a dependency),
   cached per file and per speed — a real piece of DSP, not a setting, and
   worth doing only once the player says the restriction bites.

   **First real-world bug, found and fixed:** pausing did not silence it.
   Every route to the recording reaches `Mp3Player.seek`, and seeking STARTS
   playback — so `Shift+N`/`Shift+M` on a paused song set the recording
   playing under a frozen picture, which is exactly the state the offset has
   to be judged in. `_mp3_plays()` now includes `self._playing`; paused, the
   keys only store the number and the HUD still shows it move.

   Still untested against a real file: `pygame.mixer.music.play(start=...)`
   seeking into the middle of an MP3 on Windows, and whether the tkinter
   dialog comes to the front. Both fail loudly rather than silently.

   The original assessment, for reference: The player's feature request, assessed as a
   moderate, non-research job. Wanted: a file picker, per-song path, on/off,
   and a per-song sync offset. Most of the machinery exists — `Config` already
   stores a per-song backing offset and `N`/`M` shift it live; `pygame.mixer.music`
   plays MP3 with position control. The only real question is the picker:
   PyGame has no file dialog, so either `tkinter.filedialog` (ten lines, gives
   the native Windows dialog) or a PyGame browser like `download_menu.py`.
   **Both questions are now settled by the player (2026-08-19):**

   - **MP3 and MIDI must be switchable INDEPENDENTLY, not cycled.** Not a
     preference — a workflow: he needs both sounding at once to line the MP3
     up against the click, and switches one off once it is synced. A `B` that
     cycles off → MIDI → MP3 makes exactly the state he needs unreachable. So:
     one toggle each, either or both, and the per-song sync offset applies to
     the MP3.
   - **The file picker is the native Windows dialog** (`tkinter.filedialog`).

   MP3 encoder delay varies per file, which is why the manual per-song sync is
   a requirement rather than a convenience — and why the two-at-once state
   above is what makes it usable at all.
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
