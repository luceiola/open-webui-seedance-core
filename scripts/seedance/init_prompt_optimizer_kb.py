#!/usr/bin/env python3
"""
Initialize KB-01 / KB-02 for prompt optimizer and ingest local seed markdown files.

Usage:
  python scripts/seedance/init_prompt_optimizer_kb.py \
    --base-url http://127.0.0.1:8802 \
    --token "$OPENWEBUI_TOKEN"
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


@dataclass
class KnowledgeSpec:
    name: str
    description: str
    seed_dir: str


KB_SPECS = [
    KnowledgeSpec(
        name="KB-01-规则库",
        description="Seedance prompt optimizer rules, constraints, and anti-patterns.",
        seed_dir="KB-01",
    ),
    KnowledgeSpec(
        name="KB-02-模板库",
        description="Seedance prompt optimizer templates for shot language and style phrasing.",
        seed_dir="KB-02",
    ),
]


class KBBootstrap:
    def __init__(self, base_url: str, token: str, timeout_seconds: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout_seconds = timeout_seconds
        if not self.token:
            raise ValueError("token is required")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        resp = client.request(
            method=method,
            url=url,
            headers=self._headers(),
            json=json_body,
            data=data,
            files=files,
        )
        if resp.status_code >= 400:
            detail = resp.text
            try:
                payload = resp.json()
                detail = payload.get("detail") or payload
            except Exception:
                pass
            raise RuntimeError(f"{method} {path} failed: {resp.status_code} {detail}")
        if not resp.text:
            return None
        try:
            return resp.json()
        except Exception:
            return resp.text

    def find_kb(self, client: httpx.Client, name: str) -> dict[str, Any] | None:
        query = quote(name, safe="")
        payload = self._request(client, "GET", f"/api/v1/knowledge/search?query={query}&page=1")
        if not isinstance(payload, dict):
            return None
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if isinstance(item, dict) and str(item.get("name") or "").strip() == name:
                return item
        return None

    def ensure_kb(self, client: httpx.Client, spec: KnowledgeSpec) -> str:
        existing = self.find_kb(client, spec.name)
        if existing:
            kb_id = str(existing.get("id") or "").strip()
            if kb_id:
                print(f"[kb] reuse: {spec.name} ({kb_id})")
                return kb_id

        created = self._request(
            client,
            "POST",
            "/api/v1/knowledge/create",
            json_body={
                "name": spec.name,
                "description": spec.description,
                "access_grants": [],
            },
        )
        if not isinstance(created, dict):
            raise RuntimeError(f"invalid create response for {spec.name}: {created}")
        kb_id = str(created.get("id") or "").strip()
        if not kb_id:
            raise RuntimeError(f"create response missing id for {spec.name}")
        print(f"[kb] created: {spec.name} ({kb_id})")
        return kb_id

    def list_kb_file_names(self, client: httpx.Client, kb_id: str) -> set[str]:
        names: set[str] = set()
        page = 1
        while True:
            payload = self._request(client, "GET", f"/api/v1/knowledge/{quote(kb_id, safe='')}/files?page={page}")
            if not isinstance(payload, dict):
                break
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            for item in items:
                if isinstance(item, dict):
                    filename = str(item.get("filename") or "").strip()
                    if filename:
                        names.add(filename)
            if len(items) < 30:
                break
            page += 1
        return names

    def upload_file(self, client: httpx.Client, path: Path, kb_name: str) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "text/markdown"
        metadata = {"source": "kb-bootstrap", "kb_name": kb_name}
        with path.open("rb") as fp:
            payload = self._request(
                client,
                "POST",
                "/api/v1/files/?process=true&process_in_background=false",
                data={"metadata": json.dumps(metadata, ensure_ascii=False)},
                files={"file": (path.name, fp, mime)},
            )
        if not isinstance(payload, dict):
            raise RuntimeError(f"invalid upload response for {path}: {payload}")
        file_id = str(payload.get("id") or "").strip()
        if not file_id:
            raise RuntimeError(f"upload response missing id for {path}")
        return file_id

    def add_file_to_kb(self, client: httpx.Client, kb_id: str, file_id: str) -> None:
        self._request(
            client,
            "POST",
            f"/api/v1/knowledge/{quote(kb_id, safe='')}/file/add",
            json_body={"file_id": file_id},
        )

    def ingest_seed_dir(self, client: httpx.Client, kb_id: str, kb_name: str, seed_dir: Path) -> tuple[int, int]:
        seed_files = sorted(seed_dir.glob("*.md"))
        if not seed_files:
            print(f"[kb] skip: no seed files under {seed_dir}")
            return 0, 0

        existing_file_names = self.list_kb_file_names(client, kb_id)
        added = 0
        skipped = 0

        for path in seed_files:
            if path.name in existing_file_names:
                skipped += 1
                print(f"[file] skip existing: {kb_name}/{path.name}")
                continue
            file_id = self.upload_file(client, path, kb_name)
            self.add_file_to_kb(client, kb_id, file_id)
            added += 1
            print(f"[file] added: {kb_name}/{path.name} -> {file_id}")
        return added, skipped

    def run(self, seed_root: Path, do_reindex: bool) -> None:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            total_added = 0
            total_skipped = 0
            for spec in KB_SPECS:
                kb_id = self.ensure_kb(client, spec)
                added, skipped = self.ingest_seed_dir(client, kb_id, spec.name, seed_root / spec.seed_dir)
                total_added += added
                total_skipped += skipped

            print(f"[summary] added={total_added}, skipped={total_skipped}")
            if do_reindex:
                print("[reindex] knowledge files...")
                self._request(client, "POST", "/api/v1/knowledge/reindex")
                print("[reindex] metadata embeddings...")
                self._request(client, "POST", "/api/v1/knowledge/metadata/reindex")
                print("[reindex] done")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize and seed KB-01 / KB-02 for prompt optimizer.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8802", help="OpenWebUI base URL")
    parser.add_argument("--token", default="", help="OpenWebUI bearer token")
    parser.add_argument(
        "--seed-root",
        default=str(Path(__file__).resolve().parent / "kb-seeds"),
        help="Root directory containing KB-01 and KB-02 markdown seeds",
    )
    parser.add_argument(
        "--skip-reindex",
        action="store_true",
        help="Skip /knowledge/reindex and /knowledge/metadata/reindex",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = args.token.strip()
    if not token:
        print("error: --token is required (or pass OPENWEBUI token explicitly)", file=sys.stderr)
        return 2

    seed_root = Path(args.seed_root).resolve()
    if not seed_root.exists():
        print(f"error: seed root not found: {seed_root}", file=sys.stderr)
        return 2

    app = KBBootstrap(base_url=args.base_url, token=token)
    app.run(seed_root=seed_root, do_reindex=(not args.skip_reindex))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

