"""Tests for mediameta 2.0G improvements, bug fixes and new features."""

from __future__ import annotations

import datetime as dt
import json
import struct
import wave
import zlib
from pathlib import Path

import pytest

from mediameta import (
    __version__,
    diff_metadata,
    export_metadata,
    import_metadata,
    mp4box,
    pngchunk,
    read_metadata,
    remove_metadata,
    rename_by_metadata,
    shift_datetime,
    write_metadata,
)
from mediameta.cli import main
from mediameta.errors import FieldError
from mp4_factory import make_mp4


def run(*argv):
    return main(list(argv))


# -- audio test fixtures ---------------------------------------------------


@pytest.fixture
def wav_file(tmp_path) -> Path:
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 4000)  # 0.5 seconds
    return path


@pytest.fixture
def mp3_file(tmp_path) -> Path:
    """Minimal valid MPEG1 Layer 3 frame."""
    path = tmp_path / "song.mp3"
    # MPEG1 Layer3 128kbps 44100Hz stereo frame header = 0xFFFB9064, frame size = 417 bytes
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    path.write_bytes(frame * 10)
    return path


@pytest.fixture
def flac_file(tmp_path) -> Path:
    """Minimal valid FLAC stream with a Last-Metadata-Block STREAMINFO."""
    path = tmp_path / "track.flac"
    # STREAMINFO block (type 0, last=1 -> 0x80, length=34 bytes)
    # 44100 Hz, 2 channels, 16 bps, 44100 samples (1.0s)
    samplerate = 44100
    channels_Minus1 = 1
    bps_minus1 = 15
    total_samples = 44100
    packed_sr_ch_bps_samples = (
        (samplerate << 44)
        | (channels_Minus1 << 41)
        | (bps_minus1 << 36)
        | total_samples
    )
    streaminfo = (
        struct.pack(">HH", 4096, 4096)
        + b"\x00\x00\x00"
        + b"\x00\x00\x00"
        + packed_sr_ch_bps_samples.to_bytes(8, "big")
        + b"\x00" * 16
    )
    header = b"fLaC" + bytes([0x80, 0x00, 0x00, 34]) + streaminfo
    path.write_bytes(header)
    return path


# -- version 2.0G check ----------------------------------------------------


def test_version_is_2_0g(capsys):
    assert __version__ == "2.0G"
    with pytest.raises(SystemExit):
        run("--version")
    assert "2.0G" in capsys.readouterr().out


# -- 1.0 bug fixes ---------------------------------------------------------


def test_mp4_remove_datetime_clears_mvhd_header(mp4):
    """In 1.0, deleting datetime_original left the timestamp in mvhd."""
    moment = dt.datetime(2024, 7, 1, 12, 0, 0)
    write_metadata(mp4, {"datetime_original": moment, "title": "Клип"})
    assert read_metadata(mp4).common["datetime_original"] == moment

    record = remove_metadata(mp4, ["datetime_original"])
    assert "datetime_original" not in record.common
    assert "QuickTime:CreateDate" not in record.raw
    assert record.common["title"] == "Клип"


def test_mp4_strip_all_removes_header_dates(mp4):
    """In 1.0, remove_metadata(mp4) left datetime_original in common via mvhd."""
    write_metadata(mp4, {"datetime_original": dt.datetime(2024, 7, 1, 12, 0, 0), "title": "Клип"})
    record = remove_metadata(mp4)
    assert "datetime_original" not in record.common


def test_mp4_auto_upgrades_stco_to_co64_on_overflow(tmp_path):
    """When shifting offsets overflows 32-bit stco, rewrite_file promotes to co64."""
    path = make_mp4(tmp_path / "near4gb.mp4", faststart=True)
    moov, top = mp4box.read_moov(path)
    # Set the first stco chunk offset right below 4 GiB
    for box in moov.walk():
        if box.type == b"stco":
            count = struct.unpack_from(">I", box.payload, 4)[0]
            box.payload = struct.pack(">II", 0, count) + b"".join(
                struct.pack(">I", 0xFFFFFFF0 + i) for i in range(count)
            )
    mp4box.rewrite_file(path, path, moov, top)

    # Now grow moov by adding metadata: stco offsets will cross 0xFFFFFFFF
    write_metadata(path, {"title": "За пределами 4 ГБ", "comment": "x" * 100})
    new_moov, _ = mp4box.read_moov(path)
    co64_boxes = [b for b in new_moov.walk() if b.type == b"co64"]
    assert len(co64_boxes) >= 1
    assert read_metadata(path).common["title"] == "За пределами 4 ГБ"


