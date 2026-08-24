"""Build the practice dashboard as one self-contained HTML file.

`practice_log.jsonl` is the diary; `practice_report.py` reads it in a terminal;
this turns it into the thing that was actually wanted -- a page with the shape
of the practising on it. It follows the layout of the player's own Yousician
dashboard (dark panels, year chips, a metric switch, German labels) because
that is the one they already read without thinking.

    python tools/make_dashboard.py              # writes ~/.pickhero/dashboard.html
    python tools/make_dashboard.py --open       # and opens it in the browser
    python tools/make_dashboard.py --out x.html --file some_log.jsonl

Two decisions worth keeping:

- **One file, no CDN, no build step.** The sessions are embedded as JSON and
  the charts are drawn as plain SVG in about a hundred lines. An offline-first
  practice app whose dashboard needs the internet to draw a bar chart is a
  contradiction, and a chart library would be 200 KB to draw six of them.
- **It is generated, not live.** Re-run it after practising. That keeps the
  app free of a second job and the page free of a server.
"""

import argparse
import json
import sys
import webbrowser
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickhero import practice_log  # noqa: E402
from pickhero.config import CONFIG_DIR  # noqa: E402

DEFAULT_OUT = CONFIG_DIR / "dashboard.html"


def build_data(sessions):
    """Everything the page needs, already added up. The browser only draws."""
    by_day = defaultdict(lambda: {"seconds": 0.0, "strikes": 0, "sessions": 0})
    for session in sessions:
        day = by_day[session.day]
        day["seconds"] += session.seconds
        day["strikes"] += session.strikes
        day["sessions"] += 1

    scored = [s for s in sessions if s.accuracy is not None]
    days = sorted(by_day)
    return {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "days": [{"day": d, **by_day[d]} for d in days],
        "sessions": [
            {"started": s.started, "song": s.song, "seconds": s.seconds,
             "strikes": s.strikes, "tempo": s.tempo_percent,
             "accuracy": s.accuracy}
            for s in sorted(sessions, key=lambda s: s.started)
        ],
        "totals": {
            "hours": round(sum(s.seconds for s in sessions) / 3600.0, 1),
            "strikes": sum(s.strikes for s in sessions),
            "sessions": len(sessions),
            "days": len(days),
            "songs": len({s.song for s in sessions}),
            "streak": current_streak(days),
            "best_accuracy": max((s.accuracy for s in scored), default=None),
        },
    }


def current_streak(days: list[str]) -> int:
    """Consecutive days up to today. Yesterday still counts; today may be young."""
    if not days:
        return 0
    known = {date.fromisoformat(d) for d in days}
    today = date.today()
    start = today if today in known else today - timedelta(days=1)
    streak = 0
    while start in known:
        streak += 1
        start -= timedelta(days=1)
    return streak


TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MySician — Uebungs-Dashboard</title>
<style>
  :root {
    --bg:#0f1216; --panel:#171b20; --text:#e6e8eb; --muted:#9aa3ad;
    --chip:#222831; --accent:#4cc9f0; --line:#2a2f36;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,sans-serif;
         background:var(--bg); color:var(--text); }
  header { padding:16px 24px; background:#0c0f13; border-bottom:1px solid #222;
           display:flex; justify-content:space-between; align-items:baseline; }
  header small { color:var(--muted); }
  main { padding:24px; display:grid; gap:24px; max-width:1200px; margin:0 auto; }
  .panel { background:var(--panel); border-radius:8px; padding:16px 20px; }
  h2 { font-size:14px; font-weight:600; color:var(--muted); margin:0 0 14px;
       text-transform:uppercase; letter-spacing:.06em; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
          gap:12px; }
  .kpi { background:var(--panel); border-radius:8px; padding:14px 16px; }
  .kpi .value { font-size:28px; font-weight:600; }
  .kpi .label { font-size:12px; color:var(--muted); margin-top:2px; }
  .controls { display:flex; gap:16px; flex-wrap:wrap; align-items:center; }
  label { font-size:13px; color:var(--muted); }
  select { background:#0c0f13; color:var(--text); border:1px solid var(--line);
           border-radius:6px; padding:6px 10px; }
  .chips { display:flex; gap:8px; flex-wrap:wrap; }
  .chip { padding:6px 12px; border-radius:999px; background:var(--chip);
          cursor:pointer; font-size:12px; border:1px solid var(--line); }
  .chip.active { background:var(--accent); color:#000; font-weight:600; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:var(--muted); font-weight:500; padding:6px 8px;
       border-bottom:1px solid var(--line); }
  td { padding:6px 8px; border-bottom:1px solid #1e232a; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .empty { color:var(--muted); padding:28px 0; text-align:center; }
  svg text { fill:var(--muted); font-size:11px; }
</style>
</head>
<body>
<header>
  <strong>&#127928; MySician &mdash; Uebungs-Dashboard</strong>
  <small id="generated"></small>
</header>
<main>
  <div class="kpis" id="kpis"></div>

  <div class="panel controls">
    <label>Wert
      <select id="metric">
        <option value="minutes">Minuten</option>
        <option value="strikes">Anschlaege</option>
        <option value="sessions">Sitzungen</option>
      </select>
    </label>
    <label>Jahre:</label>
    <div id="years" class="chips"></div>
  </div>

  <div class="panel"><h2 id="heatTitle">Uebungskalender</h2><div id="heat"></div></div>
  <div class="panel"><h2>Pro Monat</h2><div id="monthly"></div></div>
  <div class="panel"><h2>Pro Song</h2><div id="songs"></div></div>
  <div class="panel"><h2>Gewertete Durchlaeufe</h2><div id="accuracy"></div></div>
  <div class="panel"><h2>Letzte Sitzungen</h2><div id="recent"></div></div>
</main>

<script>
const DATA = __DATA__;
const MONTHS = ['Jan','Feb','Mrz','Apr','Mai','Jun','Jul','Aug','Sep','Okt','Nov','Dez'];
const COLORS = ['#4cc9f0','#f72585','#4361ee','#f9c74f','#90dbf4','#ff99c8'];
const METRIC_LABEL = {minutes:'Minuten', strikes:'Anschlaege', sessions:'Sitzungen'};
let activeYears = new Set();

const yearOf = s => s.slice(0, 4);
const val = (row, metric) =>
  metric === 'minutes' ? row.seconds / 60 : row[metric];

/* A round number at or above v, so an axis reads 200 and not 199. */
function niceCeil(v) {
  if (v <= 0) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(v)));
  const scaled = v / magnitude;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 2.5 ? 2.5
             : scaled <= 5 ? 5 : 10;
  return step * magnitude;
}

