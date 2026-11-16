"""Command line interface: ``mediameta show|set|remove|copy|diff|shift-date|rename|export|import|fields|backends``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, exiftool_setup
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
from .backends import backend_status
from .errors import MediaMetaError
from .fields import PRIVACY_FIELDS, parse_assignments, parse_timedelta, resolve_field
from .record import _render_value, describe_fields
from .utils import iter_media_files

EPILOG = """\
examples:
  mediameta show photo.jpg
  mediameta show album/ -r --json
  mediameta set photo.jpg -s title="Sunset" -s "date=2024-07-01 18:30" -s gps=55.7558,37.6176
  mediameta set clip.mp4 -s artist="Kostya" -s rotation=90 --backup
  mediameta set track.mp3 -s title="Intro" -s artist="Kostya" -s track=1 -s bpm=120
  mediameta remove photo.jpg --privacy --backup
  mediameta remove photo.jpg --all --backup
  mediameta copy original.jpg edited.jpg
  mediameta diff original.jpg edited.jpg
  mediameta shift-date album/ -r --by "+2h30m"
  mediameta rename *.jpg -p "{date:%Y-%m-%d_%H-%M-%S}_{model}_{title}" --dry-run
  mediameta export album/ -r --csv -o meta.csv
  mediameta import meta.csv --backup
  mediameta exiftool install      # adds HEIC, RAW, MKV, AVI support
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mediameta",
        description="Read and edit metadata of photos, videos and audio (v2.0G).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"mediameta {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--backend",
        default="auto",
        help="auto (default), native, exif, png, mp4, audio or exiftool",
    )
    common.add_argument(
        "-r", "--recursive", action="store_true", help="descend into directories"
    )

    show = subparsers.add_parser("show", parents=[common], aliases=["read", "info"],
                                 help="print the metadata of one or more files")
    show.add_argument("paths", nargs="+", type=Path)
    show.add_argument("--json", action="store_true", help="machine-readable output")
    show.add_argument("--raw", action="store_true", help="also list backend-specific tags")
    show.add_argument("-f", "--field", action="append", default=[],
                      help="show only these canonical fields (repeatable)")
    show.set_defaults(handler=command_show)

    write = subparsers.add_parser("set", parents=[common], aliases=["edit"],
                                  help="set or delete metadata fields")
    write.add_argument("paths", nargs="+", type=Path)
    write.add_argument("-s", "--set", action="append", default=[], metavar="NAME=VALUE",
                       dest="assignments",
                       help="canonical field; an empty value deletes it")
    write.add_argument("-t", "--tag", action="append", default=[], metavar="TAG=VALUE",
                       help="backend-specific tag, e.g. EXIF:ISOSpeedRatings=400")
    write.add_argument("-o", "--output", type=Path,
                       help="write the result here instead of editing in place")
    write.add_argument("--backup", action="store_true",
                       help="keep the original as FILE.bak")
    write.add_argument("-n", "--dry-run", action="store_true",
                       help="show what would change without touching any file")
    write.set_defaults(handler=command_set)

    remove = subparsers.add_parser("remove", parents=[common], aliases=["clear", "strip"],
                                   help="delete single fields, privacy data, or every metadata block")
    remove.add_argument("paths", nargs="+", type=Path)
    remove.add_argument("-f", "--field", action="append", default=[],
                        help="canonical field to delete (repeatable)")
    remove.add_argument("--privacy", action="store_true",
                        help="strip GPS, capture dates, device serial/make/model, author and comments")
    remove.add_argument("--all", action="store_true", dest="remove_all",
                        help="strip all metadata, including tags we do not map")
    remove.add_argument("-o", "--output", type=Path, help="write the result here instead")
    remove.add_argument("--backup", action="store_true", help="keep the original as FILE.bak")
    remove.add_argument("-n", "--dry-run", action="store_true", help="only report what would go")
    remove.set_defaults(handler=command_remove)

    copy = subparsers.add_parser("copy", parents=[common],
                                 help="copy metadata from one file to another")
    copy.add_argument("source", type=Path)
    copy.add_argument("destination", type=Path)
    copy.add_argument("-f", "--field", action="append", default=[],
                      help="copy only these fields (default: everything writable)")
    copy.add_argument("-o", "--output", type=Path, help="write the result here instead")
    copy.add_argument("--backup", action="store_true", help="keep the destination as FILE.bak")
    copy.set_defaults(handler=command_copy)

    diff = subparsers.add_parser("diff", parents=[common], aliases=["compare"],
                                 help="compare metadata between two files")
    diff.add_argument("first", type=Path)
    diff.add_argument("second", type=Path)
    diff.add_argument("--raw", action="store_true", help="also compare raw backend tags")
    diff.add_argument("--json", action="store_true", help="machine-readable JSON diff")
    diff.set_defaults(handler=command_diff)

    shift = subparsers.add_parser("shift-date", parents=[common], aliases=["shift"],
                                  help="shift datetime_original by a time offset (e.g. +2h30m, -1d)")
    shift.add_argument("paths", nargs="+", type=Path)
    shift.add_argument("-b", "--by", required=True, metavar="OFFSET",
                       help="time offset such as '+2h', '-1d 3h 30m', '+03:00'")
    shift.add_argument("-o", "--output", type=Path, help="write the result here instead")
    shift.add_argument("--backup", action="store_true", help="keep the original as FILE.bak")
    shift.add_argument("-n", "--dry-run", action="store_true", help="show shifted dates without writing")
    shift.set_defaults(handler=command_shift_date)

    rename = subparsers.add_parser("rename", parents=[common],
                                   help="rename files using metadata placeholders")
    rename.add_argument("paths", nargs="+", type=Path)
    rename.add_argument(
        "-p", "--pattern",
        default="{date:%Y-%m-%d_%H-%M-%S}_{title}",
        help="template pattern (default: '{date:%%Y-%%m-%%d_%%H-%%M-%%S}_{title}')",
    )
    rename.add_argument("-n", "--dry-run", action="store_true",
                        help="preview new file names without renaming")
    rename.set_defaults(handler=command_rename)

    export_cmd = subparsers.add_parser("export", parents=[common],
                                       help="export metadata of files to JSON or CSV")
    export_cmd.add_argument("paths", nargs="+", type=Path)
    export_cmd.add_argument("-o", "--output", type=Path, help="destination .json or .csv file")
    export_cmd.add_argument("--csv", action="store_true", help="export as CSV table")
    export_cmd.add_argument("--json", action="store_true", help="export as JSON (default)")
    export_cmd.add_argument("--raw", action="store_true", help="include raw tags in JSON export")
    export_cmd.set_defaults(handler=command_export)

    import_cmd = subparsers.add_parser("import", parents=[common],
                                       help="batch-import metadata from a JSON or CSV file")
    import_cmd.add_argument("source", type=Path, help="JSON or CSV file to import from")
    import_cmd.add_argument("--backup", action="store_true", help="keep originals as FILE.bak")
    import_cmd.add_argument("-n", "--dry-run", action="store_true", help="preview changes without writing")
    import_cmd.set_defaults(handler=command_import)

    fields = subparsers.add_parser("fields", help="list the canonical field names")
    fields.set_defaults(handler=command_fields)

    backends = subparsers.add_parser("backends", help="show which backends are usable")
    backends.set_defaults(handler=command_backends)

    exiftool = subparsers.add_parser(
        "exiftool", help="download or inspect the exiftool helper (HEIC, RAW, MKV, AVI)"
    )
    actions = exiftool.add_subparsers(dest="action", required=True)
    install = actions.add_parser(
        "install", help="download the latest exiftool from exiftool.org and verify it"
    )
    install.add_argument("--force", action="store_true",
                         help="download again even if this version is installed")
    install.set_defaults(handler=command_exiftool_install)
    status = actions.add_parser("status", help="show which exiftool mediameta will use")
    status.set_defaults(handler=command_exiftool_status)
    remove_et = actions.add_parser("remove", help="delete mediameta's own copy of exiftool")
    remove_et.set_defaults(handler=command_exiftool_remove)

    return parser


