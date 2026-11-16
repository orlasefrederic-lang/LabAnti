"""The library API: read, write, remove, copy, diff, shift, rename and batch export/import."""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import re
import string
from pathlib import Path

from .backends import select_backend
from .errors import FieldError, MediaMetaError
from .fields import (
    COMMON_FIELDS,
    FIELDS_BY_NAME,
    PRIVACY_FIELDS,
    normalize,
    parse_timedelta,
    resolve_field,
)
from .record import MetadataRecord, _jsonable
from .utils import iter_media_files, make_backup, staged_write

#: Fields that describe the media stream rather than user metadata; never copied.
TECHNICAL_FIELDS = {"width", "height", "duration", "fps", "mime_type"}


def read_metadata(path, backend: str = "auto") -> MetadataRecord:
    """Read everything known about one file."""
    path = Path(path)
    if not path.exists():
        raise MediaMetaError(f"{path} does not exist")
    if path.is_dir():
        raise MediaMetaError(f"{path} is a directory")
    return select_backend(path, backend).read(path)


def write_metadata(
    path,
    changes: dict | None = None,
    raw: dict | None = None,
    backend: str = "auto",
    output=None,
    backup: bool = False,
) -> MetadataRecord:
    """Apply canonical ``changes`` (``None`` as a value deletes the field).

    ``raw`` passes backend-specific tags straight through, for example
    ``{"EXIF:ISOSpeedRatings": 400}`` or ``{"iTunes:\\xa9wrt": "K"}``.
    """
    path = Path(path)
    if not path.exists():
        raise MediaMetaError(f"{path} does not exist")
    if not changes and not raw:
        raise FieldError("nothing to write: no fields given")

    normalized = _normalize_changes(changes or {})
    chosen = select_backend(path, backend, for_write=True)

    if backup and output is None:
        make_backup(path)
    return chosen.write(path, normalized, raw or {}, output=output)


def remove_metadata(
    path,
    fields=None,
    backend: str = "auto",
    output=None,
    backup: bool = False,
    privacy: bool = False,
) -> MetadataRecord:
    """Delete ``fields``, privacy-sensitive fields, or every metadata block."""
    path = Path(path)
    if not path.exists():
        raise MediaMetaError(f"{path} does not exist")

    chosen = select_backend(path, backend, for_write=True)
    if backup and output is None:
        make_backup(path)

    if privacy:
        current = chosen.read(path)
        target_fields = list(PRIVACY_FIELDS)
        if fields:
            for name in fields:
                spec = resolve_field(name)
                if not spec.writable:
                    raise FieldError(f"{spec.name} is read-only")
                if spec.name not in target_fields:
                    target_fields.append(spec.name)
        changes = {
            name: None
            for name in target_fields
            if name in current.common
        }
        if not changes:
            if output is not None and Path(output) != path:
                import shutil
                with staged_write(output) as temporary:
                    shutil.copyfile(path, temporary)
                return chosen.read(output)
            return current
        return chosen.write(path, changes, {}, output=output)

    if not fields:
        return chosen.clear(path, output=output)

    changes = {}
    for name in fields:
        spec = resolve_field(name)
        if not spec.writable:
            raise FieldError(f"{spec.name} is read-only")
        changes[spec.name] = None
    return chosen.write(path, changes, {}, output=output)


def copy_metadata(
    source,
    destination,
    fields=None,
    backend: str = "auto",
    output=None,
    backup: bool = False,
) -> MetadataRecord:
    """Copy canonical metadata from one file to another, across formats."""
    source_record = read_metadata(source, backend=backend)

    if fields:
        wanted = {resolve_field(name).name for name in fields}
    else:
        wanted = {
            name for name, spec in FIELDS_BY_NAME.items()
            if spec.writable and name not in TECHNICAL_FIELDS
        }

    dest_backend = select_backend(destination, backend, for_write=True)
    dest_kind = read_metadata(destination, backend=backend).kind

    changes = {}
    for name, value in source_record.common.items():
        if name not in wanted or name in TECHNICAL_FIELDS:
            continue
        spec = FIELDS_BY_NAME.get(name)
        # When copying across media kinds (e.g. JPEG -> MP4), only copy fields
        # applicable to the destination unless the user asked for the field explicitly.
        if not fields and spec and not spec.applies_to(dest_kind):
            continue
        if not fields and dest_backend.name == "audio" and name.startswith(("gps_", "camera_", "serial_")):
            continue
        changes[name] = value

    if not changes:
        raise FieldError(f"{Path(source).name} has no metadata worth copying")

    return write_metadata(
        destination, changes, backend=backend, output=output, backup=backup
    )


