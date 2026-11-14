"""Do the TAB and the RECORDING agree, before the app is even involved?

"The picture and the backing drift apart" has three possible causes and they
are fixed in three different places: the tab is wrong, the recording is a
different arrangement, or the app's playback path loses time. This settles the
first two without the app, so the third is only ever suspected once the first
two are ruled out.

    python tools/check_song_sync.py "songs/Song.gp" "songs/Song.mp3"
    python tools/check_song_sync.py song.gp song.mp3 --track 2

What it reports:

  * the tab's timing structure -- bars, time signatures, tempo automations,
    section markers with the time the app will put them at;
  * the recording's length and its tempo, measured by autocorrelating the
    onset envelope (a well-conditioned measurement, unlike beat tracking on a
    full band mix, which gave three different answers on the same file);
  * how far apart the two run, window by window, aligned on CHROMA rather
    than onsets -- pitch survives a dense mix where note attacks do not.

The alignment reports its own confidence and refuses windows it cannot
separate. In a pop song one chorus looks like the next, so a lag search will
always find SOMETHING; a window whose best lag beats the runner-up by too
little is dropped and counted, rather than being averaged into a trend it
would poison. That is the difference between this and the first attempt,
which happily reported jumps of forty seconds at the same confidence as the
good windows.
"""

import argparse
import os
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero.audio import autosync  # noqa: E402
from pickhero.config import Config  # noqa: E402
from pickhero.tabs.loader import load_gp_file, list_tracks  # noqa: E402

# The analysis itself lives in the package now: the app runs the very same
# measurement when the player asks it to sync a song, and two copies of it
# would be two answers to one question. What is left here is the report.
HOP = autosync.HOP
FRAME = autosync.FRAME
WINDOW_S = autosync.WINDOW_S
STEP_S = autosync.STEP_S
MAX_LAG_S = autosync.MAX_LAG_S
OUTLIER_S = autosync.OUTLIER_S

# One row printed per this many windows. The step above is what the LOCAL
# rate check needs -- at 15 s a whole minute held three windows and the
# uniformity test silently fell back to a single bucket, which is how a
# rate that doubles came back reported as steady. The table on screen is a
# different question and stays readable at every fourth row.
PRINT_EVERY = 4

# What counts as "apart". The old verdict called anything under 3 SECONDS
# "in sync" -- a threshold nobody fitted, and this song landed at 2.9 s and
# was reported as agreeing while the player was watching the picture walk
# a beat and a half away from the sound. 100 ms is the usual limit for
# picture and sound being taken as one event, and it is close to the app's
# own hit window.
AUDIBLE_MS = 100.0

# Two local rates differing by more than this are not one tempo. It decides
# whether a single stretch factor could ever have worked -- which, since the
# app warps the tab through a chain of sync points instead, is now a remark
# about the recording rather than a verdict on what can be done about it.
RATE_SPREAD_MS = 4.0

decode = autosync.decode
chroma_of_audio = autosync.chroma_of_audio
chroma_of_tab = autosync.chroma_of_tab_file
drift_curve = autosync.drift_curve
read_report = autosync.read_report
usable_rows = autosync.usable_rows
breaks_in = autosync.breaks_in
sections = autosync.sections
merged_sections = autosync.merged_sections
robust_line = autosync._robust_line


def onset_envelope(x: np.ndarray, hop: int) -> np.ndarray:
    n = len(x) // hop
    mag = np.abs(np.fft.rfft(x[:n * hop].reshape(n, hop) * np.hanning(hop),
                             axis=1))
    flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
    return flux - flux.mean()


def tempo_of(env: np.ndarray, fps: float) -> tuple[float, float]:
    """BPM by autocorrelation, with the peak's height as its own confidence."""
    seg = env - env.mean()
    ac = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
    lo, hi = int(fps * 60 / 200), int(fps * 60 / 70)
    if hi <= lo or hi > len(ac):
        return 0.0, 0.0
    k = lo + int(np.argmax(ac[lo:hi]))
    return 60.0 * fps / k, float(ac[k] / (ac[0] + 1e-9))