function svg(w, h, body) {
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}"
           preserveAspectRatio="xMinYMid meet">${body}</svg>`;
}

/* ---- bars, grouped by year --------------------------------------------- */
function groupedBars(node, labels, years, valueFor) {
  const W = 1000, H = 300, padL = 54, padB = 28, padT = 10;
  const rows = years.length || 1;
  let peak = 0;
  labels.forEach((_, i) => years.forEach(y => peak = Math.max(peak, valueFor(y, i))));
  if (peak <= 0) { node.innerHTML = '<div class="empty">Nichts in diesem Zeitraum.</div>'; return; }
  const axis = niceCeil(peak);
  const plotH = H - padB - padT, plotW = W - padL - 10;
  const slot = plotW / labels.length, bw = Math.max(2, (slot - 6) / rows);
  let body = '';
  for (let t = 0; t <= 4; t++) {                       // grid and axis
    const y = padT + plotH - plotH * t / 4;
    body += `<line x1="${padL}" y1="${y}" x2="${W - 10}" y2="${y}" stroke="#232a32"/>`;
    body += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end">${
      (axis * t / 4).toLocaleString('de', {maximumFractionDigits: 0})}</text>`;
  }
  labels.forEach((label, i) => {
    years.forEach((year, yi) => {
      const v = valueFor(year, i);
      if (!v) return;
      const h = plotH * v / axis;
      const x = padL + i * slot + 3 + yi * bw;
      body += `<rect x="${x}" y="${padT + plotH - h}" width="${bw - 1}" height="${h}"
                fill="${COLORS[yi % COLORS.length]}" rx="1"><title>${year} ${label}: ${
                v.toLocaleString('de', {maximumFractionDigits: 0})}</title></rect>`;
    });
    body += `<text x="${padL + i * slot + slot / 2}" y="${H - 8}"
              text-anchor="middle">${label}</text>`;
  });
  node.innerHTML = svg(W, H, body) + legend(years);
}

function legend(years) {
  return '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:8px">' +
    years.map((y, i) => `<span style="font-size:12px;color:var(--muted)">
      <span style="display:inline-block;width:10px;height:10px;border-radius:2px;
      background:${COLORS[i % COLORS.length]};margin-right:5px"></span>${y}</span>`)
    .join('') + '</div>';
}