def test_xmp_deleted_field_does_not_resurrect(jpeg):
    """In 1.0, deleting title on a JPEG with XMP resurrected the XMP title."""
    packet = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title><rdf:Alt><rdf:li>Из Lightroom</rdf:li></rdf:Alt></dc:title>"
        "<dc:creator><rdf:Seq><rdf:li>Костя</rdf:li></rdf:Seq></dc:creator>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    ).encode()
    data = bytearray(jpeg.read_bytes())
    sig = b"http://ns.adobe.com/xap/1.0/\x00"
    payload = sig + packet
    data[2:2] = b"\xff\xe1" + len(payload).to_bytes(2, "big") + payload
    jpeg.write_bytes(bytes(data))

    assert read_metadata(jpeg).common["title"] == "Из Lightroom"

    # Deleting title must remove it from both EXIF and XMP
    record = remove_metadata(jpeg, ["title"])
    assert "title" not in record.common
    assert record.common["artist"] == "Костя"


def test_native_raw_xmp_write_on_jpeg_and_png(jpeg, png):
    rec_jpg = write_metadata(jpeg, {}, raw={"XMP:dc:title": "Нативный XMP"})
    assert rec_jpg.raw["XMP:dc:title"] == "Нативный XMP"
    assert rec_jpg.common["title"] == "Нативный XMP"

    rec_png = write_metadata(png, {}, raw={"XMP:dc:title": "PNG XMP"})
    assert rec_png.raw["XMP:dc:title"] == "PNG XMP"
    assert rec_png.common["title"] == "PNG XMP"


def test_png_lossless_chunk_editing_preserves_idat_and_ancillary_chunks(png):
    """In 2.0G, PNG edits never recompress IDAT or drop custom ancillary chunks."""
    raw = png.read_bytes()
    chunks = pngchunk.parse_chunks(raw)
    custom = pngchunk.PngChunk(b"caBX", b"custom-ancillary-payload-12345")
    chunks.insert(1, custom)
    png.write_bytes(pngchunk.serialize_chunks(chunks))

    idat_before = [c.data for c in pngchunk.parse_chunks(png.read_bytes()) if c.type == b"IDAT"]

    write_metadata(png, {"title": "Без пережатия", "artist": "Костя", "gps_latitude": 55.75, "gps_longitude": 37.61})

    after_chunks = pngchunk.parse_chunks(png.read_bytes())
    idat_after = [c.data for c in after_chunks if c.type == b"IDAT"]
    assert idat_after == idat_before
    assert any(c.type == b"caBX" and c.data == b"custom-ancillary-payload-12345" for c in after_chunks)
    assert read_metadata(png).common["title"] == "Без пережатия"


# -- new photographic, video and audio fields ------------------------------


def test_extended_photo_fields_roundtrip(jpeg):
    changes = {
        "iso": 800,
        "aperture": "f/2.8",
        "shutter_speed": "1/250",
        "focal_length": "50 mm",
        "focal_length_35mm": 50,
        "flash": 16,
        "lens_make": "Canon",
        "lens": "RF 50mm F1.2L USM",
        "serial_number": "SN-987654321",
    }
    record = write_metadata(jpeg, changes)
    assert record.common["iso"] == 800
    assert record.common["aperture"] == pytest.approx(2.8, abs=1e-3)
    assert record.common["shutter_speed"] == pytest.approx(0.004, abs=1e-6)
    assert record.common["focal_length"] == pytest.approx(50.0, abs=1e-3)
    assert record.common["focal_length_35mm"] == 50
    assert record.common["flash"] == 16
    assert record.common["lens_make"] == "Canon"
    assert record.common["lens"] == "RF 50mm F1.2L USM"
    assert record.common["serial_number"] == "SN-987654321"


