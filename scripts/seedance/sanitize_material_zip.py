#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import zipfile


SUPPORTED_EXTENSIONS = {
    # image
    '.jpg',
    '.jpeg',
    '.png',
    '.webp',
    '.bmp',
    '.gif',
    # video
    '.mp4',
    '.mov',
    '.mkv',
    '.avi',
    '.webm',
    '.mpeg',
    '.mpg',
    '.m4v',
    # audio
    '.mp3',
    '.wav',
    '.m4a',
    '.aac',
    '.flac',
    '.ogg',
}

SKIP_DIR_NAMES = {'__MACOSX'}
SKIP_FILE_NAMES_LOWER = {'.ds_store', 'thumbs.db', 'desktop.ini'}


def is_unsafe_path(path: str) -> bool:
    p = PurePosixPath(path)
    return p.is_absolute() or '..' in p.parts


def should_skip(path: PurePosixPath) -> bool:
    for segment in path.parts[:-1]:
        if segment in SKIP_DIR_NAMES or segment.startswith('.'):
            return True

    filename = path.name
    filename_lower = filename.lower()

    if filename.startswith('._'):
        return True
    if filename_lower in SKIP_FILE_NAMES_LOWER:
        return True
    if filename.startswith('.'):
        return True

    return False


def is_media(path: PurePosixPath) -> bool:
    return Path(path.name).suffix.lower() in SUPPORTED_EXTENSIONS


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Sanitize material zip: remove hidden/system artifacts and optionally keep only media files.',
    )
    parser.add_argument('input_zip', help='Input .zip file path')
    parser.add_argument('-o', '--output', help='Output .zip file path')
    parser.add_argument(
        '--only-media',
        action='store_true',
        help='Keep only supported media extensions (image/video/audio)',
    )
    args = parser.parse_args()

    input_zip = Path(args.input_zip).expanduser().resolve()
    if not input_zip.exists():
        raise SystemExit(f'[ERROR] Input zip not found: {input_zip}')
    if input_zip.suffix.lower() != '.zip':
        raise SystemExit(f'[ERROR] Input must be .zip: {input_zip}')

    output_zip = (
        Path(args.output).expanduser().resolve()
        if args.output
        else input_zip.with_name(f'{input_zip.stem}.clean.zip')
    )
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = 0
    skipped_hidden = 0
    skipped_unsafe = 0
    skipped_non_media = 0

    with zipfile.ZipFile(input_zip, 'r') as src, zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.is_dir():
                continue

            total += 1
            rel = PurePosixPath(info.filename)

            if is_unsafe_path(info.filename):
                skipped_unsafe += 1
                continue

            if should_skip(rel):
                skipped_hidden += 1
                continue

            if args.only_media and not is_media(rel):
                skipped_non_media += 1
                continue

            with src.open(info, 'r') as f:
                data = f.read()
            dst.writestr(info.filename, data)
            kept += 1

    print(f'[OK] input={input_zip}')
    print(f'[OK] output={output_zip}')
    print(f'[STAT] total={total} kept={kept} skipped_hidden={skipped_hidden} skipped_unsafe={skipped_unsafe} skipped_non_media={skipped_non_media}')

    if kept == 0:
        print('[WARN] cleaned zip has 0 files, please check source zip contents')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
