# MySician — Session Handoff Notes

Read this together with `CLAUDE.md` before continuing work. Last updated: 2026-07-12.

## What this is

Private Yousician-style guitar practice app for one user (Philipp). Forked from
[PickHero](https://github.com/Artemarius/PickHero) (MIT, see LICENSE) into
`Trubar4/MySician`. Working branch: `claude/yousician-alternative-vshoiv`
(also the repo's default branch — all work happens here).

## User's setup (important for debugging)

- Windows PC, PowerShell; repo at `C:\Users\Admin\.vscode\MySician\mysician\mysician`
- Runs from source: `.venv` with **Python 3.12** (system Python is 3.14 — must
  NOT be used, aubio/pygame don't build there). Activate first:
  `.venv\Scripts\Activate.ps1`, then `python -m pickhero`
  ("No module named pygame" = venv not activated)
- Guitar → **Focusrite Scarlett** USB interface (native 48 kHz) → PC.
  No Jam Origin / MIDI input — pitch detection straight from audio (aubio).
- GitHub Actions builds `MySician.exe` on every push (user no longer needs it,
  they run from source; workflow: `.github/workflows/release.yml`)
- Plays metal: power chords and riffs matter more than melody lines
- User is non-technical ("vibecoding") — give copy-paste commands, explain simply,
  **answer in German**

## State as of this handoff

Working: GP5 loading, scrolling tabs, MIDI backing, pitch detection incl.
power chords, wait mode (fluid), latency auto-sync. **~50 % accuracy without
wait mode** and climbing after each fix (was 10 %). 239 tests green
(`python -m pytest tests -q`).

## What was changed vs upstream PickHero (chronological)

1. **Rebrand + exe rename** (PickHero.exe → MySician.exe in spec/build.bat/workflow);
   workflow also builds on branch pushes and uploads an artifact.
2. **Device sample-rate probing** (`audio/input.py`): Focusrite rejects 44100 in
   Windows shared mode → `_resolve_input_settings()` probes configured rate,
   device default, then common rates (mono, then stereo); detectors rebuilt at
   the resolved rate; stereo input downmixed (guitar can be on input 1 or 2).
   Calibration wizard shows an error screen instead of crashing (state "error").
3. **Pitch detection fixed** (`audio/detector.py`): upstream passed the 0.8
   confidence threshold as aubio YIN *tolerance* (dip threshold, belongs at
   0.15) → harmonics instead of fundamentals, low E almost never right.
   Now: `yinfast`, buf 4096 (was 2048; too short for 82 Hz), separate
   `yin_tolerance=0.15` config field, onset detector kept at 2048 window.
   Config migrations in `config.py::load` (buf_size 2048→4096).
4. **OnsetPitchCollector** (`audio/input.py`): matcher only scores `is_onset`
   detections, but the onset frame is the attack transient (fails confidence
   filter; large window still contains previous note's decay). Collector:
   on onset skip 3 frames, collect ≤12, median of last 4 confident samples,
   emit ONE strike note stamped with the strike time. Sustained per-frame
   notes still flow (tuner/console) but with `is_onset=False`.
   Matcher got `late_window_ms` grace so late-arriving strikes still claim
   their note.
5. **Power chords** (the big one): strummed chords make YIN report the
   *subharmonic* (common period of root+fifth, e.g. E5 chord → 41 Hz E1) —
   below guitar range, was discarded → chords never scored. Now the collector
   folds below-range pitches up by octaves (max 2) and flags
   `DetectedNote.subharmonic=True`; a subharmonic proves multiple strings
   sounded, so the matcher credits the WHOLE chord on such a strike.
   Single-string picking still uses the majority model. V toggles lenient
   chord mode. Detector's octave-jump correction leaves below-range
   frequencies alone; calibration-based halving only fires at confidence <0.9.
6. **Latency sync**: `K` auto-syncs (applies inverse of measured median timing
   error to `config.audio_latency_offset_ms`, persisted), `,`/`.` adjust
   ±10 ms manually. Measurement (`matcher._record_timing_sample`) samples
   EVERY strike against the nearest pitch-matching tab note in an asymmetric
   window (early ≤ timing window, late ≤ 500 ms — latency is never negative;
   prevents aliasing on repeated-note riffs), independent of match outcome,
   disabled while wait mode pins timestamps. HUD (top-left) shows offset +
   measured error. Miss-marking grace grows with the compensated latency
   (`scrolling._late_window_ms`). Timing window default 100→150 ms (migrated).

## Architecture facts the next session must know

- Pitch detection is **monophonic** (aubio YIN family) by design — no ML, no
  polyphony (CLAUDE.md "What NOT to do"). Chord scoring works via octave
  equivalence (`dist % 12`) + subharmonic evidence + majority model.
- Wait mode **pins detected timestamps** to the frozen playback position
  (`ui/scrolling.py` ~line 328) — timing is bypassed there; only pitch counts.
  If wait works but normal mode doesn't → timing problem, not pitch.
- Strike notes arrive ~130 ms after the physical strike (collector) — anything
  consuming detections must tolerate that (see `late_window_ms`).
- User config JSON: `~/.pickhero/settings.json`. New fields need defaults for
  old files; changed defaults need a migration in `Config.load` (there is no
  settings UI for most values). Existing migrations: buf_size, timing_window_ms.
- Tests must not need audio hardware. venv on Linux dev box needs
  `libportaudio2` and aubio built with `numpy<2`, `setuptools<74`,
  `--no-build-isolation`.

## Likely next steps (user will report where it stands)

1. **Accuracy tuning toward Yousician feel** — user was at ~50 % and rising.
   Ask for current number + whether misses feel unfair (timing) or wrong-pitch.
2. **Backing track ↔ tab alignment**: if K-measured latency was large (>200 ms),
   part of it is probably the MIDI backing lagging the visual timeline (user
   plays what they hear). Consider shifting MIDI playback earlier by a
   configurable amount instead of folding it all into input latency.
3. Possible: per-song latency offset, count-in feel, feedback polish
   (hit flash timing now ~130 ms after strike), more songs via Songsterr
   downloader, difficulty progression.
4. Upstream PickHero gets updates occasionally — `git remote add upstream
   https://github.com/Artemarius/PickHero` and cherry-pick if useful.

## Conventions

- Commit style: imperative subject + explanatory body, no model names in
  commits/PRs. Always push to `claude/yousician-alternative-vshoiv`.
- Run the full test suite before pushing; add tests for every behavior fix
  (see tests/test_onset_collector.py, tests/test_matcher.py for patterns).
- Verify signal-processing changes with synthetic audio (see the pluck/chord
  simulations in git history commit messages) — sine waves are too kind,
  add harmonics + decay + attack noise.
