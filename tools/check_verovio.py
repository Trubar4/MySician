"""Does verovio actually engrave, here, on this machine?

Importing it proves nothing: the toolkit loads without its data files and
then renders an empty page. This project has shipped that class of fault
several times -- an automatic gate that never fired, a key that wrote a file
and said nothing -- so the packaging question is asked of a rendered page,
not of an import.

    python tools/check_verovio.py
"""

import sys
import threading
from pathlib import Path

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
    """Run on a THREAD, because that is where the app engraves.

    verovio's default resource path is thread-local: set at import in the
    main thread, it is absent on any other, and the toolkit then builds and
    loads without complaint while every page comes back empty. A check on
    the main thread would pass a build whose tab view is blank.
    """
    answer: list[int] = []
    thread = threading.Thread(target=lambda: answer.append(_check()))
    thread.start()
    thread.join(120)
    if not answer:
        print("verovio never answered.")
        return 1
    return answer[0]


def _check() -> int:
    try:
        import verovio
    except Exception as exc:
        print(f"verovio does not import: {type(exc).__name__}: {exc}")
        return 1
    verovio.setDefaultResourcePath(str(Path(verovio.__file__).parent / "data"))
    tk = verovio.toolkit()
    if not tk.loadData(MXL):
        print("verovio imported but could not load a score -- its data files "
              "are missing from this build.")
        return 1
    svg = tk.renderToSVG(1)
    if "tabGrp" not in svg and "tabDurSym" not in svg:
        print(f"verovio rendered {len(svg)} bytes with no tablature in it.")
        return 1
    # And the SVG has to become PIXELS. This check used to stop at "the SVG
    # contains tablature", and everything passed while the page on screen was
    # blank: SDL's SVG loader reports a sensible size and draws 20 pixels out
    # of 1.2 million. A rendered page is the only thing that answers this.
    try:
        import cairosvg
        png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=400,
                               background_color="#ffffff")
    except Exception as exc:
        print(f"the engraving cannot be rasterised: "
              f"{type(exc).__name__}: {exc}")
        return 1
    # Counted on DECODED pixels. Counting dark bytes in the PNG itself was
    # the first version and it is meaningless -- a PNG is compressed, so the
    # bytes are not pixels. Pillow comes with cairosvg, so it is always here.
    try:
        import io
        from PIL import Image
        image = Image.open(io.BytesIO(png)).convert("L")
        ink = sum(1 for value in image.tobytes() if value < 100)
        share = ink / max(1, image.width * image.height)
    except Exception as exc:
        print(f"the page could not be inspected: {type(exc).__name__}: {exc}")
        return 1
    # An absolute count, not a share: this fixture is ONE bar on an
    # otherwise empty page, so even a perfect render is 0.08 % of it. What
    # is being asked is whether anything was drawn at all -- SDL's loader
    # managed 20 pixels of a full page, and a blank page is 0.
    if ink < 50:
        print(f"the page rasterised with {ink} ink pixels — blank.")
        return 1
    times = tk.renderToTimemap()
    print(f"verovio OK — {len(svg)} bytes of SVG, ink on the "
          f"page ({ink} px, {share:.2%}), {len(times)} timemap entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
