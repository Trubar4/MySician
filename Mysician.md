#To Dos
1. Können wir es so einstellen, dass wir beim Spurwechsel an der gleichen Stelle im Song bleiben und nicht zum Anfang zurückspringen?

2. Wie kann ich schneller vorspulen bzw. in 30 Sekunden-Schritten springen?

3. Kleiner machen geht auch nicht mehr richtig. Damit würde ich nämlich das Bild verlangsamen, aber es hängt meist bei Größe 1 und ignoriert kleiner machen. Größer machen geht, aber das macht den Bildlauf schneller.

4. Bild und Ton sind am Anfang synchron. Nach 3:30 min. liegen Bild und Ton um ca. 25 Sekunden auseinander. Das passiert bei Tempo 70%. 
Viel Songs laufen auch bei 1fach auseinander. Hast du eine Idee, wie wir erkennen könnten, wenn das Tab selbst nicht perfekt ist und das mit dem Song syncen könnten? Idee von mir: Song zu strecken macht weniger Sinn, aber ich könnte ein paar Stellen suchen, bei denen ich den Song gut auf eine Stelle im GP mappen kann. Ev. kannst du dann zwischen diesen Sync-Punkten das GP strecken/stauchen? Ich müsste die Punkte frei wählen können bei jedem der Punkte den Offset neu einstellen, ohne dass der Offset für einen anderen Punkt verloren geht. Es könnte sogar vorkommen, dass unterschiedliche Spuren eigene Offsets benötigen.

5. Wie schwierig ist es ein Stimmgerät mit in die App einzubauen? Dafür gibt es sicher Libs oder? Man wählt die Stimmung und es zeigt um wie viel zu hoch zu niedrig je Seite?




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