def test_mp4_rotation_and_music_fields(mp4):
    record = write_metadata(
        mp4,
        {
            "rotation": 90,
            "album_artist": "Кино",
            "composer": "Виктор Цой",
            "track_number": 3,
            "disc_number": 1,
            "bpm": 128,
            "lyrics": "Группа крови на рукаве",
        },
    )
    assert record.common["rotation"] == 90
    assert record.common["album_artist"] == "Кино"
    assert record.common["composer"] == "Виктор Цой"
    assert record.common["track_number"] == 3
    assert record.common["disc_number"] == 1
    assert record.common["bpm"] == 128
    assert record.common["lyrics"] == "Группа крови на рукаве"

    # Resetting rotation to 0 removes the non-zero rotation indicator
    reset = write_metadata(mp4, {"rotation": 0})
    assert "rotation" not in reset.common

    with pytest.raises(FieldError):
        write_metadata(mp4, {"rotation": 45})


def test_audio_backend_mp3_roundtrip(mp3_file):
    record = write_metadata(
        mp3_file,
        {
            "title": "Звезда по имени Солнце",
            "artist": "Кино",
            "album": "Звезда по имени Солнце",
            "album_artist": "Кино",
            "composer": "Виктор Цой",
            "genre": "Rock",
            "track_number": "2/9",
            "disc_number": 1,
            "bpm": 120,
            "rating": 5,
            "comment": "Классика",
            "lyrics": "Белый снег, серый лёд...",
            "keywords": ["рок", "80е"],
            "datetime_original": "1989-08-29",
        },
    )
    assert record.backend == "audio"
    assert record.format == "MP3"
    assert record.common["title"] == "Звезда по имени Солнце"
    assert record.common["artist"] == "Кино"
    assert record.common["album_artist"] == "Кино"
    assert record.common["composer"] == "Виктор Цой"
    assert record.common["track_number"] == 2
    assert record.common["disc_number"] == 1
    assert record.common["bpm"] == 120
    assert record.common["rating"] == 5
    assert record.common["comment"] == "Классика"
    assert record.common["lyrics"] == "Белый снег, серый лёд..."
    assert record.common["keywords"] == ["рок", "80е"]
    assert record.common["datetime_original"] == dt.datetime(1989, 8, 29, 0, 0)

    # Delete a single field and then strip all
    updated = remove_metadata(mp3_file, ["comment"])
    assert "comment" not in updated.common
    assert updated.common["title"] == "Звезда по имени Солнце"

    cleared = remove_metadata(mp3_file)
    assert "title" not in cleared.common
    assert "artist" not in cleared.common


def test_audio_backend_flac_and_wav_roundtrip(flac_file, wav_file):
    rec_flac = write_metadata(
        flac_file,
        {
            "title": "Лосслесс",
            "artist": "Костя",
            "album": "Тест",
            "track_number": 1,
            "keywords": ["flac", "hi-res"],
        },
    )
    assert rec_flac.backend == "audio"
    assert rec_flac.format == "FLAC"
    assert rec_flac.common["title"] == "Лосслесс"
    assert rec_flac.common["keywords"] == ["flac", "hi-res"]
    assert rec_flac.common["duration"] == pytest.approx(1.0, abs=0.05)

    rec_wav = write_metadata(wav_file, {"title": "Волна", "artist": "Костя"})
    assert rec_wav.format == "WAV"
    assert rec_wav.common["title"] == "Волна"
    assert rec_wav.common["duration"] == pytest.approx(0.5, abs=0.05)


# -- new 2.0G commands & API workflows -------------------------------------


