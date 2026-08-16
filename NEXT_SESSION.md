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

Die App funktioniert. Offen sind fünf Themen, nach meinem Nutzen sortiert:

**1. Timing-Streuung (±80–104 ms) — das wichtigste**

Mein ältester Kritikpunkt. Die Streuung ist teils menschlich, teils Latenz,
und wir haben sie nie getrennt gemessen. Bereits ausgeschlossen: es liegt
nicht an schlechten GP-Dateien (`songs/timing_test_100bpm.gp5` ist exakt auf
dem Raster gebaut, mit Klick nur auf den Noten), nicht an Wall-Clock-Jitter
(Strikes werden aus dem Sample-Zähler gestempelt) und nicht an einem
weglaufenden Auto-Sync (auf ±300 ms begrenzt, Shift+K setzt zurück).

Was ich mir vorstelle: ein Diagnose-Bildschirm, der die Verteilung meiner
Anschläge relativ zum Raster zeigt — eine Verschiebung aller Werte in eine
Richtung ist Latenz, eine breite Streuung um Null bin ich. Ohne diese
Trennung raten wir. Schlag mir vor, wie du es messen würdest, bevor du baust.

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

Fang mit **1** an, wenn du nichts anderes empfiehlst. Sag mir vorher, was du
vorhast und was ich testen soll.
