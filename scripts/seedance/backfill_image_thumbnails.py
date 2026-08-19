#!/usr/bin/env python3
"""Generate missing Image2 task thumbnails without touching source images."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / 'backend'
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from open_webui.routers import material_packages as material  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', help='Only process one owner user ID')
    parser.add_argument('--limit', type=int, default=0, help='Maximum records to process; 0 means unlimited')
    parser.add_argument('--overwrite', action='store_true', help='Regenerate existing thumbnails')
    parser.add_argument('--dry-run', action='store_true', help='Report candidates without writing files or records')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    scanned = 0
    generated = 0
    skipped = 0
    missing_source = 0
    failed = 0

    for owner_user_id, path in material._iter_task_record_paths():
        if args.user_id and owner_user_id != args.user_id:
            continue
        if args.limit and scanned >= args.limit:
            break
        scanned += 1
        record = material._load_task_record_from_path(path)
        if record is None or str(record.get('artifact_kind') or '').strip().lower() != material.TASK_ARTIFACT_KIND_IMAGE:
            skipped += 1
            continue
        if not material._is_succeeded_task_status(record.get('status')):
            skipped += 1
            continue

        task_id = str(record.get('task_id') or path.stem)
        thumb_relpath = material._archive_thumb_relpath(task_id)
        thumb_path = material._task_file_from_relative(owner_user_id, thumb_relpath)
        desired_url = material._build_task_thumbnail_url(task_id)
        sources = material._resolve_task_image_sources(owner_user_id, record)
        if not thumb_path and not sources:
            missing_source += 1
            logging.warning('no source image: %s/%s', owner_user_id, task_id)
            continue
        metadata_current = (
            record.get('thumbnail_path') == thumb_relpath
            and record.get('thumbnail_url') == desired_url
        )
        if thumb_path and not args.overwrite and metadata_current:
            skipped += 1
            continue
        if args.dry_run:
            generated += 1
            action = (
                'regenerate'
                if args.overwrite and thumb_path
                else ('repair metadata' if thumb_path else 'generate')
            )
            logging.info('would %s: %s/%s', action, owner_user_id, task_id)
            continue

        changed = material.ensure_image_task_thumbnail(owner_user_id, record, overwrite=args.overwrite)
        if material._sync_task_serving_fields(owner_user_id, record):
            changed = True
        resolved_thumb = material._task_file_from_relative(
            owner_user_id,
            record.get('thumbnail_path'),
        )
        if resolved_thumb and changed:
            material._save_task_record(owner_user_id, task_id, record)
            generated += 1
            logging.info('updated: %s/%s', owner_user_id, task_id)
        elif resolved_thumb:
            skipped += 1
        else:
            failed += 1
            logging.warning('generation failed: %s/%s', owner_user_id, task_id)

    print(
        f'scanned={scanned} generated={generated} skipped={skipped} '
        f'missing_source={missing_source} failed={failed}'
    )
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
