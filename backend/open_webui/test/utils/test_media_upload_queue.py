import asyncio
from pathlib import Path

from open_webui.utils.media_upload_queue import MediaUploadQueue


def test_queue_persists_jobs_and_enforces_user_scope(tmp_path: Path):
    queue = MediaUploadQueue(tmp_path / 'uploads.sqlite3')
    job_id = queue.enqueue(user_id='user-1', kind='media_asset', payload={'asset_id': 'asset-1'})

    assert queue.get(job_id, 'user-1')['status'] == 'QUEUED'
    assert queue.get(job_id, 'user-2') is None


def test_worker_runs_blocking_handler_off_event_loop(tmp_path: Path):
    asyncio.run(_test_worker_runs_blocking_handler_off_event_loop(tmp_path))


async def _test_worker_runs_blocking_handler_off_event_loop(tmp_path: Path):
    queue = MediaUploadQueue(tmp_path / 'uploads.sqlite3')
    job_id = queue.enqueue(user_id='user-1', kind='media_asset', payload={'value': 2})
    observed: list[int] = []

    def handler(payload):
        observed.append(payload['value'])

    worker = asyncio.create_task(queue.run({'media_asset': handler}))
    try:
        for _ in range(20):
            await asyncio.sleep(0.01)
            job = queue.get(job_id, 'user-1')
            if job and job['status'] == 'SUCCEEDED':
                break
        assert observed == [2]
        assert queue.get(job_id, 'user-1')['status'] == 'SUCCEEDED'
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
