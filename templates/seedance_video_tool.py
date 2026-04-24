"""
title: Seedance Video Tool (Template)
author: local-dev
version: 0.1.0
required_open_webui_version: 0.8.0
requirements: httpx>=0.28.1
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        ARK_API_KEY: str = Field(default="", description="火山方舟 API Key")
        ARK_BASE_URL: str = Field(default="https://ark.cn-beijing.volces.com", description="火山方舟基地址")
        SEEDANCE_MODEL: str = Field(default="", description="Seedance 模型 ID")
        POLL_INTERVAL_SECONDS: int = Field(default=5, ge=1, le=30)
        MAX_WAIT_SECONDS: int = Field(default=300, ge=30, le=1800)

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def _submit_task(self, prompt: str, duration_seconds: int, resolution: str) -> str:
        # TODO: 根据官方最新文档确认 submit 路径和 payload 字段。
        url = f"{self.valves.ARK_BASE_URL}/api/v3/contents/generations/tasks/submit"

        headers = {
            "Authorization": f"Bearer {self.valves.ARK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.valves.SEEDANCE_MODEL,
            "input": {
                "prompt": prompt,
                "duration": duration_seconds,
                "resolution": resolution,
            },
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise ValueError(f"submit response missing task id: {data}")
        return task_id

    async def _query_task(self, task_id: str) -> dict[str, Any]:
        # TODO: 根据官方最新文档确认 query 路径和字段。
        url = f"{self.valves.ARK_BASE_URL}/api/v3/contents/generations/tasks/query"
        headers = {
            "Authorization": f"Bearer {self.valves.ARK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {"task_id": task_id}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def seedance_text_to_video(
        self,
        prompt: str,
        duration_seconds: int = 5,
        resolution: str = "720p",
    ) -> str:
        """
        使用 Seedance 文生视频，返回结果 URL。
        """
        if not self.valves.ARK_API_KEY:
            return "[ERROR] ARK_API_KEY 未配置"
        if not self.valves.SEEDANCE_MODEL:
            return "[ERROR] SEEDANCE_MODEL 未配置"

        task_id = await self._submit_task(prompt, duration_seconds, resolution)

        elapsed = 0
        while elapsed < self.valves.MAX_WAIT_SECONDS:
            result = await self._query_task(task_id)
            status = str(result.get("status", "")).lower()

            if status in {"succeeded", "success", "done", "finished"}:
                video_url = result.get("result_url") or result.get("output_url")
                if not video_url:
                    return f"[ERROR] 任务成功但未返回视频 URL: task_id={task_id}, result={result}"
                return f"[OK] task_id={task_id}\nvideo_url={video_url}"

            if status in {"failed", "error", "canceled", "cancelled"}:
                return f"[ERROR] task_id={task_id}, result={result}"

            await asyncio.sleep(self.valves.POLL_INTERVAL_SECONDS)
            elapsed += self.valves.POLL_INTERVAL_SECONDS

        return f"[TIMEOUT] task_id={task_id} 超过最大等待时间 {self.valves.MAX_WAIT_SECONDS}s"