/* ---- the practice calendar --------------------------------------------- */
function heatmap(node, days, metric, year) {
  const first = new Date(Date.UTC(year, 0, 1)), last = new Date(Date.UTC(year, 11, 31));
  const byDay = new Map(days.map(d => [d.day, d]));
  const cell = 13, gap = 3, padL = 30, padT = 18;
  const start = new Date(first);
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));  // Monday
  let peak = 0;
  days.forEach(d => { if (yearOf(d.day) === String(year)) peak = Math.max(peak, val(d, metric)); });
  let body = '', week = 0, seen = new Set();
  for (let d = new Date(start); d <= last; d.setUTCDate(d.getUTCDate() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    const row = (d.getUTCDay() + 6) % 7;
    if (row === 0 && seen.size) week++;
    seen.add(iso);
    const entry = byDay.get(iso);
    const v = entry ? val(entry, metric) : 0;
    const strength = peak > 0 ? v / peak : 0;
    const fill = v > 0
      ? `rgba(76,201,240,${0.18 + 0.82 * Math.min(1, strength)})`
      : (iso.slice(0, 4) === String(year) ? '#1c222a' : 'transparent');
    body += `<rect x="${padL + week * (cell + gap)}" y="${padT + row * (cell + gap)}"
             width="${cell}" height="${cell}" rx="2" fill="${fill}"><title>${iso}: ${
             v.toLocaleString('de', {maximumFractionDigits: 0})} ${METRIC_LABEL[metric]}</title></rect>`;
    if (row === 0 && d.getUTCDate() <= 7) {
      body += `<text x="${padL + week * (cell + gap)}" y="${padT - 6}">${
        MONTHS[d.getUTCMonth()]}</text>`;
    }
  }
  ['Mo', '', 'Mi', '', 'Fr', '', 'So'].forEach((label, i) => {
    if (label) body += `<text x="0" y="${padT + i * (cell + gap) + 10}">${label}</text>`;
  });
  node.innerHTML = svg(padL + 54 * (cell + gap), padT + 7 * (cell + gap) + 10, body);
}

/* ---- horizontal bars, one per song ------------------------------------- */
function songBars(node, sessions, metric) {
  const totals = new Map();
  sessions.forEach(s => {
    const row = totals.get(s.song) || {seconds: 0, strikes: 0, sessions: 0};
    row.seconds += s.seconds; row.strikes += s.strikes; row.sessions += 1;
    totals.set(s.song, row);
  });
  const rows = [...totals.entries()]
    .map(([song, row]) => ({song, value: val(row, metric)}))
    .sort((a, b) => b.value - a.value).slice(0, 12);
  if (!rows.length) { node.innerHTML = '<div class="empty">Nichts in diesem Zeitraum.</div>'; return; }
  const peak = rows[0].value, W = 1000, rowH = 26;
  let body = '';
  rows.forEach((row, i) => {
    const w = Math.max(2, (W - 320) * row.value / peak);
    body += `<text x="0" y="${i * rowH + 15}" style="fill:var(--text)">${
      row.song.length > 34 ? row.song.slice(0, 33) + '\\u2026' : row.song}</text>`;
    body += `<rect x="300" y="${i * rowH + 4}" width="${w}" height="15" rx="2"
             fill="${COLORS[0]}"/>`;
    body += `<text x="${310 + w}" y="${i * rowH + 15}">${
      row.value.toLocaleString('de', {maximumFractionDigits: 0})}</text>`;
  });
  node.innerHTML = svg(W, rows.length * rowH + 8, body);
}

/* ---- accuracy of the scored runs --------------------------------------- */
function accuracyChart(node, sessions) {
  const scored = sessions.filter(s => s.accuracy !== null && s.accuracy !== undefined);
  if (!scored.length) {
    node.innerHTML = '<div class="empty">Noch kein Durchlauf mit Wertung. ' +
      'Mit A schaltest du die Bewertung ein.</div>';
    return;
  }
  const W = 1000, H = 240, padL = 44, padB = 26, padT = 10;
  const plotH = H - padB - padT, plotW = W - padL - 12;
  const lo = Math.min(50, Math.floor(Math.min(...scored.map(s => s.accuracy)) / 10) * 10);
  const x = i => padL + (scored.length === 1 ? plotW / 2 : plotW * i / (scored.length - 1));
  const y = v => padT + plotH - plotH * (v - lo) / (100 - lo);
  let body = '';
  for (let t = lo; t <= 100; t += 10) {
    body += `<line x1="${padL}" y1="${y(t)}" x2="${W - 12}" y2="${y(t)}" stroke="#232a32"/>`;
    body += `<text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end">${t}%</text>`;
  }
  body += `<polyline fill="none" stroke="${COLORS[0]}" stroke-width="2" points="${
    scored.map((s, i) => `${x(i)},${y(s.accuracy)}`).join(' ')}"/>`;
  scored.forEach((s, i) => {
    body += `<circle cx="${x(i)}" cy="${y(s.accuracy)}" r="3.5" fill="${COLORS[0]}">
             <title>${s.started.slice(0, 10)} ${s.song}: ${s.accuracy} % bei ${
             s.tempo} % Tempo</title></circle>`;
  });
  body += `<text x="${padL}" y="${H - 6}">${scored[0].started.slice(0, 10)}</text>`;
  body += `<text x="${W - 12}" y="${H - 6}" text-anchor="end">${
    scored[scored.length - 1].started.slice(0, 10)}</text>`;
  node.innerHTML = svg(W, H, body);
}