# -- commands ----------------------------------------------------------


def command_show(args) -> int:
    paths = list(iter_media_files(args.paths, args.recursive))
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1

    wanted = [resolve_field(name).name for name in args.field]
    records, failures = [], 0

    for path in paths:
        try:
            record = read_metadata(path, backend=args.backend)
        except MediaMetaError as exc:
            _report_error(path, exc)
            failures += 1
            continue

        if wanted:
            record.common = {
                name: value for name, value in record.common.items() if name in wanted
            }
        records.append(record)

    if args.json:
        print(json.dumps([record.to_dict(args.raw) for record in records],
                         indent=2, ensure_ascii=False))
    else:
        for index, record in enumerate(records):
            if index:
                print()
            print(record.render(include_raw=args.raw))

    return 1 if failures else 0


def command_set(args) -> int:
    changes = parse_assignments(args.assignments)
    raw_changes = _parse_raw_tags(args.tag)
    if not changes and not raw_changes:
        print("nothing to do: pass at least one --set or --tag", file=sys.stderr)
        return 2

    paths = list(iter_media_files(args.paths, args.recursive))
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1
    if args.output and len(paths) > 1:
        print("--output works with a single file only", file=sys.stderr)
        return 2

    if args.dry_run:
        _print_planned_changes(paths, changes, raw_changes)
        return 0

    failures = 0
    for path in paths:
        try:
            record = write_metadata(
                path,
                changes,
                raw_changes,
                backend=args.backend,
                output=args.output,
                backup=args.backup,
            )
        except MediaMetaError as exc:
            _report_error(path, exc)
            failures += 1
            continue
        _report_success(record, changes, raw_changes)
    return 1 if failures else 0


