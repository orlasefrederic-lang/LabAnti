# mediameta 2.0G

Чтение, редактирование и пакетная обработка метаданных **фото, видео и аудио** — из командной строки и из Python.

Один и тот же набор канонических полей (`title`, `artist`, `datetime_original`, `gps_latitude`, `iso`, `aperture`, `rotation`, `track_number`, …) работает для JPEG, PNG, WebP, MP4/MOV, MP3, FLAC, OGG и WAV: библиотека сама раскладывает их по EXIF-тегам, XMP-пакетам, чанкам PNG, атомам QuickTime, фреймам ID3v2 или комментариям Vorbis.

```console
$ mediameta show IMG_0042.jpg
IMG_0042.jpg
  JPEG · image · backend=exif
  metadata:
    title                  Закат на заливе
    artist                 Костя
    datetime_original      2024-07-01 18:30:00
    camera_make            Canon
    camera_model           EOS R6
    lens                   RF 24-70 F2.8
    iso                    400
    aperture               2.8
    shutter_speed          0.004
    focal_length           50
    gps_latitude           55.7558
    gps_longitude          37.6176
    width                  6000
    height                 4000

$ mediameta set IMG_0042.jpg -s title="Закат" -s gps=55.7558,37.6176 -s shutter_speed=1/250 --backup
IMG_0042.jpg: updated title, gps_latitude, gps_longitude, shutter_speed
```

## Что нового в версии 2.0G

* **Без потерь и пережатия PNG (`pngchunk.py`)** — метаданные PNG (`tEXt`, UTF-8 `iTXt`, `zTXt`, `eXIf`) редактируются напрямую на уровне чанков без перекодирования пикселей через Pillow. Потоки `IDAT` и любые нестандартные чанки (`pHYs`, `iCCP`, `sRGB`, `acTL`) сохраняются бит-в-бит.
* **Полноценная синхронизация и запись XMP (`xmp.py`)** — при редактировании или удалении полей в JPEG и PNG встроенный пакет XMP (например, от Lightroom или Photoshop) обновляется синхронно, чтобы удалённые поля не «воскресали» из XMP, а теги `-t XMP:dc:title=...` пишутся нативно без `exiftool`.
* **Нативный аудио-бэкенд (`audio`)** — чтение, запись и очистка метаданных для **MP3, FLAC, OGG, Opus, WAV и AIFF** (ID3v2 и Vorbis Comments) без внешних утилит (в 1.0 эти форматы перекладывались на `exiftool`, который не умеет записывать MP3/FLAC/OGG/WAV).
* **Исправление очистки дат и авто-апгрейд `co64` в MP4/MOV** — удаление `datetime_original` и `--all` теперь обнуляют штампы времени в заголовках `mvhd`/`tkhd`/`mdhd` (устранена утечка даты съёмки при очистке видео), а при переполнении 32-битной таблицы смещений `stco` (файлы > 4 ГБ) таблица автоматически конвертируется в 64-битную `co64`.
* **36 канонических полей (вместо 22 в 1.0)** — добавлены экспозиционные и технические поля фото (`iso`, `aperture` с поддержкой `f/2.8`, `shutter_speed` с поддержкой дробей `1/250`, `focal_length`, `focal_length_35mm`, `flash`, `lens_make`, `serial_number`), поворот видео без перекодирования (`rotation`: `0`, `90`, `180`, `270` в матрице `tkhd`), частота кадров (`fps`) и музыкальные поля (`album_artist`, `composer`, `track_number`, `disc_number`, `bpm`, `lyrics`).
* **Новые команды CLI и функции API:**
  * `mediameta remove --privacy` — умная приватная очистка (удаляет GPS, даты, серийный номер, камеру, объектив, автора, комментарии и ПО, сохраняя ориентацию кадра, цветовой профиль, заголовок и копирайт).
  * `mediameta diff ФАЙЛ1 ФАЙЛ2` — наглядное сравнение метаданных двух файлов.
  * `mediameta shift-date ФАЙЛ… --by "+2h30m"` — сдвиг даты съёмки на заданное смещение (`+2h`, `-1d 3h`, `+03:00`).
  * `mediameta rename ФАЙЛ… -p "{date:%Y-%m-%d_%H-%M-%S}_{model}_{title}"` — переименование файлов по шаблону из метаданных.
  * `mediameta export` / `mediameta import` — пакетная выгрузка метаданных в **CSV** или **JSON** (для редактирования в Excel/редакторе) и обратный импорт.

## Установка

```bash
cd media-metadata
pip install -e .
```

**Windows:** подробная инструкция — [WINDOWS.md](WINDOWS.md). В папке лежат `install-windows.bat` и ярлыки для перетаскивания файлов мышкой: `Показать метаданные.bat`, `Удалить метаданные (копия).bat` и `Очистить приватность (копия).bat`.

Для форматов HEIC, RAW, MKV, AVI и записи TIFF подключается `exiftool`:

```bash
mediameta exiftool install
```

## Команды

