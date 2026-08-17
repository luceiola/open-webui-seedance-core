"""Local read index for file-backed generation task records."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional


class TaskCatalog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=10000')
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS task_catalog (
                user_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                deleted_at INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (user_id, task_id)
            )'''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_task_catalog_created ON task_catalog(created_at DESC, task_id DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_task_catalog_filters ON task_catalog(user_id, provider, status, deleted_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_task_catalog_task_id ON task_catalog(task_id)')
        return conn

    @staticmethod
    def _row(user_id: str, task_id: str, data: dict[str, Any]) -> tuple[Any, ...]:
        raw_status = str(data.get('status') or '').strip().upper()
        status = {
            'SUCCESS': 'SUCCEEDED',
            'COMPLETED': 'SUCCEEDED',
            'ERROR': 'FAILED',
            'CANCELLED': 'CANCELED',
            'SUBMITTED': 'PENDING',
            'QUEUED': 'PENDING',
            'IN_PROGRESS': 'RUNNING',
        }.get(raw_status, raw_status)
        return (
            str(user_id),
            str(task_id),
            str(data.get('provider') or 'ark').strip().lower() or 'ark',
            str(data.get('skill_name') or 'seedance').strip().lower() or 'seedance',
            str(data.get('tool_name') or 'material_packages.generate').strip().lower() or 'material_packages.generate',
            str(data.get('model') or '').strip().lower(),
            status,
            int(data.get('created_at') or 0),
            int(data.get('updated_at') or 0),
            int(data.get('deleted_at') or 0),
            json.dumps(data, ensure_ascii=False, separators=(',', ':')),
        )

    def upsert(self, user_id: str, task_id: str, data: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                '''INSERT INTO task_catalog (
                    user_id, task_id, provider, skill_name, tool_name, model, status,
                    created_at, updated_at, deleted_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, task_id) DO UPDATE SET
                    provider=excluded.provider, skill_name=excluded.skill_name,
                    tool_name=excluded.tool_name, model=excluded.model, status=excluded.status,
                    created_at=excluded.created_at, updated_at=excluded.updated_at,
                    deleted_at=excluded.deleted_at, payload_json=excluded.payload_json''',
                self._row(user_id, task_id, data),
            )

    def rebuild(self, records: Iterable[tuple[str, str, dict[str, Any]]]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute('DELETE FROM task_catalog')
            conn.executemany(
                '''INSERT INTO task_catalog (
                    user_id, task_id, provider, skill_name, tool_name, model, status,
                    created_at, updated_at, deleted_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (self._row(user_id, task_id, data) for user_id, task_id, data in records),
            )

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            return int(conn.execute('SELECT COUNT(*) FROM task_catalog').fetchone()[0])

    def query(
        self,
        *,
        user_id: Optional[str] = None,
        owner_ids: Optional[set[str]] = None,
        provider: Optional[str] = None,
        skill_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        status: Optional[str] = None,
        model: Optional[str] = None,
        start_at: Optional[int] = None,
        end_at: Optional[int] = None,
        include_deleted: bool = False,
        deletion_status: str | None = None,
        offset: int = 0,
        limit: int = 48,
    ) -> tuple[list[tuple[str, dict[str, Any]]], int]:
        where: list[str] = []
        values: list[Any] = []
        if user_id:
            where.append('user_id = ?')
            values.append(user_id)
        if owner_ids is not None:
            if not owner_ids:
                return [], 0
            where.append(f"user_id IN ({','.join('?' for _ in owner_ids)})")
            values.extend(sorted(owner_ids))
        for column, value in (
            ('provider', provider), ('skill_name', skill_name), ('tool_name', tool_name), ('status', status), ('model', model)
        ):
            if value:
                where.append(f'{column} = ?')
                values.append(value)
        if start_at is not None:
            where.append('created_at >= ?')
            values.append(start_at)
        if end_at is not None:
            where.append('created_at <= ?')
            values.append(end_at)
        normalized_deletion_status = str(deletion_status or '').strip().lower()
        if normalized_deletion_status == 'deleted':
            where.append('deleted_at > 0')
        elif normalized_deletion_status == 'active' or not include_deleted:
            where.append('deleted_at = 0')
        clause = f" WHERE {' AND '.join(where)}" if where else ''
        with self._lock, self._connect() as conn:
            total = int(conn.execute(f'SELECT COUNT(*) FROM task_catalog{clause}', values).fetchone()[0])
            rows = conn.execute(
                f'''SELECT user_id, payload_json FROM task_catalog{clause}
                    ORDER BY created_at DESC, task_id DESC LIMIT ? OFFSET ?''',
                [*values, limit, offset],
            ).fetchall()
        return [(str(row['user_id']), json.loads(row['payload_json'])) for row in rows], total

    def owners(self, *, include_deleted: bool = False) -> list[str]:
        clause = '' if include_deleted else ' WHERE deleted_at = 0'
        with self._lock, self._connect() as conn:
            return [str(row[0]) for row in conn.execute(f'SELECT DISTINCT user_id FROM task_catalog{clause}')]

    def find(self, task_id: str, *, include_deleted: bool = False) -> Optional[tuple[str, dict[str, Any]]]:
        clauses = ['task_id = ?']
        values: list[Any] = [str(task_id)]
        if not include_deleted:
            clauses.append('deleted_at = 0')
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f'''SELECT user_id, payload_json FROM task_catalog
                    WHERE {' AND '.join(clauses)}
                    ORDER BY updated_at DESC LIMIT 1''',
                values,
            ).fetchone()
        if row is None:
            return None
        return str(row['user_id']), json.loads(row['payload_json'])
