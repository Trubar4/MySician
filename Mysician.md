

https://www.youtube.com/watch?v=-IWAOPi4VCc&t=275s
https://www.songsterr.com/a/wsa/thunder-love-walked-in-v4-tab-s2333598t1

#Üben von Kid Rock - Rock on
Takt 181–200 — der einzige Abschnitt ohne jeden Fortschritt: 75 % → 75 %. Alles andere hast du verbessert. Fang hier an.

Danach die drei Einzeltakte, die in beiden Läufen auftauchen:

Takt	Zeit	Lage	was dort steht
171	4:33	Bünde 7–11	ein Slide
123	3:16	Bünde 14–17	ein Bending
183	4:52	Bünde 10–12	ein Bending

Alle drei: hohe Lage plus eine Technik. Genau die Kombination, an der du hängst.

Konkret
Lagenwechsel mit Ankerfinger. Bei jedem Sprung in die 10.–14. Lage: Zeigefinger zuerst setzen, dann greifen. Dein +2-Fehler ist ein Sprung ohne Anker.
Takt 181–200 mit L in die Schleife, bei 80 % (PgDn). Der Abschnitt bewegt sich seit zwei Läufen nicht.
Takt 171 / 123 / 183 einzeln, langsam — dort ist Slide bzw. Bending mit hoher Lage kombiniert. Beim Bending: Zielton vorher anschlagen, dann blind hinbiegen.
Dein Timing ist nicht das Problem. Median −4 ms über 39 Takte, also praktisch auf dem Schlag. Nur Takt 60 fällt aus dem Rahmen (+156 ms). Da musst du nichts ändern.
Eine Sache am Setup

In allen drei Läufen: 21–30 % des Audios lagen unter der Rauschsperre. Das ist viel — die Sperre steht auf −50 dB, dein lautester Anschlag bei −13 dB. Ein paar leise Noten gehen dabei verloren, und die zählen dann als Fehler, die keine sind. Probier X (senkt die Sperre um 5 dB) vor dem nächsten Lauf; wenn die automatische Sperre an ist, schalte sie mit X bewusst ab und geh auf −60.




#Daten von einem Rechner auf den anderen Kopieren
:: auf NB1 den ganzen Ordner auf einen Stick
xcopy /E /I "%USERPROFILE%\.pickhero" D:\pickhero-vom-nb1
:: auf NB2
python tools\merge_stats.py --from D:\pickhero-vom-nb1 --dry-run
python tools\merge_stats.py --from D:\pickhero-vom-nb1


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

