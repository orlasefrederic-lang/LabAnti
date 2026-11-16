"""The canonical field vocabulary shared by every backend.

Each backend maps these names onto its own tags (EXIF numbers, QuickTime
atoms, PNG text keywords, ID3/Vorbis frames, ExifTool tag names), so ``title``
means the same thing whether the file is a JPEG, PNG, MP3, FLAC or MP4.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field as _dc_field
from fractions import Fraction

from .errors import FieldError
from .gps import parse_coordinate

TEXT = "text"
TEXT_LIST = "text_list"
DATETIME = "datetime"
INT = "int"
FLOAT = "float"
COORD_LAT = "lat"
COORD_LON = "lon"


@dataclass(frozen=True)
class FieldSpec:
    """Describes one canonical field."""

    name: str
    type: str
    help: str
    media: tuple = ("image", "video", "audio")
    writable: bool = True
    choices: tuple = ()
    minimum: float | None = None
    maximum: float | None = None
    aliases: tuple = _dc_field(default=())

    def applies_to(self, kind: str) -> bool:
        return kind in self.media or kind == "unknown"


COMMON_FIELDS: tuple = (
    FieldSpec("title", TEXT, "Short name of the work"),
    FieldSpec("description", TEXT, "Long caption or description", aliases=("caption",)),
    FieldSpec("comment", TEXT, "Free-form user comment"),
    FieldSpec("artist", TEXT, "Author, photographer or creator", aliases=("author", "creator")),
    FieldSpec("copyright", TEXT, "Copyright notice", aliases=("rights",)),
    FieldSpec("keywords", TEXT_LIST, "Comma-separated keywords or tags", aliases=("tags",)),
    FieldSpec("software", TEXT, "Program that produced the file", aliases=("encoder",)),
    FieldSpec(
        "datetime_original",
        DATETIME,
        "Moment the photo or video was captured",
        aliases=("date", "datetime", "taken"),
    ),
    FieldSpec("camera_make", TEXT, "Camera manufacturer", aliases=("make",)),
    FieldSpec("camera_model", TEXT, "Camera model", aliases=("model",)),
    FieldSpec("lens_make", TEXT, "Lens manufacturer", media=("image",), aliases=("lens_manufacturer",)),
    FieldSpec("lens", TEXT, "Lens model", media=("image",)),
    FieldSpec("serial_number", TEXT, "Camera or device serial number", media=("image", "video"), aliases=("serial", "body_serial")),
    FieldSpec(
        "iso",
        INT,
        "ISO speed rating",
        media=("image",),
        minimum=1,
        maximum=1024000,
        aliases=("iso_speed", "isospeedratings"),
    ),
    FieldSpec(
        "aperture",
        FLOAT,
        "Lens f-number (e.g. 2.8)",
        media=("image",),
        minimum=0.5,
        maximum=128.0,
        aliases=("f_number", "fnumber", "fstop", "f_stop"),
    ),
    FieldSpec(
        "shutter_speed",
        FLOAT,
        "Exposure time in seconds (e.g. 1/250 or 0.004)",
        media=("image",),
        minimum=0.000001,
        maximum=36000.0,
        aliases=("exposure_time", "exposure"),
    ),
    FieldSpec(
        "focal_length",
        FLOAT,
        "Focal length in millimetres",
        media=("image",),
        minimum=0.1,
        maximum=10000.0,
        aliases=("focal", "fl"),
    ),
    FieldSpec(
        "focal_length_35mm",
        INT,
        "35mm equivalent focal length in mm",
        media=("image",),
        minimum=1,
        maximum=10000,
        aliases=("focal_35mm", "fl35"),
    ),
    FieldSpec(
        "flash",
        INT,
        "EXIF flash status code (0 = did not fire)",
        media=("image",),
        minimum=0,
        maximum=255,
    ),
    FieldSpec(
        "orientation",
        INT,
        "EXIF orientation, 1-8",
        media=("image",),
        minimum=1,
        maximum=8,
    ),
    FieldSpec(
        "rotation",
        INT,
        "Video rotation in degrees (0, 90, 180, 270)",
        media=("video",),
        choices=(0, 90, 180, 270),
        aliases=("rotate", "video_rotation"),
    ),
    FieldSpec("rating", INT, "Rating from 0 to 5", minimum=0, maximum=5),
    FieldSpec("gps_latitude", COORD_LAT, "Latitude in degrees or DMS", aliases=("lat",)),
    FieldSpec("gps_longitude", COORD_LON, "Longitude in degrees or DMS", aliases=("lon", "lng")),
    FieldSpec("gps_altitude", FLOAT, "Altitude in metres", aliases=("alt",)),
    FieldSpec("album", TEXT, "Album or collection", media=("video", "audio")),
    FieldSpec("album_artist", TEXT, "Album or collection artist", media=("video", "audio"), aliases=("albumartist", "band")),
    FieldSpec("composer", TEXT, "Composer or songwriter", media=("video", "audio"), aliases=("writer", "songwriter")),
    FieldSpec("genre", TEXT, "Genre", media=("video", "audio")),
    FieldSpec("track_number", INT, "Track number", media=("video", "audio"), minimum=1, maximum=9999, aliases=("track", "trkn")),
    FieldSpec("disc_number", INT, "Disc number", media=("video", "audio"), minimum=1, maximum=999, aliases=("disc", "disk")),
    FieldSpec("bpm", INT, "Tempo in beats per minute", media=("video", "audio"), minimum=1, maximum=999, aliases=("tempo",)),
    FieldSpec("lyrics", TEXT, "Lyrics or transcript", media=("video", "audio")),
    FieldSpec("duration", FLOAT, "Length in seconds (read-only)", media=("video", "audio"), writable=False),
    FieldSpec("fps", FLOAT, "Frame rate in frames/s (read-only)", media=("video",), writable=False, aliases=("frame_rate", "framerate")),
    FieldSpec("width", INT, "Pixel width (read-only)", writable=False),
    FieldSpec("height", INT, "Pixel height (read-only)", writable=False),
    FieldSpec("mime_type", TEXT, "Detected media type (read-only)", writable=False),
)

#: Fields removed by ``--privacy`` sanitization while preserving orientation and creative title/rights.
PRIVACY_FIELDS: tuple = (
    "gps_latitude",
    "gps_longitude",
    "gps_altitude",
    "datetime_original",
    "camera_make",
    "camera_model",
    "lens_make",
    "lens",
    "serial_number",
    "artist",
    "comment",
    "software",
)

FIELDS_BY_NAME = {spec.name: spec for spec in COMMON_FIELDS}

_ALIASES = {}
for _spec in COMMON_FIELDS:
    for _alias in _spec.aliases:
        _ALIASES[_alias] = _spec.name

#: ``gps`` is a shorthand that expands into latitude/longitude/altitude.
GPS_SHORTHAND = "gps"


def resolve_field(name: str) -> FieldSpec:
    """Look a field up by canonical name or alias."""
    key = str(name).strip().lower().replace("-", "_")
    if key in FIELDS_BY_NAME:
        return FIELDS_BY_NAME[key]
    if key in _ALIASES:
        return FIELDS_BY_NAME[_ALIASES[key]]
    raise FieldError(
        f"unknown field {name!r}; run `mediameta fields` to see the supported ones"
    )


_DATE_FORMATS = (
    "%Y:%m:%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y:%m:%d %H:%M",
    "%Y-%m-%d %H:%M",
    "%Y:%m:%d",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y",
    "%Y",
)

_TIMEDELTA_TOKEN_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)
_COLON_DELTA_RE = re.compile(
    r"^(?P<sign>[+-])?(?P<h>\d{1,4}):(?P<m>\d{1,2})(?::(?P<s>\d{1,2}))?$"
)


def parse_datetime(value) -> _dt.datetime:
    """Accept the many date spellings found in metadata and on command lines."""
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)

    text = str(value).strip().strip("\x00").strip()
    if not text:
        raise FieldError("empty date")
    if text.lower() == "now":
        return _dt.datetime.now()

    # Most files use naive local time; keep an explicit offset when present.
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass

    normalized = text.replace("Z", "+0000")
    for fmt in _DATE_FORMATS:
        for suffix in ("", "%z"):
            try:
                return _dt.datetime.strptime(normalized, fmt + suffix)
            except ValueError:
                continue
    raise FieldError(f"cannot parse date {value!r}")


def parse_timedelta(value) -> _dt.timedelta:
    """Parse a time shift such as ``+2h30m``, ``-1d``, ``+03:00`` or seconds."""
    if isinstance(value, _dt.timedelta):
        return value
    if isinstance(value, (int, float)):
        return _dt.timedelta(seconds=float(value))

    text = str(value).strip()
    if not text:
        raise FieldError("empty time offset")

    # Plain numeric seconds (e.g. "3600" or "-1800")
    try:
        return _dt.timedelta(seconds=float(text))
    except ValueError:
        pass

    # Colon format: +02:30 or -01:15:30
    colon_match = re.match(r"^(?P<sign>[+-])?(?P<h>\d{1,4}):(?P<m>\d{1,2})(?::(?P<s>\d{1,2}))?$", text)
    if colon_match:
        sign = -1 if colon_match.group("sign") == "-" else 1
        hours = int(colon_match.group("h"))
        minutes = int(colon_match.group("m"))
        seconds = int(colon_match.group("s") or 0)
        return sign * _dt.timedelta(hours=hours, minutes=minutes, seconds=seconds)

    # Token format: +1d 2h 30m, -2h30m, etc.
    global_sign = -1 if text.startswith("-") else 1
    body = text.lstrip("+-").strip()
    matches = list(_TIMEDELTA_TOKEN_RE.finditer(body))
    if not matches:
        raise FieldError(
            f"cannot parse time offset {value!r}; examples: '+2h', '-1d 3h 30m', '+03:00'"
        )
    # Ensure the entire string was consumed by valid tokens
    cleaned = _TIMEDELTA_TOKEN_RE.sub("", body).strip()
    if cleaned:
        raise FieldError(f"cannot parse time offset {value!r}")

    total_seconds = 0.0
    for match in matches:
        amount = float(match.group(1))
        unit = match.group(2).lower()[0]
        if unit == "d":
            total_seconds += amount * 86400
        elif unit == "h":
            total_seconds += amount * 3600
        elif unit == "m":
            total_seconds += amount * 60
        elif unit == "s":
            total_seconds += amount
    return _dt.timedelta(seconds=global_sign * total_seconds)


def format_exif_datetime(value: _dt.datetime) -> str:
    return value.strftime("%Y:%m:%d %H:%M:%S")


def split_list(value) -> list:
    """Turn ``"a, b; c"`` or a sequence into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    else:
        text = str(value).replace(";", ",")
        items = [item.strip() for item in text.split(",")]
    return [item for item in items if item]


