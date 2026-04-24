# 生成任务管理 Tool 开发 TODO

## 目标

补齐 `seedance_material_package_tool_v2` 的任务追踪闭环：
- 提交后可查
- 可等到终态
- 可拿到视频 URL

## 当前状态（2026-04-22）

- 已有：`generate_video_with_material_package`
- 已有后端接口：`GET /api/v1/material-packages/tasks/{task_id}`
- 已新增后端接口：`GET /api/v1/material-packages/tasks`（任务列表）
- 已补齐：Tool 侧任务查询/轮询/一步生成等待/任务列表

## P0 设计冻结

- [ ] 状态映射表冻结（哪些算终态）。
- [ ] 轮询默认参数冻结（timeout/poll interval）。
- [ ] 错误结构字段冻结（status_code/error_code/error_message/request_id）。

## P1 Tool 新增方法（核心）

- [x] 在 `templates/seedance_material_package_tool.py` 新增 `get_generation_task_status(task_id)`。
- [x] 在 `templates/seedance_material_package_tool.py` 新增 `wait_generation_task(task_id, timeout_seconds=600, poll_interval_seconds=3)`。
- [x] 在 `templates/seedance_material_package_tool.py` 新增 `generate_and_wait_with_material_package(...)`。

## P2 返回结构统一

- [x] 三个新方法都返回统一字段：
  - [x] `ok`
  - [x] `task_id`
  - [x] `status`
  - [x] `video_url`
  - [x] `raw_response`
- [x] 失败统一结构：
  - [x] `status_code`
  - [x] `error_code`
  - [x] `error_message`
  - [x] `request_id`

## P3 终态解析与 URL 提取

- [x] 兼容 `status` 在不同路径：`status/raw_response.status/raw_response.data.status`。
- [x] 从 `raw_response.content.video_url` 提取结果地址。
- [x] 若无 URL，返回可读提示，不报假成功。

## P4 导入文件同步

- [x] 将最新工具代码同步到：
  - [x] `templates/seedance_material_package_tool_v2.import.json`
- [x] 保持单一导入文件策略，不再新增第二个 import json。

## P5 文档更新

- [x] 更新 [07-素材包Tool-接入与联调.md](/Users/lucas/Documents/openwebui-seedance/docs/07-素材包Tool-接入与联调.md)：新增任务查询调用示例。
- [x] 更新 [08-运行命令手册.md](/Users/lucas/Documents/openwebui-seedance/docs/08-运行命令手册.md)：新增“提交后查询/等待”命令。

## P6 验证用例

- [x] 用 `pkg_627740245f2c401f` 跑一次“提交 -> 查询 -> 成功 URL”。
- [ ] 造一个失败任务验证错误结构。
- [ ] 人工验证超时路径（短 timeout）。

## P7 后续（依赖后端）

- [x] 评估并设计 `list_generation_tasks` 的后端接口。
- [x] 后端就绪后补 Tool 方法 `list_generation_tasks(...)`。

## DoD（完成定义）

- [x] 在 OpenWebUI 对话中，LLM 能返回 `response_id(task_id)`。
- [x] 用户可通过 Tool 查到终态并拿到 `video_url`。
- [x] 错误可直接用于排障（含 request_id）。
- [x] 文档、Tool、导入文件一致。
