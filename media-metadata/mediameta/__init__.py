"""mediameta 2.0G - read and edit metadata of photos, videos and audio.

    from mediameta import read_metadata, write_metadata

    record = read_metadata("IMG_0001.jpg")
    print(record.common["datetime_original"])

    write_metadata("IMG_0001.jpg", {"title": "Sunset", "gps_latitude": 55.75})
"""

from __future__ import annotations

from .api import (
    copy_metadata,
    diff_metadata,
    export_metadata,
    import_metadata,
    read_metadata,
    remove_metadata,
    rename_by_metadata,
    shift_datetime,
    write_metadata,
)
from .backends import available_backends, backend_status, select_backend
from .errors import (
    BackendUnavailableError,
    CorruptFileError,
    FieldError,
    MediaMetaError,
    ReadOnlyFormatError,
    UnsupportedFormatError,
)
from .fields import COMMON_FIELDS, FIELDS_BY_NAME, PRIVACY_FIELDS, FieldSpec
from .record import MetadataRecord, describe_fields

__version__ = "2.0G"

__all__ = [
    "read_metadata",
    "write_metadata",
    "remove_metadata",
    "copy_metadata",
    "diff_metadata",
    "shift_datetime",
    "rename_by_metadata",
    "export_metadata",
    "import_metadata",
    "MetadataRecord",
    "describe_fields",
    "COMMON_FIELDS",
    "FIELDS_BY_NAME",
    "PRIVACY_FIELDS",
    "FieldSpec",
    "available_backends",
    "backend_status",
    "select_backend",
    "MediaMetaError",
    "UnsupportedFormatError",
    "ReadOnlyFormatError",
    "BackendUnavailableError",
    "FieldError",
    "CorruptFileError",
    "__version__",
]
