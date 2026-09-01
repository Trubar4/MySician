"""Does verovio actually engrave, here, on this machine?

Importing it proves nothing: the toolkit loads without its data files and
then renders an empty page. This project has shipped that class of fault
several times -- an automatic gate that never fired, a key that wrote a file
and said nothing -- so the packaging question is asked of a rendered page,
not of an import.

    python tools/check_verovio.py
"""

import sys

MXL = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
 <part-list><score-part id="P1"><part-name>G</part-name></score-part></part-list>
 <part id="P1"><measure number="1">
  <attributes><divisions>4</divisions>
   <time><beats>4</beats><beat-type>4</beat-type></time>
   <clef><sign>TAB</sign><line>5</line></clef>
   <staff-details><staff-lines>6</staff-lines></staff-details></attributes>
  <note><pitch><step>A</step><octave>2</octave></pitch><duration>4</duration>
   <type>quarter</type><notations><technical>
   <string>5</string><fret>0</fret></technical></notations></note>
 </measure></part>
</score-partwise>
"""


def main() -> int:
    try:
        import verovio
    except Exception as exc:
        print(f"verovio does not import: {type(exc).__name__}: {exc}")
        return 1
    tk = verovio.toolkit()
    if not tk.loadData(MXL):
        print("verovio imported but could not load a score -- its data files "
              "are missing from this build.")
        return 1
    svg = tk.renderToSVG(1)
    if "tabGrp" not in svg and "tabDurSym" not in svg:
        print(f"verovio rendered {len(svg)} bytes with no tablature in it.")
        return 1
    times = tk.renderToTimemap()
    print(f"verovio OK — {len(svg)} bytes of SVG, {len(times)} timemap entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
