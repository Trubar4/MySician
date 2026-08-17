# MySician — Session Handoff Notes

Read this together with `CLAUDE.md` before continuing work. Last updated: 2026-08-17
(muting session).

`CLAUDE.md` says how the app is built and why. This file says where it stands,
what the user's setup is, and what is still open.

## What this is

Private Yousician-style guitar practice app for one user (Philipp). Forked from
[PickHero](https://github.com/Artemarius/PickHero) (MIT, see LICENSE) into
`Trubar4/MySician`. Working branch: **`claude/mysician-timing-measurement-au2v0m`** —
all work is pushed there. The previous branch
(`claude/handoff-claude-md-review-va3ph4`) is merged into `main`; do not add to it.

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

456 tests (`python -m pytest tests -q`). Everything below is implemented,
calibrated where it needed calibrating, and pushed.

**Working:** GP3–GP5 loading, track picker, scrolling display with per-song
scroll speed, MIDI backing with per-song offset, pitch detection incl. power
chords, per-string chord verification, wait mode, latency auto-sync, bends,
slides, hammer-ons and pull-offs (drawn and scored), palm mutes and dead notes
(drawn and scored), progress tracking.

**Known-imperfect:** chord verification abstains on chords closer than
~335 ms, and the user reports fast chords coming up red — that is the next real
bug, not a missing feature. GP7 files load with muting but no bends or slides.
The timing spread the user reported is now measurable rather than guessed —
see below.

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

   Still open underneath it: **chords closer than ~335 ms get no per-string
   verdict at all**, so at eighth-note speed the credit above stands
   unchecked. That is the original topic 3 and it is now the whole of it.
3. **Bend evaluation.** The visual exists; scoring is deliberately lenient
   because nothing keeps the pitch contour — though the detector DOES produce
   one, at one frame per ~11.6 ms, which `OnsetPitchCollector` discards. The
   user's decisions: reaching the target too shallowly should score yellow (not
   red), and the target has to be held for the note's written length, roughly a
   quarter-tone accurate.
4. **GP7 techniques.** The hand-written GP7 XML path carries muting but no bends
   or slides.
5. **Palm-mute leniency (unmeasured).** Whether a chug that returns no pitch at
   all should credit its palm-muted note. Needs reference recordings, not a
   guess — see `CLAUDE.md`, "Muting".

## Conventions

- Commit style: imperative subject + explanatory body saying *why*, not what.
  No model names anywhere in commits, PRs or code.
- Always push to `claude/mysician-timing-measurement-au2v0m`.
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
