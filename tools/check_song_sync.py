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
    tab_chroma, n_notes = chroma_of_tab(tab_path, fps)
    if not len(tab_chroma):
        print("\nKeine Noten im Tab -- nichts auszurichten.")
        return 1
    rows = drift_curve(tab_chroma, rec_chroma, fps)
    if len(rows) < 4:
        print("\nZu wenige Fenster fuer eine Aussage.")
        return 0
    times = np.array([r[0] for r in rows])
    lags = np.array([r[1] for r in rows])
    # Robust line: the median over all pairwise slopes, which a handful of
    # windows matching the wrong chorus cannot move.
    slopes = [(lags[j] - lags[i]) / (times[j] - times[i])
              for i in range(len(rows)) for j in range(i + 1, len(rows))
              if times[j] - times[i] > 30.0]
    slope = float(np.median(slopes)) if slopes else 0.0
    intercept = float(np.median(lags - slope * times))
    residual = lags - (slope * times + intercept)
    used = int((np.abs(residual) <= OUTLIER_S).sum())

    print(f"\nVERSATZ  ({used} von {len(rows)} Fenstern auf der Geraden)")
    print(f"  {'Tab-Zeit':>9}{'Versatz':>10}{'erwartet':>11}{'Rest':>8}")
    for i, ((t, lag, _), fitted, res) in enumerate(
            zip(rows, slope * times + intercept, residual)):
        out = abs(res) > OUTLIER_S
        # Every window is FITTED; only the table is thinned, and an outlier
        # is always shown -- it is the thing worth seeing.
        if i % PRINT_EVERY and not out:
            continue
        mark = "   Ausreisser" if out else ""
        print(f"  {int(t) // 60:6d}:{int(t) % 60:02d}{lag:9.1f}s"
              f"{fitted:10.1f}s{res:8.1f}{mark}")
    if used < 4:
        print("\nZu wenige Fenster liegen auf einer Geraden. Das ist ein "
              "Ergebnis, kein Fehler:\ndie Aufnahme laesst sich so nicht "
              "ausrichten.")
        return 0

    print(f"\n  Anfang {intercept:+.1f}s, Wanderung {slope * 1000:+.1f} ms "
          f"pro Sekunde ({slope * 100:+.2f} %)")

    if args.write_sync:
        _write_sync(args, tab_path, rows)

    # Is the rate CONSTANT? That is the whole question behind "can one
    # number fix this", and a single fitted line cannot answer it -- it
    # reports an average and hides a rate that doubles halfway through. A
    # band playing without a click does exactly that, and no correction
    # factor, offset or stretch can follow it.
    print("\n  Abschnitt        lokale Wanderung")
    locals_ = []
    # Inliers only. A window that matched the wrong chorus is 14 s out and
    # would invent a rate change wherever it happens to fall.
    ok = np.abs(residual) <= OUTLIER_S
    for lo in range(0, int(times[-1]), 45):
        sel = ok & (times >= lo) & (times < lo + 60)
        if sel.sum() >= 5:
            m = float(np.polyfit(times[sel], lags[sel], 1)[0])
            locals_.append(m)
            print(f"  {lo:4d}-{lo + 60:4d}s      {m * 1000:+8.2f} ms/s")

    total = slope * (times[-1] - times[0])
    worst = float(np.abs(residual[ok]).max()) if ok.any() else 0.0
    print(f"\n  Insgesamt {total:+.1f}s ueber den Song.")
    if abs(total) * 1000 < AUDIBLE_MS:
        print("  -> Tab und Aufnahme laufen zusammen. Was in der App "
              "auseinanderlaeuft,\n     entsteht in der App: Offset, "
              "Resync oder verlorene Frames.")
        return 0

    reaches = AUDIBLE_MS / 1000.0 / abs(slope) if slope else float("inf")
    print(f"  -> Tab und Aufnahme laufen auseinander. {AUDIBLE_MS:.0f} ms "
          f"sind nach {reaches:.0f}s erreicht.")
    if len(locals_) < 3:
        # Not enough clean windows to say either way, and saying "steady"
        # for want of data is how the first version of this reported a rate
        # that doubles as constant.
        print("     Ob die Wanderung gleichmaessig ist, laesst sich mit "
              "so wenigen sauberen\n     Fenstern nicht sagen.")
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