def command_remove(args) -> int:
    if not args.field and not args.remove_all and not args.privacy:
        print("pass --field NAME, --privacy or --all", file=sys.stderr)
        return 2

    paths = list(iter_media_files(args.paths, args.recursive))
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1
    if args.output and len(paths) > 1:
        print("--output works with a single file only", file=sys.stderr)
        return 2

    fields = [] if args.remove_all else [resolve_field(name).name for name in args.field]

    if args.dry_run:
        if args.remove_all:
            target = "every metadata block"
        elif args.privacy:
            target = "privacy fields (" + ", ".join(PRIVACY_FIELDS) + ")"
        else:
            target = ", ".join(fields)
        for path in paths:
            print(f"would remove {target} from {path}")
        return 0

    failures = 0
    for path in paths:
        try:
            record = remove_metadata(
                path,
                fields,
                backend=args.backend,
                output=args.output,
                backup=args.backup,
                privacy=args.privacy and not args.remove_all,
            )
        except MediaMetaError as exc:
            _report_error(path, exc)
            failures += 1
            continue
        if args.remove_all:
            removed = "all metadata"
        elif args.privacy:
            removed = "privacy metadata"
        else:
            removed = ", ".join(fields)
        print(f"{record.path}: removed {removed}")
        _print_warnings(record)
    return 1 if failures else 0


def command_copy(args) -> int:
    try:
        record = copy_metadata(
            args.source,
            args.destination,
            args.field,
            backend=args.backend,
            output=args.output,
            backup=args.backup,
        )
    except MediaMetaError as exc:
        _report_error(args.destination, exc)
        return 1

    copied = ", ".join(args.field) if args.field else "all writable fields"
    print(f"{record.path}: copied {copied} from {args.source}")
    _print_warnings(record)
    return 0


def command_diff(args) -> int:
    try:
        report = diff_metadata(
            args.first,
            args.second,
            backend=args.backend,
            include_raw=args.raw,
        )
    except MediaMetaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if report["identical"]:
        print(f"{args.first} and {args.second} have identical metadata")
        return 0

    print(f"--- {report['first']}")
    print(f"+++ {report['second']}")
    for name, pair in report["common"].items():
        first_val = "(none)" if pair["first"] is None else _render_value(pair["first"])
        second_val = "(none)" if pair["second"] is None else _render_value(pair["second"])
        print(f"  {name}:")
        print(f"    - {first_val}")
        print(f"    + {second_val}")

    for name, pair in report.get("raw", {}).items():
        first_val = "(none)" if pair["first"] is None else _render_value(pair["first"])
        second_val = "(none)" if pair["second"] is None else _render_value(pair["second"])
        print(f"  {name}:")
        print(f"    - {first_val}")
        print(f"    + {second_val}")
    return 0


def command_shift_date(args) -> int:
    offset = parse_timedelta(args.by)
    paths = list(iter_media_files(args.paths, args.recursive))
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1
    if args.output and len(paths) > 1:
        print("--output works with a single file only", file=sys.stderr)
        return 2

    failures = 0
    for path in paths:
        try:
            if args.dry_run:
                rec = read_metadata(path, backend=args.backend)
                current = rec.common.get("datetime_original")
                if current is None:
                    raise MediaMetaError(f"{path.name} has no datetime_original to shift")
                print(f"{path}: {current} -> {current + offset}")
                continue
            record = shift_datetime(
                path,
                offset,
                backend=args.backend,
                output=args.output,
                backup=args.backup,
            )
            print(f"{record.path}: shifted datetime_original to {record.common.get('datetime_original')}")
            _print_warnings(record)
        except MediaMetaError as exc:
            _report_error(path, exc)
            failures += 1
    return 1 if failures else 0