def diff_metadata(
    first,
    second,
    backend: str = "auto",
    include_raw: bool = False,
) -> dict:
    """Compare metadata of two files and return their differences."""
    rec_a = read_metadata(first, backend=backend)
    rec_b = read_metadata(second, backend=backend)

    common_a = _jsonable(rec_a.ordered_common())
    common_b = _jsonable(rec_b.ordered_common())

    all_common_keys = list(common_a.keys()) + [k for k in common_b if k not in common_a]
    common_diff = {}
    for key in all_common_keys:
        val_a = common_a.get(key)
        val_b = common_b.get(key)
        if val_a != val_b:
            common_diff[key] = {"first": val_a, "second": val_b}

    raw_diff = {}
    if include_raw:
        raw_a = _jsonable(rec_a.raw)
        raw_b = _jsonable(rec_b.raw)
        for key in sorted(set(raw_a) | set(raw_b)):
            val_a = raw_a.get(key)
            val_b = raw_b.get(key)
            if val_a != val_b:
                raw_diff[key] = {"first": val_a, "second": val_b}

    result = {
        "first": str(rec_a.path),
        "second": str(rec_b.path),
        "identical": not common_diff and not raw_diff,
        "common": common_diff,
    }
    if include_raw:
        result["raw"] = raw_diff
    return result


def shift_datetime(
    path,
    delta,
    backend: str = "auto",
    output=None,
    backup: bool = False,
) -> MetadataRecord:
    """Shift ``datetime_original`` of ``path`` by ``delta`` (e.g. ``'+2h30m'``)."""
    offset = parse_timedelta(delta)
    record = read_metadata(path, backend=backend)
    current = record.common.get("datetime_original")
    if not isinstance(current, _dt.datetime):
        raise FieldError(f"{Path(path).name} has no datetime_original to shift")
    shifted = current + offset
    return write_metadata(
        path,
        {"datetime_original": shifted},
        backend=backend,
        output=output,
        backup=backup,
    )


class _SafeRenameFormatter(string.Formatter):
    """Template formatter that handles missing values and custom date formatting."""

    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            return kwargs.get(key, "")
        return super().get_value(key, args, kwargs)

    def format_field(self, value, format_spec):
        if value is None or value == "":
            return ""
        if isinstance(value, (_dt.datetime, _dt.date)):
            fmt = format_spec or "%Y-%m-%d_%H-%M-%S"
            return value.strftime(fmt)
        if isinstance(value, (list, tuple)):
            return "-".join(str(item) for item in value)
        try:
            return super().format_field(value, format_spec)
        except ValueError:
            return str(value)