def _write_sync(args, tab_path: Path, rows) -> None:
    """Store the measured points, so the app can warp the tab onto this file.

    The same rows the table above was drawn from, thinned to the fewest
    MEASURED places that still reproduce the curve. Nothing is modelled and
    no point is invented: what is stored is a subset of what was read.
    """
    points = autosync.points_from_rows(rows)
    if len(points) < 2:
        print("\n  Zu wenige brauchbare Fenster -- keine Sync-Punkte "
              "geschrieben.")
        return
    key = args.song_key or tab_path.stem
    config = Config.load()
    config.set_mp3_anchors_for(key, points)
    # A rate correction and a chain of points are two answers to one
    # question, and the old one rebuilt the recording. Clearing it is part
    # of writing these.
    config.set_mp3_rate_for(key, 1.0)
    config.save()
    print(f"\n  {len(points)} Sync-Punkte fuer \"{key}\" gespeichert:")
    for at, off in points:
        print(f"    {int(at // 60000)}:{int(at // 1000) % 60:02d}"
              f"  {off:+8.0f} ms")
    print("  Die App richtet das Tab damit an dieser Aufnahme aus. "
          "Im Song: Shift+S setzt\n  einen weiteren Punkt von Hand, "
          "Ctrl+Shift+S loescht alle.")


def mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def breaks_with_span(kept):
    """(last reading before, first after, the jump) for every break.

    ``breaks_in`` names the reading AFTER a break, which is all the map
    needs. A REPORT needs the one before it as well: the two together are
    how wide the uncertainty is, and a jump localised to two minutes is not
    a place anybody can put a sync point. Positions are window CENTRES,
    which is what a stored point carries.
    """
    at_break = dict(breaks_in(kept))
    half = WINDOW_S / 2.0
    out = []
    for (before, _), (after, _) in zip(kept, kept[1:]):
        if after in at_break:
            out.append((before + half, after + half, at_break[after]))
    return out


def bars_worth(timeline, when_s: float, seconds: float):
    """(bars, the length of one bar there) that much time is worth.

    A break said in seconds is a number; said in BARS it is something to act
    on, because what a tab holds and a recording does not is a whole number
    of bars. The length is read where the break is rather than averaged, so
    a tempo change elsewhere cannot answer about this stretch.
    """
    at = when_s * 1000.0
    lengths = [m.end_ms - m.start_ms for m in timeline.measures
               if m.end_ms > m.start_ms]
    if not lengths:
        return None
    here = [m.end_ms - m.start_ms for m in timeline.measures
            if m.start_ms <= at < m.end_ms and m.end_ms > m.start_ms]
    bar_ms = here[0] if here else float(np.median(lengths))
    return seconds * 1000.0 / bar_ms, bar_ms / 1000.0


