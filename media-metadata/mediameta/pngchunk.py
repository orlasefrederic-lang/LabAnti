"""Lossless PNG chunk parser and rewriter.

In 1.0, editing a PNG re-encoded the entire image through Pillow, which
recompressed ``IDAT`` pixel streams and dropped custom ancillary chunks.
This module edits ``tEXt``, ``iTXt``, ``zTXt`` and ``eXIf`` chunks directly
in pure Python, keeping ``IDAT`` and every other chunk bit-for-bit intact.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .errors import CorruptFileError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT_CHUNK_TYPES = frozenset((b"tEXt", b"zTXt", b"iTXt"))
METADATA_CHUNK_TYPES = TEXT_CHUNK_TYPES | frozenset((b"eXIf", b"tIME"))


@dataclass
class PngChunk:
    type: bytes
    data: bytes

    def serialize(self) -> bytes:
        crc = zlib.crc32(self.type)
        crc = zlib.crc32(self.data, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(self.data)) + self.type + self.data + struct.pack(">I", crc)


def is_png(data: bytes) -> bool:
    return data[:8] == PNG_SIGNATURE


def parse_chunks(data: bytes) -> list[PngChunk]:
    """Parse a PNG byte stream into a list of :class:`PngChunk` objects."""
    if not is_png(data):
        raise CorruptFileError("not a PNG file (missing PNG signature)")

    chunks: list[PngChunk] = []
    offset = 8
    total = len(data)
    while offset + 8 <= total:
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        if payload_end + 4 > total:
            raise CorruptFileError(
                f"PNG chunk {chunk_type.decode('latin-1', 'replace')} at {offset} is truncated"
            )
        chunks.append(PngChunk(chunk_type, data[payload_start:payload_end]))
        offset = payload_end + 4
        if chunk_type == b"IEND":
            break

    if not chunks or chunks[0].type != b"IHDR":
        raise CorruptFileError("PNG file has no IHDR header chunk")
    return chunks


def serialize_chunks(chunks: list[PngChunk]) -> bytes:
    return PNG_SIGNATURE + b"".join(chunk.serialize() for chunk in chunks)


def read_dimensions(chunks: list[PngChunk]) -> tuple[int | None, int | None]:
    """Extract ``(width, height)`` from the ``IHDR`` chunk."""
    for chunk in chunks:
        if chunk.type == b"IHDR" and len(chunk.data) >= 8:
            width, height = struct.unpack_from(">II", chunk.data, 0)
            return int(width), int(height)
    return None, None


def decode_text_chunk(chunk: PngChunk) -> tuple[str | None, str | None]:
    """Decode a ``tEXt``, ``zTXt`` or ``iTXt`` chunk into ``(keyword, text)``."""
    raw = chunk.data
    if chunk.type == b"tEXt":
        if b"\x00" not in raw:
            return None, None
        key_bytes, _, text_bytes = raw.partition(b"\x00")
        return key_bytes.decode("latin-1", "replace"), text_bytes.decode("latin-1", "replace")

    if chunk.type == b"zTXt":
        if b"\x00" not in raw:
            return None, None
        key_bytes, _, rest = raw.partition(b"\x00")
        if len(rest) < 1 or rest[0] != 0:
            return None, None
        try:
            decompressed = zlib.decompress(rest[1:])
        except zlib.error:
            return None, None
        return key_bytes.decode("latin-1", "replace"), decompressed.decode("latin-1", "replace")

    if chunk.type == b"iTXt":
        nul = raw.find(b"\x00")
        if nul < 0 or nul + 3 > len(raw):
            return None, None
        key = raw[:nul].decode("latin-1", "replace")
        comp_flag = raw[nul + 1]
        comp_method = raw[nul + 2]
        rest = raw[nul + 3 :]
        # Skip language tag and translated keyword (two NUL-terminated fields)
        parts = rest.split(b"\x00", 2)
        if len(parts) < 3:
            return None, None
        text_bytes = parts[2]
        if comp_flag == 1:
            if comp_method != 0:
                return None, None
            try:
                text_bytes = zlib.decompress(text_bytes)
            except zlib.error:
                return None, None
        return key, text_bytes.decode("utf-8", "replace")

    return None, None


def encode_text_chunk(key: str, value: str) -> PngChunk:
    """Build a ``tEXt`` chunk for Latin-1 strings or ``iTXt`` for UTF-8."""
    key_bytes = key.encode("latin-1", "replace")[:79]
    text_str = str(value)
    if key != "XML:com.adobe.xmp":
        try:
            text_latin1 = text_str.encode("latin-1")
            return PngChunk(b"tEXt", key_bytes + b"\x00" + text_latin1)
        except UnicodeEncodeError:
            pass
    # UTF-8 iTXt chunk (uncompressed)
    payload = key_bytes + b"\x00\x00\x00\x00\x00" + text_str.encode("utf-8")
    return PngChunk(b"iTXt", payload)


def extract_metadata(data: bytes) -> dict:
    """Return ``{"width", "height", "text": dict, "exif": bytes | None}``."""
    chunks = parse_chunks(data)
    width, height = read_dimensions(chunks)
    text: dict[str, str] = {}
    exif_blob: bytes | None = None

    for chunk in chunks:
        if chunk.type in TEXT_CHUNK_TYPES:
            key, val = decode_text_chunk(chunk)
            if key is not None and val is not None:
                text[key] = val
        elif chunk.type == b"eXIf" and chunk.data:
            payload = chunk.data
            if not payload.startswith(b"Exif\x00\x00"):
                payload = b"Exif\x00\x00" + payload
            exif_blob = payload

    return {"width": width, "height": height, "text": text, "exif": exif_blob}


def rewrite_metadata(
    data: bytes,
    text: dict[str, str],
    exif_blob: bytes | None = None,
) -> bytes:
    """Replace textual and ``eXIf`` chunks in a PNG without touching ``IDAT``."""
    chunks = parse_chunks(data)
    kept = [chunk for chunk in chunks if chunk.type not in (TEXT_CHUNK_TYPES | {b"eXIf"})]

    new_meta: list[PngChunk] = []
    if exif_blob:
        raw_exif = exif_blob[6:] if exif_blob.startswith(b"Exif\x00\x00") else exif_blob
        if raw_exif:
            new_meta.append(PngChunk(b"eXIf", raw_exif))

    for key, value in text.items():
        if value is not None:
            new_meta.append(encode_text_chunk(key, str(value)))

    # Insert metadata chunks right before the first IDAT (or before IEND).
    insert_at = len(kept)
    for index, chunk in enumerate(kept):
        if chunk.type in (b"IDAT", b"IEND"):
            insert_at = index
            break

    result = kept[:insert_at] + new_meta + kept[insert_at:]
    return serialize_chunks(result)
