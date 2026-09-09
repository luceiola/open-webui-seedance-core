"""Durable, bounded queue for blocking media uploads."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


class MediaUploadQueue:
    def __init__(self, path: Path, *, concurrency: int = 2, lease_seconds: int = 900):
        self.path = path
        self.concurrency = max(1, int(concurrency))
        self.lease_seconds = max(60, int(lease_seconds))
        self._lock = threading.RLock()
        self._wake = asyncio.Event()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=10000')
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS upload_jobs (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at INTEGER NOT NULL,
                lease_until INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                error TEXT
            )'''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_upload_jobs_ready ON upload_jobs(status, available_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_upload_jobs_user ON upload_jobs(user_id, created_at DESC)')
        return conn

    def enqueue(self, *, user_id: str, kind: str, payload: dict[str, Any]) -> str:
        job_id = f'upload_{uuid.uuid4().hex}'
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                '''INSERT INTO upload_jobs
                   (job_id,user_id,kind,status,payload_json,available_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (job_id, str(user_id), kind, 'QUEUED', json.dumps(payload, ensure_ascii=False), now, now, now),
            )
        self._wake.set()
        return job_id

    def get(self, job_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM upload_jobs WHERE job_id=? AND user_id=?', (job_id, str(user_id))
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item['payload'] = json.loads(item.pop('payload_json'))
        return item

    def _claim(self) -> dict[str, Any] | None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            row = conn.execute(
                '''SELECT * FROM upload_jobs
                   WHERE (status='QUEUED' AND available_at<=?)
                      OR (status='UPLOADING' AND lease_until<=?)
                   ORDER BY created_at LIMIT 1''',
                (now, now),
            ).fetchone()
            if row is None:
                return None
            job_id = str(row['job_id'])
            conn.execute(
                '''UPDATE upload_jobs SET status='UPLOADING', attempts=attempts+1,
                   lease_until=?, updated_at=? WHERE job_id=?''',
                (now + self.lease_seconds, now, job_id),
            )
            item = dict(row)
            item['attempts'] = int(item['attempts']) + 1
            item['payload'] = json.loads(item.pop('payload_json'))
            return item

    def _finish(self, job_id: str, status: str, error: str | None = None, delay: int = 0) -> None:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            conn.execute(
                '''UPDATE upload_jobs SET status=?, error=?, available_at=?, lease_until=0, updated_at=?
                   WHERE job_id=?''',
                (status, error, now + max(0, delay), now, job_id),
            )

    async def run(self, handlers: dict[str, Callable[[dict[str, Any]], None]]) -> None:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def process(job: dict[str, Any]) -> None:
            async with semaphore:
                handler = handlers.get(str(job['kind']))
                if handler is None:
                    self._finish(str(job['job_id']), 'FAILED', f'Unknown upload job kind: {job["kind"]}')
                    return
                try:
                    await asyncio.to_thread(handler, job['payload'])
                except Exception as exc:
                    attempts = int(job['attempts'])
                    if attempts < 5:
                        self._finish(str(job['job_id']), 'QUEUED', str(exc), delay=min(300, 2 ** attempts * 5))
                    else:
                        self._finish(str(job['job_id']), 'FAILED', str(exc))
                else:
                    self._finish(str(job['job_id']), 'SUCCEEDED')

        while True:
            try:
                jobs = []
                for _ in range(self.concurrency):
                    job = self._claim()
                    if job is None:
                        break
                    jobs.append(job)
                if jobs:
                    await asyncio.gather(*(process(job) for job in jobs))
                    continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
