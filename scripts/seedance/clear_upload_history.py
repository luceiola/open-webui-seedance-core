#!/usr/bin/env python3
"""Remove legacy local uploads and every Open WebUI reference to them.

Run this only while the target Open WebUI instance is stopped. The tool keeps
chat text and knowledge containers, but removes their old file attachments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path, help='Instance DATA_DIR containing webui.db')
    parser.add_argument(
        '--legacy-upload-dir', type=Path, help='Local uploads directory to delete (default: DATA_DIR/uploads)'
    )
    parser.add_argument('--backup-dir', type=Path, help='Directory for the SQLite backup and manifest')
    parser.add_argument('--execute', action='store_true', help='Perform deletion; without this flag only report scope')
    return parser.parse_args()


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def collect_scope(connection: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, int]]:
    files = [dict(row) for row in connection.execute('SELECT id, hash, filename, path FROM file ORDER BY id')]
    counts = {'file': len(files)}
    for table in ('knowledge_file', 'chat_file', 'channel_file'):
        counts[table] = connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] if table_exists(connection, table) else 0
    return files, counts


def is_file_reference(value: dict[str, Any], file_ids: set[str]) -> bool:
    if value.get('file_id') in file_ids:
        return True
    if value.get('id') in file_ids and ('filename' in value or 'path' in value or 'meta' in value):
        return True
    file_value = value.get('file')
    return isinstance(file_value, dict) and file_value.get('id') in file_ids


_REMOVE = object()


def remove_file_references(value: Any, file_ids: set[str]) -> Any:
    if isinstance(value, list):
        cleaned = [remove_file_references(item, file_ids) for item in value]
        return [item for item in cleaned if item is not _REMOVE]
    if not isinstance(value, dict):
        return value
    if is_file_reference(value, file_ids):
        return _REMOVE
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        item = remove_file_references(item, file_ids)
        if item is not _REMOVE:
            cleaned[key] = item
    return cleaned


def scrub_json_column(connection: sqlite3.Connection, table: str, column: str, file_ids: set[str]) -> int:
    if not table_exists(connection, table):
        return 0
    changed = 0
    for row_id, raw_value in connection.execute(f'SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL'):
        try:
            value = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        except (TypeError, json.JSONDecodeError):
            continue
        cleaned = remove_file_references(value, file_ids)
        if cleaned is _REMOVE:
            cleaned = {}
        if cleaned != value:
            connection.execute(f'UPDATE {table} SET {column} = ? WHERE id = ?', (json.dumps(cleaned, ensure_ascii=False), row_id))
            changed += 1
    return changed


async def clear_vectors(files: list[dict[str, Any]], knowledge_ids: dict[str, set[str]]) -> list[str]:
    from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

    errors: list[str] = []
    for file in files:
        file_id = file['id']
        try:
            if await ASYNC_VECTOR_DB_CLIENT.has_collection(f'file-{file_id}'):
                await ASYNC_VECTOR_DB_CLIENT.delete_collection(f'file-{file_id}')
            for knowledge_id in knowledge_ids.get(file_id, set()):
                await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={'file_id': file_id})
                if file['hash']:
                    await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=knowledge_id, filter={'hash': file['hash']})
        except Exception as exc:
            errors.append(f'{file_id}: {exc}')
    return errors


def create_backup(source: Path, backup_dir: Path) -> tuple[Path, Path]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_path = backup_dir / f'webui-before-upload-cleanup-{timestamp}.db'
    manifest_path = backup_dir / f'upload-cleanup-{timestamp}.json'
    with sqlite3.connect(source) as source_db, sqlite3.connect(backup_path) as backup_db:
        source_db.backup(backup_db)
    return backup_path, manifest_path


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    database_path = data_dir / 'webui.db'
    legacy_upload_dir = (args.legacy_upload_dir or data_dir / 'uploads').expanduser().resolve()
    backup_dir = (args.backup_dir or data_dir / 'maintenance-backups').expanduser().resolve()

    if not database_path.is_file():
        print(f'[ERROR] database does not exist: {database_path}', file=sys.stderr)
        return 2
    if data_dir == legacy_upload_dir or legacy_upload_dir == Path('/'):
        print(f'[ERROR] refusing unsafe legacy upload directory: {legacy_upload_dir}', file=sys.stderr)
        return 2

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        files, counts = collect_scope(connection)
    disk_files = sum(1 for path in legacy_upload_dir.rglob('*') if path.is_file()) if legacy_upload_dir.is_dir() else 0
    report = {'data_dir': str(data_dir), 'legacy_upload_dir': str(legacy_upload_dir), 'database_rows': counts, 'disk_files': disk_files}
    if not args.execute:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0

    backup_path, manifest_path = create_backup(database_path, backup_dir)
    manifest_path.write_text(json.dumps({**report, 'files': files, 'backup': str(backup_path)}, ensure_ascii=False, indent=2), encoding='utf-8')

    # Import the configured vector backend only after a successful local DB backup.
    os.environ['DATA_DIR'] = str(data_dir)
    os.environ['UPLOAD_DIR'] = str(legacy_upload_dir)
    os.environ['ENABLE_DB_MIGRATIONS'] = 'false'

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        knowledge_ids: dict[str, set[str]] = {}
        if table_exists(connection, 'knowledge_file'):
            for row in connection.execute('SELECT file_id, knowledge_id FROM knowledge_file'):
                knowledge_ids.setdefault(row['file_id'], set()).add(row['knowledge_id'])

    vector_errors = asyncio.run(clear_vectors(files, knowledge_ids))
    if vector_errors:
        print(json.dumps({'error': 'vector cleanup failed', 'details': vector_errors, 'backup': str(backup_path)}, ensure_ascii=False), file=sys.stderr)
        return 1

    file_ids = {file['id'] for file in files}
    with sqlite3.connect(database_path) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('BEGIN IMMEDIATE')
        scrubbed = {
            'chat.chat': scrub_json_column(connection, 'chat', 'chat', file_ids),
            'message.data': scrub_json_column(connection, 'message', 'data', file_ids),
            'message.meta': scrub_json_column(connection, 'message', 'meta', file_ids),
        }
        for table in ('knowledge_file', 'chat_file', 'channel_file'):
            if table_exists(connection, table):
                connection.execute(f'DELETE FROM {table} WHERE file_id IN (SELECT id FROM file)')
        connection.execute('DELETE FROM file')
        connection.commit()

    if legacy_upload_dir.exists():
        shutil.rmtree(legacy_upload_dir)
    legacy_upload_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps({**report, 'backup': str(backup_path), 'manifest': str(manifest_path), 'scrubbed': scrubbed}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