def section_fits(kept):
    """Each section with the line fitted to it, in time order."""
    out = []
    for part in merged_sections(sections(kept)):
        ats = [a for a, _ in part]
        lags = [b for _, b in part]
        slope, intercept = robust_line(ats, lags)
        residual = [b - (slope * a + intercept) for a, b in part]
        out.append((part, slope, intercept, residual))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tab")
    ap.add_argument("audio")
    ap.add_argument("--track", type=int, default=None)
    ap.add_argument("--write-sync", action="store_true",
                    help="store the measured sync points for this song, so "
                         "the app plays the tab warped onto this recording")
    ap.add_argument("--song-key", default=None,
                    help="which song to store them under; the tab file's "
                         "name without its extension by default, which is "
                         "what the app uses")
    args = ap.parse_args()

    tab_path, audio_path = Path(args.tab), Path(args.audio)
    for path in (tab_path, audio_path):
        if not path.exists():
            print(f"Nicht gefunden: {path}")
            return 1

    # ---- what the tab says -------------------------------------------
    timeline = load_gp_file(tab_path, track_index=args.track)
    print(f"TAB  {tab_path.name}")
    print(f"  {len(timeline.notes)} Noten, {len(timeline.measures)} gespielte "
          f"Takte, Laenge {timeline.duration_ms / 1000:.1f}s "
          f"({int(timeline.duration_ms / 1000) // 60}:"
          f"{int(timeline.duration_ms / 1000) % 60:02d})")
    if tab_path.suffix.lower() in (".gp", ".gpx"):
        try:
            root = ET.fromstring(zipfile.ZipFile(tab_path)
                                 .read("Content/score.gpif").decode("utf-8"))
            bars = root.findall(".//MasterBar")
            sigs = {mb.findtext("Time") for mb in bars
                    if mb.find("Time") is not None}
            tempos = [(a.findtext("Bar"), a.findtext("Value"))
                      for a in root.findall(".//Automation")
                      if a.findtext("Type") == "Tempo"]
            repeats = sum(1 for mb in bars if mb.find("Repeat") is not None)
            print(f"  {len(bars)} geschriebene Takte, Taktarten {sorted(sigs)},"
                  f" {repeats} Wiederholungszeichen")
            print(f"  Tempo-Eintraege: "
                  f"{', '.join(f'Takt {b}: {v}' for b, v in tempos) or 'keine'}")
        except Exception as exc:                     # noqa: BLE001
            print(f"  (GPIF nicht lesbar: {exc})")

    # ---- what the recording says -------------------------------------
    audio, sr = decode(audio_path)
    env = onset_envelope(audio, 512)
    bpm, conf = tempo_of(env, sr / 512)
    print(f"\nAUFNAHME  {audio_path.name}")
    print(f"  Laenge {len(audio) / sr:.1f}s "
          f"({int(len(audio) / sr) // 60}:{int(len(audio) / sr) % 60:02d}), "
          f"Tempo {bpm:.2f} BPM (Guete {conf:.2f})")
    print(f"  Laengendifferenz zum Tab: "
          f"{len(audio) / sr - timeline.duration_ms / 1000:+.1f}s")

    # ---- do they run together? ---------------------------------------
    rec_chroma, fps = chroma_of_audio(audio, sr)
    tab_chroma = chroma_of_tab(tab_path, fps)
    if not len(tab_chroma):
        print("\nKeine Noten im Tab -- nichts auszurichten.")
        return 1
    rows = drift_curve(tab_chroma, rec_chroma, fps)
    if len(rows) < 4:
        print("\nZu wenige Fenster fuer eine Aussage.")
        return 0

    # One line through the WHOLE song is what this tool used to fit, and on a
    # song whose tab holds bars the recording does not it reports the
    # staircase as a speed: Dry County came back at "-2.65 % Wanderung",
    # which is not a rate -- it is eight bars. The sections machinery that
    # answers it has been in autosync for months, so it is read from there
    # rather than fitted a second way here. Two answers to one question is
    # the fault this project has paid for at the repeats and the transpose.
    report = read_report(rows)
    kept = usable_rows(rows)
    unusable = report["windows"] - report["ambiguous"] - report["usable"]
    print(f"\nLESEN  ({report['windows']} Fenster)")
    print(f"  nicht eindeutig        {report['ambiguous']:4d}   (konnten "
          f"einen Refrain nicht vom anderen unterscheiden)")
    print(f"  Ausreisser/zu klein    {unusable:4d}")
    print(f"  verwendbar             {report['usable']:4d}   "
          f"= {report['share'] * 100:.0f} %")
    if report["covered"]:
        lo, hi = report["covered"]
        print(f"  gemessen  {mmss(lo)}-{mmss(hi)} von {mmss(report['song_s'])}")
    print(f"  Abschnitte {report['sections']}, davon "
          f"{report['sections_used']} verwendbar")
    for lo, hi in report["unreadable"]:
        print(f"    nicht lesbar  {mmss(lo)}-{mmss(hi)}")

    if not kept:
        print("\nKein Abschnitt laesst sich ausrichten. Das ist ein "
              "Ergebnis, kein Fehler:\ndie Aufnahme passt so nicht zum Tab.")
        return 0

    # ---- where they stop being the same piece of music ---------------
    breaks = breaks_with_span(kept)
    if breaks:
        print("\nBRUECHE  (hier sind Tab und Aufnahme nicht dasselbe Stueck)")
        for before, after, jump in breaks:
            who = ("der Tab hat Musik, die die Aufnahme nicht hat"
                   if jump < 0 else
                   "die Aufnahme hat Musik, die der Tab nicht hat")
            print(f"  zwischen {mmss(before)} und {mmss(after)}: "
                  f"{abs(jump):.1f}s -- {who}")
            worth = bars_worth(timeline, before, abs(jump))
            if worth:
                bars, bar_s = worth
                # In BARS, because what a tab holds and a recording does not
                # is a whole number of them -- and that is the thing to act
                # on. Seconds alone are a number nobody can do anything with.
                print(f"    das sind {bars:.1f} Takte "
                      f"(ein Takt dort: {bar_s:.2f}s)")
                if bars < 1.0:
                    # Music cannot be missing by two thirds of a bar. Under
                    # one bar this is the break threshold firing on a reading
                    # at a section edge, not an arrangement difference -- and
                    # saying "0.7 Takte fehlen" sends somebody looking for
                    # something that is not there.
                    print("    Unter einem Takt: das ist eher ein "
                          "Lesefehler am Abschnittsrand\n    als fehlende "
                          "Musik.")
            if after - before > 2 * STEP_S:
                print(f"    Die Stelle ist nur auf {after - before:.0f}s "
                      f"eingegrenzt -- dazwischen\n    ist kein Fenster "
                      f"lesbar, also kann niemand sagen WO genau.")

    # ---- each section, with its own line -----------------------------
    fits = section_fits(kept)
    print("\nABSCHNITTE")
    print(f"  {'von-bis':<14}{'Fenster':>8}{'Versatz':>10}"
          f"{'Wanderung':>12}{'Rest':>8}")
    half = WINDOW_S / 2.0
    for part, slope, intercept, residual in fits:
        ats = [a for a, _ in part]
        span = f"{mmss(ats[0] + half)}-{mmss(ats[-1] + half)}"
        worst = max(abs(r) for r in residual)
        print(f"  {span:<14}{len(part):8d}"
              f"{slope * ats[0] + intercept:+9.2f}s"
              f"{slope * 1000:+10.2f} ms/s{worst * 1000:7.0f} ms")

    # Every window is FITTED; only the table is thinned. The residual per
    # window is the point of this tool -- it is what says an outlier was
    # named and counted rather than quietly moving the answer.
    print(f"\nVERSATZ  ({'Rest je Fenster gegen die Gerade seines Abschnitts'})")
    print(f"  {'Tab-Zeit':>9}{'Versatz':>10}{'erwartet':>11}{'Rest':>8}")
    for part, slope, intercept, residual in fits:
        for i, ((at, lag), res) in enumerate(zip(part, residual)):
            if i % PRINT_EVERY and i not in (0, len(part) - 1):
                continue
            print(f"  {mmss(at + half):>9}{lag:9.1f}s"
                  f"{slope * at + intercept:10.1f}s{res:8.1f}")

    if args.write_sync:
        _write_sync(args, tab_path, rows)

    # ---- the verdict -------------------------------------------------
    if breaks:
        total = sum(abs(j) for _, _, j in breaks)
        print(f"\n  -> Tab und Aufnahme sind nicht dasselbe Arrangement: "
              f"{len(breaks)} Bruch/Brueche,\n     zusammen {total:.1f}s. "
              f"Kein Offset und keine Rate ueberbrueckt einen Bruch.")
        print("     Entweder eine Aufnahme, die zum Tab passt, oder die Takte "
              "aus dem Tab\n     nehmen -- oder je Abschnitt ein Sync-Punkt "
              "von Hand (Shift+S).")

    # Is the rate CONSTANT within the longest section? That is the whole
    # question behind "can one number fix this", and it has to be asked of a
    # section rather than of the song: across a break the answer is about
    # the arrangement and not about a tempo.
    part, slope, intercept, residual = max(
        fits, key=lambda f: f[0][-1][0] - f[0][0][0])
    ats = [a for a, _ in part]
    lags = [b for _, b in part]
    where = f"{mmss(ats[0] + half)}-{mmss(ats[-1] + half)}"
    total = slope * (ats[-1] - ats[0])
    worst = max(abs(r) for r in residual)
    print(f"\n  Laengster Abschnitt {where}: {total:+.1f}s ueber "
          f"{(ats[-1] - ats[0]):.0f}s.")
    if abs(total) * 1000 < AUDIBLE_MS:
        print("  -> Darin laufen Tab und Aufnahme zusammen. Was in der App "
              "auseinanderlaeuft,\n     entsteht in der App: Offset, Resync "
              "oder verlorene Frames.")
        return 0

    reaches = AUDIBLE_MS / 1000.0 / abs(slope) if slope else float("inf")
    print(f"  -> Darin laufen sie auseinander. {AUDIBLE_MS:.0f} ms sind nach "
          f"{reaches:.0f}s erreicht.")
    print("\n  Abschnitt        lokale Wanderung")
    locals_ = []
    for lo in range(int(ats[0]), int(ats[-1]), 45):
        sel = [(a, b) for a, b in part if lo <= a < lo + 60]
        if len(sel) >= 5:
            m = float(np.polyfit([a for a, _ in sel], [b for _, b in sel],
                                 1)[0])
            locals_.append(m)
            print(f"  {lo:4d}-{lo + 60:4d}s      {m * 1000:+8.2f} ms/s")
    if len(locals_) < 3:
        # Not enough clean windows to say either way, and saying "steady"
        # for want of data is how the first version of this reported a rate
        # that doubles as constant.
        print("     Ob die Wanderung gleichmaessig ist, laesst sich mit so "
              "wenigen sauberen\n     Fenstern nicht sagen.")
    elif (max(locals_) - min(locals_)) * 1000 > RATE_SPREAD_MS:
        print(f"     Die Wanderung ist NICHT gleichmaessig "
              f"({min(locals_) * 1000:+.1f} bis {max(locals_) * 1000:+.1f} "
              f"ms/s), also\n     wurde die Aufnahme nicht zu einem Klick "
              f"gespielt. Eine einzige Korrektur\n     von "
              f"{-slope * 100:+.2f} % liesse bis zu {worst * 1000:.0f} ms "
              f"stehen -- besser, aber nicht sauber.")
    else:
        print(f"     Die Wanderung ist gleichmaessig, also reicht eine "
              f"Korrektur von {-slope * 100:+.2f} %\n     "
              f"(Restfehler bis {worst * 1000:.0f} ms).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
