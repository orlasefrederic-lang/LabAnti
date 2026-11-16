"""Native audio metadata backend for MP3, FLAC, OGG, Opus, WAV and AIFF.

In 1.0, audio formats other than M4A were delegated to ExifTool — which
cannot actually write MP3, FLAC, OGG or WAV files (ExifTool treats them as
read-only). In 2.0G, :class:`AudioBackend` reads, writes and strips ID3v2
frames (MP3, WAV, AIFF) and Vorbis comments (FLAC, OGG, Opus) directly.
"""

from __future__ import annotations

import datetime as _dt
import shutil
from pathlib import Path

from ..errors import BackendUnavailableError, CorruptFileError, FieldError, MediaMetaError
from ..fields import parse_datetime, split_list
from ..record import MetadataRecord
from ..utils import MIME_TYPES, effective_suffix, guess_format, guess_kind, staged_write
from .base import Backend

try:
    import mutagen
    from mutagen import id3 as _id3

    _MUTAGEN_ERROR = ""
except ImportError as exc:  # pragma: no cover
    mutagen = None
    _id3 = None
    _MUTAGEN_ERROR = str(exc)

VORBIS_SUFFIXES = frozenset({".flac", ".ogg", ".opus"})
ID3_SUFFIXES = frozenset({".mp3", ".wav", ".aiff"})

#: Canonical field -> Vorbis comment key (uppercase by convention).
VORBIS_KEYS = {
    "title": "TITLE",
    "artist": "ARTIST",
    "album": "ALBUM",
    "album_artist": "ALBUMARTIST",
    "composer": "COMPOSER",
    "genre": "GENRE",
    "copyright": "COPYRIGHT",
    "software": "ENCODER",
    "description": "DESCRIPTION",
    "comment": "COMMENT",
    "lyrics": "LYRICS",
    "keywords": "KEYWORDS",
    "datetime_original": "DATE",
    "track_number": "TRACKNUMBER",
    "disc_number": "DISCNUMBER",
    "bpm": "BPM",
    "rating": "RATING",
}
REVERSE_VORBIS_KEYS = {value.lower(): key for key, value in VORBIS_KEYS.items()}
REVERSE_VORBIS_KEYS.update({
    "author": "artist",
    "organization": "album_artist",
    "track": "track_number",
    "disc": "disc_number",
    "tempo": "bpm",
})

#: Canonical field -> simple ID3v2 text frame ID.
ID3_TEXT_FRAMES = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "album_artist": "TPE2",
    "composer": "TCOM",
    "genre": "TCON",
    "copyright": "TCOP",
    "software": "TSSE",
    "description": "TIT3",
    "track_number": "TRCK",
    "disc_number": "TPOS",
    "bpm": "TBPM",
}


def _require_mutagen():
    if mutagen is None:
        raise BackendUnavailableError(
            "the audio backend needs mutagen: pip install mutagen"
            + (f" ({_MUTAGEN_ERROR})" if _MUTAGEN_ERROR else "")
        )


