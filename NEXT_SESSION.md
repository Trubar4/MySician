# Prompt für die nächste Konversation

Alles ab der Trennlinie in eine neue Unterhaltung kopieren.

---

Wir arbeiten weiter an **MySician** (Repo `Trubar4/MySician`, Branch
`claude/handoff-claude-md-review-va3ph4`). Lies zuerst `HANDOFF.md` und
`CLAUDE.md` — dort steht der Stand, mein Setup und warum die Dinge so gebaut
sind, wie sie sind.

Kurz zu mir: ich bin nicht technisch ("vibecoding"). Bitte **auf Deutsch
antworten**, Befehle zum Kopieren geben, und erklären, was eine Änderung für
mich als Spieler bedeutet. Ich spiele Metal und übe bei Yousician auf
Pro-Level — daran messe ich die App.

**Fang mit Thema 1 an, aber frag mich zuerst nach dem Messergebnis — bau
nichts, bevor du es kennst.** Genau dafür wurde der Bericht gebaut: damit wir
nicht mehr raten.

**1. Timing — das Messgerät steht, das Ergebnis fehlt**

Letzte Sitzung haben wir den Timing-Bericht gebaut (Taste **Y**,
`Shift+Y` schreibt die Rohwerte als CSV). Er zeigt die Verteilung meiner
Anschläge und benennt das Problem: `fine`, `latency`, `scatter`, `mixed` oder
`per_string`.

Ich wollte `songs/timing_test_100bpm.gp5` einmal durchspielen und **Y**
drücken. Frag mich nach dem Befund (oder nach dem Screenshot / der CSV),
bevor du irgendetwas änderst. Was er sagt, entscheidet, was zu tun ist:

- `latency` → **K** erledigt es bereits, es gibt nichts zu bauen.
- `scatter` → liegt nicht an der App. Sag mir das bitte klar, statt etwas
  zu erfinden.
- `mixed` → erst **K**, dann neu messen, dann weiterreden.
- `per_string` → **das ist der Fall, in dem gebaut wird**: Offsets pro Saite.
  Die Daten dafür stecken in der CSV von `Shift+Y`.
- Zu wenige Messwerte → im Bericht steht, wie viele Anschläge nicht
  zuordenbar waren; das zuerst anschauen.

Schon ausgeschlossen und nicht nochmal herzuleiten: es liegt nicht an
schlechten GP-Dateien (die Testdatei ist exakt auf dem Raster gebaut), nicht
an Wall-Clock-Jitter (Anschläge werden aus dem Sample-Zähler gestempelt) und
nicht an einem weglaufenden Auto-Sync (auf ±300 ms begrenzt, Shift+K setzt
zurück). Die Datenausbeute ist ebenfalls schon behoben — sie lag bei 39 % und
liegt jetzt bei 98 %.

**2. Bend-Auswertung**

Die Optik steht (Kurve in der Note, Badge ½/1/1½). Die Bewertung ist bewusst
großzügig: akzeptiert wird der ganze Tonhöhenbereich, den der Bend abdeckt,
weil der Detektor keinen Tonhöhenverlauf liefert. Mein Ziel: die Zielhöhe
sollte **ca. auf einen Viertelton genau** stimmen — der Übergang darf
fließend und ungenau sein, wie bei Yousician auch. Dafür bräuchte es einen
Verlauf über die Notendauer statt eines einzelnen Werts pro Anschlag.

**3. Akkorde in schneller Folge**

Die Saitenprüfung enthält sich unter ~335 ms Abstand (also bei Achteln ab ca.
90 BPM), weil ein kürzeres Fenster nachweislich falsche Urteile liefert — das
ist gemessen, siehe `tools/sweep_chord_window.py`. Schneller ginge nur mit
einem anderen Ansatz: analysieren, *bevor* der nächste Anschlag kommt, statt
danach. Das ist Forschung, kein Umbau — bitte erst abschätzen, ob es sich
lohnt.

**4. Palm Mutes und Dead Notes**

Stehen in den GP-Dateien, werden aber wie normale Noten gezeichnet. Der
kleinste Brocken von den fünf.

**5. GP7-Techniken**

GP7-Dateien laden, tragen aber keine Bends und Slides — dieser Pfad parst das
XML von Hand. GP3–GP5 laufen über pyguitarpro und sind vollständig.
