# MySician — Session Handoff Notes

Read this together with `CLAUDE.md` before continuing work. Last updated: 2026-08-16.

`CLAUDE.md` says how the app is built and why. This file says where it stands,
what the user's setup is, and what is still open.

## What this is

Private Yousician-style guitar practice app for one user (Philipp). Forked from
[PickHero](https://github.com/Artemarius/PickHero) (MIT, see LICENSE) into
`Trubar4/MySician`. Working branch: **`claude/handoff-claude-md-review-va3ph4`** —
all work is pushed there.

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

## State

406 tests (`python -m pytest tests -q`). Everything below is implemented,
calibrated where it needed calibrating, and pushed.

**Working:** GP3–GP5 loading, track picker, scrolling display with per-song
scroll speed, MIDI backing with per-song offset, pitch detection incl. power
chords, per-string chord verification, wait mode, latency auto-sync, bends,
slides, hammer-ons and pull-offs (drawn and scored), progress tracking.

**Known-imperfect:** timing spread of roughly ±80–104 ms remains (see Open
Topics 1). Chord verification abstains on chords closer than ~335 ms. GP7
files load but carry no techniques.

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
| `make_technique_test.py` | Are bends/slides/H/P drawn right? States what each bar contains. |
| `record_reference.py` | Records a labelled take set, including deliberate wrong notes. |
| `analyze_reference.py` | Per-string verdict table over a take set; counts false alarms. |
| `sweep_chord_window.py` | How short may the chord window get before it lies? |

Generate the test songs with `python tools/make_*_test.py`; they land in `songs/`.

## Open topics

Ordered by what would help the user most. Details, including what has already
been ruled out, are in the handoff prompt at the bottom of this file.

1. **Timing spread (±80–104 ms).** The oldest open complaint. Needs a diagnostic
   that separates latency (shifts everything one way) from human scatter
   (spreads both ways) before anything is "fixed".
2. **Bend evaluation.** The visual exists; scoring is deliberately lenient
   because the detector produces no pitch contour. The user's target is
   "roughly a quarter-tone accurate on the target pitch".
3. **Chords at eighth-note speed.** Currently abstains below ~335 ms spacing.
   Would need analysis before the next strike rather than after it — research,
   not refactoring.
4. **Palm mutes and dead notes.** In the GP files, currently drawn as ordinary
   notes. Smallest of the four.
5. **GP7 techniques.** The hand-written GP7 XML path carries no bends or slides.

## Conventions

- Commit style: imperative subject + explanatory body saying *why*, not what.
  No model names anywhere in commits, PRs or code.
- Always push to `claude/handoff-claude-md-review-va3ph4`.
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
