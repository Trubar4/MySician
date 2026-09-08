"""Guitar Pro 6 files, and the two layers between them and the music.

A .gpx is not a zip. GP6 wrapped its score in a container of its own -- BCFZ
compression around a BCFS sector image -- which is the whole reason the app
refused to open one. Inside sits the same `score.gpif` XML that GP7 and GP8
keep in a zip, so everything past the container is the parser that already
existed.

**The real proof of this module is not in this file.** It is that all 35 of
alphaTab's Guitar Pro 6 test files decompress and load through the app's own
loader, which is what caught the one real bug here: a genuine GP6 file stops
one byte short of the length it declares, and refusing that rejected 13 of the
35. What the tests below do is hold the format still -- containers built by
hand, so a future change that breaks the bit order or the sector arithmetic
fails here rather than on the player's machine.
"""

import struct
import zipfile

import pytest

from pickhero.tabs import gpx
from pickhero.tabs.loader import load_gp_file
from pickhero.tabs.gpx import GpxError

SECTOR = gpx.SECTOR_SIZE


# -- building containers by hand ---------------------------------------------

def _bcfs_image(files: dict[str, bytes]) -> bytes:
    """A BCFS image holding `files`: one entry sector, then its data."""
    sectors = [bytearray(b"\0" * SECTOR)]          # sector 0 is never read
    for name, contents in files.items():
        blocks = [contents[i:i + SECTOR] for i in range(0, len(contents), SECTOR)]
        blocks = blocks or [b""]
        entry = bytearray(b"\0" * SECTOR)
        entry[0:4] = struct.pack("<i", gpx.ENTRY_FILE)
        encoded = name.encode()
        entry[gpx.OFFSET_NAME:gpx.OFFSET_NAME + len(encoded)] = encoded
        entry[gpx.OFFSET_SIZE:gpx.OFFSET_SIZE + 4] = struct.pack("<i", len(contents))
        first = len(sectors) + 1                    # the entry itself is next
        for index in range(len(blocks)):
            at = gpx.OFFSET_BLOCKS + 4 * index
            entry[at:at + 4] = struct.pack("<i", first + index)
        sectors.append(entry)
        for block in blocks:
            sectors.append(bytearray(block.ljust(SECTOR, b"\0")))
    return b"BCFS" + b"".join(bytes(s) for s in sectors)


class _BitWriter:
    """The other end of gpx._BitReader, for building a compressed stream."""

    def __init__(self):
        self.bits: list[int] = []

    def write(self, value: int, count: int) -> None:
        for shift in range(count - 1, -1, -1):      # most significant first
            self.bits.append((value >> shift) & 1)

    def write_reversed(self, value: int, count: int) -> None:
        for shift in range(count):                  # least significant first
            self.bits.append((value >> shift) & 1)

    def bytes(self) -> bytes:
        padded = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(str(b) for b in padded[i:i + 8]), 2)
                     for i in range(0, len(padded), 8))


def _bcfz(payload: bytes, back_reference: bool = True) -> bytes:
    """Compress `payload` the way GP6 does: literals, and one back-reference.

    Deliberately naive -- three bytes at a time -- because what is under test
    is the reader, not how well anything compresses.
    """
    writer = _BitWriter()
    position = 0
    while position < len(payload):
        chunk = payload[position:position + 3]
        # A run that repeats what came just before is written as a reference,
        # which is the case that catches a wrong bit order.
        if (back_reference and position >= len(chunk)
                and payload[position - len(chunk):position] == chunk):
            word = 4                      # bits per offset/length field
            writer.write(1, 1)
            writer.write(word, 4)
            writer.write_reversed(len(chunk), word)   # offset
            writer.write_reversed(len(chunk), word)   # length
        else:
            writer.write(0, 1)
            writer.write_reversed(len(chunk), 2)
            for byte in chunk:
                writer.write(byte, 8)
        position += len(chunk)
    return b"BCFZ" + struct.pack("<I", len(payload)) + writer.bytes()


# -- the pieces ---------------------------------------------------------------

