"""PNG metadata: textual chunks (``tEXt``/``iTXt``/``zTXt``) plus ``eXIf``.

In 2.0G, PNG metadata is read and rewritten at the chunk level via
:mod:`mediameta.pngchunk` without re-encoding image pixels through Pillow.
``IDAT`` streams and custom ancillary chunks (``pHYs``, ``iCCP``, ``sRGB``,
``gAMA``, APNG ``acTL``/``fcTL``/``fdAT``) remain bit-for-bit untouched.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .. import pngchunk
from .. import xmp as xmp_module
from ..errors import MediaMetaError
from ..fields import format_exif_datetime, split_list
from ..record import MetadataRecord
from ..utils import MIME_TYPES, guess_format, guess_kind, staged_write
from .exif_image import ExifImageBackend, IFD_ORDER, _require_piexif

#: canonical field -> PNG text keyword
TEXT_KEYS = {
    "title": "Title",
    "artist": "Author",
    "description": "Description",
    "copyright": "Copyright",
    "software": "Software",
    "comment": "Comment",
    "datetime_original": "Creation Time",
    "keywords": "Keywords",
}
REVERSE_TEXT_KEYS = {value.lower(): key for key, value in TEXT_KEYS.items()}

#: Chunks that are part of the image or handled separately, not plain text tags.
SKIP_INFO_KEYS = {
    "exif", "icc_profile", "dpi", "gamma", "transparency", "aspect",
    "srgb", "chromaticity", "interlace", "compression", "XML:com.adobe.xmp",
}


class PngBackend(ExifImageBackend):
    """Same canonical fields as EXIF, stored losslessly in PNG chunks."""

    name = "png"
    priority = 50
    read_suffixes = frozenset({".png"})
    write_suffixes = frozenset({".png"})

    def is_available(self) -> tuple:
        return super().is_available()

    # -- reading ------------------------------------------------------

    def read(self, path) -> MetadataRecord:
        _require_piexif()
        path = Path(path)
        record = MetadataRecord(
            path=path,
            kind=guess_kind(path),
            format=guess_format(path),
            backend=self.name,
            writable=True,
        )

        parsed = pngchunk.extract_metadata(path.read_bytes())
        if parsed["width"] and parsed["height"]:
            record.common["width"] = parsed["width"]
            record.common["height"] = parsed["height"]

        info = parsed["text"]
        exif = self._exif_from_blob(parsed["exif"], record)
        for ifd in IFD_ORDER:
            for tag, value in (exif.get(ifd) or {}).items():
                record.raw[f"EXIF:{self._tag_name(ifd, tag)}"] = self._raw_value(ifd, tag, value)
        self._fill_common(record, exif)

        # Text chunks win: they are what the user sees in file browsers.
        for key, value in info.items():
            if key in SKIP_INFO_KEYS or not isinstance(value, str):
                continue
            record.raw[f"PNG:{key}"] = value
            field_name = REVERSE_TEXT_KEYS.get(key.lower())
            if field_name:
                record.set_common(field_name, self._decode_text_field(field_name, value))

        packet = info.get("XML:com.adobe.xmp") or self._xmp_packet(path)
        properties = xmp_module.parse(packet)
        for key, value in properties.items():
            record.raw[f"XMP:{key}"] = value
        for field_name, value in xmp_module.to_common(properties).items():
            record.common.setdefault(field_name, self._decode_text_field(field_name, value))

        mime = MIME_TYPES.get(record.format)
        if mime:
            record.common["mime_type"] = mime
        return record

    def _exif_from_blob(self, blob: bytes | None, record: MetadataRecord) -> dict:
        if not blob:
            return {ifd: {} for ifd in IFD_ORDER}
        try:
            import piexif

            return piexif.load(blob)
        except Exception as exc:
            record.warnings.append(f"the eXIf chunk could not be parsed ({exc})")
            return {ifd: {} for ifd in IFD_ORDER}

    def _decode_text_field(self, field_name: str, value):
        if field_name == "keywords":
            return split_list(str(value).replace(";", ","))
        if field_name == "datetime_original":
            try:
                return self._normalize(field_name, value)
            except Exception:
                return value
        if field_name == "rating":
            try:
                return int(value)
            except (TypeError, ValueError):
                return value
        return value

    # -- writing ------------------------------------------------------

    def write(self, path, changes: dict, raw_changes: dict | None = None, output=None) -> MetadataRecord:
        _require_piexif()
        path = Path(path)
        target = self._target(path, output)
        changes = dict(changes or {})
        raw_changes = dict(raw_changes or {})

        raw_bytes = path.read_bytes()
        parsed = pngchunk.extract_metadata(raw_bytes)
        info = dict(parsed["text"])
        record = MetadataRecord(path=path, backend=self.name)
        exif = self._exif_from_blob(parsed["exif"], record)

        exif_changes = {
            name: value for name, value in changes.items()
            if name not in TEXT_KEYS or name in ("datetime_original",)
        }
        self._apply_changes(exif, exif_changes)

        raw_xmp = {}
        for key, value in raw_changes.items():
            namespace, _, name = key.partition(":")
            if namespace.upper() == "PNG" and name:
                if value is None:
                    info.pop(name, None)
                else:
                    info[name] = str(value)
            elif namespace.upper() == "XMP" and name:
                raw_xmp[key] = value
            else:
                self._apply_raw(exif, key, value)

        text = self._text_chunks(info, changes)

        # Keep embedded XMP packet in sync if present or requested.
        existing_xmp = info.get("XML:com.adobe.xmp")
        if existing_xmp is not None or raw_xmp:
            updated_xmp = xmp_module.update_packet(
                existing_xmp, changes, raw_xmp, create=bool(raw_xmp)
            )
            if updated_xmp:
                text["XML:com.adobe.xmp"] = updated_xmp.decode("utf-8")

        self._save_chunks(raw_bytes, target, text, exif)
        return self.read(target)

    def clear(self, path, output=None) -> MetadataRecord:
        _require_piexif()
        path = Path(path)
        target = self._target(path, output)
        raw_bytes = path.read_bytes()
        self._save_chunks(raw_bytes, target, {}, None)
        return self.read(target)

    def _text_chunks(self, info: dict, changes: dict) -> dict:
        """Start from the chunks already in the file, then apply the changes."""
        text = {
            key: value
            for key, value in info.items()
            if isinstance(value, str) and key not in SKIP_INFO_KEYS
        }
        for field_name, value in changes.items():
            key = TEXT_KEYS.get(field_name)
            if key is None:
                continue
            # Remove case-insensitive duplicates of the same PNG keyword
            for existing_key in list(text):
                if existing_key.lower() == key.lower() and existing_key != key:
                    text.pop(existing_key, None)
            if value is None:
                text.pop(key, None)
            elif field_name == "keywords":
                items = split_list(value)
                if items:
                    text[key] = "; ".join(items)
                else:
                    text.pop(key, None)
            elif isinstance(value, _dt.datetime):
                text[key] = format_exif_datetime(value)
            else:
                text[key] = str(value)
        return text

    def _save_chunks(self, raw_bytes: bytes, target: Path, text: dict, exif) -> None:
        exif_blob = None
        if exif is not None and any(exif.get(ifd) for ifd in IFD_ORDER):
            exif_blob = self._dump(exif)

        try:
            rebuilt = pngchunk.rewrite_metadata(raw_bytes, text, exif_blob)
            with staged_write(target) as temporary:
                temporary.write_bytes(rebuilt)
        except Exception as exc:
            raise MediaMetaError(f"could not write {Path(target).name}: {exc}") from exc