def _format_audio_date(value) -> str:
    if isinstance(value, _dt.datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _rating_to_popm(rating: int) -> int:
    """Map 0..5 star rating to ID3v2 POPM 0..255 byte."""
    mapping = {0: 0, 1: 1, 2: 64, 3: 128, 4: 196, 5: 255}
    return mapping.get(int(rating), max(0, min(255, int(round(rating * 51)))))


def _popm_to_rating(raw: int) -> int:
    if raw <= 0:
        return 0
    if raw < 32:
        return 1
    if raw < 96:
        return 2
    if raw < 160:
        return 3
    if raw < 224:
        return 4
    return 5


class AudioBackend(Backend):
    """Reads and writes MP3, FLAC, OGG, Opus, WAV and AIFF via ``mutagen``."""

    name = "audio"
    priority = 50
    read_suffixes = VORBIS_SUFFIXES | ID3_SUFFIXES
    write_suffixes = read_suffixes

    def is_available(self) -> tuple:
        if mutagen is None:
            return False, "mutagen is not installed (pip install mutagen)"
        return True, ""

    # -- reading ------------------------------------------------------

    def read(self, path) -> MetadataRecord:
        _require_mutagen()
        path = Path(path)
        suffix = effective_suffix(path)
        audio = self._open(path, suffix)

        record = MetadataRecord(
            path=path,
            kind=guess_kind(path),
            format=guess_format(path),
            backend=self.name,
            writable=True,
        )

        if getattr(audio, "info", None) is not None:
            length = getattr(audio.info, "length", None)
            if length is not None:
                record.common["duration"] = round(float(length), 3)

        if suffix in VORBIS_SUFFIXES:
            self._read_vorbis(audio, record)
        else:
            self._read_id3(audio, record)

        mime = MIME_TYPES.get(record.format)
        if mime:
            record.common["mime_type"] = mime
        return record

    def _open(self, path: Path, suffix: str):
        try:
            if suffix == ".mp3":
                from mutagen.mp3 import MP3
                return MP3(path)
            if suffix == ".flac":
                from mutagen.flac import FLAC
                return FLAC(path)
            if suffix == ".ogg":
                from mutagen.oggvorbis import OggVorbis
                return OggVorbis(path)
            if suffix == ".opus":
                from mutagen.oggopus import OggOpus
                return OggOpus(path)
            if suffix == ".wav":
                from mutagen.wave import WAVE
                return WAVE(path)
            if suffix == ".aiff":
                from mutagen.aiff import AIFF
                return AIFF(path)
            loaded = mutagen.File(path)
            if loaded is None:
                raise CorruptFileError(f"{path.name} could not be parsed as an audio file")
            return loaded
        except MediaMetaError:
            raise
        except Exception as exc:
            raise CorruptFileError(f"could not read audio container {path.name}: {exc}") from exc

    def _read_vorbis(self, audio, record: MetadataRecord) -> None:
        tags = audio.tags or {}
        for key, values in tags.items():
            key_str = str(key)
            val_list = [str(v) for v in values] if isinstance(values, (list, tuple)) else [str(values)]
            if not val_list:
                continue
            record.raw[f"Vorbis:{key_str.upper()}"] = val_list[0] if len(val_list) == 1 else val_list
            field_name = REVERSE_VORBIS_KEYS.get(key_str.lower())
            if field_name and field_name not in record.common:
                record.set_common(field_name, self._coerce_field(field_name, val_list))

    def _read_id3(self, audio, record: MetadataRecord) -> None:
        tags = audio.tags
        if not tags:
            return

        for key, frame in tags.items():
            frame_id = key.split(":")[0]
            if hasattr(frame, "text") and frame.text:
                values = [str(item) for item in frame.text if str(item)]
                if values:
                    record.raw[f"ID3:{key}"] = values[0] if len(values) == 1 else values
            elif frame_id == "POPM":
                record.raw[f"ID3:{frame_id}"] = getattr(frame, "rating", 0)

        for field_name, frame_id in ID3_TEXT_FRAMES.items():
            frame = tags.get(frame_id)
            if frame and getattr(frame, "text", None):
                text_val = str(frame.text[0]).strip()
                if text_val:
                    record.set_common(field_name, self._coerce_field(field_name, [text_val]))

        # Date frames (TDRC in ID3v2.4, TYER in ID3v2.3)
        for date_id in ("TDRC", "TDOR", "TYER"):
            frame = tags.get(date_id)
            if frame and getattr(frame, "text", None):
                text_val = str(frame.text[0]).strip()
                if text_val:
                    record.set_common("datetime_original", self._coerce_field("datetime_original", [text_val]))
                    break

        # Comments (COMM)
        for key, frame in tags.items():
            if key.startswith("COMM") and getattr(frame, "text", None):
                text_val = str(frame.text[0]).strip()
                if text_val:
                    record.set_common("comment", text_val)
                    break

        # Lyrics (USLT)
        for key, frame in tags.items():
            if key.startswith("USLT") and getattr(frame, "text", None):
                text_val = str(frame.text).strip()
                if text_val:
                    record.set_common("lyrics", text_val)
                    break

        # Keywords (TXXX:KEYWORDS or TXXX:keywords)
        for key, frame in tags.items():
            if key.upper() == "TXXX:KEYWORDS" and getattr(frame, "text", None):
                items = []
                for item in frame.text:
                    items.extend(split_list(str(item)))
                if items:
                    record.set_common("keywords", items)
                break

        # Rating (POPM)
        for key, frame in tags.items():
            if key.startswith("POPM") and hasattr(frame, "rating"):
                record.set_common("rating", _popm_to_rating(int(frame.rating)))
                break

    @staticmethod
    def _coerce_field(field_name: str, values: list[str]):
        first = values[0] if values else ""
        if field_name == "keywords":
            items = []
            for v in values:
                items.extend(split_list(v))
            return items
        if field_name in ("track_number", "disc_number"):
            part = first.partition("/")[0].strip()
            try:
                return int(part)
            except ValueError:
                return first
        if field_name in ("bpm", "rating"):
            try:
                val = int(float(first))
                if field_name == "rating" and val > 5:
                    val = max(0, min(5, round(val / 20)))
                return val
            except ValueError:
                return first
        if field_name == "datetime_original":
            try:
                return parse_datetime(first)
            except FieldError:
                return first
        return first

    # -- writing ------------------------------------------------------

    def write(self, path, changes: dict, raw_changes: dict | None = None, output=None) -> MetadataRecord:
        _require_mutagen()
        path = Path(path)
        target = self._target(path, output)
        suffix = effective_suffix(path)

        with staged_write(target) as temporary:
            shutil.copyfile(path, temporary)
            audio = self._open(temporary, suffix)
            if audio.tags is None:
                audio.add_tags()

            if suffix in VORBIS_SUFFIXES:
                self._write_vorbis(audio, changes or {}, raw_changes or {})
            else:
                self._write_id3(audio, changes or {}, raw_changes or {})

            try:
                audio.save()
            except Exception as exc:
                raise MediaMetaError(f"could not save audio tags to {target.name}: {exc}") from exc

        return self.read(target)

    def clear(self, path, output=None) -> MetadataRecord:
        _require_mutagen()
        path = Path(path)
        target = self._target(path, output)
        suffix = effective_suffix(path)

        with staged_write(target) as temporary:
            shutil.copyfile(path, temporary)
            audio = self._open(temporary, suffix)
            if audio.tags is not None:
                audio.delete()
                audio.tags.clear()
                audio.save()

        return self.read(target)

    def _write_vorbis(self, audio, changes: dict, raw_changes: dict) -> None:
        tags = audio.tags
        for field_name, value in changes.items():
            key = VORBIS_KEYS.get(field_name)
            if key is None:
                raise FieldError(f"the {self.name} backend cannot write {field_name}")
            # Remove case-insensitive existing keys
            for existing in list(tags.keys()):
                if existing.upper() == key:
                    tags.pop(existing, None)
            if value is None:
                continue
            if field_name == "keywords":
                items = split_list(value)
                if items:
                    tags[key] = items
            elif field_name == "datetime_original":
                tags[key] = [_format_audio_date(value)]
            else:
                tags[key] = [str(value)]

        for raw_key, value in raw_changes.items():
            ns, _, name = raw_key.partition(":")
            if not name:
                ns, name = "vorbis", raw_key
            if ns.lower() != "vorbis":
                raise FieldError(f"expected Vorbis:KEY for {audio.filename}, got {raw_key!r}")
            for existing in list(tags.keys()):
                if existing.upper() == name.upper():
                    tags.pop(existing, None)
            if value is not None:
                tags[name.upper()] = [str(value)]

    def _write_id3(self, audio, changes: dict, raw_changes: dict) -> None:
        tags = audio.tags
        for field_name, value in changes.items():
            if field_name in ID3_TEXT_FRAMES:
                frame_id = ID3_TEXT_FRAMES[field_name]
                tags.delall(frame_id)
                if value is not None:
                    cls = getattr(_id3, frame_id)
                    tags.add(cls(encoding=3, text=[str(value)]))
            elif field_name == "datetime_original":
                for date_id in ("TDRC", "TDOR", "TYER"):
                    tags.delall(date_id)
                if value is not None:
                    tags.add(_id3.TDRC(encoding=3, text=[_format_audio_date(value)]))
            elif field_name == "comment":
                tags.delall("COMM")
                if value is not None:
                    tags.add(_id3.COMM(encoding=3, lang="eng", desc="", text=[str(value)]))
            elif field_name == "lyrics":
                tags.delall("USLT")
                if value is not None:
                    tags.add(_id3.USLT(encoding=3, lang="eng", desc="", text=str(value)))
            elif field_name == "keywords":
                tags.delall("TXXX:KEYWORDS")
                tags.delall("TXXX:keywords")
                if value is not None:
                    items = split_list(value)
                    if items:
                        tags.add(_id3.TXXX(encoding=3, desc="KEYWORDS", text=items))
            elif field_name == "rating":
                tags.delall("POPM")
                if value is not None:
                    tags.add(_id3.POPM(email="mediameta", rating=_rating_to_popm(int(value)), count=0))
            else:
                raise FieldError(f"the {self.name} backend cannot write {field_name}")

        for raw_key, value in raw_changes.items():
            ns, _, name = raw_key.partition(":")
            if not name:
                ns, name = "id3", raw_key
            if ns.lower() != "id3":
                raise FieldError(f"expected ID3:FRAME for this format, got {raw_key!r}")
            tags.delall(name)
            if value is not None:
                cls = getattr(_id3, name, None)
                if cls is None or not issubclass(cls, _id3.TextFrame):
                    raise FieldError(f"unsupported raw ID3 frame {name!r}")
                tags.add(cls(encoding=3, text=[str(value)]))