class TestTheBitReader:
    """Both orders are needed and they are not interchangeable."""

    DATA = bytes([0b11001010, 0b11110000])

    def test_bits_come_out_most_significant_first(self):
        assert gpx._BitReader(self.DATA).bits(8) == 0b11001010

    def test_a_partial_read_is_still_most_significant_first(self):
        assert gpx._BitReader(self.DATA).bits(7) == 0b1100101

    def test_reversed_bits_come_out_least_significant_first(self):
        assert gpx._BitReader(self.DATA).bits_reversed(8) == 0b01010011

    def test_the_two_orders_disagree(self):
        """If they ever agreed, one of them would be wrong and nothing here
        would notice."""
        assert (gpx._BitReader(self.DATA).bits(8)
                != gpx._BitReader(self.DATA).bits_reversed(8))


class TestDecompression:
    def test_a_literal_stream_comes_back_unchanged(self):
        payload = b"BCFS" + bytes(range(256)) * 3
        assert gpx._decompress(_bcfz(payload, back_reference=False)[4:]) == payload

    def test_a_back_reference_comes_back_unchanged(self):
        payload = b"BCFS" + b"abc" * 40 + b"xyz"
        assert gpx._decompress(_bcfz(payload)[4:]) == payload

    def test_a_stream_that_ends_early_keeps_what_it_had(self):
        """Real GP6 files stop one byte short of the length they declare, and
        refusing them was this module's first bug -- it rejected 13 of
        alphaTab's 35 GP6 test files."""
        payload = b"BCFS" + b"hello world" * 20
        compressed = _bcfz(payload, back_reference=False)
        out = gpx._decompress(compressed[4:-1])
        assert len(out) >= len(payload) - 8
        assert payload.startswith(out)

    def test_a_stream_with_nothing_in_it_is_an_error(self):
        with pytest.raises(GpxError):
            gpx._decompress(struct.pack("<I", 500))


class TestTheFileSystem:
    def test_a_file_comes_back_by_name(self):
        image = _bcfs_image({"score.gpif": b"<GPIF/>"})
        assert gpx.read_files(image)["score.gpif"] == b"<GPIF/>"

    def test_a_file_spanning_several_sectors_is_joined_up(self):
        contents = bytes(range(256)) * 100          # over 4 KB
        image = _bcfs_image({"score.gpif": contents})
        assert gpx.read_files(image)["score.gpif"] == contents

    def test_two_files_are_both_found(self):
        image = _bcfs_image({"BinaryStylesheet": b"junk", "score.gpif": b"<x/>"})
        assert set(gpx.read_files(image)) == {"BinaryStylesheet", "score.gpif"}

    def test_a_compressed_container_reads_the_same(self):
        image = _bcfs_image({"score.gpif": b"<GPIF>hello</GPIF>"})
        assert gpx.read_files(_bcfz(image))["score.gpif"] == b"<GPIF>hello</GPIF>"


class TestWhatItRefuses:
    def test_something_that_is_not_a_gpx_says_so(self, tmp_path):
        path = tmp_path / "not-really.gpx"
        path.write_bytes(b"PK\x03\x04 this is a zip")
        with pytest.raises(GpxError, match="not a Guitar Pro 6 file"):
            gpx.read_gpif(path)

    def test_a_container_without_a_score_says_so(self, tmp_path):
        path = tmp_path / "empty.gpx"
        path.write_bytes(_bcfs_image({"BinaryStylesheet": b"junk"}))
        with pytest.raises(GpxError, match="no score.gpif"):
            gpx.read_gpif(path)

    def test_a_missing_file_is_not_mistaken_for_a_gpx(self, tmp_path):
        assert not gpx.is_gpx_file(tmp_path / "gone.gpx")


# -- end to end ---------------------------------------------------------------