/* ---- the page ----------------------------------------------------------- */
function kpis() {
  const t = DATA.totals;
  const cards = [
    [t.hours.toLocaleString('de'), 'Stunden geuebt'],
    [t.strikes.toLocaleString('de'), 'Noten angeschlagen'],
    [t.sessions, 'Sitzungen'],
    [t.days, 'Tage'],
    [t.streak, t.streak === 1 ? 'Tag in Folge' : 'Tage in Folge'],
    [t.best_accuracy === null ? '&mdash;' : t.best_accuracy + ' %', 'beste Wertung'],
  ];
  document.getElementById('kpis').innerHTML = cards.map(
    ([v, l]) => `<div class="kpi"><div class="value">${v}</div>
                 <div class="label">${l}</div></div>`).join('');
}

function setupYears() {
  const years = [...new Set(DATA.days.map(d => yearOf(d.day)))].sort();
  activeYears = new Set(years);
  const box = document.getElementById('years');
  box.innerHTML = '';
  years.forEach(year => {
    const chip = document.createElement('div');
    chip.className = 'chip active';
    chip.textContent = year;
    chip.onclick = () => {
      chip.classList.toggle('active');
      chip.classList.contains('active') ? activeYears.add(year) : activeYears.delete(year);
      redraw();
    };
    box.appendChild(chip);
  });
  return years;
}

function redraw() {
  const metric = document.getElementById('metric').value;
  const days = DATA.days.filter(d => activeYears.has(yearOf(d.day)));
  const sessions = DATA.sessions.filter(s => activeYears.has(yearOf(s.started)));
  const years = [...activeYears].sort();

  const heatYear = years.length ? years[years.length - 1] : new Date().getFullYear();
  document.getElementById('heatTitle').textContent =
    `Uebungskalender ${heatYear} \\u2014 ${METRIC_LABEL[metric]}`;
  heatmap(document.getElementById('heat'), DATA.days, metric, Number(heatYear));

  groupedBars(document.getElementById('monthly'), MONTHS, years, (year, month) =>
    days.filter(d => yearOf(d.day) === year && Number(d.day.slice(5, 7)) - 1 === month)
        .reduce((sum, d) => sum + val(d, metric), 0));

  songBars(document.getElementById('songs'), sessions, metric);
  accuracyChart(document.getElementById('accuracy'), sessions);

  const recent = sessions.slice(-15).reverse();
  document.getElementById('recent').innerHTML = recent.length ? `<table>
    <tr><th>Wann</th><th>Song</th><th class="num">Minuten</th>
        <th class="num">Anschlaege</th><th class="num">Tempo</th>
        <th class="num">Wertung</th></tr>` + recent.map(s => `<tr>
      <td>${s.started.replace('T', ' ').slice(0, 16)}</td>
      <td>${s.song}</td>
      <td class="num">${(s.seconds / 60).toFixed(1)}</td>
      <td class="num">${s.strikes.toLocaleString('de')}</td>
      <td class="num">${s.tempo} %</td>
      <td class="num">${s.accuracy === null || s.accuracy === undefined
        ? '\\u2014' : s.accuracy + ' %'}</td></tr>`).join('') + '</table>'
    : '<div class="empty">Nichts in diesem Zeitraum.</div>';
}

document.getElementById('generated').textContent = 'Stand: ' +
  DATA.generated.replace('T', ' ');
document.getElementById('metric').addEventListener('change', redraw);
kpis();
setupYears();
redraw();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=None, help="a practice log to read")
    ap.add_argument("--out", default=None, help="where to write the page")
    ap.add_argument("--open", action="store_true", help="open it when done")
    args = ap.parse_args()

    sessions = practice_log.read(Path(args.file) if args.file else None)
    if not sessions:
        print("Noch nichts aufgezeichnet.")
        print(f"Die App schreibt nach {practice_log.PRACTICE_FILE},")
        print("sobald du einen Song mindestens "
              f"{practice_log.MIN_SESSION_SECONDS:.0f} Sekunden gespielt hast.")
        return 1

    out = Path(args.out) if args.out else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    # A song is named after a file on disk, and the name lands inside a
    # <script>. A file called "</script>..." would end the block and render
    # the rest of the diary as HTML -- so the two characters that could do
    # that are escaped. JSON allows it and JavaScript reads it back the same.
    data = (json.dumps(build_data(sessions), ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e"))
    out.write_text(TEMPLATE.replace("__DATA__", data), encoding="utf-8")

    total = data.count('"song"')
    print(f"{out}  ({out.stat().st_size // 1024} KB, {total} Sitzungen)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