def command_rename(args) -> int:
    paths = list(iter_media_files(args.paths, args.recursive))
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1

    failures = 0
    for path in paths:
        try:
            target = rename_by_metadata(
                path,
                template=args.pattern,
                backend=args.backend,
                dry_run=args.dry_run,
            )
            prefix = "would rename" if args.dry_run else "renamed"
            print(f"{prefix}: {path} -> {target.name}")
        except MediaMetaError as exc:
            _report_error(path, exc)
            failures += 1
    return 1 if failures else 0


def command_export(args) -> int:
    fmt = "csv" if args.csv else ("json" if args.json else "auto")
    text = export_metadata(
        args.paths,
        destination=args.output,
        format=fmt,
        recursive=args.recursive,
        include_raw=args.raw,
        backend=args.backend,
    )
    if args.output:
        print(f"exported metadata to {args.output}")
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


def command_import(args) -> int:
    results = import_metadata(
        args.source,
        backend=args.backend,
        backup=args.backup,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        for item in results:
            print(f"would update {item['path']}: {', '.join(item['changes'])}")
    else:
        for record in results:
            print(f"{record.path}: imported metadata")
    return 0


def command_fields(args) -> int:
    print(describe_fields())
    return 0


def command_backends(args) -> int:
    for name, available, reason, formats in backend_status():
        mark = "yes" if available else "no "
        print(f"  [{mark}] {name:<10} {formats}")
        if not available:
            print(f"         {reason}")
    return 0


def command_exiftool_install(args) -> int:
    result = exiftool_setup.install(force=args.force, progress=print)
    if result["changed"]:
        print(f"installed exiftool {result['version']} into {result['path']}")
    else:
        print(f"exiftool {result['version']} is already installed in {result['path']}")
    return 0


def command_exiftool_status(args) -> int:
    from .backends import BACKENDS_BY_NAME

    backend = BACKENDS_BY_NAME["exiftool"]
    command, source = backend.resolve()
    if not command:
        print(f"exiftool: not available ({backend.is_available()[1]})")
        return 0

    version = _exiftool_version(command)
    print(f"exiftool: {version or 'unknown version'}, from {source}")
    print(f"  command: {' '.join(command)}")
    print(f"  mediameta's own folder: {exiftool_setup.install_dir()}")
    return 0


def command_exiftool_remove(args) -> int:
    folder = exiftool_setup.install_dir()
    if exiftool_setup.uninstall():
        print(f"removed {folder}")
    else:
        print(f"nothing to remove: {folder} does not exist")
    return 0


def _exiftool_version(command):
    import subprocess

    try:
        result = subprocess.run([*command, "-ver"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


# -- helpers -----------------------------------------------------------


def _parse_raw_tags(pairs) -> dict:
    tags = {}
    for pair in pairs:
        if "=" not in pair:
            raise MediaMetaError(f"expected TAG=VALUE, got {pair!r}")
        name, _, value = pair.partition("=")
        tags[name.strip()] = None if value == "" else value
    return tags


def _print_planned_changes(paths, changes: dict, raw_changes: dict) -> None:
    for path in paths:
        print(f"{path}:")
        for name, value in changes.items():
            print(f"    {name} = {'(delete)' if value is None else value}")
        for name, value in raw_changes.items():
            print(f"    {name} = {'(delete)' if value is None else value}")


def _report_success(record, changes: dict, raw_changes: dict) -> None:
    touched = list(changes) + list(raw_changes)
    print(f"{record.path}: updated {', '.join(touched)}")
    _print_warnings(record)


def _print_warnings(record) -> None:
    for warning in record.warnings:
        print(f"    ! {warning}", file=sys.stderr)


def _report_error(path, exc: Exception) -> None:
    print(f"{path}: {exc}", file=sys.stderr)


def configure_console() -> None:
    """Make sure non-ASCII output survives the terminal.

    The Windows console still defaults to a legacy code page, where a
    Cyrillic title or the separator dot would raise UnicodeEncodeError
    instead of being printed. Switching the console and our own streams to
    UTF-8 fixes both; everywhere else this is a no-op.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass  # a redirected or unusual console: the reconfigure below still helps

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _normalize_argv(argv):
    """Allow ``--by -1d`` or ``-b -2h`` without argparse mistaking ``-1d`` for a flag."""
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    out = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in ("-b", "--by") and i + 1 < len(argv) and argv[i + 1].startswith("-"):
            out.append(f"--by={argv[i + 1]}")
            i += 2
            continue
        out.append(token)
        i += 1
    return out


def main(argv=None) -> int:
    configure_console()
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(argv))
    try:
        return args.handler(args)
    except MediaMetaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