GPIF = """<?xml version="1.0" encoding="utf-8"?>
<GPIF>
  <Score><Title>Bent</Title><Artist>Nobody</Artist></Score>
  <MasterTrack>
    <Tracks>0</Tracks>
    <Automations><Automation>
      <Type>Tempo</Type><Bar>0</Bar><Value>120 2</Value>
    </Automation></Automations>
  </MasterTrack>
  <Tracks><Track id="0">
    <Name>Guitar</Name>
    <GeneralMidi><Program>30</Program></GeneralMidi>
    <MIDI><Program>30</Program></MIDI>
    <Properties><Property name="Tuning">
      <Pitches>40 45 50 55 59 64</Pitches>
    </Property></Properties>
  </Track></Tracks>
  <MasterBars><MasterBar><Bars>0</Bars><Time>4/4</Time></MasterBar></MasterBars>
  <Bars><Bar id="0"><Voices>0</Voices></Bar></Bars>
  <Voices><Voice id="0"><Beats>0 1 2 3</Beats></Voice></Voices>
  <Beats>
    <Beat id="0"><Rhythm ref="0"/><Notes>0</Notes></Beat>
    <Beat id="1"><Rhythm ref="0"/><Notes>1</Notes></Beat>
    <Beat id="2"><Rhythm ref="0"/><Notes>2</Notes></Beat>
    <Beat id="3"><Rhythm ref="0"/><Notes>3</Notes></Beat>
  </Beats>
  <Rhythms><Rhythm id="0"><NoteValue>Quarter</NoteValue></Rhythm></Rhythms>
  <Notes>
    <Note id="0"><Properties>
      <Property name="String"><String>2</String></Property>
      <Property name="Fret"><Fret>7</Fret></Property>
      <Property name="Bended"><Enable/></Property>
      <Property name="BendOriginValue"><Float>0</Float></Property>
      <Property name="BendMiddleValue"><Float>100</Float></Property>
      <Property name="BendDestinationValue"><Float>100</Float></Property>
    </Properties></Note>
    <Note id="1"><Properties>
      <Property name="String"><String>2</String></Property>
      <Property name="Fret"><Fret>5</Fret></Property>
      <Property name="Slide"><Flags>1</Flags></Property>
    </Properties></Note>
    <Note id="2"><Properties>
      <Property name="String"><String>2</String></Property>
      <Property name="Fret"><Fret>7</Fret></Property>
      <Property name="HopoOrigin"><Enable/></Property>
      <Property name="PalmMuted"><Enable/></Property>
    </Properties></Note>
    <Note id="3"><Properties>
      <Property name="String"><String>2</String></Property>
      <Property name="Fret"><Fret>9</Fret></Property>
      <Property name="Muted"><Enable/></Property>
    </Properties></Note>
  </Notes>
</GPIF>
"""


@pytest.fixture(params=["gpx", "gp"])
def score(request, tmp_path):
    """The same score in both wrappers: GP6's container and GP7's zip.

    Parameterised on purpose. GP6 and GP7 differ only in what they wrap the
    XML in, and a test that covers one of them proves nothing about the other
    unless it is literally the same test.
    """
    if request.param == "gpx":
        path = tmp_path / "score.gpx"
        path.write_bytes(_bcfz(_bcfs_image({"score.gpif": GPIF.encode()})))
    else:
        path = tmp_path / "score.gp"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Content/score.gpif", GPIF)
    return load_gp_file(path)


class TestATabWithTechniques:
    """Bends and slides were read for GP3-5 and dropped for everything newer,
    so a GP6 or GP7 tab scored its techniques as wrong notes."""

    def test_the_song_loads(self, score):
        assert len(score.notes) == 4
        assert score.metadata.title == "Bent"

    def test_the_pitches_are_right(self, score):
        # B string (GPIF string 2 counting up from the low E) at fret 7.
        assert score.notes[0].midi_note == 50 + 7

    def test_a_bend_carries_its_curve(self, score):
        # GPIF writes 100 for a whole tone: two semitones, not one.
        assert score.notes[0].bend_semitones == pytest.approx(2.0)

    def test_a_bend_reaches_its_top_partway_through(self, score):
        """A middle point with no position of its own sits halfway, not at
        zero -- at zero the scoring asks for a pitch to be held before the
        string has been struck."""
        positions = [position for position, value in score.notes[0].bend
                     if value > 0]
        assert min(positions) == pytest.approx(0.5)

    def test_a_slide_is_carried(self, score):
        assert score.notes[1].slide_to_next is True

    def test_a_hammer_on_is_carried(self, score):
        assert score.notes[2].hammer_to_next is True

    def test_muting_still_works(self, score):
        assert score.notes[2].palm_mute is True
        assert score.notes[3].dead is True

    def test_a_plain_note_carries_nothing(self, score):
        assert score.notes[1].bend == ()
        assert score.notes[3].hammer_to_next is False