def test_privacy_remove_keeps_creative_fields_and_strips_tracking(jpeg):
    write_metadata(
        jpeg,
        {
            "title": "Оставить заголовок",
            "copyright": "(c) 2026 Костя",
            "keywords": ["природа"],
            "orientation": 6,
            "rating": 5,
            "artist": "Секретный автор",
            "camera_make": "Canon",
            "camera_model": "EOS R6",
            "lens": "RF 50mm",
            "serial_number": "123456",
            "gps_latitude": 55.7558,
            "gps_longitude": 37.6176,
            "datetime_original": "2024-07-01 18:30:00",
            "comment": "Личный комментарий",
        },
    )
    assert run("remove", str(jpeg), "--privacy") == 0
    record = read_metadata(jpeg)
    # Creative & display fields survive:
    assert record.common["title"] == "Оставить заголовок"
    assert record.common["copyright"] == "(c) 2026 Костя"
    assert record.common["keywords"] == ["природа"]
    assert record.common["orientation"] == 6
    assert record.common["rating"] == 5
    # Privacy & device tracking fields are gone:
    for sensitive in (
        "artist", "camera_make", "camera_model", "lens", "serial_number",
        "gps_latitude", "gps_longitude", "datetime_original", "comment",
    ):
        assert sensitive not in record.common, sensitive


def test_diff_metadata_api_and_cli(jpeg, tmp_path, capsys):
    other = tmp_path / "other.jpg"
    other.write_bytes(jpeg.read_bytes())

    write_metadata(jpeg, {"title": "Оригинал", "artist": "Костя"})
    write_metadata(other, {"title": "Копия", "artist": "Костя", "rating": 5})

    report = diff_metadata(jpeg, other)
    assert report["identical"] is False
    assert report["common"]["title"] == {"first": "Оригинал", "second": "Копия"}
    assert report["common"]["rating"] == {"first": None, "second": 5}
    assert "artist" not in report["common"]

    assert run("diff", str(jpeg), str(other)) == 0
    out = capsys.readouterr().out
    assert "Оригинал" in out and "Копия" in out


def test_shift_datetime_api_and_cli(jpeg, capsys):
    write_metadata(jpeg, {"datetime_original": "2024-07-01 10:00:00"})
    record = shift_datetime(jpeg, "+2h 30m")
    assert record.common["datetime_original"] == dt.datetime(2024, 7, 1, 12, 30, 0)

    assert run("shift-date", str(jpeg), "--by", "-1d") == 0
    capsys.readouterr()
    assert read_metadata(jpeg).common["datetime_original"] == dt.datetime(2024, 6, 30, 12, 30, 0)


def test_rename_by_metadata_api_and_cli(jpeg, capsys):
    write_metadata(
        jpeg,
        {
            "title": "Закат на море",
            "camera_model": "EOS R6",
            "datetime_original": "2024-07-01 18:30:00",
        },
    )
    preview = rename_by_metadata(
        jpeg,
        template="{date:%Y-%m-%d}_{model}_{title}",
        dry_run=True,
    )
    assert preview.name == "2024-07-01_EOS_R6_Закат_на_море.jpg"
    assert jpeg.exists()

    assert run("rename", str(jpeg), "-p", "{date:%Y-%m-%d}_{model}_{title}") == 0
    assert "renamed" in capsys.readouterr().out
    assert preview.exists()
    assert not jpeg.exists()


def test_export_and_import_csv_and_json(jpeg, png, tmp_path):
    write_metadata(jpeg, {"title": "Первое", "artist": "Костя"})
    write_metadata(png, {"title": "Второе", "artist": "Костя"})

    csv_path = tmp_path / "meta.csv"
    export_metadata([jpeg, png], destination=csv_path, format="csv")
    assert csv_path.exists()
    text = csv_path.read_text(encoding="utf-8")
    assert "Первое" in text and "Второе" in text

    # Edit the CSV and import it back
    edited = text.replace("Первое", "Обновлённое 1").replace("Второе", "Обновлённое 2")
    csv_path.write_text(edited, encoding="utf-8")
    assert run("import", str(csv_path)) == 0

    assert read_metadata(jpeg).common["title"] == "Обновлённое 1"
    assert read_metadata(png).common["title"] == "Обновлённое 2"

    # Also test JSON export/import roundtrip
    json_path = tmp_path / "meta.json"
    assert run("export", str(jpeg), "-o", str(json_path)) == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload[0]["common"]["title"] = "Из JSON"
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    import_metadata(json_path)
    assert read_metadata(jpeg).common["title"] == "Из JSON"