def _parse_float_or_fraction(value) -> float:
    """Accept floats, rationals like ``1/250`` or strings with units like ``50 mm``."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (tuple, list)) and len(value) == 2:
        num, den = value
        return float(num) / float(den or 1)
    text = str(value).strip()
    # Strip common trailing units ("1/250 s", "50 mm", "f/2.8")
    if text.lower().startswith("f/") and len(text) > 2:
        text = text[2:].strip()
    text = re.sub(r"\s*(?:mm|sec|s)$", "", text, flags=re.IGNORECASE).strip()
    if "/" in text:
        return float(Fraction(text))
    return float(text)


def normalize(spec: FieldSpec, value):
    """Coerce a user-supplied value into the canonical Python type."""
    if value is None:
        return None

    try:
        if spec.type == TEXT:
            text = value if isinstance(value, str) else str(value)
            return text.strip("\x00")
        if spec.type == TEXT_LIST:
            return split_list(value)
        if spec.type == DATETIME:
            return parse_datetime(value)
        if spec.type == INT:
            if isinstance(value, bool):
                raise ValueError("expected an integer, got boolean")
            if isinstance(value, int):
                number = value
            else:
                raw_str = str(value).strip()
                # Handle track numbers like "3/12" -> 3
                if "/" in raw_str and spec.name in ("track_number", "disc_number"):
                    raw_str = raw_str.partition("/")[0].strip()
                number = int(raw_str)
            _check_range(spec, number)
            return number
        if spec.type == FLOAT:
            number = _parse_float_or_fraction(value)
            _check_range(spec, number)
            return number
        if spec.type == COORD_LAT:
            return parse_coordinate(value, "lat")
        if spec.type == COORD_LON:
            return parse_coordinate(value, "lon")
    except FieldError:
        raise
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise FieldError(f"invalid value for {spec.name}: {value!r} ({exc})") from exc

    return value


def _check_range(spec: FieldSpec, number) -> None:
    if spec.choices and number not in spec.choices:
        allowed = ", ".join(str(c) for c in spec.choices)
        raise FieldError(f"{spec.name} must be one of ({allowed}), got {number}")
    if spec.minimum is not None and number < spec.minimum:
        raise FieldError(f"{spec.name} must be >= {spec.minimum}, got {number}")
    if spec.maximum is not None and number > spec.maximum:
        raise FieldError(f"{spec.name} must be <= {spec.maximum}, got {number}")


def parse_assignments(pairs) -> dict:
    """Parse ``name=value`` strings from the command line into canonical fields.

    An empty value means "delete this field". ``gps=lat,lon[,alt]`` expands
    into the three separate coordinate fields.
    """
    changes: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise FieldError(f"expected NAME=VALUE, got {pair!r}")
        name, _, raw = pair.partition("=")
        name = name.strip().lower().replace("-", "_")
        raw = raw.strip()

        if name == GPS_SHORTHAND:
            changes.update(_expand_gps(raw))
            continue

        spec = resolve_field(name)
        if not spec.writable:
            raise FieldError(f"{spec.name} is read-only")
        changes[spec.name] = None if raw == "" else normalize(spec, raw)
    return changes


def _expand_gps(raw: str) -> dict:
    if raw == "":
        return {"gps_latitude": None, "gps_longitude": None, "gps_altitude": None}

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) not in (2, 3):
        raise FieldError("gps expects 'lat,lon' or 'lat,lon,altitude'")

    changes = {
        "gps_latitude": parse_coordinate(parts[0], "lat"),
        "gps_longitude": parse_coordinate(parts[1], "lon"),
    }
    if len(parts) == 3:
        changes["gps_altitude"] = float(parts[2])
    return changes
