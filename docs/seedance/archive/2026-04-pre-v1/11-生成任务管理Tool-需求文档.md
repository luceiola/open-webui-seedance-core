# 生成任务管理 Tool 需求文档

## 1. 背景

当前 `seedance_material_package_tool_v2` 已支持素材包解析与提交生成，但“提交后追踪结果”能力不足。
用户常见问题是：
- 不知道任务是否真的提交成功。
- 没有稳定方式查询任务状态。
- 无法直接拿到最终视频 URL。

## 2. 目标

在现有素材包工具基础上补齐“任务管理能力”，实现：
1. 可查询单个任务状态。
2. 可等待任务到终态并输出最终结果。
3. 可按筛选条件列任务历史。
4. 所有失败场景返回结构化错误，避免 LLM 误报。

## 3. 术语

- `task_id`: Seedance 生成任务 ID（当前返回字段 `response_id`）。
- `terminal status`: 终态，包含 `succeeded/failed/cancelled/error`。
- `video_url`: 任务成功后返回的视频下载地址（通常为时效 URL）。

## 4. 范围

## 4.1 In Scope（本期）

- 新增 Tool 方法 `get_generation_task_status(task_id)`。
- 新增 Tool 方法 `wait_generation_task(task_id, timeout_seconds, poll_interval_seconds)`。
- 新增 Tool 方法 `generate_and_wait_with_material_package(...)`（封装提交+等待）。
- 统一成功/失败返回结构。

## 4.2 Out of Scope（本期不做）

- 前端任务中心页面。
- WebSocket/SSE 主动推送通知。
- 跨项目统一任务编排系统。

## 5. 用户场景

1. 用户发起生成后，要求“给我任务 ID”。
2. 用户发起生成后，要求“直接给视频 URL”。
3. 用户要求“看最近几条任务状态”（需后端列表接口）。
4. 用户要求“失败原因与 request_id，用于排障”。

## 6. 功能需求

## FR-1 查询单任务状态

- 方法名：`get_generation_task_status`
- 入参：`task_id`
- 行为：调用后端 `/api/v1/material-packages/tasks/{task_id}`
- 出参至少包含：
  - `ok`
  - `task_id`
  - `status`
  - `video_url`（若可提取）
  - `raw_response`

## FR-2 等待任务完成

- 方法名：`wait_generation_task`
- 入参：
  - `task_id`
  - `timeout_seconds`（默认 600）
  - `poll_interval_seconds`（默认 3）
- 行为：循环查询直到终态或超时。
- 超时返回：`ok=false` + `status_code=408` + 可读错误信息。

## FR-3 一步生成并等待

- 方法名：`generate_and_wait_with_material_package`
- 行为：
  1) 调用已有 `generate_video_with_material_package`。
  2) 读取 `response_id` 作为 `task_id`。
  3) 自动等待到终态并返回最终结果。

## FR-4 任务列表

- 方法名：`list_generation_tasks`。
- 后端接口：`GET /api/v1/material-packages/tasks`。
- 支持筛选：`asset_package_id/status/chat_id/time_range/limit`。

## FR-5 统一错误结构

所有任务相关方法失败时返回：
- `ok=false`
- `status_code`
- `error_code`
- `error_message`
- `request_id`

说明：严禁把 4xx 误报为 5xx，严禁编造错误原因。

## 7. 状态机

任务状态按下列逻辑处理：
- 非终态：`queued/running/submitted`
- 终态成功：`succeeded/completed`
- 终态失败：`failed/error/cancelled`

`wait_generation_task` 仅在进入终态后返回。

## 8. 接口与返回契约（Tool 层）

## 8.1 get_generation_task_status

入参：
- `task_id: string`

返回（示例）：
```json
{
  "ok": true,
  "task_id": "cgt-xxx",
  "status": "succeeded",
  "video_url": "https://...mp4",
  "raw_response": {}
}
```

## 8.2 wait_generation_task

入参：
- `task_id: string`
- `timeout_seconds?: int`
- `poll_interval_seconds?: int`

返回：
- 成功：同 `get_generation_task_status`，但 `status` 一定是终态。
- 超时：
```json
{
  "ok": false,
  "status_code": 408,
  "error_message": "Generation task polling timeout"
}
```

## 8.3 generate_and_wait_with_material_package

入参：
- `asset_package_id`
- `prompt`
- `model`（建议必传）
- `duration/ratio/watermark/generate_audio`
- `timeout_seconds/poll_interval_seconds`

返回：
- 包含 `task_id(response_id)` + 最终 `status` + `video_url`。

## 9. 非功能要求

- 可观测：日志中记录 `asset_package_id/task_id/status/request_id`。
- 稳定性：轮询失败可重试；超时可控。
- 可读性：对用户返回“结论优先”。

## 10. 验收标准

1. 生成提交后，100% 能拿到 `task_id` 或明确失败原因。
2. `wait_generation_task` 能在终态时返回，并可提取 `video_url`。
3. 超时场景返回 408 与明确提示。
4. 错误返回结构字段完整，不出现空泛“请求失败”。

## 11. 里程碑建议

- M1（本周）：实现 `get_generation_task_status` + `wait_generation_task`。
- M2（本周）：实现 `generate_and_wait_with_material_package`。
- M3（本周）：实现并验证 `list_generation_tasks`。
