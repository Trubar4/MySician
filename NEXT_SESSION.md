# Prompt für die nächste Konversation

Alles ab der Trennlinie in eine neue Unterhaltung kopieren.

---

Wir arbeiten weiter an **MySician** (Repo `Trubar4/MySician`). Lies zuerst
`HANDOFF.md` und `CLAUDE.md` — dort steht der Stand, mein Setup und warum die
Dinge so gebaut sind, wie sie sind. Der Branch, auf dem gearbeitet wird, steht
in der Datei `UPLOAD_BRANCH` im Repo-Hauptverzeichnis.

Kurz zu mir: ich bin nicht technisch ("vibecoding"). Bitte **auf Deutsch
antworten**, Befehle zum Kopieren geben, und erklären, was eine Änderung für
mich als Spieler bedeutet. Ich spiele vorwiegend Rock und Metal (auch Pop und
Country) und übe bei Yousician auf Pro-Level — daran messe ich die App.

Meine Reihenfolge: **1. Erkennung fertig machen, 2. Klingende Saiten,
3. MP3-Backing, 4. Einstellungsbildschirm.** GP7-Techniken und
Palm-Mute-Nachsicht ganz hinten.

**Wichtig zum Einstieg: bau nichts, bevor du den Run-Log gelesen hast.**

Stand der Messung (alles in `HANDOFF.md`, Punkt 3, mit Tabelle):

- Meine Aufnahme vom 19.08. wurde von der App mit **34,6 %** bewertet.
- Dieselbe Aufnahme, durch denselben Detektor und denselben Matcher offline
  laufen gelassen: **97,4 %**. Der Detektor allein hört 52 von 54 Anschlägen
  richtig.
- Es liegt also **nicht** an der Erkennung, nicht am Matching, nicht am
  Akkord-Prüfer, nicht an verworfenen Puffern und nicht an der GP-Datei.
- Wo genau die App die Noten verliert, ist noch offen. Genau dafür gibt es
  jetzt den **Run-Log**.

**Erster Schritt: ich spiele einmal `songs/timing_test_100bpm.gp5` durch.**
Am Ende schreibt die App von selbst eine Datei nach
`C:\Users\Admin\.pickhero\run_<song>_<zeit>.txt` (Taste **D** schreibt sie
auch mitten im Song). Der Abschlussbildschirm sagt zusätzlich, wie viele
Anschläge überhaupt **gehört** wurden — das trennt "gar nicht gehört" von
"gehört, aber nicht gewertet". Frag mich nach dieser Datei und lies sie,
bevor du irgendetwas änderst.

Was in `HANDOFF.md` als Verdächtige steht und der Log entscheidet: das
Noise-Gate (dieselbe Aufnahme fällt bei -40 dB auf 80 %, bei -30 dB auf 52 %,
gegen 96 % beim Standard -60 dB), ein Tempo-Wechsel mitten im Song unter der
alten, nicht neu verankerten Uhr, und ein zweiter offener Audio-Stream. Die
letzten beiden sind bereits repariert, aber ob sie im 34,6-%-Lauf beteiligt
waren, sagt erst der Log.

Kleinigkeit für denselben Lauf: meine `timing_test_100bpm.gp5` ist noch die
alte Fassung mit 78 Noten. `python tools/make_timing_test.py` erzeugt die
aktuelle, in der der Achtel-Akkord-Teil Luft bekommt.

**Erledigt und nicht neu aufzurollen** (Details in `HANDOFF.md`):

- Timing: gemessen, Urteil `latency`, mit `K` erledigt. Per-Saiten-Offsets
  sind ausgeschlossen.
- Akkorde werden rot: Ursache gefunden und behoben, 54 % → 100 % bei null
  Fehlalarmen.
- Saiten-Urteile bei schnellen Akkorden: Grenze jetzt 255 ms.
- Bundfilter überlebt keinen Neustart mehr; Notengröße/Vorschauzeit erledigt;
  Stimmung steht im HUD.
- Der Analyse-Fehler, der die letzte Sitzung gekostet hat: das Werkzeug las
  eine bei 80 % gespielte Aufnahme gegen das 100-%-Raster und meldete 22 %
  statt 96 %. Behoben, mit Test.

**Meine Festlegungen** (stehen auch in `HANDOFF.md`):

- Wo eine Note gegriffen wird, darf nie einen Unterschied machen.
- Ein Oktavfehler darf grün bleiben.
- Eine Dead Note zählt allein durch den Anschlag.
- Ein zu flacher Bend ist gelb, nicht rot; die Zielhöhe muss so lange
  gehalten werden wie geschrieben, etwa auf einen Viertelton genau.
- Bei Sechssaiten-Akkorden im Zweifel tolerant.

**Zum MP3-Backing (Punkt 3):** Dateiauswahl, Pfad pro Song gespeichert,
an/aus schaltbar, Sync-Offset pro Song. Einschätzung in `HANDOFF.md`,
Punkt 5. Zwei Dinge vorab mit mir klären: ob MP3 und MIDI-Backing
Alternativen sein sollen statt übereinander, und der Dateidialog.
