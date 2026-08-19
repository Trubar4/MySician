# Prompt für die nächste Konversation

Alles ab der Trennlinie in eine neue Unterhaltung kopieren.

---

Wir arbeiten weiter an **MySician** (Repo `Trubar4/MySician`). Lies zuerst
`HANDOFF.md` und `CLAUDE.md` — dort steht der Stand, mein Setup und warum die
Dinge so gebaut sind, wie sie sind.

Kurz zu mir: ich bin nicht technisch ("vibecoding"). Bitte **auf Deutsch
antworten**, Befehle zum Kopieren geben, und erklären, was eine Änderung für
mich als Spieler bedeutet. Ich spiele vorwiegend Rock und Metal (auch Pop und
Country) und übe bei Yousician auf Pro-Level — daran messe ich die App.

**Branch:** Arbeite auf `claude/mysician-timing-measurement-au2v0m` bzw. auf
dem, was in `HANDOFF.md` steht, falls der PR schon gemerged ist. Mein lokales
Repo hing eine Weile auf einem alten Branch — falls `git push` bei mir
fehlschlägt, ist das die Ursache.

## Reihenfolge, die ich will

1. Erkennung fertig machen
2. Klingende Saiten
3. MP3-Backing
4. Einstellungsbildschirm

**GP7-Techniken und Palm-Mute-Nachsicht bitte ganz hinten anstellen.**

## Wichtig zum Einstieg: alle alten Messungen sind wertlos

In der letzten Sitzung kam heraus, dass die App bei jedem verworfenen
Audio-Puffer ihre **Sample-Uhr angehalten** hat. Dadurch wurden alle folgenden
Anschläge zu früh gestempelt, kumulativ, bis nichts mehr passte. An meiner
echten Aufnahme gemessen: 42/46 Anschläge richtig gehört ohne Verluste,
**17/46** mit 2 % Verlust nach altem Verhalten, 40/46 mit weiterlaufender Uhr.
Der Fehler ist behoben.

**Das heißt: jede Trefferquote, die vor dem 19.08. aus der laufenden App kam,
ist durch diesen Fehler hindurch gemessen.** Die 24 %, die mich gestört haben,
stammen von einer Aufnahme, die der Detektor selbst mit **91 %** liest.

**Erster Schritt der Sitzung ist deshalb: frag mich nach einem frischen Lauf**
von `songs/timing_test_100bpm.gp5` — Trefferquote, die Zeile `Audio dropouts`
im HUD, und `Shift+Y`. Bau nichts an der Erkennung, bevor du weißt, was davon
überhaupt noch übrig ist. Möglicherweise erledigt sich Punkt 1 von selbst.

## Was zu Punkt 1 an Kandidaten offen ist

Falls nach der Neumessung noch etwas fehlt, sind das die gemessenen
Verdächtigen (Details in `HANDOFF.md`, Punkt 3):

- **Konfidenzschwelle** 0,80 → 0,65: gemessen grenzwertig (+2 Powerchord- und
  +4 Akkord-Anschläge, aber ein zusätzlicher **falscher** Ton bei Einzeltönen,
  über nur 150 Anschläge Stichprobe). Nicht ohne mehr Daten ändern.
- **Doppelte Anschläge** bei einzeln gespielten Akkorden (1 von 4, 2 von 4
  Abständen unter 250 ms). Fürs Werten harmlos, verrauscht den Timing-Bericht.

## Erledigt und nicht neu aufzurollen

- **Timing**: gemessen, Urteil `latency`, mit `K` erledigt. Mittlerer Fehler
  +4 ms bei ±13 ms Streuung, Status `synced`. **Per-Saiten-Offsets sind
  ausgeschlossen** (9 ms Unterschied = Zufall), nicht offen.
- **Akkorde werden rot**: Ursache war, dass ein Sechssaiten-Anschlag dem
  monophonen YIN keine Periode gibt. Ein tonloser Anschlag schreibt jetzt einen
  Akkord ab 3 Saiten gut, der Saitenprüfer kontrolliert weiterhin. 54 % → 100 %
  bei null Fehlalarmen, alle absichtlichen Fehlgriffe weiter erkannt.
- **Saiten-Urteile bei schnellen Akkorden**: die 335-ms-Grenze war veraltet,
  nicht physikalisch. Jetzt 255 ms (Achtel bis ~118 BPM).
- **Bundfilter**: überlebt keinen Neustart mehr.
- **Notengröße/Vorschauzeit**: dichte Songs schrumpfen die Notenköpfe pro Song,
  um auf 4 s Vorschau zu kommen. Die Bildrate war nie das Problem (74-106 FPS
  gemessen).
- **Stimmung** steht im HUD, gelb mit "← retune" wenn nicht Standard.

## Meine Festlegungen (stehen auch in `HANDOFF.md`)

- Wo eine Note gegriffen wird, darf nie einen Unterschied machen.
- Ein Oktavfehler darf grün bleiben.
- Eine Dead Note zählt allein durch den Anschlag.
- Ein zu flacher Bend ist gelb, nicht rot; die Zielhöhe muss so lange gehalten
  werden wie geschrieben, etwa auf einen Viertelton genau.
- Bei Sechssaiten-Akkorden im Zweifel tolerant.

## Zum MP3-Backing (Punkt 3)

Ich will echte MP3s als Hintergrund mitlaufen lassen: Dateiauswahl, Pfad pro
Song gespeichert, an/aus schaltbar, und ein Sync-Offset pro Song gespeichert,
damit es zu Klick und Tab passt. Die Einschätzung steht in `HANDOFF.md`
Punkt 5 — zwei Dinge sind vorab mit mir zu klären: ob MP3 und MIDI-Backing
Alternativen sein sollen statt übereinander, und der Dateidialog.