| Команда | Что делает |
|---|---|
| `mediameta show ФАЙЛ…` | показать метаданные (`--json`, `--raw`, `-f поле`) |
| `mediameta set ФАЙЛ…` | записать или удалить поля (`-s поле=значение`, `-t ТЕГ=значение`) |
| `mediameta remove ФАЙЛ…` | удалить отдельные поля (`-f`), приватные данные (`--privacy`) или всё (`--all`) |
| `mediameta copy ИСТОЧНИК ПРИЁМНИК` | перенести метаданные между файлами (в том числе между форматами) |
| `mediameta diff ФАЙЛ1 ФАЙЛ2` | сравнить метаданные двух файлов (`--raw`, `--json`) |
| `mediameta shift-date ФАЙЛ… --by СДВИГ` | сдвинуть `datetime_original` (например, `--by "+2h30m"` или `--by "-1d"`) |
| `mediameta rename ФАЙЛ… -p ШАБЛОН` | переименовать файлы по шаблону из метаданных (`--dry-run`) |
| `mediameta export ФАЙЛ… -o ФАЙЛ` | выгрузить метаданные в `.json` или `.csv` (`--csv`, `--json`, `--raw`) |
| `mediameta import ФАЙЛ` | пакетно загрузить метаданные из `.json` или `.csv` (`--backup`, `--dry-run`) |
| `mediameta fields` | список всех 36 канонических полей и их псевдонимов |
| `mediameta backends` | какие бэкенды доступны в системе (`exif`, `png`, `mp4`, `audio`, `exiftool`) |
| `mediameta exiftool install` | скачать `exiftool` для HEIC, RAW, MKV, AVI (`status`, `remove`) |

Общие флаги: `-r` (рекурсивно по папкам), `--backend`, `--backup`, `-o/--output` (писать в другой файл), `-n/--dry-run`.

### Примеры

```bash
# Проставить экспозицию, дату съёмки и координаты
mediameta set photo.jpg -s "date=2024-07-01 18:30" -s iso=400 -s aperture=f/2.8 -s shutter_speed=1/250 -s gps=55.7558,37.6176

# Повернуть видео на 90 градусов без перекодирования
mediameta set clip.mp4 -s rotation=90 --backup

# Подписать MP3 или FLAC
mediameta set song.mp3 -s title="Звезда по имени Солнце" -s artist="Кино" -s track=2 -s bpm=120

# Убрать приватные данные (GPS, серийник, камеру, дату, автора), сохранив заголовок и ориентацию
mediameta remove photo.jpg --privacy --backup

# Сдвинуть часы съёмки на +2 часа 30 минут для всей папки с отпуска
mediameta shift-date ~/Фото/Отпуск -r --by "+2h30m"

# Переименовать фотографии по дате, модели камеры и названию
mediameta rename *.jpg -p "{date:%Y-%m-%d_%H-%M-%S}_{model}_{title}"

# Выгрузить метаданные папки в CSV-таблицу, отредактировать и загрузить обратно
mediameta export ~/Фото -r --csv -o meta.csv
mediameta import meta.csv --backup

# Сравнить метаданные оригинала и копии
mediameta diff original.jpg edited.jpg
```

## Python API

```python
from mediameta import (
    read_metadata,
    write_metadata,
    remove_metadata,
    copy_metadata,
    diff_metadata,
    shift_datetime,
    rename_by_metadata,
    export_metadata,
    import_metadata,
)

record = read_metadata("clip.mp4")
print(record.common["duration"], record.common.get("gps_latitude"))

write_metadata("photo.jpg", {
    "title": "Лето 2024",
    "artist": "Костя",
    "iso": 400,
    "aperture": "f/2.8",
    "shutter_speed": "1/250",
    "gps_latitude": 55.7558,
    "gps_longitude": 37.6176,
})

remove_metadata("photo.jpg", privacy=True)            # удалить только приватные поля
shift_datetime("photo.jpg", "+2h30m")                 # сдвинуть дату съёмки
diff = diff_metadata("original.jpg", "edited.jpg")    # сравнить два файла
rename_by_metadata("photo.jpg", "{date:%Y-%m-%d}_{title}")
```

## Форматы

| Формат | Чтение | Запись | Бэкенд |
|---|---|---|---|
| JPEG, WebP | да (EXIF + XMP) | да (EXIF + XMP) | `exif` (piexif + родной XMP) |
| PNG | да (`tEXt`/`iTXt`/`zTXt`, `eXIf`, XMP) | да (без пережатия `IDAT`) | `png` (собственный чанк-движок + piexif) |
| MP4, M4V, M4A, M4B, MOV, 3GP | да (`ilst`, `mdta`, `udta`, `mvhd`, `tkhd`) | да (включая `co64` и `rotation`) | `mp4` (собственный парсер) |
| MP3, FLAC, OGG, Opus, WAV, AIFF | да (ID3v2, Vorbis) | да (ID3v2, Vorbis) | `audio` (mutagen) |
| TIFF | да | с exiftool | `exif` / `exiftool` |
| HEIC, AVIF, RAW (CR2/CR3/NEF/ARW/DNG…), MKV, AVI | с exiftool | с exiftool | `exiftool` |

## Тесты

```bash
pip install -e ".[dev]"
python -m pytest -q
```
