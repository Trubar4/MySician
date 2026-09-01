#To Dos
Sync nochmals testen
Längere Töne (zB am Anfang von Californication)
Erkennung Papa Roach
SHIFT+A um Audio-Device zu resetten

#Features
Lass uns an zwei anderen Features arbeiten, bis ich wieder sauber testen kann:
1. Kleiner machen geht nicht richtig. Damit würde ich nämlich das Bild verlangsamen, aber es hängt meist bei Größe 1 und ignoriert kleiner machen. Größer machen geht, aber das macht den Bildlauf schneller.
2. Wie schwierig ist es ein Stimmgerät mit in die App einzubauen? Dafür gibt es sicher Libs oder? Man wählt die Stimmung und es zeigt um wie viel zu hoch zu niedrig je Seite?

https://www.songsterr.com/a/wsa/red-hot-chili-peppers-californication-tab-s439

So bedienst du es
1. Song starten, MP3 an (U)
2. Am Anfang: mit Shift+N / Shift+M ausrichten, bis Bild und Ton passen
3. Shift+S → unten steht "Sync point 1 at 0:12 …"
4. Mit Strg+→ ans Ende springen (30-Sekunden-Sprünge), dort wieder mit Shift+N/Shift+M ausrichten
5. Shift+S → "Recording stretched by +1,10 % — building it now"
(Strg+Shift+S setzt alles zurück.)

#Import
python tools\merge_stats.py --from D:\pickhero-von-NB1 --into %USERPROFILE%\.pickhero

#Stats erstellen
python tools/make_dashboard.py --open

##Songs
Shinedown - Lost in the crowd
Asking Alexandria - When the lights go on
Asking Alexandria - Dark Void
Bon Jovi - Dry Country
Disturbed - The Sound of Silence
I Prevail - Rain
I Prevail - Rise above it
Everest - Darkness always wins
Everest - Everest

##Statistik zusammenführen
git fetch origin && git switch claude/mysician-timing-remeasure-uaj3t8 && git pull

python tools\merge_stats.py --from D:\pickhero-vom-notebook --dry-run
python tools\merge_stats.py --from D:\pickhero-vom-notebook
python tools\make_dashboard.py --open


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