def _sanitize_filename_stem(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    cleaned = re.sub(r"[\s_]+", "_", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.strip(" _.-")


def rename_by_metadata(
    path,
    template: str = "{date:%Y-%m-%d_%H-%M-%S}_{title}",
    backend: str = "auto",
    dry_run: bool = False,
) -> Path:
    """Rename ``path`` in its folder using a metadata template string.

    Supported placeholders include any canonical field (``{title}``,
    ``{artist}``, ``{camera_model}``, ``{iso}``, ``{track_number:02d}``),
    aliases (``{date:%Y-%m-%d}``, ``{make}``, ``{model}``), and file parts
    (``{stem}``, ``{ext}``, ``{format}``).
    """
    path = Path(path)
    record = read_metadata(path, backend=backend)

    context = {spec.name: "" for spec in COMMON_FIELDS}
    context.update(record.common)
    # Helpful shorthand aliases in templates
    context.setdefault("date", record.common.get("datetime_original", ""))
    if record.common.get("datetime_original"):
        context["date"] = record.common["datetime_original"]
    context["make"] = record.common.get("camera_make", "")
    context["model"] = record.common.get("camera_model", "")
    context["track"] = record.common.get("track_number", "")
    context["disc"] = record.common.get("disc_number", "")
    context["stem"] = path.stem
    context["ext"] = path.suffix
    context["format"] = record.format

    formatter = _SafeRenameFormatter()
    try:
        rendered = formatter.format(template, **context)
    except Exception as exc:
        raise FieldError(f"invalid rename template {template!r}: {exc}") from exc

    # Strip suffix if template included {ext}, sanitize stem, then re-attach suffix.
    suffix = path.suffix
    if rendered.lower().endswith(suffix.lower()) and suffix:
        rendered = rendered[: -len(suffix)]
    stem = _sanitize_filename_stem(rendered)
    if not stem:
        raise FieldError(
            f"template {template!r} produced an empty filename for {path.name}"
        )

    candidate = path.with_name(f"{stem}{suffix}")
    counter = 1
    while candidate.exists() and candidate.resolve() != path.resolve():
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        counter += 1

    if not dry_run and candidate != path:
        path.rename(candidate)
    return candidate


def export_metadata(
    paths,
    destination=None,
    format: str = "auto",
    recursive: bool = False,
    include_raw: bool = False,
    backend: str = "auto",
) -> str:
    """Export metadata of ``paths`` as a JSON or CSV string (and optionally write to ``destination``)."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    expanded = list(iter_media_files(paths, recursive=recursive))
    if not expanded:
        raise MediaMetaError("no files matched")

    records = [read_metadata(p, backend=backend) for p in expanded]
    fmt = format.lower()
    if fmt == "auto":
        if destination and str(destination).lower().endswith(".csv"):
            fmt = "csv"
        else:
            fmt = "json"

    if fmt == "json":
        text = json.dumps(
            [rec.to_dict(include_raw=include_raw) for rec in records],
            indent=2,
            ensure_ascii=False,
        )
    elif fmt == "csv":
        field_names = ["path", "format", "kind", "backend"] + [
            spec.name for spec in COMMON_FIELDS
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=field_names, lineterminator="\n")
        writer.writeheader()
        for rec in records:
            row = {
                "path": str(rec.path),
                "format": rec.format,
                "kind": rec.kind,
                "backend": rec.backend,
            }
            for spec in COMMON_FIELDS:
                val = rec.common.get(spec.name)
                if val is None:
                    row[spec.name] = ""
                elif isinstance(val, _dt.datetime):
                    row[spec.name] = val.isoformat(sep=" ")
                elif isinstance(val, (list, tuple)):
                    row[spec.name] = ", ".join(str(item) for item in val)
                else:
                    row[spec.name] = str(val)
            writer.writerow(row)
        text = buf.getvalue()
    else:
        raise FieldError(f"unsupported export format {format!r}; choose json or csv")

    if destination is not None:
        with staged_write(destination) as temporary:
            temporary.write_text(text, encoding="utf-8")
    return text


def import_metadata(
    source,
    backend: str = "auto",
    backup: bool = False,
    dry_run: bool = False,
) -> list:
    """Batch-apply metadata from a JSON or CSV file produced by :func:`export_metadata`."""
    source_path = Path(source)
    if not source_path.exists():
        raise MediaMetaError(f"{source_path} does not exist")

    text = source_path.read_text(encoding="utf-8")
    is_csv = source_path.suffix.lower() == ".csv" or (
        not text.lstrip().startswith(("[", "{"))
    )

    entries = []
    if is_csv:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            file_path = row.get("path")
            if not file_path:
                continue
            common = {
                k: v
                for k, v in row.items()
                if k in FIELDS_BY_NAME and FIELDS_BY_NAME[k].writable and v not in (None, "")
            }
            entries.append((file_path, common, {}))
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MediaMetaError(f"could not parse JSON from {source_path.name}: {exc}") from exc
        if isinstance(payload, dict):
            payload = [payload]
        for item in payload:
            file_path = item.get("path")
            if not file_path:
                continue
            raw_common = item.get("common") if isinstance(item.get("common"), dict) else item
            common = {
                k: v
                for k, v in raw_common.items()
                if k in FIELDS_BY_NAME and FIELDS_BY_NAME[k].writable
            }
            entries.append((file_path, common, {}))

    results = []
    for raw_target, changes, raw_tags in entries:
        target = Path(raw_target)
        if not target.is_absolute() and not target.exists():
            candidate = source_path.parent / target
            if candidate.exists():
                target = candidate
        if not changes and not raw_tags:
            continue
        if dry_run:
            results.append({"path": str(target), "changes": _normalize_changes(changes)})
        else:
            results.append(
                write_metadata(target, changes, raw_tags, backend=backend, backup=backup)
            )
    return results


def _normalize_changes(changes: dict) -> dict:
    normalized = {}
    for name, value in changes.items():
        spec = resolve_field(name)
        if not spec.writable:
            raise FieldError(f"{spec.name} is read-only")
        normalized[spec.name] = None if value is None else normalize(spec, value)
    return normalized
