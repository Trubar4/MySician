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

## Meine ursprüngliche Reihenfolge ist abgearbeitet

1. **Erkennung** — erledigt. Zuletzt **98,4 % (61/62)** und **96,8 % (58+2/62)**
   im Timing-Test. Die Ursache der alten 34,6 % war der **Eingangspegel**, nicht
   der Detektor: ein zu leises Signal verliert kaum Anschläge, es benennt sie
   falsch. Steht mit Messtabelle in `CLAUDE.md`.
2. **Klingende Saiten** — gemessen und zum Teil repariert. Es kommt **nie** eine
   falsche Note zurück; klingende Nachbarn kosten Anschläge **ganz ohne
   Tonhöhe**, und nur im Schnellen. Solche Anschläge werden jetzt gerettet,
   wenn dort genau eine Note geschrieben steht (schnell/klingend 8/14 → 10/14,
   gedämpfte Kontrolle gewinnt exakt nichts).
3. **MP3-Backing** — erledigt, inklusive **Zeitdehnung**: unter 100 % läuft die
   Aufnahme mit, in richtiger Tonhöhe (höchstens 15 Cent Abweichung, gegen
   −385 Cent bei bloßem Langsamerspielen).
4. **Einstellungsbildschirm** — erledigt. **`O`** in der Songliste. Zeigt alles,
   was einmal eingestellt wird, und **markiert, was vom Standard abweicht**.

## Was ich noch testen soll (bitte danach fragen)

- Den **Einstellungsbildschirm**, die **Zeitdehnung** und die **Bend-Bewertung**
  im echten Lauf — und für die Bends die Aufnahme aus Block 6.
- Das offene Experiment: läuft `record_reference.py` parallel mit, während die
  App wertet, verdirbt das die Wertung? Der 34,6-%-Lauf hatte genau das, und
  derselbe Mitschnitt liest offline 97,4 %. Bis das geklärt ist, gilt: **eine
  Wertung, die während einer Referenzaufnahme entsteht, ist kein Beweis.**

## Was als Nächstes offen ist (`HANDOFF.md` hat die Details)

- **Bend-Schwellen messen** (Punkt 7 ist gebaut, aber nicht kalibriert). Ein zu
  flacher oder nicht gehaltener Bend wird gelb — die drei Schwellen dahinter
  sind aber die einzigen der App, die an nichts gefittet wurden. Zwei Befehle
  ändern das:
  `python tools/record_reference.py --block 6` (sechs Bend-Aufnahmen, ca. eine
  Minute) und danach `python tools/check_bends.py`, das ausgibt, in welchem
  Fenster jede Schwelle liegen muss.
- **Palm-Mute-Nachsicht** (zurückgestellt und ungemessen): ob ein schwerer Chug
  ohne Tonhöhe seine Note gutgeschrieben bekommen soll. Das ist Nachsicht und
  braucht Referenzaufnahmen, kein Bauchgefühl.

## Erledigt und nicht neu aufzurollen (Details in `HANDOFF.md`)

- Timing: gemessen, Urteil `latency`, mit `K` erledigt; Per-Saiten-Offsets sind
  ausgeschlossen. Der Befund "ich würde eilen" ist **zurückgezogen** — er war
  ein Artefakt unzuverlässiger Tonhöhen, ein sauberer Lauf zeigt 0,1 %.
- Akkorde werden rot: Ursache gefunden, 54 % → 100 % bei null Fehlalarmen.
  Zweisaitige Powerchords zählen jetzt auch ohne Tonhöhe.
- Suchen/Schleife öffnen das Eingabegerät nicht mehr neu (das waren die 10
  Sekunden Hänger).
- Ein verworfener Puffer hält die Uhr nicht mehr an.
- Bundfilter überlebt keinen Neustart mehr; Notengröße nutzt jetzt die Höhe der
  Spur; Stimmung steht im HUD.
- Der Run-Log (**`D`**, und automatisch am Songende) benennt jeden Anschlag,
  jede geschriebene Note und den Pegel. Wenn etwas unklar ist: diese Datei
  schicken, nicht raten.

## Meine Festlegungen

- Wo eine Note gegriffen wird, darf nie einen Unterschied machen.
- Ein Oktavfehler darf grün bleiben.
- Eine Dead Note zählt allein durch den Anschlag.
- Ein zu flacher Bend ist gelb, nicht rot.
- Bei Sechssaiten-Akkorden im Zweifel tolerant.
