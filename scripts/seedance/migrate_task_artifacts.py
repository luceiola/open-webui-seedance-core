#!/usr/bin/env python3
"""Copy generated task media to a separate artifact root with SHA-256 checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


ARTIFACT_DIR_NAMES = frozenset({'task_archives', 'task_thumbnails', 'task_vendor_artifacts'})
CHUNK_SIZE = 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b''):
            digest.update(chunk)
    return digest.hexdigest()


def iter_artifacts(source_root: Path) -> Iterator[tuple[Path, Path]]:
    for user_dir in sorted(source_root.iterdir()):
        if not user_dir.is_dir() or user_dir.is_symlink():
            continue
        for name in ARTIFACT_DIR_NAMES:
            artifact_dir = user_dir / name
            if not artifact_dir.is_dir() or artifact_dir.is_symlink():
                continue
            for path in sorted(artifact_dir.rglob('*')):
                if path.is_file() and not path.is_symlink():
                    yield path, path.relative_to(source_root)


def copy_verified(source: Path, target: Path) -> tuple[str, bool]:
    source_hash = sha256(source)
    if target.is_file() and target.stat().st_size == source.stat().st_size and sha256(target) == source_hash:
        return source_hash, False

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.name}.part-{os.getpid()}')
    try:
        shutil.copyfile(source, temporary)
        shutil.copystat(source, temporary, follow_symlinks=False)
        if sha256(temporary) != source_hash:
            raise RuntimeError('checksum mismatch after copy')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return source_hash, True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path, help='Local material_packages directory')
    parser.add_argument('--target-root', required=True, type=Path, help='Existing NAS artifact root')
    parser.add_argument('--verify-only', action='store_true', help='Do not copy; require every target file to match')
    parser.add_argument('--delete-source', action='store_true', help='Delete each local file only after checksum verification')
    parser.add_argument('--manifest', type=Path, help='Write JSONL results here (default: target root)')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source_root.expanduser().resolve()
    target_root = args.target_root.expanduser().resolve()
    if not source_root.is_dir():
        print(f'[ERROR] source root does not exist: {source_root}', file=sys.stderr)
        return 2
    if not target_root.is_dir():
        print(f'[ERROR] target root does not exist: {target_root}', file=sys.stderr)
        return 2
    if source_root == target_root:
        print('[ERROR] source and target roots must differ', file=sys.stderr)
        return 2

    manifest_path = args.manifest or target_root / f'migration-manifest-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.jsonl'
    manifest_path = manifest_path.expanduser().resolve()
    if not os.access(target_root, os.W_OK | os.X_OK):
        print(f'[ERROR] target root is not writable: {target_root}', file=sys.stderr)
        return 2

    copied = skipped = deleted = failed = 0
    with manifest_path.open('x', encoding='utf-8') as manifest:
        for source, relative_path in iter_artifacts(source_root):
            target = target_root / relative_path
            row: dict[str, object] = {
                'source': str(source),
                'target': str(target),
                'relative_path': relative_path.as_posix(),
                'bytes': source.stat().st_size,
            }
            try:
                source_hash = sha256(source)
                if args.verify_only:
                    if not target.is_file() or target.stat().st_size != source.stat().st_size or sha256(target) != source_hash:
                        raise RuntimeError('target is missing or checksum differs')
                    was_copied = False
                else:
                    source_hash, was_copied = copy_verified(source, target)
                row['sha256'] = source_hash
                row['status'] = 'copied' if was_copied else 'verified'
                if was_copied:
                    copied += 1
                else:
                    skipped += 1
                if args.delete_source:
                    if not target.is_file() or sha256(target) != source_hash:
                        raise RuntimeError('refusing to delete unverified source')
                    source.unlink()
                    row['source_deleted'] = True
                    deleted += 1
            except Exception as exc:
                row['status'] = 'failed'
                row['error'] = str(exc)
                failed += 1
            manifest.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')

    print(json.dumps({
        'copied': copied,
        'verified': skipped,
        'deleted': deleted,
        'failed': failed,
        'manifest': str(manifest_path),
    }, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
