"""Reading Guitar Pro 6 files (.gpx).

A .gpx is not a zip, which is why the app refused to open one: GP6 wrapped
its score in a container of its own making. Two layers sit between the file
and the music:

- **BCFZ**, a compression scheme. A bit stream of chunks, each either a run
  of literal bytes or a back-reference into what has already been produced --
  LZ77 by another name, with the lengths written in a variable number of bits.
- **BCFS**, a miniature file system. The decompressed image is a sequence of
  4 KB sectors; a sector beginning with the number 2 is a file entry, naming
  the file and listing the sectors its contents are spread across.

Inside that file system sits `score.gpif`, and a GP6 `score.gpif` is the same
XML that GP7 and GP8 keep in their zip. So this module's whole job is to hand
that XML to the parser the app already has -- there is no second loader, no
second set of conventions, and a technique fixed for GP7 is fixed for GP6 in
the same breath.

The layout is not documented by Arobas. It is implemented here from the
reading in TuxGuitar, alphaTab and `Antti/rust-gpx-reader`, which agree with
each other down to the sector offsets.
"""

from __future__ import annotations

from pathlib import Path

MAGIC_COMPRESSED = b"BCFZ"
MAGIC_FILESYSTEM = b"BCFS"
SECTOR_SIZE = 0x1000
# The number a sector starts with when it describes a file rather than
# holding data.
ENTRY_FILE = 2
# Where things sit inside a file entry's sector.
OFFSET_NAME = 0x04
NAME_LENGTH = 127
OFFSET_SIZE = 0x8C
OFFSET_BLOCKS = 0x94


class GpxError(RuntimeError):
    """A .gpx file this module cannot read, with the reason in the message."""


class _EndOfStream(Exception):
    """The compressed bit stream ran out. Expected — see `_decompress`."""


def is_gpx_file(path: str | Path) -> bool:
    """True for a Guitar Pro 6 container, by its own first four bytes."""
    try:
        with open(path, "rb") as handle:
            return handle.read(4) in (MAGIC_COMPRESSED, MAGIC_FILESYSTEM)
    except OSError:
        return False


def read_gpif(path: str | Path) -> str:
    """The score XML out of a .gpx file."""
    data = Path(path).read_bytes()
    for name, contents in read_files(data).items():
        if name.lower().endswith("score.gpif"):
            return contents.decode("utf-8", errors="replace")
    raise GpxError("this .gpx contains no score.gpif")


def read_files(data: bytes) -> dict[str, bytes]:
    """Every file inside a .gpx container, by name."""
    magic = data[:4]
    if magic == MAGIC_COMPRESSED:
        data = _decompress(data[4:])
        magic = data[:4]
        if magic != MAGIC_FILESYSTEM:
            raise GpxError("the compressed part of this .gpx is not a BCFS "
                           "image — the file may be damaged")
    elif magic != MAGIC_FILESYSTEM:
        raise GpxError("not a Guitar Pro 6 file (expected BCFZ or BCFS)")
    return _read_filesystem(data[4:])


# -- BCFZ ---------------------------------------------------------------------

class _BitReader:
    """Bits out of a byte string, most significant first.

    Both orders are needed and they are not interchangeable: the chunk header
    fields are written most significant bit first, the lengths inside them
    least significant bit first. Getting one of them backwards produces a
    stream that decompresses for a while and then collapses, which is the
    hardest kind of bug to read backwards from the wreckage.
    """

    __slots__ = ("_data", "_byte", "_bit", "_current")

    def __init__(self, data: bytes):
        self._data = data
        self._byte = 0
        self._current = 0
        self._bit = 8                       # forces a fetch on the first read

    def bit(self) -> int:
        if self._bit == 8:
            if self._byte >= len(self._data):
                raise _EndOfStream()
            self._current = self._data[self._byte]
            self._byte += 1
            self._bit = 0
        value = (self._current >> (7 - self._bit)) & 1
        self._bit += 1
        return value

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def bits_reversed(self, count: int) -> int:
        value = 0
        for index in range(count):
            value |= self.bit() << index
        return value

    def byte(self) -> int:
        return self.bits(8)


def _decompress(payload: bytes) -> bytes:
    """Undo BCFZ. `payload` is everything after the four magic bytes."""
    if len(payload) < 4:
        raise GpxError("this .gpx is too short to contain anything")
    expected = int.from_bytes(payload[:4], "little")
    reader = _BitReader(payload[4:])
    out = bytearray()
    try:
        while len(out) < expected:
            if reader.bit():
                # A back-reference into what has already been produced. The
                # word size says how many bits the offset and the length each
                # take, which is what keeps small distances cheap.
                word_size = reader.bits(4)
                offset = reader.bits_reversed(word_size)
                length = reader.bits_reversed(word_size)
                if offset == 0 or offset > len(out):
                    raise GpxError("this .gpx refers backwards past its own "
                                   "start — the file may be damaged")
                start = len(out) - offset
                # Never more than the distance back: a run that would read its
                # own output as it writes it is not what the format means.
                out += out[start:start + min(length, offset)]
            else:
                for _ in range(reader.bits_reversed(2)):
                    out.append(reader.byte())
    except _EndOfStream:
        # Expected, and not an error. Real GP6 files stop one byte short of
        # the length they declare -- measured on 13 of alphaTab's 35 GP6 test
        # files, every one of them ending at expected - 1. The missing byte is
        # padding inside the last 4 KB sector and no file's contents reach it.
        # Refusing here was this module's first bug, and it rejected exactly
        # those 13 files.
        if not out:
            raise GpxError("this .gpx has no readable compressed data") from None
    return bytes(out)


# -- BCFS ---------------------------------------------------------------------

def _read_filesystem(image: bytes) -> dict[str, bytes]:
    """Every file in a BCFS image, which begins after its magic bytes.

    Sectors are walked in order; one that starts with `ENTRY_FILE` describes a
    file and lists the sectors holding its contents. Scanning then carries on
    AFTER that data rather than back over it, because a data sector that
    happened to begin with the number 2 would otherwise be read as a file of
    its own -- which is how these readers are written everywhere, and the
    reason the walk looks odd.
    """
    files: dict[str, bytes] = {}
    offset = 0
    while True:
        offset += SECTOR_SIZE
        if offset + 3 >= len(image):
            break
        if _int_at(image, offset) != ENTRY_FILE:
            continue
        entry = offset
        contents = bytearray()
        index = 0
        while True:
            block = _int_at(image, entry + OFFSET_BLOCKS + 4 * index)
            index += 1
            if block == 0:
                break
            start = block * SECTOR_SIZE
            contents += image[start:start + SECTOR_SIZE]
            offset = start
        # A declared size larger than the blocks hold is taken as far as it
        # goes rather than refused: the last sector of the image can be short
        # by the padding the compressor left off.
        size = max(0, min(_int_at(image, entry + OFFSET_SIZE), len(contents)))
        raw = image[entry + OFFSET_NAME:entry + OFFSET_NAME + NAME_LENGTH]
        name = raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
        files[name] = bytes(contents[:size])
    return files


def _int_at(image: bytes, offset: int) -> int:
    if offset + 4 > len(image):
        return 0
    return int.from_bytes(image[offset:offset + 4], "little", signed=True)
