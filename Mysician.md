# Feedback
Das offset von -400 ms reicht nicht. Vielleicht liegt es am Einzählen. Shift+M/N funktioniert ansonsten.
Was macht wait on/off genau?
Die Anwendung wird sehr langsam. Wenn ich Vorspule bewegt sich das OGG nicht und danach dauert es 10 Sek. bis das NB wieder reagiert in der App.
Tempo sollte nicht auf Stumm schalten, sondern Tonhöhe anpassen und mitlaufen.
Teste aktuell mit OGG.

# Ein Experiment würde das klären, und es lohnt sich, weil sonst jeder künftige Mitschnitt einen wertlosen Punktestand danebenstellt:

cd C:\Users\Admin\.vscode\MySician\mysician\mysician
git pull
### Fenster 1:
.venv\Scripts\Activate.ps1
python tools\record_reference.py --play-along timing_test_100bpm.gp5
### Fenster 2 (parallel):
.venv\Scripts\Activate.ps1
python -m pickhero

Bricht die Quote wieder auf ~35 % ein, wissen wir: während einer Aufnahme ist der App-Score wertlos — gut zu wissen, statt es nochmal zu jagen. Bleibt sie bei ~90 %, waren es die Fixes und Punkt 1 ist erledigt.



**Features:**
○ Taste, um Midi zu muten
○ Ich hätte gerne eine Möglichkeit auch echte MP3s Background mitlaufen zu lassen.
Dafür brauche ich einen Datei-Auswähler zur Wahl des MP3s (Quelladresse wir gespeichert zum Song). Außerdem möchte ich es starten können (oder abschalten). Außerdem brauche ich eine Möglichkeit es zu Syncen, damit es mit den MIDI Klicks und der Visualisierung synchron läuft. Auch das muss gespeichert werden.



**Prüfen:**
○ Bending
○ Sliding
○ Hammer on / pull off
○ Farbschema

cd C:\\Users\\Admin.vscode\\MySician\\mysician\\mysician
git pull origin claude/mysician-timing-measurement-au2v0m
.venv\\Scripts\\Activate.ps1
python -m pytest tests -q
python tools\\make\_technique\_test.py

Kopieren des Test-files
python -m pickhero

Bild	Bedeutung	Abhilfe
Schmaler Hügel neben der Null	Latenz	K drücken
Breiter Hügel über der Null	Streuung (du)	Langsamer üben (PgDn), Fenster weiten (G)
Beides	Gemischt	K, danach bleibt Streuung übrig
Saiten mit verschiedenen Medianen	der Detektor reagiert je Saite anders	Kein globaler Offset hilft

○ Shift+Y schreibt die Rohmesswerte als CSV neben deine Einstellungen. Wenn der Befund unklar ist, schick mir die Datei.

* timing\_test\_100bpm.gp5 laden, Audio an (A), einmal ganz durchspielen.
* Y drücken.
* Schick mir einen Screenshot — oder Shift+Y und die CSV.

